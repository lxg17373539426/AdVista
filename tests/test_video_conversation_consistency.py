from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select, text

from ad_vista_agent.agent import chat
from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database, PostgresConversationStore
from ad_vista_agent.database.models import AnswerVersion, Feedback, Message, Request
from ad_vista_agent.web.service import WebService


@pytest.fixture
def video(tmp_path, monkeypatch):
    base = load_settings()
    url = os.environ.get("ADVISTA_VIDEO_TEST_DATABASE_URL")
    if url:
        # The disposable PostgreSQL harness supplies this URL; every test gets its own schema.
        schema = f"review_{uuid.uuid4().hex}"
        database = Database(DatabaseConfig(url=SecretStr(url)))
        with database.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database.close()
        parts = urlsplit(url)
        query = parse_qsl(parts.query) + [("options", f"-csearch_path={schema}")]
        url = urlunsplit(parts._replace(query=urlencode(query)))
    else:
        url = f"sqlite+pysqlite:///{tmp_path / 'video.db'}"
    settings = base.model_copy(
        update={
            "paths": base.paths.model_copy(
                update={"output_root": tmp_path / "outputs"}
            ),
            "database": DatabaseConfig(url=SecretStr(url)),
        }
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.close()
    service = WebService(settings)
    repository = service.request_repository
    repository.ensure_conversation(
        "conversation_video",
        user_id="system",
        run_id=None,
        conversation_type="video_chat",
    )
    repository.create_request(
        request_id="initial",
        user_id="system",
        conversation_id="conversation_video",
        request_type="video",
        input_json={},
        idempotency_key="initial",
    )
    service.execution_repository.create(
        execution_id="exec_00000000000000000000000000000000",
        request_id="initial",
        model_version=None,
        prompt_version=None,
    )
    repository.mark_completed("initial")
    state = SimpleNamespace(
        session_id="conversation_video",
        execution_id="exec_00000000000000000000000000000000",
        run_id="asset_video",
        source_path=tmp_path / "video.mp4",
        request=SimpleNamespace(goal="识别产品"),
        plan=SimpleNamespace(response_mode="answer"),
        status="completed",
        deliverables={},
        confirmations=[],
    )
    monkeypatch.setattr(chat, "_resolve_session", lambda *args: (tmp_path, state))
    monkeypatch.setattr(chat, "_context", lambda *args: ({}, set()))
    monkeypatch.setattr(chat, "_latest_context_version", lambda *args: 7)
    monkeypatch.setattr(
        "ad_vista_agent.web.service.load_agent_session",
        lambda *args: (tmp_path, tmp_path, state),
    )
    monkeypatch.setattr(service, "_asset_run_id", lambda *args: "asset_video")
    try:
        yield service, settings, state
    finally:
        service.close()


def test_answer_history_and_feedback_share_database_message_identity(video):
    service, settings, _ = video
    result = service.chat(
        "exec_00000000000000000000000000000000",
        "任务状态",
        user_id="system",
        idempotency_key="status",
    )
    history = service.messages("exec_00000000000000000000000000000000")["messages"]
    assert history[-1]["message_id"] == result["assistant_message_id"]
    assert history[-2]["message_id"] == result["user_message_id"]
    with service.database.session() as session:
        version = session.get(AnswerVersion, result["version_id"])
        assert version.message_id == result["assistant_message_id"]
        assert version.answer_json == result
    feedback = service.feedback(
        "exec_00000000000000000000000000000000",
        {"decision": "approve", "message_id": history[-1]["message_id"]},
        user_id="system",
    )
    with service.database.session() as session:
        assert (
            session.get(Feedback, feedback["feedback_id"]).message_id
            == result["assistant_message_id"]
        )
    assert not chat.conversation_db(settings).exists()


def test_completed_retry_replays_full_response_without_generating_again(
    video, monkeypatch
):
    service, _, _ = video
    count = 0
    original = chat._workspace_answer

    def counted(*args):
        nonlocal count
        count += 1
        return original(*args)

    monkeypatch.setattr(chat, "_workspace_answer", counted)
    first = service.chat(
        "exec_00000000000000000000000000000000",
        "任务状态",
        user_id="system",
        idempotency_key="retry",
    )
    chunks = []
    second = service.chat_stream(
        "exec_00000000000000000000000000000000",
        "任务状态",
        chunks.append,
        user_id="system",
        idempotency_key="retry",
    )
    assert second == first
    assert "".join(chunks) == first["answer"]
    assert count == 1
    assert first["context_version"] == 7
    messages = service.request_repository.messages(
        "conversation_video", user_id="system"
    )
    assert len([item for item in messages if item.role == "assistant"]) == 1


def test_idempotency_key_cannot_replay_another_question(video):
    service, _, _ = video
    service.chat(
        "exec_00000000000000000000000000000000",
        "任务状态",
        user_id="system",
        idempotency_key="conflict",
    )
    with pytest.raises(ValueError, match="different video request"):
        service.chat(
            "exec_00000000000000000000000000000000",
            "你好",
            user_id="system",
            idempotency_key="conflict",
        )


def test_failed_retry_does_not_execute_model_again(video, monkeypatch):
    service, _, _ = video

    def failed(*args, **kwargs):
        raise TimeoutError("test failure")

    monkeypatch.setattr(chat, "_workspace_answer", failed)
    with pytest.raises(TimeoutError):
        service.chat(
            "exec_00000000000000000000000000000000",
            "任务状态",
            user_id="system",
            idempotency_key="failed",
        )
    with pytest.raises(ValueError, match="new idempotency key"):
        service.chat(
            "exec_00000000000000000000000000000000",
            "任务状态",
            user_id="system",
            idempotency_key="failed",
        )
    with service.database.session() as session:
        request = session.scalar(
            select(Request).where(Request.idempotency_key == "failed")
        )
        assert request.status == "failed"
        assert session.scalar(select(func.count()).select_from(AnswerVersion)) == 0


def test_rejected_feedback_has_no_sqlite_or_database_side_effect(video):
    service, settings, _ = video
    result = service.chat(
        "exec_00000000000000000000000000000000",
        "任务状态",
        user_id="system",
        idempotency_key="feedback",
    )
    with pytest.raises(PermissionError):
        service.feedback(
            "exec_00000000000000000000000000000000",
            {"decision": "approve", "message_id": result["assistant_message_id"]},
            user_id="other",
        )
    with pytest.raises(KeyError):
        service.feedback(
            "exec_00000000000000000000000000000000",
            {"decision": "approve", "message_id": "missing"},
            user_id="system",
        )
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(Feedback)) == 0
    assert not chat.conversation_db(settings).exists()


