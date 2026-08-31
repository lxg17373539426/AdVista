from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Any, TypedDict, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import SecretStr

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import (
    AgentRequest,
    AgentRunStatus,
    AgentSessionState,
    AnalysisPlan,
    StepStatus,
    ToolCallRecord,
    ToolCallStatus,
)
from ad_vista_agent.tools import ToolContext, ToolRegistry

from .planner import CANONICAL_TOOL_ORDER, TOOL_DEPENDENCIES, rule_plan, validate_plan


class ReActState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    completed_tools: list[str]
    observations: list[dict[str, object]]
    tool_calls: list[dict[str, object]]
    status: str
    error: str | None
    final_answer: str


def _graph_state_path(state_dir: Path) -> Path:
    return state_dir / "graph_state.json"


def _serialize_graph_state(state: ReActState) -> dict[str, Any]:
    return {
        "messages": messages_to_dict(state.get("messages", [])),
        "completed_tools": list(state.get("completed_tools", [])),
        "observations": list(state.get("observations", [])),
        "tool_calls": list(state.get("tool_calls", [])),
        "status": state.get("status"),
        "error": state.get("error"),
        "final_answer": state.get("final_answer"),
    }


def _restore_graph_state(store: ArtifactStore, state_dir: Path) -> ReActState | None:
    path = _graph_state_path(state_dir)
    if not path.is_file():
        return None
    value = store.read_json(path)
    raw_messages = value.get("messages", [])
    if not isinstance(raw_messages, list):
        raise ValueError("Persisted LangGraph messages must be a list")
    return cast(ReActState, {
        "messages": messages_from_dict(raw_messages),
        "completed_tools": [str(item) for item in value.get("completed_tools", [])],
        "observations": list(value.get("observations", [])),
        "tool_calls": list(value.get("tool_calls", [])),
        "status": value.get("status"),
        "error": value.get("error"),
        "final_answer": value.get("final_answer"),
    })


def _next_graph_state(state: ReActState, update: dict[str, Any]) -> ReActState:
    next_state = dict(state)
    if "messages" in update:
        next_state["messages"] = [
            *state.get("messages", []),
            *cast(list[AnyMessage], update["messages"]),
        ]
    for key, value in update.items():
        if key != "messages":
            next_state[key] = value
    return cast(ReActState, next_state)


def _observation(output: dict[str, Any]) -> dict[str, object]:
    keys = (
        "status", "cache_hit", "run_id", "run_dir", "artifact_root", "insight_count",
        "audit_status", "evidence_count", "cluster_count", "markdown", "html",
        "package", "hooks", "script", "storyboard", "ab_plan",
    )
    return {key: output[key] for key in keys if key in output}


def _model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.insight.served_model,
        base_url=settings.insight.endpoint,
        api_key=SecretStr("local"),
        temperature=settings.insight.temperature,
        timeout=settings.insight.timeout_seconds,
        max_retries=0,
    )


