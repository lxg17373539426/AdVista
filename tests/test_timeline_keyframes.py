from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ad_vista_agent.runtime import sha256_file
from ad_vista_agent.schemas import Keyframe, Shot, Timeline, TimelineCoverage
from ad_vista_agent.timeline.builder import _keyframe_specs, _visual_change_timestamp
from ad_vista_agent.timeline.validate import validate_keyframes


def _settings(*, max_keyframes_per_shot: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        timeline=SimpleNamespace(
            keyframe_middle_min_shot_ms=1500,
            keyframe_end_min_shot_ms=3000,
            max_keyframes_per_shot=max_keyframes_per_shot,
            visual_change_enabled=True,
            visual_change_interval_ms=1000,
            visual_change_threshold=0.18,
            visual_change_width=64,
            visual_change_height=36,
        ),
        tools=SimpleNamespace(
            ffmpeg_executable="ffmpeg",
            frame_extract_timeout_seconds=60,
        ),
    )


def _shot(start_ms: int, end_ms: int) -> Shot:
    return Shot(
        shot_id="shot_test",
        asset_id="asset_test",
        index=0,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=end_ms - start_ms,
        start_frame=0,
        end_frame=max(0, end_ms // 33 - 1),
        detector="test",
    )


def test_adaptive_keyframe_specs_keep_primary_and_add_long_shot_samples() -> None:
    settings = _settings()

    short = _keyframe_specs(_shot(0, 1000), cast(Any, settings))
    medium = _keyframe_specs(_shot(0, 2000), cast(Any, settings))
    long = _keyframe_specs(_shot(0, 4000), cast(Any, settings))

    assert [(time, role, reason) for time, role, reason in short] == [
        (0, "primary", "shot_start")
    ]
    assert [(time, role, reason) for time, role, reason in medium] == [
        (0, "primary", "shot_start"),
        (1000, "secondary", "shot_middle"),
    ]
    assert [(time, role, reason) for time, role, reason in long] == [
        (0, "primary", "shot_start"),
        (2000, "secondary", "shot_middle"),
        (3600, "secondary", "shot_end"),
    ]


def test_keyframe_budget_limits_adaptive_samples() -> None:
    specs = _keyframe_specs(
        _shot(1000, 6000), cast(Any, _settings(max_keyframes_per_shot=2))
    )

    assert len(specs) == 2
    assert specs[0][1:] == ("primary", "shot_start")
    assert specs[1][1:] == ("secondary", "shot_middle")


def test_keyframe_specs_prioritize_visual_change_before_middle() -> None:
    specs = _keyframe_specs(
        _shot(0, 5000),
        cast(Any, _settings(max_keyframes_per_shot=3)),
        visual_change_timestamp=1200,
    )

    assert specs == [
        (0, "primary", "shot_start"),
        (1200, "secondary", "visual_change"),
        (2500, "secondary", "shot_middle"),
    ]


def test_visual_change_timestamp_returns_strongest_change(monkeypatch) -> None:
    settings = _settings()
    settings.timeline.visual_change_interval_ms = 1000
    settings.timeline.visual_change_threshold = 0.1
    settings.timeline.visual_change_width = 2
    settings.timeline.visual_change_height = 1

    # Three 2-pixel grayscale frames: no change, then a strong change.
    raw = bytes([10, 10, 10, 10, 200, 200])

    class Result:
        returncode = 0
        stdout = raw

    monkeypatch.setattr("ad_vista_agent.timeline.builder.subprocess.run", lambda *args, **kwargs: Result())

    timestamp, score = _visual_change_timestamp(
        Path("video.mp4"), _shot(1000, 4000), cast(Any, settings)
    )

    assert timestamp == 3000
    assert score is not None and score > 0.7


def test_visual_change_timestamp_ignores_below_threshold(monkeypatch) -> None:
    settings = _settings()
    settings.timeline.visual_change_interval_ms = 1000
    settings.timeline.visual_change_threshold = 0.5
    settings.timeline.visual_change_width = 2
    settings.timeline.visual_change_height = 1

    class Result:
        returncode = 0
        stdout = bytes([10, 10, 20, 20, 30, 30])

    monkeypatch.setattr("ad_vista_agent.timeline.builder.subprocess.run", lambda *args, **kwargs: Result())

    timestamp, score = _visual_change_timestamp(
        Path("video.mp4"), _shot(1000, 4000), cast(Any, settings)
    )

    assert timestamp is None
    assert score is not None and score < 0.5


def test_validate_keyframes_allows_secondary_frames_and_requires_one_primary(tmp_path: Path) -> None:
    asset_id = "asset_test"
    shot = Shot(
        shot_id="shot_0001",
        asset_id=asset_id,
        index=0,
        start_ms=0,
        end_ms=4000,
        duration_ms=4000,
        start_frame=0,
        end_frame=119,
        detector="test",
    )
    timeline = Timeline(
        asset_id=asset_id,
        duration_ms=4000,
        shots=[shot],
        coverage=TimelineCoverage(start_ms=0, end_ms=4000, gap_ms=0, overlap_ms=0),
    )
    keyframes = []
    for keyframe_id, timestamp_ms, role, suffix in (
        ("kf_0001_primary", 0, "primary", "primary"),
        ("kf_0001_middle_02", 2000, "secondary", "middle"),
        ("kf_0001_end_03", 3999, "secondary", "end"),
    ):
        path = tmp_path / f"{suffix}.jpg"
        path.write_bytes(keyframe_id.encode("ascii"))
        keyframes.append(
            Keyframe(
                keyframe_id=keyframe_id,
                asset_id=asset_id,
                shot_id=shot.shot_id,
                timestamp_ms=timestamp_ms,
                frame_number=timestamp_ms // 33,
                role=role,
                selection_reason=f"shot_{suffix}",
                artifact_path=path.relative_to(tmp_path),
                artifact_sha256=sha256_file(path),
                width=1,
                height=1,
            )
        )

    validate_keyframes(timeline, keyframes, tmp_path)


@pytest.mark.parametrize("roles", [("secondary",), ("primary", "primary")])
def test_validate_keyframes_rejects_missing_or_duplicate_primary(
    tmp_path: Path, roles: tuple[str, ...]
) -> None:
    asset_id = "asset_test"
    shot = _shot(0, 2000).model_copy(update={"shot_id": "shot_0001"})
    timeline = Timeline(
        asset_id=asset_id,
        duration_ms=2000,
        shots=[shot],
        coverage=TimelineCoverage(start_ms=0, end_ms=2000, gap_ms=0, overlap_ms=0),
    )
    keyframes = []
    for index, role in enumerate(roles):
        path = tmp_path / f"frame_{index}.jpg"
        path.write_bytes(f"frame-{index}".encode("ascii"))
        keyframes.append(
            Keyframe(
                keyframe_id=f"kf_{index}",
                asset_id=asset_id,
                shot_id=shot.shot_id,
                timestamp_ms=index * 100,
                frame_number=index,
                role=role,
                selection_reason="test",
                artifact_path=path.relative_to(tmp_path),
                artifact_sha256=sha256_file(path),
                width=1,
                height=1,
            )
        )

    with pytest.raises(ValueError, match="exactly one primary"):
        validate_keyframes(timeline, keyframes, tmp_path)
