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
from ad_vista_agent.schemas import AdAsset, Keyframe, Shot, Timeline, TimelineCoverage
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
        "keyframe_strategy": "adaptive_shot_start_middle_end_v1",
        "schema_version": settings.project.schema_version,
        "scene_detector": settings.timeline.detector,
        "scene_detector_version": version("scenedetect"),
        "scene_threshold": settings.timeline.scene_threshold,
        "min_shot_ms": settings.timeline.min_shot_ms,
        "frame_extractor_version": _executable_version(settings.tools.ffmpeg_executable),
        "frame_format": settings.timeline.frame_format,
        "jpeg_quality": settings.timeline.jpeg_quality,
        "keyframe_middle_min_shot_ms": settings.timeline.keyframe_middle_min_shot_ms,
        "keyframe_end_min_shot_ms": settings.timeline.keyframe_end_min_shot_ms,
        "max_keyframes_per_shot": settings.timeline.max_keyframes_per_shot,
        "visual_change_enabled": settings.timeline.visual_change_enabled,
        "visual_change_interval_ms": settings.timeline.visual_change_interval_ms,
        "visual_change_threshold": settings.timeline.visual_change_threshold,
        "visual_change_width": settings.timeline.visual_change_width,
        "visual_change_height": settings.timeline.visual_change_height,
    }


def _visual_change_timestamp(
    video_path: Path,
    shot: Shot,
    settings: Settings,
) -> tuple[int | None, float | None]:
    """Find the strongest low-resolution visual change inside a shot."""
    if not settings.timeline.visual_change_enabled:
        return None, None
    duration_ms = shot.end_ms - shot.start_ms
    interval_ms = settings.timeline.visual_change_interval_ms
    if duration_ms <= interval_ms:
        return None, None
    command = [
        settings.tools.ffmpeg_executable,
        "-v",
        "error",
        "-ss",
        f"{shot.start_ms / 1000:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration_ms / 1000:.3f}",
        "-vf",
        (
            f"fps=1000/{interval_ms},scale={settings.timeline.visual_change_width}:"
            f"{settings.timeline.visual_change_height},format=gray"
        ),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    frame_size = settings.timeline.visual_change_width * settings.timeline.visual_change_height
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=settings.tools.frame_extract_timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0 or frame_size <= 0:
        return None, None
    raw = result.stdout
    frame_count = len(raw) // frame_size
    if frame_count < 2:
        return None, None
    best_index = -1
    best_score = 0.0
    previous = raw[:frame_size]
    for index in range(1, frame_count):
        current = raw[index * frame_size : (index + 1) * frame_size]
        score = sum(abs(left - right) for left, right in zip(previous, current)) / (
            255.0 * frame_size
        )
        if score > best_score:
            best_index = index
            best_score = score
        previous = current
    if best_index < 0 or best_score < settings.timeline.visual_change_threshold:
        return None, best_score
    timestamp = min(shot.end_ms - 1, shot.start_ms + best_index * interval_ms)
    if timestamp <= shot.start_ms or timestamp >= shot.end_ms:
        return None, best_score
    return timestamp, best_score


def _keyframe_specs(
    shot: Shot,
    settings: Settings,
    visual_change_timestamp: int | None = None,
) -> list[tuple[int, str, str]]:
    """Return bounded representative samples while preserving a shot-start anchor."""
    duration_ms = shot.end_ms - shot.start_ms
    timestamp_ms = shot.start_ms
    specs: list[tuple[int, str, str]] = [(timestamp_ms, "primary", "shot_start")]
    if visual_change_timestamp is not None:
        specs.append((visual_change_timestamp, "secondary", "visual_change"))
    if (
        settings.timeline.max_keyframes_per_shot >= 2
        and duration_ms >= settings.timeline.keyframe_middle_min_shot_ms
    ):
        middle = shot.start_ms + duration_ms // 2
        if middle < shot.end_ms and middle != shot.start_ms:
            specs.append((middle, "secondary", "shot_middle"))
    if (
        settings.timeline.max_keyframes_per_shot >= 3
        and duration_ms >= settings.timeline.keyframe_end_min_shot_ms
    ):
        # Sample the late part of the shot without seeking onto the exact
        # boundary; some codecs return an empty frame near the video end.
        end = shot.start_ms + max(1, int(duration_ms * 0.9))
        end = min(end, shot.end_ms - 1)
        if end > shot.start_ms and all(timestamp != end for timestamp, _, _ in specs):
            specs.append((end, "secondary", "shot_end"))
    unique: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    for spec in specs:
        if spec[0] not in seen:
            unique.append(spec)
            seen.add(spec[0])
    return unique[: settings.timeline.max_keyframes_per_shot]


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
            cached_keyframes = _load_keyframes(keyframes_path)
            validate_keyframes(timeline, cached_keyframes, run_dir)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "shot_count": len(timeline.shots),
                "keyframe_count": len(cached_keyframes),
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
        for stale_frame in frames_dir.glob("shot_*.jpg"):
            stale_frame.unlink()
    for shot in timeline.shots:
        visual_change_timestamp, _ = _visual_change_timestamp(asset.source_path, shot, settings)
        for sample_index, (timestamp_ms, role, reason) in enumerate(
            _keyframe_specs(shot, settings, visual_change_timestamp),
            1,
        ):
            timestamp_ms = min(timestamp_ms, shot.end_ms - 1)
            suffix = "primary" if role == "primary" else f"{reason}_{sample_index:02d}"
            relative_path = Path("timeline") / "frames" / f"{shot.shot_id}_{suffix}.jpg"
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
                    keyframe_id=f"kf_{shot.index + 1:04d}_{suffix}",
                    asset_id=asset.asset_id,
                    shot_id=shot.shot_id,
                    timestamp_ms=timestamp_ms,
                    frame_number=round(timestamp_ms * fps / 1000),
                    role=role,
                    selection_reason=reason,
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
