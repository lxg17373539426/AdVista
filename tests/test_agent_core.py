import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

from ad_vista_agent.agent.core import resume_agent, run_agent
from ad_vista_agent.agent.planner import validate_plan
from ad_vista_agent.agent.stage_tools import StageTool
from ad_vista_agent.config import DEFAULT_CONFIG, load_settings
from ad_vista_agent.schemas import AgentRequest, AnalysisPlan, PlanStep, ToolCallStatus
from ad_vista_agent.tools import Tool, ToolContext, ToolRegistry


class FakeTool(Tool):
    input_schema = {"type": "object"}
    output_schema = {"type": "object"}

    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self.description = name
        self.calls = calls
        self.fail = fail

    def run(self, context: ToolContext, arguments: dict[str, object]) -> dict[str, object]:
        del context, arguments
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError("fake failure")
        return {"status": "ok", "run_id": "run", "run_dir": "/tmp/run", "cache_hit": False}


class AgentCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "ad.mp4"
        self.video.write_bytes(b"video")
        settings = load_settings(DEFAULT_CONFIG)
        self.settings = settings.model_copy(
            update={"paths": settings.paths.model_copy(update={"output_root": self.root})}
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def registry(self, calls: list[str], fail: str | None = None) -> ToolRegistry:
        registry = ToolRegistry()
        for name in ("ingest", "timeline", "speech", "ocr", "ledger", "insights", "report", "creative"):
            registry.register(FakeTool(name, calls, fail=name == fail))
        return registry

    def plan(self, *, report: bool = True, budget: int = 12) -> AnalysisPlan:
        names = ["ingest", "timeline", "speech", "ocr", "ledger", "insights"]
        if report:
            names.append("report")
        dependencies = {
            "timeline": ["ingest"],
            "speech": ["timeline"],
            "ocr": ["timeline"],
            "ledger": ["speech", "ocr"],
            "insights": ["ledger"],
            "report": ["insights"],
        }
        return AnalysisPlan(
            goal="分析广告卖点",
            steps=[
                PlanStep(step_id=f"step_{i}", tool=name, purpose=name, depends_on=dependencies.get(name, []))
                for i, name in enumerate(names)
            ],
            deliverables=["report"] if report else ["insights"],
            max_tool_calls=budget,
        )

    def run_core(self, request: AgentRequest, plan: AnalysisPlan, registry: ToolRegistry):
        run_dir = self.root / "runs" / "ingest_test"
        run_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "ad_vista_agent.ingestion.ingest_video",
            return_value={
                "status": "ok",
                "cache_hit": True,
                "run_id": "ingest_test",
                "run_dir": str(run_dir),
            },
        ):
            return run_agent(
                self.video,
                self.settings,
                request,
                plan=plan,
                registry=registry,
            )

    def test_goal_plan_can_stop_before_report(self) -> None:
        from ad_vista_agent.agent.planner import rule_plan

        registry = self.registry([])
        plan = rule_plan(AgentRequest(goal="只提取广告字幕和语音证据", deliverables=["evidence"]))
        validated = validate_plan(plan, registry, AgentRequest(goal="只提取广告字幕和语音证据", deliverables=["evidence"]))
        self.assertEqual(validated.steps[-1].tool, "ledger")
        self.assertNotIn("insights", [step.tool for step in validated.steps])

    def test_creative_goal_compiles_insights_dependency(self) -> None:
        from ad_vista_agent.agent.planner import rule_plan

        request = AgentRequest(goal="生成广告脚本、分镜和 A/B 方案", deliverables=["creative"])
        plan = validate_plan(rule_plan(request), self.registry([]), request)
        self.assertEqual(plan.steps[-1].tool, "creative")
        self.assertEqual([step.tool for step in plan.steps][-2], "insights")

    def test_all_deliverable_combinations_compile_required_tools(self) -> None:
        from ad_vista_agent.agent.planner import rule_plan

        deliverables = ["evidence", "insights", "risk_audit", "report", "creative"]
        required = {
            "evidence": "ledger",
            "insights": "insights",
            "risk_audit": "report",
            "report": "report",
            "creative": "creative",
        }
        for size in range(1, len(deliverables) + 1):
            for selected in combinations(deliverables, size):
                request = AgentRequest(goal="组合交付", deliverables=list(selected))
                plan = validate_plan(rule_plan(request), self.registry([]), request)
                tools = {step.tool for step in plan.steps}
                for deliverable in selected:
                    self.assertIn(required[deliverable], tools, selected)

    def test_explicit_deliverables_discard_planner_extra_tools(self) -> None:
        from ad_vista_agent.agent.planner import PlannerDecision, compile_decision

        request = AgentRequest(goal="只生成洞察", deliverables=["insights"])
        decision = PlannerDecision(
            goal=request.goal,
            selected_tools=["insights", "report", "creative"],
            deliverables=["insights", "report", "creative"],
        )
        plan = compile_decision(decision, request)
        self.assertEqual(plan.deliverables, ["insights"])
        self.assertEqual(plan.steps[-1].tool, "insights")
        self.assertNotIn("report", [step.tool for step in plan.steps])
        self.assertNotIn("creative", [step.tool for step in plan.steps])

    def test_unknown_tool_plan_is_rejected(self) -> None:
        request = AgentRequest(goal="分析广告卖点")
        plan = self.plan()
        plan.steps[1].tool = "shell"
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            validate_plan(plan, self.registry([]), request)

    def test_plan_over_budget_is_rejected(self) -> None:
        request = AgentRequest(goal="分析广告卖点", max_tool_calls=4)
        with self.assertRaisesRegex(ValueError, "exceeds the tool-call budget"):
            validate_plan(self.plan(), self.registry([]), request)

    def test_agent_records_tool_observations_and_waits_for_report_review(self) -> None:
        calls: list[str] = []
        registry = self.registry(calls)
        plan = self.plan()
        result = self.run_core(AgentRequest(goal="分析广告卖点"), plan, registry)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["ingest", "timeline", "speech", "ocr", "ledger", "insights", "report"])
        self.assertEqual(len(result["tool_calls"]), 7)
        self.assertTrue(all(call["status"] == ToolCallStatus.COMPLETED for call in result["tool_calls"]))
        self.assertEqual(result["reflections"][0]["decision"], "deliver")

    def test_agent_persists_failure(self) -> None:
        calls: list[str] = []
        with self.assertRaisesRegex(RuntimeError, "Tool ocr failed"):
            self.run_core(
                AgentRequest(goal="分析广告卖点"),
                self.plan(),
                self.registry(calls, fail="ocr"),
            )
        self.assertEqual(calls, ["ingest", "timeline", "speech", "ocr"])

    def test_report_review_stops_before_creative_and_approval_resumes(self) -> None:
        calls: list[str] = []

        class ReviewTool(FakeTool):
            def run(self, context: ToolContext, arguments: dict[str, object]) -> dict[str, object]:
                output = super().run(context, arguments)
                output["audit_status"] = "review"
                output["html"] = "/tmp/report.html"
                return output

        registry = self.registry(calls)
        registry._tools["report"] = ReviewTool("report", calls)
        request = AgentRequest(
            goal="生成报告和创意",
            deliverables=["report", "creative"],
        )
        names = ["ingest", "timeline", "speech", "ocr", "ledger", "insights", "report", "creative"]
        dependencies = {
            "timeline": ["ingest"],
            "speech": ["timeline"],
            "ocr": ["timeline"],
            "ledger": ["speech", "ocr"],
            "insights": ["ledger"],
            "report": ["insights"],
            "creative": ["insights"],
        }
        plan = AnalysisPlan(
            goal=request.goal,
            steps=[
                PlanStep(
                    step_id=f"step_{index}",
                    tool=name,
                    purpose=name,
                    depends_on=dependencies.get(name, []),
                )
                for index, name in enumerate(names)
            ],
            deliverables=request.deliverables,
        )
        waiting = self.run_core(request, plan, registry)
        self.assertEqual(waiting["status"], "waiting_confirmation")
        self.assertEqual(calls[-1], "report")
        self.assertNotIn("creative", calls)
        resumed = resume_agent(
            waiting["execution_id"],
            self.settings,
            approve=True,
            registry=registry,
        )
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(calls[-1], "creative")
        self.assertEqual(resumed["approved_confirmations"], ["report_review"])

    def test_same_video_requests_create_distinct_executions_and_conversations(self) -> None:
        risk_plan = self.plan()
        risk_plan.goal = "分析合规风险"
        first = self.run_core(
            AgentRequest(goal="分析广告卖点"),
            self.plan(),
            self.registry([]),
        )
        second = self.run_core(
            AgentRequest(goal="分析合规风险"),
            risk_plan,
            self.registry([]),
        )
        self.assertNotEqual(first["execution_id"], second["execution_id"])
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(first["request"]["goal"], "分析广告卖点")
        self.assertEqual(second["request"]["goal"], "分析合规风险")
        for result in (first, second):
            session = self.root / "executions" / result["execution_id"] / "session.json"
            self.assertTrue(session.is_file())

    def test_same_asset_tool_execution_is_serialized(self) -> None:
        active = 0
        maximum = 0
        guard = threading.Lock()

        class BlockingTool(FakeTool):
            def run(self, context: ToolContext, arguments: dict[str, object]) -> dict[str, object]:
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    time.sleep(0.05)
                    return super().run(context, arguments)
                finally:
                    with guard:
                        active -= 1

        registry = ToolRegistry()
        registry.register(BlockingTool("ingest", []))
        request = AgentRequest(goal="登记视频", max_tool_calls=1)
        plan = AnalysisPlan(
            goal=request.goal,
            steps=[PlanStep(step_id="step_1", tool="ingest", purpose="登记")],
            deliverables=[],
            max_tool_calls=1,
        )
        run_dir = self.root / "runs" / "ingest_test"
        run_dir.mkdir(parents=True, exist_ok=True)
        ingestion = {
            "status": "ok",
            "cache_hit": True,
            "run_id": "ingest_test",
            "run_dir": str(run_dir),
        }

        def execute(_: int) -> dict[str, object]:
            return run_agent(
                self.video,
                self.settings,
                request,
                plan=plan,
                registry=registry,
            )

        with patch("ad_vista_agent.ingestion.ingest_video", return_value=ingestion):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(execute, range(2)))

        self.assertEqual(maximum, 1)
        self.assertNotEqual(results[0]["execution_id"], results[1]["execution_id"])

    def test_cancelled_execution_stops_before_first_tool(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        calls: list[str] = []
        run_dir = self.root / "runs" / "ingest_test"
        run_dir.mkdir(parents=True, exist_ok=True)
        with patch(
            "ad_vista_agent.ingestion.ingest_video",
            return_value={
                "status": "ok",
                "cache_hit": True,
                "run_id": "ingest_test",
                "run_dir": str(run_dir),
            },
        ):
            result = run_agent(
                self.video,
                self.settings,
                AgentRequest(goal="分析广告卖点"),
                plan=self.plan(),
                registry=self.registry(calls),
                cancel_event=cancel_event,
            )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(calls, [])

    def test_stage_tool_propagates_request_and_execution_artifact_root(self) -> None:
        captured: dict[str, object] = {}

        def builder(path, settings, force, request, artifact_root):
            del settings
            captured.update(
                path=path,
                force=force,
                request=request,
                artifact_root=artifact_root,
            )
            return {"status": "ok", "run_id": "run", "run_dir": "/tmp/run"}

        request = AgentRequest(
            goal="重点分析转化路径",
            mode="deep",
            deliverables=["insights"],
        )
        execution_dir = self.root / "executions" / ("exec_" + "a" * 32)
        tool = StageTool("insights", "insights", builder, self.settings)
        tool.run(
            ToolContext(
                run_id="ingest_test",
                run_dir=self.root / "runs" / "ingest_test",
                source_path=self.video,
                execution_dir=execution_dir,
                request=request,
            ),
            {"force": True},
        )
        self.assertEqual(captured["request"], request)
        self.assertEqual(captured["artifact_root"], execution_dir / "artifacts")
        self.assertTrue(captured["force"])


if __name__ == "__main__":
    unittest.main()
