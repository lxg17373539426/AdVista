from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import (
    AdAsset,
    BoundingBox,
    EpistemicStatus,
    Evidence,
    EvidenceModality,
    Keyframe,
    OcrFrameResult,
    OcrRegion,
)
from ad_vista_agent.timeline.builder import build_timeline
from ad_vista_agent.tools import OcrWorkerResult, OcrWorkerTool, ToolContext

from .parse import ocr_quality_flags, parse_deepseek_grounding


DEEPSEEK_VERSIONS = {
    "torch": "2.6.0+cu124",
    "transformers": "4.46.3",
    "flash_attn": "2.7.3",
}
PADDLE_VERSIONS = {"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"}
OCR_PIPELINE_VERSION = "1"


def _model_identity(model_path: Path) -> dict[str, object]:
    config = model_path / "config.json"
    weights = model_path / "model-00001-of-000001.safetensors"
    if not config.is_file() or not weights.is_file():
        raise FileNotFoundError(f"Invalid DeepSeek-OCR model directory: {model_path}")
    stat = weights.stat()
    return {
        "path": str(model_path.resolve()),
        "config_sha256": sha256_file(config),
        "weights_size": stat.st_size,
        "weights_mtime_ns": stat.st_mtime_ns,
    }


def _load_keyframes(path: Path) -> list[Keyframe]:
    frames: list[Keyframe] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                frames.append(Keyframe.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"Invalid keyframe at {path}:{line_number}") from exc
    return frames


