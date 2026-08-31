from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import uuid
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import (
    AgentRequest,
    Evidence,
    EvidenceCluster,
    EvidenceLedger,
    EvidenceRelation,
    Keyframe,
    MarketingAnalysis,
)
from ad_vista_agent.tools import QwenInsightTool, ToolContext

from .grounding import (
    grounded_executive_summary,
    normalize_inline_citations,
    validate_analysis_grounding,
)
from .payload import build_ledger_payload


INSIGHT_PIPELINE_VERSION = "6"
QWEN_VERSIONS = {"vllm": "0.19.1", "torch": "2.10.0", "transformers": "5.13.0"}
ModelT = TypeVar("ModelT", bound=BaseModel)


class FrameObservation(BaseModel):
    keyframe_id: str
    description: str
    visible_text: str = ""
    product_or_subject: str = ""
    action_or_change: str = ""


class FrameObservationBatch(BaseModel):
    observations: list[FrameObservation]


def _load_jsonl(path: Path, model: type[ModelT], label: str) -> list[ModelT]:
    rows: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(model.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid {label} at {path}:{line_number}") from exc
    return rows


def _model_identity(model_path: Path) -> dict[str, object]:
    config = model_path / "config.json"
    index = model_path / "model.safetensors.index.json"
    shards = sorted(model_path.glob("model-*-of-*.safetensors"))
    if not shards:
        shards = sorted(model_path.glob("model.safetensors-*.safetensors"))
    if not config.is_file() or not index.is_file() or not shards:
        raise FileNotFoundError(f"Invalid Qwen model directory: {model_path}")
    return {
        "path": str(model_path.resolve()),
        "config_sha256": sha256_file(config),
        "index_sha256": sha256_file(index),
        "shards": [
            {"name": shard.name, "size": shard.stat().st_size, "mtime_ns": shard.stat().st_mtime_ns}
            for shard in shards
        ],
    }


def _runtime_model_identity(settings: Settings) -> dict[str, object]:
    configured = _model_identity(settings.model_path(settings.insight.model))
    if settings.insight.runtime != "openai":
        return configured
    return {
        **configured,
        "endpoint": settings.insight.endpoint,
        "served_model": settings.insight.served_model,
    }


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```json"):
        value = value[7:]
    elif value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    value = value.strip()
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Qwen output does not contain a JSON object")
        result = json.loads(value[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("Qwen output must be a JSON object")
    return result


def _visual_content(run_dir: Path, keyframes: list[Keyframe]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for item in keyframes:
        path = (run_dir / item.artifact_path).resolve()
        if not path.is_file() or not path.is_relative_to(run_dir.resolve()):
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend(
            [
                {
                    "type": "text",
                    "text": f"关键帧 {item.keyframe_id}，时间 {item.timestamp_ms}ms：",
                },
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]
        )
    return content


def _visual_prompt() -> str:
    return (
        "你是广告视频视觉证据提取器。只描述关键帧中直接可见的内容，不做营销推断。"
        "逐帧返回结构化观察：画面描述、可读文字、产品或主体、动作或相对前后变化。"
        "每个 keyframe_id 必须原样保留；看不清的字段填写空字符串。"
        "所有描述使用中文，不要编造品牌、材质、功效或人物属性。"
    )


def _visual_observations(
    run_dir: Path,
    keyframes: list[Keyframe],
    settings: Settings,
    tool: QwenInsightTool,
    run_id: str,
) -> tuple[list[FrameObservation], list[str], int, int]:
    observations_by_id: dict[str, FrameObservation] = {}
    attempts: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    batch_size = settings.insight.max_images_per_prompt
    overlap = min(settings.insight.visual_batch_overlap, batch_size - 1)
    for start in _visual_batch_starts(len(keyframes), batch_size, overlap):
        batch = keyframes[start : start + batch_size]
        messages = [
            {"role": "system", "content": _visual_prompt()},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "请分析以下关键帧，不要遗漏任何一帧。",
                    },
                    *_visual_content(run_dir, batch),
                ],
            },
        ]
        result = tool.run(
            ToolContext(run_id=run_id, run_dir=run_dir),
            {
                "model_path": str(settings.model_path(settings.insight.model)),
                "gpu_memory_utilization": settings.insight.gpu_memory_utilization,
                "max_model_len": settings.insight.max_model_len,
                "max_tokens": 900,
                "temperature": 0.0,
                "messages": messages,
                "output_schema": FrameObservationBatch.model_json_schema(),
            },
        )
        attempts.extend(result.attempts)
        prompt_tokens += result.prompt_tokens
        completion_tokens += result.completion_tokens
        parsed = FrameObservationBatch.model_validate(_extract_json(result.text))
        expected = {item.keyframe_id for item in batch}
        for item in parsed.observations:
            if item.keyframe_id in expected:
                observations_by_id[item.keyframe_id] = item
    observations = [
        observations_by_id[item.keyframe_id]
        for item in keyframes
        if item.keyframe_id in observations_by_id
    ]
    return observations, attempts, prompt_tokens, completion_tokens


def _visual_batch_starts(keyframe_count: int, batch_size: int, overlap: int) -> list[int]:
    if keyframe_count <= 0:
        return []
    effective_overlap = min(overlap, batch_size - 1)
    step = batch_size - effective_overlap
    starts = [0]
    while starts[-1] + batch_size < keyframe_count:
        starts.append(starts[-1] + step)
    return starts


def _visual_batch_count(keyframe_count: int, batch_size: int, overlap: int) -> int:
    return len(_visual_batch_starts(keyframe_count, batch_size, overlap))


def _cache_payload(
    settings: Settings,
    *,
    ledger_paths: list[Path],
    prompt_path: Path,
    request: AgentRequest | None = None,
    max_insights_per_dimension: int,
) -> dict[str, Any]:
    return {
        "stage": "stage_6_insights",
        "schema_version": settings.project.schema_version,
        "insight_pipeline_version": INSIGHT_PIPELINE_VERSION,
        "model": _runtime_model_identity(settings),
        "runtime_versions": QWEN_VERSIONS,
        "ledger_artifacts": {path.name: sha256_file(path) for path in ledger_paths},
        "prompt_sha256": sha256_file(prompt_path),
        "generation": {
            "max_model_len": settings.insight.max_model_len,
            "max_tokens": settings.insight.max_tokens,
            "temperature": settings.insight.temperature,
            "max_images_per_prompt": settings.insight.max_images_per_prompt,
            "visual_batch_overlap": settings.insight.visual_batch_overlap,
            "max_insights_per_dimension": max_insights_per_dimension,
        },
        "task": request.model_dump(mode="json") if request is not None else None,
        "runtime": {
            "type": settings.insight.runtime,
            "endpoint": settings.insight.endpoint if settings.insight.runtime == "openai" else None,
            "served_model": settings.insight.served_model if settings.insight.runtime == "openai" else None,
        },
    }


def build_insights(
    video_path: Path,
    settings: Settings,
    *,
    force: bool = False,
    request: AgentRequest | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    ledger_dir = run_dir / "ledger"
    evidence_path = ledger_dir / "evidence.jsonl"
    clusters_path = ledger_dir / "clusters.jsonl"
    relations_path = ledger_dir / "relations.jsonl"
    ledger_path = ledger_dir / "ledger.json"
    keyframes_path = run_dir / "timeline" / "keyframes.jsonl"
    required = [evidence_path, clusters_path, relations_path, ledger_path, keyframes_path]
    missing = [path for path in required if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(run_dir)) for path in missing)
        raise FileNotFoundError(f"Stage 6 requires completed Stage 5 artifacts; missing: {names}")

    prompt_path = Path(__file__).resolve().parents[1] / "resources" / "marketing_insights.txt"
    output_root = artifact_root or run_dir
    output_dir = output_root / "insights"
    staging_root = output_root / f".stage_6_{uuid.uuid4().hex}"
    staging_dir = staging_root / "insights"
    staging_dir.mkdir(parents=True, exist_ok=True)
    raw_path = staging_dir / "raw_response.json"
    visual_observations_path = staging_dir / "visual_observations.json"
    analysis_path = staging_dir / "analysis.json"
    published_raw_path = output_dir / "raw_response.json"
    published_visual_observations_path = output_dir / "visual_observations.json"
    published_analysis_path = output_dir / "analysis.json"
    metrics_path = output_root / "stage_6_metrics.json"
    manifest_path = output_root / "manifest.json"
    max_insights = settings.insight.max_insights_per_dimension
    if request is not None and request.mode == "quick":
        max_insights = min(max_insights, 3)
    cache_payload = _cache_payload(
        settings,
        ledger_paths=required,
        prompt_path=prompt_path,
        request=request,
        max_insights_per_dimension=max_insights,
    )
    cache_key = stage_cache_key(cache_payload)

    evidence = _load_jsonl(evidence_path, Evidence, "ledger evidence")
    clusters = _load_jsonl(clusters_path, EvidenceCluster, "ledger cluster")
    relations = _load_jsonl(relations_path, EvidenceRelation, "ledger relation")
    keyframes = _load_jsonl(keyframes_path, Keyframe, "timeline keyframe")
    ledger = EvidenceLedger.model_validate(store.read_json(ledger_path))
    allowed_refs = {
        item.evidence_id for item in evidence if item.modality.value == "speech"
    } | {item.cluster_id for item in clusters} | {item.keyframe_id for item in keyframes}
    reference_content = {
        **{item.evidence_id: item.content for item in evidence},
        **{item.cluster_id: item.canonical_content for item in clusters},
        **{item.keyframe_id: f"关键画面 {item.shot_id}" for item in keyframes},
    }

    if not force and published_analysis_path.is_file() and metrics_path.is_file() and published_visual_observations_path.is_file() and published_raw_path.is_file():
        metrics = store.read_json(metrics_path)
        artifact_hashes = metrics.get("artifact_sha256")
        if (
            metrics.get("cache_key") == cache_key
            and isinstance(artifact_hashes, dict)
            and artifact_hashes.get("analysis") == sha256_file(published_analysis_path)
            and artifact_hashes.get("visual_observations") == sha256_file(published_visual_observations_path)
            and artifact_hashes.get("raw_response") == sha256_file(published_raw_path)
        ):
            analysis = MarketingAnalysis.model_validate(store.read_json(published_analysis_path))
            validate_analysis_grounding(
                analysis,
                asset_id=ledger.asset_id,
                allowed_references=allowed_refs,
                max_per_dimension=max_insights,
                reference_content=reference_content,
            )
            grounded_summary = grounded_executive_summary(analysis)
            if analysis.executive_summary != grounded_summary:
                analysis = analysis.model_copy(update={"executive_summary": grounded_summary})
                store.write_json(published_analysis_path, analysis)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "artifact_root": str(output_root),
                "insight_count": len(analysis.insights),
                "cache_key": cache_key,
            }

    prompt = prompt_path.read_text(encoding="utf-8").replace(
        "{max_insights_per_dimension}", str(max_insights)
    )
    ledger_payload = build_ledger_payload(ledger, evidence, clusters, relations)
    task_payload = (
        request.model_dump(mode="json")
        if request is not None
        else {"goal": "通用广告分析", "mode": "quick", "deliverables": ["insights"]}
    )
    tool = QwenInsightTool(
        settings.insight.python_executable,
        settings.insight.timeout_seconds,
        settings.hardware.cuda_visible_devices,
        runtime=settings.insight.runtime,
        endpoint=settings.insight.endpoint,
        served_model=settings.insight.served_model,
        max_images_per_prompt=settings.insight.max_images_per_prompt,
    )
    visual_observations, visual_attempts, visual_prompt_tokens, visual_completion_tokens = _visual_observations(
        run_dir, keyframes, settings, tool, run_id
    )
    store.write_json(
        visual_observations_path,
        {"observations": [item.model_dump(mode="json") for item in visual_observations]},
    )
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "请围绕 TASK 的用户目标，综合完整视频的视觉观察与 EVIDENCE_LEDGER 输出结构化广告洞察。"
            "目标只决定分析重点，不能降低证据标准。\n"
            + json.dumps(
                {
                    "task": task_payload,
                    "evidence_ledger": ledger_payload,
                    "visual_observations": [item.model_dump(mode="json") for item in visual_observations],
                    "allowed_keyframe_ids": [item.keyframe_id for item in keyframes],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    analysis_path.unlink(missing_ok=True)
    metrics_path.unlink(missing_ok=True)
    inference_started = time.perf_counter()
    result = tool.run(
        ToolContext(run_id=run_id, run_dir=run_dir),
        {
            "model_path": str(settings.model_path(settings.insight.model)),
            "gpu_memory_utilization": settings.insight.gpu_memory_utilization,
            "max_model_len": settings.insight.max_model_len,
            "max_tokens": settings.insight.max_tokens,
            "temperature": settings.insight.temperature,
            "messages": messages,
            "output_schema": MarketingAnalysis.model_json_schema(),
        },
    )
    inference_seconds = time.perf_counter() - inference_started
    if result.versions != QWEN_VERSIONS:
        raise RuntimeError(f"Unexpected Qwen worker versions: {result.versions}")
    store.write_json(
        raw_path,
        {
            "attempts": visual_attempts + result.attempts,
            "attempt_count": len(visual_attempts) + result.attempt_count,
            "selected_attempt": len(visual_attempts) + result.attempt_count - 1,
            "versions": result.versions,
        },
    )
    try:
        parsed = _extract_json(result.text)
        analysis = normalize_inline_citations(MarketingAnalysis.model_validate(parsed))
    except Exception as exc:
        raise ValueError(f"Qwen structured output remained invalid after repair: {exc}") from exc
    validate_analysis_grounding(
        analysis,
        asset_id=ledger.asset_id,
        allowed_references=allowed_refs,
        max_per_dimension=max_insights,
        reference_content=reference_content,
    )
    analysis = analysis.model_copy(
        update={"executive_summary": grounded_executive_summary(analysis)}
    )
    store.write_json(analysis_path, analysis)
    total_seconds = time.perf_counter() - total_started
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_6_insights",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "prompt_tokens": visual_prompt_tokens + result.prompt_tokens,
        "completion_tokens": visual_completion_tokens + result.completion_tokens,
        "attempt_count": len(visual_attempts) + result.attempt_count,
        "insight_count": len(analysis.insights),
        "dimension_counts": {
            dimension: sum(item.dimension.value == dimension for item in analysis.insights)
            for dimension in sorted({item.dimension.value for item in analysis.insights})
        },
        "unknown_count": len(analysis.unknowns),
        "visual_keyframe_count": len(keyframes),
        "visual_observation_count": len(visual_observations),
        "visual_batch_count": _visual_batch_count(
            len(keyframes),
            settings.insight.max_images_per_prompt,
            settings.insight.visual_batch_overlap,
        ),
        "task": task_payload,
        "effective_mode_profile": {
            "mode": task_payload.get("mode", "quick"),
            "max_insights_per_dimension": max_insights,
        },
        "inference_seconds": round(inference_seconds, 6),
        "total_seconds": round(total_seconds, 6),
        "versions": result.versions,
        "artifact_sha256": {
            "analysis": sha256_file(analysis_path),
            "visual_observations": sha256_file(visual_observations_path),
            "raw_response": sha256_file(raw_path),
        },
    }
    store.publish_directory(staging_dir, output_dir)
    shutil.rmtree(staging_root, ignore_errors=True)
    metrics["artifact_sha256"] = {
            "analysis": sha256_file(published_analysis_path),
            "visual_observations": sha256_file(published_visual_observations_path),
            "raw_response": sha256_file(published_raw_path),
    }
    store.write_json(metrics_path, metrics)
    manifest: dict[str, Any] = (
        store.read_json(manifest_path)
        if manifest_path.is_file()
        else {"schema_version": settings.project.schema_version, "run_id": run_id}
    )
    stages_value = manifest.setdefault("stages", {})
    if not isinstance(stages_value, dict):
        raise ValueError("Manifest stages must be an object")
    stages: dict[str, Any] = stages_value
    stages["stage_6_insights"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "analysis": "insights/analysis.json",
            "visual_observations": "insights/visual_observations.json",
            "raw_response": "insights/raw_response.json",
            "metrics": "stage_6_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifact_root": str(output_root),
        "insight_count": len(analysis.insights),
        "cache_key": cache_key,
        "metrics": metrics,
    }
