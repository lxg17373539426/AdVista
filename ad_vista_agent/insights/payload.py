from __future__ import annotations

from typing import Any

from ad_vista_agent.schemas import Evidence, EvidenceCluster, EvidenceLedger, EvidenceRelation


def build_ledger_payload(
    ledger: EvidenceLedger,
    evidence: list[Evidence],
    clusters: list[EvidenceCluster],
    relations: list[EvidenceRelation],
) -> dict[str, Any]:
    speech = [
        {
            "evidence_id": item.evidence_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "content": item.content,
            "confidence": item.confidence,
            "epistemic_status": item.epistemic_status.value,
        }
        for item in evidence
        if item.modality.value == "speech"
    ]
    ocr_clusters = [
        {
            "cluster_id": item.cluster_id,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            "content": item.canonical_content,
            "confidence": item.confidence,
            "source_count": len(item.member_evidence_ids),
            "member_evidence_ids": item.member_evidence_ids,
        }
        for item in clusters
    ]
    relation_rows = [item.model_dump(mode="json") for item in relations]
    return {
        "asset_id": ledger.asset_id,
        "duration_ms": ledger.duration_ms,
        "speech_evidence": speech,
        "ocr_clusters": ocr_clusters,
        "candidate_relations": relation_rows,
        "citation_rules": {
            "allowed_speech_ids": [item["evidence_id"] for item in speech],
            "allowed_ocr_cluster_ids": [item["cluster_id"] for item in ocr_clusters],
        },
    }
