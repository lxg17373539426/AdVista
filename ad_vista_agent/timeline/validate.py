from __future__ import annotations

from pathlib import Path

from ad_vista_agent.runtime.fingerprint import sha256_file
from ad_vista_agent.schemas import Keyframe, Timeline


def validate_keyframes(timeline: Timeline, keyframes: list[Keyframe], run_dir: Path) -> None:
    shots = {shot.shot_id: shot for shot in timeline.shots}
    if len(keyframes) != len(shots):
        raise ValueError("Each shot must have exactly one primary keyframe")
    if len({frame.shot_id for frame in keyframes}) != len(keyframes):
        raise ValueError("Each shot must have only one keyframe in Stage 2")

    for frame in keyframes:
        shot = shots.get(frame.shot_id)
        if shot is None:
            raise ValueError(f"Keyframe references an unknown shot: {frame.shot_id}")
        if frame.asset_id != timeline.asset_id:
            raise ValueError("Keyframe asset does not match timeline asset")
        if not shot.start_ms <= frame.timestamp_ms < shot.end_ms:
            raise ValueError(f"Keyframe timestamp is outside its shot: {frame.keyframe_id}")
        artifact = run_dir / frame.artifact_path
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f"Keyframe artifact is missing or empty: {artifact}")
        if sha256_file(artifact) != frame.artifact_sha256:
            raise ValueError(f"Keyframe artifact hash does not match: {artifact}")
