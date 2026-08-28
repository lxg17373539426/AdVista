import unittest
import tempfile
from pathlib import Path

from ad_vista_agent.ledger import build_ocr_clusters, build_relations, normalize_text
from ad_vista_agent.config import load_settings
from ad_vista_agent.ledger.builder import build_ledger
from ad_vista_agent.schemas import (
    BoundingBox,
    EpistemicStatus,
    Evidence,
    EvidenceModality,
    RelationType,
)


def evidence(
    evidence_id: str,
    modality: EvidenceModality,
    text: str,
    start_ms: int,
    end_ms: int,
    *,
    region: BoundingBox | None = None,
    confidence: float | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        asset_id="asset_1",
        modality=modality,
        start_ms=start_ms,
        end_ms=end_ms,
        content=text,
        confidence=confidence,
        epistemic_status=(
            EpistemicStatus.OBSERVED
            if modality == EvidenceModality.OCR
            else EpistemicStatus.STATED_BY_AD
        ),
        tool="test",
        region=region,
    )


class LedgerRuleTests(unittest.TestCase):
    def test_normalizes_case_quotes_and_punctuation(self) -> None:
        self.assertEqual(normalize_text("  Real-Life ‘Filter’! "), "real life filter")

    def test_clusters_nearby_similar_ocr_with_overlapping_regions(self) -> None:
        box1 = BoundingBox(x1=0.1, y1=0.7, x2=0.8, y2=0.8)
        box2 = BoundingBox(x1=0.11, y1=0.7, x2=0.81, y2=0.8)
        rows = [
            evidence("ocr_1", EvidenceModality.OCR, "Make you look younger", 1000, 1000, region=box1, confidence=0.9),
            evidence("ocr_2", EvidenceModality.OCR, "make you look younger.", 2200, 2200, region=box2, confidence=0.99),
        ]
        clusters = build_ocr_clusters(rows, similarity_threshold=0.92, max_gap_ms=2500, min_region_iou=0.5)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].member_evidence_ids, ["ocr_1", "ocr_2"])
        self.assertEqual(clusters[0].representative_evidence_id, "ocr_2")

    def test_does_not_cluster_same_text_in_distant_time(self) -> None:
        box = BoundingBox(x1=0.1, y1=0.7, x2=0.8, y2=0.8)
        rows = [
            evidence("ocr_1", EvidenceModality.OCR, "Buy now", 1000, 1000, region=box),
            evidence("ocr_2", EvidenceModality.OCR, "Buy now", 10000, 10000, region=box),
        ]
        clusters = build_ocr_clusters(rows, similarity_threshold=0.92, max_gap_ms=2500, min_region_iou=0.5)
        self.assertEqual(len(clusters), 2)

    def test_builds_cross_modal_support_relation(self) -> None:
        rows = [
            evidence("speech_1", EvidenceModality.SPEECH, "Try CoveBalm today", 0, 1000),
            evidence("ocr_1", EvidenceModality.OCR, "Try CoveBalm today", 900, 900, region=BoundingBox(x1=0.1, y1=0.1, x2=0.9, y2=0.2)),
        ]
        relations = build_relations(rows, similarity_threshold=0.8, window_ms=1500, conflict_context_similarity_threshold=0.8)
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].type, RelationType.SUPPORTS)

    def test_marks_only_numeric_context_conflict(self) -> None:
        rows = [
            evidence("speech_1", EvidenceModality.SPEECH, "Get 30 percent off today", 0, 1000),
            evidence("ocr_1", EvidenceModality.OCR, "Get 20 percent off today", 900, 900, region=BoundingBox(x1=0.1, y1=0.1, x2=0.9, y2=0.2)),
        ]
        relations = build_relations(rows, similarity_threshold=0.95, window_ms=1500, conflict_context_similarity_threshold=0.8)
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].type, RelationType.POSSIBLE_CONFLICT)


class LedgerBoundaryTests(unittest.TestCase):
    def test_does_not_auto_run_missing_upstream_model_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.write_bytes(b"not a real video")
            settings = load_settings().model_copy(
                update={"paths": load_settings().paths.model_copy(update={"output_root": root / "outputs"})}
            )
            with self.assertRaises((FileNotFoundError, RuntimeError)):
                build_ledger(video, settings)


if __name__ == "__main__":
    unittest.main()
