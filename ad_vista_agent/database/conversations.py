from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from .connection import Database
from .models import AnswerVersion, Conversation, Feedback, Message, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresConversationStore:
    """ConversationStore-compatible adapter backed by PostgreSQL."""

    def __init__(self, database: Database, *, user_id: str = "system") -> None:
        self.database = database
        self.user_id = user_id

    def create_session(self, *, session_id: str, run_id: str, source_path: Path, goal: str) -> None:
        with self.database.session() as session:
            user = session.get(User, self.user_id)
            if user is None:
                raise KeyError(f"Unknown database user: {self.user_id}")
            existing = session.get(Conversation, session_id)
            if existing is not None:
                if existing.user_id != self.user_id or existing.run_id != run_id:
                    raise ValueError(f"Conversation session metadata conflict: {session_id}")
                return
            session.add(
                Conversation(
                    conversation_id=session_id,
                    user_id=self.user_id,
                    run_id=run_id,
                    conversation_type="general_chat",
                    title=goal,
                    status="active",
                    created_at=_now(),
                    updated_at=_now(),
                )
            )

    def set_status(self, session_id: str, status: str) -> None:
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation)
            conversation.status = status
            conversation.updated_at = _now()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[str],
        *,
        message_id: str | None = None,
        context_version: int | None = None,
    ) -> str:
        message_id = message_id or f"msg_{__import__('uuid').uuid4().hex}"
        with self.database.session() as session:
            conversation = session.scalar(
                select(Conversation)
                .where(Conversation.conversation_id == session_id)
                .with_for_update()
            )
            conversation = self._check_owner(conversation)
            next_sequence = session.scalar(
                select(func.coalesce(func.max(Message.sequence_no), 0) + 1).where(
                    Message.conversation_id == session_id
                )
            )
            session.add(
                Message(
                    message_id=message_id,
                    conversation_id=session_id,
                    sequence_no=int(next_sequence or 1),
                    role=role,
                    content=content,
                    citations_json=citations,
                    context_version=context_version,
                    status="completed",
                    created_at=_now(),
                )
            )
            conversation.updated_at = _now()
        return message_id

    def add_version(
        self,
        session_id: str,
        message_id: str,
        answer: dict[str, Any],
        *,
        version_id: str | None = None,
        context_version: int | None = None,
    ) -> str:
        version_id = version_id or f"version_{__import__('uuid').uuid4().hex}"
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation)
            message = session.get(Message, message_id)
            if message is None or message.conversation_id != session_id:
                raise KeyError(message_id)
            session.add(
                AnswerVersion(
                    version_id=version_id,
                    conversation_id=session_id,
                    message_id=message_id,
                    context_version=context_version,
                    answer_json=answer,
                    created_at=_now(),
                )
            )
        return version_id

    def add_feedback(
        self,
        session_id: str,
        decision: str,
        note: str,
        message_id: str | None = None,
        feedback_id: str | None = None,
    ) -> str:
        if decision not in {"approve", "reject", "revise"}:
            raise ValueError(f"Unsupported feedback decision: {decision}")
        feedback_id = feedback_id or f"feedback_{__import__('uuid').uuid4().hex}"
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation)
            session.add(
                Feedback(
                    feedback_id=feedback_id,
                    user_id=self.user_id,
                    conversation_id=session_id,
                    message_id=message_id,
                    decision=decision,
                    note=note,
                    created_at=_now(),
                )
            )
        return feedback_id

    def messages(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation)
            rows = list(
                session.scalars(
                    select(Message)
                    .where(Message.conversation_id == session_id)
                    .order_by(Message.sequence_no.desc())
                    .limit(limit)
                )
            )
            return [
                {
                    "message_id": row.message_id,
                    "role": row.role,
                    "content": row.content,
                    "citations": list(row.citations_json or []),
                    "created_at": row.created_at.isoformat(),
                }
                for row in reversed(rows)
            ]

    def session(self, session_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation)
            return {
                "session_id": conversation.conversation_id,
                "run_id": conversation.run_id,
                "source_path": "",
                "goal": conversation.title or "",
                "status": conversation.status,
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
            }

    def _check_owner(self, conversation: Conversation | None) -> Conversation:
        if conversation is None:
            raise FileNotFoundError("Unknown conversation session")
        if conversation.user_id != self.user_id:
            raise PermissionError("Conversation does not belong to the authenticated user")
        return conversation
