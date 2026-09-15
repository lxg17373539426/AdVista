from pathlib import Path

from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.schemas import AgentRequest
from ad_vista_agent.skills import task_skill_fingerprint, task_skill_prompt


def test_strategy_is_a_first_class_task_with_insight_dependency() -> None:
    plan = rule_plan(AgentRequest(goal="请制定营销策略", deliverables=["strategy"]))
    assert plan.deliverables == ["strategy"]
    assert [step.tool for step in plan.steps] == [
        "ingest",
        "timeline",
        "speech",
        "ocr",
        "ledger",
        "insights",
        "strategy",
    ]


def test_task_skills_are_runtime_loadable_and_distinct() -> None:
    report_prompt = task_skill_prompt("report")
    strategy_prompt = task_skill_prompt("strategy")
    creative_prompt = task_skill_prompt("creative")
    assert "Analysis Report Skill" in report_prompt
    assert "Marketing Strategy Skill" in strategy_prompt
    assert "Creative Generation Skill" in creative_prompt
    assert len({task_skill_fingerprint(name) for name in ("report", "strategy", "creative")}) == 3


def test_strategy_observation_is_published_as_a_deliverable() -> None:
    from ad_vista_agent.agent.core import _observation

    assert _observation({"status": "ok", "strategy": "/tmp/strategy.json"}) == {
        "status": "ok",
        "strategy": "/tmp/strategy.json",
    }
