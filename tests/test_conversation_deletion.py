from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import (
    Base,
    Database,
    UserRepository,
    migrate_sqlite_conversations,
)
from ad_vista_agent.database.models import Conversation, Message
from ad_vista_agent.web.app import create_server
from ad_vista_agent.web.service import WebService


@pytest.fixture(params=["database", "legacy_sqlite"])
def service(request, tmp_path):
    base = load_settings()
    administrator = None
    current = None
    schema = None
    try:
        config = DatabaseConfig()
        if request.param == "database":
            url = os.environ.get("ADVISTA_DELETE_TEST_DATABASE_URL")
            if url:
                administrator = Database(DatabaseConfig(url=SecretStr(url)))
                schema = f"deletion_review_{uuid.uuid4().hex}"
                with administrator.engine.begin() as connection:
                    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                url = (
                    make_url(url)
                    .update_query_dict({"options": f"-csearch_path={schema}"})
                    .render_as_string(hide_password=False)
                )
            else:
                url = f"sqlite+pysqlite:///{tmp_path / 'service.db'}"
            config = DatabaseConfig(url=SecretStr(url))
            database = Database(config)
            try:
                Base.metadata.create_all(database.engine)
            finally:
                database.close()
        settings = base.model_copy(
            update={
                "paths": base.paths.model_copy(
                    update={"output_root": tmp_path / "outputs"}
                ),
                "database": config,
            }
        )
        current = WebService(settings)
        yield current
    finally:
        try:
            if current is not None:
                current.close()
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


def _general(service, *, user_id=None):
    store = service._conversation_store(user_id=user_id)
    store.create_session(
        session_id="chat_deleted",
        run_id="general_chat_deleted",
        source_path=Path("."),
        goal="普通对话",
    )
    store.add_message("chat_deleted", "user", "保留历史消息", [])
    return store


def test_soft_delete_is_idempotent_hides_every_store_access_and_keeps_data(service):
    store = _general(service)
    expected = {"status": "ok", "session_id": "chat_deleted"}
    assert service.delete_conversation("chat_deleted") == expected
    assert service.delete_conversation("chat_deleted") == expected
    assert service.conversations() == []
    for action in (
        lambda: store.session("chat_deleted"),
        lambda: store.messages("chat_deleted"),
        lambda: store.set_title("chat_deleted", "不能改名"),
        lambda: store.set_status("chat_deleted", "active"),
        lambda: store.add_message("chat_deleted", "assistant", "不能追加", []),
        lambda: store.add_feedback("chat_deleted", "approve", "不能追加"),
        lambda: store.add_version(
            "chat_deleted", "existing_message", {"answer": "不能追加"}
        ),
        lambda: store.create_session(
            session_id="chat_deleted",
            run_id="general_chat_deleted",
            source_path=Path("."),
            goal="普通对话",
        ),
    ):
        with pytest.raises(FileNotFoundError):
            action()
    if service.database is not None:
        with service.database.session() as session:
            assert session.get(Conversation, "chat_deleted").deleted_at is not None
            assert session.scalar(select(func.count()).select_from(Message)) == 1
    else:
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT deleted_at FROM sessions").fetchone()[0]
            assert (
                connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
            )


def test_delete_does_not_disclose_or_modify_another_owner(service):
    if service.database is None:
        pytest.skip("Local SQLite has one owner")
    owner, _ = UserRepository(service.database).create_api_user("owner")
    other, _ = UserRepository(service.database).create_api_user("other")
    store = _general(service, user_id=owner.user_id)
    for identifier in ("chat_deleted", "unknown_session"):
        with pytest.raises(FileNotFoundError):
            service.delete_conversation(identifier, user_id=other.user_id)
    assert store.session("chat_deleted")["status"] == "active"
    assert service.conversations(user_id=other.user_id) == []


def test_deleted_chat_cannot_be_recreated_or_call_the_model(service, monkeypatch):
    _general(service)
    service.delete_conversation("chat_deleted")
    monkeypatch.setattr(
        "ad_vista_agent.web.service.cancellable_urlopen",
        lambda *a, **kw: pytest.fail("deleted session requested model"),
    )
    for action in (
        lambda: service.general_chat("继续", "chat_deleted"),
        lambda: service.general_messages("chat_deleted"),
        lambda: service.rename_conversation("chat_deleted", "新标题"),
        lambda: service.generate_conversation_title("chat_deleted"),
    ):
        with pytest.raises(FileNotFoundError):
            action()


