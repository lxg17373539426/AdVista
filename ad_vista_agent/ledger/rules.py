from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from ad_vista_agent.schemas import (
    BoundingBox,
    Evidence,
    EvidenceCluster,
    EvidenceModality,
    EvidenceRelation,
    RelationType,
)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def text_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def region_iou(left: BoundingBox | None, right: BoundingBox | None) -> float:
    if left is None or right is None:
        return 0.0
    x1, y1 = max(left.x1, right.x1), max(left.y1, right.y1)
    x2, y2 = min(left.x2, right.x2), min(left.y2, right.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection == 0:
        return 0.0
    left_area = (left.x2 - left.x1) * (left.y2 - left.y1)
    right_area = (right.x2 - right.x1) * (right.y2 - right.y1)
    return intersection / (left_area + right_area - intersection)


def _representative(members: list[Evidence]) -> Evidence:
    def flags(item: Evidence) -> set[str]:
        raw = item.metadata.get("quality_flags", [])
        return {str(value) for value in raw} if isinstance(raw, list) else set()

    return max(
        members,
        key=lambda item: (
            "possible_mojibake" not in flags(item),
            "short_text" not in flags(item),
            item.confidence is not None,
            item.confidence if item.confidence is not None else -1.0,
            len(normalize_text(item.content)),
            -item.start_ms,
        ),
    )


def _canonical_content(members: list[Evidence], representative: Evidence) -> str:
    candidates = [item for item in members if item.content.strip()]
    def flags(item: Evidence) -> set[str]:
        raw = item.metadata.get("quality_flags", [])
        return {str(value) for value in raw} if isinstance(raw, list) else set()

    preferred = [
        item
        for item in candidates
        if not {"possible_mojibake", "short_text"}.intersection(flags(item))
    ]
    if preferred:
        return max(preferred, key=lambda item: (len(normalize_text(item.content)), item.confidence or 0)).content
    return representative.content


def build_ocr_clusters(
    evidence: list[Evidence],
    *,
    similarity_threshold: float,
    max_gap_ms: int,
    min_region_iou: float,
) -> list[EvidenceCluster]:
    ocr = sorted(
        (item for item in evidence if item.modality == EvidenceModality.OCR),
        key=lambda item: (item.start_ms, item.evidence_id),
    )
    groups: list[list[Evidence]] = []
    for item in ocr:
        target: list[Evidence] | None = None
        for group in reversed(groups):
            last = group[-1]
            if item.start_ms - last.end_ms > max_gap_ms:
                break
            if (
                text_similarity(item.content, last.content) >= similarity_threshold
                and region_iou(item.region, last.region) >= min_region_iou
            ):
                target = group
                break
        if target is None:
            groups.append([item])
        else:
            target.append(item)

    clusters: list[EvidenceCluster] = []
    for index, members in enumerate(groups, 1):
        representative = _representative(members)
        confidences = [item.confidence for item in members if item.confidence is not None]
        clusters.append(
            EvidenceCluster(
                cluster_id=f"ocr_cluster_{index:04d}",
                asset_id=representative.asset_id,
                modality=EvidenceModality.OCR,
                canonical_content=_canonical_content(members, representative),
                start_ms=min(item.start_ms for item in members),
                end_ms=max(item.end_ms for item in members),
                representative_evidence_id=representative.evidence_id,
                member_evidence_ids=[item.evidence_id for item in members],
                confidence=max(confidences) if confidences else None,
            )
        )
    return clusters


def _time_distance(left: Evidence, right: Evidence) -> int:
    if left.end_ms < right.start_ms:
        return right.start_ms - left.end_ms
    if right.end_ms < left.start_ms:
        return left.start_ms - right.end_ms
    return 0


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", value))


def _without_numbers(value: str) -> str:
    return re.sub(r"\d+(?:[.,]\d+)?", "#", value)


def build_relations(
    evidence: list[Evidence],
    *,
    similarity_threshold: float,
    window_ms: int,
    conflict_context_similarity_threshold: float,
) -> list[EvidenceRelation]:
    speech = [item for item in evidence if item.modality == EvidenceModality.SPEECH]
    ocr = [item for item in evidence if item.modality == EvidenceModality.OCR]
    relations: list[EvidenceRelation] = []
    for spoken in speech:
        for visible in ocr:
            if _time_distance(spoken, visible) > window_ms:
                continue
            similarity = text_similarity(spoken.content, visible.content)
            relation_type: RelationType | None = None
            reason = ""
            score = similarity
            spoken_numbers, visible_numbers = _numbers(spoken.content), _numbers(visible.content)
            context_similarity = text_similarity(
                _without_numbers(spoken.content), _without_numbers(visible.content)
            )
            if (
                spoken_numbers
                and visible_numbers
                and spoken_numbers != visible_numbers
                and context_similarity >= conflict_context_similarity_threshold
            ):
                relation_type = RelationType.POSSIBLE_CONFLICT
                reason = "Similar nearby statements contain different numeric values."
                score = context_similarity
            elif similarity >= similarity_threshold:
                relation_type = RelationType.SUPPORTS
                reason = "Speech and on-screen text are similar within the temporal window."
            if relation_type is not None:
                relations.append(
                    EvidenceRelation(
                        relation_id=f"relation_{len(relations) + 1:04d}",
                        type=relation_type,
                        left_evidence_id=spoken.evidence_id,
                        right_evidence_id=visible.evidence_id,
                        score=score,
                        reason=reason,
                    )
                )
    return relations
