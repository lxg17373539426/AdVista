from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .connection import Database
from .models import Execution, Request, ToolCall


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        execution_id: str,
        request_id: str,
        model_version: str | None,
        prompt_version: str | None,
    ) -> Execution:
        with self.database.session() as session:
            request = session.get(Request, request_id)
            if request is None:
                raise KeyError(request_id)
            existing = session.get(Execution, execution_id)
            if existing is not None:
                session.expunge(existing)
                return existing
            execution = Execution(
                execution_id=execution_id,
                request_id=request_id,
                status="queued",
                model_version=model_version,
                prompt_version=prompt_version,
            )
            session.add(execution)
            session.flush()
            session.expunge(execution)
            return execution

    def update(
        self,
        execution_id: str,
        *,
        status: str,
        current_stage: str | None = None,
        run_id: str | None = None,
        context_version: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.database.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                raise KeyError(execution_id)
            execution.status = status
            execution.current_stage = current_stage
            if run_id is not None:
                execution.run_id = run_id
            if context_version is not None:
                execution.context_version = context_version
            execution.error_code = error_code
            execution.error_message = error_message[-4000:] if error_message else None
            if status == "running" and execution.started_at is None:
                execution.started_at = _now()
            if status in {"completed", "failed", "cancelled", "waiting_confirmation"}:
                execution.completed_at = _now()

    def sync_tool_calls(self, execution_id: str, calls: list[dict[str, Any]]) -> None:
        with self.database.session() as session:
            if session.get(Execution, execution_id) is None:
                raise KeyError(execution_id)
            for item in calls:
                call_id = str(item.get("call_id") or "")
                if not call_id:
                    continue
                tool_call_id = f"{execution_id}:{call_id}"
                record = session.get(ToolCall, tool_call_id)
                if record is None:
                    record = ToolCall(
                        tool_call_id=tool_call_id,
                        execution_id=execution_id,
                        call_id=call_id,
                        tool_name=str(item.get("tool") or "unknown"),
                        status=str(item.get("status") or "unknown"),
                        attempt=1,
                        input_summary_json=dict(item.get("arguments") or {}),
                    )
                    session.add(record)
                record.tool_name = str(item.get("tool") or record.tool_name)
                record.status = str(item.get("status") or record.status)
                record.input_summary_json = dict(item.get("arguments") or {})
                observation = item.get("observation")
                record.output_summary_json = dict(observation) if isinstance(observation, dict) else None
                duration = item.get("duration_seconds")
                record.duration_ms = round(float(duration) * 1000) if duration is not None else None
                record.error_message = str(item.get("error"))[-4000:] if item.get("error") else None
                record.error_code = "tool_failed" if record.status == "failed" else None
                record.started_at = _parse_time(item.get("started_at"))
                record.completed_at = _parse_time(item.get("completed_at"))

    def get(self, execution_id: str) -> Execution:
        with self.database.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                raise KeyError(execution_id)
            session.expunge(execution)
            return execution

    def get_by_request(self, request_id: str) -> Execution | None:
        with self.database.session() as session:
            execution = session.scalar(
                select(Execution).where(Execution.request_id == request_id)
            )
            if execution is None:
                return None
            session.expunge(execution)
            return execution

    def request_context(self, execution_id: str) -> tuple[str, str] | None:
        with self.database.session() as session:
            value = session.execute(
                select(Request.request_id, Request.user_id)
                .join(Execution, Execution.request_id == Request.request_id)
                .where(Execution.execution_id == execution_id)
            ).first()
            return (str(value[0]), str(value[1])) if value is not None else None

    def tool_calls(self, execution_id: str) -> list[ToolCall]:
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(ToolCall)
                    .where(ToolCall.execution_id == execution_id)
                    .order_by(ToolCall.started_at, ToolCall.call_id)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
