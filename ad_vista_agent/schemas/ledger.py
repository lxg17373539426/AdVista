from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EvidenceModality


class RelationType(StrEnum):
    SUPPORTS = "supports"
    POSSIBLE_CONFLICT = "possible_conflict"


class EvidenceCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    asset_id: str
    modality: EvidenceModality
    canonical_content: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    representative_evidence_id: str
    member_evidence_ids: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_cluster(self) -> "EvidenceCluster":
        if self.end_ms < self.start_ms:
            raise ValueError("Cluster end_ms must be greater than or equal to start_ms")
        if self.representative_evidence_id not in self.member_evidence_ids:
            raise ValueError("Cluster representative must be a member")
        if len(set(self.member_evidence_ids)) != len(self.member_evidence_ids):
            raise ValueError("Cluster members must be unique")
        return self


class EvidenceRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str
    type: RelationType
    left_evidence_id: str
    right_evidence_id: str
    score: float = Field(ge=0, le=1)
    reason: str

    @model_validator(mode="after")
    def validate_relation(self) -> "EvidenceRelation":
        if self.left_evidence_id == self.right_evidence_id:
            raise ValueError("A relation must connect different evidence")
        return self


class EvidenceLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    asset_id: str
    duration_ms: int = Field(gt=0)
    source_evidence_count: int = Field(ge=0)
    ordered_evidence_ids: list[str]
    cluster_ids: list[str]
    relation_ids: list[str]
    modality_counts: dict[str, int]
    configuration: dict[str, object]

    @model_validator(mode="after")
    def validate_counts(self) -> "EvidenceLedger":
        if self.source_evidence_count != len(self.ordered_evidence_ids):
            raise ValueError("source_evidence_count must match ordered evidence IDs")
        if len(set(self.ordered_evidence_ids)) != len(self.ordered_evidence_ids):
            raise ValueError("Evidence IDs must be unique")
        return self
