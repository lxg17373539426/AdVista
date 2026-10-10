import json
import sys
from pathlib import Path

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import EvidenceLedger
from ad_vista_agent.tools import QwenInsightResult, QwenInsightTool

UNKNOWN = {name: "unknown" for name in ("vllm", "torch", "transformers")}


def test_http_inference_does_not_invent_remote_package_versions(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{}'}}]}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    tool = QwenInsightTool(Path(sys.executable), 1, "0", runtime="openai")
    result = tool._run_openai({"messages": [], "temperature": 0, "max_tokens": 10, "output_schema": {}})
    assert result.versions == UNKNOWN


@pytest.mark.parametrize("runtime", ["openai", "subprocess"])
def test_insights_provenance_matches_runtime_without_rejecting_unknown_http(tmp_path, monkeypatch, runtime):
    from ad_vista_agent.insights import builder

    base = load_settings()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
        "insight": base.insight.model_copy(update={"runtime": runtime}),
    })
    store = ArtifactStore(settings.paths.output_root)
    run_dir = store.prepare("test")
    for name in ("evidence", "clusters", "relations"):
        store.write_jsonl(run_dir / "ledger" / f"{name}.jsonl", [])
    store.write_jsonl(run_dir / "timeline" / "keyframes.jsonl", [])
    store.write_json(run_dir / "ledger" / "ledger.json", EvidenceLedger(
        asset_id="asset_test", duration_ms=1000, source_evidence_count=0,
        ordered_evidence_ids=[], cluster_ids=[], relation_ids=[], modality_counts={}, configuration={},
    ))
    monkeypatch.setattr(builder, "ingest_video", lambda *args: {"run_id": "test", "run_dir": str(run_dir)})
    monkeypatch.setattr(builder, "_runtime_model_identity", lambda *args: {"served_model": "test"})
    monkeypatch.setattr(QwenInsightTool, "run", lambda *args: QwenInsightResult(
        text=json.dumps({"asset_id": "asset_test", "language": "zh", "subject": "测试广告", "executive_summary": "", "insights": [], "unknowns": []}),
        attempts=["{}"], attempt_count=1, prompt_tokens=0, completion_tokens=0, versions=UNKNOWN,
    ))
    if runtime == "subprocess":
        with pytest.raises(RuntimeError, match="Unexpected Qwen worker versions"):
            builder.build_insights(tmp_path / "video.mp4", settings)
        return
    assert builder.build_insights(tmp_path / "video.mp4", settings)["status"] == "ok"
    metrics = store.read_json(run_dir / "stage_6_metrics.json")
    assert metrics["versions"] == UNKNOWN
    assert metrics["cache_payload"]["runtime_versions"] == UNKNOWN
    assert store.read_json(run_dir / "insights" / "raw_response.json")["versions"] == UNKNOWN


def test_http_creative_accepts_unknown_versions_and_records_them(tmp_path, monkeypatch):
    from ad_vista_agent.creative import builder

    base = load_settings()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
        "insight": base.insight.model_copy(update={"runtime": "openai"}),
    })
    store = ArtifactStore(settings.paths.output_root)
    run_dir = store.prepare("test")
    monkeypatch.setattr(builder, "ingest_video", lambda *args: {"run_id": "test", "run_dir": str(run_dir)})
    monkeypatch.setattr(builder, "_load_context", lambda *args: ({"ledger": {}, "validated_insights": [], "unknowns": []}, {"speech_0001"}, []))
    monkeypatch.setattr(builder, "_runtime_model_identity", lambda *args: {"served_model": "test"})
    refs = ["speech_0001"]
    package = {
        "hooks": [{"text": "展示产品", "angle": "产品", "evidence_refs": refs}],
        "script": {"title": "产品展示", "target_audience": "感兴趣的人", "call_to_action": "了解产品", "scenes": [{"order": 1, "duration_seconds": 1, "narration": "产品介绍", "visual_direction": "建议展示产品", "evidence_refs": refs}]},
        "storyboard": {"frames": [{"order": 1, "shot_description": "建议展示产品", "camera_direction": "固定镜头", "evidence_refs": refs}]},
        "ab_variants": [{"name": name, "hypothesis": "测试产品表达", "hook_text": "产品展示", "change": "调整开场", "success_metric": "观看率", "evidence_refs": refs} for name in ("A", "B")],
    }
    monkeypatch.setattr(QwenInsightTool, "run", lambda *args: QwenInsightResult(
        text=json.dumps(package), attempts=["{}"], attempt_count=1, prompt_tokens=0, completion_tokens=0, versions=UNKNOWN,
    ))
    assert builder.build_creative(tmp_path / "video.mp4", settings)["status"] == "ok"
    assert store.read_json(run_dir / "stage_11_metrics.json")["versions"] == UNKNOWN
