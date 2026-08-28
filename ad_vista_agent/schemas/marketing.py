from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import EpistemicStatus


class MarketingDimension(StrEnum):
    SELLING_POINT = "selling_point"
    PAIN_POINT = "pain_point"
    AUDIENCE = "audience"
    CREATIVE_STRUCTURE = "creative_structure"
    CONVERSION_PATH = "conversion_path"
    BRAND_EXPOSURE = "brand_exposure"
    RISK = "risk"


class GroundedMarketingInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insight_id: str
    dimension: MarketingDimension
    claim: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=0.85)
    epistemic_status: EpistemicStatus
    reasoning_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_refs(self) -> "GroundedMarketingInsight":
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Insight evidence references must be unique")
        return self


class MarketingAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    asset_id: str
    language: str
    subject: str
    executive_summary: str
    insights: list[GroundedMarketingInsight]
    unknowns: list[str]
