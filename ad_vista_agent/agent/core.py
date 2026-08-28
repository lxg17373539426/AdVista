from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import (
    AgentRequest,
    AgentRunStatus,
    AgentSessionState,
    AnalysisPlan,
    ReflectionDecision,
    ReflectionRecord,
    StepStatus,
    ToolCallRecord,
    ToolCallStatus,
)
from ad_vista_agent.tools import ToolContext, ToolRegistry

from .planner import rule_plan, validate_plan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_path(state_dir: Path) -> Path:
    return state_dir / "session.json"


def _legacy_session_path(run_dir: Path) -> Path:
    return run_dir / "agent" / "session.json"


def _observation(output: dict[str, Any]) -> dict[str, object]:
    keys = (
        "status",
        "cache_hit",
        "run_id",
        "run_dir",
        "artifact_root",
        "insight_count",
        "audit_status",
        "evidence_count",
        "cluster_count",
        "markdown",
        "html",
        "package",
        "hooks",
        "script",
        "storyboard",
        "ab_plan",
    )
    return {key: output[key] for key in keys if key in output}


def _write(
    store: ArtifactStore,
    state_dir: Path,
    run_dir: Path,
    state: AgentSessionState,
) -> None:
    state.updated_at = _now()
    store.write_json(_session_path(state_dir), state)
    legacy_path = _legacy_session_path(run_dir)
    if legacy_path != _session_path(state_dir):
        store.write_json(legacy_path, state)


def _load(
    store: ArtifactStore, identifier: str
) -> tuple[Path, Path, AgentSessionState]:
    if identifier.startswith("exec_"):
        state_dir = store.execution_dir(identifier)
        path = _session_path(state_dir)
        if not path.is_file():
            raise FileNotFoundError(f"No Agent execution: {identifier}")
        state = AgentSessionState.model_validate(store.read_json(path))
        return state_dir, store.run_dir(state.run_id), state
    run_dir = store.run_dir(identifier)
    path = _legacy_session_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(f"No Agent session for run: {identifier}")
    state = AgentSessionState.model_validate(store.read_json(path))
    if state.execution_id:
        execution_dir = store.execution_dir(state.execution_id)
        execution_path = _session_path(execution_dir)
        if execution_path.is_file():
            return (
                execution_dir,
                run_dir,
                AgentSessionState.model_validate(store.read_json(execution_path)),
            )
    return run_dir / "agent", run_dir, state


def load_agent_session(
    identifier: str, settings: Settings
) -> tuple[Path, Path, AgentSessionState]:
    return _load(ArtifactStore(settings.paths.output_root), identifier)


def _make_state(
    run_id: str,
    asset_id: str | None,
    execution_id: str,
    source_path: Path,
    request: AgentRequest,
    plan: AnalysisPlan,
) -> AgentSessionState:
    now = _now()
    return AgentSessionState(
        execution_id=execution_id,
        asset_id=asset_id,
        session_id=f"conversation_{uuid.uuid4().hex}",
        run_id=run_id,
        source_path=source_path,
        request=request,
        plan=plan,
        status=AgentRunStatus.PLANNED,
        created_at=now,
        updated_at=now,
    )


def _deliver(state: AgentSessionState) -> None:
    observations = state.observations
    report = next((item for item in reversed(observations) if "html" in item), None)
    insights = next(
        (
            item
            for item in reversed(observations)
            if "insight_count" in item and "html" not in item
        ),
        None,
    )
    if report:
        state.deliverables["report"] = report
    if insights:
        state.deliverables["insights"] = insights
    creative = next((item for item in reversed(observations) if "package" in item), None)
    if creative:
        state.deliverables["creative"] = creative


def _reflect(state: AgentSessionState) -> ReflectionRecord:
    reflection_id = f"reflection_{len(state.reflections) + 1:03d}"
    audit_statuses = {
        str(item.get("audit_status"))
        for item in state.observations
        if item.get("audit_status")
    }
    if "review" in audit_statuses and "report_review" not in state.approved_confirmations:
        return ReflectionRecord(
            reflection_id=reflection_id,
            decision=ReflectionDecision.ASK_USER,
            reason="Critic 发现需要人工复核的高风险或单模态主张，Agent 不能自动批准。",
            missing_evidence=["部分高风险洞察缺少多模态交叉支持"],
            created_at=_now(),
        )
    if "fail" in audit_statuses:
        return ReflectionRecord(
            reflection_id=reflection_id,
            decision=ReflectionDecision.FAIL,
            reason="Critic 审计失败，禁止交付。",
            created_at=_now(),
        )
    if state.plan.steps and state.plan.steps[-1].tool == "ledger":
        return ReflectionRecord(
            reflection_id=reflection_id,
            decision=ReflectionDecision.DELIVER,
            reason="用户只请求证据，Evidence Ledger 已完成，无需调用洞察和报告工具。",
            created_at=_now(),
        )
    return ReflectionRecord(
        reflection_id=reflection_id,
        decision=ReflectionDecision.DELIVER,
        reason="计划内必要工具已完成，当前 Observation 满足用户目标。",
        created_at=_now(),
    )


