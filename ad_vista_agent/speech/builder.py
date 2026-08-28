from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import AdAsset, EpistemicStatus, Evidence, EvidenceModality, SpeechTranscript
from ad_vista_agent.timeline.builder import build_timeline
from ad_vista_agent.tools import FasterWhisperTool, ToolContext

from .clean import clean_segments


FASTER_WHISPER_VERSION = "1.2.1"
CTRANSLATE2_VERSION = "4.8.1"


def _model_identity(model_path: Path) -> dict[str, object]:
    config = model_path / "config.json"
    weights = model_path / "model.bin"
    if not config.is_file() or not weights.is_file():
        raise FileNotFoundError(f"Invalid CTranslate2 model directory: {model_path}")
    stat = weights.stat()
    return {
        "path": str(model_path.resolve()),
        "config_sha256": sha256_file(config),
        "weights_size": stat.st_size,
        "weights_mtime_ns": stat.st_mtime_ns,
    }


def _cache_payload(asset: AdAsset, settings: Settings) -> dict[str, Any]:
    return {
        "asset_sha256": asset.sha256,
        "stage": "stage_3_speech",
        "schema_version": settings.project.schema_version,
        "model": _model_identity(settings.model_path(settings.asr.model)),
        "faster_whisper_version": FASTER_WHISPER_VERSION,
        "ctranslate2_version": CTRANSLATE2_VERSION,
        "device": settings.asr.device,
        "device_index": settings.asr.device_index,
        "compute_type": settings.asr.compute_type,
        "beam_size": settings.asr.beam_size,
        "vad_filter": settings.asr.vad_filter,
        "condition_on_previous_text": settings.asr.condition_on_previous_text,
        "word_timestamps": settings.asr.word_timestamps,
        "merge_gap_ms": settings.asr.merge_gap_ms,
    }


def _load_evidence(path: Path) -> list[Evidence]:
    rows: list[Evidence] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(Evidence.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid speech evidence at {path}:{line_number}") from exc
    return rows


def build_speech(video_path: Path, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    total_started = time.perf_counter()
    timeline_result = build_timeline(video_path, settings)
    run_id = str(timeline_result["run_id"])
    run_dir = Path(str(timeline_result["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    asset = AdAsset.model_validate(store.read_json(run_dir / "asset.json"))
    speech_dir = run_dir / "speech"
    transcript_path = speech_dir / "transcript.json"
    evidence_path = speech_dir / "evidence.jsonl"
    metrics_path = run_dir / "stage_3_metrics.json"
    manifest_path = run_dir / "manifest.json"
    cache_payload = _cache_payload(asset, settings)
    cache_key = stage_cache_key(cache_payload)

    if not force and transcript_path.is_file() and evidence_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            transcript = SpeechTranscript.model_validate(store.read_json(transcript_path))
            evidence = _load_evidence(evidence_path)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "language": transcript.language,
                "segment_count": len(transcript.segments),
                "evidence_count": len(evidence),
                "cache_key": cache_key,
            }

    has_audio = bool(asset.metadata.audio_streams)
    inference_started = time.perf_counter()
    versions = {
        "faster_whisper": FASTER_WHISPER_VERSION,
        "ctranslate2": CTRANSLATE2_VERSION,
    }
    language = None
    language_probability = None
    raw_segments = []
    if has_audio:
        tool = FasterWhisperTool(
            python_executable=settings.asr.python_executable,
            timeout_seconds=settings.asr.timeout_seconds,
            cuda_visible_devices=settings.hardware.cuda_visible_devices,
        )
        result = tool.run(
            ToolContext(run_id=run_id, run_dir=run_dir),
            {
                "video_path": str(asset.source_path),
                "model_path": str(settings.model_path(settings.asr.model)),
                "device": settings.asr.device,
                "device_index": 0,
                "compute_type": settings.asr.compute_type,
                "beam_size": settings.asr.beam_size,
                "vad_filter": settings.asr.vad_filter,
                "condition_on_previous_text": settings.asr.condition_on_previous_text,
                "word_timestamps": settings.asr.word_timestamps,
            },
        )
        language = result.language
        language_probability = result.language_probability
        raw_segments = result.segments
        versions = result.versions
        if versions != {
            "faster_whisper": FASTER_WHISPER_VERSION,
            "ctranslate2": CTRANSLATE2_VERSION,
        }:
            raise RuntimeError(f"Unexpected ASR worker versions: {versions}")
    inference_seconds = time.perf_counter() - inference_started

    cleaned = clean_segments(
        raw_segments,
        duration_ms=asset.metadata.duration_ms,
        merge_gap_ms=settings.asr.merge_gap_ms,
    )
    generation = {
        "device": settings.asr.device,
        "configured_device_index": settings.asr.device_index,
        "visible_device_index": 0,
        "compute_type": settings.asr.compute_type,
        "beam_size": settings.asr.beam_size,
        "vad_filter": settings.asr.vad_filter,
        "condition_on_previous_text": settings.asr.condition_on_previous_text,
        "word_timestamps": settings.asr.word_timestamps,
        "merge_gap_ms": settings.asr.merge_gap_ms,
        "versions": versions,
    }
    transcript = SpeechTranscript(
        asset_id=asset.asset_id,
        duration_ms=asset.metadata.duration_ms,
        language=language,
        language_probability=language_probability,
        has_audio=has_audio,
        segments=cleaned,
        tool="faster_whisper",
        model=settings.asr.model,
        model_version=FASTER_WHISPER_VERSION,
        generation=generation,
    )
    store.write_json(transcript_path, transcript)
    transcript_hash = sha256_file(transcript_path)
    evidence = [
        Evidence(
            evidence_id=segment.segment_id,
            asset_id=asset.asset_id,
            modality=EvidenceModality.SPEECH,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            content=segment.text,
            confidence=segment.confidence,
            epistemic_status=EpistemicStatus.STATED_BY_AD,
            tool="faster_whisper",
            model_version=FASTER_WHISPER_VERSION,
            artifact_path=Path("speech/transcript.json"),
            artifact_hash=transcript_hash,
            metadata={
                "language": language,
                "word_count": len(segment.words),
                "words": [word.model_dump(mode="json") for word in segment.words],
            },
        )
        for segment in cleaned
    ]
    store.write_jsonl(evidence_path, evidence)
    total_seconds = time.perf_counter() - total_started
    duration_seconds = asset.metadata.duration_ms / 1000
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_3_speech",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "has_audio": has_audio,
        "language": language,
        "raw_segment_count": len(raw_segments),
        "segment_count": len(cleaned),
        "word_count": sum(len(segment.words) for segment in cleaned),
        "video_duration_seconds": duration_seconds,
        "inference_seconds": round(inference_seconds, 6),
        "realtime_factor": round(inference_seconds / duration_seconds, 6),
        "total_seconds": round(total_seconds, 6),
    }
    store.write_json(metrics_path, metrics)
    manifest = store.read_json(manifest_path)
    stages = manifest.setdefault("stages", {})
    stages["stage_3_speech"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "transcript": "speech/transcript.json",
            "evidence": "speech/evidence.jsonl",
            "metrics": "stage_3_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "language": language,
        "segment_count": len(cleaned),
        "evidence_count": len(evidence),
        "cache_key": cache_key,
        "metrics": metrics,
    }
