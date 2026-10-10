from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.errors import AnalysisGroundingError
from ad_vista_agent.insights.grounding import validate_analysis_grounding
from ad_vista_agent.insights.text_sources import (
    filter_product_text,
    omit_visual_only_audiences,
    review_text_sources,
)
from ad_vista_agent.runtime.process import ProcessCancelled
from ad_vista_agent.schemas import MarketingAnalysis
from ad_vista_agent.tools import ToolContext


class ReviewTool:
    def __init__(self, decisions: list[dict[str, str]] | Exception) -> None:
        self.decisions = decisions
        self.arguments: dict[str, Any] = {}

    def run(self, _context: Any, arguments: dict[str, Any]) -> Any:
        self.arguments = arguments
        if isinstance(self.decisions, Exception):
            raise self.decisions
        return SimpleNamespace(
            text=json.dumps({"sources": self.decisions}),
            attempts=["review"], prompt_tokens=10, completion_tokens=20,
        )


def _decision(reference: str, classification: str) -> dict[str, str]:
    return {"reference_id": reference, "classification": classification, "reason": "依据文字来源判断"}


def _review(ledger: dict[str, Any], tool: Any) -> dict[str, Any]:
    return review_text_sources(
        ledger,
        visual_observations=[{"keyframe_id": "kf_0001_primary", "description": "人物展示米色外套并手持手机"}],
        settings=load_settings(), tool=tool,
        context=ToolContext(run_id="fixture_14_seconds", run_dir=Path("/tmp")),
    )


def test_14_second_video_lyrics_cannot_be_product_citation_even_with_visual_reference() -> None:
    ledger = {
        "speech_evidence": [{"evidence_id": "speech_0001", "content": "背景说唱，包含 push the pack"}],
        "ocr_clusters": [
            {"cluster_id": "ocr_cluster_0001", "content": "1 2 3 " * 20},
            {"cluster_id": "ocr_cluster_0002", "content": "[Your input text]"},
            {"cluster_id": "ocr_cluster_0003", "content": "NOTATION"},
        ],
    }
    tool = ReviewTool([
        _decision("speech_0001", "background_audio"),
        _decision("ocr_cluster_0003", "unrelated_text"),
    ])
    review = _review(ledger, tool)
    assert review["allowed_product_references"] == []
    assert len(review["sources"]) == 4
    model_sources = json.loads(tool.arguments["messages"][-1]["content"])["sources"]
    assert len(model_sources) == 2  # 明确 OCR 噪声不送进模型。
    filtered = filter_product_text(ledger, review)
    assert filtered["speech_evidence"] == filtered["ocr_clusters"] == []
    assert ledger["speech_evidence"][0]["content"].endswith("push the pack")
    # 原失败含关键帧和歌词，旧词语校验因关键帧直接放行；新 allowed 集合必须拒绝歌词。
    analysis = MarketingAnalysis.model_validate({
        "asset_id": "asset_test", "language": "zh", "subject": "画面可见外套", "executive_summary": "", "unknowns": [],
        "insights": [{
            "insight_id": "insight_002", "dimension": "selling_point",
            "claim": "把歌词的 pack 推断为手机包装", "confidence": 0.75,
            "epistemic_status": "inferred_from_ad", "reasoning_summary": "将背景歌词牵强关联到商品",
            "evidence_refs": ["kf_0001_primary", "speech_0001"],
        }],
    })
    with pytest.raises(AnalysisGroundingError, match="unknown evidence"):
        validate_analysis_grounding(analysis, asset_id="asset_test", allowed_references={"kf_0001_primary"}, max_per_dimension=3)
    observed = analysis.insights[0].model_copy(update={
        "claim": "画面人物手持手机展示米色外套", "epistemic_status": "observed", "evidence_refs": ["kf_0001_primary"],
    })
    validate_analysis_grounding(
        analysis.model_copy(update={"insights": [observed]}),
        asset_id="asset_test", allowed_references={"kf_0001_primary"}, max_per_dimension=3,
    )


def test_real_product_voiceover_price_and_specification_remain_available() -> None:
    ledger = {
        "speech_evidence": [{"evidence_id": "speech_0001", "content": "这件外套采用棉质面料，活动价299元"}],
        "ocr_clusters": [{"cluster_id": "ocr_cluster_0001", "content": "100% 棉 / 到手价 299 元 / M-XL"}],
    }
    review = _review(ledger, ReviewTool([
        _decision("speech_0001", "product_information"),
        _decision("ocr_cluster_0001", "product_information"),
    ]))
    assert review["allowed_product_references"] == ["speech_0001", "ocr_cluster_0001"]
    filtered = filter_product_text(ledger, review)
    assert filtered["speech_evidence"] == ledger["speech_evidence"]
    assert filtered["ocr_clusters"] == ledger["ocr_clusters"]


@pytest.mark.parametrize("decisions", [
    [],
    [_decision("speech_9999", "product_information")],
    [_decision("speech_0001", "product_information")] * 2,
    RuntimeError("模型暂不可用"),
])
def test_invalid_review_fails_closed_and_keeps_source_record(decisions: Any) -> None:
    ledger = {"speech_evidence": [{"evidence_id": "speech_0001", "content": "待确认文字"}], "ocr_clusters": []}
    review = _review(ledger, ReviewTool(decisions))
    assert review["allowed_product_references"] == []
    assert review["sources"][0]["classification"] == "uncertain"
    assert review["sources"][0]["reason"]
    assert ledger["speech_evidence"]


def test_source_review_preserves_cancellation() -> None:
    with pytest.raises(ProcessCancelled):
        _review({"speech_evidence": [{"evidence_id": "speech_0001", "content": "文字"}]}, ReviewTool(ProcessCancelled("用户取消")))


def test_empty_text_skips_model_request() -> None:
    tool = ReviewTool(RuntimeError("不得调用"))
    assert _review({"speech_evidence": [], "ocr_clusters": []}, tool)["sources"] == []
    assert not tool.arguments


def test_visual_model_appearance_does_not_prove_audience_but_text_audience_is_preserved() -> None:
    def audience(identifier: str, references: list[str], claim: str) -> dict[str, Any]:
        return {
            "insight_id": identifier, "dimension": "audience", "claim": claim,
            "confidence": 0.65, "epistemic_status": "inferred_from_ad",
            "reasoning_summary": "对应原始来源", "evidence_refs": references,
        }
    original = MarketingAnalysis.model_validate({
        "asset_id": "asset_test", "language": "zh", "subject": "服装", "executive_summary": "", "unknowns": [],
        "insights": [
            audience("aud_visual", ["kf_0001_primary"], "从模特外观推断为年轻女性"),
            audience("aud_text", ["speech_0001", "kf_0001_primary"], "广告旁白明确称适合初入职场人群"),
        ],
    })
    filtered, omitted = omit_visual_only_audiences(original)
    assert omitted == ["aud_visual"]
    assert [item.insight_id for item in filtered.insights] == ["aud_text"]
    assert filtered.unknowns[0].startswith("当前 Ledger 无证据表明")
    assert len(original.insights) == 2
