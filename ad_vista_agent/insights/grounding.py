from __future__ import annotations

from collections import Counter
import re

from ad_vista_agent.schemas import MarketingAnalysis, MarketingDimension


SUMMARY_ORDER = (
    MarketingDimension.CREATIVE_STRUCTURE,
    MarketingDimension.PAIN_POINT,
    MarketingDimension.SELLING_POINT,
    MarketingDimension.CONVERSION_PATH,
)


def grounded_executive_summary(analysis: MarketingAnalysis) -> str:
    claims: list[str] = []
    for dimension in SUMMARY_ORDER:
        item = next((value for value in analysis.insights if value.dimension == dimension), None)
        if item is not None:
            claims.append(item.claim.rstrip("。") + "。")
    if not claims:
        return "当前 Evidence Ledger 未形成可用的广告洞察摘要。"
    return "基于当前 Evidence Ledger：" + "".join(claims)


def validate_analysis_grounding(
    analysis: MarketingAnalysis,
    *,
    asset_id: str,
    allowed_references: set[str],
    max_per_dimension: int,
) -> None:
    if analysis.asset_id != asset_id:
        raise ValueError("Marketing analysis belongs to another asset")
    insight_ids = [item.insight_id for item in analysis.insights]
    if len(insight_ids) != len(set(insight_ids)):
        raise ValueError("Marketing insight IDs must be unique")
    counts = Counter(item.dimension for item in analysis.insights)
    excessive = [dimension.value for dimension, count in counts.items() if count > max_per_dimension]
    if excessive:
        raise ValueError(f"Too many insights for dimensions: {sorted(excessive)}")
    for item in analysis.insights:
        unknown = set(item.evidence_refs) - allowed_references
        if unknown:
            raise ValueError(
                f"Insight {item.insight_id} cites unknown evidence: {sorted(unknown)}"
            )
        mentioned = set(
            re.findall(
                r"(?:speech_\d{4}|ocr_cluster_\d{4})",
                item.claim + " " + item.reasoning_summary,
            )
        )
        if mentioned:
            raise ValueError(
                f"Insight {item.insight_id} writes citation IDs outside evidence_refs: {sorted(mentioned)}"
            )
    if any(not value.startswith("当前 Ledger 无证据表明") for value in analysis.unknowns):
        raise ValueError("Each unknown must state that the current Ledger lacks evidence")
