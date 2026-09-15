from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, RequestRepository
from ad_vista_agent.web.service import WebService


STATIC = Path(__file__).resolve().parents[1] / "ad_vista_agent" / "web" / "static"


def test_failure_classification_is_actionable_and_never_leaks_paths() -> None:
    classify = WebService._classify_failure

    timeout = classify("model server timeout after 30s at /data/secret/model")
    assert timeout["category"] == "model_unavailable"
    assert timeout["retryable"] is True
    assert "advice" in timeout
    assert "/data/secret" not in timeout["message"]

    overflow = classify("This model's maximum context length is 11264 tokens")
    assert overflow["code"] == "context_overflow"
    assert overflow["retryable"] is False

    invalid_video = classify("无法读取该视频，请确认文件未损坏且包含有效的视频轨道")
    assert invalid_video["category"] == "input_video"
    assert invalid_video["retryable"] is False

    citation = classify("Insight insight_003 writes citation IDs outside evidence_refs")
    assert citation["code"] == "grounding_failed"
    assert citation["category"] == "validation"

    cancel = classify("Job cancelled before execution started")
    assert cancel["category"] == "cancelled"


def test_task_completion_summary_describes_results() -> None:
    empty = WebService._task_completion_summary({"deliverables": {"insights": {"insight_count": 0}, "evidence": {}}})
    assert "没有生成可验证洞察" in empty

    populated = WebService._task_completion_summary(
        {"deliverables": {"report": {"insight_count": 3}, "insights": {"insight_count": 3}}}
    )
    assert "3 条可验证洞察" in populated

    plain = WebService._task_completion_summary({"deliverables": {"evidence": {}}})
    assert "证据提取已完成" in plain


def test_repair_pending_user_messages_only_touches_terminal_requests(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'repair.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        repository = RequestRepository(database)
        user = repository.ensure_system_user()
        repository.ensure_conversation(
            "conversation_repaired",
            user_id=user.user_id,
            run_id=None,
            conversation_type="video_analysis",
        )
        completed, _ = repository.create_request(
            request_id="request_done",
            user_id=user.user_id,
            conversation_id="conversation_repaired",
            request_type="video_analysis",
            input_json={"goal": "卖点"},
            idempotency_key="done-1",
        )
        repository.add_user_message(
            message_id="message_done",
            conversation_id="conversation_repaired",
            user_id=user.user_id,
            content="卖点",
            request_id=completed.request_id,
        )
        repository.mark_completed(completed.request_id)

        pending, _ = repository.create_request(
            request_id="request_live",
            user_id=user.user_id,
            conversation_id="conversation_repaired",
            request_type="video_analysis",
            input_json={"goal": "报告"},
            idempotency_key="live-1",
        )
        repository.add_user_message(
            message_id="message_live",
            conversation_id="conversation_repaired",
            user_id=user.user_id,
            content="报告",
            request_id=pending.request_id,
        )

        result = repository.repair_pending_user_messages()
        assert result["scanned"] == 2
        assert result["completed"] == 1
        statuses = {
            message.message_id: message.status
            for message in repository.messages("conversation_repaired", user_id=user.user_id)
            if message.role == "user"
        }
        assert statuses["message_done"] == "completed"
        assert statuses["message_live"] == "pending"

        # Idempotent second run.
        assert repository.repair_pending_user_messages()["completed"] == 0
    finally:
        database.close()


def test_frontend_exposes_evidence_status_and_removes_duplicate_context_bar() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert html.count('id="context-bar"') == 1
    # Critical epistemic/feedback signals must not be force-hidden.
    for selector in (".answer-status", ".feedback-actions", ".progress-steps", ".context-bar"):
        assert f"{selector} {{ display: none" not in styles
        assert f"{selector} {{ display: none !important" not in styles
    assert ".answer-status" in styles
    assert "flashComposerHint" in script
    assert "请输入问题" in script


def test_frontend_groups_history_and_filters() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "function groupRuns" in script
    assert "function renderRuns" in script
    assert "run-search" in html
    assert 'data-filter="failed"' in html
    assert 'data-filter="report"' in html
    assert "run.deliverables" in script
    assert 'String(run.goal||"视频分析")' in script
    assert '.run-goal { display: none' not in (STATIC / "styles.css").read_text(encoding="utf-8")


def test_frontend_shows_failure_advice() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "failure?.advice" in script or "failure.advice" in script
    assert "retryable" in script


def test_frontend_shows_remaining_time_for_long_jobs() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "function jobTiming" in script
    assert "预计还需约" in script
    assert "job-timing" in styles