@pytest.mark.parametrize("operation", ["chat", "title"])
def test_delete_wins_against_inflight_model_result(service, monkeypatch, operation):
    store = _general(service)
    store.set_title("chat_deleted", "")
    entered = threading.Event()
    release = threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        if operation == "title":
            return "晚到的摘要"
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": "晚到的回答"}}]}).encode()
        )

    target = "generate_title" if operation == "title" else "cancellable_urlopen"
    monkeypatch.setattr(f"ad_vista_agent.web.service.{target}", delayed)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(
            service.generate_conversation_title
            if operation == "title"
            else service.general_chat,
            *(["chat_deleted"] if operation == "title" else ["继续", "chat_deleted"]),
        )
        try:
            assert entered.wait(timeout=5)
            service.delete_conversation("chat_deleted")
        finally:
            release.set()
        with pytest.raises(FileNotFoundError):
            future.result(timeout=5)
    assert service.conversations() == []
    if service.database is not None:
        with service.database.session() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.role == "assistant")
                )
                == 0
            )
    else:
        with sqlite3.connect(store.path) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE role='assistant'"
                ).fetchone()[0]
                == 0
            )


def _video(service, tmp_path, monkeypatch):
    asset_id = "ingest_" + "1" * 20
    execution_id = "exec_" + "2" * 32
    directory = service.store.run_dir(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    report = service.artifact_root(execution_id) / "report" / "report.html"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("保留报告", encoding="utf-8")
    state = SimpleNamespace(
        session_id="legacy_actual_session",
        run_id=asset_id,
        execution_id=execution_id,
        source_path=tmp_path / "video.mp4",
        request=SimpleNamespace(goal="原始视频目标"),
        status="completed",
    )
    monkeypatch.setattr(
        "ad_vista_agent.web.service.load_agent_session",
        lambda *a: (directory, directory, state),
    )
    service._video_title_session(execution_id, user_id=None)
    return execution_id, state, report


def test_video_deletion_uses_actual_session_and_blocks_all_run_aliases(
    service, tmp_path, monkeypatch
):
    identifier, state, report = _video(service, tmp_path, monkeypatch)
    result = service.delete_run(identifier)
    assert result == {
        "status": "ok",
        "session_id": state.session_id,
        "run_id": identifier,
    }
    assert service.delete_run(identifier) == result
    assert report.read_text(encoding="utf-8") == "保留报告"
    for alias in (identifier, state.run_id):
        for action in (
            lambda alias=alias: service.authorize_run(alias, user_id=None),
            lambda alias=alias: service.run(alias),
            lambda alias=alias: service.messages(alias),
            lambda alias=alias: service.chat(alias, "继续"),
            lambda alias=alias: service.rename_run(alias, "不能恢复"),
            lambda alias=alias: service.generate_run_title(alias),
        ):
            with pytest.raises(FileNotFoundError):
                action()


def test_running_video_cannot_be_deleted_before_cancellation(
    service, tmp_path, monkeypatch
):
    from ad_vista_agent.errors import ConversationBusyError

    identifier, state, _ = _video(service, tmp_path, monkeypatch)
    service.jobs["running"] = {
        "job_id": "running",
        "execution_id": identifier,
        "conversation_id": state.session_id,
        "status": "running",
    }
    with pytest.raises(ConversationBusyError, match="取消"):
        service.delete_run(identifier)
    with pytest.raises(ConversationBusyError):
        service.delete_conversation(state.session_id)
    assert service._conversation_store().session(state.session_id)["status"] == "active"


def test_video_without_execution_record_still_enforces_session_owner(
    service, tmp_path, monkeypatch
):
    if service.database is None:
        pytest.skip("Local SQLite has one owner")
    identifier, state, report = _video(service, tmp_path, monkeypatch)
    other, _ = UserRepository(service.database).create_api_user("other-video")
    with pytest.raises(FileNotFoundError):
        service.delete_run(identifier, user_id=other.user_id)
    with pytest.raises(PermissionError):
        service.authorize_run(identifier, user_id=other.user_id)
    assert service._conversation_store().session(state.session_id)["status"] == "active"
    assert report.is_file()


def test_running_database_execution_requires_cancellation_even_without_local_job(
    service, tmp_path, monkeypatch
):
    from ad_vista_agent.errors import ConversationBusyError

    if service.database is None:
        pytest.skip("Requires request/execution tables")
    identifier, state, _ = _video(service, tmp_path, monkeypatch)
    service.request_repository.create_request(
        request_id="running_request",
        user_id="system",
        conversation_id=state.session_id,
        request_type="video_analysis",
        input_json={},
        idempotency_key="running",
    )
    service.execution_repository.create(
        execution_id=identifier,
        request_id="running_request",
        model_version=None,
        prompt_version=None,
    )
    with pytest.raises(ConversationBusyError):
        service.delete_run(identifier)
    with pytest.raises(ConversationBusyError):
        service._conversation_store().delete_session(state.session_id)
    service.execution_repository.update(identifier, status="cancelled")
    assert service.delete_run(identifier)["status"] == "ok"


def test_deleted_session_blocks_request_creation_writers_and_replay(service):
    if service.database is None:
        pytest.skip("Requires request tables")
    store = _general(service)
    repository = service.request_repository
    repository.create_request(
        request_id="to_delete",
        user_id="system",
        conversation_id="chat_deleted",
        request_type="general_chat",
        input_json={},
        idempotency_key="deleted_key",
    )
    store.complete_response(
        "chat_deleted",
        "to_delete",
        {"answer": "历史回答", "evidence_refs": []},
        question="历史问题",
    )
    service.delete_conversation("chat_deleted")
    for action in (
        lambda: repository.ensure_conversation(
            "chat_deleted",
            user_id="system",
            run_id="general_chat_deleted",
            conversation_type="general_chat",
        ),
        lambda: repository.create_request(
            request_id="blocked",
            user_id="system",
            conversation_id="chat_deleted",
            request_type="general_chat",
            input_json={},
            idempotency_key="new_key",
        ),
        lambda: repository.messages("chat_deleted", user_id="system"),
        lambda: repository.add_assistant_message(
            message_id="blocked_message",
            conversation_id="chat_deleted",
            user_id="system",
            content="不能追加",
            request_id="to_delete",
            citations=[],
        ),
        lambda: store.response_for_request("to_delete"),
        lambda: store.complete_response(
            "chat_deleted",
            "to_delete",
            {"answer": "不能重放", "evidence_refs": []},
            question="历史问题",
        ),
    ):
        with pytest.raises(FileNotFoundError):
            action()


@pytest.mark.parametrize("first", ["delete", "write"])
def test_postgresql_delete_and_message_writes_are_serialized(service, first):
    if service.database is None or service.database.engine.dialect.name != "postgresql":
        pytest.skip("Set ADVISTA_DELETE_TEST_DATABASE_URL to a private PostgreSQL URL")
    store = _general(service)
    held = threading.Event()
    contender = threading.Event()
    release = threading.Event()

    def pause_holder(connection, cursor, statement, parameters, context, executemany):
        is_write = statement.startswith("INSERT INTO messages")
        is_delete = (
            statement.startswith("UPDATE conversations SET")
            and "deleted_at" in statement
        )
        if (first == "write" and is_write) or (first == "delete" and is_delete):
            held.set()
            assert release.wait(timeout=5)
        if held.is_set() and "FOR UPDATE" in statement:
            contender.set()

    event.listen(service.database.engine, "before_cursor_execute", pause_holder)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            write = lambda: store.add_message(
                "chat_deleted", "assistant", "历史回答", []
            )
            delete = lambda: service.delete_conversation("chat_deleted")
            holder = workers.submit(write if first == "write" else delete)
            try:
                assert held.wait(timeout=5)
                blocked = workers.submit(delete if first == "write" else write)
                assert contender.wait(timeout=5)
                assert not blocked.done()
            finally:
                release.set()
            holder.result(timeout=5)
            if first == "delete":
                with pytest.raises(FileNotFoundError):
                    blocked.result(timeout=5)
            else:
                blocked.result(timeout=5)
        with service.database.session() as session:
            conversation = session.get(Conversation, "chat_deleted")
            messages = list(
                session.scalars(select(Message).where(Message.role == "assistant"))
            )
            assert conversation.deleted_at is not None
            assert len(messages) == (1 if first == "write" else 0)
            assert all(item.created_at <= conversation.deleted_at for item in messages)
    finally:
        release.set()
        event.remove(service.database.engine, "before_cursor_execute", pause_holder)


def test_postgresql_idempotency_conflict_does_not_replay_deleted_conversation(service):
    if service.database is None or service.database.engine.dialect.name != "postgresql":
        pytest.skip("Set ADVISTA_DELETE_TEST_DATABASE_URL to a private PostgreSQL URL")
    repository = service.request_repository
    for identifier in ("left", "right"):
        repository.ensure_conversation(
            identifier,
            user_id="system",
            run_id=f"general_{identifier}",
            conversation_type="general_chat",
        )
    injected = False

    def create_and_delete_competitor(
        connection, cursor, statement, parameters, context, executemany
    ):
        nonlocal injected
        if (
            injected
            or "FROM requests" not in statement
            or "idempotency_key" not in statement
        ):
            return
        injected = True
        repository.create_request(
            request_id="competitor",
            user_id="system",
            conversation_id="left",
            request_type="general_chat",
            input_json={},
            idempotency_key="same_key",
        )
        service._conversation_store().delete_session("left")

    event.listen(
        service.database.engine, "after_cursor_execute", create_and_delete_competitor
    )
    try:
        with pytest.raises(FileNotFoundError):
            repository.create_request(
                request_id="late_request",
                user_id="system",
                conversation_id="right",
                request_type="general_chat",
                input_json={},
                idempotency_key="same_key",
            )
        assert injected
        assert repository.get_request("competitor").status == "cancelled"
    finally:
        event.remove(
            service.database.engine,
            "after_cursor_execute",
            create_and_delete_competitor,
        )


def test_migration_skips_deleted_sqlite_sessions(service, tmp_path):
    if service.database is not None:
        pytest.skip("Source must be SQLite")
    source = _general(service)
    service.delete_conversation("chat_deleted")
    target = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'target.db'}"))
    )
    try:
        Base.metadata.create_all(target.engine)
        result = migrate_sqlite_conversations(source.path, target)
        assert result["sessions"] == 0
        assert result["skipped"] == 1
        with target.session() as session:
            assert session.get(Conversation, "chat_deleted") is None
    finally:
        target.close()


