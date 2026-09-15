from ad_vista_agent.ledger.rules import build_ocr_clusters
from ad_vista_agent.ocr.parse import ocr_quality_flags, parse_deepseek_grounding
from ad_vista_agent.ocr.quality import build_ocr_quality_report
from ad_vista_agent.schemas import BoundingBox, Evidence, EvidenceModality, EpistemicStatus, OcrFrameResult, OcrRegion
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
        tool="deepseek_ocr",
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


def test_deepseek_plain_text_is_kept_as_frame_evidence() -> None:
    regions = parse_deepseek_grounding("产品名称：清润保湿霜\n产品名称：清润保湿霜")

    assert len(regions) == 1
    assert regions[0].text == "产品名称:清润保湿霜"
    assert regions[0].label == "frame_text"
    assert regions[0].region.x1 == 0
    assert regions[0].region.y2 == 1


def test_deepseek_plain_text_collapses_repeated_watermark_variants() -> None:
    regions = parse_deepseek_grounding(
        "TikTok: Business Creative Center\n"
        "TikToks: Business Creative Center\n"
        "TikTok: Business Creative Center\n"
        "¿ANDAS BUSCANDO PICK-UPS DE TRABAJO?"
    )

    assert regions[0].text.count("TikTok") == 1
    assert "PICK-UPS DE TRABAJO" in regions[0].text


def test_deepseek_image_placeholder_does_not_create_evidence() -> None:
    assert parse_deepseek_grounding("<|ref|>image<|/ref|><|det|>[[0, 0, 999, 999]]<|/det|>") == []


def test_deepseek_math_noise_does_not_create_evidence() -> None:
    raw = r"\[ \text{1.1.1.1.1.1.1.1.1.1.1.1.1.1.1.1.1.1.1.1} \]"

    assert parse_deepseek_grounding(raw) == []


def test_quality_report_flags_empty_and_repeated_output() -> None:
    region = OcrRegion(
        text="TikTok: Business Creative Center",
        region=BoundingBox(x1=0, y1=0, x2=1, y2=1),
        label="frame_text",
    )
    frames = [
        OcrFrameResult(
            keyframe_id="kf_1",
            shot_id="shot_1",
            timestamp_ms=0,
            frame_path="one.jpg",
            backend="deepseek_ocr",
            raw_text=region.text,
            regions=[region],
        ),
        OcrFrameResult(
            keyframe_id="kf_2",
            shot_id="shot_2",
            timestamp_ms=1000,
            frame_path="two.jpg",
            backend="deepseek_ocr",
            raw_text=region.text,
            regions=[region],
        ),
        OcrFrameResult(
            keyframe_id="kf_3",
            shot_id="shot_3",
            timestamp_ms=2000,
            frame_path="three.jpg",
            backend="deepseek_ocr",
            raw_text="",
            regions=[],
        ),
    ]

    report = build_ocr_quality_report(frames)

    assert report["engine"] == "deepseek_ocr"
    assert report["empty_frame_count"] == 1
    assert report["repeated_line_count"] == 1
    assert report["status"] == "review"