def _execute_state(
    state: AgentSessionState,
    state_dir: Path,
    run_dir: Path,
    settings: Settings,
    registry: ToolRegistry,
    *,
    force_tools: set[str],
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    store = ArtifactStore(settings.paths.output_root)
    state.status = AgentRunStatus.RUNNING
    state.error = None
    _write(store, state_dir, run_dir, state)
    executed = len(
        [call for call in state.tool_calls if call.status == ToolCallStatus.COMPLETED]
    )
    for step in state.plan.steps:
        if cancel_event is not None and cancel_event.is_set():
            state.status = AgentRunStatus.CANCELLED
            state.error = "Agent execution cancelled"
            _write(store, state_dir, run_dir, state)
            return state.model_dump(mode="json")
        if step.status == StepStatus.COMPLETED or any(
            call.tool == step.tool and call.status == ToolCallStatus.COMPLETED
            for call in state.tool_calls
        ):
            step.status = StepStatus.COMPLETED
            continue
        if executed >= state.request.max_tool_calls:
            state.status = AgentRunStatus.FAILED
            state.error = "Agent tool-call budget exhausted"
            _write(store, state_dir, run_dir, state)
            raise RuntimeError(state.error)
        tool = registry.get(step.tool)
        record = ToolCallRecord(
            call_id=f"call_{len(state.tool_calls) + 1:03d}",
            tool=step.tool,
            arguments={"force": step.tool in force_tools},
            status=ToolCallStatus.RUNNING,
            started_at=_now(),
        )
        state.tool_calls.append(record)
        step.status = StepStatus.RUNNING
        _write(store, state_dir, run_dir, state)
        started = time.perf_counter()
        needs_review = False
        try:
            output = tool.run(
                ToolContext(
                    run_id=state.run_id,
                    run_dir=run_dir,
                    source_path=state.source_path,
                    execution_dir=state_dir,
                    request=state.request,
                ),
                record.arguments,
            )
            observation = _observation(output)
            record.status = ToolCallStatus.COMPLETED
            record.observation = observation
            state.observations.append(observation)
            if step.tool == "report":
                audit_status = output.get("audit_status")
                if audit_status == "fail":
                    raise RuntimeError("Critic audit failed")
                if audit_status == "review" and "report_review" not in state.confirmations:
                    state.confirmations.append("report_review")
                    needs_review = True
        except Exception as exc:
            record.status = ToolCallStatus.FAILED
            step.status = StepStatus.FAILED
            record.error = str(exc)[-4000:]
            state.status = AgentRunStatus.FAILED
            state.error = f"Tool {step.tool} failed: {record.error}"
            record.completed_at = _now()
            record.duration_seconds = round(time.perf_counter() - started, 6)
            _write(store, state_dir, run_dir, state)
            raise RuntimeError(state.error) from exc
        record.completed_at = _now()
        record.duration_seconds = round(time.perf_counter() - started, 6)
        step.status = StepStatus.COMPLETED
        executed += 1
        _write(store, state_dir, run_dir, state)
        if cancel_event is not None and cancel_event.is_set():
            state.status = AgentRunStatus.CANCELLED
            state.error = "Agent execution cancelled after the current tool completed"
            _write(store, state_dir, run_dir, state)
            return state.model_dump(mode="json")
        if needs_review:
            _deliver(state)
            reflection = _reflect(state)
            state.reflections.append(reflection)
            state.status = AgentRunStatus.WAITING_CONFIRMATION
            _write(store, state_dir, run_dir, state)
            return state.model_dump(mode="json")
    _deliver(state)
    reflection = _reflect(state)
    state.reflections.append(reflection)
    if reflection.decision == ReflectionDecision.ASK_USER:
        state.status = AgentRunStatus.WAITING_CONFIRMATION
    elif reflection.decision == ReflectionDecision.FAIL:
        state.status = AgentRunStatus.FAILED
        state.error = reflection.reason
    else:
        state.status = AgentRunStatus.COMPLETED
    _write(store, state_dir, run_dir, state)
    return state.model_dump(mode="json")


def run_agent(
    video_path: Path,
    settings: Settings,
    request: AgentRequest,
    *,
    plan: AnalysisPlan | None = None,
    registry: ToolRegistry,
    force_tools: set[str] | None = None,
    execution_id: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if settings.agent.backend == "langgraph":
        from .langgraph_backend import run_langgraph_agent

        return run_langgraph_agent(
            video_path,
            settings,
            request,
            plan=plan,
            force_tools=force_tools,
            registry=registry,
            execution_id=execution_id,
            cancel_event=cancel_event,
        )
    source = video_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    force = set(force_tools or set())
    unknown = force.difference(registry.names())
    if unknown:
        raise ValueError(f"Unknown forced tools: {', '.join(sorted(unknown))}")
    # Ingest is still content-addressed, so it establishes the stable run directory.
    from ad_vista_agent.ingestion import ingest_video

    ingestion = ingest_video(source, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    selected_plan = validate_plan(plan or rule_plan(request), registry, request)
    active_execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
    state_dir = store.prepare_execution(active_execution_id)
    asset = ingestion.get("asset")
    asset_id = str(asset.get("asset_id")) if isinstance(asset, dict) else None
    state = _make_state(
        run_id,
        asset_id,
        active_execution_id,
        source,
        request,
        selected_plan,
    )
    with store.lock(f"agent_{run_id}"):
        return _execute_state(
            state,
            state_dir,
            run_dir,
            settings,
            registry,
            force_tools=force,
            cancel_event=cancel_event,
        )


def resume_agent(
    run_id: str,
    settings: Settings,
    *,
    approve: bool = False,
    registry: ToolRegistry,
) -> dict[str, Any]:
    store = ArtifactStore(settings.paths.output_root)
    state_dir, run_dir, state = _load(store, run_id)
    if settings.agent.backend == "langgraph":
        from .langgraph_backend import run_langgraph_agent

        if approve:
            if state.status != AgentRunStatus.WAITING_CONFIRMATION:
                raise ValueError("Agent session is not waiting for confirmation")
            state.approved_confirmations.extend(
                item for item in state.confirmations if item not in state.approved_confirmations
            )
            state.confirmations.clear()
        if state.status == AgentRunStatus.COMPLETED:
            return state.model_dump(mode="json")
        return run_langgraph_agent(
            state.source_path,
            settings,
            state.request,
            plan=state.plan,
            registry=registry,
            execution_id=state.execution_id,
            existing_state=state,
        )
    if approve:
        if state.status != AgentRunStatus.WAITING_CONFIRMATION:
            raise ValueError("Agent session is not waiting for confirmation")
        state.approved_confirmations.extend(
            item for item in state.confirmations if item not in state.approved_confirmations
        )
        state.confirmations.clear()
        state.status = AgentRunStatus.PLANNED
        state.error = None
        _write(store, state_dir, run_dir, state)
        with store.lock(f"agent_{state.run_id}"):
            return _execute_state(
                state,
                state_dir,
                run_dir,
                settings,
                registry,
                force_tools=set(),
            )
    if state.status == AgentRunStatus.COMPLETED:
        return state.model_dump(mode="json")
    if state.status == AgentRunStatus.WAITING_CONFIRMATION:
        return state.model_dump(mode="json")
    with store.lock(f"agent_{state.run_id}"):
        return _execute_state(
            state,
            state_dir,
            run_dir,
            settings,
            registry,
            force_tools=set(),
            cancel_event=None,
        )


def reject_agent(
    identifier: str,
    settings: Settings,
    *,
    registry: ToolRegistry,
    reason: str = "人工确认被拒绝",
) -> dict[str, Any]:
    del registry
    store = ArtifactStore(settings.paths.output_root)
    state_dir, run_dir, state = _load(store, identifier)
    if state.status != AgentRunStatus.WAITING_CONFIRMATION:
        raise ValueError("Agent session is not waiting for confirmation")
    state.confirmations.clear()
    state.status = AgentRunStatus.FAILED
    state.error = reason
    _write(store, state_dir, run_dir, state)
    return state.model_dump(mode="json")
