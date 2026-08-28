from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EvidenceModality, EpistemicStatus


class AuditSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class InsightAuditStatus(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    FAIL = "fail"


class AuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: AuditSeverity
    message: str
    evidence_refs: list[str] = Field(default_factory=list)


class ReportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str
    source_evidence_id: str
    modality: EvidenceModality
    content: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    epistemic_status: EpistemicStatus
    artifact_path: str
    artifact_hash: str
    member_count: int = Field(default=1, ge=1)


class InsightAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    status: InsightAuditStatus
    findings: list[AuditFinding]
    evidence: list[ReportEvidence]


class CriticAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    asset_id: str
    status: InsightAuditStatus
    insight_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    insights: list[InsightAudit]
    global_findings: list[AuditFinding]

    @model_validator(mode="after")
    def validate_counts(self) -> "CriticAudit":
        if self.insight_count != len(self.insights):
            raise ValueError("Critic insight_count must match insight audits")
        if self.passed_count + self.review_count + self.failed_count != self.insight_count:
            raise ValueError("Critic status counts must sum to insight_count")
        return self
