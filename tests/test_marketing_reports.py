from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.ledger.quality import ocr_quality_issue
from ad_vista_agent.reports.marketing import (
    render_marketing_html,
    render_marketing_markdown,
)
from ad_vista_agent.schemas import AgentRequest, MarketingStrategy
from ad_vista_agent.skills import (
    report_frontend_skill_fingerprint,
    task_skill_fingerprint,
)
from ad_vista_agent.strategy import builder


def _strategy(**updates: Any) -> MarketingStrategy:
    payload = {
        "positioning": "无法从当前文本确认产品定位",
        "target_audience": "待验证假设：测试对穿搭内容感兴趣的人群，年龄无法确认",
        "core_message": "当前文字证据不足，需补充产品卖点信息",
        "communication_strategy": ["建议先补充可核验的产品文案"],
        "content_directions": ["建议对卖点表达做 A/B 测试"],
        "channel_recommendations": ["待验证建议：测试短视频渠道"],
        "conversion_recommendations": ["建议先核实购买入口"],
        "hypotheses": ["待验证假设：清晰的商品文案能改善理解"],
        "evidence_refs": [],
        "unsupported_points": [],
        **updates,
    }
    return MarketingStrategy.model_validate(payload)


def test_strategy_references_are_text_only_and_unique() -> None:
    strategy = _strategy(evidence_refs=["speech_0001", "ocr_cluster_0001", "speech_0001"])
    assert strategy.evidence_refs == ["speech_0001", "ocr_cluster_0001"]
    with pytest.raises(ValueError, match="String should match pattern"):
        _strategy(evidence_refs=["kf_0001_primary"])


@pytest.mark.parametrize(
    ("content", "issue"),
    [
        ("[Your input text]", "recognition_placeholder"),
        ("[图片中没有可识别的文字内容]", "recognition_placeholder"),
        ("...，。!?", "empty_or_punctuation"),
        ("37", "isolated_number"),
        ("- 1", "isolated_number"),
        ("1 2 3 " * 20, "repeated_numeric_sequence"),
        ("2023.12\n2023.13\n2023.30", "invalid_date"),
        ("2023-02-30", "invalid_date"),
        ("活动日期 2024-02-29", None),
        ("到手价 299 元", None),
        ("100% 棉，防风保暖", None),
    ],
)
def test_ocr_quality_flags_only_confirmed_noise(content: str, issue: str | None) -> None:
    assert ocr_quality_issue(content) == issue


def test_strategy_context_excludes_noise_but_keeps_original_sources() -> None:
    context = {
        "ledger": {
            "speech_evidence": [{"evidence_id": "speech_0001", "content": "背景音乐"}],
            "ocr_clusters": [
                {"cluster_id": "ocr_cluster_0001", "content": "[Your input text]"},
                {"cluster_id": "ocr_cluster_0002", "content": "到手价 299 元"},
            ],
            "candidate_relations": [{"left_evidence_id": "ocr_0001"}],
        },
        "validated_insights": [
            {"claim": "不能根据占位符判断材质", "evidence_refs": ["ocr_cluster_0001"]},
            {"claim": "广告显示价格", "evidence_refs": ["ocr_cluster_0002"]},
        ],
        "unknowns": [],
    }
    prepared, allowed, sources = builder._prepare_text_context(context)
    assert allowed == {"speech_0001", "ocr_cluster_0002"}
    assert len(prepared["ledger"]["ocr_clusters"]) == 1
    assert len(prepared["validated_insights"]) == 1
    assert len(sources) == 3
    assert sources[1]["content"] == "[Your input text]"
    assert sources[1]["quality_issue"] == "recognition_placeholder"
    assert len(context["ledger"]["ocr_clusters"]) == 2


