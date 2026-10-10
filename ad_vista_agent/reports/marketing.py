"""确定性营销报告渲染：模型只产出结构化数据，HTML 由程序生成。"""

from __future__ import annotations

import base64
import html
import json
from typing import Any

from ad_vista_agent.schemas import MarketingStrategy
from ad_vista_agent.skills import (
    REPORT_FRONTEND_SKILL,
    report_frontend_skill_fingerprint,
    report_frontend_summary,
)

from .render import format_timestamp

SECTIONS = (
    ("communication_strategy", "传播策略"),
    ("content_directions", "内容方向"),
    ("channel_recommendations", "渠道测试建议"),
    ("conversion_recommendations", "转化与验证建议"),
)
QUALITY_LABELS = {
    "empty_or_punctuation": "空白或纯标点",
    "recognition_placeholder": "识别占位符",
    "isolated_number": "缺少上下文的孤立数字",
    "repeated_numeric_sequence": "异常重复数字序列",
    "invalid_date": "无效日期",
}


def _source_label(row: dict[str, Any]) -> str:
    return "ASR" if row.get("modality") == "speech" else "OCR"


def render_marketing_markdown(
    strategy: MarketingStrategy,
    *,
    request_goal: str,
    sources: list[dict[str, Any]],
) -> str:
    lines = [
        "# 营销报告",
        "",
        f"> 本轮任务：{request_goal}",
        "> 依据 OCR / ASR 文本生成；广告主张不等于独立验证事实，渠道与投放建议需要测试。",
        "",
        "## 定位与传播主张",
        "",
        f"- 定位：{strategy.positioning}",
        f"- 受众线索：{strategy.target_audience}",
        f"- 核心主张：{strategy.core_message}",
        "",
    ]
    for field, title in SECTIONS:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {item}" for item in getattr(strategy, field))
        lines.append("")
    lines.extend(["## 待验证假设", ""])
    lines.extend(f"- {item}" for item in strategy.hypotheses or ["未提出额外假设。"])
    lines.extend(["", "## 当前证据缺口", ""])
    lines.extend(f"- {item}" for item in strategy.unsupported_points or ["仍需独立验证广告主张与实际营销效果。"])
    lines.extend(["", "## 引用来源", ""])
    cited = set(strategy.evidence_refs)
    for row in sources:
        if row["reference_id"] in cited:
            lines.extend([
                f"### {row['reference_id']} · {_source_label(row)} · {format_timestamp(int(row.get('start_ms', 0)))}",
                "",
                str(row.get("content", "")),
                "",
            ])
    if not cited:
        lines.extend(["当前未引用能够支撑产品营销结论的文本证据。", ""])
    lines.extend(["<details>", "<summary>全部 OCR / ASR 原始文本及质量提示</summary>", ""])
    for row in sources:
        issue = row.get("quality_issue")
        label = QUALITY_LABELS.get(str(issue), str(issue)) if issue else "识别文本；相关性需结合上下文判断"
        lines.extend([
            f"### {row['reference_id']} · {_source_label(row)} · {label}",
            "",
            # 原文可能含 HTML，避免 Markdown 预览器将其当作标签执行。
            html.escape(str(row.get("content", ""))),
            "",
        ])
    lines.extend(["</details>", ""])
    return "\n".join(lines)


