from __future__ import annotations

import base64
import html
from collections import defaultdict
from pathlib import Path

from ad_vista_agent.schemas import (
    AuditFinding,
    CriticAudit,
    GroundedMarketingInsight,
    InsightAudit,
    MarketingAnalysis,
    MarketingDimension,
    ReportEvidence,
)
from ad_vista_agent.skills import (
    REPORT_FRONTEND_SKILL,
    report_frontend_summary,
)

DIMENSION_LABELS = {
    MarketingDimension.SELLING_POINT: "核心卖点",
    MarketingDimension.PAIN_POINT: "用户痛点",
    MarketingDimension.AUDIENCE: "目标受众",
    MarketingDimension.CREATIVE_STRUCTURE: "创意结构",
    MarketingDimension.CONVERSION_PATH: "转化路径",
    MarketingDimension.BRAND_EXPOSURE: "品牌露出",
    MarketingDimension.RISK: "风险提示",
}
STATUS_LABELS = {"pass": "通过", "review": "建议复核", "fail": "失败"}


def format_timestamp(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _audit_by_id(audit: CriticAudit) -> dict[str, InsightAudit]:
    return {item.insight_id: item for item in audit.insights}


def _markdown_evidence(item: ReportEvidence) -> str:
    confidence = "未提供" if item.confidence is None else f"{item.confidence:.3f}"
    path = "../" + item.artifact_path
    return (
        f"- `{item.reference_id}` [{format_timestamp(item.start_ms)}] "
        f"{item.content}  \n"
        f"  来源：`{item.source_evidence_id}` · {item.modality.value} · "
        f"置信度 {confidence} · 聚合 {item.member_count} 条 · [证据文件]({path})"
    )


def _markdown_finding(finding: AuditFinding) -> str:
    references = (
        f" · 引用：{', '.join(finding.evidence_refs)}" if finding.evidence_refs else ""
    )
    return f"- `{finding.severity.value}` / `{finding.code}`：{finding.message}{references}"


def render_markdown_report(
    analysis: MarketingAnalysis,
    audit: CriticAudit,
    *,
    source_name: str | None = None,
) -> str:
    audits = _audit_by_id(audit)
    grouped: dict[MarketingDimension, list[GroundedMarketingInsight]] = defaultdict(
        list
    )
    for insight in analysis.insights:
        grouped[insight.dimension].append(insight)
    lines = [
        f"# {source_name or analysis.subject} 广告分析报告",
        "",
        (
            f"> 审计状态：**{STATUS_LABELS[audit.status.value]}** · 洞察 {audit.insight_count} 条 · "
            f"通过 {audit.passed_count} · 复核 {audit.review_count} · 失败 {audit.failed_count}"
        ),
        "",
        "## 执行摘要",
        "",
        analysis.executive_summary,
        "",
    ]
    for dimension, dimension_label in DIMENSION_LABELS.items():
        insights = grouped.get(dimension, [])
        if not insights:
            continue
        lines.extend([f"## {dimension_label}", ""])
        for insight in insights:
            item_audit = audits[insight.insight_id]
            lines.extend(
                [
                    f"### {insight.claim}",
                    "",
                    f"- 洞察 ID：`{insight.insight_id}`",
                    f"- 完整引用：{', '.join(f'`{reference}`' for reference in insight.evidence_refs)}",
                    f"- 审计：**{STATUS_LABELS[item_audit.status.value]}**",
                    f"- 认识论状态：`{insight.epistemic_status.value}`",
                    f"- 模型置信度：{insight.confidence:.2f}",
                    f"- 推理摘要：{insight.reasoning_summary}",
                    "",
                    "**证据**",
                    "",
                ]
            )
            lines.extend(
                _markdown_evidence(evidence) for evidence in item_audit.evidence
            )
            if item_audit.findings:
                lines.extend(["", "**审计发现**", ""])
                lines.extend(
                    _markdown_finding(finding) for finding in item_audit.findings
                )
            lines.append("")
    if audit.global_findings:
        lines.extend(["## 报告审计发现", ""])
        lines.extend(_markdown_finding(finding) for finding in audit.global_findings)
        lines.append("")
    lines.extend(["## 当前证据缺口", ""])
    lines.extend(f"- {value}" for value in analysis.unknowns)
    lines.extend(
        [
            "",
            "## 使用说明",
            "",
            "本报告仅基于提供的视频证据与来源记录。广告中的功效、健康、安全和结果声明均不代表独立事实验证。",
            "",
        ]
    )
    return "\n".join(lines)


def _image_data_uri(item: ReportEvidence, run_dir: Path, max_bytes: int) -> str | None:
    artifact = (run_dir / item.artifact_path).resolve()
    if not artifact.is_relative_to(run_dir.resolve()):
        return None
    if artifact.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    if not artifact.is_file() or artifact.stat().st_size > max_bytes:
        return None
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[artifact.suffix.casefold()]
    encoded = base64.b64encode(artifact.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


EPISTEMIC_LABELS = {
    "observed": "直接观察",
    "stated_by_ad": "广告中的主张",
    "inferred_from_ad": "营销推断 · 待验证",
    "retrieved_knowledge": "外部检索信息",
    "unknown": "证据不足",
}
MODALITY_LABELS = {
    "video": "视频片段",
    "frame": "关键画面",
    "speech": "语音转写",
    "ocr": "画面文字",
    "audio": "音频",
    "detection": "检测结果",
    "retrieval": "检索资料",
}
SEVERITY_LABELS = {"error": "错误", "warning": "需复核", "info": "说明"}
DIMENSION_DESCRIPTIONS = {
    MarketingDimension.SELLING_POINT: "先看产品主张，再核查对应证据与适用边界。",
    MarketingDimension.PAIN_POINT: "广告如何描述需求、问题与使用场景。",
    MarketingDimension.AUDIENCE: "受众判断来自广告线索，不等同于真实用户研究。",
    MarketingDimension.CREATIVE_STRUCTURE: "梳理广告内容与表达方式。",
    MarketingDimension.CONVERSION_PATH: "查看广告呈现的行动引导与购买线索。",
    MarketingDimension.BRAND_EXPOSURE: "查看品牌与产品信息的呈现。",
    MarketingDimension.RISK: "保留需要复核的主张、推断与证据限制。",
}


def _data_download(text: str, mime: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _html_findings(findings: list[AuditFinding]) -> str:
    rows = []
    for finding in findings:
        references = (
            f"<small>涉及引用：{html.escape('、'.join(finding.evidence_refs))}</small>"
            if finding.evidence_refs
            else ""
        )
        rows.append(
            f'<li class="{finding.severity.value}">'
            f'<span class="finding-label">{SEVERITY_LABELS[finding.severity.value]}</span>'
            f"<div>{html.escape(finding.message)}"
            f"<small>检查项：<code>{html.escape(finding.code)}</code></small>{references}</div></li>"
        )
    return f'<ul class="findings-list">{"".join(rows)}</ul>'


def _html_evidence(
    item: ReportEvidence,
    *,
    run_dir: Path,
    embed_images: bool,
    max_image_bytes: int,
) -> str:
    confidence = "未提供" if item.confidence is None else f"{item.confidence:.3f}"
    image = _image_data_uri(item, run_dir, max_image_bytes) if embed_images else None
    label = MODALITY_LABELS.get(item.modality.value, item.modality.value)
    timestamp = format_timestamp(item.start_ms)
    if item.end_ms != item.start_ms:
        timestamp += f"–{format_timestamp(item.end_ms)}"
    image_html = (
        f'<img loading="lazy" src="{image}" alt="{html.escape(label)}，{timestamp}">'
        if image
        else ""
    )
    # The critic's synthetic frame caption is provenance, not a marketing claim.
    content = (
        "该时间点的视频关键帧。"
        if item.modality.value == "frame" and item.content.startswith("关键画面 ")
        else item.content
    )
    download = _data_download(item.model_dump_json(indent=2), "application/json")
    return (
        f'<article class="evidence-card{" with-image" if image else ""}">'
        f'{image_html}<div class="evidence-copy"><div class="evidence-label">'
        f"<strong>{html.escape(label)}</strong><time>{timestamp}</time></div>"
        f"<p>{html.escape(content)}</p>"
        '<details class="source-metadata"><summary>来源与技术信息</summary><dl>'
        f"<dt>引用 ID</dt><dd><code>{html.escape(item.reference_id)}</code></dd>"
        f"<dt>原始来源</dt><dd><code>{html.escape(item.source_evidence_id)}</code></dd>"
        f"<dt>原始内容</dt><dd>{html.escape(item.content)}</dd>"
        f"<dt>证据性质</dt><dd>{EPISTEMIC_LABELS[item.epistemic_status.value]}</dd>"
        f"<dt>识别置信度</dt><dd>{confidence} · 聚合 {item.member_count} 条</dd>"
        f"<dt>文件路径</dt><dd><code>{html.escape(item.artifact_path)}</code></dd>"
        f"<dt>文件校验</dt><dd><code>{html.escape(item.artifact_hash)}</code></dd></dl>"
        f'<a class="text-link" href="{download}" download="advista-evidence.json">下载这条证据记录</a>'
        "</details></div></article>"
    )


def _dimension_anchor(dimension: MarketingDimension) -> str:
    return f"section-{dimension.value.replace('_', '-')}"


def render_html_report(
    analysis: MarketingAnalysis,
    audit: CriticAudit,
    *,
    run_dir: Path,
    embed_images: bool,
    max_image_bytes: int,
    max_evidence_per_insight: int,
    source_name: str | None = None,
) -> str:
    frontend_summary = report_frontend_summary()
    audits = _audit_by_id(audit)
    grouped: dict[MarketingDimension, list[GroundedMarketingInsight]] = defaultdict(
        list
    )
    for insight in analysis.insights:
        grouped[insight.dimension].append(insight)
    sections: list[str] = []
    navigation = ['<a href="#executive-summary"><span>执行摘要</span></a>']
    preview_limit = max(0, max_evidence_per_insight)
    for dimension, dimension_label in DIMENSION_LABELS.items():
        insights = grouped.get(dimension, [])
        if not insights:
            continue
        anchor = _dimension_anchor(dimension)
        navigation.append(
            f'<a href="#{anchor}"><span>{dimension_label}</span><em>{len(insights)}</em></a>'
        )
        cards = []
        for index, insight in enumerate(insights, 1):
            item_audit = audits[insight.insight_id]
            evidence_cards = []
            for evidence_index, item in enumerate(item_audit.evidence):
                evidence_cards.append(
                    _html_evidence(
                        item,
                        run_dir=run_dir,
                        embed_images=embed_images and evidence_index < preview_limit,
                        max_image_bytes=max_image_bytes,
                    )
                )
            primary = "".join(evidence_cards[:preview_limit])
            remaining = evidence_cards[preview_limit:]
            additional = (
                '<details class="additional-evidence">'
                f"<summary>更多证据 · {len(remaining)} 条</summary>"
                f'<div class="evidence-grid">{"".join(remaining)}</div></details>'
                if remaining
                else ""
            )
            evidence_block = (
                f'<div class="evidence-grid">{primary}</div>{additional}'
                if evidence_cards
                else '<p class="empty-state">该结论暂无可展示的证据；请查看审计发现。</p>'
            )
            critical_findings = [
                item for item in item_audit.findings if item.severity.value != "info"
            ]
            source_notes = [
                item for item in item_audit.findings if item.severity.value == "info"
            ]
            findings_block = (
                '<div class="findings"><h4>需要关注的审计发现</h4>'
                f"{_html_findings(critical_findings)}</div>"
                if critical_findings
                else ""
            )
            if source_notes:
                findings_block += (
                    '<details class="source-notes">'
                    f"<summary>来源说明（{len(source_notes)}项）</summary>"
                    f"{_html_findings(source_notes)}</details>"
                )
            epistemic = insight.epistemic_status.value
            hypothesis = (
                '<p class="hypothesis-note">此结论来自广告线索的推断，仍需补充验证。</p>'
                if epistemic == "inferred_from_ad"
                else ""
            )
            cards.append(
                f'<article class="insight {item_audit.status.value}">'
                f'<div class="insight-head"><span class="insight-number">{dimension_label} {index:02d}</span>'
                f'<span class="status {item_audit.status.value}">审计：{STATUS_LABELS[item_audit.status.value]}</span></div>'
                f"<h3>{html.escape(insight.claim)}</h3>"
                f'<div class="claim-meta"><span class="epistemic {epistemic}">{EPISTEMIC_LABELS[epistemic]}</span>'
                f"<span>模型置信度 {insight.confidence:.2f}</span></div>{hypothesis}"
                f'<p class="reasoning"><strong>分析依据</strong>{html.escape(insight.reasoning_summary)}</p>'
                '<details class="evidence-details"><summary>'
                f"<span>核查证据链 <em>{len(item_audit.evidence)} 条来源</em></span>"
                f"</summary>{evidence_block}</details>{findings_block}"
                '<details class="insight-metadata"><summary>结论引用与编号</summary>'
                f"<p>洞察 ID：<code>{html.escape(insight.insight_id)}</code></p>"
                f"<p>完整引用：<code>{html.escape('、'.join(insight.evidence_refs))}</code></p>"
                f"</details></article>"
            )
        featured = " featured" if dimension == MarketingDimension.SELLING_POINT else ""
        sections.append(
            f'<section class="dimension{featured}" id="{anchor}"><div class="section-heading">'
            f'<div><span class="section-index">{len(sections) + 1:02d}</span>'
            f'<h2>{dimension_label}</h2></div><span class="section-count">{len(insights)} 条结论</span>'
            f'</div><p class="section-intro">{DIMENSION_DESCRIPTIONS[dimension]}</p>{"".join(cards)}</section>'
        )
    if not sections:
        sections.append(
            '<section class="dimension"><h2>分析结果</h2>'
            '<p class="empty-state">本次没有生成可展示的结论。请核查证据缺口与审计信息。</p></section>'
        )
    global_findings = (
        '<section class="audit-notes" id="audit-notes"><h2>报告审计发现</h2>'
        f"{_html_findings(audit.global_findings)}</section>"
        if audit.global_findings
        else ""
    )
    if global_findings:
        navigation.append('<a href="#audit-notes"><span>报告审计发现</span></a>')
    unknowns = "".join(f"<li>{html.escape(value)}</li>" for value in analysis.unknowns)
    gaps_content = f"<ul>{unknowns}</ul>" if unknowns else "<p>未记录额外证据缺口。</p>"
    unknowns_block = (
        '<section class="unknowns" id="evidence-gaps"><h2>当前证据缺口</h2>'
        f"{gaps_content}</section>"
    )
    navigation.append('<a href="#evidence-gaps"><span>证据缺口</span></a>')
    report_name = analysis.subject or source_name or "广告视频分析"
    source = (
        f'<p class="source-name">素材：{html.escape(source_name)}</p>'
        if source_name and source_name != report_name
        else ""
    )
    cover = ""
    if embed_images:
        for item_audit in audit.insights:
            for item in item_audit.evidence:
                image = _image_data_uri(item, run_dir, max_image_bytes)
                if image:
                    cover = (
                        '<figure class="report-cover">'
                        f'<img src="{image}" alt="广告视频证据画面，{format_timestamp(item.start_ms)}">'
                        f"<figcaption>视频证据画面 <time>{format_timestamp(item.start_ms)}</time></figcaption></figure>"
                    )
                    break
            if cover:
                break
    markdown_download = _data_download(
        render_markdown_report(analysis, audit, source_name=source_name),
        "text/markdown;charset=utf-8",
    )
    status = audit.status.value
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="AdVista {REPORT_FRONTEND_SKILL}">
<meta name="ad-vista-frontend-contract" content="deterministic-renderer-v2">
<meta name="ad-vista-frontend-rules" content="{html.escape(frontend_summary)}">
<title>{html.escape(report_name)} · 广告分析报告</title>
<style>
:root{{--ink:#252e2b;--muted:#68736d;--paper:#f7f8f5;--white:#fff;--line:#e2e6df;--accent:#bf603a;--green:#346a51;--amber:#865e1d;--red:#a0443b}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 system-ui,-apple-system,"Noto Sans SC","Microsoft YaHei",sans-serif}}a{{color:inherit}}a:focus-visible,summary:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}.skip-link{{position:absolute;left:20px;top:-60px;padding:10px;background:#fff;z-index:10}}.skip-link:focus{{top:16px}}.layout{{display:grid;grid-template-columns:208px minmax(0,1fr);width:min(1440px,100%);margin:0 auto;min-height:100vh}}.rail{{position:sticky;top:0;height:100vh;padding:38px 22px 28px;border-right:1px solid var(--line);display:flex;flex-direction:column}}.brand{{font-size:17px;font-weight:750;letter-spacing:-.03em}}.brand small{{display:block;margin-top:4px;color:var(--muted);font-size:11px;font-weight:400;letter-spacing:0}}.rail-title{{margin:44px 0 12px;color:var(--muted);font-size:11px}}nav{{display:grid;gap:5px}}nav a{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-radius:6px;color:#59645e;font-size:13px;text-decoration:none}}nav a:hover{{background:#ecefe8;color:var(--ink)}}nav em{{font-size:11px;font-style:normal;color:var(--muted)}}.rail-foot{{margin-top:auto;color:var(--muted);font-size:11px;line-height:1.7}}
.report-main{{min-width:0;width:100%;max-width:1180px;padding:42px 48px 72px}}.report-header{{margin-bottom:30px}}.report-topline{{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:28px}}.kicker{{margin:0;color:var(--accent);font-size:11px;font-weight:750;letter-spacing:.12em}}.report-actions{{display:flex;flex-wrap:wrap;align-items:center;gap:12px;color:var(--muted);font-size:11px}}.text-link{{color:var(--accent);text-underline-offset:3px}}.hero-grid{{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:28px;align-items:center}}.hero-grid.no-cover{{grid-template-columns:1fr}}h1{{margin:0;font-size:36px;font-weight:720;line-height:1.35;letter-spacing:-.035em;overflow-wrap:anywhere}}.report-subtitle{{margin:12px 0 0;color:var(--muted);font-size:15px}}.source-name{{margin:12px 0 0;color:var(--muted);font-size:11px;overflow-wrap:anywhere}}.report-cover{{margin:0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#edf0eb}}.report-cover img{{display:block;width:100%;aspect-ratio:16/10;object-fit:contain}}.report-cover figcaption{{display:flex;justify-content:space-between;padding:8px 10px;color:var(--muted);font-size:10px;background:#fff}}.report-status{{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:22px}}.status{{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border:1px solid transparent;border-radius:5px;font-size:11px;font-weight:650;line-height:1.5;white-space:nowrap}}.status::before{{content:"";width:5px;height:5px;border-radius:50%;background:currentColor}}.status.pass{{color:var(--green);background:#eaf2eb;border-color:#d4e3d6}}.status.review{{color:var(--amber);background:#fff4e1;border-color:#eeddb8}}.status.fail{{color:var(--red);background:#fff0ed;border-color:#efd4ce}}.audit-caption{{color:var(--muted);font-size:11px}}
.executive-summary{{padding:25px 28px;background:#fff;border:1px solid var(--line);border-radius:8px;scroll-margin-top:24px}}.summary-heading{{display:flex;justify-content:space-between;align-items:center;gap:12px}}h2{{margin:0;font-size:21px;font-weight:700;line-height:1.4}}.summary-heading small{{color:var(--muted);font-size:11px}}.summary{{margin:14px 0 24px;font-size:15px;line-height:1.85;white-space:pre-line;overflow-wrap:anywhere}}.scoreboard{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;padding-top:18px;border-top:1px solid var(--line)}}.score strong{{display:block;font-size:24px;font-weight:650;line-height:1.2}}.score span{{display:block;margin-top:6px;color:var(--muted);font-size:11px}}.score.review strong{{color:var(--amber)}}.score.fail strong{{color:var(--red)}}
.dimension{{padding-top:42px;scroll-margin-top:22px}}.section-heading{{display:flex;align-items:center;justify-content:space-between;gap:12px}}.section-heading>div{{display:flex;align-items:center;gap:12px}}.section-index{{color:var(--accent);font-size:11px;font-weight:700}}.section-count{{color:var(--muted);font-size:11px;white-space:nowrap}}.section-intro{{margin:9px 0 20px;color:var(--muted);font-size:13px}}.dimension.featured .section-heading{{padding-bottom:13px;border-bottom:1px solid #bcc9bc}}.dimension.featured h2::after{{content:"重点";display:inline-block;margin-left:10px;padding:3px 7px;border-radius:4px;color:var(--accent);background:#f7ebe2;font-size:10px;font-weight:600;vertical-align:middle}}
.insight{{margin:0 0 18px;padding:23px 26px;background:#fff;border:1px solid var(--line);border-radius:8px}}.insight.review{{border-left:3px solid #caa666}}.insight.fail{{border-left:3px solid #c77e73}}.insight-head{{display:flex;align-items:center;justify-content:space-between;gap:12px}}.insight-number{{color:var(--muted);font-size:11px}}h3{{margin:15px 0 11px;font-size:20px;font-weight:650;line-height:1.6;overflow-wrap:anywhere}}.claim-meta{{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;color:var(--muted);font-size:11px}}.epistemic{{padding:3px 7px;border-radius:4px;background:#f0f2ed;color:#58665c}}.epistemic.inferred_from_ad{{background:#f4eee4;color:#7d643e}}.epistemic.unknown{{background:#fff0ed;color:var(--red)}}.hypothesis-note{{margin:9px 0 0;color:var(--amber);font-size:12px}}.reasoning{{margin:18px 0 20px;color:#5c6660;font-size:14px;line-height:1.8;white-space:pre-line;overflow-wrap:anywhere}}.reasoning strong{{display:block;margin-bottom:5px;color:var(--ink);font-size:12px;font-weight:650}}details>summary{{cursor:pointer}}.evidence-details{{border-top:1px solid var(--line)}}.evidence-details>summary{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 0 2px;font-size:12px;font-weight:650;list-style:none}}.evidence-details>summary::-webkit-details-marker{{display:none}}.evidence-details>summary::after{{content:"＋";color:var(--muted);font-size:16px;font-weight:400}}.evidence-details[open]>summary::after{{content:"−"}}.evidence-details summary em{{margin-left:7px;color:var(--muted);font-size:11px;font-style:normal;font-weight:400}}.evidence-grid{{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;margin-top:14px}}.evidence-card{{min-width:0;overflow:hidden;border:1px solid var(--line);border-radius:6px;background:#fafbf8}}.evidence-card.with-image{{display:grid;grid-template-columns:170px minmax(0,1fr)}}.evidence-card img{{display:block;width:100%;height:auto;aspect-ratio:16/10;object-fit:contain;background:#edf0eb;align-self:start}}.evidence-copy{{min-width:0;padding:13px 15px}}.evidence-label{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;font-size:11px}}.evidence-label strong{{font-weight:650}}time{{color:var(--accent);font-variant-numeric:tabular-nums;font-size:11px}}.evidence-copy>p{{margin:8px 0 12px;color:#536059;font-size:13px;line-height:1.7;white-space:pre-line;overflow-wrap:anywhere}}.source-metadata,.insight-metadata{{color:var(--muted);font-size:11px}}.source-metadata>summary,.insight-metadata>summary{{color:#6a756d}}.source-metadata dl{{display:grid;grid-template-columns:80px minmax(0,1fr);gap:6px 10px;margin:12px 0}}.source-metadata dt{{color:var(--muted)}}.source-metadata dd{{margin:0;overflow-wrap:anywhere;min-width:0}}code{{font:11px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}}.source-notes{{margin-top:16px;padding-top:14px;border-top:1px dashed var(--line);color:var(--muted);font-size:11px}}.source-notes .findings-list{{margin-top:14px;color:#606b63}}.insight-metadata{{margin-top:15px}}.insight-metadata p{{margin:9px 0 0;overflow-wrap:anywhere}}.additional-evidence{{margin-top:14px;font-size:12px;color:var(--muted)}}.empty-state{{padding:14px 0;margin:0;color:var(--muted);font-size:13px}}
.findings{{margin-top:20px;padding:15px 17px;border:1px solid #efdfbd;border-radius:6px;background:#fffaf0}}.findings h4{{margin:0 0 10px;font-size:12px;font-weight:650}}.findings-list{{display:grid;gap:10px;margin:0;padding:0;list-style:none;font-size:12px;line-height:1.7}}.findings-list li{{display:grid;grid-template-columns:52px minmax(0,1fr);gap:10px;overflow-wrap:anywhere}}.finding-label{{align-self:start;padding:2px 5px;border-radius:3px;background:#eee;color:#60665e;font-size:10px;text-align:center}}.warning .finding-label{{background:#f5e8cb;color:var(--amber)}}.error .finding-label{{background:#fae5e0;color:var(--red)}}.findings-list small{{display:block;margin-top:4px;color:var(--muted);font-size:10px}}.audit-notes,.unknowns{{margin-top:36px;padding:22px 26px;border:1px solid var(--line);border-radius:8px;background:#f0f3ec;scroll-margin-top:24px}}.audit-notes h2,.unknowns h2{{margin-bottom:14px;font-size:17px}}.unknowns ul{{display:grid;gap:8px;margin:0;padding-left:20px;color:#536059;font-size:13px}}.unknowns li,.unknowns p{{overflow-wrap:anywhere}}.unknowns p{{margin:0;color:var(--muted);font-size:13px}}footer{{margin-top:32px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:11px;line-height:1.8}}footer p{{margin:0 0 8px}}
@media(min-width:1440px){{.report-main{{padding-left:60px;padding-right:60px}}}}
@media(max-width:1080px){{.layout{{grid-template-columns:180px minmax(0,1fr)}}.rail{{padding:30px 16px}}.report-main{{padding:32px 30px 60px}}.hero-grid{{grid-template-columns:minmax(0,1fr) 210px;gap:22px}}h1{{font-size:30px}}}}
@media(max-width:780px){{.layout{{display:block}}.rail{{position:relative;height:auto;padding:18px 20px 12px;border-right:0;border-bottom:1px solid var(--line)}}.brand{{font-size:15px}}.brand small,.rail-title,.rail-foot{{display:none}}nav{{display:flex;gap:6px;overflow-x:auto;margin-top:12px;padding:2px 0 5px}}nav a{{flex:none;padding:6px 9px;border:1px solid var(--line);font-size:12px}}.report-main{{padding:24px 20px 48px}}.report-topline{{align-items:start;gap:10px;margin-bottom:23px}}.report-actions{{justify-content:flex-end}}.report-actions span{{display:none}}.kicker{{font-size:10px;letter-spacing:.06em}}.hero-grid{{grid-template-columns:minmax(0,1fr) 160px;gap:20px}}h1{{font-size:28px}}.report-subtitle{{font-size:13px}}.report-status{{margin-top:18px}}.audit-caption{{font-size:10px}}.executive-summary{{padding:20px}}.summary-heading small{{display:none}}.summary{{font-size:14px;margin-bottom:20px}}.scoreboard{{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.score strong{{font-size:21px}}.score span{{font-size:10px}}.dimension{{padding-top:32px}}h2{{font-size:19px}}.section-count{{font-size:10px}}.section-intro{{font-size:12px;margin-bottom:16px}}.insight{{padding:19px 20px}}h3{{font-size:18px}}.reasoning{{font-size:13px}}.evidence-card.with-image{{grid-template-columns:140px minmax(0,1fr)}}.evidence-copy{{padding:12px}}.audit-notes,.unknowns{{padding:20px}}}}
@media(max-width:520px){{.hero-grid{{grid-template-columns:1fr}}.report-cover{{max-width:100%;margin-top:2px}}.report-cover img{{max-height:205px;aspect-ratio:16/9}}.report-main{{padding:24px 16px 40px}}h1{{font-size:27px}}.report-subtitle{{margin-top:9px}}.report-cover figcaption{{font-size:10px}}.executive-summary,.insight{{padding:18px}}.section-heading>div{{gap:8px}}.section-heading{{gap:8px}}.dimension.featured h2::after{{margin-left:7px}}.evidence-card.with-image{{grid-template-columns:1fr}}.evidence-card img{{max-height:230px;aspect-ratio:16/9}}.claim-meta{{gap:8px 12px}}.summary{{line-height:1.8}}.source-metadata dl{{grid-template-columns:65px minmax(0,1fr)}}.findings{{padding:13px}}.findings-list li{{grid-template-columns:44px minmax(0,1fr);gap:8px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
@media print{{@page{{margin:16mm}}body{{background:#fff;color:#222;font-size:12px}}.layout{{display:block;width:100%;min-height:0}}.rail,.skip-link,.report-actions{{display:none}}.report-main{{max-width:none;padding:0}}.report-header{{margin-bottom:22px}}.report-topline{{margin-bottom:14px}}.hero-grid{{grid-template-columns:minmax(0,1fr) 170px}}h1{{font-size:26px}}.report-cover img{{max-height:110px}}.report-cover figcaption{{font-size:9px}}.executive-summary{{padding:18px;border-radius:0}}.summary{{font-size:12px}}.score strong{{font-size:20px}}.dimension{{padding-top:26px;break-before:auto}}h2{{font-size:18px;break-after:avoid}}h3{{font-size:16px;break-after:avoid}}.insight{{padding:16px;border-radius:0;break-inside:avoid}}.evidence-card{{break-inside:avoid}}.evidence-card.with-image{{grid-template-columns:120px minmax(0,1fr)}}.evidence-copy>p,.reasoning{{font-size:11px}}details::details-content{{content-visibility:visible!important;display:block!important}}details:not([open])>*:not(summary){{display:block!important}}.evidence-details>summary::after{{display:none}}.source-metadata:not([open])>dl{{display:grid!important}}.source-metadata a{{display:none}}.audit-notes,.unknowns{{break-inside:avoid;border-radius:0;padding:16px}}.report-status,.status{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}footer{{margin-top:24px;font-size:9px}}}}
</style></head><body><a class="skip-link" href="#report-main">跳到报告正文</a><div class="layout">
<aside class="rail"><div class="brand">AdVista<small>广告分析 / 证据审阅</small></div><p class="rail-title">报告目录</p><nav aria-label="报告目录">{"".join(navigation)}</nav><div class="rail-foot">基于视频证据生成<br>主张与推断均保留核查入口</div></aside>
<main class="report-main" id="report-main">
<header class="report-header"><div class="report-topline"><p class="kicker">ADVISTA / 广告分析报告</p><div class="report-actions"><a class="text-link" href="{markdown_download}" download="advista-analysis.md">下载文字版</a><span>打印 / PDF：Ctrl / ⌘ + P</span></div></div>
<div class="hero-grid{"" if cover else " no-cover"}"><div><h1>{html.escape(report_name)}</h1><p class="report-subtitle">广告卖点、表达方式与可核查证据</p>{source}<div class="report-status"><span class="status {status}">审计：{STATUS_LABELS[status]}</span><span class="audit-caption">通过表示证据检查通过，未经独立功效验证</span></div></div>{cover}</div></header>
<section class="executive-summary" id="executive-summary"><div class="summary-heading"><h2>执行摘要</h2><small>先读结论，再核查证据</small></div><p class="summary">{html.escape(analysis.executive_summary)}</p><div class="scoreboard" aria-label="审计统计"><div class="score"><strong>{audit.insight_count}</strong><span>分析结论</span></div><div class="score"><strong>{audit.passed_count}</strong><span>审计通过</span></div><div class="score review"><strong>{audit.review_count}</strong><span>建议复核</span></div><div class="score fail"><strong>{audit.failed_count}</strong><span>审计失败</span></div></div></section>
{"".join(sections)}{global_findings}{unknowns_block}
<footer><p>本报告仅基于提供的视频证据与来源记录。广告中的功效、健康、安全和结果声明未经独立事实验证；营销推断需要补充验证。</p><p>审计检查证据引用与风险，不替代人工审批。模型置信度不等于事实正确率。</p></footer>
</main></div></body></html>"""
