from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ad_vista_agent.agent.orchestrator import (
    PIPELINE_STAGE_NAMES,
    resume_pipeline,
    run_pipeline,
)
from ad_vista_agent.config import load_settings
from ad_vista_agent.insights.grounding import validate_analysis_grounding
from ad_vista_agent.reports.critic import audit_analysis
from ad_vista_agent.runtime import sha256_file
from ad_vista_agent.schemas import Evidence, MarketingAnalysis

ROOT = Path(__file__).resolve().parents[1]


def artifact_metrics() -> dict[str, Any]:
    runs = []
    for run_dir in (ROOT / "outputs" / "runs").glob("ingest_*"):
        metrics = {}
        for path in run_dir.glob("stage_*_metrics.json"):
            metrics[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        if all(f"stage_{stage}_metrics" in metrics for stage in range(2, 6)):
            runs.append(metrics)

    durations = [row["stage_2_metrics"]["video_duration_ms"] / 1000 for row in runs]
    evidence_counts = [row["stage_5_metrics"]["source_evidence_count"] for row in runs]
    stage_seconds = {
        f"stage_{stage}": [row[f"stage_{stage}_metrics"]["total_seconds"] for row in runs]
        for stage in range(2, 6)
    }
    return {
        "video_count": len(runs),
        "total_video_seconds": round(sum(durations), 3),
        "mean_video_seconds": round(statistics.mean(durations), 3),
        "total_ledger_evidence": sum(evidence_counts),
        "mean_ledger_evidence": round(statistics.mean(evidence_counts), 3),
        "total_ocr_clusters": sum(
            row["stage_5_metrics"]["ocr_cluster_count"] for row in runs
        ),
        "collapsed_ocr_duplicates": sum(
            row["stage_5_metrics"]["ocr_duplicate_count"] for row in runs
        ),
        "stage_mean_seconds": {
            name: round(statistics.mean(values), 3) for name, values in stage_seconds.items()
        },
        "stage_median_seconds": {
            name: round(statistics.median(values), 3) for name, values in stage_seconds.items()
        },
    }


def execution_metrics() -> dict[str, Any]:
    sessions = []
    for path in (ROOT / "outputs" / "executions").glob("*/session.json"):
        sessions.append(json.loads(path.read_text(encoding="utf-8")))
    statuses = Counter(str(row.get("status")) for row in sessions)
    completed = [
        row for row in sessions if row.get("status") in {"completed", "waiting_confirmation"}
    ]
    cache_results = [
        bool(call["observation"]["cache_hit"])
        for row in completed
        for call in row.get("tool_calls", [])
        if isinstance(call.get("observation"), dict)
        and "cache_hit" in call["observation"]
    ]
    return {
        "execution_count": len(sessions),
        "unique_run_count": len({row.get("run_id") for row in sessions}),
        "status_counts": dict(statuses),
        "completed_tool_cache_observations": len(cache_results),
        "completed_tool_cache_hits": sum(cache_results),
        "historical_cache_hit_rate": round(sum(cache_results) / len(cache_results), 4),
        "note": "Historical development executions; not a controlled task-success benchmark.",
    }


def recovery_metrics() -> dict[str, Any]:
    results = []
    for failed_stage in PIPELINE_STAGE_NAMES[1:]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.write_bytes(b"fault-injection-video")
            base = load_settings(ROOT / "configs" / "local.yaml")
            settings = base.model_copy(
                update={"paths": base.paths.model_copy(update={"output_root": root / "outputs"})}
            )
            run_id = "ingest_fault_injection"
            run_dir = settings.paths.output_root / "runs" / run_id
            calls = Counter()
            injected = False

            def runner(
                name: str, calls=calls, run_dir=run_dir,
                failed_stage=failed_stage, run_id=run_id,
            ):
                def run(path: Path, loaded: Any, force: bool) -> dict[str, Any]:
                    nonlocal injected
                    del path, loaded, force
                    calls[name] += 1
                    run_dir.mkdir(parents=True, exist_ok=True)
                    if name == failed_stage and not injected:
                        injected = True
                        raise RuntimeError(f"injected failure at {name}")
                    output: dict[str, Any] = {
                        "status": "ok",
                        "cache_hit": False,
                        "run_id": run_id,
                        "run_dir": str(run_dir),
                    }
                    if name == "report":
                        report_dir = run_dir / "report"
                        report_dir.mkdir(parents=True, exist_ok=True)
                        output.update(
                            {
                                "audit_status": "pass",
                                "markdown": str(report_dir / "report.md"),
                                "html": str(report_dir / "report.html"),
                            }
                        )
                    return output

                return run

            runners = {name: runner(name) for name in PIPELINE_STAGE_NAMES}
            try:
                run_pipeline(
                    video,
                    settings,
                    config_path=ROOT / "configs" / "local.yaml",
                    stage_runners=runners,
                )
            except RuntimeError:
                pass
            resumed = resume_pipeline(run_id, settings, stage_runners=runners)
            failed_index = PIPELINE_STAGE_NAMES.index(failed_stage)
            previous_not_repeated = all(
                calls[name] == 1 for name in PIPELINE_STAGE_NAMES[:failed_index]
            )
            results.append(
                {
                    "failed_stage": failed_stage,
                    "recovered": resumed["status"] == "completed",
                    "previous_stages_not_repeated": previous_not_repeated,
                    "call_counts": dict(calls),
                }
            )
    passed = sum(row["recovered"] and row["previous_stages_not_repeated"] for row in results)
    return {
        "scenario_count": len(results),
        "passed": passed,
        "recovery_rate": round(passed / len(results), 4),
        "scenarios": results,
        "note": "Deterministic fault injection at each post-ingestion pipeline stage.",
    }


def _analysis(claim: str, reference: str, *, dimension: str = "selling_point") -> MarketingAnalysis:
    return MarketingAnalysis.model_validate(
        {
            "asset_id": "asset_eval",
            "language": "zh",
            "subject": "测试广告",
            "executive_summary": "测试摘要",
            "insights": [
                {
                    "insight_id": "insight_001",
                    "dimension": dimension,
                    "claim": claim,
                    "evidence_refs": [reference],
                    "confidence": 0.7,
                    "epistemic_status": "stated_by_ad",
                    "reasoning_summary": "依据广告证据。",
                }
            ],
            "unknowns": [],
        }
    )


def validation_metrics() -> dict[str, Any]:
    grounding_cases = [
        ("unknown_reference", _analysis("Nenuco 产品", "ocr_cluster_9999"), {"ocr_cluster_0001"}, {"ocr_cluster_0001": "Nenuco"}),
        ("unsupported_ocr_claim", _analysis("临床抗衰老功效", "ocr_cluster_0001"), {"ocr_cluster_0001"}, {"ocr_cluster_0001": "37"}),
        ("inline_citation", _analysis("Nenuco 产品 ocr_cluster_0001", "ocr_cluster_0001"), {"ocr_cluster_0001"}, {"ocr_cluster_0001": "Nenuco"}),
        ("sensitive_identity", _analysis("画面人物的真实性别为女性", "ocr_cluster_0001", dimension="audience"), {"ocr_cluster_0001"}, {"ocr_cluster_0001": "女性"}),
    ]
    results = []
    for name, analysis, allowed, content in grounding_cases:
        blocked = False
        try:
            validate_analysis_grounding(
                analysis,
                asset_id="asset_eval",
                allowed_references=allowed,
                max_per_dimension=5,
                reference_content=content,
            )
        except ValueError:
            blocked = True
        results.append({"case": name, "expected": "blocked", "passed": blocked})

    with TemporaryDirectory() as directory:
        run_dir = Path(directory)
        artifact = run_dir / "evidence.txt"
        artifact.write_text("Nenuco", encoding="utf-8")
        evidence = Evidence.model_validate(
            {
                "evidence_id": "ocr_0001",
                "asset_id": "asset_eval",
                "modality": "ocr",
                "start_ms": 0,
                "end_ms": 1000,
                "content": "Nenuco",
                "confidence": 0.9,
                "epistemic_status": "observed",
                "tool": "evaluation",
                "artifact_path": "evidence.txt",
                "artifact_hash": sha256_file(artifact),
            }
        )
        audit = audit_analysis(
            _analysis("临床抗衰老功效", "ocr_0001"),
            evidence=[evidence],
            clusters=[],
            keyframes=[],
            duration_ms=1000,
            run_dir=run_dir,
        )
        results.append(
            {
                "case": "single_modality_high_risk_claim",
                "expected": "review",
                "passed": audit.status.value == "review",
            }
        )
        artifact.write_text("tampered", encoding="utf-8")
        audit = audit_analysis(
            _analysis("Nenuco 产品", "ocr_0001"),
            evidence=[evidence],
            clusters=[],
            keyframes=[],
            duration_ms=1000,
            run_dir=run_dir,
        )
        results.append(
            {
                "case": "tampered_evidence_artifact",
                "expected": "failed",
                "passed": audit.status.value == "fail",
            }
        )
        artifact.unlink()
        audit = audit_analysis(
            _analysis("Nenuco 产品", "ocr_0001"),
            evidence=[evidence],
            clusters=[],
            keyframes=[],
            duration_ms=1000,
            run_dir=run_dir,
        )
        results.append(
            {
                "case": "missing_evidence_artifact",
                "expected": "failed",
                "passed": audit.status.value == "fail",
            }
        )

    passed = sum(row["passed"] for row in results)
    return {
        "adversarial_case_count": len(results),
        "passed": passed,
        "detection_rate": round(passed / len(results), 4),
        "cases": results,
        "note": "Fixed synthetic validation cases; not a production accuracy metric.",
    }


def main() -> None:
    report = {
        "artifact_metrics": artifact_metrics(),
        "execution_metrics": execution_metrics(),
        "recovery_metrics": recovery_metrics(),
        "validation_metrics": validation_metrics(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
