from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ad_vista_agent.reports.evidence import build_evidence_document


def test_evidence_document_includes_asr_ocr_and_keyframe_images(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "ingest_test"
    ledger_dir = run_dir / "ledger"
    timeline_dir = run_dir / "timeline"
    frame_path = timeline_dir / "frames" / "shot_0001_primary.jpg"
    frame_path.parent.mkdir(parents=True)
    ledger_dir.mkdir(parents=True)
    frame_bytes = b"test-jpeg-bytes"
    frame_path.write_bytes(frame_bytes)

    asset = {
        "asset_id": "asset_test",
        "source_path": str(tmp_path / "video.mp4"),
        "filename": "video.mp4",
        "media_type": "video/mp4",
        "size_bytes": 1,
        "sha256": "0" * 64,
        "metadata": {
            "format_name": "mp4",
            "duration_ms": 2000,
            "video_streams": [],
            "audio_streams": [],
        },
    }
    (run_dir / "asset.json").write_text(json.dumps(asset), encoding="utf-8")
    evidence = [
        {
            "evidence_id": "speech_0001",
            "asset_id": "asset_test",
            "modality": "speech",
            "start_ms": 0,
            "end_ms": 1500,
            "content": "语音介绍产品",
            "confidence": 0.95,
            "epistemic_status": "stated_by_ad",
            "tool": "asr",
        },
        {
            "evidence_id": "ocr_0001",
            "asset_id": "asset_test",
            "modality": "ocr",
            "start_ms": 0,
            "end_ms": 0,
            "content": "画面促销文字",
            "epistemic_status": "observed",
            "tool": "ocr",
            "artifact_path": "timeline/frames/shot_0001_primary.jpg",
            "artifact_hash": hashlib.sha256(frame_bytes).hexdigest(),
            "metadata": {"keyframe_id": "kf_0001_primary"},
        },
    ]
    (ledger_dir / "evidence.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in evidence),
        encoding="utf-8",
    )
    (ledger_dir / "clusters.jsonl").write_text("", encoding="utf-8")
    keyframe = {
        "keyframe_id": "kf_0001_primary",
        "asset_id": "asset_test",
        "shot_id": "shot_0001",
        "timestamp_ms": 0,
        "frame_number": 0,
        "role": "primary",
        "selection_reason": "shot_start",
        "artifact_path": "timeline/frames/shot_0001_primary.jpg",
        "artifact_sha256": hashlib.sha256(frame_bytes).hexdigest(),
        "width": 360,
        "height": 640,
    }
    (timeline_dir / "keyframes.jsonl").write_text(
        json.dumps(keyframe) + "\n", encoding="utf-8"
    )

    result = build_evidence_document(run_dir)

    html = Path(str(result["html"])).read_text(encoding="utf-8")
    markdown = Path(str(result["markdown"])).read_text(encoding="utf-8")
    assert "语音介绍产品" in html
    assert "画面促销文字" in html
    assert 'src="data:image/jpeg;base64,' in html
    assert "kf_0001_primary" in html
    assert "关键帧 1 张" in markdown
    assert "kf_0001_primary" in markdown
    assert "timeline/frames/shot_0001_primary.jpg" in markdown
    assert result["keyframe_count"] == 1
