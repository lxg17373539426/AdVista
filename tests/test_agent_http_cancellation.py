import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ad_vista_agent.agent import chat, planner
from ad_vista_agent.agent.core import _make_state, _write, resume_agent
from ad_vista_agent.agent.stage_tools import build_agent_registry
from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.runtime.process import ProcessCancelled
from ad_vista_agent.schemas import (
    AgentRequest,
    AgentRunStatus,
    ConversationAnswer,
    StepStatus,
)
from ad_vista_agent.tools.base import (
    get_active_cancel_event,
    reset_active_cancel_event,
    set_active_cancel_event,
)


class Response:
    def __init__(self, content):
        self.value = {"choices": [{"message": {"content": content}}]}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.value).encode()


@pytest.mark.parametrize("operation", ["planner", "chat"])
def test_pre_cancelled_request_never_calls_http(monkeypatch, operation):
    event = threading.Event()
    event.set()
    calls = []
    def open_http(*args, **kwargs):
        calls.append("http")
        if operation == "planner":
            return Response(json.dumps({"goal": "分析视频", "selected_tools": ["ingest"], "deliverables": ["report"], "response_mode": "answer"}))
        return Response(json.dumps({"answer": "测试回答", "epistemic_status": "conversational", "evidence_refs": []}))
    monkeypatch.setattr("urllib.request.urlopen", open_http)
    settings = load_settings()
    token = set_active_cancel_event(event)
    try:
        with pytest.raises(ProcessCancelled):
            if operation == "planner":
                planner.qwen_plan(AgentRequest(goal="分析视频"), build_agent_registry(settings), settings)
            else:
                chat._qwen_answer("问题", {}, [], settings)
    finally:
        reset_active_cancel_event(token)
    assert calls == []


def test_cancelled_visual_request_does_not_attempt_fallback_batches(monkeypatch):
    calls = []
    def answer(*args, **kwargs):
        calls.append("model")
        raise ProcessCancelled("用户已取消")
    monkeypatch.setattr(chat, "_qwen_answer", answer)
    settings = load_settings()
    settings = settings.model_copy(update={"insight": settings.insight.model_copy(update={"max_images_per_prompt": 2})})
    context = {"visual_keyframes": [{"id": f"kf_{index}"} for index in range(8)]}
    with pytest.raises(ProcessCancelled):
        chat._answer_with_video_fallback("视频内容", context, [], settings, settings.paths.output_root)
    assert calls == ["model"]


def test_cancelled_visual_observation_does_not_split_and_retry(tmp_path, monkeypatch):
    from ad_vista_agent.insights import builder

    calls = []
    def cancel(*args, **kwargs):
        calls.append("model")
        raise ProcessCancelled("用户已取消")
    monkeypatch.setattr(builder, "_visual_content", lambda *args: [])
    keyframes = [SimpleNamespace(keyframe_id=f"kf_{index}") for index in range(2)]
    with pytest.raises(ProcessCancelled):
        builder._visual_observations(tmp_path, keyframes, load_settings(), SimpleNamespace(run=cancel), "test")
    assert calls == ["model"]


def session_fixture(tmp_path):
    base = load_settings()
    settings = base.model_copy(update={"paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"})})
    store = ArtifactStore(settings.paths.output_root)
    execution_id = "exec_" + "b" * 32
    state_dir = store.prepare_execution(execution_id)
    run_dir = store.prepare("test")
    request = AgentRequest(goal="视频里有什么？")
    state = _make_state("test", "asset_test", execution_id, tmp_path / "video.mp4", request, planner.rule_plan(request))
    state.status = AgentRunStatus.COMPLETED
    _write(store, state_dir, run_dir, state)
    return settings, store, run_dir, state


def test_cancel_after_model_response_prevents_answer_persistence(tmp_path, monkeypatch):
    settings, _store, run_dir, state = session_fixture(tmp_path)
    event = threading.Event()
    written = []
    conversations = SimpleNamespace(
        add_message=lambda *args: written.append("message") or "message_test",
        add_version=lambda *args: written.append("version") or "version_test",
    )
    monkeypatch.setattr(chat, "_context", lambda *args: ({}, set()))
    monkeypatch.setattr(chat, "_seed_conversation", lambda *args: [])
    def answer(*args, **kwargs):
        event.set()
        return ConversationAnswer(answer="测试回答", epistemic_status="conversational"), []
    monkeypatch.setattr(chat, "_answer_with_video_fallback", answer)
    token = set_active_cancel_event(event)
    try:
        with pytest.raises(ProcessCancelled):
            chat._ask_agent_with_store("test", "视频里有什么？", settings, conversations, run_dir, state, on_delta=None, database_request_id=None)
    finally:
        reset_active_cancel_event(token)
    assert written == []


