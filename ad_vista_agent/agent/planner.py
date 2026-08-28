from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ad_vista_agent.config import Settings
from ad_vista_agent.schemas import AgentRequest, AnalysisPlan, PlanStep
from ad_vista_agent.tools import ToolRegistry


TOOL_DEPENDENCIES: dict[str, set[str]] = {
    "timeline": {"ingest"},
    "speech": {"timeline"},
    "ocr": {"timeline"},
    "ledger": {"speech", "ocr"},
    "insights": {"ledger"},
    "report": {"insights"},
    "creative": {"insights"},
}
ALLOWED_DELIVERABLES = {"evidence", "insights", "risk_audit", "report", "creative"}
CANONICAL_TOOL_ORDER = ("ingest", "timeline", "speech", "ocr", "ledger", "insights", "report", "creative")
DELIVERABLE_TOOL = {
    "evidence": "ledger",
    "insights": "insights",
    "risk_audit": "report",
    "report": "report",
    "creative": "creative",
}


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    selected_tools: list[str] = Field(min_length=1, max_length=8)
    deliverables: list[str] = Field(min_length=1, max_length=5)
    requires_confirmation: list[str] = Field(default_factory=list, max_length=4)


def _compile_tools(
    deliverables: list[str], selected_tools: set[str] | None = None
) -> list[str]:
    invalid_deliverables = set(deliverables).difference(ALLOWED_DELIVERABLES)
    if invalid_deliverables:
        raise ValueError(f"Unknown deliverables: {', '.join(sorted(invalid_deliverables))}")
    selected = set(selected_tools or set())
    selected.update(DELIVERABLE_TOOL[item] for item in deliverables)
    changed = True
    while changed:
        changed = False
        for tool in tuple(selected):
            missing = TOOL_DEPENDENCIES.get(tool, set()).difference(selected)
            if missing:
                selected.update(missing)
                changed = True
    selected.add("ingest")
    return [name for name in CANONICAL_TOOL_ORDER if name in selected]


def _build_plan(
    request: AgentRequest,
    deliverables: list[str],
    *,
    selected_tools: set[str] | None = None,
    requires_confirmation: list[str] | None = None,
) -> AnalysisPlan:
    normalized_deliverables = list(dict.fromkeys(deliverables))
    tools = _compile_tools(normalized_deliverables, selected_tools)
    return AnalysisPlan(
        goal=request.goal,
        mode=request.mode,
        steps=[
            PlanStep(
                step_id=f"step_{index:02d}",
                tool=name,
                purpose=f"为用户目标执行 {name}",
                depends_on=sorted(TOOL_DEPENDENCIES.get(name, set())),
                arguments={"force": False},
            )
            for index, name in enumerate(tools, 1)
        ],
        deliverables=normalized_deliverables,
        max_tool_calls=request.max_tool_calls,
        requires_confirmation=list(requires_confirmation or []),
    )


def compile_decision(decision: PlannerDecision, request: AgentRequest) -> AnalysisPlan:
    if decision.goal != request.goal:
        raise ValueError("Planner changed the user goal")
    selected = set(decision.selected_tools)
    unknown = selected.difference(CANONICAL_TOOL_ORDER)
    if unknown:
        raise ValueError(f"Planner selected unknown tool: {', '.join(sorted(unknown))}")
    if request.deliverables:
        deliverables = list(request.deliverables)
        selected = set()
    else:
        deliverables = list(decision.deliverables)
    return _build_plan(
        request,
        deliverables,
        selected_tools=selected,
        requires_confirmation=decision.requires_confirmation,
    )


def rule_plan(request: AgentRequest) -> AnalysisPlan:
    goal = request.goal.casefold()
    requested = [item for item in request.deliverables if item in ALLOWED_DELIVERABLES]
    if requested:
        deliverables = requested
    elif any(term in goal for term in ("证据", "字幕", "语音", "ocr", "asr", "evidence")) and not any(
        term in goal for term in ("卖点", "风险", "报告", "洞察", "受众", "营销", "risk", "report", "insight")
    ):
        deliverables = ["evidence"]
    elif any(term in goal for term in ("卖点", "受众", "痛点", "洞察", "营销", "insight")) and not any(
        term in goal for term in ("报告", "风险", "合规", "risk", "report")
    ):
        deliverables = ["insights"]
    elif any(term in goal for term in ("hook", "脚本", "分镜", "创作", "a/b", "ab", "广告文案")):
        deliverables = ["creative"]
    else:
        deliverables = ["risk_audit", "report"]
    return _build_plan(request, deliverables)


