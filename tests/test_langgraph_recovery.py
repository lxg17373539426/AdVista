from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from ad_vista_agent.agent.langgraph_backend import run_langgraph_agent
from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.agent.stage_tools import build_agent_registry
from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import AgentRequest, AgentRunStatus, ToolCallRecord, ToolCallStatus


class OneResponseModel:
    def bind_tools(self, tools, **kwargs):
        del tools, kwargs
        return RunnableLambda(
            lambda messages: AIMessage(
                content="",
                tool_calls=[{"name": "ingest", "args": {}, "id": "call_1"}],
            )
        )


def test_completed_tool_is_not_repeated_after_stale_graph_snapshot() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "video.mp4"
        video.write_bytes(b"video")
        base = load_settings()
        settings = base.model_copy(
            update={"paths": base.paths.model_copy(update={"output_root": root / "outputs"})}
        )
        output = settings.paths.output_root
        run_dir = output / "runs" / "ingest_test"
        run_dir.mkdir(parents=True)
        execution_id = "exec_" + "a" * 32
        state_dir = ArtifactStore(output).prepare_execution(execution_id)
        request = AgentRequest(goal="只登记视频")
        plan = rule_plan(request).model_copy(
            update={"steps": rule_plan(request).steps[:1], "deliverables": [], "response_mode": "answer"}
        )
        from ad_vista_agent.schemas import AgentSessionState

        session = AgentSessionState(
            execution_id=execution_id,
            asset_id="asset_test",
            session_id="conversation_test",
            run_id="ingest_test",
            source_path=video,
            request=request,
            plan=plan,
            status=AgentRunStatus.RUNNING,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            tool_calls=[
                ToolCallRecord(
                    call_id="call_001",
                    tool="ingest",
                    arguments={"force": False},
                    status=ToolCallStatus.COMPLETED,
                    started_at="2026-01-01T00:00:00Z",
                    completed_at="2026-01-01T00:00:01Z",
                    observation={"status": "ok", "run_id": "ingest_test", "run_dir": str(run_dir)},
                )
            ],
        )
        from ad_vista_agent.agent.core import _write

        store = ArtifactStore(output)
        _write(store, state_dir, run_dir, session)
        store.write_json(
            state_dir / "graph_state.json",
            {
                "messages": [
                    {
                        "type": "ai",
                        "data": {
                            "content": "",
                            "tool_calls": [{"name": "ingest", "args": {}, "id": "call_1", "type": "tool_call"}],
                            "invalid_tool_calls": [],
                            "additional_kwargs": {},
                            "response_metadata": {},
                        },
                    }
                ],
                "completed_tools": [],
                "observations": [],
                "tool_calls": [],
                "status": None,
                "error": None,
                "final_answer": None,
            },
        )
        calls: list[str] = []
        registry = build_agent_registry(settings)
        registry.get("ingest").run = lambda context, arguments: calls.append("ingest") or {
            "status": "ok", "run_id": context.run_id, "run_dir": str(context.run_dir)
        }

        result = run_langgraph_agent(
            video,
            settings,
            request,
            plan=plan,
            registry=registry,
            execution_id=execution_id,
            existing_state=session,
            chat_model=OneResponseModel(),
        )

        assert result["status"] == "completed"
        assert calls == []