def test_final_answer_cancellation_marks_persisted_agent_session_cancelled(tmp_path, monkeypatch):
    settings, store, run_dir, state = session_fixture(tmp_path)
    monkeypatch.setattr(chat, "_resolve_session", lambda *args: (run_dir, state))
    @contextmanager
    def conversations(*args):
        yield object()
    monkeypatch.setattr(chat, "_video_conversations", conversations)
    def cancel(*args, **kwargs):
        raise ProcessCancelled("用户已取消")
    monkeypatch.setattr(chat, "_ask_agent_with_store", cancel)
    with pytest.raises(ProcessCancelled):
        chat.ask_agent(state.execution_id, "视频里有什么？", settings)
    persisted = store.read_json(store.execution_dir(state.execution_id) / "session.json")
    assert persisted["status"] == "cancelled"


@pytest.mark.parametrize("operation", ["planner", "chat"])
@pytest.mark.parametrize("cancel_attempt", [1, 2])
def test_model_cancellation_propagates_on_initial_and_repair_http(monkeypatch, operation, cancel_attempt):
    event = threading.Event()
    attempts = []
    @contextmanager
    def open_http(call, *, timeout, cancel_event):
        assert cancel_event is event
        attempts.append(call)
        if len(attempts) == cancel_attempt:
            event.set()
            raise ProcessCancelled("用户已取消")
        text = "{" if operation == "planner" else json.dumps({"answer": "以下：", "epistemic_status": "conversational"})
        yield Response(text)
    module = planner if operation == "planner" else chat
    monkeypatch.setattr(module, "cancellable_urlopen", open_http)
    settings = load_settings()
    token = set_active_cancel_event(event)
    try:
        with pytest.raises(ProcessCancelled):
            if operation == "planner":
                planner.qwen_plan(AgentRequest(goal="分析视频"), build_agent_registry(settings), settings)
            else:
                chat._qwen_answer("问题", {}, [], settings)
    finally:
        reset_active_cancel_event(token)
    assert len(attempts) == cancel_attempt


def test_streaming_answer_stops_emitting_after_cancellation(monkeypatch):
    event, chunks = threading.Event(), []
    class Stream:
        def __iter__(self):
            for content in ('{"answer":"第一段', '第二段","epistemic_status":"conversational"}'):
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]}) + "\n").encode()
    @contextmanager
    def open_http(call, *, timeout, cancel_event):
        assert cancel_event is event
        yield Stream()
    def receive(value):
        chunks.append(value)
        event.set()
    monkeypatch.setattr(chat, "cancellable_urlopen", open_http)
    token = set_active_cancel_event(event)
    try:
        with pytest.raises(ProcessCancelled):
            chat._qwen_answer("问题", {}, [], load_settings(), on_delta=receive)
    finally:
        reset_active_cancel_event(token)
    assert chunks == ["第一段"]


def test_react_header_wait_is_cancelled_and_owned_client_closed(tmp_path, monkeypatch):
    import httpx

    from ad_vista_agent.agent.langgraph_backend import run_langgraph_agent

    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            entered.set()
            release.wait(5)
            try:
                self.send_response(200)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *args):
            pass
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("沙箱禁止本地 socket；需在授权的测试环境验证 HTTP 取消。")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    def stop():
        if entered.wait(3):
            cancel.set()
    cancel_thread = threading.Thread(target=stop)
    cancel_thread.start()
    settings, store, _run_dir, state = session_fixture(tmp_path)
    state.source_path.write_bytes(b"video")
    state.plan = state.plan.model_copy(update={"steps": state.plan.steps[:1], "deliverables": [], "response_mode": "answer"})
    settings = settings.model_copy(update={"insight": settings.insight.model_copy(update={"endpoint": f"http://127.0.0.1:{server.server_port}/v1", "timeout_seconds": 30})})
    closed = []
    original_close = httpx.Client.close
    def close(client):
        closed.append(client)
        original_close(client)
    monkeypatch.setattr(httpx.Client, "close", close)
    started = time.monotonic()
    try:
        result = run_langgraph_agent(state.source_path, settings, state.request,
            registry=build_agent_registry(settings), existing_state=state,
            execution_id=state.execution_id, cancel_event=cancel)
        assert result["status"] == "cancelled"
        assert entered.is_set()
        assert time.monotonic() - started < 2
        assert closed and all(client.is_closed for client in closed)
        assert store.read_json(store.execution_dir(state.execution_id) / "session.json")["status"] == "cancelled"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        cancel_thread.join(timeout=3)


