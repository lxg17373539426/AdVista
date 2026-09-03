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
INLINE_CITATION_PATTERN = re.compile(r"(?:speech_\d{4}|ocr_cluster_\d{4}|kf_[0-9a-z_]+)")


def normalize_inline_citations(analysis: MarketingAnalysis) -> MarketingAnalysis:
    """Keep citation IDs in evidence_refs without discarding otherwise valid insights."""
    normalized = []
    for item in analysis.insights:
        claim = INLINE_CITATION_PATTERN.sub("", item.claim)
        reasoning = INLINE_CITATION_PATTERN.sub("", item.reasoning_summary)
        for opening, closing in (("（", "）"), ("(", ")"), ("[", "]")):
            claim = claim.replace(opening + closing, "")
            reasoning = reasoning.replace(opening + closing, "")
        normalized.append(
            item.model_copy(
                update={
                    "claim": re.sub(r"[ \t]{2,}", " ", claim).strip(),
                    "reasoning_summary": re.sub(r"[ \t]{2,}", " ", reasoning).strip(),
                }
            )
        )
    return analysis.model_copy(update={"insights": normalized})


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
    reference_content: dict[str, str] | None = None,
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
        if item.dimension == MarketingDimension.AUDIENCE and any(
            term in (item.claim + " " + item.reasoning_summary)
            for term in ("真实性别", "跨性别", "性别流动")
        ):
            raise ValueError(
                f"Insight {item.insight_id} infers a person's gender identity from advertising evidence"
            )
        unknown = set(item.evidence_refs) - allowed_references
        if unknown:
            raise ValueError(
                f"Insight {item.insight_id} cites unknown evidence: {sorted(unknown)}"
            )
        if not _claim_has_source_overlap(
            item.claim,
            item.evidence_refs,
            allowed_references,
            reference_content or {},
        ):
            raise ValueError(
                f"Insight {item.insight_id} cites evidence with no obvious lexical support"
            )
        mentioned = set(INLINE_CITATION_PATTERN.findall(item.claim + " " + item.reasoning_summary))
        if mentioned:
            raise ValueError(
                f"Insight {item.insight_id} writes citation IDs outside evidence_refs: {sorted(mentioned)}"
            )
    if any(not value.startswith("当前 Ledger 无证据表明") for value in analysis.unknowns):
        raise ValueError("Each unknown must state that the current Ledger lacks evidence")


def _claim_terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {term for term in terms if term not in {"当前", "视频", "广告", "画面", "展示"}}


def _claim_has_source_overlap(
    claim: str,
    references: list[str],
    allowed_references: set[str],
    reference_content: dict[str, str],
) -> bool:
    if any(reference.startswith("kf_") for reference in references):
        return True
    claim_terms = _claim_terms(claim)
    if not claim_terms:
        return True
    source_terms: set[str] = set()
    for reference in references:
        if reference not in allowed_references:
            continue
        source_terms.update(_claim_terms(reference_content.get(reference, "")))
    # This is a conservative contradiction guard, not a full entailment proof.
    # Abstract marketing inferences may use different wording from their sources.
    if claim_terms.intersection(source_terms):
        return True
    # A translated claim can be grounded in a speech transcript even when the
    # transcript and output use different languages. OCR-only references still
    # require visible lexical support so noise such as "37" cannot validate a
    # made-up product or benefit.
    return any(
        reference.startswith("speech_") and reference_content.get(reference, "").strip()
        for reference in references
    )