def validate_plan(plan: AnalysisPlan, registry: ToolRegistry, request: AgentRequest) -> AnalysisPlan:
    if plan.goal != request.goal:
        raise ValueError("Planner changed the user goal")
    if plan.mode != request.mode:
        raise ValueError("Planner changed the execution mode")
    if request.deliverables and plan.deliverables != list(dict.fromkeys(request.deliverables)):
        raise ValueError("Planner changed the requested deliverables")
    if len(plan.steps) > request.max_tool_calls:
        raise ValueError("Plan exceeds the tool-call budget")
    if not plan.steps or plan.steps[0].tool != "ingest":
        raise ValueError("Plan must begin with ingest")
    known = set(registry.names())
    completed: set[str] = set()
    step_ids: set[str] = set()
    seen_calls: set[str] = set()
    for step in plan.steps:
        if step.step_id in step_ids:
            raise ValueError(f"Duplicate plan step ID: {step.step_id}")
        step_ids.add(step.step_id)
        if step.tool not in known:
            raise ValueError(f"Planner selected unknown tool: {step.tool}")
        if step.tool in seen_calls:
            raise ValueError(f"Plan repeats tool with the same fixed workflow: {step.tool}")
        seen_calls.add(step.tool)
        required = TOOL_DEPENDENCIES.get(step.tool, set())
        if not required.issubset(completed):
            missing = ", ".join(sorted(required - completed))
            raise ValueError(f"Tool {step.tool} has unsatisfied dependencies: {missing}")
        if set(step.arguments).difference({"force"}):
            raise ValueError(f"Tool {step.tool} received unsupported arguments")
        if "force" in step.arguments and not isinstance(step.arguments["force"], bool):
            raise ValueError(f"Tool {step.tool} force argument must be boolean")
        completed.add(step.tool)
    invalid_deliverables = set(plan.deliverables).difference(ALLOWED_DELIVERABLES)
    if invalid_deliverables:
        raise ValueError(f"Unknown deliverables: {', '.join(sorted(invalid_deliverables))}")
    if "report" in plan.deliverables and "report" not in completed:
        raise ValueError("Report deliverable requires the report tool")
    if "risk_audit" in plan.deliverables and "report" not in completed:
        raise ValueError("Risk-audit deliverable requires the report tool")
    if "insights" in plan.deliverables and "insights" not in completed:
        raise ValueError("Insights deliverable requires the insights tool")
    if "creative" in plan.deliverables and "creative" not in completed:
        raise ValueError("Creative deliverable requires the creative tool")
    if "evidence" in plan.deliverables and "ledger" not in completed:
        raise ValueError("Evidence deliverable requires the ledger tool")
    return plan


def qwen_plan(request: AgentRequest, registry: ToolRegistry, settings: Settings) -> AnalysisPlan:
    tool_descriptions = registry.describe()
    schema = PlannerDecision.model_json_schema()
    prompt = (
        "你是受约束的广告分析 Agent Planner。只决定需要哪些工具和交付物，不生成执行步骤或参数。"
        "selected_tools 只能使用工具列表中的名称，并且只能选择目标所需的最终能力；本地编译器会补齐依赖。"
        "如果 request.deliverables 非空，必须严格服从这些交付物，不得增加其他最终工具。"
        "不要执行工具。deliverables 仅允许 evidence、insights、risk_audit、report、creative。goal 必须原样复制。"
    )
    payload = {
        "model": settings.insight.served_model,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": request.model_dump(mode="json"),
                        "tools": tool_descriptions,
                        "dependencies": {
                            key: sorted(value) for key, value in TOOL_DEPENDENCIES.items()
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 1024,
        "seed": 42,
        "structured_outputs": {"json": schema},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    call = urllib.request.Request(
        settings.insight.endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(call, timeout=settings.insight.timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        text = str(value["choices"][0]["message"]["content"])
        try:
            decision = PlannerDecision.model_validate_json(text)
        except ValueError:
            repair_payload = {
                "model": settings.insight.served_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "仅修复 JSON 语法。不要改变 goal、selected_tools、deliverables 或 requires_confirmation，不要添加内容。返回完整 JSON 对象。",
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
                "max_tokens": 1024,
                "seed": 43,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            repair_call = urllib.request.Request(
                settings.insight.endpoint.rstrip("/") + "/chat/completions",
                data=json.dumps(repair_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(repair_call, timeout=settings.insight.timeout_seconds) as repair_response:
                repaired = json.loads(repair_response.read().decode("utf-8"))
            decision = PlannerDecision.model_validate_json(
                str(repaired["choices"][0]["message"]["content"])
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Planner service HTTP {exc.code}: {body[-4000:]}") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Planner returned an invalid plan: {exc}") from exc
    return validate_plan(compile_decision(decision, request), registry, request)
