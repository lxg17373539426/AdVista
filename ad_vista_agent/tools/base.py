from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ad_vista_agent.schemas.plans import AgentRequest


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    run_dir: Path
    source_path: Path | None = None
    execution_dir: Path | None = None
    request: AgentRequest | None = None


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}
    risk_level: str = "low"
    requires_gpu: bool = False
    cacheable: bool = True

    @abstractmethod
    def run(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError
