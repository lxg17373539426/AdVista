from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .insights import Insight


class AnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    report_id: str
    asset_ids: list[str] = Field(min_length=1)
    goal: str
    summary: str
    insights: list[Insight]
    deliverables: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
