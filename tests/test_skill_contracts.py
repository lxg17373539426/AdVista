from __future__ import annotations

from pathlib import Path

from ad_vista_agent.skills import (
    TASK_SKILLS,
    report_frontend_contract,
    report_frontend_summary,
    split_frontmatter,
    strip_frontmatter,
    task_skill_prompt,
)

SKILLS_DIR = Path(__file__).resolve().parents[1] / "ad_vista_agent" / "skills"
SKILL_DIRS = (
    "selling_points",
    "analysis_report",
    "marketing_strategy",
    "creative_generation",
    "report_frontend",
)


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def test_every_skill_has_valid_frontmatter() -> None:
    for name in SKILL_DIRS:
        meta, body = split_frontmatter(_skill_text(name))
        assert meta, f"{name} is missing YAML frontmatter"
        assert isinstance(meta.get("name"), str) and meta["name"].strip()
        description = meta.get("description", "")
        assert isinstance(description, str)
        # Descriptions drive skill routing; they must state what and when.
        assert len(description) >= 40, f"{name} description is too short to route on"
        assert "当" in description or "使用" in description, f"{name} description lacks a trigger"
        assert body.strip(), f"{name} body is empty"


def test_frontmatter_is_stripped_from_injected_prompts() -> None:
    for deliverable in TASK_SKILLS:
        prompt = task_skill_prompt(deliverable)
        assert not prompt.lstrip().startswith("---")
        assert "description:" not in prompt.splitlines()[0]


def test_task_prompts_carry_structured_sections() -> None:
    report = task_skill_prompt("report")
    assert "Selling Points Skill" in report
    assert "Analysis Report Skill" in report
    for anchor in ("## Output contract", "## Anti-patterns", "## Self-check"):
        assert anchor in report

    strategy = task_skill_prompt("strategy")
    assert "Marketing Strategy Skill" in strategy
    assert "## Self-check" in strategy

    creative = task_skill_prompt("creative")
    assert "Creative Generation Skill" in creative
    assert "## Output contract" in creative


def test_report_frontend_contract_excludes_frontmatter_but_keeps_rules() -> None:
    body = report_frontend_contract()
    assert not body.lstrip().startswith("---")
    assert "minimal, light" in body
    assert "## Rendering Rules" in body
    # The one-line summary must be machine-safe (single line) for the meta tag.
    summary = report_frontend_summary()
    assert "\n" not in summary
    assert summary.strip()


def test_strip_frontmatter_is_noop_without_frontmatter() -> None:
    plain = "# Title\n\nBody only."
    assert strip_frontmatter(plain) == plain
