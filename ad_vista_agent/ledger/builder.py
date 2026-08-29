from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.runtime import ArtifactStore, sha256_file
from ad_vista_agent.runtime.stage_cache import stage_cache_key
from ad_vista_agent.schemas import (
    AdAsset,
    Evidence,
    EvidenceCluster,
    EvidenceLedger,
    EvidenceRelation,
)
from .rules import build_ocr_clusters, build_relations
from ad_vista_agent.reports.evidence import build_evidence_document


LEDGER_PIPELINE_VERSION = "1"
ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_jsonl(path: Path, model: type[ModelT], label: str) -> list[ModelT]:
    rows: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"Invalid {label} at {path}:{line_number}") from exc
    return rows


def _validate_source_evidence(
    evidence: list[Evidence], *, asset: AdAsset, run_dir: Path
) -> None:
    seen: set[str] = set()
    for item in evidence:
        if item.evidence_id in seen:
            raise ValueError(f"Duplicate source evidence ID: {item.evidence_id}")
        seen.add(item.evidence_id)
        if item.asset_id != asset.asset_id:
            raise ValueError(f"Evidence belongs to another asset: {item.evidence_id}")
        if item.end_ms > asset.metadata.duration_ms:
            raise ValueError(f"Evidence exceeds asset duration: {item.evidence_id}")
        if item.artifact_path is None or item.artifact_hash is None:
            raise ValueError(f"Evidence has no artifact provenance: {item.evidence_id}")
        artifact = run_dir / item.artifact_path
        if not artifact.is_file():
            raise ValueError(f"Evidence artifact is missing: {artifact}")
        if sha256_file(artifact) != item.artifact_hash:
            raise ValueError(f"Evidence artifact hash mismatch: {item.evidence_id}")


def _validate_ledger_outputs(
    ledger: EvidenceLedger,
    evidence: list[Evidence],
    clusters: list[EvidenceCluster],
    relations: list[EvidenceRelation],
) -> None:
    evidence_ids = {item.evidence_id for item in evidence}
    if ledger.ordered_evidence_ids != [item.evidence_id for item in evidence]:
        raise ValueError("Ledger evidence order does not match evidence artifact")
    if ledger.cluster_ids != [item.cluster_id for item in clusters]:
        raise ValueError("Ledger cluster index does not match cluster artifact")
    if ledger.relation_ids != [item.relation_id for item in relations]:
        raise ValueError("Ledger relation index does not match relation artifact")
    member_ids = [member for cluster in clusters for member in cluster.member_evidence_ids]
    if not set(member_ids).issubset(evidence_ids):
        raise ValueError("A cluster references unknown evidence")
    for relation in relations:
        if relation.left_evidence_id not in evidence_ids or relation.right_evidence_id not in evidence_ids:
            raise ValueError("A relation references unknown evidence")


def _cache_payload(
    asset: AdAsset,
    speech_path: Path,
    ocr_path: Path,
    settings: Settings,
) -> dict[str, Any]:
    return {
        "asset_sha256": asset.sha256,
        "stage": "stage_5_ledger",
        "schema_version": settings.project.schema_version,
        "ledger_pipeline_version": LEDGER_PIPELINE_VERSION,
        "source_artifacts": {
            "speech_sha256": sha256_file(speech_path),
            "ocr_sha256": sha256_file(ocr_path),
        },
        "rules": settings.ledger.model_dump(mode="json"),
    }


