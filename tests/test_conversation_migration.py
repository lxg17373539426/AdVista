from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import (
    Base,
    Database,
    PostgresConversationStore,
    migrate_sqlite_conversations,
)
from ad_vista_agent.database.models import (
    AnswerVersion,
    Conversation,
    Feedback,
    Message,
)
from ad_vista_agent.memory import ConversationStore


def test_migrate_legacy_sqlite_conversations_is_idempotent(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "conversations.sqlite3"
    with sqlite3.connect(sqlite_path) as source:
        source.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                source_path TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                citations_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE feedback (
                feedback_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                message_id TEXT, decision TEXT NOT NULL, note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE answer_versions (
                version_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                message_id TEXT NOT NULL, answer_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES
                ('session_1', 'run_1', 'video.mp4', '分析卖点', 'active', '2026-01-01', '2026-01-01');
            INSERT INTO messages VALUES
                ('msg_1', 'session_1', 'user', '分析卖点', '[]', '2026-01-01T00:00:01');
            INSERT INTO messages VALUES
                ('msg_2', 'session_1', 'assistant', '核心卖点是清洁', '["speech_0001"]', '2026-01-01T00:00:02');
            INSERT INTO feedback VALUES
                ('feedback_1', 'session_1', 'msg_2', 'approve', '正确', '2026-01-01T00:00:03');
            INSERT INTO answer_versions VALUES
                ('version_1', 'session_1', 'msg_2', '{"answer":"核心卖点是清洁"}', '2026-01-01T00:00:02');
            """
        )

    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'target.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        first = migrate_sqlite_conversations(sqlite_path, database)
        second = migrate_sqlite_conversations(sqlite_path, database)
        assert first["sessions"] == 1
        assert first["messages"] == 2
        assert first["feedback"] == 1
        assert first["answer_versions"] == 1
        assert second["messages"] == 0
        assert second["feedback"] == 0
        assert second["answer_versions"] == 0
        with database.session() as session:
            message = session.get(Message, "msg_2")
            feedback = session.get(Feedback, "feedback_1")
            version = session.get(AnswerVersion, "version_1")
            assert message is not None and message.content == "核心卖点是清洁"
            assert feedback is not None and feedback.decision == "approve"
            assert version is not None and version.answer_json["answer"] == "核心卖点是清洁"
    finally:
        database.close()


def _source_with_manual_title(tmp_path: Path) -> ConversationStore:
    source = ConversationStore(tmp_path / "named-conversations.sqlite3")
    source.create_session(
        session_id="session_named",
        run_id="general_named",
        source_path=Path("video.mp4"),
        goal="原始分析目标",
    )
    source.set_title("session_named", "用户手动名称")
    return source


def test_migrate_sqlite_preserves_manual_title(tmp_path: Path) -> None:
    source = _source_with_manual_title(tmp_path)
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'target.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        result = migrate_sqlite_conversations(source.path, database)
        assert result["sessions"] == 1
        with database.session() as session:
            imported = session.get(Conversation, "session_named")
            assert imported is not None
            assert imported.title == "用户手动名称"
            assert imported.run_id == "general_named"
        assert source.session("session_named")["goal"] == "原始分析目标"
        assert source.session("session_named")["source_path"] == "video.mp4"
    finally:
        database.close()


def test_repeat_migration_preserves_target_manual_title(tmp_path: Path) -> None:
    source = _source_with_manual_title(tmp_path)
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'target.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        migrate_sqlite_conversations(source.path, database)
        target = PostgresConversationStore(database)
        target.set_title("session_named", "目标库中的新名称")
        source.set_title("session_named", "旧库中的后续名称")
        migrate_sqlite_conversations(source.path, database)
        assert target.session("session_named")["title"] == "目标库中的新名称"
        assert source.session("session_named")["goal"] == "原始分析目标"
    finally:
        database.close()
