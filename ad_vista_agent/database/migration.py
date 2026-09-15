from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from typing import Any

from .connection import Database
from .conversations import PostgresConversationStore
from .contexts import ContextRepository
from .requests import RequestRepository
from ad_vista_agent.runtime import sha256_file


def migrate_sqlite_conversations(sqlite_path: Path, database: Database) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    target = PostgresConversationStore(database)
    RequestRepository(database).ensure_system_user()
    counts = {"sessions": 0, "messages": 0, "feedback": 0, "answer_versions": 0, "skipped": 0}
    with sqlite3.connect(sqlite_path) as source:
        source.row_factory = sqlite3.Row
        sessions = source.execute(
            "SELECT session_id, run_id, source_path, goal, status, created_at, updated_at FROM sessions ORDER BY created_at, session_id"
        ).fetchall()
        for item in sessions:
            session_id = str(item["session_id"])
            try:
                target.create_session(
                    session_id=session_id,
                    run_id=str(item["run_id"]),
                    source_path=Path(str(item["source_path"])),
                    goal=str(item["goal"]),
                )
            except ValueError:
                counts["skipped"] += 1
                continue
            target.set_status(session_id, str(item["status"]))
            counts["sessions"] += 1
            for message in source.execute(
                "SELECT message_id, role, content, citations_json FROM messages WHERE session_id = ? ORDER BY created_at, message_id",
                (session_id,),
            ):
                with database.session() as check:
                    from .models import Message

                    if check.get(Message, str(message["message_id"])) is not None:
                        counts["skipped"] += 1
                        continue
                citations = _json_list(message["citations_json"])
                target.add_message(
                    session_id,
                    str(message["role"]),
                    str(message["content"]),
                    citations,
                    message_id=str(message["message_id"]),
                )
                counts["messages"] += 1
            for feedback in source.execute(
                "SELECT feedback_id, message_id, decision, note, created_at FROM feedback WHERE session_id = ? ORDER BY created_at, feedback_id",
                (session_id,),
            ):
                with database.session() as check:
                    from .models import Feedback

                    if check.get(Feedback, str(feedback["feedback_id"])) is not None:
                        counts["skipped"] += 1
                        continue
                target.add_feedback(
                    session_id,
                    str(feedback["decision"]),
                    str(feedback["note"]),
                    str(feedback["message_id"]) if feedback["message_id"] else None,
                    feedback_id=str(feedback["feedback_id"]),
                )
                counts["feedback"] += 1
            # Answer versions are migrated only when their referenced message
            # exists; their old IDs remain in the source audit trail.
            for version in source.execute(
                "SELECT version_id, message_id, answer_json FROM answer_versions WHERE session_id = ? ORDER BY created_at, version_id",
                (session_id,),
            ):
                with database.session() as check:
                    from .models import AnswerVersion

                    if check.get(AnswerVersion, str(version["version_id"])) is not None:
                        counts["skipped"] += 1
                        continue
                target.add_version(
                    session_id,
                    str(version["message_id"]),
                    _json_object(version["answer_json"]),
                    version_id=str(version["version_id"]),
                )
                counts["answer_versions"] += 1
    return counts


