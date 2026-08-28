import unittest
from pathlib import Path

from ad_vista_agent.ocr import parse_deepseek_grounding
from ad_vista_agent.ocr.builder import _validate_evidence
from ad_vista_agent.schemas import EpistemicStatus, Evidence, EvidenceModality, Keyframe


class DeepSeekGroundingTests(unittest.TestCase):
    def test_parses_text_and_normalized_box(self) -> None:
        raw = "<|ref|>text<|/ref|><|det|>[[100, 200, 800, 900]]<|/det|>\nHello world"
        regions = parse_deepseek_grounding(raw)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].text, "Hello world")
        self.assertAlmostEqual(regions[0].region.x1, 100 / 999)
        self.assertIsNone(regions[0].confidence)

    def test_associates_text_until_next_grounding_marker(self) -> None:
        raw = (
            "<|ref|>title<|/ref|><|det|>[[0, 0, 500, 100]]<|/det|> First title "
            "<|ref|>text<|/ref|><|det|>[[10, 200, 900, 300]]<|/det|> Body text"
        )
        regions = parse_deepseek_grounding(raw)
        self.assertEqual([item.text for item in regions], ["First title", "Body text"])
        self.assertEqual([item.label for item in regions], ["title", "text"])

    def test_skips_invalid_boxes_and_empty_text(self) -> None:
        raw = (
            "<|ref|>text<|/ref|><|det|>not-json<|/det|> text "
            "<|ref|>text<|/ref|><|det|>[[900, 10, 100, 50]]<|/det|> invalid"
        )
        self.assertEqual(parse_deepseek_grounding(raw), [])


class OcrEvidenceValidationTests(unittest.TestCase):
    def test_rejects_evidence_hash_mismatch(self) -> None:
        frame = Keyframe(
            keyframe_id="kf_0001_primary",
            asset_id="asset_1",
            shot_id="shot_0001",
            timestamp_ms=100,
            frame_number=2,
            selection_reason="shot_midpoint",
            artifact_path=Path("timeline/frames/shot_0001_primary.jpg"),
            artifact_sha256="a" * 64,
            width=100,
            height=100,
        )
        evidence = Evidence(
            evidence_id="ocr_0001",
            asset_id="asset_1",
            modality=EvidenceModality.OCR,
            start_ms=100,
            end_ms=100,
            content="text",
            epistemic_status=EpistemicStatus.OBSERVED,
            tool="deepseek_ocr",
            artifact_path=frame.artifact_path,
            artifact_hash="b" * 64,
            metadata={"keyframe_id": frame.keyframe_id},
        )
        with self.assertRaises(ValueError):
            _validate_evidence([evidence], [frame])


if __name__ == "__main__":
    unittest.main()
