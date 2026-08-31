from ad_vista_agent.ledger.rules import build_ocr_clusters
from ad_vista_agent.ocr.parse import ocr_quality_flags
from ad_vista_agent.schemas import BoundingBox, Evidence, EvidenceModality, EpistemicStatus
from pathlib import Path


def _evidence(identifier: str, content: str, confidence: float, flags: list[str]) -> Evidence:
    return Evidence(
        evidence_id=identifier,
        asset_id="asset_test",
        modality=EvidenceModality.OCR,
        start_ms=1000,
        end_ms=1000,
        content=content,
        confidence=confidence,
        epistemic_status=EpistemicStatus.OBSERVED,
        tool="paddleocr",
        model_version="test",
        artifact_path=Path("frame.jpg"),
        artifact_hash="a" * 64,
        region=BoundingBox(x1=0.1, y1=0.1, x2=0.4, y2=0.2),
        metadata={"quality_flags": flags},
    )


def test_ocr_quality_flags_preserve_suspicious_raw_text() -> None:
    assert "possible_mojibake" in ocr_quality_flags("PREGATIT SÃ")
    assert "short_text" in ocr_quality_flags("我")


def test_ocr_cluster_prefers_cleaner_longer_candidate() -> None:
    clusters = build_ocr_clusters(
        [
            _evidence("ocr_1", "SÃ", 0.99, ["possible_mojibake"]),
            _evidence("ocr_2", "Nenuco Sara", 0.90, []),
        ],
        similarity_threshold=0.0,
        max_gap_ms=2500,
        min_region_iou=0.5,
    )

    assert clusters[0].canonical_content == "Nenuco Sara"
    assert clusters[0].representative_evidence_id == "ocr_2"
