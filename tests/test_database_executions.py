from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig
from ad_vista_agent.database import Base, Database, ExecutionRepository, RequestRepository


def test_execution_and_tool_calls_are_queryable(tmp_path: Path) -> None:
    database = Database(
        DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'execution.db'}"))
    )
    try:
        Base.metadata.create_all(database.engine)
        requests = RequestRepository(database)
        user = requests.ensure_system_user()
        requests.ensure_conversation(
            "conversation_execution",
            user_id=user.user_id,
            run_id="run_execution",
            conversation_type="video_analysis",
        )
        request, _ = requests.create_request(
            request_id="request_execution",
            user_id=user.user_id,
            conversation_id="conversation_execution",
            request_type="video_analysis",
            input_json={"goal": "分析卖点"},
            idempotency_key="execution-idem",
        )
        executions = ExecutionRepository(database)
        executions.create(
            execution_id="exec_execution",
            request_id=request.request_id,
            model_version="AdInsight-RL",
            prompt_version="video-agent-v1",
        )
        executions.update(
            "exec_execution",
            status="running",
            current_stage="insights",
        )
        executions.sync_tool_calls(
            "exec_execution",
            [
                {
                    "call_id": "call_001",
                    "tool": "insights",
                    "status": "completed",
                    "arguments": {"force": False},
                    "observation": {"status": "ok", "insight_count": 2},
                    "duration_seconds": 1.25,
                    "started_at": "2026-09-13T00:00:00Z",
                    "completed_at": "2026-09-13T00:00:01Z",
                }
            ],
        )
        executions.update("exec_execution", status="completed", run_id="run_execution")

        execution = executions.get("exec_execution")
        calls = executions.tool_calls("exec_execution")
        assert execution.status == "completed"
        assert execution.run_id == "run_execution"
        assert len(calls) == 1
        assert calls[0].tool_name == "insights"
        assert calls[0].duration_ms == 1250
        assert calls[0].output_summary_json == {"status": "ok", "insight_count": 2}
    finally:
        database.close()