def _load_frame_results(path: Path) -> list[OcrFrameResult]:
    rows: list[OcrFrameResult] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(OcrFrameResult.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid OCR frame result at {path}:{line_number}") from exc
    return rows


def _load_evidence(path: Path) -> list[Evidence]:
    rows: list[Evidence] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(Evidence.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid OCR evidence at {path}:{line_number}") from exc
    return rows


def _cache_payload(asset: AdAsset, keyframes: list[Keyframe], settings: Settings) -> dict[str, Any]:
    return {
        "asset_sha256": asset.sha256,
        "stage": "stage_4_ocr",
        "schema_version": settings.project.schema_version,
        "ocr_pipeline_version": OCR_PIPELINE_VERSION,
        "keyframes": [
            {"keyframe_id": frame.keyframe_id, "sha256": frame.artifact_sha256}
            for frame in keyframes
        ],
        "model": _model_identity(settings.model_path(settings.ocr.model)),
        "primary": settings.ocr.primary,
        "primary_versions": DEEPSEEK_VERSIONS,
        "fallback": settings.ocr.fallback,
        "fallback_versions": PADDLE_VERSIONS,
        "prompt": settings.ocr.prompt,
        "base_size": settings.ocr.base_size,
        "image_size": settings.ocr.image_size,
        "crop_mode": settings.ocr.crop_mode,
        "paddle_score_threshold": settings.ocr.paddle_score_threshold,
    }


def _image_items(keyframes: list[Keyframe], run_dir: Path) -> list[dict[str, str]]:
    return [
        {
            "keyframe_id": frame.keyframe_id,
            "path": str((run_dir / frame.artifact_path).resolve()),
        }
        for frame in keyframes
    ]


def _validate_evidence(evidence: list[Evidence], keyframes: list[Keyframe]) -> None:
    by_id = {frame.keyframe_id: frame for frame in keyframes}
    for item in evidence:
        keyframe_id = str(item.metadata.get("keyframe_id") or "")
        frame = by_id.get(keyframe_id)
        if frame is None:
            raise ValueError(f"OCR evidence references an unknown keyframe: {keyframe_id}")
        if item.start_ms != frame.timestamp_ms or item.end_ms != frame.timestamp_ms:
            raise ValueError(f"OCR evidence timestamp does not match keyframe: {item.evidence_id}")
        if item.artifact_path != frame.artifact_path:
            raise ValueError(f"OCR evidence path does not match keyframe: {item.evidence_id}")
        if item.artifact_hash != frame.artifact_sha256:
            raise ValueError(f"OCR evidence hash does not match keyframe: {item.evidence_id}")


def _deepseek_results(
    keyframes: list[Keyframe], run_dir: Path, settings: Settings
) -> tuple[dict[str, tuple[str, list[OcrRegion]]], dict[str, str], float]:
    started = time.perf_counter()
    tool = OcrWorkerTool(
        settings.ocr.deepseek_python,
        settings.ocr.timeout_seconds,
        settings.hardware.cuda_visible_devices,
    )
    merged: dict[str, tuple[str, list[OcrRegion]]] = {}
    versions: dict[str, str] = {}
    items = _image_items(keyframes, run_dir)
    work_dir = run_dir / "ocr" / "deepseek_work"
    try:
        result = tool.run(
            ToolContext(run_id=run_dir.name, run_dir=run_dir),
            {
                "backend": "deepseek_ocr",
                "model_path": str(settings.model_path(settings.ocr.model)),
                "prompt": settings.ocr.prompt,
                "images": items,
                "output_root": str(work_dir),
                "base_size": settings.ocr.base_size,
                "image_size": settings.ocr.image_size,
                "crop_mode": settings.ocr.crop_mode,
            },
        )
        versions = result.versions
        for item in result.items:
            raw = str(item.get("text") or "")
            merged[str(item["keyframe_id"])] = (raw, parse_deepseek_grounding(raw))
    finally:
        if work_dir.is_dir():
            shutil.rmtree(work_dir)
    if versions != DEEPSEEK_VERSIONS:
        raise RuntimeError(f"Unexpected DeepSeek-OCR worker versions: {versions}")
    return merged, versions, time.perf_counter() - started


def _polygon_region(polygon: Any, width: int, height: int) -> BoundingBox | None:
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        return BoundingBox(
            x1=max(0, min(xs)) / width,
            y1=max(0, min(ys)) / height,
            x2=min(width, max(xs)) / width,
            y2=min(height, max(ys)) / height,
        )
    except (TypeError, ValueError, IndexError):
        return None


def _paddle_results(
    keyframes: list[Keyframe], run_dir: Path, settings: Settings
) -> tuple[dict[str, list[OcrRegion]], dict[str, str], float]:
    if not keyframes:
        return {}, {}, 0.0
    started = time.perf_counter()
    tool = OcrWorkerTool(
        settings.ocr.paddle_python,
        settings.ocr.timeout_seconds,
        settings.hardware.cuda_visible_devices,
    )
    result: OcrWorkerResult = tool.run(
        ToolContext(run_id=run_dir.name, run_dir=run_dir),
        {"backend": "paddleocr", "images": _image_items(keyframes, run_dir)},
    )
    if result.versions != PADDLE_VERSIONS:
        raise RuntimeError(f"Unexpected PaddleOCR worker versions: {result.versions}")
    by_id: dict[str, list[OcrRegion]] = {}
    frames = {frame.keyframe_id: frame for frame in keyframes}
    for item in result.items:
        frame = frames[str(item["keyframe_id"])]
        regions: list[OcrRegion] = []
        for text, score, polygon in zip(
            item.get("texts", []), item.get("scores", []), item.get("polygons", [])
        ):
            region = _polygon_region(polygon, frame.width, frame.height)
            value = " ".join(str(text).split())
            confidence = float(score)
            if value and region is not None and confidence >= settings.ocr.paddle_score_threshold:
                regions.append(
                    OcrRegion(text=value, region=region, confidence=confidence, label="text")
                )
        by_id[frame.keyframe_id] = regions
    return by_id, result.versions, time.perf_counter() - started


def build_ocr(video_path: Path, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    total_started = time.perf_counter()
    timeline_result = build_timeline(video_path, settings)
    run_id = str(timeline_result["run_id"])
    run_dir = Path(str(timeline_result["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    asset = AdAsset.model_validate(store.read_json(run_dir / "asset.json"))
    keyframes = _load_keyframes(run_dir / "timeline" / "keyframes.jsonl")
    ocr_dir = run_dir / "ocr"
    raw_path = ocr_dir / "raw.jsonl"
    evidence_path = ocr_dir / "evidence.jsonl"
    metrics_path = run_dir / "stage_4_metrics.json"
    manifest_path = run_dir / "manifest.json"
    cache_payload = _cache_payload(asset, keyframes, settings)
    cache_key = stage_cache_key(cache_payload)

    if not force and raw_path.is_file() and evidence_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            frames = _load_frame_results(raw_path)
            evidence = _load_evidence(evidence_path)
            _validate_evidence(evidence, keyframes)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "frame_count": len(frames),
                "evidence_count": len(evidence),
                "cache_key": cache_key,
            }

    deepseek_error = None
    try:
        deepseek, deepseek_versions, deepseek_seconds = _deepseek_results(keyframes, run_dir, settings)
    except Exception as exc:
        deepseek = {}
        deepseek_versions = {}
        deepseek_seconds = 0.0
        deepseek_error = str(exc)

    missing = [frame for frame in keyframes if not deepseek.get(frame.keyframe_id, ("", []))[1]]
    paddle, paddle_versions, paddle_seconds = _paddle_results(missing, run_dir, settings)
    frame_results: list[OcrFrameResult] = []
    for frame in keyframes:
        raw, regions = deepseek.get(frame.keyframe_id, ("", []))
        backend = "deepseek_ocr"
        if not regions:
            regions = paddle.get(frame.keyframe_id, [])
            backend = "paddleocr" if frame.keyframe_id in paddle else "deepseek_ocr"
        frame_results.append(
            OcrFrameResult(
                keyframe_id=frame.keyframe_id,
                shot_id=frame.shot_id,
                timestamp_ms=frame.timestamp_ms,
                frame_path=str(frame.artifact_path),
                backend=backend,
                raw_text=raw,
                regions=regions,
            )
        )

    store.write_jsonl(raw_path, frame_results)
    evidence: list[Evidence] = []
    evidence_index = 0
    for frame in frame_results:
        for region in frame.regions:
            evidence_index += 1
            evidence.append(
                Evidence(
                    evidence_id=f"ocr_{evidence_index:04d}",
                    asset_id=asset.asset_id,
                    modality=EvidenceModality.OCR,
                    start_ms=frame.timestamp_ms,
                    end_ms=frame.timestamp_ms,
                    content=region.text,
                    confidence=region.confidence,
                    epistemic_status=EpistemicStatus.OBSERVED,
                    tool=frame.backend,
                    model_version=(
                        "DeepSeek-OCR"
                        if frame.backend == "deepseek_ocr"
                        else "PP-OCRv6"
                    ),
                    artifact_path=Path(frame.frame_path),
                    artifact_hash=next(
                        item.artifact_sha256
                        for item in keyframes
                        if item.keyframe_id == frame.keyframe_id
                    ),
                    region=region.region,
                    metadata={
                        "keyframe_id": frame.keyframe_id,
                        "shot_id": frame.shot_id,
                        "label": region.label,
                        "quality_flags": ocr_quality_flags(region.text),
                        "backend_versions": (
                            DEEPSEEK_VERSIONS
                            if frame.backend == "deepseek_ocr"
                            else PADDLE_VERSIONS
                        ),
                    },
                )
            )
    store.write_jsonl(evidence_path, evidence)
    _validate_evidence(evidence, keyframes)
    total_seconds = time.perf_counter() - total_started
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_4_ocr",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "keyframe_count": len(keyframes),
        "deepseek_frame_count": sum(item.backend == "deepseek_ocr" for item in frame_results),
        "paddle_frame_count": sum(item.backend == "paddleocr" for item in frame_results),
        "evidence_count": len(evidence),
        "deepseek_seconds": round(deepseek_seconds, 6),
        "paddle_seconds": round(paddle_seconds, 6),
        "total_seconds": round(total_seconds, 6),
        "deepseek_versions": deepseek_versions,
        "paddle_versions": paddle_versions,
        "deepseek_error": deepseek_error,
    }
    store.write_json(metrics_path, metrics)
    manifest = store.read_json(manifest_path)
    stages = manifest.setdefault("stages", {})
    stages["stage_4_ocr"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "raw": "ocr/raw.jsonl",
            "evidence": "ocr/evidence.jsonl",
            "metrics": "stage_4_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "frame_count": len(frame_results),
        "evidence_count": len(evidence),
        "cache_key": cache_key,
        "metrics": metrics,
    }
