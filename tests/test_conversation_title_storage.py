from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import (
    Base,
    Database,
    PostgresConversationStore,
    UserRepository,
)
from ad_vista_agent.database.models import Conversation
from ad_vista_agent.memory import ConversationStore


@pytest.fixture(params=["sqlite", "postgres_adapter"])
def store(request, tmp_path):
    if request.param == "sqlite":
        yield ConversationStore(tmp_path / "memory.sqlite3")
    else:
        url = os.environ.get("ADVISTA_TITLE_TEST_DATABASE_URL")
        administrator = None
        database = None
        schema = None
        try:
            if url:
                administrator = Database(DatabaseConfig(url=SecretStr(url)))
                schema = f"title_review_{uuid.uuid4().hex}"
                with administrator.engine.begin() as connection:
                    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                parts = urlsplit(url)
                query = [
                    (key, value)
                    for key, value in parse_qsl(parts.query)
                    if key != "options"
                ]
                query.append(("options", f"-csearch_path={schema}"))
                url = urlunsplit(parts._replace(query=urlencode(query)))
            else:
                url = f"sqlite+pysqlite:///{tmp_path / 'metadata.db'}"
            database = Database(DatabaseConfig(url=SecretStr(url)))
            Base.metadata.create_all(database.engine)
            owner, _ = UserRepository(database).create_api_user("owner")
            yield PostgresConversationStore(database, user_id=owner.user_id)
        finally:
            try:
                if database is not None:
                    database.close()
            finally:
                if administrator is not None:
                    try:
                        if schema is not None:
                            with administrator.engine.begin() as connection:
                                connection.execute(
                                    text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                                )
                    finally:
                        administrator.close()


def _session(store, identifier="general_one"):
    store.create_session(
        session_id=identifier,
        run_id=f"general_{identifier}",
        source_path=Path("video.mp4"),
        goal="原始任务目标",
    )


def test_title_persists_without_session_recreation_overwriting_it(store):
    _session(store)
    assert store.set_title("general_one", "用户自定标题") == "用户自定标题"
    _session(store)
    assert store.session("general_one")["title"] == "用户自定标题"
    sessions = store.list_sessions()
    assert sessions[0]["session_id"] == "general_one"
    assert sessions[0]["title"] == "用户自定标题"
    assert {"run_id", "status", "created_at", "updated_at"}.issubset(sessions[0])
    if isinstance(store, ConversationStore):
        reopened = ConversationStore(store.path)
    else:
        reopened = PostgresConversationStore(store.database, user_id=store.user_id)
    assert reopened.session("general_one")["title"] == "用户自定标题"


def test_auto_title_only_fills_null_or_empty_and_preserves_manual_generic_name(store):
    _session(store)
    store.set_title("general_one", "")
    assert store.set_title("general_one", "内容摘要", only_if_empty=True) == "内容摘要"
    assert (
        store.set_title("general_one", "另一个自动摘要", only_if_empty=True)
        == "内容摘要"
    )
    store.set_title("general_one", "新对话")
    assert store.set_title("general_one", "不应替换", only_if_empty=True) == "新对话"
    _session(store, "general_two")
    if isinstance(store, PostgresConversationStore):
        with store.database.session() as session:
            session.get(Conversation, "general_two").title = None
    assert (
        store.set_title("general_two", "从空值生成", only_if_empty=True) == "从空值生成"
    )


def test_list_filters_general_literal_prefix_and_applies_limit(store):
    _session(store)
    store.create_session(
        session_id="video",
        run_id="ingest_video",
        source_path=Path("video.mp4"),
        goal="视频目标",
    )
    store.create_session(
        session_id="lookalike",
        run_id="generalX_video",
        source_path=Path("video.mp4"),
        goal="不是普通会话",
    )
    assert {item["session_id"] for item in store.list_sessions(general_only=True)} == {
        "general_one"
    }
    assert len(store.list_sessions(limit=1)) == 1
    assert store.list_sessions(limit=0) == []


def test_postgres_list_and_rename_enforce_owner_and_deleted_rows(tmp_path):
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'ownership.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        users = UserRepository(database)
        owner, _ = users.create_api_user("owner")
        other, _ = users.create_api_user("other")
        owned = PostgresConversationStore(database, user_id=owner.user_id)
        other_store = PostgresConversationStore(database, user_id=other.user_id)
        _session(owned, "owned")
        _session(other_store, "other")
        _session(owned, "deleted")
        _session(owned, "wrong_type")
        with database.session() as session:
            session.get(Conversation, "deleted").deleted_at = datetime.now(UTC)
            session.get(Conversation, "wrong_type").conversation_type = "video_chat"
        assert {item["session_id"] for item in owned.list_sessions()} == {
            "owned",
            "wrong_type",
        }
        assert {
            item["session_id"] for item in owned.list_sessions(general_only=True)
        } == {"owned"}
        with pytest.raises(PermissionError):
            owned.set_title("other", "非法跨用户改名")
        assert other_store.session("other")["title"] == "原始任务目标"
        with pytest.raises(FileNotFoundError):
            owned.set_title("deleted", "不能恢复已删除会话")
    finally:
        database.close()


def test_sqlite_legacy_database_adds_title_without_changing_goal_or_metadata(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_path TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "general_legacy",
                "video.mp4",
                "原始目标",
                "active",
                "2020-01-01",
                "2020-01-01",
            ),
        )
    for _ in range(2):
        store = ConversationStore(path)
        assert store.session("legacy")["goal"] == "原始目标"
    assert store.session("legacy")["title"] is None
    store.set_title("legacy", "手动名称")
    store.create_session(
        session_id="legacy",
        run_id="general_legacy",
        source_path=Path("video.mp4"),
        goal="原始目标",
    )
    assert store.session("legacy")["goal"] == "原始目标"
    assert store.session("legacy")["title"] == "手动名称"
    with pytest.raises(ValueError, match="metadata conflict"):
        store.create_session(
            session_id="legacy",
            run_id="general_legacy",
            source_path=Path("video.mp4"),
            goal="不同目标",
        )
    with sqlite3.connect(path) as connection:
        assert [
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")
        ].count("title") == 1


def test_missing_session_cannot_be_renamed(store):
    with pytest.raises(FileNotFoundError):
        store.set_title("missing", "名称")


def test_concurrent_automatic_and_manual_titles_keep_manual_name(store):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    _session(store)
    store.set_title("general_one", "")
    ready = threading.Barrier(2)

    def automatic():
        ready.wait(timeout=5)
        return store.set_title("general_one", "自动摘要", only_if_empty=True)

    def manual():
        ready.wait(timeout=5)
        return store.set_title("general_one", "用户命名")

    with ThreadPoolExecutor(max_workers=2) as workers:
        auto = workers.submit(automatic)
        renamed = workers.submit(manual)
        auto.result(timeout=5)
        renamed.result(timeout=5)
    assert store.session("general_one")["title"] == "用户命名"


def test_sqlite_legacy_upgrade_supports_concurrent_initialization(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_path TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "general_legacy",
                "video.mp4",
                "保留目标",
                "active",
                "2020-01-01",
                "2020-01-01",
            ),
        )
    ready = threading.Barrier(4)

    def initialize(_index):
        ready.wait(timeout=5)
        return ConversationStore(path).session("legacy")

    with ThreadPoolExecutor(max_workers=4) as workers:
        sessions = list(workers.map(initialize, range(4)))
    assert all(
        item["goal"] == "保留目标" and item["title"] is None for item in sessions
    )
    with sqlite3.connect(path) as connection:
        assert [
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")
        ].count("title") == 1