def test_http_delete_routes_and_old_links_return_not_found(
    service, monkeypatch, tmp_path
):
    _general(service)
    identifier, state, report = _video(service, tmp_path, monkeypatch)
    monkeypatch.setattr("ad_vista_agent.web.app.WebService", lambda settings: service)
    settings = service.settings.model_copy(
        update={"web": service.settings.web.model_copy(update={"port": 0})}
    )
    server = create_server(settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, method="GET", body=None):
        payload = json.dumps(body).encode() if body is not None else None
        with urlopen(
            Request(
                base + path,
                data=payload,
                method=method,
                headers={"Content-Type": "application/json"},
            ),
            timeout=5,
        ) as response:
            return json.load(response)

    try:
        service.jobs["running"] = {
            "job_id": "running",
            "execution_id": identifier,
            "conversation_id": state.session_id,
            "status": "running",
        }
        with pytest.raises(HTTPError) as busy:
            call(f"/api/runs/{identifier}", "DELETE")
        assert busy.value.code == 409
        assert "取消" in busy.value.read().decode()
        service.jobs["running"]["status"] = "cancelled"
        assert call("/api/conversations/chat_deleted", "DELETE")["status"] == "ok"
        assert (
            call(f"/api/runs/{identifier}", "DELETE")["session_id"] == state.session_id
        )
        assert call(f"/api/runs/{identifier}", "DELETE")["status"] == "ok"
        for path, method, body in (
            ("/api/chat/messages?session_id=chat_deleted", "GET", None),
            ("/api/chat", "POST", {"session_id": "chat_deleted", "message": "继续"}),
            ("/api/conversations/chat_deleted/title", "POST", {"title": "不能恢复"}),
            (f"/api/runs/{identifier}/messages", "GET", None),
            (f"/api/runs/{identifier}/exports/report-html", "GET", None),
            (f"/api/runs/{identifier}/messages", "POST", {"question": "继续"}),
        ):
            with pytest.raises(HTTPError) as missing:
                call(path, method, body)
            assert missing.value.code == 404
        assert report.is_file()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
