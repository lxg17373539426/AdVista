from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy import func

from .connection import Database
from .models import ContextVersion, Execution, Feedback, Request, ToolCall


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosticRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def executions(
        self,
        *,
        status: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            query = select(Execution, Request).join(Request, Request.request_id == Execution.request_id)
            if status:
                query = query.where(Execution.status == status)
            if user_id:
                query = query.where(Request.user_id == user_id)
            rows = session.execute(
                query.order_by(Execution.started_at.desc().nullslast()).limit(max(1, min(limit, 500)))
            ).all()
            return [self._execution_summary(execution, request) for execution, request in rows]

    def execution_detail(self, execution_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                raise KeyError(execution_id)
            request = session.get(Request, execution.request_id)
            calls = list(
                session.scalars(
                    select(ToolCall)
                    .where(ToolCall.execution_id == execution_id)
                    .order_by(ToolCall.started_at, ToolCall.call_id)
                )
            )
            contexts = list(
                session.scalars(
                    select(ContextVersion)
                    .where(ContextVersion.execution_id == execution_id)
                    .order_by(ContextVersion.version)
                )
            )
            return {
                "execution": self._execution_summary(execution, request),
                "tool_calls": [
                    {
                        "call_id": call.call_id,
                        "tool_name": call.tool_name,
                        "status": call.status,
                        "attempt": call.attempt,
                        "duration_ms": call.duration_ms,
                        "input": call.input_summary_json,
                        "output": call.output_summary_json,
                        "error_code": call.error_code,
                        "error_message": call.error_message,
                        "started_at": call.started_at.isoformat() if call.started_at else None,
                        "completed_at": call.completed_at.isoformat() if call.completed_at else None,
                    }
                    for call in calls
                ],
                "context_versions": [
                    {
                        "version": value.version,
                        "model_version": value.model_version,
                        "prompt_version": value.prompt_version,
                        "schema_version": value.schema_version,
                        "artifact_hashes": value.artifact_hashes_json,
                        "created_at": value.created_at.isoformat(),
                    }
                    for value in contexts
                ],
            }

    def stats(self) -> dict[str, Any]:
        with self.database.session() as session:
            status_rows = session.execute(
                select(Execution.status, func.count()).group_by(Execution.status)
            ).all()
            error_rows = session.execute(
                select(Execution.error_code, func.count())
                .where(Execution.error_code.is_not(None))
                .group_by(Execution.error_code)
            ).all()
            tool_rows = session.execute(
                select(ToolCall.tool_name, ToolCall.status, func.count())
                .group_by(ToolCall.tool_name, ToolCall.status)
            ).all()
            model_rows = session.execute(
                select(Execution.model_version, func.count())
                .where(Execution.model_version.is_not(None))
                .group_by(Execution.model_version)
            ).all()
            return {
                "executions_by_status": {str(status): int(count) for status, count in status_rows},
                "errors_by_code": {str(code): int(count) for code, count in error_rows},
                "tool_calls": [
                    {"tool_name": str(name), "status": str(status), "count": int(count)}
                    for name, status, count in tool_rows
                ],
                "executions_by_model": {
                    str(model): int(count) for model, count in model_rows
                },
            }

    def execution_owner(self, execution_id: str) -> str | None:
        with self.database.session() as session:
            value = session.scalar(
                select(Request.user_id)
                .join(Execution, Execution.request_id == Request.request_id)
                .where(Execution.execution_id == execution_id)
            )
            return str(value) if value is not None else None

    def run_owner(self, run_id: str) -> str | None:
        with self.database.session() as session:
            value = session.scalar(
                select(Request.user_id)
                .join(Execution, Execution.request_id == Request.request_id)
                .where(Execution.run_id == run_id)
                .limit(1)
            )
            return str(value) if value is not None else None

    def export_executions(self, *, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.executions(status=status, limit=limit)
        return [
            {
                "execution_id": item["execution_id"],
                "request_id": item["request_id"],
                "request_type": item["request_type"],
                "status": item["status"],
                "current_stage": item["current_stage"],
                "model_version": item["model_version"],
                "prompt_version": item["prompt_version"],
                "context_version": item["context_version"],
                "error_code": item["error_code"],
                "started_at": item["started_at"],
                "completed_at": item["completed_at"],
            }
            for item in rows
        ]

    def add_feedback(
        self,
        *,
        feedback_id: str,
        user_id: str,
        conversation_id: str,
        execution_id: str | None,
        message_id: str | None,
        decision: str,
        category: str | None,
        note: str,
    ) -> None:
        if decision not in {"approve", "reject", "revise"}:
            raise ValueError(f"Unsupported feedback decision: {decision}")
        with self.database.session() as session:
            session.add(
                Feedback(
                    feedback_id=feedback_id or f"feedback_{secrets.token_hex(16)}",
                    user_id=user_id,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    message_id=message_id,
                    decision=decision,
                    category=category,
                    note=note[-4000:],
                    created_at=_now(),
                )
            )

    @staticmethod
    def _execution_summary(execution: Execution, request: Request | None) -> dict[str, Any]:
        return {
            "execution_id": execution.execution_id,
            "request_id": execution.request_id,
            "user_id": request.user_id if request is not None else None,
            "request_type": request.request_type if request is not None else None,
            "input": request.input_json if request is not None else None,
            "status": execution.status,
            "current_stage": execution.current_stage,
            "run_id": execution.run_id,
            "model_version": execution.model_version,
            "prompt_version": execution.prompt_version,
            "context_version": execution.context_version,
            "error_code": execution.error_code,
            "error_message": execution.error_message,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        }