def test_marketing_html_separates_hypotheses_gaps_and_preserves_escaped_sources() -> None:
    strategy = _strategy(positioning='<script>alert("x")</script>')
    sources = [{
        "reference_id": "ocr_cluster_0001",
        "content": '<img src=x onerror="alert(1)">',
        "modality": "ocr",
        "start_ms": 1283,
        "quality_issue": "recognition_placeholder",
    }]
    document = render_marketing_html(strategy, request_goal='<script>alert("goal")</script>', sources=sources)
    assert '<script>' not in document
    assert '<img src=x' not in document
    assert '&lt;script&gt;' in document
    assert '&lt;img src=x' in document
    assert '<section id="hypotheses">' in document
    assert '<section id="gaps"' in document
    assert '<details><summary>查看全部原始文本' in document
    assert "识别占位符" in document
    assert "00:01.283" in document
    assert "@media(max-width:680px)" in document
    assert "@media print" in document
    assert report_frontend_skill_fingerprint() in document
    assert 'download="marketing.md" href="data:text/markdown;charset=utf-8;base64,' in document
    assert 'download="sources.json" href="data:application/json;charset=utf-8;base64,' in document
    markdown = render_marketing_markdown(strategy, request_goal="生成营销报告", sources=sources)
    assert "当前未引用能够支撑产品营销结论的文本证据。" in markdown
    assert "&lt;img src=x" in markdown


def _mock_builder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, strategy: MarketingStrategy) -> tuple[Any, dict[str, Any]]:
    run_dir = tmp_path / "runs" / "ingest_test"
    run_dir.mkdir(parents=True)
    artifact_root = tmp_path / "executions" / "exec_test"
    monkeypatch.setattr(builder, "ingest_video", lambda *_: {"run_id": "ingest_test", "run_dir": str(run_dir)})
    context = {
        "ledger": {
            "speech_evidence": [{"evidence_id": "speech_0001", "content": "背景歌词，不介绍产品", "start_ms": 0}],
            "ocr_clusters": [
                {"cluster_id": "ocr_cluster_0001", "content": "1 2 3 " * 20, "start_ms": 2567},
                {"cluster_id": "ocr_cluster_0002", "content": "[Your input text]", "start_ms": 10633},
            ],
        },
        "validated_insights": [],
        "unknowns": ["产品材质及受众年龄无法确认"],
    }
    monkeypatch.setattr(builder, "_load_context", lambda *_: (context, {"speech_0001", "ocr_cluster_0001", "ocr_cluster_0002"}, []))
    captured: dict[str, Any] = {}

    class FakeTool:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def run(self, _context: Any, payload: dict[str, Any]) -> Any:
            captured.update(payload)
            return SimpleNamespace(text=strategy.model_dump_json(), attempt_count=1)

    monkeypatch.setattr(builder, "QwenInsightTool", FakeTool)
    settings = load_settings()
    settings.paths.output_root = tmp_path
    return (settings, artifact_root), captured


def test_builder_generates_current_task_exports_without_forcing_irrelevant_citations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (settings, artifact_root), captured = _mock_builder(monkeypatch, tmp_path, _strategy())
    request = AgentRequest(goal="给这个视频生成一份营销报告，先说明证据缺口", deliverables=["strategy"])
    result = builder.build_strategy(tmp_path / "video.mp4", settings, request=request, artifact_root=artifact_root)
    strategy = json.loads(Path(result["strategy"]).read_text())
    metrics = json.loads(Path(result["metrics"]).read_text())
    assert strategy["evidence_refs"] == []
    assert any("材质" in point and "无法确认" in point for point in strategy["unsupported_points"])
    assert result["evidence_count"] == 0
    assert Path(result["html"]) == artifact_root / "marketing" / "marketing.html"
    assert Path(result["markdown"]).is_file()
    assert metrics["task"]["goal"] == request.goal
    assert metrics["task_skill_sha256"] == task_skill_fingerprint("strategy")
    assert metrics["frontend_skill_sha256"] == report_frontend_skill_fingerprint()
    assert metrics["excluded_ocr_count"] == 2
    model_context = json.loads(captured["messages"][1]["content"])
    assert model_context["ledger"]["ocr_clusters"] == []
    assert "MarketingStrategy" in captured["messages"][0]["content"]
    assert "ASR 背景歌词" in captured["messages"][0]["content"]
    assert "[Your input text]" in Path(result["html"]).read_text()
    assert not list(artifact_root.glob(".stage_strategy_*"))


def test_builder_rejects_filtered_noise_citations_without_overwriting_existing_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (settings, artifact_root), _ = _mock_builder(monkeypatch, tmp_path, _strategy(evidence_refs=["ocr_cluster_0002"]))
    report = artifact_root / "marketing" / "marketing.html"
    report.parent.mkdir(parents=True)
    report.write_text("先前已生成的报告")
    with pytest.raises(ValueError, match="unknown evidence"):
        builder.build_strategy(tmp_path / "video.mp4", settings, artifact_root=artifact_root)
    assert report.read_text() == "先前已生成的报告"