def migrate_json_jobs(job_root: Path, database: Database) -> dict[str, int]:
    if not job_root.is_dir():
        raise FileNotFoundError(job_root)
    requests = RequestRepository(database)
    executions = __import__("ad_vista_agent.database", fromlist=["ExecutionRepository"]).ExecutionRepository(database)
    requests.ensure_system_user()
    counts = {"jobs": 0, "executions": 0, "tool_calls": 0, "skipped": 0}
    for path in sorted(job_root.glob("job_*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        execution_id = str(raw.get("execution_id") or "")
        if not execution_id:
            counts["skipped"] += 1
            continue
        request_id = str(raw.get("request_id") or f"request_job_{execution_id[5:]}")
        conversation_id = f"conversation_{execution_id}"
        requests.ensure_conversation(
            conversation_id,
            user_id="system",
            run_id=str(raw.get("asset_run_id") or "") or None,
            conversation_type="video_analysis",
        )
        try:
            existing_request = requests.get_request(request_id)
        except KeyError:
            existing_request = None
        try:
            existing_execution = executions.get(execution_id)
        except KeyError:
            existing_execution = None
        if existing_request is not None and existing_execution is not None:
            counts["skipped"] += 1
            continue
        request, created = requests.create_request(
            request_id=request_id,
            user_id="system",
            conversation_id=conversation_id,
            request_type="video_analysis",
            input_json={
                "goal": str(raw.get("goal") or "视频分析"),
                "mode": str(raw.get("mode") or "quick"),
                "deliverables": list(raw.get("deliverables") or []),
                "execution_id": execution_id,
                "job_id": str(raw.get("job_id") or path.stem),
            },
            idempotency_key=f"migrated:{path.stem}",
        )
        if created:
            requests.add_user_message(
                message_id=f"message_job_{execution_id[5:]}",
                conversation_id=conversation_id,
                user_id="system",
                content=str(raw.get("goal") or "视频分析"),
                request_id=request.request_id,
            )
        execution = executions.create(
            execution_id=execution_id,
            request_id=request.request_id,
            model_version=None,
            prompt_version=None,
        )
        counts["jobs"] += 1
        if existing_execution is None:
            counts["executions"] += 1
        status = str(raw.get("status") or "failed")
        if status not in {"queued", "running", "waiting_confirmation", "completed", "failed", "cancelled"}:
            status = "failed"
        executions.update(
            execution_id,
            status=status,
            run_id=str(raw.get("asset_run_id") or "") or None,
            error_message=str(raw.get("error") or "") or None,
            error_code="job_migration_error" if status == "failed" else None,
        )
        result = raw.get("result")
        if isinstance(result, dict):
            calls = result.get("tool_calls")
            if isinstance(calls, list):
                executions.sync_tool_calls(
                    execution_id,
                    [item for item in calls if isinstance(item, dict)],
                )
                counts["tool_calls"] += len(calls)
        if status in {"completed", "waiting_confirmation"}:
            requests.mark_completed(request.request_id)
        elif status == "cancelled":
            requests.mark_cancelled(request.request_id)
        elif status == "failed":
            requests.mark_failed(
                request.request_id,
                error_code="job_migration_error",
                error_message=str(raw.get("error") or "historical job failed"),
            )
    return counts


def migrate_json_contexts(
    job_root: Path, output_root: Path, database: Database
) -> dict[str, int]:
    if not job_root.is_dir():
        raise FileNotFoundError(job_root)
    contexts = ContextRepository(database)
    counts = {"contexts": 0, "skipped": 0, "missing": 0}
    for path in sorted(job_root.glob("job_*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        execution_id = str(raw.get("execution_id") or "")
        run_id = str(raw.get("asset_run_id") or "")
        if not execution_id or not run_id:
            counts["missing"] += 1
            continue
        run_dir = output_root / "runs" / run_id
        execution_root = output_root / "executions" / execution_id
        artifact_root = execution_root / "artifacts"
        if not artifact_root.is_dir():
            artifact_root = run_dir
        candidates = {
            "asset": run_dir / "asset.json",
            "timeline": run_dir / "timeline" / "keyframes.jsonl",
            "speech": run_dir / "speech" / "evidence.jsonl",
            "ocr": run_dir / "ocr" / "evidence.jsonl",
            "ledger": run_dir / "ledger" / "ledger.json",
            "insights": artifact_root / "insights" / "analysis.json",
            "report": artifact_root / "report" / "report.html",
        }
        hashes = {name: sha256_file(value) for name, value in candidates.items() if value.is_file()}
        if not hashes:
            counts["missing"] += 1
            continue
        conversation_id = f"conversation_{execution_id}"
        latest = contexts.latest(conversation_id)
        if latest is not None and latest.artifact_hashes_json == hashes:
            counts["skipped"] += 1
            continue
        try:
            contexts.publish(
                conversation_id=conversation_id,
                execution_id=execution_id,
                schema_version="0.1",
                model_version=None,
                prompt_version=None,
                artifact_hashes=hashes,
            )
        except KeyError:
            counts["missing"] += 1
            continue
        counts["contexts"] += 1
    return counts


def _json_list(value: str) -> list[str]:
    import json

    raw = json.loads(value)
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _json_object(value: str) -> dict[str, Any]:
    import json

    raw = json.loads(value)
    return raw if isinstance(raw, dict) else {}
