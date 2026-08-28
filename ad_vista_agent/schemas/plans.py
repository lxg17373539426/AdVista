from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(StrEnum):
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolCallStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class ReflectionDecision(StrEnum):
    CONTINUE = "continue"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    DELIVER = "deliver"
    FAIL = "fail"


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    purpose: str
    depends_on: list[str] = Field(default_factory=list)
    arguments: dict[str, object] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    goal: str
    mode: str = "quick"
    steps: list[PlanStep]
    deliverables: list[str]
    max_tool_calls: int = Field(default=20, ge=1)
    requires_confirmation: list[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    goal: str = Field(min_length=1, max_length=4000)
    mode: str = Field(default="quick", pattern=r"^(quick|deep)$")
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    max_tool_calls: int = Field(default=12, ge=1, le=30)
    max_replans: int = Field(default=2, ge=0, le=5)


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: ToolCallStatus
    started_at: str
    completed_at: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    observation: dict[str, object] = Field(default_factory=dict)
    error: str | None = None


class ReflectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reflection_id: str
    decision: ReflectionDecision
    reason: str
    missing_evidence: list[str] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    created_at: str


class AgentSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.2"
    execution_id: str | None = None
    asset_id: str | None = None
    session_id: str
    run_id: str
    source_path: Path
    request: AgentRequest
    plan: AnalysisPlan
    status: AgentRunStatus
    replan_count: int = Field(default=0, ge=0)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    observations: list[dict[str, object]] = Field(default_factory=list)
    reflections: list[ReflectionRecord] = Field(default_factory=list)
    confirmations: list[str] = Field(default_factory=list)
    approved_confirmations: list[str] = Field(default_factory=list)
    deliverables: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str


class PipelineStageState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    cache_hit: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    error: str | None = None


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    pipeline_version: str
    run_id: str
    source_path: Path
    config_path: Path
    status: PipelineStatus
    current_stage: str | None = None
    stages: list[PipelineStageState]
    audit_status: str | None = None
    review_approved: bool = False
    report_markdown: Path | None = None
    report_html: Path | None = None
    error: str | None = None
    created_at: str
    updated_at: str
