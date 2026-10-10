from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ad_vista_agent.schemas.plans import AgentRequest

_active_cancel_event: ContextVar[threading.Event | None] = ContextVar(
    "advista_active_cancel_event", default=None
)


def set_active_cancel_event(event: threading.Event | None):
    return _active_cancel_event.set(event)


def get_active_cancel_event() -> threading.Event | None:
    return _active_cancel_event.get()


def reset_active_cancel_event(token: object) -> None:
    _active_cancel_event.reset(token)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    run_dir: Path
    source_path: Path | None = None
    execution_dir: Path | None = None
    request: AgentRequest | None = None
    cancel_event: threading.Event | None = None

    def __post_init__(self) -> None:
        if self.cancel_event is None:
            object.__setattr__(self, "cancel_event", _active_cancel_event.get())


class Tool(ABC):
    name: str
    description: str
    input_schema: ClassVar[dict[str, Any]] = {}
    output_schema: ClassVar[dict[str, Any]] = {}
    risk_level: str = "low"
    requires_gpu: bool = False
    cacheable: bool = True

    @abstractmethod
    def run(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        raise NotImplementedError
