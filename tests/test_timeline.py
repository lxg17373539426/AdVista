import unittest
import tempfile
from pathlib import Path

from pydantic import ValidationError

from ad_vista_agent.runtime import sha256_file
from ad_vista_agent.schemas import Keyframe, Shot, Timeline, TimelineCoverage
from ad_vista_agent.timeline import RawShot, normalize_shots
from ad_vista_agent.timeline.validate import validate_keyframes


class TimelineSchemaTests(unittest.TestCase):
    def test_rejects_incorrect_duration(self) -> None:
        with self.assertRaises(ValidationError):
            Shot(
                shot_id="shot_0001",
                asset_id="asset_1",
                index=0,
                start_ms=0,
                end_ms=1000,
                duration_ms=999,
                start_frame=0,
                end_frame=24,
                detector="test",
            )

    def test_rejects_gap(self) -> None:
        shots = [
            Shot(shot_id="shot_0001", asset_id="asset_1", index=0, start_ms=0, end_ms=500, duration_ms=500, start_frame=0, end_frame=12, detector="test"),
            Shot(shot_id="shot_0002", asset_id="asset_1", index=1, start_ms=600, end_ms=1000, duration_ms=400, start_frame=15, end_frame=24, detector="test"),
        ]
        with self.assertRaises(ValidationError):
            Timeline(
                asset_id="asset_1",
                duration_ms=1000,
                shots=shots,
                coverage=TimelineCoverage(start_ms=0, end_ms=1000, gap_ms=0, overlap_ms=0),
            )


class NormalizeTimelineTests(unittest.TestCase):
    def test_empty_detection_falls_back_to_full_video(self) -> None:
        shots = normalize_shots([], asset_id="asset_1", duration_ms=1000, fps=25, min_shot_ms=200, detector="test")
        self.assertEqual(len(shots), 1)
        self.assertEqual((shots[0].start_ms, shots[0].end_ms), (0, 1000))
        self.assertIn("fallback_full_video", shots[0].normalization_actions)

    def test_short_first_shot_merges_forward(self) -> None:
        shots = normalize_shots(
            [RawShot(0, 100), RawShot(100, 700), RawShot(700, 1000)],
            asset_id="asset_1",
            duration_ms=1000,
            fps=25,
            min_shot_ms=200,
            detector="test",
        )
        self.assertEqual([(shot.start_ms, shot.end_ms) for shot in shots], [(0, 700), (700, 1000)])
        self.assertIn("merged_short_first_forward", shots[0].normalization_actions)

    def test_short_middle_shot_merges_into_previous(self) -> None:
        shots = normalize_shots(
            [RawShot(0, 400), RawShot(400, 500), RawShot(500, 1000)],
            asset_id="asset_1",
            duration_ms=1000,
            fps=25,
            min_shot_ms=200,
            detector="test",
        )
        self.assertEqual([(shot.start_ms, shot.end_ms) for shot in shots], [(0, 500), (500, 1000)])
        self.assertIn("merged_short_into_previous", shots[0].normalization_actions)

    def test_normalized_shots_build_valid_timeline(self) -> None:
        shots = normalize_shots(
            [RawShot(20, 500), RawShot(510, 990)],
            asset_id="asset_1",
            duration_ms=1000,
            fps=25,
            min_shot_ms=100,
            detector="test",
        )
        timeline = Timeline(
            asset_id="asset_1",
            duration_ms=1000,
            shots=shots,
            coverage=TimelineCoverage(start_ms=0, end_ms=1000, gap_ms=0, overlap_ms=0),
        )
        self.assertEqual(len(timeline.shots), 2)


class KeyframeValidationTests(unittest.TestCase):
    def test_rejects_artifact_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            artifact = run_dir / "timeline" / "frames" / "shot_0001_primary.jpg"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"frame")
            shot = Shot(
                shot_id="shot_0001",
                asset_id="asset_1",
                index=0,
                start_ms=0,
                end_ms=1000,
                duration_ms=1000,
                start_frame=0,
                end_frame=24,
                detector="test",
            )
            timeline = Timeline(
                asset_id="asset_1",
                duration_ms=1000,
                shots=[shot],
                coverage=TimelineCoverage(start_ms=0, end_ms=1000, gap_ms=0, overlap_ms=0),
            )
            valid_hash = sha256_file(artifact)
            keyframe = Keyframe(
                keyframe_id="kf_0001_primary",
                asset_id="asset_1",
                shot_id="shot_0001",
                timestamp_ms=500,
                frame_number=12,
                selection_reason="shot_midpoint",
                artifact_path=Path("timeline/frames/shot_0001_primary.jpg"),
                artifact_sha256="0" * 64,
                width=10,
                height=10,
            )
            self.assertNotEqual(keyframe.artifact_sha256, valid_hash)
            with self.assertRaises(ValueError):
                validate_keyframes(timeline, [keyframe], run_dir)


if __name__ == "__main__":
    unittest.main()
