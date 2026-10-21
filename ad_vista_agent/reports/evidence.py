from __future__ import annotations

import base64
import html
from pathlib import Path

from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.runtime.fingerprint import sha256_file
from ad_vista_agent.schemas import AdAsset, Evidence, EvidenceCluster, Keyframe


def _safe_keyframe_path(run_dir: Path, keyframe: Keyframe) -> Path | None:
    path = (run_dir / keyframe.artifact_path).resolve()
    if run_dir.resolve() not in path.parents or not path.is_file():
        return None
    if sha256_file(path) != keyframe.artifact_sha256:
        return None
    return path


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
    keyframes_path = run_dir / "timeline" / "keyframes.jsonl"
    keyframes = [
        Keyframe.model_validate_json(line)
        for line in keyframes_path.read_text(encoding="utf-8").splitlines()
        if line
    ] if keyframes_path.is_file() else []
    title = f"{Path(asset.filename).stem} 证据提取文档"
    rows = []
    markdown_rows = []
    ocr_by_keyframe: dict[str, list[str]] = {}
    for item in evidence:
        confidence = f"{item.confidence:.2f}" if item.confidence is not None else "未提供"
        markdown_content = item.content.replace("|", "\\|")
        keyframe_id = item.metadata.get("keyframe_id")
        if item.modality.value == "ocr" and isinstance(keyframe_id, str):
            ocr_by_keyframe.setdefault(keyframe_id, []).append(item.content)
        rows.append(
            f"<tr><td>{item.start_ms / 1000:.1f}s</td><td>{html.escape(item.modality.value)}</td>"
            f"<td>{html.escape(item.content)}</td><td>{confidence}</td></tr>"
        )
        markdown_rows.append(
            f"| {item.start_ms / 1000:.1f}s | {item.modality.value} | "
            f"{markdown_content} | {confidence} |"
        )
    frame_cards = []
    markdown_frames = []
    embedded_keyframes = 0
    for keyframe in keyframes:
        frame_path = _safe_keyframe_path(run_dir, keyframe)
        if frame_path is None:
            continue
        media_type = "image/png" if frame_path.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        captions = ocr_by_keyframe.get(keyframe.keyframe_id, [])
        caption = "\n".join(captions) if captions else "该关键帧未提取到 OCR 文字"
        frame_cards.append(
            f'<figure><img src="data:{media_type};base64,{encoded}" '
            f'alt="{html.escape(keyframe.keyframe_id)} 视频关键帧">'
            f"<figcaption><strong>{keyframe.timestamp_ms / 1000:.1f}s · "
            f"{html.escape(keyframe.keyframe_id)}</strong><span>{html.escape(caption)}</span>"
            f"</figcaption></figure>"
        )
        markdown_caption = caption.replace("\n", " / ")
        markdown_frames.append(
            f"- **{keyframe.timestamp_ms / 1000:.1f}s · {keyframe.keyframe_id}**  "
            f"`{keyframe.artifact_path.as_posix()}`  \n  OCR：{markdown_caption}"
        )
        embedded_keyframes += 1
    html_text = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{--ink:#202522;--muted:#69716b;--paper:#f6f4ee;--card:#fffefa;--line:#d9d8d0;--accent:#bd5b38}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Noto Sans SC",sans-serif}}main{{width:min(1040px,100%);margin:auto;padding:clamp(28px,6vw,72px) clamp(18px,5vw,64px)}}.kicker{{color:var(--accent);font:700 11px ui-monospace,monospace;letter-spacing:.14em}}h1{{margin:14px 0 10px;font-size:clamp(2rem,5vw,3.4rem);letter-spacing:-.04em}}h2{{margin:42px 0 14px;font-size:1.35rem}}.intro{{color:var(--muted);max-width:720px}}.stats{{display:flex;flex-wrap:wrap;gap:28px;margin:34px 0;padding:16px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.stats strong{{display:block;font-size:1.25rem}}.stats span{{color:var(--muted);font-size:12px}}.frames{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}}figure{{margin:0;background:var(--card);border:1px solid var(--line)}}figure img{{display:block;width:100%;aspect-ratio:9/16;object-fit:contain;background:#171918}}figcaption{{display:grid;gap:5px;padding:11px 12px}}figcaption strong{{font-size:12px}}figcaption span{{color:var(--muted);font-size:12px;white-space:pre-wrap}}.table-wrap{{overflow:auto;background:var(--card);border:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;min-width:650px}}th,td{{padding:13px 15px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:pre-wrap}}th{{color:var(--muted);font-size:12px;font-weight:650;background:#f0eee7}}tr:last-child td{{border-bottom:0}}footer{{margin-top:34px;color:var(--muted);font-size:12px}}@media(max-width:560px){{.frames{{grid-template-columns:1fr 1fr}}}}</style></head><body><main><div class="kicker">ADVISTA / MULTIMODAL EVIDENCE</div><h1>{html.escape(title)}</h1><p class="intro">按时间顺序整理视频关键帧、语音识别（ASR）和画面文字识别（OCR）。图片与文字均来自本次视频处理制品，不对未被原始证据支持的内容进行推断。</p><div class="stats"><div><strong>{embedded_keyframes}</strong><span>关键帧</span></div><div><strong>{len(evidence)}</strong><span>ASR / OCR 原始证据</span></div><div><strong>{len(clusters)}</strong><span>文字聚合</span></div><div><strong>{asset.metadata.duration_ms / 1000:.1f}s</strong><span>视频时长</span></div></div><h2>视频关键帧</h2><div class="frames">{''.join(frame_cards) or '<p>未提取到可用关键帧。</p>'}</div><h2>ASR 与 OCR</h2><div class="table-wrap"><table><thead><tr><th>时间</th><th>来源</th><th>提取内容</th><th>置信度</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="4">未提取到可用文字证据</td></tr>'}</tbody></table></div><footer>证据文档包含自动抽取的关键帧、ASR 与 OCR；重要结论仍应回看原视频核验。</footer></main></body></html>'''
    markdown = (
        f"# {title}\n\n> 关键帧 {embedded_keyframes} 张 · ASR / OCR 原始证据 {len(evidence)} 条 · 文字聚合 {len(clusters)} 条 · "
        f"视频时长 {asset.metadata.duration_ms / 1000:.1f}s\n\n"
        "## 视频关键帧\n\n"
        + ("\n".join(markdown_frames) if markdown_frames else "未提取到可用关键帧。")
        + "\n\n## ASR 与 OCR\n\n"
        "| 时间 | 来源 | 提取内容 | 置信度 |\n| --- | --- | --- | --- |\n"
        + "\n".join(markdown_rows)
        + "\n\n> 证据文档包含自动抽取的关键帧、ASR 与 OCR；重要结论仍应回看原视频核验。\n"
    )
    target = run_dir / "ledger"
    (target / "evidence.html").write_text(html_text, encoding="utf-8")
    (target / "evidence.md").write_text(markdown, encoding="utf-8")
    return {
        "html": str(target / "evidence.html"),
        "markdown": str(target / "evidence.md"),
        "evidence_count": len(evidence),
        "keyframe_count": embedded_keyframes,
    }
