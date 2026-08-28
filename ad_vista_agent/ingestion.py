from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .runtime import ArtifactStore, sha256_file
from .schemas import AdAsset
from .tools import ToolContext, VideoProbeTool


SUPPORTED_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def ingest_video(video_path: Path, settings: Settings) -> dict[str, Any]:
    source = video_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported video extension: {source.suffix or '<none>'}")

    content_hash = sha256_file(source)
    asset_id = f"asset_{content_hash[:20]}"
    run_id = f"ingest_{content_hash[:20]}"
    store = ArtifactStore(settings.paths.output_root)
    with store.lock(f"ingest_{content_hash}"):
        run_dir = store.prepare(run_id)
        asset_path = run_dir / "asset.json"
        manifest_path = run_dir / "manifest.json"

        if asset_path.is_file() and manifest_path.is_file():
            cached = AdAsset.model_validate(store.read_json(asset_path))
            if cached.sha256 == content_hash:
                return {
                    "status": "ok",
                    "cache_hit": True,
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "asset": cached.model_dump(mode="json"),
                }

        probe = VideoProbeTool(
            executable=settings.tools.ffprobe_executable,
            timeout_seconds=settings.tools.probe_timeout_seconds,
        )
        context = ToolContext(run_id=run_id, run_dir=run_dir)
        metadata = probe.run(context, {"video_path": source})
        asset = AdAsset(
            asset_id=asset_id,
            source_path=source,
            filename=source.name,
            media_type=f"video/{source.suffix.lstrip('.').casefold()}",
            size_bytes=source.stat().st_size,
            sha256=content_hash,
            metadata=metadata,
        )
        manifest = {
            "schema_version": settings.project.schema_version,
            "run_id": run_id,
            "stage": "stage_1_ingestion",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"path": str(source), "sha256": content_hash},
            "artifacts": {"asset": "asset.json"},
            "tools": {"video_probe": settings.tools.ffprobe_executable},
            "runtime": {"python": sys.version.split()[0], "platform": platform.platform()},
        }
        store.write_json(asset_path, asset)
        store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "asset": asset.model_dump(mode="json"),
    }
