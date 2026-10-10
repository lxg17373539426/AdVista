from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update

from ad_vista_agent.errors import ConversationBusyError
from ad_vista_agent.memory.metadata import answer_metadata

from .connection import Database
from .models import (
    AnswerVersion,
    Conversation,
    Execution,
    Feedback,
    Message,
    Request,
    User,
)


def _now() -> datetime:
    return datetime.now(UTC)


class PostgresConversationStore:
    """ConversationStore-compatible adapter backed by PostgreSQL."""

    def __init__(self, database: Database, *, user_id: str = "system") -> None:
        self.database = database
        self.user_id = user_id

    def response_for_request(self, request_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            request = session.get(Request, request_id)
            if request is None or request.user_id != self.user_id:
                raise PermissionError(
                    "Request does not belong to the authenticated user"
                )
            self._check_owner(session.get(Conversation, request.conversation_id))
            row = session.scalar(
                select(AnswerVersion)
                .join(Message, Message.message_id == AnswerVersion.message_id)
                .where(Message.request_id == request_id)
                .order_by(AnswerVersion.created_at.desc())
                .limit(1)
            )
            return dict(row.answer_json) if row is not None else None

    def complete_response(
        self,
        session_id: str,
        request_id: str,
        response: dict[str, Any],
        *,
        question: str,
    ) -> dict[str, Any]:
        """Commit response, version and request status together, using one message identity."""
        import uuid

        with self.database.session() as session:
            conversation = self._check_owner(
                session.scalar(
                    select(Conversation)
                    .where(Conversation.conversation_id == session_id)
                    .with_for_update()
                )
            )
            request = session.get(Request, request_id)
            if (
                request is None
                or request.user_id != self.user_id
                or request.conversation_id != session_id
            ):
                raise PermissionError("Request does not belong to this conversation")
            if request.status in {"failed", "cancelled"}:
                raise ValueError(
                    "Request failed or was cancelled; use a new idempotency key"
                )
            prior = session.scalar(
                select(AnswerVersion)
                .join(Message, Message.message_id == AnswerVersion.message_id)
                .where(Message.request_id == request_id)
            )
            if prior is not None:
                return dict(prior.answer_json)
            sequence = int(
                session.scalar(
                    select(func.coalesce(func.max(Message.sequence_no), 0)).where(
                        Message.conversation_id == session_id
                    )
                )
                or 0
            )
            user_message = session.scalar(
                select(Message).where(
                    Message.request_id == request_id, Message.role == "user"
                )
            )
            if user_message is None:
                sequence += 1
                user_message = Message(
                    message_id=f"message_{uuid.uuid4().hex}",
                    conversation_id=session_id,
                    request_id=request_id,
                    sequence_no=sequence,
                    role="user",
                    content=question,
                    citations_json=[],
                    status="completed",
                    created_at=_now(),
                )
                session.add(user_message)
            user_message.status = "completed"
            result = {
                **response,
                "user_message_id": user_message.message_id,
                "assistant_message_id": f"message_{uuid.uuid4().hex}",
                "version_id": f"version_{uuid.uuid4().hex}",
            }
            message = Message(
                message_id=result["assistant_message_id"],
                conversation_id=session_id,
                request_id=request_id,
                sequence_no=sequence + 1,
                role="assistant",
                content=result["answer"],
                citations_json=result["evidence_refs"],
                context_version=result.get("context_version"),
                status="completed",
                created_at=_now(),
            )
            session.add(message)
            session.flush()
            session.add(
                AnswerVersion(
                    version_id=result["version_id"],
                    conversation_id=session_id,
                    message_id=message.message_id,
                    context_version=result.get("context_version"),
                    answer_json=result,
                    created_at=_now(),
                )
            )
            request.status = "completed"
            request.completed_at = _now()
            request.error_code = request.error_message = None
            conversation.updated_at = _now()
            return result

    def create_session(
        self, *, session_id: str, run_id: str, source_path: Path, goal: str
    ) -> None:
        with self.database.session() as session:
            user = session.get(User, self.user_id)
            if user is None:
                raise KeyError(f"Unknown database user: {self.user_id}")
            existing = session.get(Conversation, session_id)
            if existing is not None:
                if existing.user_id != self.user_id or existing.run_id != run_id:
                    raise ValueError(
                        f"Conversation session metadata conflict: {session_id}"
                    )
                self._check_owner(existing)
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
            conversation = session.scalar(
                select(Conversation).where(Conversation.conversation_id == session_id).with_for_update()
            )
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
            conversation = session.scalar(
                select(Conversation).where(Conversation.conversation_id == session_id).with_for_update()
            )
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
            conversation = session.scalar(
                select(Conversation).where(Conversation.conversation_id == session_id).with_for_update()
            )
            conversation = self._check_owner(conversation)
            if message_id is not None:
                message = session.get(Message, message_id)
                if message is None or message.conversation_id != session_id:
                    raise KeyError(message_id)
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
            versions = session.scalars(
                select(AnswerVersion)
                .where(
                    AnswerVersion.conversation_id == session_id,
                    AnswerVersion.message_id.in_([row.message_id for row in rows]),
                )
                .order_by(AnswerVersion.created_at, AnswerVersion.version_id)
            )
            latest = {
                version.message_id: answer_metadata(version.answer_json)
                for version in versions
            }
            return [
                {
                    **latest.get(row.message_id, {}),
                    "message_id": row.message_id,
                    "request_id": row.request_id,
                    "role": row.role,
                    "content": row.content,
                    "citations": list(row.citations_json or []),
                    "created_at": row.created_at.isoformat(),
                }
                for row in reversed(rows)
            ]

    def session(self, session_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        with self.database.session() as session:
            conversation = session.get(Conversation, session_id)
            conversation = self._check_owner(conversation, include_deleted=include_deleted)
            return {
                "session_id": conversation.conversation_id,
                "run_id": conversation.run_id,
                "source_path": "",
                "goal": conversation.title or "",
                "title": conversation.title,
                "status": conversation.status,
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
                "deleted_at": conversation.deleted_at.isoformat() if conversation.deleted_at is not None else None,
            }

    def list_sessions(
        self, *, general_only: bool = False, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            query = select(Conversation).where(
                Conversation.user_id == self.user_id,
                Conversation.deleted_at.is_(None),
            )
            if general_only:
                query = query.where(
                    Conversation.conversation_type == "general_chat",
                    Conversation.run_id.startswith("general_", autoescape=True),
                )
            rows = session.scalars(
                query.order_by(
                    Conversation.updated_at.desc(), Conversation.conversation_id
                ).limit(max(0, limit)).offset(max(0, offset))
            )
            return [
                {
                    "session_id": row.conversation_id,
                    "run_id": row.run_id,
                    "title": row.title,
                    "status": row.status,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]

    def set_title(
        self, session_id: str, title: str, *, only_if_empty: bool = False
    ) -> str:
        with self.database.session() as session:
            conversation = self._check_owner(session.get(Conversation, session_id))
            if conversation.deleted_at is not None:
                raise FileNotFoundError("Unknown conversation session")
            statement = update(Conversation).where(
                Conversation.conversation_id == session_id,
                Conversation.user_id == self.user_id,
                Conversation.deleted_at.is_(None),
            )
            if only_if_empty:
                statement = statement.where(
                    or_(Conversation.title.is_(None), Conversation.title == "")
                )
            session.execute(
                statement.values(title=title, updated_at=_now()),
                execution_options={"synchronize_session": False},
            )
            session.refresh(conversation)
            self._check_owner(conversation)
            if conversation.deleted_at is not None:
                raise FileNotFoundError("Unknown conversation session")
            return conversation.title or ""

    def _check_owner(self, conversation: Conversation | None, *, include_deleted: bool = False) -> Conversation:
        if conversation is None:
            raise FileNotFoundError("Unknown conversation session")
        if conversation.user_id != self.user_id:
            raise PermissionError(
                "Conversation does not belong to the authenticated user"
            )
        if conversation.deleted_at is not None and not include_deleted:
            raise FileNotFoundError("Unknown conversation session")
        return conversation

    def delete_session(self, session_id: str) -> None:
        """Hide a session while retaining its contents for administrator recovery."""
        with self.database.session() as session:
            conversation = session.scalar(
                select(Conversation).where(Conversation.conversation_id == session_id).with_for_update()
            )
            if conversation is None or conversation.user_id != self.user_id:
                raise FileNotFoundError("Unknown conversation session")
            if conversation.deleted_at is None:
                active = session.scalar(
                    select(Execution.execution_id).join(Request).where(
                        Request.conversation_id == session_id,
                        Execution.status.in_({"queued", "running", "cancelling"}),
                    ).limit(1)
                )
                if active is not None:
                    raise ConversationBusyError("视频任务仍在运行，请先取消任务，等待停止后再删除。")
                conversation.deleted_at = _now()
                conversation.updated_at = conversation.deleted_at
                session.execute(
                    update(Request).where(
                        Request.conversation_id == session_id, Request.status == "pending"
                    ).values(status="cancelled", completed_at=_now())
                )