def build_ledger(video_path: Path, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    total_started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    asset = AdAsset.model_validate(store.read_json(run_dir / "asset.json"))
    speech_path = run_dir / "speech" / "evidence.jsonl"
    ocr_path = run_dir / "ocr" / "evidence.jsonl"
    missing_sources = [path for path in (speech_path, ocr_path) if not path.is_file()]
    if missing_sources:
        missing = ", ".join(str(path.relative_to(run_dir)) for path in missing_sources)
        raise FileNotFoundError(
            f"Stage 5 requires completed Stage 3 and Stage 4 evidence; missing: {missing}"
        )
    ledger_dir = run_dir / "ledger"
    evidence_path = ledger_dir / "evidence.jsonl"
    clusters_path = ledger_dir / "clusters.jsonl"
    relations_path = ledger_dir / "relations.jsonl"
    ledger_path = ledger_dir / "ledger.json"
    metrics_path = run_dir / "stage_5_metrics.json"
    manifest_path = run_dir / "manifest.json"
    cache_payload = _cache_payload(asset, speech_path, ocr_path, settings)
    cache_key = stage_cache_key(cache_payload)

    if not force and all(
        path.is_file()
        for path in (evidence_path, clusters_path, relations_path, ledger_path, metrics_path)
    ):
        metrics = store.read_json(metrics_path)
        if metrics.get("cache_key") == cache_key:
            evidence = _load_jsonl(evidence_path, Evidence, "ledger evidence")
            clusters = _load_jsonl(clusters_path, EvidenceCluster, "evidence cluster")
            relations = _load_jsonl(relations_path, EvidenceRelation, "evidence relation")
            ledger = EvidenceLedger.model_validate(store.read_json(ledger_path))
            _validate_source_evidence(evidence, asset=asset, run_dir=run_dir)
            _validate_ledger_outputs(ledger, evidence, clusters, relations)
            build_evidence_document(run_dir)
            return {
                "status": "ok",
                "cache_hit": True,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "evidence_count": len(evidence),
                "cluster_count": len(clusters),
                "relation_count": len(relations),
                "cache_key": cache_key,
            }

    speech = _load_jsonl(speech_path, Evidence, "speech evidence")
    ocr = _load_jsonl(ocr_path, Evidence, "OCR evidence")
    evidence = sorted(
        [*speech, *ocr],
        key=lambda item: (item.start_ms, item.end_ms, item.modality.value, item.evidence_id),
    )
    _validate_source_evidence(evidence, asset=asset, run_dir=run_dir)
    clusters = build_ocr_clusters(
        evidence,
        similarity_threshold=settings.ledger.ocr_similarity_threshold,
        max_gap_ms=settings.ledger.ocr_max_gap_ms,
        min_region_iou=settings.ledger.ocr_min_region_iou,
    )
    relations = build_relations(
        evidence,
        similarity_threshold=settings.ledger.cross_modal_similarity_threshold,
        window_ms=settings.ledger.cross_modal_window_ms,
        conflict_context_similarity_threshold=settings.ledger.conflict_context_similarity_threshold,
    )
    modality_counts = Counter(item.modality.value for item in evidence)
    ledger = EvidenceLedger(
        asset_id=asset.asset_id,
        duration_ms=asset.metadata.duration_ms,
        source_evidence_count=len(evidence),
        ordered_evidence_ids=[item.evidence_id for item in evidence],
        cluster_ids=[item.cluster_id for item in clusters],
        relation_ids=[item.relation_id for item in relations],
        modality_counts=dict(modality_counts),
        configuration=settings.ledger.model_dump(mode="json"),
    )
    _validate_ledger_outputs(ledger, evidence, clusters, relations)
    store.write_jsonl(evidence_path, evidence)
    store.write_jsonl(clusters_path, clusters)
    store.write_jsonl(relations_path, relations)
    store.write_json(ledger_path, ledger)
    build_evidence_document(run_dir)
    collapsed_count = sum(max(0, len(item.member_evidence_ids) - 1) for item in clusters)
    total_seconds = time.perf_counter() - total_started
    metrics = {
        "schema_version": settings.project.schema_version,
        "stage": "stage_5_ledger",
        "cache_key": cache_key,
        "cache_payload": cache_payload,
        "cache_hit": False,
        "speech_evidence_count": len(speech),
        "ocr_evidence_count": len(ocr),
        "source_evidence_count": len(evidence),
        "ocr_cluster_count": len(clusters),
        "ocr_duplicate_count": collapsed_count,
        "support_relation_count": sum(item.type.value == "supports" for item in relations),
        "possible_conflict_count": sum(item.type.value == "possible_conflict" for item in relations),
        "total_seconds": round(total_seconds, 6),
    }
    store.write_json(metrics_path, metrics)
    manifest = store.read_json(manifest_path)
    stages = manifest.setdefault("stages", {})
    stages["stage_5_ledger"] = {
        "cache_key": cache_key,
        "configuration": cache_payload,
        "artifacts": {
            "ledger": "ledger/ledger.json",
            "evidence": "ledger/evidence.jsonl",
            "clusters": "ledger/clusters.jsonl",
            "relations": "ledger/relations.jsonl",
            "metrics": "stage_5_metrics.json",
        },
    }
    store.write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "evidence_count": len(evidence),
        "cluster_count": len(clusters),
        "relation_count": len(relations),
        "cache_key": cache_key,
        "metrics": metrics,
    }
