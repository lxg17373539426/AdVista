from __future__ import annotations

import io
import json
import subprocess
import sys
from email.message import Message as Headers
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database, UserRepository
from ad_vista_agent.database.models import Execution, Request
from ad_vista_agent.web.app import _Handler, _MultipartReader, _read_json
from ad_vista_agent.web.service import WebService


@pytest.mark.parametrize("operation", ["readline()", "consume(2)", "body_until(b'x', lambda data: None, 100)"])
def test_truncated_multipart_terminates(operation: str) -> None:
    code = (
        "import io\nfrom ad_vista_agent.web.app import _MultipartReader\n"
        "reader = _MultipartReader(io.BytesIO(b'a'), 1)\n"
        f"try:\n    reader.{operation}\n"
        "except ValueError:\n    pass\nelse:\n    raise AssertionError('accepted truncated upload')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=2, check=False)
    assert result.returncode == 0, result.stderr.decode()


def test_valid_multipart_reader_keeps_payload() -> None:
    payload = b"--x\r\nhello\r\n--x--\r\n"
    reader = _MultipartReader(io.BytesIO(payload), len(payload))
    assert reader.readline() == b"--x\r\n"
    chunks: list[bytes] = []
    assert reader.body_until(b"x", chunks.append, 100) == (5, True)
    assert b"".join(chunks) == b"hello"


@pytest.mark.parametrize("length", ["-1", "0"])
def test_json_rejects_invalid_length_before_reading(length: str) -> None:
    class Stream:
        def read(self, size: int) -> bytes:
            raise AssertionError("invalid request body was read")

    handler = SimpleNamespace(headers={"Content-Length": length}, rfile=Stream())
    with pytest.raises(ValueError, match="length"):
        _read_json(handler)


@pytest.fixture
def service(tmp_path: Path):
    base = load_settings()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
        "database": DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'service.db'}")),
    })
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.close()
    current = WebService(settings)
    try:
        yield current
    finally:
        current.close()


def test_general_history_uses_authenticated_owner(service: WebService) -> None:
    user, _ = UserRepository(service.database).create_api_user("history-owner")
    store = service._conversation_store(user_id=user.user_id)
    store.create_session(session_id="web_owned", run_id="general_web_owned", source_path=Path("."), goal="普通对话")
    store.add_message("web_owned", "user", "我的历史", [])
    assert service.general_messages("web_owned", user_id=user.user_id)["messages"][0]["content"] == "我的历史"
    with pytest.raises(PermissionError):
        service.general_messages("web_owned", user_id="system")


def _model_response(answer: str) -> io.BytesIO:
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": answer}}]}).encode())


def test_general_chat_replays_the_requested_answer(service: WebService, monkeypatch) -> None:
    responses = iter(["第一个回答", "第二个回答"])
    calls = []

    def respond(*args, **kwargs):
        calls.append(args)
        return _model_response(next(responses))

    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", respond)
    first = service.general_chat("问题一", "web_replay", user_id="system", idempotency_key="first")
    service.general_chat("问题二", "web_replay", user_id="system", idempotency_key="second")
    replay = service.general_chat("问题一", "web_replay", user_id="system", idempotency_key="first")
    assert replay["answer"] == first["answer"]
    assert len(calls) == 2


def test_general_chat_replays_original_session_when_session_was_omitted(service: WebService, monkeypatch) -> None:
    calls = []

    def respond(*args, **kwargs):
        calls.append(args)
        return _model_response("回答")

    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", respond)
    first = service.general_chat("问题", user_id="system", idempotency_key="same")
    replay = service.general_chat("问题", user_id="system", idempotency_key="same")
    assert replay["session_id"] == first["session_id"]
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [b"not json", b'{"choices": []}', b'{"choices":[{"message":{"content":null}}]}'])
def test_malformed_model_reply_marks_request_failed(service: WebService, monkeypatch, payload: bytes) -> None:
    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", lambda *a, **k: io.BytesIO(payload))
    with pytest.raises(RuntimeError):
        service.general_chat("问题", "web_invalid", user_id="system", idempotency_key="invalid")
    with service.database.session() as session:
        request = session.scalar(select(Request).where(Request.idempotency_key == "invalid"))
        assert request.status == "failed"


