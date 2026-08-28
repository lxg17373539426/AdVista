from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConversationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=12000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    epistemic_status: str = Field(pattern=r"^(grounded|visual|conversational|unknown)$")
    unsupported_points: list[str] = Field(default_factory=list, max_length=12)
    follow_up_needed: bool = False
