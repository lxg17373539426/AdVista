from __future__ import annotations

import json
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
    MarketingAnalysis,
)
from ad_vista_agent.tools import QwenInsightTool, ToolContext

from .grounding import (
    grounded_executive_summary,
    normalize_inline_citations,
    validate_analysis_grounding,
)
from .payload import build_ledger_payload


INSIGHT_PIPELINE_VERSION = "2"
QWEN_VERSIONS = {"vllm": "0.19.1", "torch": "2.10.0", "transformers": "5.13.0"}
ModelT = TypeVar("ModelT", bound=BaseModel)


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
    required = [evidence_path, clusters_path, relations_path, ledger_path]
    missing = [path for path in required if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(run_dir)) for path in missing)
        raise FileNotFoundError(f"Stage 6 requires completed Stage 5 artifacts; missing: {names}")

    prompt_path = Path(__file__).resolve().parents[1] / "resources" / "marketing_insights.txt"
    output_root = artifact_root or run_dir
    output_dir = output_root / "insights"
    raw_path = output_dir / "raw_response.json"
    analysis_path = output_dir / "analysis.json"
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
    ledger = EvidenceLedger.model_validate(store.read_json(ledger_path))
    allowed_refs = {
        item.evidence_id for item in evidence if item.modality.value == "speech"
    } | {item.cluster_id for item in clusters}

    if not force and analysis_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            analysis = MarketingAnalysis.model_validate(store.read_json(analysis_path))
            validate_analysis_grounding(
                analysis,
                asset_id=ledger.asset_id,
                allowed_references=allowed_refs,
                max_per_dimension=max_insights,
            )
            grounded_summary = grounded_executive_summary(analysis)
            if analysis.executive_summary != grounded_summary:
                analysis = analysis.model_copy(update={"executive_summary": grounded_summary})
                store.write_json(analysis_path, analysis)
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
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "请围绕 TASK 的用户目标，基于 EVIDENCE_LEDGER 输出结构化广告洞察。"
            "目标只决定分析重点，不能降低证据标准。\n"
            + json.dumps(
                {"task": task_payload, "evidence_ledger": ledger_payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    tool = QwenInsightTool(
        settings.insight.python_executable,
        settings.insight.timeout_seconds,
        settings.hardware.cuda_visible_devices,
        runtime=settings.insight.runtime,
        endpoint=settings.insight.endpoint,
        served_model=settings.insight.served_model,
    )
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
            "attempts": result.attempts,
            "attempt_count": result.attempt_count,
            "selected_attempt": result.attempt_count - 1,
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
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "attempt_count": result.attempt_count,
        "insight_count": len(analysis.insights),
        "dimension_counts": {
            dimension: sum(item.dimension.value == dimension for item in analysis.insights)
            for dimension in sorted({item.dimension.value for item in analysis.insights})
        },
        "unknown_count": len(analysis.unknowns),
        "task": task_payload,
        "effective_mode_profile": {
            "mode": task_payload.get("mode", "quick"),
            "max_insights_per_dimension": max_insights,
        },
        "inference_seconds": round(inference_seconds, 6),
        "total_seconds": round(total_seconds, 6),
        "versions": result.versions,
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
