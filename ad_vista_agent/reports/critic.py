from __future__ import annotations

from collections import Counter
from pathlib import Path

from ad_vista_agent.runtime import sha256_file
from ad_vista_agent.schemas import (
    AuditFinding,
    AuditSeverity,
    CriticAudit,
    EpistemicStatus,
    Evidence,
    EvidenceCluster,
    EvidenceModality,
    GroundedMarketingInsight,
    Keyframe,
    InsightAudit,
    InsightAuditStatus,
    MarketingAnalysis,
    MarketingDimension,
    ReportEvidence,
)


INFERRED_DIMENSIONS = {
    MarketingDimension.AUDIENCE,
    MarketingDimension.CREATIVE_STRUCTURE,
}
HIGH_RISK_TERMS = (
    "伤害",
    "破坏",
    "皱纹",
    "细纹",
    "更老",
    "老化",
    "抗衰老",
    "健康",
    "临床",
    "保证",
)


def _report_evidence_from_source(reference_id: str, source: Evidence, member_count: int) -> ReportEvidence:
    if source.artifact_path is None or source.artifact_hash is None:
        raise ValueError(f"Evidence has incomplete artifact provenance: {source.evidence_id}")
    return ReportEvidence(
        reference_id=reference_id,
        source_evidence_id=source.evidence_id,
        modality=source.modality,
        content=source.content,
        start_ms=source.start_ms,
        end_ms=source.end_ms,
        confidence=source.confidence,
        epistemic_status=source.epistemic_status,
        artifact_path=str(source.artifact_path),
        artifact_hash=source.artifact_hash,
        member_count=member_count,
    )


def expand_reference(
    reference_id: str,
    *,
    evidence_by_id: dict[str, Evidence],
    clusters_by_id: dict[str, EvidenceCluster],
    keyframes_by_id: dict[str, Keyframe],
) -> ReportEvidence:
    if reference_id in evidence_by_id:
        return _report_evidence_from_source(reference_id, evidence_by_id[reference_id], 1)
    cluster = clusters_by_id.get(reference_id)
    if cluster is None:
        keyframe = keyframes_by_id.get(reference_id)
        if keyframe is None:
            raise KeyError(reference_id)
        return ReportEvidence(
            reference_id=reference_id,
            source_evidence_id=reference_id,
            modality=EvidenceModality.FRAME,
            content=f"关键画面 {keyframe.shot_id}",
            start_ms=keyframe.timestamp_ms,
            end_ms=keyframe.timestamp_ms,
            confidence=None,
            epistemic_status=EpistemicStatus.OBSERVED,
            artifact_path=str(keyframe.artifact_path),
            artifact_hash=keyframe.artifact_sha256,
        )
    representative = evidence_by_id.get(cluster.representative_evidence_id)
    if representative is None:
        raise ValueError(
            f"Cluster {cluster.cluster_id} has unknown representative: {cluster.representative_evidence_id}"
        )
    expanded = _report_evidence_from_source(
        reference_id, representative, len(cluster.member_evidence_ids)
    )
    return expanded.model_copy(
        update={
            "content": cluster.canonical_content,
            "start_ms": cluster.start_ms,
            "end_ms": cluster.end_ms,
            "confidence": cluster.confidence,
        }
    )


def _artifact_findings(item: ReportEvidence, run_dir: Path) -> list[AuditFinding]:
    artifact = run_dir / item.artifact_path
    if not artifact.is_file():
        return [
            AuditFinding(
                code="artifact_missing",
                severity=AuditSeverity.ERROR,
                message=f"证据文件不存在：{item.artifact_path}",
                evidence_refs=[item.reference_id],
            )
        ]
    if sha256_file(artifact) != item.artifact_hash:
        return [
            AuditFinding(
                code="artifact_hash_mismatch",
                severity=AuditSeverity.ERROR,
                message=f"证据文件哈希不匹配：{item.artifact_path}",
                evidence_refs=[item.reference_id],
            )
        ]
    return []