def render_marketing_html(
    strategy: MarketingStrategy,
    *,
    request_goal: str,
    sources: list[dict[str, Any]],
) -> str:
    escape = html.escape
    values = strategy.model_dump(mode="json")
    markdown = render_marketing_markdown(strategy, request_goal=request_goal, sources=sources)
    markdown_data = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    source_data = base64.b64encode(json.dumps(sources, ensure_ascii=False).encode("utf-8")).decode("ascii")
    title = request_goal.strip()[:80] or "营销报告"
    sections = "".join(
        f'<section id="{field}"><h2>{title}</h2><ol>'
        + "".join(f"<li>{escape(item)}</li>" for item in values[field])
        + "</ol></section>"
        for field, title in SECTIONS
    )
    cards = "".join(
        f'<article class="summary-card"><h2>{title}</h2><p>{escape(str(values[field]))}</p></article>'
        for field, title in (
            ("positioning", "产品定位"),
            ("target_audience", "受众线索"),
            ("core_message", "核心传播主张"),
        )
    )
    cited = set(strategy.evidence_refs)

    def source_card(row: dict[str, Any]) -> str:
        issue = row.get("quality_issue")
        label = QUALITY_LABELS.get(str(issue), str(issue)) if issue else "识别文本；相关性需核对"
        tag = "已引用" if row["reference_id"] in cited else label
        return (
            '<article class="source-card">'
            f'<h3>{escape(str(row["reference_id"]))} · {_source_label(row)} · '
            f'{format_timestamp(int(row.get("start_ms", 0)))}</h3>'
            f'<span class="source-tag">{escape(tag)}</span>'
            f'<p>{escape(str(row.get("content", "")))}</p></article>'
        )

    cited_html = "".join(source_card(row) for row in sources if row["reference_id"] in cited)
    if not cited_html:
        cited_html = '<p class="empty">当前没有引用能够支撑产品营销结论的 OCR / ASR 文本证据。</p>'
    source_html = "".join(source_card(row) for row in sources)
    hypotheses = "".join(f"<li>{escape(item)}</li>" for item in strategy.hypotheses)
    gaps = "".join(f"<li>{escape(item)}</li>" for item in strategy.unsupported_points)
    status = "需复核与补充证据" if strategy.unsupported_points or not cited else "已有文本引用 · 建议仍需验证"
    nav = "".join(f'<a href="#{field}">{title}</a>' for field, title in SECTIONS)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="report-skill" content="{escape(REPORT_FRONTEND_SKILL)}">
