from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import (
    AgentRequest,
    CriticAudit,
    Evidence,
    EvidenceCluster,
    Keyframe,
    MarketingAnalysis,
    AdAsset,
)

from .critic import audit_analysis
from .render import render_html_report, render_markdown_report


REPORT_PIPELINE_VERSION = "6"
ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_jsonl(path: Path, model: type[ModelT], label: str) -> list[ModelT]:
    rows: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(model.model_validate(json.loads(line)))
                except Exception as exc:
                    raise ValueError(f"Invalid {label} at {path}:{line_number}") from exc
    return rows


def _cache_payload(
    settings: Settings,
    artifacts: list[Path],
    request: AgentRequest | None = None,
) -> dict[str, Any]:
    return {
        "stage": "stage_7_report",
        "schema_version": settings.project.schema_version,
        "report_pipeline_version": REPORT_PIPELINE_VERSION,
        "source_artifacts": {path.name: sha256_file(path) for path in artifacts},
        "report": settings.report.model_dump(mode="json"),
        "task": request.model_dump(mode="json") if request is not None else None,
    }


def _validate_cached_deliverables(
    metrics: dict[str, Any], markdown_path: Path, html_path: Path
) -> None:
    if not markdown_path.is_file() or not html_path.is_file():
        raise ValueError("Cached report deliverables are missing")
    hashes = metrics.get("deliverable_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("Cached report metrics have no deliverable hashes")
    if hashes.get("markdown") != sha256_file(markdown_path):
        raise ValueError("Cached Markdown report hash mismatch")
    if hashes.get("html") != sha256_file(html_path):
        raise ValueError("Cached HTML report hash mismatch")


def build_report(
    video_path: Path,
    settings: Settings,
    *,
    force: bool = False,
    request: AgentRequest | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    output_root = artifact_root or run_dir
    analysis_path = output_root / "insights" / "analysis.json"
    evidence_path = run_dir / "ledger" / "evidence.jsonl"
    clusters_path = run_dir / "ledger" / "clusters.jsonl"
    ledger_path = run_dir / "ledger" / "ledger.json"
    keyframes_path = run_dir / "timeline" / "keyframes.jsonl"
    asset_path = run_dir / "asset.json"
    required = [analysis_path, evidence_path, clusters_path, ledger_path, keyframes_path, asset_path]
    missing = [path for path in required if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Stage 7 requires completed Stage 5 and Stage 6 artifacts; missing: {names}")

    critic_dir = output_root / "critic"
    report_dir = output_root / "report"
    audit_path = critic_dir / "audit.json"
    markdown_path = report_dir / "report.md"
    html_path = report_dir / "report.html"
    metrics_path = output_root / "stage_7_metrics.json"
    manifest_path = output_root / "manifest.json"
    analysis = MarketingAnalysis.model_validate(store.read_json(analysis_path))
    if not analysis.insights:
        raise ValueError(
            "当前视频没有形成可验证的广告卖点，无法生成报告。请先检查语音、OCR 和关键帧证据。"
        )
    cache_payload = _cache_payload(settings, required, request)
    cache_key = stage_cache_key(cache_payload)

    if not force and audit_path.is_file() and metrics_path.is_file():
        metrics = store.read_json(metrics_path)
        hashes = metrics.get("artifact_sha256")
        if (
            metrics.get("cache_key") == cache_key
            and isinstance(hashes, dict)
            and hashes.get("audit") == sha256_file(audit_path)
        ):
            audit = CriticAudit.model_validate(store.read_json(audit_path))
            _validate_cached_deliverables(metrics, markdown_path, html_path)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "artifact_root": str(output_root),
                "audit_status": audit.status.value,
                "insight_count": audit.insight_count,
                "markdown": str(markdown_path),
                "html": str(html_path),
                "cache_key": cache_key,
            }

    asset = AdAsset.model_validate(store.read_json(asset_path))
    display_name_path = video_path.with_suffix(video_path.suffix + ".name")
    if display_name_path.is_file():
        source_name = Path(display_name_path.read_text(encoding="utf-8").strip()).stem
    elif asset.filename.startswith("upload_"):
        source_name = analysis.subject
    else:
        source_name = Path(asset.filename).stem
    evidence = _load_jsonl(evidence_path, Evidence, "ledger evidence")
    clusters = _load_jsonl(clusters_path, EvidenceCluster, "ledger cluster")
    keyframes = _load_jsonl(keyframes_path, Keyframe, "timeline keyframe")
    ledger = store.read_json(ledger_path)
    duration_ms = int(ledger["duration_ms"])
    audit = audit_analysis(
        analysis,
        evidence=evidence,
        clusters=clusters,
        keyframes=keyframes,
        duration_ms=duration_ms,
        run_dir=run_dir,
    )
    markdown = render_markdown_report(analysis, audit, source_name=source_name)
    html = render_html_report(
        analysis,
        audit,
        run_dir=run_dir,
        embed_images=settings.report.embed_images,
        max_image_bytes=settings.report.max_embedded_image_bytes,
        max_evidence_per_insight=settings.report.max_evidence_per_insight,
        source_name=source_name,
    )
    store.write_json(audit_path, audit)
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    total_seconds = time.perf_counter() - total_started
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_7_report",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "audit_status": audit.status.value,
        "insight_count": audit.insight_count,
        "passed_count": audit.passed_count,
        "review_count": audit.review_count,
        "failed_count": audit.failed_count,
        "task": request.model_dump(mode="json") if request is not None else None,
        "effective_mode_profile": {
            "mode": request.mode if request is not None else "quick",
            "audit_depth": "deep" if request is not None and request.mode == "deep" else "standard",
        },
        "embedded_image_count": html.count("data:image/"),
        "markdown_bytes": markdown_path.stat().st_size,
        "html_bytes": html_path.stat().st_size,
        "deliverable_sha256": {
            "markdown": sha256_file(markdown_path),
            "html": sha256_file(html_path),
        },
        "artifact_sha256": {"audit": sha256_file(audit_path)},
        "total_seconds": round(total_seconds, 6),
    }
    store.write_json(metrics_path, metrics)
    manifest: dict[str, Any] = (
        store.read_json(manifest_path)
        if manifest_path.is_file()
        else {"schema_version": settings.project.schema_version, "run_id": run_id}
    )
    stages_value = manifest.setdefault("stages", {})
    if not isinstance(stages_value, dict):
        raise ValueError("Manifest stages must be an object")
    stages: dict[str, Any] = stages_value
    stages["stage_7_report"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "audit": "critic/audit.json",
            "markdown": "report/report.md",
            "html": "report/report.html",
            "metrics": "stage_7_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifact_root": str(output_root),
        "audit_status": audit.status.value,
        "insight_count": audit.insight_count,
        "markdown": str(markdown_path),
        "html": str(html_path),
        "cache_key": cache_key,
        "metrics": metrics,
    }