def test_react_cancellable_transport_preserves_tool_call_protocol(tmp_path, monkeypatch):
    from ad_vista_agent.agent import http_transport
    from ad_vista_agent.agent.langgraph_backend import run_langgraph_agent

    settings, _store, run_dir, state = session_fixture(tmp_path)
    state.source_path.write_bytes(b"video")
    state.plan = state.plan.model_copy(update={"steps": state.plan.steps[:1], "deliverables": [], "response_mode": "answer"})
    event, requests = threading.Event(), []
    value = {
        "id": "chatcmpl_test", "object": "chat.completion", "created": 1,
        "model": "test", "choices": [{"index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {"name": "ingest", "arguments": "{}"},
            }]}}],
    }
    @contextmanager
    def open_http(call, *, timeout, cancel_event):
        assert cancel_event is event
        requests.append(json.loads(call.data))
        yield SimpleNamespace(status=200, headers={"Content-Type": "application/json"}, read=lambda: json.dumps(value).encode())
    monkeypatch.setattr(http_transport, "cancellable_urlopen", open_http)
    registry = build_agent_registry(settings)
    registry.get("ingest").run = lambda *args: {"status": "ok", "run_id": "test", "run_dir": str(run_dir)}
    result = run_langgraph_agent(state.source_path, settings, state.request,
        registry=registry, existing_state=state, execution_id=state.execution_id, cancel_event=event)
    assert result["status"] == "completed"
    assert [item["tool"] for item in result["tool_calls"]] == ["ingest"]
    assert len(requests) == 1
    assert requests[0]["tools"][0]["function"]["name"] == "ingest"


@pytest.mark.parametrize("approve", [True, False])
def test_resume_forwards_active_cancellation_to_graph_backend(tmp_path, monkeypatch, approve):
    from ad_vista_agent.agent import langgraph_backend

    settings, store, run_dir, state = session_fixture(tmp_path)
    state.status = AgentRunStatus.WAITING_CONFIRMATION if approve else AgentRunStatus.FAILED
    state.confirmations = ["report_review"] if approve else []
    _write(store, store.execution_dir(state.execution_id), run_dir, state)
    event = threading.Event()
    def resume(*args, **kwargs):
        # Backend may receive cancellation explicitly or inherit the active scope.
        assert (kwargs.get("cancel_event") or get_active_cancel_event()) is event
        return {"status": "cancelled"}
    monkeypatch.setattr(langgraph_backend, "run_langgraph_agent", resume)
    token = set_active_cancel_event(event)
    try:
        assert resume_agent(state.execution_id, settings, approve=approve, registry=build_agent_registry(settings))["status"] == "cancelled"
    finally:
        reset_active_cancel_event(token)


@pytest.mark.parametrize("approve", [True, False])
def test_legacy_resume_cancellation_during_remaining_tool_is_cancelled(tmp_path, approve):
    settings, store, run_dir, state = session_fixture(tmp_path)
    settings = settings.model_copy(update={"agent": settings.agent.model_copy(update={"backend": "legacy"})})
    state.status = AgentRunStatus.WAITING_CONFIRMATION if approve else AgentRunStatus.FAILED
    state.confirmations = ["report_review"] if approve else []
    for step in state.plan.steps:
        step.status = StepStatus.COMPLETED if step.tool != "report" else StepStatus.PENDING
    _write(store, store.execution_dir(state.execution_id), run_dir, state)
    event = threading.Event()
    registry = build_agent_registry(settings)
    def cancel(context, arguments):
        assert context.cancel_event is event
        event.set()
        raise ProcessCancelled("用户已取消")
    registry.get("report").run = cancel
    token = set_active_cancel_event(event)
    try:
        result = resume_agent(state.execution_id, settings, approve=approve, registry=registry)
        assert result["status"] == "cancelled"
    finally:
        reset_active_cancel_event(token)


@pytest.mark.parametrize("backend", ["legacy", "langgraph"])
@pytest.mark.parametrize("status", [AgentRunStatus.COMPLETED, AgentRunStatus.WAITING_CONFIRMATION])
def test_resume_without_approval_preserves_completed_and_waiting_sessions(tmp_path, monkeypatch, backend, status):
    from ad_vista_agent.agent import langgraph_backend

    settings, store, run_dir, state = session_fixture(tmp_path)
    settings = settings.model_copy(update={"agent": settings.agent.model_copy(update={"backend": backend})})
    state.status = status
    state.confirmations = ["report_review"] if status == AgentRunStatus.WAITING_CONFIRMATION else []
    _write(store, store.execution_dir(state.execution_id), run_dir, state)
    monkeypatch.setattr(langgraph_backend, "run_langgraph_agent", lambda *args, **kwargs: pytest.fail("unexpected execution"))
    event = threading.Event()
    event.set()
    token = set_active_cancel_event(event)
    try:
        assert resume_agent(state.execution_id, settings, registry=build_agent_registry(settings))["status"] == status.value
    finally:
        reset_active_cancel_event(token)
