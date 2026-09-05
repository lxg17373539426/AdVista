from __future__ import annotations

import hashlib
from importlib.resources import files


REPORT_FRONTEND_SKILL = "report-frontend-v1"


def report_frontend_skill_fingerprint() -> str:
    content = files("ad_vista_agent.skills.report_frontend").joinpath("SKILL.md").read_bytes()
    return hashlib.sha256(content).hexdigest()
