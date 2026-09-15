from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .connection import Database
from .models import Conversation, Message, Request, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RequestRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_system_user(self) -> User:
        with self.database.session() as session:
            user = session.get(User, "system")
            if user is None:
                user = User(
                    user_id="system",
                    username="system",
                    role="admin",
                    status="active",
                    created_at=_now(),
                )
                session.add(user)
                session.flush()
            session.expunge(user)
            return user

    def ensure_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str,
        run_id: str | None,
        conversation_type: str,
        title: str | None = None,
    ) -> Conversation:
        with self.database.session() as session:
            conversation = session.scalar(
                select(Conversation)
                .where(Conversation.conversation_id == conversation_id)
                .with_for_update()
            )
            if conversation is None:
                conversation = Conversation(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    run_id=run_id,
                    conversation_type=conversation_type,
                    title=title,
                    status="active",
                    created_at=_now(),
                    updated_at=_now(),
                )
                session.add(conversation)
                session.flush()
            elif conversation.user_id != user_id:
                raise PermissionError("Conversation does not belong to the authenticated user")
            session.expunge(conversation)
            return conversation

    def create_request(
        self,
        *,
        request_id: str,
        user_id: str,
        conversation_id: str | None,
        request_type: str,
        input_json: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[Request, bool]:
        with self.database.session() as session:
            existing = session.scalar(
                select(Request).where(
                    Request.user_id == user_id,
                    Request.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing, False
            request = Request(
                request_id=request_id,
                user_id=user_id,
                conversation_id=conversation_id,
                request_type=request_type,
                input_json=input_json,
                status="pending",
                idempotency_key=idempotency_key,
                created_at=_now(),
            )
            session.add(request)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(Request).where(
                        Request.user_id == user_id,
                        Request.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                session.expunge(existing)
                return existing, False
            session.expunge(request)
            return request, True

    def add_user_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: str,
        request_id: str,
        context_version: int | None = None,
    ) -> Message:
        return self._add_message(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="user",
            content=content,
            request_id=request_id,
            citations=[],
            context_version=context_version,
            status="pending",
        )

    def add_assistant_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: str,
        request_id: str,
        citations: list[str],
        context_version: int | None = None,
    ) -> Message:
        return self._add_message(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
            content=content,
            request_id=request_id,
            citations=citations,
            context_version=context_version,
            status="completed",
        )

    def _add_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        request_id: str,
        citations: list[str],
        context_version: int | None,
        status: str,
    ) -> Message:
        with self.database.session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise PermissionError("Conversation does not belong to the authenticated user")
            next_sequence = session.scalar(
                select(func.coalesce(func.max(Message.sequence_no), 0) + 1).where(
                    Message.conversation_id == conversation_id
                )
            )
            message = Message(
                message_id=message_id,
                conversation_id=conversation_id,
                request_id=request_id,
                sequence_no=int(next_sequence or 1),
                role=role,
                content=content,
                citations_json=citations,
                context_version=context_version,
                status=status,
                created_at=_now(),
            )
            session.add(message)
            conversation.updated_at = _now()
            session.flush()
            session.expunge(message)
            return message

    def mark_completed(self, request_id: str) -> None:
        self._mark_request(request_id, "completed")

    def mark_cancelled(self, request_id: str) -> None:
        self._mark_request(request_id, "cancelled")

    def complete_user_message(self, request_id: str) -> None:
        with self.database.session() as session:
            session.query(Message).filter(
                Message.request_id == request_id,
                Message.role == "user",
            ).update({"status": "completed"})

    def repair_pending_user_messages(self) -> dict[str, int]:
        """Complete user messages whose request already reached a terminal state.

        Before the status-sync fix, general chat and report-mode video requests
        created a `pending` user message that was never advanced. This repair is
        idempotent and only touches messages tied to completed/failed/cancelled
        requests, so in-flight requests are left intact.
        """
        terminal = {"completed", "failed", "cancelled"}
        with self.database.session() as session:
            pending = list(
                session.scalars(
                    select(Message)
                    .where(Message.role == "user", Message.status == "pending")
                )
            )
            request_ids = {
                str(message.request_id)
                for message in pending
                if message.request_id is not None
            }
            if not request_ids:
                return {"scanned": 0, "completed": 0}
            finished = {
                str(value)
                for value in session.scalars(
                    select(Request.request_id).where(
                        Request.request_id.in_(request_ids),
                        Request.status.in_(terminal),
                    )
                )
            }
            if not finished:
                return {"scanned": len(pending), "completed": 0}
            messages = list(
                session.scalars(
                    select(Message).where(
                        Message.role == "user",
                        Message.status == "pending",
                        Message.request_id.in_(finished),
                    )
                )
            )
            for message in messages:
                message.status = "completed"
            return {"scanned": len(pending), "completed": len(messages)}

    def mark_failed(self, request_id: str, *, error_code: str, error_message: str) -> None:
        self._mark_request(
            request_id,
            "failed",
            error_code=error_code,
            error_message=error_message[-4000:],
        )

    def messages(self, conversation_id: str, *, user_id: str) -> list[Message]:
        with self.database.session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise PermissionError("Conversation does not belong to the authenticated user")
            values = list(
                session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.sequence_no)
                )
            )
            for value in values:
                session.expunge(value)
            return values

    def get_request(self, request_id: str) -> Request:
        with self.database.session() as session:
            request = session.get(Request, request_id)
            if request is None:
                raise KeyError(request_id)
            session.expunge(request)
            return request

    def _mark_request(
        self,
        request_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.database.session() as session:
            request = session.get(Request, request_id)
            if request is None:
                raise KeyError(request_id)
            request.status = status
            request.error_code = error_code
            request.error_message = error_message
            request.completed_at = _now()

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(16)}"
