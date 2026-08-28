import unittest

from pydantic import ValidationError

from ad_vista_agent.schemas import BoundingBox, EpistemicStatus, Evidence, EvidenceModality


class EvidenceSchemaTests(unittest.TestCase):
    def test_valid_evidence(self) -> None:
        evidence = Evidence(
            evidence_id="ev_1",
            asset_id="asset_1",
            modality=EvidenceModality.OCR,
            start_ms=100,
            end_ms=200,
            content="Buy now",
            confidence=0.9,
            epistemic_status=EpistemicStatus.OBSERVED,
            tool="test_ocr",
            region=BoundingBox(x1=0.1, y1=0.2, x2=0.8, y2=0.9),
        )
        self.assertEqual(evidence.end_ms, 200)

    def test_rejects_reversed_interval(self) -> None:
        with self.assertRaises(ValidationError):
            Evidence(
                evidence_id="ev_1",
                asset_id="asset_1",
                modality=EvidenceModality.VIDEO,
                start_ms=200,
                end_ms=100,
                content="invalid",
                epistemic_status=EpistemicStatus.OBSERVED,
                tool="test",
            )

    def test_rejects_invalid_bounding_box(self) -> None:
        with self.assertRaises(ValidationError):
            BoundingBox(x1=0.8, y1=0.2, x2=0.1, y2=0.9)


if __name__ == "__main__":
    unittest.main()
