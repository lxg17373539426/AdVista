from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import (
    Base,
    Database,
    DiagnosticRepository,
    DiagnosticRepository,
    ExecutionRepository,
    RequestRepository,
)


def test_diagnostic_repository_returns_execution_timeline(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'diagnostics.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        requests = RequestRepository(database)
        user = requests.ensure_system_user()
        requests.ensure_conversation(
            "conversation_diag",
            user_id=user.user_id,
            run_id="run_diag",
            conversation_type="video_analysis",
        )
        request, _ = requests.create_request(
            request_id="request_diag",
            user_id=user.user_id,
            conversation_id="conversation_diag",
            request_type="video_analysis",
            input_json={"goal": "诊断"},
            idempotency_key="diag-idem",
        )
        executions = ExecutionRepository(database)
        executions.create(
            execution_id="exec_diag",
            request_id=request.request_id,
            model_version="model-v1",
            prompt_version="prompt-v1",
        )
        executions.update("exec_diag", status="failed", error_code="model_timeout", error_message="timeout")
        detail = DiagnosticRepository(database).execution_detail("exec_diag")
        rows = DiagnosticRepository(database).executions(status="failed")
        assert rows[0]["execution_id"] == "exec_diag"
        assert rows[0]["error_code"] == "model_timeout"
        assert detail["execution"]["request_id"] == "request_diag"
        assert detail["tool_calls"] == []
        stats = DiagnosticRepository(database).stats()
        assert stats["executions_by_status"] == {"failed": 1}
        assert stats["errors_by_code"] == {"model_timeout": 1}
        exported = DiagnosticRepository(database).export_executions()
        assert exported == [
            {
                "execution_id": "exec_diag",
                "request_id": "request_diag",
                "request_type": "video_analysis",
                "status": "failed",
                "current_stage": None,
                "model_version": "model-v1",
                "prompt_version": "prompt-v1",
                "context_version": None,
                "error_code": "model_timeout",
                "started_at": None,
                "completed_at": exported[0]["completed_at"],
            }
        ]
    finally:
        database.close()
