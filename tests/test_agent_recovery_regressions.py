from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from ad_vista_agent.agent.core import _make_state, _write, resume_agent
from ad_vista_agent.agent.langgraph_backend import (
    _serialize_graph_state,
    run_langgraph_agent,
)
from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.agent.stage_tools import build_agent_registry
from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import (
    AgentRequest,
    AgentRunStatus,
    StepStatus,
    ToolCallRecord,
    ToolCallStatus,
)


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []
        self.available_tools = []

    def bind_tools(self, tools, **kwargs):
        self.available_tools.append([tool.name for tool in tools])
        def invoke(messages):
            self.inputs.append(messages)
            # An OpenAI-compatible backend rejects unresolved assistant tool calls.
            for index, message in enumerate(messages):
                if isinstance(message, AIMessage) and message.tool_calls:
                    answered = {
                        later.tool_call_id for later in messages[index + 1 :]
                        if isinstance(later, ToolMessage)
                    }
                    assert all(call["id"] in answered for call in message.tool_calls)
            return next(self.responses)
        return RunnableLambda(invoke)


def fixture(tmp_path, *, tools=2, request=None):
    base = load_settings()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"})
    })
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    request = request or AgentRequest(goal="分析视频")
    plan = rule_plan(request)
    if tools is not None:
        plan = plan.model_copy(update={"steps": plan.steps[:tools], "deliverables": [], "response_mode": "answer"})
    execution_id = "exec_" + "a" * 32
    store = ArtifactStore(settings.paths.output_root)
    state_dir = store.prepare_execution(execution_id)
    run_dir = store.prepare("test")
    session = _make_state("test", "asset_test", execution_id, video, request, plan)
    registry = build_agent_registry(settings)
    calls = []
    for name in registry.names():
        def run(context, arguments, name=name):
            calls.append(name)
            output = {"status": "ok", "run_id": "test", "run_dir": str(run_dir)}
            if name == "report":
                output.update(audit_status="review", html="report.html")
            return output
        registry.get(name).run = run
    _write(store, state_dir, run_dir, session)
    return settings, video, request, session, store, state_dir, registry, calls


def complete(session, name):
    session.tool_calls.append(ToolCallRecord(
        call_id=f"call_{len(session.tool_calls) + 1:03d}", tool=name,
        arguments={}, status=ToolCallStatus.COMPLETED,
        started_at="2026-01-01T00:00:00Z", observation={"status": "ok"},
    ))
    next(step for step in session.plan.steps if step.tool == name).status = StepStatus.COMPLETED


def execute(data, model, **kwargs):
    settings, video, request, session, _store, _state_dir, registry, _calls = data
    return run_langgraph_agent(
        video, settings, request, existing_state=session, registry=registry,
        execution_id=session.execution_id, chat_model=model, **kwargs,
    )


def test_resume_answers_pending_tool_call_before_invoking_model(tmp_path):
    data = fixture(tmp_path)
    _settings, _video, _request, session, store, state_dir, _registry, calls = data
    complete(session, "ingest")
    pending = AIMessage(content="", tool_calls=[{"name": "ingest", "args": {}, "id": "ingest-1"}])
    store.write_json(state_dir / "graph_state.json", _serialize_graph_state({
        "messages": [pending], "completed_tools": [],
    }))
    model = Model([AIMessage(content="", tool_calls=[{"name": "timeline", "args": {}, "id": "timeline-1"}])])
    result = execute(data, model)
    assert result["status"] == "completed"
    assert calls == ["timeline"]


def test_resume_finalizes_transient_complete_snapshot(tmp_path):
    data = fixture(tmp_path, tools=1)
    _settings, _video, _request, session, store, state_dir, _registry, calls = data
    complete(session, "ingest")
    store.write_json(state_dir / "graph_state.json", _serialize_graph_state({
        "messages": [], "completed_tools": ["ingest"], "status": "complete",
    }))
    result = execute(data, Model([]))
    assert result["status"] == "completed"
    assert calls == []


def test_review_pauses_before_later_deliverables(tmp_path):
    data = fixture(tmp_path, tools=None, request=AgentRequest(goal="报告和创作", deliverables=["report", "creative"]))
    session = data[3]
    for step in session.plan.steps:
        if step.tool not in {"report", "creative"}:
            complete(session, step.tool)
    model = Model([AIMessage(content="", tool_calls=[
        {"name": "report", "args": {}, "id": "report-1"},
        {"name": "creative", "args": {}, "id": "creative-1"},
    ])])
    result = execute(data, model)
    assert result["status"] == "waiting_confirmation"
    assert data[-1] == ["report"]
    assert session.plan.steps[-1].status == StepStatus.PENDING


def test_report_review_gate_precedes_creative_when_both_requested(tmp_path):
    data = fixture(tmp_path, tools=None, request=AgentRequest(goal="报告和创作", deliverables=["report", "creative"]))
    session = data[3]
    for step in session.plan.steps:
        if step.tool not in {"report", "creative"}:
            complete(session, step.tool)
    model = Model([AIMessage(content="", tool_calls=[
        {"name": "report", "args": {}, "id": "report-1"},
    ])])
    assert execute(data, model)["status"] == "waiting_confirmation"
    assert model.available_tools == [["report"]]


