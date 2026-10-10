from ad_vista_agent.insights.grounding import (
    normalize_inline_citations,
    normalize_ocr_cluster_references,
)
from ad_vista_agent.schemas import MarketingAnalysis


def test_inline_citation_ids_are_moved_out_of_user_facing_text() -> None:
    analysis = MarketingAnalysis.model_validate(
        {
            "asset_id": "asset_1",
            "language": "zh",
            "subject": "测试广告",
            "executive_summary": "摘要",
            "insights": [
                {
                    "insight_id": "insight_001",
                    "dimension": "creative_structure",
                    "claim": "先提出问题（speech_0002），再给出方案。",
                    "evidence_refs": ["speech_0002", "speech_0003"],
                    "confidence": 0.8,
                    "epistemic_status": "inferred_from_ad",
                    "reasoning_summary": "前半段 speech_0002，后半段 speech_0003。",
                }
            ],
            "unknowns": [],
        }
    )

    normalized = normalize_inline_citations(analysis)

    assert "speech_" not in normalized.insights[0].claim
    assert "speech_" not in normalized.insights[0].reasoning_summary
    assert normalized.insights[0].evidence_refs == ["speech_0002", "speech_0003"]

    fragments = analysis.model_copy(update={"insights": [analysis.insights[0].model_copy(update={
        "claim": "多种穿搭（kf_0001_primary, kf_0002_primary）。",
        "reasoning_summary": "来自关键帧（参考证据：kf_0001_primary，kf_0002_primary）与字幕[ocr_cluster_0001, ocr_cluster_0002]。",
    })]})
    cleaned = normalize_inline_citations(fragments)
    assert cleaned.insights[0].claim == "多种穿搭。"
    assert cleaned.insights[0].reasoning_summary == "来自关键帧与字幕。"
    visual_inference = analysis.model_copy(update={"insights": [analysis.insights[0].model_copy(update={
        "claim": "展示多种搭配，暗示产品百搭。",
        "evidence_refs": ["kf_0001_primary"], "epistemic_status": "observed",
    })]})
    assert normalize_inline_citations(visual_inference).insights[0].epistemic_status == "inferred_from_ad"


def test_ocr_member_reference_is_mapped_to_its_actual_cluster() -> None:
    analysis = MarketingAnalysis.model_validate(
        {
            "asset_id": "asset_1",
            "language": "zh",
            "subject": "测试广告",
            "executive_summary": "摘要",
            "insights": [
                {
                    "insight_id": "insight_001",
                    "dimension": "selling_point",
                    "claim": "产品主张平滑和保湿。",
                    "evidence_refs": ["ocr_cluster_0024", "ocr_0024"],
                    "confidence": 0.8,
                    "epistemic_status": "stated_by_ad",
                    "reasoning_summary": "依据广告文字。",
                }
            ],
            "unknowns": [],
        }
    )

    normalized = normalize_ocr_cluster_references(
        analysis,
        {"ocr_0024": "ocr_cluster_0016"},
    )

    assert normalized.insights[0].evidence_refs == ["ocr_cluster_0016"]


def test_unknown_ocr_reference_is_not_guessed() -> None:
    analysis = MarketingAnalysis.model_validate(
        {
            "asset_id": "asset_1",
            "language": "zh",
            "subject": "测试广告",
            "executive_summary": "摘要",
            "insights": [
                {
                    "insight_id": "insight_001",
                    "dimension": "selling_point",
                    "claim": "产品主张平滑和保湿。",
                    "evidence_refs": ["ocr_cluster_9999"],
                    "confidence": 0.8,
                    "epistemic_status": "stated_by_ad",
                    "reasoning_summary": "依据广告文字。",
                }
            ],
            "unknowns": [],
        }
    )

    normalized = normalize_ocr_cluster_references(analysis, {})

    assert normalized.insights[0].evidence_refs == ["ocr_cluster_9999"]


def test_valid_cluster_reference_is_not_remapped_using_its_member_number() -> None:
    # Real Ledger numbers diverge once repeated OCR frames are clustered.
    analysis = MarketingAnalysis.model_validate({
        "asset_id": "asset_1", "language": "zh", "subject": "测试广告",
        "executive_summary": "", "unknowns": [], "insights": [{
            "insight_id": "selling_01", "dimension": "selling_point",
            "claim": "广告显示 contact points。", "confidence": 0.8,
            "epistemic_status": "stated_by_ad", "reasoning_summary": "依据字幕。",
            "evidence_refs": ["ocr_cluster_0015", "ocr_0015"],
        }],
    })
    normalized = normalize_ocr_cluster_references(analysis, {
        "ocr_0015": "ocr_cluster_0014", "ocr_0016": "ocr_cluster_0015",
    })
    assert normalized.insights[0].evidence_refs == ["ocr_cluster_0015", "ocr_cluster_0014"]
    assert normalize_ocr_cluster_references(normalized, {
        "ocr_0015": "ocr_cluster_0014", "ocr_0016": "ocr_cluster_0015",
    }) == normalized
