from __future__ import annotations

import base64
import html
from collections import defaultdict
from pathlib import Path

from ad_vista_agent.schemas import (
    CriticAudit,
    GroundedMarketingInsight,
    InsightAudit,
    MarketingAnalysis,
    MarketingDimension,
    ReportEvidence,
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


def render_markdown_report(
    analysis: MarketingAnalysis,
    audit: CriticAudit,
    *,
    source_name: str | None = None,
) -> str:
    audits = _audit_by_id(audit)
    grouped: dict[MarketingDimension, list[GroundedMarketingInsight]] = defaultdict(list)
    for insight in analysis.insights:
        grouped[insight.dimension].append(insight)
    lines = [
        f"# {source_name or analysis.subject} 广告分析报告",
        "",
        f"> 审计状态：**{STATUS_LABELS[audit.status.value]}** · 洞察 {audit.insight_count} 条 · "
        f"通过 {audit.passed_count} · 复核 {audit.review_count} · 失败 {audit.failed_count}",
        "",
        "## 执行摘要",
        "",
        analysis.executive_summary,
        "",
    ]
    for dimension in DIMENSION_LABELS:
        insights = grouped.get(dimension, [])
        if not insights:
            continue
        lines.extend([f"## {DIMENSION_LABELS[dimension]}", ""])
        for insight in insights:
            item_audit = audits[insight.insight_id]
            lines.extend(
                [
                    f"### {insight.claim}",
                    "",
                    f"- 洞察 ID：`{insight.insight_id}`",
                    f"- 审计：**{STATUS_LABELS[item_audit.status.value]}**",
                    f"- 认识论状态：`{insight.epistemic_status.value}`",
                    f"- 模型置信度：{insight.confidence:.2f}",
                    f"- 推理摘要：{insight.reasoning_summary}",
                    "",
                    "**证据**",
                    "",
                ]
            )
            lines.extend(_markdown_evidence(evidence) for evidence in item_audit.evidence)
            if item_audit.findings:
                lines.extend(["", "**审计发现**", ""])
                lines.extend(
                    f"- `{finding.severity.value}` / `{finding.code}`：{finding.message}"
                    for finding in item_audit.findings
                )
            lines.append("")
    lines.extend(["## 当前证据缺口", ""])
    lines.extend(f"- {value}" for value in analysis.unknowns)
    lines.extend(
        [
            "",
            "## 使用说明",
            "",
            "本报告仅基于 Evidence Ledger 中的自动转写与 OCR 证据。广告中的功效、健康、安全和结果声明均不代表独立事实验证。",
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


def _html_evidence(
    item: ReportEvidence,
    *,
    run_dir: Path,
    embed_images: bool,
    max_image_bytes: int,
) -> str:
    confidence = "未提供" if item.confidence is None else f"{item.confidence:.3f}"
    image = _image_data_uri(item, run_dir, max_image_bytes) if embed_images else None
    image_html = (
        f'<img loading="lazy" src="{image}" alt="{html.escape(item.reference_id)} 的证据帧">'
        if image
        else ""
    )
    return (
        '<article class="evidence-card">'
        f'{image_html}<div class="evidence-copy"><div class="evidence-label">'
        f"<code>{html.escape(item.reference_id)}</code>"
        f'<span class="time">{format_timestamp(item.start_ms)}</span></div>'
        f"<p>{html.escape(item.content)}</p>"
        f"<small>{html.escape(item.modality.value)} · 置信度 {confidence} · "
        f"聚合 {item.member_count} 条 · 来源 {html.escape(item.source_evidence_id)}</small></div></article>"
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
    audits = _audit_by_id(audit)
    grouped: dict[MarketingDimension, list[GroundedMarketingInsight]] = defaultdict(list)
    for insight in analysis.insights:
        grouped[insight.dimension].append(insight)
    sections: list[str] = []
    navigation: list[str] = []
    for dimension in DIMENSION_LABELS:
        insights = grouped.get(dimension, [])
        if not insights:
            continue
        anchor = _dimension_anchor(dimension)
        navigation.append(
            f'<a href="#{anchor}"><span class="nav-dot"></span>{DIMENSION_LABELS[dimension]}'
            f'<em>{len(insights)}</em></a>'
        )
        cards = []
        for insight in insights:
            item_audit = audits[insight.insight_id]
            evidence_html = "".join(
                _html_evidence(
                    item,
                    run_dir=run_dir,
                    embed_images=embed_images,
                    max_image_bytes=max_image_bytes,
                )
                for item in item_audit.evidence[:max_evidence_per_insight]
            )
            findings_html = "".join(
                f'<li class="{finding.severity.value}"><code>{html.escape(finding.code)}</code> '
                f"{html.escape(finding.message)}</li>"
                for finding in item_audit.findings
            )
            findings_block = (
                f'<div class="findings"><div class="subhead">审计发现</div><ul>{findings_html}</ul></div>'
                if findings_html
                else ""
            )
            evidence_block = (
                f'<div class="evidence-grid">{evidence_html}</div>'
                if evidence_html
                else '<div class="empty-state">该洞察暂无可展示的证据条目</div>'
            )
            cards.append(
                f'<article class="insight {item_audit.status.value}">'
                f'<div class="insight-head"><span class="insight-id">{html.escape(insight.insight_id)}</span>'
                f'<strong class="status {item_audit.status.value}">{STATUS_LABELS[item_audit.status.value]}</strong></div>'
                f"<h3>{html.escape(insight.claim)}</h3>"
                f'<p class="reasoning">{html.escape(insight.reasoning_summary)}</p>'
                f'<div class="meta"><span>{html.escape(insight.epistemic_status.value)}</span>'
                f"<span>模型置信度 {insight.confidence:.2f}</span><span>证据 {len(item_audit.evidence)} 条</span></div>"
                f'<div class="subhead">证据链</div>{evidence_block}{findings_block}</article>'
            )
        sections.append(
            f'<section class="dimension" id="{anchor}"><div class="section-heading">'
            f'<span class="section-index">{len(sections) + 1:02d}</span>'
            f'<h2>{DIMENSION_LABELS[dimension]}</h2><span class="section-count">{len(insights)} 条洞察</span>'
            f'</div>{"".join(cards)}</section>'
        )
    unknowns = "".join(f'<li><span class="unknown-mark">!</span>{html.escape(value)}</li>' for value in analysis.unknowns)
    unknowns_block = (
        f'<section class="unknowns" id="evidence-gaps"><div class="section-heading">'
        f'<span class="section-index">!</span><h2>当前证据缺口</h2></div><ul>{unknowns}</ul></section>'
        if unknowns
        else '<section class="unknowns empty" id="evidence-gaps"><div class="section-heading"><span class="section-index">+</span><h2>当前证据缺口</h2></div><p>未记录额外证据缺口。</p></section>'
    )
    report_name = source_name or analysis.subject
    status = audit.status.value
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report_name)} 广告分析报告</title>
<style>
:root{{--ink:#202522;--deep:#18362f;--paper:#f5f2eb;--paper-light:#fffdf8;--line:#d9d7cf;--muted:#6d756f;--orange:#bd5b38;--amber:#806c47;--red:#8b514a;--green:#466b5a}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.7 Inter,system-ui,-apple-system,"Noto Sans SC",sans-serif}}
.layout{{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}}.rail{{position:sticky;top:0;height:100vh;padding:30px 24px;background:var(--deep);color:#f8f3e8;display:flex;flex-direction:column}}
.brand{{font:700 14px/1.2 ui-monospace,monospace;letter-spacing:.08em}}.brand small{{display:block;margin-top:8px;color:#b8c7bd;font:12px/1.4 inherit;letter-spacing:0}}
.rail-title{{margin:52px 0 12px;color:#aebfb4;font-size:11px;font-weight:700;letter-spacing:.12em}}nav{{display:grid;gap:4px}}nav a{{display:flex;align-items:center;gap:9px;padding:8px 4px;color:#e3e9df;text-decoration:none;font-size:13px;border-left:1px solid transparent}}nav a:hover{{color:#fff;border-left-color:var(--orange)}}nav em{{margin-left:auto;color:#9eb2a5;font-style:normal;font-size:11px}}.nav-dot{{width:6px;height:6px;border-radius:50%;background:#879f91}}
.rail-foot{{margin-top:auto;color:#9eb2a5;font-size:11px;line-height:1.6}}main{{min-width:0;max-width:1120px;width:100%;padding:58px clamp(24px,5vw,76px) 90px}}header{{padding-bottom:38px;border-bottom:1px solid var(--line)}}.kicker{{margin:0 0 20px;color:var(--orange);font:700 11px/1 ui-monospace,monospace;letter-spacing:.16em}}
h1{{max-width:850px;margin:0 0 20px;font-size:clamp(2rem,4vw,3.8rem);line-height:1.1;font-weight:750;letter-spacing:0}}.summary{{max-width:760px;margin:0;color:#526159;font-size:1.04rem}}.header-row{{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-top:30px}}.status{{display:inline-flex;align-items:center;gap:7px;padding:5px 10px;border-radius:3px;font-size:12px;font-weight:700;white-space:nowrap}}.status::before{{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}}.status.pass{{color:var(--green);background:#e8eee9}}.status.review{{color:var(--amber);background:#eeece5}}.status.fail{{color:var(--red);background:#eee7e4}}
.scoreboard{{display:grid;grid-template-columns:repeat(4,minmax(90px,1fr));gap:1px;max-width:640px;border:1px solid var(--line);background:var(--line)}}.score{{padding:12px 14px;background:var(--paper-light)}}.score strong{{display:block;font-size:1.25rem;line-height:1.2}}.score span{{display:block;margin-top:3px;color:var(--muted);font-size:11px}}
.dimension{{padding-top:54px;scroll-margin-top:18px}}.section-heading{{display:flex;align-items:baseline;gap:12px;margin-bottom:17px}}h2{{margin:0;font-size:1.35rem;line-height:1.2}}.section-index{{color:var(--orange);font:700 12px ui-monospace,monospace}}.section-count{{margin-left:auto;color:var(--muted);font-size:12px}}
.insight{{position:relative;margin:0 0 14px;padding:22px 24px 25px;background:var(--paper-light);border:1px solid var(--line);border-left:4px solid var(--green)}}.insight.review{{border-left-color:var(--amber)}}.insight.fail{{border-left-color:var(--red)}}.insight-head{{display:flex;align-items:center;justify-content:space-between;gap:14px}}.insight-id{{color:var(--muted);font:600 11px ui-monospace,monospace;letter-spacing:.04em}}h3{{margin:16px 0 7px;font-size:1.12rem;line-height:1.45}}.reasoning{{margin:0;color:#53635b}}.meta{{display:flex;flex-wrap:wrap;gap:10px 18px;margin-top:15px;color:var(--muted);font-size:12px}}.meta span+span{{position:relative}}.meta span+span::before{{content:"/";position:absolute;left:-11px;color:#b4b5aa}}
.subhead{{margin-top:21px;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.1em}}.evidence-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;margin-top:9px}}.evidence-card{{overflow:hidden;border:1px solid var(--line);background:#f7f4ed}}.evidence-card img{{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:#e5e1d7}}.evidence-copy{{padding:11px 12px}}.evidence-label{{display:flex;justify-content:space-between;gap:8px}}code{{font-family:ui-monospace,monospace;font-size:.8em}}.time{{color:var(--orange);font:600 .76em ui-monospace,monospace}}.evidence-card p{{margin:7px 0 8px;line-height:1.5}}small{{color:var(--muted);font-size:11px}}.empty-state{{margin-top:9px;padding:12px;background:#f1ede4;color:var(--muted);font-size:12px}}
 .findings{{margin-top:18px;padding-top:14px;border-top:1px dashed var(--line)}}.findings .subhead{{margin:0}}.findings ul{{margin:8px 0 0;padding-left:20px;color:var(--muted);font-size:12px}}.findings .warning{{color:var(--amber)}}.findings .error{{color:var(--red)}}.unknowns{{margin-top:56px;padding:22px 24px;background:#eee8db;border-top:2px solid var(--orange);scroll-margin-top:18px}}.unknowns ul{{display:grid;gap:8px;margin:0;padding:0;list-style:none;color:#53635b;font-size:13px}}.unknowns li{{display:flex;gap:10px}}.unknown-mark{{display:inline-grid;place-items:center;flex:none;width:18px;height:18px;border-radius:50%;background:#e6c6a7;color:#7d482d;font:700 11px/1 ui-monospace,monospace}}.unknowns.empty p{{margin:0;color:var(--muted);font-size:13px}}footer{{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}
@media(max-width:780px){{.layout{{display:block}}.rail{{position:relative;height:auto;padding:20px 18px 16px}}.rail-title{{margin:22px 0 8px}}nav{{display:flex;overflow:auto;gap:8px;padding-bottom:3px}}nav a{{flex:none;padding:6px 8px;border:1px solid #3e6358;border-radius:999px;font-size:12px}}nav a:hover{{border-color:var(--orange)}}.rail-foot{{display:none}}main{{padding:34px 18px 58px}}.header-row{{display:block;margin-top:25px}}.scoreboard{{margin-top:18px;grid-template-columns:repeat(2,1fr);max-width:none}}.score:first-child{{grid-column:span 2}}.dimension{{padding-top:40px}}.insight{{padding:18px 16px 20px}}}}
@media print{{.layout{{display:block}}.rail{{display:none}}main{{max-width:none;padding:0}}.dimension{{break-before:page}}.insight{{break-inside:avoid}}.evidence-card{{break-inside:avoid}}}}
</style></head><body><div class="layout"><aside class="rail"><div class="brand">ADVISTA<small>证据分析报告</small></div><div class="rail-title">报告目录</div><nav>{''.join(navigation)}<a href="#evidence-gaps"><span class="nav-dot"></span>证据缺口</a></nav><div class="rail-foot">自动生成 · Evidence Ledger<br>所有声明需结合证据审阅</div></aside><main>
<header><p class="kicker">ADVERTISING EVIDENCE REVIEW / 07</p><h1>{html.escape(report_name)}</h1><p class="summary">{html.escape(analysis.executive_summary)}</p><div class="header-row"><span class="status {status}">{STATUS_LABELS[status]}</span><div class="scoreboard"><div class="score"><strong>{audit.insight_count}</strong><span>洞察总数</span></div><div class="score"><strong>{audit.passed_count}</strong><span>通过</span></div><div class="score"><strong>{audit.review_count}</strong><span>建议复核</span></div><div class="score"><strong>{audit.failed_count}</strong><span>失败</span></div></div></div></header>
{''.join(sections)}
{unknowns_block}
<footer>本报告仅基于自动转写与 OCR Evidence Ledger；广告功效、健康、安全与结果声明未经独立事实验证。</footer>
</main></div></body></html>"""