def test_response_transaction_rolls_back_message_version_and_completion(video):
    service, _, _ = video
    request_id, _, _ = service._create_db_request(
        user_id="system",
        conversation_id="conversation_video",
        request_type="video_chat",
        input_json={"question": "test"},
        content="test",
        idempotency_key="atomic",
    )
    store = PostgresConversationStore(service.database)

    def fail_insert(*args):
        raise RuntimeError("version insert failed")

    event.listen(AnswerVersion, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="version insert failed"):
            store.complete_response(
                "conversation_video",
                request_id,
                {"answer": "test", "evidence_refs": []},
                question="test",
            )
    finally:
        event.remove(AnswerVersion, "before_insert", fail_insert)
    with service.database.session() as session:
        assert session.get(Request, request_id).status == "pending"
        messages = list(
            session.scalars(select(Message).where(Message.request_id == request_id))
        )
        assert len(messages) == 1 and messages[0].status == "pending"
        assert session.scalar(select(func.count()).select_from(AnswerVersion)) == 0


def test_standalone_cli_question_does_not_overwrite_initial_request(video):
    service, settings, _ = video
    result = chat.ask_agent(
        "exec_00000000000000000000000000000000", "任务状态", settings
    )
    assert (
        result["assistant_message_id"]
        == service.messages("exec_00000000000000000000000000000000")["messages"][-1][
            "message_id"
        ]
    )
    with service.database.session() as session:
        assert session.get(Request, "initial").status == "completed"
        message = session.get(Message, result["assistant_message_id"])
        assert message.request_id != "initial"


def test_sqlite_fallback_preserves_answer_history_and_feedback(video):
    _, settings, state = video
    settings = settings.model_copy(update={"database": DatabaseConfig()})
    state.execution_id = None
    result = chat.ask_agent("asset_video", "任务状态", settings)
    history = chat.show_conversation("asset_video", settings)
    assert history["messages"][-1]["message_id"] == result["assistant_message_id"]
    assert (
        chat.record_feedback(
            "asset_video",
            "approve",
            "",
            settings,
            message_id=result["assistant_message_id"],
        )["status"]
        == "ok"
    )


def test_delivery_failure_preserves_committed_response_for_retry(video):
    service, _, _ = video

    def disconnected(_chunk):
        raise BrokenPipeError("client disconnected")

    with pytest.raises(BrokenPipeError):
        service.chat_stream(
            "exec_00000000000000000000000000000000",
            "任务状态",
            disconnected,
            user_id="system",
            idempotency_key="disconnect",
        )
    with service.database.session() as session:
        request = session.scalar(
            select(Request).where(Request.idempotency_key == "disconnect")
        )
        assert request.status == "completed"
    result = service.chat(
        "exec_00000000000000000000000000000000",
        "任务状态",
        user_id="system",
        idempotency_key="disconnect",
    )
    assert result["status"] == "ok"


