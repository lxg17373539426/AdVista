from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MarketingStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    positioning: str = Field(min_length=1, max_length=1000)
    target_audience: str = Field(min_length=1, max_length=1000)
    core_message: str = Field(min_length=1, max_length=1000)
    communication_strategy: list[str] = Field(min_length=1, max_length=8)
    content_directions: list[str] = Field(min_length=1, max_length=8)
    channel_recommendations: list[str] = Field(min_length=1, max_length=8)
    conversion_recommendations: list[str] = Field(min_length=1, max_length=8)
    hypotheses: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(min_length=1, max_length=12)
    unsupported_points: list[str] = Field(default_factory=list, max_length=12)