def test_post_permission_error_returns_403() -> None:
    handler = object.__new__(_Handler)
    handler._route = lambda: (["api", "runs", "exec_" + "a" * 32, "messages"], {})
    handler._require_api_access = lambda parts: True
    handler.identity = {"user_id": "user_other", "role": "user"}

    def deny(*a, **k):
        raise PermissionError("Run does not belong to the authenticated user")

    handler.service = SimpleNamespace(authorize_run=deny)
    sent = []
    handler._error = lambda status, message: sent.append(status)
    handler.do_POST()
    assert sent == [403]


def test_duplicate_video_parts_leave_no_temporary_files(tmp_path: Path) -> None:
    base = load_settings()
    settings = base.model_copy(update={"paths": base.paths.model_copy(update={"output_root": tmp_path})})
    handler = object.__new__(_Handler)
    handler.settings = settings
    handler.headers = Headers()
    handler.headers["Content-Type"] = "multipart/form-data; boundary=x"
    body = b"".join([
        b'--x\r\nContent-Disposition: form-data; name="video"; filename="a.mp4"\r\n\r\nfirst\r\n',
        b'--x\r\nContent-Disposition: form-data; name="video"; filename="b.mp4"\r\n\r\nsecond\r\n--x--\r\n',
    ])
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler.service = SimpleNamespace(validate_uploaded_video=lambda path: None, submit=lambda *a, **k: {})
    handler._send_json = lambda *a, **k: None
    with pytest.raises(ValueError, match="video"):
        handler._upload()
    assert not list((tmp_path / "uploads").iterdir())


def test_general_chat_sends_current_question_once(service: WebService, monkeypatch) -> None:
    def respond(request, **kwargs):
        payload = json.loads(request.data)
        assert [m["content"] for m in payload["messages"] if m["role"] == "user"] == ["问题"]
        return _model_response("回答")

    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", respond)
    service.general_chat("问题", "web_once", user_id="system", idempotency_key="once")


def test_interrupted_model_stream_is_not_saved_as_success(service: WebService, monkeypatch) -> None:
    stream = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", lambda *a, **k: io.BytesIO(stream))
    with pytest.raises(RuntimeError):
        service.general_chat("问题", "web_stream", user_id="system", idempotency_key="stream", on_delta=lambda value: None)
    with service.database.session() as session:
        request = session.scalar(select(Request).where(Request.idempotency_key == "stream"))
        assert request.status == "failed"


def test_complete_model_stream_accepts_usage_chunk(service: WebService, monkeypatch) -> None:
    stream = (
        b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n'
        b'data: {"choices":[],"usage":{"completion_tokens":1}}\n\n'
        b'data: [DONE]\n\n'
    )
    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", lambda *a, **k: io.BytesIO(stream))
    chunks = []
    result = service.general_chat("问题", "web_stream", user_id="system", idempotency_key="stream", on_delta=chunks.append)
    assert result["answer"] == "answer"
    assert chunks == ["answer"]


def test_job_capacity_rejection_does_not_leave_pending_execution(service: WebService, monkeypatch) -> None:
    service.settings.web.max_active_jobs = 1
    service.jobs["job_existing"] = {"status": "queued"}
    upload = service.settings.paths.output_root / "uploads" / "sample.mp4"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"video")
    monkeypatch.setattr(service, "validate_uploaded_video", lambda path: None)
    with pytest.raises(RuntimeError, match="active video jobs"):
        service.submit(upload, "目标", ["report"], user_id="system", idempotency_key="capacity")
    with service.database.session() as session:
        request = session.scalar(select(Request).where(Request.idempotency_key == "capacity"))
        assert request.status == "failed"
        assert list(session.scalars(select(Execution))) == []