def test_explicit_initial_question_key_is_respected(video, monkeypatch):
    service, _, _ = video
    original = chat._workspace_answer
    monkeypatch.setattr(
        chat, "_workspace_answer", lambda question, *args: original("任务状态", *args)
    )
    first = service.chat(
        "exec_00000000000000000000000000000000",
        "识别产品",
        user_id="system",
        idempotency_key="initial-answer",
    )
    second = service.chat(
        "exec_00000000000000000000000000000000",
        "识别产品",
        user_id="system",
        idempotency_key="initial-answer",
    )
    assert first == second


def test_standalone_cli_failure_finishes_diagnostic_request(video, monkeypatch):
    service, settings, _ = video

    def failure(*args, **kwargs):
        raise TimeoutError("model failed")

    monkeypatch.setattr(chat, "_workspace_answer", failure)
    with pytest.raises(TimeoutError):
        chat.ask_agent("exec_00000000000000000000000000000000", "任务状态", settings)
    with service.database.session() as session:
        request = session.scalar(
            select(Request).where(Request.request_type == "video_chat")
        )
        assert request.status == "failed"


def test_separate_services_serialize_duplicate_requests(video, monkeypatch):
    service, settings, _ = video
    other = WebService(settings)
    monkeypatch.setattr(other, "_asset_run_id", lambda *args: "asset_video")
    original = chat._workspace_answer
    calls = []

    def slow(*args):
        calls.append(True)
        time.sleep(0.05)
        return original(*args)

    monkeypatch.setattr(chat, "_workspace_answer", slow)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    instance.chat,
                    "exec_00000000000000000000000000000000",
                    "任务状态",
                    user_id="system",
                    idempotency_key="parallel",
                )
                for instance in (service, other)
            ]
            results = [future.result(timeout=10) for future in futures]
        assert results[0] == results[1]
        assert len(calls) == 1
    finally:
        other.close()


def test_replay_survives_service_restart_and_preserves_report_link(video):
    service, settings, state = video
    report = (
        service.store.execution_dir(state.execution_id) / "artifacts/report/report.html"
    )
    report.parent.mkdir(parents=True)
    report.write_text("<html>report</html>", encoding="utf-8")
    first = service.chat(
        state.execution_id, "任务状态", user_id="system", idempotency_key="durable"
    )
    assert first["report_url"] == f"/api/runs/{state.execution_id}/exports/report-html"
    service.close()
    restarted = WebService(settings)
    try:
        second = restarted._video_chat(
            state.execution_id, "任务状态", user_id="system", idempotency_key="durable"
        )
        assert second == first
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "execution_id", [None, "exec_11111111111111111111111111111111"]
)
def test_legacy_video_with_database_supports_replay_history_and_feedback(
    video, execution_id
):
    service, settings, state = video
    state.execution_id = execution_id
    result = service.chat(
        "asset_video", "任务状态", user_id="system", idempotency_key="legacy"
    )
    assert (
        service.chat(
            "asset_video", "任务状态", user_id="system", idempotency_key="legacy"
        )
        == result
    )
    history = service.messages("asset_video")["messages"]
    assert history[-1]["message_id"] == result["assistant_message_id"]
    feedback = service.feedback(
        "asset_video",
        {"decision": "approve", "message_id": result["assistant_message_id"]},
        user_id="system",
    )
    with service.database.session() as session:
        assert session.get(Feedback, feedback["feedback_id"]).execution_id is None
        assert (
            session.scalar(
                select(Request).where(Request.idempotency_key == "legacy")
            ).status
            == "completed"
        )
    assert not chat.conversation_db(settings).exists()


def test_failed_initial_request_does_not_block_fresh_question(video, monkeypatch):
    service, _, _ = video
    service.request_repository.mark_failed(
        "initial", error_code="failed", error_message="earlier failure"
    )
    original = chat._workspace_answer
    monkeypatch.setattr(
        chat, "_workspace_answer", lambda question, *args: original("任务状态", *args)
    )
    result = service.chat(
        "exec_00000000000000000000000000000000", "识别产品", user_id="system"
    )
    assert result["status"] == "ok"
    with service.database.session() as session:
        assert session.get(Request, "initial").status == "failed"
        assert (
            session.get(Message, result["assistant_message_id"]).request_id != "initial"
        )


def test_concurrent_empty_history_is_seeded_once(video, monkeypatch):
    _, settings, _ = video
    original = PostgresConversationStore.add_message
    calls = []

    def slow_add(self, *args, **kwargs):
        calls.append(True)
        time.sleep(0.05)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PostgresConversationStore, "add_message", slow_add)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                chat.show_conversation,
                "exec_00000000000000000000000000000000",
                settings,
            )
            for _ in range(2)
        ]
        histories = [future.result(timeout=10)["messages"] for future in futures]
    assert len(calls) == 1
    assert len(histories[0]) == len(histories[1]) == 1
    assert histories[0] == histories[1]
