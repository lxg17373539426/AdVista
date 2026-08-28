import unittest

from pydantic import ValidationError

from ad_vista_agent.schemas import SpeechTranscript, TranscriptSegment, TranscriptWord
from ad_vista_agent.speech import clean_segments


class SpeechSchemaTests(unittest.TestCase):
    def test_rejects_segment_outside_duration(self) -> None:
        segment = TranscriptSegment(segment_id="raw", start_ms=900, end_ms=1100, text="hello")
        with self.assertRaises(ValidationError):
            SpeechTranscript(
                asset_id="asset_1",
                duration_ms=1000,
                language="en",
                has_audio=True,
                segments=[segment],
                tool="faster_whisper",
                model="model",
                model_version="1",
                generation={},
            )

    def test_no_audio_requires_empty_segments(self) -> None:
        segment = TranscriptSegment(segment_id="raw", start_ms=0, end_ms=100, text="hello")
        with self.assertRaises(ValidationError):
            SpeechTranscript(
                asset_id="asset_1",
                duration_ms=1000,
                has_audio=False,
                segments=[segment],
                tool="faster_whisper",
                model="model",
                model_version="1",
                generation={},
            )


class SpeechCleanupTests(unittest.TestCase):
    def test_merges_adjacent_segments_and_renumbers(self) -> None:
        segments = [
            TranscriptSegment(
                segment_id="raw_1",
                start_ms=0,
                end_ms=500,
                text=" hello ",
                confidence=0.8,
                words=[TranscriptWord(start_ms=0, end_ms=400, text=" hello", probability=0.9)],
            ),
            TranscriptSegment(
                segment_id="raw_2",
                start_ms=700,
                end_ms=1000,
                text="world",
                confidence=0.6,
            ),
        ]
        cleaned = clean_segments(segments, duration_ms=1000, merge_gap_ms=500)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].segment_id, "speech_0001")
        self.assertEqual(cleaned[0].text, "hello world")
        self.assertAlmostEqual(cleaned[0].confidence or 0, 0.7)

    def test_drops_empty_and_clamps_duration(self) -> None:
        segments = [
            TranscriptSegment(segment_id="raw_1", start_ms=0, end_ms=100, text="  "),
            TranscriptSegment(segment_id="raw_2", start_ms=900, end_ms=1200, text="end"),
        ]
        cleaned = clean_segments(segments, duration_ms=1000, merge_gap_ms=0)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].end_ms, 1000)


if __name__ == "__main__":
    unittest.main()
