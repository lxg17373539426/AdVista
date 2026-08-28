from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.insights.builder import _extract_json, _load_jsonl, _runtime_model_identity
from ad_vista_agent.insights.payload import build_ledger_payload
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import (
    AgentRequest,
    CreativePackage,
    Evidence,
    EvidenceCluster,
    EvidenceLedger,
    EvidenceRelation,
    MarketingAnalysis,
)
from ad_vista_agent.tools import QwenInsightTool, ToolContext


CREATIVE_PIPELINE_VERSION = "1"
QWEN_VERSIONS = {"vllm": "0.19.1", "torch": "2.10.0", "transformers": "5.13.0"}
ModelT = TypeVar("ModelT", bound=BaseModel)


def _validate_grounding(package: CreativePackage, allowed_refs: set[str]) -> None:
    references: list[str] = []
    references.extend(ref for item in package.hooks for ref in item.evidence_refs)
    references.extend(ref for scene in package.script.scenes for ref in scene.evidence_refs)
    references.extend(ref for frame in package.storyboard.frames for ref in frame.evidence_refs)
    references.extend(ref for item in package.ab_variants for ref in item.evidence_refs)
    unknown = set(references).difference(allowed_refs)
    if unknown:
        raise ValueError(f"Creative output cites unknown evidence: {', '.join(sorted(unknown))}")
    if not references:
        raise ValueError("Creative output must cite at least one Evidence ID")
    if len({scene.order for scene in package.script.scenes}) != len(package.script.scenes):
        raise ValueError("Creative script scene orders must be unique")
    if len({frame.order for frame in package.storyboard.frames}) != len(package.storyboard.frames):
        raise ValueError("Creative storyboard frame orders must be unique")
    text_values = [item.text for item in package.hooks]
    text_values.extend(
        value
        for scene in package.script.scenes
        for value in (scene.narration, scene.visual_direction, scene.on_screen_text)
        if value
    )
    text_values.extend(
        value
        for frame in package.storyboard.frames
        for value in (frame.shot_description, frame.camera_direction, frame.text_overlay)
        if value
    )
    if any(value.rstrip().endswith(("“", "‘", "：", ":", "，", ",")) for value in text_values):
        raise ValueError("Creative output contains an incomplete text fragment")