<meta name="report-skill-sha256" content="{report_frontend_skill_fingerprint()}">
<meta name="report-skill-description" content="{escape(report_frontend_summary(), quote=True)}">
<title>{escape(title)}</title><style>
:root{{color-scheme:light;--ink:#202c38;--muted:#657586;--line:#dce4eb;--accent:#1c5c68;--paper:#fff;--wash:#f2f5f7}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--wash);color:var(--ink);font:15px/1.8 system-ui,-apple-system,"Microsoft YaHei",sans-serif}}a{{color:var(--accent)}}.skip{{position:absolute;left:-10000px}}.skip:focus{{left:16px;top:12px;background:white;padding:8px;z-index:2}}.layout{{display:grid;grid-template-columns:225px minmax(0,1fr);max-width:1480px;margin:auto;min-height:100vh}}aside{{position:sticky;top:0;align-self:start;height:100vh;padding:40px 24px;background:#eaf0f2;border-right:1px solid var(--line)}}.brand{{font-size:21px;font-weight:750;letter-spacing:.04em}}.eyebrow{{color:var(--muted);font-size:12px;letter-spacing:.12em}}nav{{display:flex;flex-direction:column;gap:6px;margin-top:30px}}nav a{{text-decoration:none;padding:8px;border-radius:7px;font-size:13px}}nav a:hover,nav a:focus{{background:white}}main{{padding:42px 52px 60px;min-width:0;background:var(--paper)}}header{{padding-bottom:28px;border-bottom:1px solid var(--line)}}h1{{margin:6px 0 12px;font-size:38px;line-height:1.25}}h2{{font-size:22px;line-height:1.4;margin:0 0 17px}}h3{{font-size:13px;margin:0 0 7px}}p{{margin:0 0 12px;white-space:pre-wrap;overflow-wrap:anywhere}}.goal{{color:var(--muted)}}.badge{{display:inline-block;padding:5px 12px;border:1px solid #ead7a9;background:#fff8e6;border-radius:20px;color:#7d5d15;font-size:12px}}.stats{{display:flex;gap:24px;color:var(--muted);font-size:13px;margin-top:17px}}.summary-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin:30px 0}}.summary-card{{padding:21px;background:#f6f8f9;border:1px solid var(--line);border-radius:12px;min-width:0}}.summary-card h2{{font-size:14px;color:var(--accent);margin-bottom:10px}}.summary-card p{{font-size:14px;margin:0}}section{{padding-top:30px}}ol,ul{{margin:0;padding-left:23px}}li{{padding:5px 0;white-space:pre-wrap;overflow-wrap:anywhere}}.note{{padding:22px 25px;border-left:4px solid #c89c43;background:#fffbf2;border-radius:8px;margin-top:30px}}.note h2{{font-size:20px}}.source-card{{padding:18px;border:1px solid var(--line);border-radius:9px;margin:12px 0}}.source-card p{{font-size:13px;margin-top:10px}}.source-tag{{font-size:11px;color:var(--muted)}}details{{margin-top:20px;border:1px solid var(--line);border-radius:9px;padding:16px}}summary{{cursor:pointer;font-size:13px;color:var(--accent)}}.empty{{color:var(--muted)}}footer{{margin-top:40px;border-top:1px solid var(--line);padding-top:18px;font-size:12px;color:var(--muted)}}
@media(max-width:1000px){{.layout{{grid-template-columns:185px minmax(0,1fr)}}main{{padding:30px}}aside{{padding:30px 16px}}.summary-grid{{grid-template-columns:1fr}}}}
@media(max-width:680px){{.layout{{display:block}}aside{{position:relative;height:auto;padding:18px 20px;border-right:0;border-bottom:1px solid var(--line)}}nav{{flex-direction:row;overflow-x:auto;margin-top:12px}}nav a{{flex:none}}main{{padding:26px 18px}}h1{{font-size:30px}}.stats{{gap:14px;flex-wrap:wrap}}.summary-grid{{margin:24px 0}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}}}
@media print{{@page{{margin:16mm}}body{{background:white;font-size:12px}}.layout{{display:block}}aside,.skip{{display:none}}main{{padding:0}}h1{{font-size:28px}}h2{{break-after:avoid}}.summary-card,.source-card{{break-inside:avoid}}.badge{{print-color-adjust:exact}}details::details-content{{content-visibility:visible!important;display:block!important}}details:not([open])>*:not(summary){{display:block!important}}footer{{font-size:10px}}}}
</style></head><body><a class="skip" href="#main">跳到报告正文</a><div class="layout"><aside><div class="brand">AdVista</div><div class="eyebrow">MARKETING REPORT</div><nav><a href="#overview">定位与主张</a>{nav}<a href="#hypotheses">待验证假设</a><a href="#gaps">证据缺口</a><a href="#sources">引用与原文</a></nav></aside>
<main id="main"><header><div class="eyebrow">OCR / ASR · 营销报告</div><h1>{escape(title)}</h1><p class="goal">本轮任务：{escape(request_goal)}</p><span class="badge">{status}</span><div class="stats"><span>文本引用 {len(cited)} 条</span><span>待验证假设 {len(strategy.hypotheses)} 条</span><span>证据缺口 {len(strategy.unsupported_points)} 项</span></div><p class="downloads"><a download="marketing.md" href="data:text/markdown;charset=utf-8;base64,{markdown_data}">下载文字报告</a> · <a download="sources.json" href="data:application/json;charset=utf-8;base64,{source_data}">下载来源记录</a></p></header>
<div id="overview" class="summary-grid">{cards}</div>{sections}
<section id="hypotheses"><h2>待验证假设</h2><ul>{hypotheses or '<li>未提出额外假设。</li>'}</ul></section>
<section id="gaps" class="note"><h2>当前证据缺口</h2><ul>{gaps or '<li>仍需独立验证广告主张与实际营销效果。</li>'}</ul></section>
<section id="sources"><h2>引用与 OCR / ASR 原文</h2><p class="empty">以下文本是识别结果。未引用文本、背景音轨和异常 OCR 不能作为产品材质、功效或受众年龄的证明。</p>{cited_html}<details><summary>查看全部原始文本及质量提示（{len(sources)} 条）</summary>{source_html or '<p>未取得文本识别结果。</p>'}</details></section>
<footer>本报告以当前视频的 OCR / ASR 为依据；广告声明未经独立验证，传播、渠道和转化建议需要通过测试确认。原始识别记录保留，质量提示仅用于控制引用。</footer></main></div></body></html>'''
