from __future__ import annotations

from dataclasses import dataclass, field

from ad_vista_agent.schemas.timeline import Shot


@dataclass
class RawShot:
    start_ms: int
    end_ms: int
    score: float | None = None
    actions: list[str] = field(default_factory=list)


def _frame_number(timestamp_ms: int, fps: float) -> int:
    return max(0, round(timestamp_ms * fps / 1000))


def normalize_shots(
    raw_shots: list[RawShot],
    *,
    asset_id: str,
    duration_ms: int,
    fps: float,
    min_shot_ms: int,
    detector: str,
) -> list[Shot]:
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if min_shot_ms <= 0:
        raise ValueError("min_shot_ms must be positive")

    valid = [
        raw
        for raw in raw_shots
        if raw.end_ms > raw.start_ms and raw.end_ms > 0 and raw.start_ms < duration_ms
    ]
    valid.sort(key=lambda item: (item.start_ms, item.end_ms))

    if not valid:
        normalized = [RawShot(0, duration_ms, actions=["fallback_full_video"])]
    else:
        cut_points = {0, duration_ms}
        for raw in valid[:-1]:
            cut_points.add(min(duration_ms, max(0, raw.end_ms)))
        ordered = sorted(cut_points)
        normalized = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            score = next((item.score for item in valid if item.end_ms == end), None)
            normalized.append(RawShot(start, end, score, ["boundaries_normalized"]))

    index = 0
    while len(normalized) > 1 and index < len(normalized):
        current = normalized[index]
        if current.end_ms - current.start_ms >= min_shot_ms:
            index += 1
            continue
        if index == 0:
            following = normalized[1]
            following.start_ms = current.start_ms
            following.actions = current.actions + ["merged_short_first_forward"] + following.actions
            normalized.pop(0)
        else:
            previous = normalized[index - 1]
            previous.end_ms = current.end_ms
            previous.actions.extend(current.actions + ["merged_short_into_previous"])
            normalized.pop(index)
            index = max(0, index - 1)

    shots: list[Shot] = []
    for shot_index, raw in enumerate(normalized):
        start_frame = _frame_number(raw.start_ms, fps)
        end_frame = max(start_frame, _frame_number(raw.end_ms, fps) - 1)
        shots.append(
            Shot(
                shot_id=f"shot_{shot_index + 1:04d}",
                asset_id=asset_id,
                index=shot_index,
                start_ms=raw.start_ms,
                end_ms=raw.end_ms,
                duration_ms=raw.end_ms - raw.start_ms,
                start_frame=start_frame,
                end_frame=end_frame,
                detector=detector,
                detector_score=raw.score,
                normalization_actions=raw.actions,
            )
        )
    return shots
