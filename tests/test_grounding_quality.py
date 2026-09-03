import pytest

from ad_vista_agent.insights.grounding import validate_analysis_grounding
from ad_vista_agent.schemas import MarketingAnalysis


def _analysis(claim: str, evidence_ref: str = "ocr_cluster_0001") -> MarketingAnalysis:
    return MarketingAnalysis.model_validate(
        {
            "asset_id": "asset_test",
            "language": "zh",
            "subject": "测试广告",
            "executive_summary": "摘要",
            "insights": [
                {
                    "insight_id": "insight_001",
                    "dimension": "selling_point",
                    "claim": claim,
                    "evidence_refs": [evidence_ref],
                    "confidence": 0.7,
                    "epistemic_status": "observed",
                    "reasoning_summary": "依据画面文字。",
                }
            ],
            "unknowns": [],
        }
    )


def test_grounding_requires_claim_overlap_for_text_evidence() -> None:
    with pytest.raises(ValueError, match="obvious lexical support"):
        validate_analysis_grounding(
            _analysis("视频展示了临床抗衰老功效"),
            asset_id="asset_test",
            allowed_references={"ocr_cluster_0001"},
            max_per_dimension=5,
            reference_content={"ocr_cluster_0001": "37"},
        )


def test_grounding_accepts_claim_with_source_overlap() -> None:
    validate_analysis_grounding(
        _analysis("画面文字显示 Nenuco 产品"),
        asset_id="asset_test",
        allowed_references={"ocr_cluster_0001"},
        max_per_dimension=5,
        reference_content={"ocr_cluster_0001": "Nenuco"},
    )


def test_grounding_accepts_translated_claim_with_speech_source() -> None:
    validate_analysis_grounding(
        _analysis("视频介绍一款可以互动和照料的娃娃", evidence_ref="speech_0001"),
        asset_id="asset_test",
        allowed_references={"speech_0001"},
        max_per_dimension=5,
        reference_content={"speech_0001": "Papusa interactiva pe care o poti hrani"},
    )
