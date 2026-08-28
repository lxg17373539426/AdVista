from __future__ import annotations

import base64
import json
import mimetypes
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from ad_vista_agent.config import Settings
from ad_vista_agent.creative.builder import build_creative
from ad_vista_agent.reports.builder import build_report
from ad_vista_agent.memory import ConversationStore
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import (
    AgentSessionState,
    ConversationAnswer,
    Evidence,
    EvidenceCluster,
    MarketingAnalysis,
    Keyframe,
    CreativePackage,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def conversation_db(settings: Settings) -> Path:
    return settings.paths.output_root / "memory" / "conversations.sqlite3"


def _load_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(model.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid conversation source at {path}:{line_number}") from exc
    return rows


def _session_state(run_dir: Path) -> AgentSessionState:
    path = run_dir / "agent" / "session.json"
    if not path.is_file():
        raise FileNotFoundError(f"Agent session is missing: {path}")
    return AgentSessionState.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _resolve_session(
    identifier: str, settings: Settings
) -> tuple[Path, AgentSessionState]:
    store = ArtifactStore(settings.paths.output_root)
    if identifier.startswith("exec_"):
        from .core import load_agent_session

        _, run_dir, state = load_agent_session(identifier, settings)
        return run_dir, state
    run_dir = store.run_dir(identifier)
    return run_dir, _session_state(run_dir)


def _context(
    run_dir: Path, artifact_root: Path | None = None
) -> tuple[dict[str, Any], set[str]]:
    ledger_dir = run_dir / "ledger"
    evidence_path = ledger_dir / "evidence.jsonl"
    clusters_path = ledger_dir / "clusters.jsonl"
    analysis_path = (artifact_root or run_dir) / "insights" / "analysis.json"
    missing = [path for path in (evidence_path, clusters_path, analysis_path) if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Chat requires existing Ledger and insight artifacts; missing: {names}")
    evidence = _load_jsonl(evidence_path, Evidence)
    clusters = _load_jsonl(clusters_path, EvidenceCluster)
    analysis = MarketingAnalysis.model_validate(json.loads(analysis_path.read_text(encoding="utf-8")))
    speech = [
        {
            "id": item.evidence_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "content": item.content,
            "confidence": item.confidence,
        }
        for item in evidence
        if item.modality.value == "speech"
    ]
    ocr_clusters = [
        {
            "id": item.cluster_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "content": item.canonical_content,
            "confidence": item.confidence,
        }
        for item in clusters
    ]
    allowed = {item["id"] for item in speech} | {item["id"] for item in ocr_clusters}
    keyframes_path = run_dir / "timeline" / "keyframes.jsonl"
    keyframes = _load_jsonl(keyframes_path, Keyframe) if keyframes_path.is_file() else []
    visual_frames = [
        {
            "id": item.keyframe_id,
            "shot_id": item.shot_id,
            "timestamp_ms": item.timestamp_ms,
            "artifact_path": str(item.artifact_path),
        }
        for item in keyframes
    ]
    allowed.update(item["id"] for item in visual_frames)
    return {
        "asset_id": analysis.asset_id,
        "speech_evidence": speech,
        "ocr_clusters": ocr_clusters,
        "validated_insights": [
            {
                "id": item.insight_id,
                "dimension": item.dimension.value,
                "claim": item.claim,
                "evidence_refs": item.evidence_refs,
                "epistemic_status": item.epistemic_status.value,
            }
            for item in analysis.insights
        ],
        "unknowns": analysis.unknowns,
        "visual_keyframes": visual_frames,
    }, allowed


def _resolved_citations(
    run_dir: Path, identifier: str, references: list[str]
) -> list[dict[str, Any]]:
    if not references:
        return []
    evidence_path = run_dir / "ledger" / "evidence.jsonl"
    clusters_path = run_dir / "ledger" / "clusters.jsonl"
    keyframes_path = run_dir / "timeline" / "keyframes.jsonl"
    evidence = (
        {item.evidence_id: item for item in _load_jsonl(evidence_path, Evidence)}
        if evidence_path.is_file()
        else {}
    )
    clusters = (
        {item.cluster_id: item for item in _load_jsonl(clusters_path, EvidenceCluster)}
        if clusters_path.is_file()
        else {}
    )
    keyframes = (
        {item.keyframe_id: item for item in _load_jsonl(keyframes_path, Keyframe)}
        if keyframes_path.is_file()
        else {}
    )
    resolved: list[dict[str, Any]] = []
    for reference in references:
        if reference in clusters:
            item = clusters[reference]
            resolved.append(
                {
                    "type": "ocr",
                    "label": "画面文字",
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "excerpt": item.canonical_content,
                    "confidence": item.confidence,
                }
            )
        elif reference in evidence:
            item = evidence[reference]
            resolved.append(
                {
                    "type": item.modality.value,
                    "label": "语音" if item.modality.value == "speech" else "画面证据",
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "excerpt": item.content,
                    "confidence": item.confidence,
                }
            )
        elif reference in keyframes:
            item = keyframes[reference]
            resolved.append(
                {
                    "type": "keyframe",
                    "label": "关键画面",
                    "start_ms": item.timestamp_ms,
                    "end_ms": item.timestamp_ms,
                    "excerpt": f"镜头 {item.shot_id}",
                    "thumbnail_url": f"/api/runs/{identifier}/keyframes/{reference}",
                }
            )
        else:
            resolved.append(
                {
                    "type": "unavailable",
                    "label": "证据不可用",
                    "excerpt": "该历史引用已无法定位。",
                }
            )
    return resolved


def validate_conversation_answer(answer: ConversationAnswer, allowed_refs: set[str]) -> None:
    unknown = set(answer.evidence_refs).difference(allowed_refs)
    if unknown:
        raise ValueError(f"Chat answer cites unknown evidence: {', '.join(sorted(unknown))}")
    if answer.epistemic_status in {"grounded", "visual"} and not answer.evidence_refs:
        raise ValueError("Grounded chat answer must cite at least one Evidence ID")
    if answer.epistemic_status == "visual" and any(
        not ref.startswith("kf_") for ref in answer.evidence_refs
    ):
        raise ValueError("Visual chat answer must cite only presented keyframes")
    if answer.epistemic_status == "conversational" and answer.evidence_refs:
        raise ValueError("Conversational chat answer must not cite evidence")
    if answer.epistemic_status == "unknown":
        if answer.evidence_refs:
            raise ValueError("Unknown chat answer must not cite evidence")
        if not answer.answer.startswith("当前证据不足"):
            raise ValueError("Unknown chat answer must begin with 当前证据不足")


def normalize_conversation_answer(answer: ConversationAnswer) -> ConversationAnswer:
    if answer.epistemic_status == "unknown" and answer.evidence_refs:
        answer = answer.model_copy(update={"evidence_refs": []})
    if answer.epistemic_status in {"grounded", "visual"} and not answer.evidence_refs:
        points = list(answer.unsupported_points)
        if answer.answer not in points:
            points.append(answer.answer)
        return answer.model_copy(
            update={
                "answer": f"当前证据不足：{answer.answer}",
                "epistemic_status": "unknown",
                "unsupported_points": points[:12],
            }
        )
    if answer.epistemic_status == "unknown" and not answer.answer.startswith("当前证据不足"):
        return answer.model_copy(update={"answer": f"当前证据不足：{answer.answer}"})
    return answer


def _terms(value: str) -> set[str]:
    lowered = value.casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}", lowered))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return terms


def repair_grounded_references(
    answer: ConversationAnswer,
    question: str,
    context: dict[str, Any],
) -> ConversationAnswer:
    del question, context
    return answer


def _is_greeting(question: str) -> bool:
    normalized = question.strip().casefold().rstrip("!！。,.？?")
    return normalized in {"你好", "您好", "嗨", "hi", "hello", "在吗", "你是谁"}


def _needs_visual_context(question: str) -> bool:
    value = question.casefold()
    return any(
        term in value
        for term in (
            "颜色", "什么色", "外观", "长什么样", "款式", "形状", "画面", "镜头",
            "包装", "logo", "标志", "人物", "穿着", "鞋", "衣服", "材质", "质地",
            "color", "look", "visual", "material",
        )
    )


def _is_creative_request(question: str) -> bool:
    value = question.casefold()
    return any(term in value for term in ("脚本", "分镜", "hook", "钩子", "a/b", "ab 版本", "广告文案"))


def _is_report_request(question: str) -> bool:
    value = question.casefold()
    return any(term in value for term in ("html", "htlm", "markdown", "报告", "导出分析", "生成分析"))


def _workspace_answer(
    question: str,
    artifact_root: Path,
    status: str,
    confirmations: list[str],
) -> tuple[ConversationAnswer, bool] | None:
    value = question.casefold()
    asks_location = any(
        term in value
        for term in ("结果在哪里", "分析结果", "有哪些结果", "生成了什么", "有哪些产物", "产物在哪里")
    )
    asks_download = any(term in value for term in ("怎么下载", "如何下载", "下载报告", "下载结果"))
    asks_status = any(term in value for term in ("完成了吗", "是否完成", "任务状态", "为什么待确认", "为什么还没完成"))
    if not (asks_location or asks_download or asks_status):
        return None
    artifacts = [
        ("结构化洞察", artifact_root / "insights" / "analysis.json"),
        ("HTML 报告", artifact_root / "report" / "report.html"),
        ("Markdown 报告", artifact_root / "report" / "report.md"),
        ("创作方案", artifact_root / "creative" / "package.json"),
    ]
    available = [label for label, path in artifacts if path.is_file()]
    has_html = (artifact_root / "report" / "report.html").is_file()
    labels = {
        "planned": "已计划",
        "running": "正在运行",
        "waiting_confirmation": "等待人工确认",
        "completed": "已完成",
        "failed": "执行失败",
        "cancelled": "已取消",
    }
    lines = [f"当前任务状态：{labels.get(status, status)}。"]
    if status == "waiting_confirmation":
        reason = "报告审计需要人工复核" if "report_review" in confirmations else "存在待处理的人工确认项"
        lines.append(f"暂停原因：{reason}，确认前不会继续执行后续步骤。")
    if available:
        lines.append("当前已生成：" + "、".join(available) + "。")
        lines.append("下载链接会直接显示在当前对话中。")
    else:
        lines.append("当前还没有可下载的分析产物。")
    return ConversationAnswer(
        answer="\n".join(lines),
        epistemic_status="conversational",
    ), has_html


def _strip_evidence_ids(value: str) -> str:
    cleaned = re.sub(r"\b(?:speech|ocr_cluster|kf)_[0-9a-z_]+\b", "", value)
    cleaned = re.sub(r"[（(]\s*参考(?:语音|画面|证据)?\s*[，,：:、\s]*[）)]", "", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _render_creative_answer(package: CreativePackage) -> ConversationAnswer:
    script = package.script
    lines = [
        f"# {script.title}",
        "",
        f"**目标受众：** {script.target_audience}",
        "",
    ]
    refs: list[str] = []
    for scene in sorted(script.scenes, key=lambda item: item.order):
        lines.extend(
            [
                f"## 镜头 {scene.order}（{scene.duration_seconds:g} 秒）",
                f"**画面：** {scene.visual_direction}",
                f"**旁白：** {scene.narration}",
            ]
        )
        if scene.on_screen_text:
            lines.append(f"**屏幕文字：** {scene.on_screen_text}")
        lines.append("")
        for ref in scene.evidence_refs:
            if ref not in refs:
                refs.append(ref)
    lines.extend([f"**行动号召：** {script.call_to_action}"])
    if package.unsupported_points:
        lines.extend(["", "**创作边界：**", *[f"- {item}" for item in package.unsupported_points]])
    return ConversationAnswer(
        answer="\n".join(lines),
        evidence_refs=refs[:12],
        epistemic_status="grounded",
    )


def _image_content(run_dir: Path, context: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for item in context.get("visual_keyframes", [])[:limit]:
        path = (run_dir / str(item["artifact_path"])).resolve()
        if not path.is_file() or not path.is_relative_to(run_dir.resolve()):
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "text",
                "text": f"关键帧 {item['id']}，时间 {item['timestamp_ms']}ms：",
            }
        )
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
        )
    return content


def _presented_keyframe_refs(
    run_dir: Path, context: dict[str, Any], limit: int = 8
) -> set[str]:
    refs: set[str] = set()
    for item in context.get("visual_keyframes", [])[:limit]:
        path = (run_dir / str(item["artifact_path"])).resolve()
        if path.is_file() and path.is_relative_to(run_dir.resolve()):
            refs.add(str(item["id"]))
    return refs


def _partial_json_string(value: str, field: str) -> str:
    marker = f'"{field}"'
    start = value.find(marker)
    if start < 0:
        return ""
    start = value.find(":", start + len(marker))
    if start < 0:
        return ""
    start = value.find('"', start + 1)
    if start < 0:
        return ""
    chars: list[str] = []
    escaped = False
    index = start + 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/"}
    while index < len(value):
        char = value[index]
        if escaped:
            if char == "u" and index + 4 < len(value):
                code = value[index + 1 : index + 5]
                try:
                    chars.append(chr(int(code, 16)))
                    index += 5
                    escaped = False
                    continue
                except ValueError:
                    break
            chars.append(escapes.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
        else:
            chars.append(char)
        index += 1
    return "".join(chars)


def _qwen_answer(
    question: str,
    context: dict[str, Any],
    history: list[dict[str, Any]],
    settings: Settings,
    *,
    run_dir: Path | None = None,
    include_images: bool = False,
    on_delta: Callable[[str], None] | None = None,
) -> ConversationAnswer:
    prompt = (
        "你是 AdVista 广告分析对话 Agent。根据给定 Evidence Ledger、关键帧、已验证洞察和会话历史回答。"
        "先尽力使用语音、OCR 和关键帧回答，不要因为文字证据缺失就直接拒绝视觉问题。"
        "语音/OCR 支持的事实用 grounded；关键帧直接可见的颜色、外观和画面用 visual，并引用合法 kf_* ID。"
        "普通问候和非事实闲聊用 conversational，不引用 Evidence，也不要说证据不足。"
        "问题中证据不足的部分必须单独写入 unsupported_points，不得伪装成有证据结论。"
        "如果证据不足，epistemic_status 必须为 unknown，answer 必须以‘当前证据不足’开头，且 evidence_refs 为空。"
        "材质和成分不能仅凭外观确认；可以描述视觉上像什么，但必须说明无法确认真实材质。"
        "不得把广告声明写成独立验证的客观事实，不得编造价格、成分、受众属性或视频内容。"
        "不要在 answer 正文中输出 speech_*、ocr_cluster_*、kf_* 等内部证据 ID，只放在 evidence_refs 字段。"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    for item in history[-12:]:
        messages.append({"role": item["role"], "content": item["content"]})
    user_text = (
        "EVIDENCE_CONTEXT\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        + "\n\nQUESTION\n"
        + question
    )
    if include_images and run_dir is not None:
        messages.append({"role": "user", "content": [{"type": "text", "text": user_text}, *_image_content(run_dir, context)]})
    else:
        messages.append({"role": "user", "content": user_text})
    payload = {
        "model": settings.insight.served_model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
        "seed": 42,
        "structured_outputs": {"json": ConversationAnswer.model_json_schema()},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if on_delta is not None:
        payload["stream"] = True
    call = urllib.request.Request(
        settings.insight.endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(call, timeout=settings.insight.timeout_seconds) as response:
            if on_delta is None:
                value = json.loads(response.read().decode("utf-8"))
                text = str(value["choices"][0]["message"]["content"])
            else:
                chunks: list[str] = []
                emitted = ""
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = json.loads(line[6:])
                    delta = str(event["choices"][0].get("delta", {}).get("content") or "")
                    if not delta:
                        continue
                    chunks.append(delta)
                    current = _partial_json_string("".join(chunks), "answer")
                    if len(current) > len(emitted):
                        on_delta(current[len(emitted) :])
                        emitted = current
                text = "".join(chunks)
        return ConversationAnswer.model_validate_json(text)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Chat service HTTP {exc.code}: {body[-4000:]}") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Chat service returned an invalid answer: {exc}") from exc


def ask_agent(
    run_id: str,
    question: str,
    settings: Settings,
    *,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    value = question.strip()
    if not value:
        raise ValueError("Chat question must not be empty")
    store = ArtifactStore(settings.paths.output_root)
    run_dir, state = _resolve_session(run_id, settings)
    artifact_root = (
        store.execution_dir(str(getattr(state, "execution_id", None))) / "artifacts"
        if getattr(state, "execution_id", None) is not None
        else run_dir
    )
    context, allowed_refs = _context(run_dir, artifact_root)
    conversations = ConversationStore(conversation_db(settings))
    conversations.create_session(
        session_id=state.session_id,
        run_id=getattr(state, "execution_id", None) or state.run_id,
        source_path=state.source_path,
        goal=state.request.goal,
    )
    history = conversations.messages(state.session_id)
    report_url: str | None = None
    state_status = getattr(state, "status", "completed")
    status_value = str(getattr(state_status, "value", state_status))
    workspace = _workspace_answer(
        value,
        artifact_root,
        status_value,
        list(getattr(state, "confirmations", [])),
    )
    if workspace is not None:
        answer, has_html_report = workspace
        if has_html_report:
            report_url = f"/api/runs/{run_id}/exports/report-html"
    elif _is_report_request(value):
        result = build_report(
            state.source_path,
            settings,
            request=state.request,
            artifact_root=artifact_root,
        )
        report_url = f"/api/runs/{run_id}/exports/report-html"
        answer = ConversationAnswer(
            answer=(
                f"HTML 广告分析报告已生成：{result['insight_count']} 条洞察，"
                "点击下方按钮即可下载 HTML 或 Markdown 版本。"
            ),
            epistemic_status="conversational",
        )
    elif _is_creative_request(value):
        build_creative(
            state.source_path,
            settings,
            request=state.request,
            artifact_root=artifact_root,
        )
        package = CreativePackage.model_validate(
            store.read_json(artifact_root / "creative" / "package.json")
        )
        answer = _render_creative_answer(package)
    elif _is_greeting(value):
        answer = ConversationAnswer(
            answer="你好，我可以结合当前视频的语音、字幕和关键帧回答广告内容，也可以继续分析卖点、画面和创意。",
            epistemic_status="conversational",
        )
    else:
        include_images = _needs_visual_context(value)
        raw_answer = _qwen_answer(
                value,
                context,
                history,
                settings,
                run_dir=run_dir,
                include_images=include_images,
            )
        answer = normalize_conversation_answer(raw_answer)
        keyframe_refs = _presented_keyframe_refs(run_dir, context)
        allowed_refs = {
            ref for ref in allowed_refs if not ref.startswith("kf_")
        } | (keyframe_refs if include_images else set())
    validate_conversation_answer(answer, allowed_refs)
    answer = answer.model_copy(update={"answer": _strip_evidence_ids(answer.answer)})
    user_message_id = conversations.add_message(state.session_id, "user", value, [])
    assistant_message_id = conversations.add_message(
        state.session_id,
        "assistant",
        answer.answer,
        answer.evidence_refs,
    )
    version_id = conversations.add_version(
        state.session_id,
        assistant_message_id,
        answer.model_dump(mode="json"),
    )
    if on_delta is not None:
        for offset in range(0, len(answer.answer), 120):
            on_delta(answer.answer[offset : offset + 120])
    citations = _resolved_citations(run_dir, run_id, answer.evidence_refs)
    return {
        "status": "ok",
        "session_id": state.session_id,
        "run_id": state.run_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "version_id": version_id,
        **answer.model_dump(mode="json"),
        "citations": citations,
        "report_url": report_url,
    }


def show_conversation(run_id: str, settings: Settings) -> dict[str, Any]:
    run_dir, state = _resolve_session(run_id, settings)
    conversations = ConversationStore(conversation_db(settings))
    session = conversations.session(state.session_id)
    messages = conversations.messages(state.session_id, limit=100)
    for message in messages:
        references = [str(item) for item in message.get("citations", [])]
        message["evidence_refs"] = references
        message["citations"] = _resolved_citations(run_dir, run_id, references)
    return {"session": session, "messages": messages}


def record_feedback(
    run_id: str,
    decision: str,
    note: str,
    settings: Settings,
    *,
    message_id: str | None = None,
) -> dict[str, Any]:
    _, state = _resolve_session(run_id, settings)
    conversations = ConversationStore(conversation_db(settings))
    conversations.create_session(
        session_id=state.session_id,
        run_id=getattr(state, "execution_id", None) or state.run_id,
        source_path=state.source_path,
        goal=state.request.goal,
    )
    feedback_id = conversations.add_feedback(
        state.session_id,
        decision,
        note.strip(),
        message_id,
    )
    return {
        "status": "ok",
        "session_id": state.session_id,
        "feedback_id": feedback_id,
        "decision": decision,
    }
