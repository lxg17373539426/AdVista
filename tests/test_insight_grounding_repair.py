import json

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.errors import AnalysisGroundingError
from ad_vista_agent.insights import builder
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import Evidence, EvidenceCluster, EvidenceLedger
from ad_vista_agent.tools import QwenInsightResult, QwenInsightTool
from ad_vista_agent.web.service import WebService


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    base = load_settings()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
        "insight": base.insight.model_copy(update={"runtime": "openai"}),
    })
    store = ArtifactStore(settings.paths.output_root)
    run_dir = store.prepare("test")
    evidence = Evidence(
        evidence_id="ocr_0001", asset_id="asset_test", modality="ocr",
        start_ms=0, end_ms=1000, content="besties don't copy, we match",
        epistemic_status="observed", tool="ocr",
    )
    cluster = EvidenceCluster(
        cluster_id="ocr_cluster_0001", asset_id="asset_test", modality="ocr",
        start_ms=0, end_ms=1000, canonical_content=evidence.content,
        representative_evidence_id=evidence.evidence_id,
        member_evidence_ids=[evidence.evidence_id], confidence=0.8,
    )
    store.write_jsonl(run_dir / "ledger/evidence.jsonl", [evidence])
    store.write_jsonl(run_dir / "ledger/clusters.jsonl", [cluster])
    store.write_jsonl(run_dir / "ledger/relations.jsonl", [])
    store.write_jsonl(run_dir / "timeline/keyframes.jsonl", [])
    store.write_json(run_dir / "ledger/ledger.json", EvidenceLedger(
        asset_id="asset_test", duration_ms=1000, source_evidence_count=1,
        ordered_evidence_ids=[evidence.evidence_id], cluster_ids=[cluster.cluster_id],
        relation_ids=[], modality_counts={"ocr": 1}, configuration={},
    ))
    monkeypatch.setattr(builder, "ingest_video", lambda *args: {
        "run_id": "test", "run_dir": str(run_dir),
    })
    monkeypatch.setattr(builder, "_runtime_model_identity", lambda *args: {"model": "test"})
    return tmp_path / "video.mp4", settings, run_dir


def draft(claim, reference="ocr_cluster_0001"):
    return {
        "asset_id": "asset_test", "language": "zh", "subject": "绿色饮品杯",
        "executive_summary": "需要重新生成的草稿摘要", "unknowns": [],
        "insights": [{
            "insight_id": "selling_01", "dimension": "selling_point", "claim": claim,
            "evidence_refs": [reference], "confidence": 0.8,
            "epistemic_status": "inferred_from_ad", "reasoning_summary": "依据字幕解读好友主题。",
        }],
    }


def responses(monkeypatch, drafts):
    calls = []

    def run(self, context, arguments):
        calls.append(arguments)
        text = json.dumps(drafts[len(calls) - 1], ensure_ascii=False)
        return QwenInsightResult(
            text=text, attempts=[text], attempt_count=1,
            prompt_tokens=10, completion_tokens=20,
            versions={name: "unknown" for name in ("vllm", "torch", "transformers")},
        )

    monkeypatch.setattr(QwenInsightTool, "run", run)
    return calls


def test_chinese_interpretation_of_english_ocr_is_repaired_and_revalidated(pipeline, monkeypatch):
    video, settings, run_dir = pipeline
    calls = responses(monkeypatch, [
        draft("产品主打‘匹配’而非‘复制’，强调好友间的默契与同步感。"),
        draft("字幕‘besties don't copy, we match’通过‘匹配’表达好友默契主题。"),
    ])
    result = builder.build_insights(video, settings)
    assert result["status"] == "ok"
    assert len(calls) == 2
    assert "validation_error" in calls[1]["messages"][-1]["content"]
    metrics = result["metrics"]
    assert metrics["grounding_repair"]["status"] == "repaired"
    assert metrics["prompt_tokens"] == 20
    assert metrics["completion_tokens"] == 40
    raw = json.loads((run_dir / "insights/raw_response.json").read_text())
    assert raw["selected_attempt"] == 1
    assert raw["attempt_count"] == len(raw["attempts"]) == 2
    analysis = json.loads((run_dir / "insights/analysis.json").read_text())
    assert "需要重新生成" not in analysis["executive_summary"]
    assert "besties don't copy" in analysis["insights"][0]["claim"]
    # A validated cache hit performs no additional model requests.
    assert builder.build_insights(video, settings)["cache_hit"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("bad_draft", [
    draft("字幕‘besties don't copy’表达好友默契。", "ocr_cluster_9999"),
    draft("产品具有临床抗衰老功效。"),
])
def test_repair_is_bounded_and_never_publishes_invalid_analysis(pipeline, monkeypatch, bad_draft):
    video, settings, run_dir = pipeline
    calls = responses(monkeypatch, [bad_draft, bad_draft])
    with pytest.raises(AnalysisGroundingError, match="after grounding repair") as failure:
        builder.build_insights(video, settings)
    assert len(calls) == 2
    assert not (run_dir / "insights/analysis.json").exists()
    raw_path = next(run_dir.glob(".stage_6_*/insights/raw_response.json"))
    raw = json.loads(raw_path.read_text())
    assert raw["grounding_repair"]["status"] == "failed"
    assert len(raw["grounding_repair"]["validation_errors"]) == 2
    assert WebService._classify_failure(str(failure.value), failure.value)["code"] == "grounding_failed"


def test_valid_output_does_not_trigger_content_repair(pipeline, monkeypatch):
    video, settings, _ = pipeline
    calls = responses(monkeypatch, [draft("字幕‘besties don't copy, we match’表达好友默契。")])
    assert builder.build_insights(video, settings)["metrics"]["grounding_repair"]["attempt_count"] == 0
    assert len(calls) == 1


def test_saved_legacy_lexical_failure_gets_actionable_classification():
    failure = WebService._classify_failure(
        "Tool insights failed: Insight selling_01 cites evidence with no obvious lexical support"
    )
    assert failure["code"] == "grounding_failed"
    assert failure["category"] == "validation"
