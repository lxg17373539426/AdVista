from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ad_vista_agent.config import Settings


class ContextBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_model_tokens: int = Field(ge=1024)
    reserved_output_tokens: int = Field(ge=256)
    reserved_protocol_tokens: int = Field(ge=0)
    soft_limit_tokens: int = Field(ge=1024)
    hard_limit_tokens: int = Field(ge=1024)

    @property
    def input_limit_tokens(self) -> int:
        return self.max_model_tokens - self.reserved_output_tokens - self.reserved_protocol_tokens


def build_context_budget(settings: Settings, *, output_tokens: int | None = None) -> ContextBudget:
    max_tokens = settings.insight.max_model_len
    reserved_output = output_tokens or min(settings.insight.max_tokens, 8192)
    reserved_protocol = min(8192, max(2048, max_tokens // 32))
    input_limit = max_tokens - reserved_output - reserved_protocol
    return ContextBudget(
        max_model_tokens=max_tokens,
        reserved_output_tokens=reserved_output,
        reserved_protocol_tokens=reserved_protocol,
        soft_limit_tokens=int(input_limit * 0.72),
        hard_limit_tokens=int(input_limit * 0.94),
    )


def estimate_tokens(value: Any) -> int:
    """Conservative preflight estimate; model usage remains authoritative."""
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, (len(serialized.encode("utf-8")) + 1) // 2)