def test_resume_without_approval_does_not_continue_waiting_graph(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    settings, _video, _request, session, store, state_dir, registry, _calls = data
    session.status = AgentRunStatus.WAITING_CONFIRMATION
    session.confirmations = ["report_review"]
    _write(store, state_dir, store.run_dir(session.run_id), session)
    monkeypatch.setattr("ad_vista_agent.agent.langgraph_backend.run_langgraph_agent", lambda *args, **kwargs: pytest.fail("continued without approval"))
    result = resume_agent(session.execution_id, settings, registry=registry)
    assert result["status"] == "waiting_confirmation"


def test_cancelled_graph_does_not_call_model(tmp_path):
    data = fixture(tmp_path)
    event = threading.Event()
    event.set()
    result = execute(data, Model([]), cancel_event=event)
    assert result["status"] == "cancelled"
    assert data[-1] == []


def test_reviewed_batch_resumes_deferred_tool_after_approval(tmp_path):
    data = fixture(tmp_path, tools=None, request=AgentRequest(goal="报告和创作", deliverables=["report", "creative"]))
    session = data[3]
    for step in session.plan.steps:
        if step.tool not in {"report", "creative"}:
            complete(session, step.tool)
    first_model = Model([AIMessage(content="", tool_calls=[
        {"name": "report", "args": {}, "id": "report-1"},
        {"name": "creative", "args": {}, "id": "creative-1"},
    ])])
    assert execute(data, first_model)["status"] == "waiting_confirmation"
    session.approved_confirmations.extend(session.confirmations)
    session.confirmations.clear()
    second_model = Model([AIMessage(content="", tool_calls=[
        {"name": "creative", "args": {}, "id": "creative-2"},
    ])])
    assert execute(data, second_model)["status"] == "completed"
    assert data[-1] == ["report", "creative"]


def test_concurrent_resume_reads_state_after_acquiring_lock(tmp_path, monkeypatch):
    data = fixture(tmp_path, tools=1)
    settings, _video, _request, session, _store, _state_dir, registry, calls = data
    settings = settings.model_copy(update={"agent": settings.agent.model_copy(update={"backend": "legacy"})})
    barrier = threading.Barrier(2)
    serial = threading.Lock()

    @contextmanager
    def lock(self, key):
        # Both callers have reached the lock before either is allowed to execute.
        barrier.wait(timeout=5)
        with serial:
            yield

    monkeypatch.setattr(ArtifactStore, "lock", lock)
    results, errors = [], []
    def resume():
        try:
            results.append(resume_agent(session.execution_id, settings, registry=registry))
        except Exception as exc:  # noqa: BLE001 — 记录外部调用或线程异常，允许降级或交回调用线程。
            errors.append(exc)
    threads = [threading.Thread(target=resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert calls == ["ingest"]


def test_legacy_failed_attempts_consume_tool_budget(tmp_path):
    from ad_vista_agent.agent.core import _execute_state
    data = fixture(tmp_path, tools=1, request=AgentRequest(goal="分析视频", max_tool_calls=1))
    settings, _video, _request, session, store, state_dir, registry, calls = data
    session.tool_calls.append(ToolCallRecord(
        call_id="call_001", tool="ingest", arguments={}, status=ToolCallStatus.FAILED,
        started_at="2026-01-01T00:00:00Z", error="worker failed",
    ))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        _execute_state(session, state_dir, store.run_dir(session.run_id), settings, registry, force_tools=set())
    assert calls == []


def test_legacy_cancellation_during_tool_is_cancelled(tmp_path):
    from ad_vista_agent.agent.core import _execute_state
    data = fixture(tmp_path, tools=1)
    settings, _video, _request, session, store, state_dir, registry, _calls = data
    event = threading.Event()
    def cancel(context, arguments):
        event.set()
        raise RuntimeError("worker cancelled")
    registry.get("ingest").run = cancel
    result = _execute_state(session, state_dir, store.run_dir(session.run_id), settings, registry, force_tools=set(), cancel_event=event)
    assert result["status"] == "cancelled"


def test_concurrent_pipeline_resume_reloads_state_inside_lock(tmp_path, monkeypatch):
    from ad_vista_agent.agent.orchestrator import (
        _new_state,
        _write_state,
        resume_pipeline,
    )
    data = fixture(tmp_path, tools=1)
    settings, video, _request, session, store, _state_dir, _registry, calls = data
    state = _new_state(session.run_id, video, tmp_path / "config.yaml")
    for stage in state.stages[:-1]:
        stage.status = StepStatus.COMPLETED
    _write_state(store, store.run_dir(session.run_id), state)
    barrier, serial = threading.Barrier(2), threading.Lock()

    @contextmanager
    def lock(self, key):
        barrier.wait(timeout=5)
        with serial:
            yield

    monkeypatch.setattr(ArtifactStore, "lock", lock)
    def report(*args):
        calls.append("report")
        return {"status": "ok", "audit_status": "pass", "markdown": "report.md", "html": "report.html"}
    results, errors = [], []
    def resume():
        try:
            results.append(resume_pipeline(session.run_id, settings, stage_runners={"report": report}))
        except Exception as exc:  # noqa: BLE001 — 记录外部调用或线程异常，允许降级或交回调用线程。
            errors.append(exc)
    threads = [threading.Thread(target=resume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert calls == ["report"]