def _load_context(
    run_dir: Path, artifact_root: Path | None = None
) -> tuple[dict[str, Any], set[str], list[Path]]:
    ledger_dir = run_dir / "ledger"
    paths = [
        ledger_dir / "evidence.jsonl",
        ledger_dir / "clusters.jsonl",
        ledger_dir / "relations.jsonl",
        ledger_dir / "ledger.json",
        (artifact_root or run_dir) / "insights" / "analysis.json",
    ]
    missing = [path for path in paths if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Stage 11 requires existing insights and Ledger artifacts; missing: {names}")
    evidence = _load_jsonl(paths[0], Evidence, "ledger evidence")
    clusters = _load_jsonl(paths[1], EvidenceCluster, "ledger clusters")
    relations = _load_jsonl(paths[2], EvidenceRelation, "ledger relations")
    store = ArtifactStore(run_dir.parents[1])
    ledger = EvidenceLedger.model_validate(store.read_json(paths[3]))
    analysis = MarketingAnalysis.model_validate(store.read_json(paths[4]))
    allowed = {item.evidence_id for item in evidence if item.modality.value == "speech"} | {
        item.cluster_id for item in clusters
    }
    return {
        "ledger": build_ledger_payload(ledger, evidence, clusters, relations),
        "validated_insights": analysis.model_dump(mode="json"),
        "unknowns": analysis.unknowns,
    }, allowed, paths


def build_creative(
    video_path: Path,
    settings: Settings,
    *,
    force: bool = False,
    request: AgentRequest | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    output_root = artifact_root or run_dir
    context, allowed_refs, source_paths = _load_context(run_dir, artifact_root)
    output_dir = output_root / "creative"
    package_path = output_dir / "package.json"
    metrics_path = output_root / "stage_11_metrics.json"
    cache_payload = {
        "stage": "stage_11_creative",
        "schema_version": settings.project.schema_version,
        "pipeline_version": CREATIVE_PIPELINE_VERSION,
        "source_artifacts": {path.name: sha256_file(path) for path in source_paths},
        "model": _runtime_model_identity(settings),
        "task": request.model_dump(mode="json") if request is not None else None,
    }
    cache_key = stage_cache_key(cache_payload)
    if not force and package_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            package = CreativePackage.model_validate(store.read_json(package_path))
            _validate_grounding(package, allowed_refs)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "artifact_root": str(output_root),
                **_paths(output_dir),
            }

    prompt = (
        "你是证据约束的广告创作 Agent。只能根据输入 Ledger 和已验证洞察创作。"
        "生成中文 Hook、短视频脚本、结构化分镜和至少两个 A/B 版本。"
        "每个创作项目必须在 evidence_refs 引用输入中存在的 speech Evidence ID 或 ocr_cluster ID。"
        "广告声明只能写成广告声称，不得升级为独立验证事实；证据无法确认的内容写入 unsupported_points。"
        "visual_direction 和 shot_description 是建议，不得声称原视频已经展示未被证据支持的画面。"
        "不要编造价格、成分、功效、受众属性或画面内容；每个字符串必须完整结束，不得截断。只返回符合 Schema 的 JSON。"
        "当前 TASK 的 goal 决定创作重点，mode=deep 时应提供更完整的镜头与 A/B 推演，但不得超出证据。"
    )
    context["task"] = (
        request.model_dump(mode="json")
        if request is not None
        else {"goal": "生成广告创意", "mode": "quick", "deliverables": ["creative"]}
    )
    tool = QwenInsightTool(
        settings.insight.python_executable,
        settings.insight.timeout_seconds,
        settings.hardware.cuda_visible_devices,
        runtime=settings.insight.runtime,
        endpoint=settings.insight.endpoint,
        served_model=settings.insight.served_model,
    )
    inference = tool.run(
        ToolContext(run_id=run_id, run_dir=run_dir),
        {
            "model_path": str(settings.model_path(settings.insight.model)),
            "gpu_memory_utilization": settings.insight.gpu_memory_utilization,
            "max_model_len": settings.insight.max_model_len,
            "max_tokens": settings.insight.max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
            ],
            "output_schema": CreativePackage.model_json_schema(),
        },
    )
    if inference.versions != QWEN_VERSIONS:
        raise RuntimeError(f"Unexpected Qwen worker versions: {inference.versions}")
    try:
        package = CreativePackage.model_validate(_extract_json(inference.text))
        _validate_grounding(package, allowed_refs)
    except Exception as exc:
        raise ValueError(f"Qwen creative output remained invalid after repair: {exc}") from exc
    store.write_json(package_path, package)
    store.write_json(
        output_dir / "hooks.json",
        {"hooks": [item.model_dump(mode="json") for item in package.hooks]},
    )
    store.write_json(output_dir / "script.json", package.script)
    store.write_json(output_dir / "storyboard.json", package.storyboard)
    store.write_json(
        output_dir / "ab_plan.json",
        {"variants": [item.model_dump(mode="json") for item in package.ab_variants]},
    )
    metrics = {
        "stage": "stage_11_creative",
        "cache_key": cache_key,
        "cache_hit": False,
        "attempt_count": inference.attempt_count,
        "hook_count": len(package.hooks),
        "scene_count": len(package.script.scenes),
        "storyboard_frame_count": len(package.storyboard.frames),
        "ab_variant_count": len(package.ab_variants),
        "task": context["task"],
        "effective_mode_profile": {
            "mode": context["task"].get("mode", "quick"),
            "creative_depth": "deep" if context["task"].get("mode") == "deep" else "standard",
        },
        "total_seconds": round(time.perf_counter() - started, 6),
    }
    store.write_json(metrics_path, metrics)
    manifest_path = output_root / "manifest.json"
    manifest: dict[str, Any] = (
        store.read_json(manifest_path)
        if manifest_path.is_file()
        else {"schema_version": settings.project.schema_version, "run_id": run_id}
    )
    stages_value = manifest.setdefault("stages", {})
    if not isinstance(stages_value, dict):
        raise ValueError("Manifest stages must be an object")
    stages_value["stage_11_creative"] = {
        "cache_key": cache_key,
        "artifacts": {
            "package": "creative/package.json",
            "hooks": "creative/hooks.json",
            "script": "creative/script.json",
            "storyboard": "creative/storyboard.json",
            "ab_plan": "creative/ab_plan.json",
            "metrics": "stage_11_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifact_root": str(output_root),
        **_paths(output_dir),
        "metrics": metrics,
    }


def _paths(output_dir: Path) -> dict[str, Any]:
    return {
        "package": str(output_dir / "package.json"),
        "hooks": str(output_dir / "hooks.json"),
        "script": str(output_dir / "script.json"),
        "storyboard": str(output_dir / "storyboard.json"),
        "ab_plan": str(output_dir / "ab_plan.json"),
    }
