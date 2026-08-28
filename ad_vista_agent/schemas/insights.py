from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .evidence import EpistemicStatus


class InsightType(StrEnum):
    SELLING_POINT = "selling_point"
    PAIN_POINT = "pain_point"
    AUDIENCE = "audience"
    CREATIVE_STRUCTURE = "creative_structure"
    CONVERSION = "conversion"
    BRAND_EXPOSURE = "brand_exposure"
    RISK = "risk"


class Insight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    insight_id: str
    asset_id: str
    type: InsightType
    claim: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    epistemic_status: EpistemicStatus
    unsupported: bool = False
