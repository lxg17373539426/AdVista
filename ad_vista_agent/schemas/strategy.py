from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # 无相关 OCR / ASR 时允许明确报告证据不足，避免为满足数量约束引用背景歌词。
    evidence_refs: list[Annotated[str, Field(pattern=r"^(?:speech|ocr_cluster)_[0-9]+$")]] = Field(min_length=0, max_length=12)
    unsupported_points: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("evidence_refs")
    @classmethod
    def unique_references(cls, references: list[str]) -> list[str]:
        return list(dict.fromkeys(references))
