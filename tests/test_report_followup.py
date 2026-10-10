from __future__ import annotations

import json
from pathlib import Path

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.reports import builder
from ad_vista_agent.schemas import MarketingAnalysis


def test_explicit_empty_report_is_downloadable_and_keeps_review_status(tmp_path, monkeypatch):
    settings = load_settings()
    settings.paths.output_root = tmp_path
    run_dir = tmp_path / "runs" / "ingest_empty"
    for part in ("ledger", "timeline", "insights"):
        (run_dir / part).mkdir(parents=True, exist_ok=True)
    for name in ("ledger/evidence.jsonl", "ledger/clusters.jsonl", "timeline/keyframes.jsonl"):
        (run_dir / name).write_text("")
    (run_dir / "ledger/ledger.json").write_text('{"duration_ms":1000}')
    asset = {
        "asset_id": "asset_empty", "source_path": str(tmp_path / "video.mp4"),
        "filename": "video.mp4", "media_type": "video/mp4", "size_bytes": 0,
        "sha256": "0" * 64,
        "metadata": {"format_name": "mp4", "duration_ms": 1000, "video_streams": [], "audio_streams": []},
    }
    (run_dir / "asset.json").write_text(json.dumps(asset))
    analysis = MarketingAnalysis(
        asset_id="asset_empty", language="zh", subject="视频证据缺口",
        executive_summary="当前没有足够的产品证据形成卖点。", insights=[],
        unknowns=["当前 Ledger 无证据表明产品名称、价格或材质。"],
    )
    (run_dir / "insights/analysis.json").write_text(analysis.model_dump_json())
    monkeypatch.setattr(builder, "ingest_video", lambda *_: {"run_id": "ingest_empty", "run_dir": str(run_dir)})
    with pytest.raises(ValueError, match="没有形成可验证"):
        builder.build_report(tmp_path / "video.mp4", settings)
    result = builder.build_report(tmp_path / "video.mp4", settings, allow_empty=True)
    assert result["audit_status"] == "review"
    assert result["insight_count"] == 0
    document = Path(result["html"]).read_text()
    assert "没有足够的产品证据" in document
    assert "empty_analysis" in document
    assert "审计：建议复核" in document
    assert "产品名称、价格或材质" in document
    assert builder.build_report(tmp_path / "video.mp4", settings, allow_empty=True)["cache_hit"]
