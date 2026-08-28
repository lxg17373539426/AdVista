from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceModality(StrEnum):
    VIDEO = "video"
    FRAME = "frame"
    SPEECH = "speech"
    OCR = "ocr"
    AUDIO = "audio"
    DETECTION = "detection"
    RETRIEVAL = "retrieval"


class EpistemicStatus(StrEnum):
    OBSERVED = "observed"
    STATED_BY_AD = "stated_by_ad"
    INFERRED_FROM_AD = "inferred_from_ad"
    RETRIEVED_KNOWLEDGE = "retrieved_knowledge"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Bounding box must have positive area")
        return self


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    evidence_id: str
    asset_id: str
    modality: EvidenceModality
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    content: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    epistemic_status: EpistemicStatus
    tool: str
    model_version: str | None = None
    artifact_path: Path | None = None
    artifact_hash: str | None = None
    region: BoundingBox | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval(self) -> "Evidence":
        if self.end_ms < self.start_ms:
            raise ValueError("Evidence end_ms must be greater than or equal to start_ms")
        return self
