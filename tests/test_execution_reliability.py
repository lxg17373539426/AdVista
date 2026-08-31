from pathlib import Path
from tempfile import TemporaryDirectory

from ad_vista_agent.agent.core import _execute_state, _make_state
from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.agent.stage_tools import build_agent_registry
from ad_vista_agent.agent.orchestrator import PIPELINE_STAGE_NAMES, run_pipeline
from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import AgentRequest, StepStatus
from ad_vista_agent.tools.qwen import QwenInsightTool
from ad_vista_agent.tools import ToolContext


def _settings(root: Path):
    base = load_settings()
    return base.model_copy(
        update={"paths": base.paths.model_copy(update={"output_root": root / "outputs"})}
    )


def test_langgraph_rejects_plan_that_exceeds_tool_budget(monkeypatch) -> None:
    from ad_vista_agent.agent.langgraph_backend import run_langgraph_agent

    with TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "video.mp4"
        video.write_bytes(b"video")
        settings = _settings(root)
        request = AgentRequest(goal="生成完整报告", deliverables=["report"], max_tool_calls=1)
        monkeypatch.setattr(
            "ad_vista_agent.agent.langgraph_backend.ingest_video",
            lambda path, loaded: {
                "status": "ok",
                "run_id": "ingest_test",
                "run_dir": str(root / "outputs" / "runs" / "ingest_test"),
                "asset": {"asset_id": "asset_test"},
            },
        )

        try:
            run_langgraph_agent(
                video,
                settings,
                request,
                registry=build_agent_registry(settings),
            )
        except ValueError as exc:
            assert "tool-call budget" in str(exc)
        else:
            raise AssertionError("an over-budget plan was accepted")


def test_legacy_force_tool_reexecutes_completed_step() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = _settings(root)
        request = AgentRequest(goal="只登记视频")
        plan = rule_plan(request).model_copy(
            update={"steps": rule_plan(request).steps[:1], "deliverables": [], "response_mode": "answer"}
        )
        state = _make_state(
            "ingest_test",
            "asset_test",
            "exec_" + "a" * 32,
            root / "video.mp4",
            request,
            plan,
        )
        assert state.execution_id is not None
        state_dir = ArtifactStore(settings.paths.output_root).prepare_execution(state.execution_id)
        run_dir = ArtifactStore(settings.paths.output_root).prepare("ingest_test")
        calls: list[dict[str, object]] = []
        registry = build_agent_registry(settings)
        tool = registry.get("ingest")
        tool.run = lambda context, arguments: calls.append(arguments) or {
            "status": "ok", "run_id": context.run_id, "run_dir": str(context.run_dir)
        }

        _execute_state(
            state,
            state_dir,
            run_dir,
            settings,
            registry,
            force_tools={"ingest"},
        )

        assert len(calls) == 1
        assert calls[0]["force"] is True
        assert state.plan.steps[0].status == StepStatus.COMPLETED


def test_qwen_subprocess_paths_are_unique() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        context_dir = root / "run"
        context_dir.mkdir()
        tool = QwenInsightTool(
            Path("/usr/bin/python3"),
            10,
            "0",
        )
        captured: list[Path] = []

        def fake_run(command, **kwargs):
            request = Path(command[-1])
            captured.append(request)
            raise RuntimeError("stop before worker")

        import ad_vista_agent.tools.qwen as qwen

        original = qwen.run_process
        qwen.run_process = fake_run
        try:
            for _ in range(2):
                try:
                    tool._run_subprocess(
                        ToolContext(run_id="run", run_dir=context_dir),
                        {
                            "model_path": "model",
                            "messages": [],
                            "temperature": 0,
                            "max_tokens": 10,
                            "output_schema": {},
                        },
                    )
                except RuntimeError:
                    pass
        finally:
            qwen.run_process = original

        assert len(captured) == 2
        assert captured[0] != captured[1]


def test_pipeline_from_stage_forces_target_and_downstream_stages() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        video = root / "video.mp4"
        video.write_bytes(b"video")
        settings = _settings(root)
        calls: list[tuple[str, bool]] = []

        def runner(name: str):
            def execute(path, loaded, force):
                calls.append((name, force))
                run_dir = settings.paths.output_root / "runs" / "ingest_test"
                run_dir.mkdir(parents=True, exist_ok=True)
                result = {
                    "status": "ok",
                    "run_id": "ingest_test",
                    "run_dir": str(run_dir),
                    "cache_hit": not force,
                }
                if name == "report":
                    result.update(
                        {
                            "audit_status": "pass",
                            "markdown": str(run_dir / "report.md"),
                            "html": str(run_dir / "report.html"),
                        }
                    )
                return result

            return execute

        runners = {name: runner(name) for name in PIPELINE_STAGE_NAMES}
        run_pipeline(video, settings, config_path=root / "config.yaml", stage_runners=runners)
        calls.clear()

        run_pipeline(
            video,
            settings,
            config_path=root / "config.yaml",
            from_stage="speech",
            stage_runners=runners,
        )

        assert calls == [
            ("ingest", False),
            ("speech", True),
            ("ocr", True),
            ("ledger", True),
            ("insights", True),
            ("report", True),
        ]
