from __future__ import annotations

import json
import subprocess
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import AdAsset, Keyframe, Timeline, TimelineCoverage
from ad_vista_agent.tools import FrameExtractTool, SceneDetectTool, ToolContext

from .normalize import normalize_shots
from .validate import validate_keyframes


def _executable_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unavailable"
    first_line = result.stdout.splitlines()[0] if result.stdout else "unavailable"
    return first_line.strip()


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


def _cache_payload(asset: AdAsset, settings: Settings) -> dict[str, Any]:
    return {
        "asset_sha256": asset.sha256,
        "stage": "stage_2_timeline",
        "schema_version": settings.project.schema_version,
        "scene_detector": settings.timeline.detector,
        "scene_detector_version": version("scenedetect"),
        "scene_threshold": settings.timeline.scene_threshold,
        "min_shot_ms": settings.timeline.min_shot_ms,
        "frame_extractor_version": _executable_version(settings.tools.ffmpeg_executable),
        "frame_format": settings.timeline.frame_format,
        "jpeg_quality": settings.timeline.jpeg_quality,
    }


def build_timeline(video_path: Path, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    total_started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    asset = AdAsset.model_validate(ingestion["asset"])
    timeline_dir = run_dir / "timeline"
    timeline_path = timeline_dir / "shots.json"
    keyframes_path = timeline_dir / "keyframes.jsonl"
    metrics_path = run_dir / "stage_2_metrics.json"
    manifest_path = run_dir / "manifest.json"

    cache_payload = _cache_payload(asset, settings)
    cache_key = stage_cache_key(cache_payload)
    if not force and timeline_path.is_file() and keyframes_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            timeline = Timeline.model_validate(store.read_json(timeline_path))
            keyframes = _load_keyframes(keyframes_path)
            validate_keyframes(timeline, keyframes, run_dir)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "shot_count": len(timeline.shots),
                "keyframe_count": len(keyframes),
                "cache_key": cache_key,
            }

    fps = asset.metadata.video_streams[0].fps
    min_scene_len_frames = max(1, round(settings.timeline.min_shot_ms * fps / 1000))
    context = ToolContext(run_id=run_id, run_dir=run_dir)
    detector = SceneDetectTool()
    detection_started = time.perf_counter()
    detection = detector.run(
        context,
        {
            "video_path": asset.source_path,
            "threshold": settings.timeline.scene_threshold,
            "min_scene_len_frames": min_scene_len_frames,
        },
    )
    detection_seconds = time.perf_counter() - detection_started
    shots = normalize_shots(
        detection.shots,
        asset_id=asset.asset_id,
        duration_ms=asset.metadata.duration_ms,
        fps=fps,
        min_shot_ms=settings.timeline.min_shot_ms,
        detector=detection.detector,
    )
    timeline = Timeline(
        asset_id=asset.asset_id,
        duration_ms=asset.metadata.duration_ms,
        shots=shots,
        coverage=TimelineCoverage(
            start_ms=0,
            end_ms=asset.metadata.duration_ms,
            gap_ms=0,
            overlap_ms=0,
        ),
    )

    extractor = FrameExtractTool(
        executable=settings.tools.ffmpeg_executable,
        probe_executable=settings.tools.ffprobe_executable,
        timeout_seconds=settings.tools.frame_extract_timeout_seconds,
    )
    extraction_started = time.perf_counter()
    keyframes: list[Keyframe] = []
    frames_dir = timeline_dir / "frames"
    if frames_dir.is_dir():
        for stale_frame in frames_dir.glob("shot_*_primary.jpg"):
            stale_frame.unlink()
    for shot in timeline.shots:
        timestamp_ms = shot.start_ms + shot.duration_ms // 2
        timestamp_ms = min(timestamp_ms, shot.end_ms - 1)
        relative_path = Path("timeline") / "frames" / f"{shot.shot_id}_primary.jpg"
        extracted = extractor.run(
            context,
            {
                "video_path": asset.source_path,
                "timestamp_ms": timestamp_ms,
                "output_path": relative_path,
                "jpeg_quality": settings.timeline.jpeg_quality,
            },
        )
        keyframes.append(
            Keyframe(
                keyframe_id=f"kf_{shot.index + 1:04d}_primary",
                asset_id=asset.asset_id,
                shot_id=shot.shot_id,
                timestamp_ms=timestamp_ms,
                frame_number=round(timestamp_ms * fps / 1000),
                selection_reason="shot_midpoint",
                artifact_path=relative_path,
                artifact_sha256=extracted.sha256,
                width=extracted.width,
                height=extracted.height,
            )
        )
    extraction_seconds = time.perf_counter() - extraction_started
    validate_keyframes(timeline, keyframes, run_dir)

    frame_bytes = sum((run_dir / frame.artifact_path).stat().st_size for frame in keyframes)
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_2_timeline",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "video_duration_ms": asset.metadata.duration_ms,
        "processed_frames": detection.processed_frames,
        "raw_shot_count": len(detection.shots),
        "shot_count": len(timeline.shots),
        "keyframe_count": len(keyframes),
        "keyframe_bytes": frame_bytes,
        "scene_detection_seconds": round(detection_seconds, 6),
        "frame_extraction_seconds": round(extraction_seconds, 6),
        "total_seconds": round(time.perf_counter() - total_started, 6),
    }
    store.write_json(timeline_path, timeline)
    store.write_jsonl(keyframes_path, keyframes)
    store.write_json(metrics_path, metrics)

    manifest = store.read_json(manifest_path)
    stages = manifest.setdefault("stages", {})
    stages["stage_2_timeline"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "timeline": "timeline/shots.json",
            "keyframes": "timeline/keyframes.jsonl",
            "frames": "timeline/frames",
            "metrics": "stage_2_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "shot_count": len(timeline.shots),
        "keyframe_count": len(keyframes),
        "cache_key": cache_key,
        "metrics": metrics,
    }
