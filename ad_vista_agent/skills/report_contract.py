from __future__ import annotations

import hashlib
from importlib.resources import files

from .frontmatter import frontmatter_field, strip_frontmatter

REPORT_FRONTEND_SKILL = "report-frontend-v1"


def _skill_text() -> str:
    return files("ad_vista_agent.skills.report_frontend").joinpath("SKILL.md").read_text(
        encoding="utf-8"
    )


def report_frontend_contract() -> str:
    """Return the human-readable body of the report frontend skill.

    The YAML frontmatter is metadata used for skill discovery and validation;
    only the body is injected into prompts or embedded in report artifacts.
    """
    return strip_frontmatter(_skill_text())


def report_frontend_summary() -> str:
    """Return a short machine-safe summary for the report ``<meta>`` tag."""
    text = _skill_text()
    summary = frontmatter_field(text, "description")
    if summary:
        return " ".join(summary.split())
    return REPORT_FRONTEND_SKILL


def report_frontend_skill_fingerprint() -> str:
    content = _skill_text().encode("utf-8")
    return hashlib.sha256(content).hexdigest()
