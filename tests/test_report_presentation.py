"""检查报告重排后的证据完整性、来源安全和离线可用性。"""

import base64
import html
import json
from html.parser import HTMLParser
from pathlib import Path

from ad_vista_agent.reports.render import render_html_report
from ad_vista_agent.schemas import (
    AuditFinding,
    AuditSeverity,
    CriticAudit,
    EpistemicStatus,
    EvidenceModality,
    GroundedMarketingInsight,
    InsightAudit,
    InsightAuditStatus,
    MarketingAnalysis,
    MarketingDimension,
    ReportEvidence,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBM0AAAAASUVORK5CYII="
)


class Document(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def sample(tmp_path: Path) -> tuple[MarketingAnalysis, CriticAudit]:
    (tmp_path / "frame.png").write_bytes(PNG)
    evidence = [
        ReportEvidence(
            reference_id=f"ocr_{index}",
            source_evidence_id=f"source_{index}",
            modality=EvidenceModality.OCR,
            content=f"来源内容 {index}",
            start_ms=index * 1000,
            end_ms=index * 1000 + 500,
            confidence=0.91,
            epistemic_status=EpistemicStatus.STATED_BY_AD,
            artifact_path="frame.png",
            artifact_hash=f"hash_{index}",
            member_count=2,
        )
        for index in range(5)
    ]
    analysis = MarketingAnalysis(
        asset_id="asset_1",
        language="zh",
        subject="清爽控油洁面广告",
        executive_summary="广告强调清爽肤感。功效主张需要额外验证。",
        insights=[
            GroundedMarketingInsight(
                insight_id="insight_internal_1",
                dimension=MarketingDimension.SELLING_POINT,
                claim="广告以清爽肤感作为核心卖点",
                evidence_refs=[item.reference_id for item in evidence],
                confidence=0.81,
                epistemic_status=EpistemicStatus.STATED_BY_AD,
                reasoning_summary="画面文字给出产品主张，语义与宣称保持一致。",
            ),
            GroundedMarketingInsight(
                insight_id="insight_internal_2",
                dimension=MarketingDimension.AUDIENCE,
                claim="广告可能面向重视清爽肤感的用户",
                evidence_refs=["ocr_0"],
                confidence=0.7,
                epistemic_status=EpistemicStatus.INFERRED_FROM_AD,
                reasoning_summary="这是从广告文案进行的受众推断，未做用户调研。",
            ),
            GroundedMarketingInsight(
                insight_id="insight_internal_3",
                dimension=MarketingDimension.RISK,
                claim="长期效果尚缺独立证据",
                evidence_refs=["missing_reference"],
                confidence=0.4,
                epistemic_status=EpistemicStatus.UNKNOWN,
                reasoning_summary="现有材料无法核实持续效果。",
            ),
        ],
        unknowns=["缺少独立功效实验"],
    )
    audit = CriticAudit(
        asset_id="asset_1",
        status=InsightAuditStatus.FAIL,
        insight_count=3,
        passed_count=1,
        review_count=1,
        failed_count=1,
        insights=[
            InsightAudit(
                insight_id="insight_internal_1",
                status=InsightAuditStatus.PASS,
                findings=[],
                evidence=evidence,
            ),
            InsightAudit(
                insight_id="insight_internal_2",
                status=InsightAuditStatus.REVIEW,
                findings=[
                    AuditFinding(
                        code="audience_hypothesis",
                        severity=AuditSeverity.WARNING,
                        message="受众推断需要验证",
                        evidence_refs=["ocr_0"],
                    )
                ],
                evidence=evidence[:1],
            ),
            InsightAudit(
                insight_id="insight_internal_3",
                status=InsightAuditStatus.FAIL,
                findings=[
                    AuditFinding(
                        code="invalid_reference",
                        severity=AuditSeverity.ERROR,
                        message="缺少引用来源",
                        evidence_refs=["missing_reference"],
                    )
                ],
                evidence=[],
            ),
        ],
        global_findings=[
            AuditFinding(
                code="global_review",
                severity=AuditSeverity.WARNING,
                message="全局风险项仍需人工复核",
                evidence_refs=["missing_reference"],
            )
        ],
    )
    return analysis, audit


def render(
    tmp_path: Path, analysis: MarketingAnalysis, audit: CriticAudit, **options: object
) -> str:
    defaults = {
        "embed_images": True,
        "max_image_bytes": 500_000,
        "max_evidence_per_insight": 1,
    }
    defaults.update(options)
    return render_html_report(analysis, audit, run_dir=tmp_path, **defaults)


def test_report_hierarchy_keeps_facts_risks_and_audit_status(tmp_path: Path) -> None:
    analysis, audit = sample(tmp_path)
    before = (analysis.model_dump(), audit.model_dump())
    report = render(
        tmp_path, analysis, audit, source_name="02c2938d-48be-42ed-9617-be520a4019e0"
    )
    document = Document(report)
    text = "\n".join(document.text)
    assert f"<h1>{analysis.subject}</h1>" in report
    assert "素材：02c2938d-48be-42ed-9617-be520a4019e0" in text
    for insight in analysis.insights:
        assert insight.claim in text
        assert insight.reasoning_summary in text
        assert insight.insight_id in text
        assert all(reference in text for reference in insight.evidence_refs)
    assert "营销推断 · 待验证" in text
    assert "广告中的主张" in text
    assert "证据不足" in text
    assert "审计：失败" in text
    assert "审计：建议复核" in text
    assert "全局风险项仍需人工复核" in text
    assert "缺少独立功效实验" in text
    assert "不替代人工审批" in text
    assert (analysis.model_dump(), audit.model_dump()) == before
    ids = {attrs.get("id") for _, attrs in document.elements}
    for tag, attrs in document.elements:
        if tag == "a" and str(attrs.get("href", "")).startswith("#"):
            assert attrs["href"][1:] in ids


def test_preview_limit_preserves_every_evidence_record(tmp_path: Path) -> None:
    analysis, audit = sample(tmp_path)
    report = render(tmp_path, analysis, audit)
    document = Document(report)
    records = [
        json.loads(base64.b64decode(attrs["href"].split(",", 1)[1]))
        for tag, attrs in document.elements
        if tag == "a" and attrs.get("download") == "advista-evidence.json"
    ]
    assert [record["reference_id"] for record in records[:5]] == [
        f"ocr_{index}" for index in range(5)
    ]
    assert records[:5] == [
        item.model_dump(mode="json") for item in audit.insights[0].evidence
    ]
    assert "更多证据 · 4 条" in report
    # 一张封面与每个非空结论的首张证据图，额外来源保留文字与元数据。
    assert len([tag for tag, _ in document.elements if tag == "img"]) == 3
    for index in range(5):
        assert f"来源内容 {index}" in report


def test_report_escapes_untrusted_text_and_is_self_contained(tmp_path: Path) -> None:
    analysis, audit = sample(tmp_path)
    hostile = (
        '<script>alert(1)</script><img src="https://bad.invalid" onerror="alert(2)"> &'
    )
    analysis.subject = hostile
    analysis.executive_summary = hostile
    analysis.unknowns = [hostile]
    analysis.insights[0].claim = hostile
    analysis.insights[0].reasoning_summary = hostile
    audit.insights[0].evidence[0].content = hostile
    audit.global_findings[0].message = hostile
    report = render(tmp_path, analysis, audit, source_name=hostile)
    document = Document(report)
    assert html.escape(hostile) in report
    assert "<script" not in report
    for tag, attrs in document.elements:
        assert tag not in {"script", "iframe", "link"}
        assert not any(name.startswith("on") for name in attrs)
        for name in ("src", "href"):
            if attrs.get(name):
                assert attrs[name].startswith(("data:", "#"))


def test_downloads_keep_original_analysis_and_provenance(tmp_path: Path) -> None:
    analysis, audit = sample(tmp_path)
    document = Document(render(tmp_path, analysis, audit))
    downloads = [
        attrs
        for tag, attrs in document.elements
        if tag == "a" and attrs.get("download")
    ]
    markdown = next(
        attrs for attrs in downloads if attrs["download"] == "advista-analysis.md"
    )
    text = base64.b64decode(markdown["href"].split(",", 1)[1]).decode("utf-8")
    assert analysis.executive_summary in text
    assert all(insight.claim in text for insight in analysis.insights)
    assert "建议复核" in text
    assert "missing_reference" in text
    evidence = next(
        attrs for attrs in downloads if attrs["download"] == "advista-evidence.json"
    )
    record = json.loads(base64.b64decode(evidence["href"].split(",", 1)[1]))
    assert record == audit.insights[0].evidence[0].model_dump(mode="json")


def test_image_limits_and_path_boundary_apply_to_cover_and_evidence(
    tmp_path: Path,
) -> None:
    analysis, audit = sample(tmp_path)
    assert "<img" not in render(tmp_path, analysis, audit, embed_images=False)
    assert "<img" not in render(tmp_path, analysis, audit, max_image_bytes=0)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (tmp_path / "outside.png").write_bytes(PNG)
    for item in audit.insights:
        for evidence in item.evidence:
            evidence.artifact_path = "../outside.png"
    report = render(run_dir, analysis, audit)
    assert "<img" not in report
    assert "来源内容 0" in report


def test_native_disclosures_and_print_responsive_contract(tmp_path: Path) -> None:
    analysis, audit = sample(tmp_path)
    report = render(tmp_path, analysis, audit)
    document = Document(report)
    details = [attrs for tag, attrs in document.elements if tag == "details"]
    assert any(attrs.get("class") == "evidence-details" for attrs in details)
    assert any(attrs.get("class") == "source-metadata" for attrs in details)
    assert all("open" not in attrs for attrs in details)
    assert "@media(max-width:780px)" in report
    assert "@media(max-width:520px)" in report
    assert "@media print" in report
    assert "details::details-content{content-visibility:visible!important" in report
    assert "object-fit:contain" in report
    assert "prefers-reduced-motion:reduce" in report


def test_empty_report_does_not_invent_conclusions(tmp_path: Path) -> None:
    analysis = MarketingAnalysis(
        asset_id="empty",
        language="zh",
        subject="未完成分析",
        executive_summary="没有足够证据。",
        insights=[],
        unknowns=["缺少素材"],
    )
    audit = CriticAudit(
        asset_id="empty",
        status=InsightAuditStatus.REVIEW,
        insight_count=0,
        passed_count=0,
        review_count=0,
        failed_count=0,
        insights=[],
        global_findings=[],
    )
    report = render(tmp_path, analysis, audit)
    assert "本次没有生成可展示的结论" in report
    assert "没有足够证据。" in report
    assert "缺少素材" in report
    assert "审计：建议复核" in report
    assert "<img" not in report


def test_info_notes_are_neutral_and_folded_while_risks_stay_visible(
    tmp_path: Path,
) -> None:
    analysis, audit = sample(tmp_path)
    note = AuditFinding(
        code="uncalibrated_source_confidence",
        severity=AuditSeverity.INFO,
        message="该证据源没有提供校准置信度，报告保留原始不确定性。",
        evidence_refs=["ocr_0"],
    )
    audit.insights[0].findings = [
        note,
        note.model_copy(update={"evidence_refs": ["ocr_1"]}),
    ]
    audit.insights[1].findings.append(note)
    before = audit.model_dump()
    report = render(tmp_path, analysis, audit)
    document = Document(report)
    notes = [
        attrs
        for tag, attrs in document.elements
        if tag == "details" and attrs.get("class") == "source-notes"
    ]
    assert len(notes) == 2
    assert all("open" not in attrs for attrs in notes)
    assert "来源说明（2项）" in report
    assert "来源说明（1项）" in report
    first_card = report.split('<article class="insight pass">', 1)[1].split(
        "</article>", 1
    )[0]
    assert 'class="findings"' not in first_card
    assert "受众推断需要验证" in report
    assert "缺少引用来源" in report
    assert report.count('class="findings"') == 2
    assert report.count(note.message) == 3
    assert audit.model_dump() == before
    download = next(
        attrs
        for tag, attrs in document.elements
        if tag == "a" and attrs.get("download") == "advista-analysis.md"
    )
    markdown = base64.b64decode(download["href"].split(",", 1)[1]).decode("utf-8")
    assert markdown.count(note.message) == 3
    assert "uncalibrated_source_confidence" in markdown
    assert "引用：ocr_0" in markdown and "引用：ocr_1" in markdown
    assert "全局风险项仍需人工复核" in markdown
