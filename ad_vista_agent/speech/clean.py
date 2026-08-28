from __future__ import annotations

import re

from ad_vista_agent.schemas.speech import TranscriptSegment


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_segments(
    segments: list[TranscriptSegment], *, duration_ms: int, merge_gap_ms: int
) -> list[TranscriptSegment]:
    cleaned: list[TranscriptSegment] = []
    for segment in sorted(segments, key=lambda item: (item.start_ms, item.end_ms)):
        text = _space(segment.text)
        start_ms = max(0, min(segment.start_ms, duration_ms))
        end_ms = max(0, min(segment.end_ms, duration_ms))
        if not text or end_ms <= start_ms:
            continue

        normalized = segment.model_copy(
            update={
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "words": [
                    word.model_copy(
                        update={
                            "start_ms": max(start_ms, min(word.start_ms, end_ms)),
                            "end_ms": max(start_ms, min(word.end_ms, end_ms)),
                            "text": _space(word.text),
                        }
                    )
                    for word in segment.words
                    if _space(word.text)
                ],
            }
        )
        if cleaned and text.casefold() == cleaned[-1].text.casefold():
            previous = cleaned[-1]
            cleaned[-1] = previous.model_copy(
                update={
                    "end_ms": max(previous.end_ms, end_ms),
                    "confidence": max(
                        value for value in (previous.confidence, normalized.confidence) if value is not None
                    ) if previous.confidence is not None or normalized.confidence is not None else None,
                    "words": previous.words + normalized.words,
                }
            )
            continue
        if cleaned and start_ms - cleaned[-1].end_ms <= merge_gap_ms:
            previous = cleaned[-1]
            confidences = [value for value in (previous.confidence, normalized.confidence) if value is not None]
            cleaned[-1] = previous.model_copy(
                update={
                    "end_ms": end_ms,
                    "text": _space(previous.text + " " + text),
                    "confidence": sum(confidences) / len(confidences) if confidences else None,
                    "words": previous.words + normalized.words,
                }
            )
        else:
            cleaned.append(normalized)

    return [segment.model_copy(update={"segment_id": f"speech_{index + 1:04d}"}) for index, segment in enumerate(cleaned)]