def run_langgraph_agent(
    video_path: Path,
    settings: Settings,
    request: AgentRequest,
    *,
    registry: ToolRegistry,
    plan: AnalysisPlan | None = None,
    force_tools: set[str] | None = None,
    execution_id: str | None = None,
    cancel_event: threading.Event | None = None,
    chat_model: Any | None = None,
    existing_state: AgentSessionState | None = None,
) -> dict[str, Any]:
    source = video_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    store = ArtifactStore(settings.paths.output_root)
    plan = validate_plan(plan or rule_plan(request), registry, request)
    session = existing_state
    if session is None:
        ingestion = ingest_video(source, settings)
        run_id = str(ingestion["run_id"])
        run_dir = ArtifactStore(settings.paths.output_root).run_dir(run_id)
        execution = execution_id or f"exec_{uuid.uuid4().hex}"
        state_dir = store.prepare_execution(execution)
        asset = ingestion.get("asset")
        asset_id = str(asset.get("asset_id")) if isinstance(asset, dict) else None
        session = AgentSessionState(
            execution_id=execution,
            asset_id=asset_id,
            session_id=f"conversation_{uuid.uuid4().hex}",
            run_id=run_id,
            source_path=source,
            request=request,
            plan=plan,
            status=AgentRunStatus.RUNNING,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    else:
        if execution_id and session.execution_id != execution_id:
            raise ValueError("Existing Agent execution ID does not match the requested execution")
        run_id = session.run_id
        run_dir = store.run_dir(run_id)
        state_dir = store.execution_dir(session.execution_id or execution_id or "")
        source = session.source_path
        plan = session.plan
        session.status = AgentRunStatus.RUNNING
        session.error = None

    if session.execution_id is None:
        raise ValueError("Agent execution is missing an execution ID")

    required = [step.tool for step in plan.steps]
    forced = set(force_tools or set())
    unknown_forced = forced.difference(required)
    if unknown_forced:
        raise ValueError(f"Unknown or unplanned forced tools: {', '.join(sorted(unknown_forced))}")
    context = ToolContext(
        run_id=run_id,
        run_dir=run_dir,
        source_path=source,
        execution_dir=state_dir,
        request=request,
        cancel_event=cancel_event,
    )

    def persist() -> None:
        from .core import _write
        _write(store, state_dir, run_dir, session)

    def persist_graph(state: ReActState) -> None:
        store.write_json(_graph_state_path(state_dir), _serialize_graph_state(state))

    persist()

    def ready_tools(completed: set[str]) -> list[str]:
        return [
            name for name in CANONICAL_TOOL_ORDER
            if name in required
            and name not in completed
            and TOOL_DEPENDENCIES.get(name, set()).issubset(completed)
        ]

    def make_tool(name: str) -> StructuredTool:
        def invoke() -> str:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Agent execution cancelled")
            if len(session.tool_calls) >= request.max_tool_calls:
                session.status = AgentRunStatus.FAILED
                session.error = "Agent tool-call budget exhausted"
                persist()
                raise RuntimeError(session.error)
            record = ToolCallRecord(
                call_id=f"call_{len(session.tool_calls) + 1:03d}",
                tool=name,
                arguments={},
                status=ToolCallStatus.RUNNING,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            session.tool_calls.append(record)
            persist()
            started = time.perf_counter()
            try:
                arguments: dict[str, object] = {"force": name in forced}
                record.arguments = arguments
                output = registry.get(name).run(context, arguments)
                observation = _observation(output)
                record.status = ToolCallStatus.COMPLETED
                record.observation = observation
                record.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                record.duration_seconds = round(time.perf_counter() - started, 6)
                session.observations.append(observation)
                session.plan.steps[
                    next(i for i, step in enumerate(session.plan.steps) if step.tool == name)
                ].status = StepStatus.COMPLETED
                audit_status = output.get("audit_status")
                if audit_status == "fail":
                    raise RuntimeError("Critic audit failed")
                if audit_status == "review" and "report_review" not in session.confirmations:
                    session.confirmations.append("report_review")
                persist()
                return json.dumps(observation, ensure_ascii=False)
            except Exception as exc:
                record.status = ToolCallStatus.FAILED
                record.error = str(exc)[-4000:]
                record.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                record.duration_seconds = round(time.perf_counter() - started, 6)
                session.status = AgentRunStatus.FAILED
                session.error = f"Tool {name} failed: {record.error}"
                persist()
                raise

        return StructuredTool.from_function(
            invoke,
            name=name,
            description=f"执行广告分析阶段 {name}。只能在满足前置依赖时调用。",
        )

    llm = chat_model or _model(settings)
    graph = StateGraph(ReActState)

    def agent_node(state: ReActState) -> dict[str, Any]:
        completed = set(state.get("completed_tools", []))
        available = ready_tools(completed)
        if not available:
            result = {"status": "complete"}
            persist_graph(_next_graph_state(state, result))
            return result
        system = SystemMessage(content=(
            "你是 AdVista 的 LangGraph ReAct Agent。根据用户目标选择下一步工具。"
            "只能调用当前提供的工具，不能跳过依赖，不能重复调用。"
            f"用户目标：{request.goal}\n已完成：{sorted(completed)}\n"
            f"剩余工具：{available}\n交付物：{request.deliverables or plan.deliverables}"
        ))
        response = llm.bind_tools(
            [make_tool(name) for name in available],
            tool_choice=settings.agent.tool_choice,
        ).invoke(
            [system, *state.get("messages", [])]
        )
        result = {"messages": [response]}
        persist_graph(_next_graph_state(state, result))
        return result

    def tool_node(state: ReActState) -> dict[str, Any]:
        message = state.get("messages", [])[-1]
        if not isinstance(message, AIMessage):
            return {"status": "failed", "error": "ReAct did not return an AI message"}
        completed = set(state.get("completed_tools", []))
        messages: list[AnyMessage] = []
        for call in message.tool_calls:
            name = str(call["name"])
            previous = next(
                (
                    record
                    for record in reversed(session.tool_calls)
                    if record.tool == name and record.status == ToolCallStatus.COMPLETED
                ),
                None,
            )
            if previous is not None and name not in forced:
                completed.add(name)
                messages.append(
                    ToolMessage(
                        content=json.dumps(previous.observation, ensure_ascii=False),
                        tool_call_id=str(call["id"]),
                    )
                )
                continue
            available = ready_tools(completed)
            if name not in available:
                raise ValueError(f"ReAct selected unavailable or out-of-order tool: {name}")
            result = make_tool(name).invoke(call.get("args") or {})
            completed.add(name)
            messages.append(ToolMessage(content=str(result), tool_call_id=str(call["id"])))
        result = {"messages": messages, "completed_tools": sorted(completed)}
        persist_graph(_next_graph_state(state, result))
        return result

    def route(state: ReActState) -> str:
        if state.get("status") == "complete":
            return "finish"
        message = state.get("messages", [])[-1]
        return "tools" if isinstance(message, AIMessage) and message.tool_calls else "finish"

    def finish(state: ReActState) -> dict[str, Any]:
        if len(set(state.get("completed_tools", []))) < len(required):
            raise RuntimeError("ReAct finished before all required tools completed")
        from .core import _deliver

        _deliver(session)
        session.status = (
            AgentRunStatus.WAITING_CONFIRMATION
            if session.confirmations else AgentRunStatus.COMPLETED
        )
        persist()
        result = {"status": session.status.value}
        persist_graph(_next_graph_state(state, result))
        return result

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_node("finish", finish)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", "finish": "finish"})
    graph.add_edge("tools", "agent")
    graph.add_edge("finish", END)
    compiled = graph.compile()
    completed_initial = sorted(
        {
            record.tool
            for record in session.tool_calls
            if record.status == ToolCallStatus.COMPLETED and record.tool not in forced
        }
    )
    initial: ReActState | None = _restore_graph_state(store, state_dir)
    if initial is None:
        initial = {
            "messages": [HumanMessage(content="开始执行用户请求。")],
            "completed_tools": completed_initial,
            "observations": [],
            "tool_calls": [],
        }
        persist_graph(initial)
    elif initial.get("status") == "complete":
        return session.model_dump(mode="json")
    try:
        compiled.invoke(initial, {"recursion_limit": settings.agent.recursion_limit})
    except Exception as exc:
        if session.status != AgentRunStatus.FAILED:
            session.status = AgentRunStatus.FAILED
            session.error = str(exc)[-4000:]
            persist()
        raise
    return session.model_dump(mode="json")
