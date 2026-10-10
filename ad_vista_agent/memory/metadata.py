from __future__ import annotations

from typing import Any


def answer_metadata(answer: dict[str, Any]) -> dict[str, Any]:
    """Restore presentation fields without replacing the stored message identity."""
    fields = (
        "epistemic_status", "unsupported_points", "report_url", "report_label",
        "context_version", "version_id",
    )
    return {key: answer[key] for key in fields if key in answer}
