from __future__ import annotations

import hashlib
from importlib.resources import files


TASK_SKILLS = {
    "report": ("selling_points", "analysis_report"),
    "strategy": ("selling_points", "marketing_strategy"),
    "creative": ("selling_points", "creative_generation"),
}


def task_skill_prompt(deliverable: str) -> str:
    names = TASK_SKILLS.get(deliverable)
    if names is None:
        raise ValueError(f"Unknown task skill: {deliverable}")
    return "\n\n".join(
        files(f"ad_vista_agent.skills.{name}").joinpath("SKILL.md").read_text(encoding="utf-8")
        for name in names
    )


def task_skill_fingerprint(deliverable: str) -> str:
    return hashlib.sha256(task_skill_prompt(deliverable).encode("utf-8")).hexdigest()
