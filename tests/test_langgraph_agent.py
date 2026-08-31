from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from ad_vista_agent.agent.langgraph_backend import run_langgraph_agent
from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.agent.stage_tools import build_agent_registry
from ad_vista_agent.config import load_settings
from ad_vista_agent.schemas import AgentRequest


class ToolCallingFakeModel:
    def __init__(self, messages: list[AIMessage]) -> None:
        self.messages = iter(messages)

    def bind_tools(self, tools, **kwargs):
        del tools, kwargs
        return RunnableLambda(lambda messages: next(self.messages))


def test_langgraph_react_executes_required_tools_in_order(monkeypatch) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "video.mp4"
        video.write_bytes(b"video")
        settings = load_settings().model_copy(
            update={"paths": load_settings().paths.model_copy(update={"output_root": root / "outputs"})}
        )
        request = AgentRequest(goal="只登记视频")
        plan = rule_plan(request).model_copy(
            update={"steps": rule_plan(request).steps[:1], "deliverables": [], "response_mode": "answer"}
        )
        run_dir = root / "outputs" / "runs" / "ingest_test"
        run_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "ad_vista_agent.agent.langgraph_backend.ingest_video",
            lambda path, loaded: {
                "status": "ok",
                "run_id": "ingest_test",
                "run_dir": str(run_dir),
                "asset": {"asset_id": "asset_test"},
            },
        )
        registry = build_agent_registry(settings)
        tool = registry.get("ingest")
        tool.run = lambda context, arguments: {
            "status": "ok", "run_id": context.run_id, "run_dir": str(context.run_dir)
        }
        model = ToolCallingFakeModel(
            messages=[
                AIMessage(content="", tool_calls=[{"name": "ingest", "args": {}, "id": "call_1"}]),
            ]
        )
        result = run_langgraph_agent(
            video,
            settings,
            request,
            registry=registry,
            plan=plan,
            chat_model=model,
        )
        assert result["status"] == "completed"
        assert [item["tool"] for item in result["tool_calls"]] == ["ingest"]


def test_langgraph_rejects_out_of_order_tool(monkeypatch) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "video.mp4"
        video.write_bytes(b"video")
        base = load_settings()
        settings = base.model_copy(
            update={"paths": base.paths.model_copy(update={"output_root": root / "outputs"})}
        )
        request = AgentRequest(goal="生成证据", deliverables=["evidence"])
        run_dir = root / "outputs" / "runs" / "ingest_test"
        run_dir.mkdir(parents=True)
        monkeypatch.setattr(
            "ad_vista_agent.agent.langgraph_backend.ingest_video",
            lambda path, loaded: {
                "status": "ok", "run_id": "ingest_test", "run_dir": str(run_dir),
                "asset": {"asset_id": "asset_test"},
            },
        )
        model = ToolCallingFakeModel(
            [AIMessage(content="", tool_calls=[{"name": "ledger", "args": {}, "id": "bad"}])]
        )
        try:
            run_langgraph_agent(
                video,
                settings,
                request,
                registry=build_agent_registry(settings),
                chat_model=model,
            )
        except ValueError as exc:
            assert "out-of-order" in str(exc)
        else:
            raise AssertionError("out-of-order tool call was accepted")
