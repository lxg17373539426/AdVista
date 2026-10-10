from __future__ import annotations

from pathlib import Path

from ad_vista_agent.runtime.fingerprint import sha256_file
from ad_vista_agent.schemas import Keyframe, Timeline


def validate_keyframes(timeline: Timeline, keyframes: list[Keyframe], run_dir: Path) -> None:
    shots = {shot.shot_id: shot for shot in timeline.shots}
    if not keyframes:
        raise ValueError("Timeline must contain at least one keyframe")
    frames_by_shot: dict[str, list[Keyframe]] = {}
    for frame in keyframes:
        frames_by_shot.setdefault(frame.shot_id, []).append(frame)
    if set(frames_by_shot) != set(shots):
        raise ValueError("Each shot must have at least one keyframe")
    for shot_id, frames in frames_by_shot.items():
        primary = [frame for frame in frames if frame.role == "primary"]
        if len(primary) != 1:
            raise ValueError(f"Each shot must have exactly one primary keyframe: {shot_id}")
        if len({frame.timestamp_ms for frame in frames}) != len(frames):
            raise ValueError(f"Keyframe timestamps must be unique within a shot: {shot_id}")

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
