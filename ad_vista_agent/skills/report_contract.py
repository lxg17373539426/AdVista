from __future__ import annotations

import hashlib
from importlib.resources import files


REPORT_FRONTEND_SKILL = "report-frontend-v1"


def report_frontend_contract() -> str:
    return files("ad_vista_agent.skills.report_frontend").joinpath("SKILL.md").read_text(
        encoding="utf-8"
    )


def report_frontend_skill_fingerprint() -> str:
    content = report_frontend_contract().encode("utf-8")
    return hashlib.sha256(content).hexdigest()
