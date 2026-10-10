from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database, RequestRepository
from ad_vista_agent.web.service import WebService


def test_request_is_saved_before_completion_and_messages_keep_order(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'requests.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        repository = RequestRepository(database)
        user = repository.ensure_system_user()
        repository.ensure_conversation(
            "conversation_1",
            user_id=user.user_id,
            run_id=None,
            conversation_type="general_chat",
        )
        request, created = repository.create_request(
            request_id="request_1",
            user_id=user.user_id,
            conversation_id="conversation_1",
            request_type="general_chat",
            input_json={"message": "你好"},
            idempotency_key="idem_1",
        )
        assert created
        repository.add_user_message(
            message_id="message_1",
            conversation_id="conversation_1",
            user_id=user.user_id,
            content="你好",
            request_id=request.request_id,
        )
        duplicate, duplicate_created = repository.create_request(
            request_id="request_other",
            user_id=user.user_id,
            conversation_id="conversation_1",
            request_type="general_chat",
            input_json={"message": "你好"},
            idempotency_key="idem_1",
        )
        assert not duplicate_created
        assert duplicate.request_id == request.request_id
        assert repository.get_request(request.request_id).status == "pending"

        repository.add_assistant_message(
            message_id="message_2",
            conversation_id="conversation_1",
            user_id=user.user_id,
            content="你好，我是 AdVista。",
            request_id=request.request_id,
            citations=[],
        )
        repository.mark_completed(request.request_id)
        messages = repository.messages("conversation_1", user_id=user.user_id)
        assert [item.sequence_no for item in messages] == [1, 2]
        assert [item.role for item in messages] == ["user", "assistant"]
        assert repository.get_request(request.request_id).status == "completed"
    finally:
        database.close()


def test_failed_request_keeps_diagnostic_error(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'failed.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        repository = RequestRepository(database)
        repository.ensure_system_user()
        repository.ensure_conversation(
            "conversation_failed",
            user_id="system",
            run_id=None,
            conversation_type="general_chat",
        )
        request, _ = repository.create_request(
            request_id="request_failed",
            user_id="system",
            conversation_id="conversation_failed",
            request_type="general_chat",
            input_json={"message": "test"},
            idempotency_key="failed_1",
        )
        repository.mark_failed(
            request.request_id,
            error_code="model_timeout",
            error_message="backend timeout",
        )
        failed = repository.get_request(request.request_id)
        assert failed.status == "failed"
        assert failed.error_code == "model_timeout"
        assert failed.error_message == "backend timeout"
        assert failed.completed_at is not None
    finally:
        database.close()


def test_general_chat_persists_request_when_model_fails(tmp_path: Path, monkeypatch) -> None:
    base = load_settings()
    settings = base.model_copy(
        update={
            "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
            "database": DatabaseConfig(
                url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'service.db'}")
            ),
        }
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.close()
    service = WebService(settings)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("model timeout")

    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", fail)
    try:
        try:
            service.general_chat("测试请求", user_id="system", idempotency_key="chat-failure")
        except RuntimeError as exc:
            assert "不可用" in str(exc)
        else:
            raise AssertionError("model failure was not propagated")
        with Database(settings.database).session() as session:
            from sqlalchemy import select

            from ad_vista_agent.database.models import Request

            request = session.scalar(select(Request).where(Request.idempotency_key == "chat-failure"))
            assert request is not None
            assert request.status == "failed"
            assert request.error_code == "model_request_failed"
    finally:
        service.close()


def test_video_follow_up_creates_user_request_before_answer(tmp_path: Path, monkeypatch) -> None:
    base = load_settings()
    settings = base.model_copy(
        update={
            "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
            "database": DatabaseConfig(
                url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'video-chat.db'}")
            ),
        }
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.close()
    service = WebService(settings)
    state = type(
        "State",
        (),
        {
            "session_id": "conversation_video",
            "execution_id": None,
            "request": type("Request", (), {"goal": "识别产品"})(),
        },
    )()
    monkeypatch.setattr(
        "ad_vista_agent.web.service.load_agent_session",
        lambda run_id, current_settings: (tmp_path, tmp_path, state),
    )
    repository = service.request_repository
    assert repository is not None

    def answer(run_id: str, question: str, current_settings: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["database_request_id"] is not None
        assert kwargs["database_user_id"] == "system"
        messages = repository.messages("conversation_video", user_id="system")
        assert [(item.role, item.content, item.status) for item in messages] == [
            ("user", "目标人群是谁？", "pending")
        ]
        return {"status": "ok", "answer": "高尔夫爱好者。"}

    monkeypatch.setattr("ad_vista_agent.web.service.ask_agent", answer)
    try:
        result = service._video_chat(
            "exec_video",
            "目标人群是谁？",
            user_id="system",
            idempotency_key="video-follow-up",
        )
        assert result["answer"] == "高尔夫爱好者。"
        user_message = repository.messages("conversation_video", user_id="system")[0]
        assert user_message.request_id is not None
        request = repository.get_request(user_message.request_id)
        assert request.idempotency_key == "video-follow-up"
        assert request.request_type == "video_chat"
    finally:
        service.close()