def _insight_findings(
    insight: GroundedMarketingInsight,
    expanded: list[ReportEvidence],
    *,
    duration_ms: int,
    run_dir: Path,
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for item in expanded:
        findings.extend(_artifact_findings(item, run_dir))
        if item.end_ms > duration_ms:
            findings.append(
                AuditFinding(
                    code="evidence_time_out_of_range",
                    severity=AuditSeverity.ERROR,
                    message="证据时间超出视频总时长。",
                    evidence_refs=[item.reference_id],
                )
            )
        if item.confidence is None:
            findings.append(
                AuditFinding(
                    code="uncalibrated_source_confidence",
                    severity=AuditSeverity.INFO,
                    message="该证据源没有提供校准置信度，报告保留原始不确定性。",
                    evidence_refs=[item.reference_id],
                )
            )

    if insight.dimension in INFERRED_DIMENSIONS:
        if insight.epistemic_status != EpistemicStatus.INFERRED_FROM_AD:
            findings.append(
                AuditFinding(
                    code="expected_inferred_status",
                    severity=AuditSeverity.WARNING,
                    message="该维度通常属于营销推断，但当前认识论状态不是 inferred_from_ad。",
                    evidence_refs=insight.evidence_refs,
                )
            )
    elif insight.epistemic_status == EpistemicStatus.OBSERVED:
        findings.append(
            AuditFinding(
                code="claim_marked_as_observed",
                severity=AuditSeverity.WARNING,
                message="营销结论被标记为 observed；应确认它是否只是画面事实而非广告主张。",
                evidence_refs=insight.evidence_refs,
            )
        )

    if any(term in insight.claim for term in HIGH_RISK_TERMS):
        modalities = {item.modality.value for item in expanded}
        if len(modalities) < 2:
            findings.append(
                AuditFinding(
                    code="single_modality_risk_claim",
                    severity=AuditSeverity.WARNING,
                    message="健康、功效或风险相关主张只有单一模态证据，建议人工复核广告原片。",
                    evidence_refs=insight.evidence_refs,
                )
            )

    return findings


def _status(findings: list[AuditFinding]) -> InsightAuditStatus:
    severities = {item.severity for item in findings}
    if AuditSeverity.ERROR in severities:
        return InsightAuditStatus.FAIL
    if AuditSeverity.WARNING in severities:
        return InsightAuditStatus.REVIEW
    return InsightAuditStatus.PASS


def audit_analysis(
    analysis: MarketingAnalysis,
    *,
    evidence: list[Evidence],
    clusters: list[EvidenceCluster],
    keyframes: list[Keyframe],
    duration_ms: int,
    run_dir: Path,
) -> CriticAudit:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    clusters_by_id = {item.cluster_id: item for item in clusters}
    keyframes_by_id = {item.keyframe_id: item for item in keyframes}
    insight_audits: list[InsightAudit] = []
    global_findings: list[AuditFinding] = []

    for insight in analysis.insights:
        expanded: list[ReportEvidence] = []
        findings: list[AuditFinding] = []
        for reference in insight.evidence_refs:
            try:
                expanded.append(
                    expand_reference(
                        reference,
                        evidence_by_id=evidence_by_id,
                        clusters_by_id=clusters_by_id,
                        keyframes_by_id=keyframes_by_id,
                    )
                )
            except (KeyError, ValueError) as exc:
                findings.append(
                    AuditFinding(
                        code="invalid_evidence_reference",
                        severity=AuditSeverity.ERROR,
                        message=str(exc),
                        evidence_refs=[reference],
                    )
                )
        if expanded:
            findings.extend(
                _insight_findings(
                    insight,
                    expanded,
                    duration_ms=duration_ms,
                    run_dir=run_dir,
                )
            )
        else:
            findings.append(
                AuditFinding(
                    code="no_resolvable_evidence",
                    severity=AuditSeverity.ERROR,
                    message="洞察没有可展开的有效证据。",
                    evidence_refs=insight.evidence_refs,
                )
            )
        insight_audits.append(
            InsightAudit(
                insight_id=insight.insight_id,
                status=_status(findings),
                findings=findings,
                evidence=expanded,
            )
        )

    counts = Counter(item.status for item in insight_audits)
    overall = (
        InsightAuditStatus.FAIL
        if counts[InsightAuditStatus.FAIL]
        else InsightAuditStatus.REVIEW
        if counts[InsightAuditStatus.REVIEW]
        else InsightAuditStatus.PASS
    )
    if not analysis.insights:
        global_findings.append(
            AuditFinding(
                code="empty_analysis",
                severity=AuditSeverity.WARNING,
                message="分析没有生成任何洞察。",
            )
        )
        overall = InsightAuditStatus.REVIEW
    return CriticAudit(
        asset_id=analysis.asset_id,
        status=overall,
        insight_count=len(insight_audits),
        passed_count=counts[InsightAuditStatus.PASS],
        review_count=counts[InsightAuditStatus.REVIEW],
        failed_count=counts[InsightAuditStatus.FAIL],
        insights=insight_audits,
        global_findings=global_findings,
    )
