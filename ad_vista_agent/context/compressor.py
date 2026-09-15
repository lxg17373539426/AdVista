from __future__ import annotations

import re
from typing import Any

from .budget import ContextBudget, estimate_tokens


def _terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return terms


def _rank(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    return sorted(
        rows,
        key=lambda row: (
            len(query_terms.intersection(_terms(str(row.get("content") or row.get("claim") or "")))),
            -int(row.get("start_ms") or 0),
        ),
        reverse=True,
    )


def compress_evidence_context(
    context: dict[str, Any],
    query: str,
    budget: ContextBudget,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = estimate_tokens(context)
    if before <= budget.soft_limit_tokens:
        return context, {"strategy": "full", "estimated_tokens": before, "omitted": {}}

    compressed = dict(context)
    ledger_key = next(
        (key for key in ("ledger", "evidence_ledger") if isinstance(compressed.get(key), dict)),
        None,
    )
    evidence_context = dict(compressed[ledger_key]) if ledger_key is not None else compressed
    omitted: dict[str, int] = {}
    limits = {
        "speech_evidence": 40,
        "ocr_clusters": 80,
        "ocr_candidates": 30,
        "visual_keyframes": 32,
        "visual_observations": 32,
    }
    for key, limit in limits.items():
        rows = evidence_context.get(key)
        if isinstance(rows, list) and len(rows) > limit:
            ranked = _rank([row for row in rows if isinstance(row, dict)], query)
            evidence_context[key] = ranked[:limit]
            omitted[key] = len(rows) - len(evidence_context[key])

    citation_rules = evidence_context.get("citation_rules")
    if isinstance(citation_rules, dict):
        citation_rules = dict(citation_rules)
        speech = evidence_context.get("speech_evidence", [])
        clusters = evidence_context.get("ocr_clusters", [])
        citation_rules["allowed_speech_ids"] = [
            row["evidence_id"] for row in speech
            if isinstance(row, dict) and isinstance(row.get("evidence_id"), str)
        ]
        citation_rules["allowed_ocr_cluster_ids"] = [
            row["cluster_id"] for row in clusters
            if isinstance(row, dict) and isinstance(row.get("cluster_id"), str)
        ]
        evidence_context["citation_rules"] = citation_rules

    if ledger_key is not None:
        compressed[ledger_key] = evidence_context

    rows = compressed.get("visual_observations")
    if isinstance(rows, list) and len(rows) > limits["visual_observations"]:
        compressed["visual_observations"] = _rank(
            [row for row in rows if isinstance(row, dict)], query
        )[:limits["visual_observations"]]
        omitted["visual_observations"] = len(rows) - len(compressed["visual_observations"])

    after = estimate_tokens(compressed)
    if after > budget.hard_limit_tokens:
        raise ValueError(
            f"Compressed context exceeds safe model input budget: {after} > {budget.hard_limit_tokens}"
        )
    compressed["context_compression"] = {
        "strategy": "evidence_relevance_v1",
        "estimated_tokens_before": before,
        "estimated_tokens_after": after,
        "omitted": omitted,
    }
    return compressed, compressed["context_compression"]
