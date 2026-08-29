from __future__ import annotations

import html
from pathlib import Path

from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import AdAsset, Evidence, EvidenceCluster


def build_evidence_document(run_dir: Path) -> dict[str, str | int]:
    store = ArtifactStore(run_dir.parent.parent)
    asset = AdAsset.model_validate(store.read_json(run_dir / "asset.json"))
    evidence = [
        Evidence.model_validate_json(line)
        for line in (run_dir / "ledger" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    clusters = [
        EvidenceCluster.model_validate_json(line)
        for line in (run_dir / "ledger" / "clusters.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    title = f"{Path(asset.filename).stem} 证据提取文档"
    rows = []
    markdown_rows = []
    for item in evidence:
        confidence = f"{item.confidence:.2f}" if item.confidence is not None else "未提供"
        rows.append(
            f"<tr><td>{item.start_ms / 1000:.1f}s</td><td>{html.escape(item.modality.value)}</td>"
            f"<td>{html.escape(item.content)}</td><td>{confidence}</td></tr>"
        )
        markdown_rows.append(
            f"| {item.start_ms / 1000:.1f}s | {item.modality.value} | "
            f"{item.content.replace('|', '\\|')} | {confidence} |"
        )
    html_text = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{--ink:#202522;--muted:#69716b;--paper:#f6f4ee;--card:#fffefa;--line:#d9d8d0;--accent:#bd5b38}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Noto Sans SC",sans-serif}}main{{width:min(1040px,100%);margin:auto;padding:clamp(28px,6vw,72px) clamp(18px,5vw,64px)}}.kicker{{color:var(--accent);font:700 11px ui-monospace,monospace;letter-spacing:.14em}}h1{{margin:14px 0 10px;font-size:clamp(2rem,5vw,3.4rem);letter-spacing:-.04em}}.intro{{color:var(--muted);max-width:680px}}.stats{{display:flex;gap:28px;margin:34px 0;padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.stats strong{{display:block;font-size:1.25rem}}.stats span{{color:var(--muted);font-size:12px}}.table-wrap{{overflow:auto;background:var(--card);border:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;min-width:650px}}th,td{{padding:13px 15px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:12px;font-weight:650;background:#f0eee7}}tr:last-child td{{border-bottom:0}}footer{{margin-top:34px;color:var(--muted);font-size:12px}}</style></head><body><main><div class="kicker">ADVISTA / EVIDENCE EXTRACTION</div><h1>{html.escape(title)}</h1><p class="intro">按时间顺序整理视频中可直接定位的语音与画面文字证据。此文档不对未被原始证据支持的内容进行推断。</p><div class="stats"><div><strong>{len(evidence)}</strong><span>原始证据</span></div><div><strong>{len(clusters)}</strong><span>文字聚合</span></div><div><strong>{asset.metadata.duration_ms / 1000:.1f}s</strong><span>视频时长</span></div></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>来源</th><th>提取内容</th><th>置信度</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="4">未提取到可用证据</td></tr>'}</tbody></table></div><footer>证据提取结果仅反映自动转写与 OCR 输出，重要结论请回看原视频核验。</footer></main></body></html>'''
    markdown = (
        f"# {title}\n\n> 原始证据 {len(evidence)} 条 · 文字聚合 {len(clusters)} 条 · "
        f"视频时长 {asset.metadata.duration_ms / 1000:.1f}s\n\n"
        "| 时间 | 来源 | 提取内容 | 置信度 |\n| --- | --- | --- | --- |\n"
        + "\n".join(markdown_rows)
        + "\n\n> 证据提取结果仅反映自动转写与 OCR 输出，重要结论请回看原视频核验。\n"
    )
    target = run_dir / "ledger"
    (target / "evidence.html").write_text(html_text, encoding="utf-8")
    (target / "evidence.md").write_text(markdown, encoding="utf-8")
    return {"html": str(target / "evidence.html"), "markdown": str(target / "evidence.md"), "evidence_count": len(evidence)}
