from __future__ import annotations

import ast
import re
import unicodedata
from difflib import SequenceMatcher

from ad_vista_agent.schemas import BoundingBox, OcrRegion


GROUNDING = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>",
    flags=re.DOTALL,
)
_SPECIAL_TOKEN = re.compile(r"<\|/?(?:ref|det)\|>")
_MATH_NOISE = re.compile(r"(?:\\text\s*\{[^}]*\}|(?:[0-9]+\.){4,})")


def _space(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def ocr_quality_flags(value: str) -> list[str]:
    flags: list[str] = []
    if len(value.strip()) <= 1:
        flags.append("short_text")
    if "�" in value or any(marker in value for marker in ("Ã", "Â", "â")):
        flags.append("possible_mojibake")
    if not re.search(r"[\w\u4e00-\u9fff]", value, flags=re.UNICODE):
        flags.append("no_alphanumeric_content")
    return flags


def _plain_text(value: str) -> str:
    value = _SPECIAL_TOKEN.sub("", value)
    lines: list[str] = []
    seen: list[str] = []
    for line in value.splitlines():
        normalized = _space(line)
        if not normalized:
            continue
        compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized.casefold())
        duplicate = any(
            compact == prior
            or (len(compact) >= 8 and len(prior) >= 8 and SequenceMatcher(None, compact, prior).ratio() >= 0.82)
            for prior in seen
        )
        if not duplicate:
            seen.append(compact)
            lines.append(normalized)
    return "\n".join(lines) if lines else _space(value)


def _is_usable_plain_text(value: str) -> bool:
    """Accept text-only OCR output without treating image placeholders as text."""
    text = _plain_text(value)
    if not text or text.casefold() in {"image", "<image>"}:
        return False
    semantic_text = re.sub(r"\\[A-Za-z]+", "", text)
    if (
        (_MATH_NOISE.search(text) or ("\\text{" in text and text.count(".") >= 3))
        and not re.search(r"[\u4e00-\u9fffA-Za-z]{3,}", semantic_text)
    ):
        return False
    if len(text) < 2 and not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return False
    return True


def parse_deepseek_grounding(raw: str) -> list[OcrRegion]:
    matches = list(GROUNDING.finditer(raw))
    regions: list[OcrRegion] = []
    seen: set[tuple[str, float, float, float, float]] = set()
    for index, match in enumerate(matches):
        label = _space(match.group(1)) or "text"
        try:
            boxes = ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            continue
        text_start = match.end()
        text_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        text = _space(raw[text_start:text_end])
        if not text:
            continue
        for box in boxes if isinstance(boxes, list) else []:
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                x1, y1, x2, y2 = (float(value) / 999 for value in box)
                region = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)
            except (TypeError, ValueError):
                continue
            key = (text.casefold(), region.x1, region.y1, region.x2, region.y2)
            if key in seen:
                continue
            seen.add(key)
            regions.append(OcrRegion(text=text, region=region, confidence=None, label=label))
    if regions or GROUNDING.search(raw) or not _is_usable_plain_text(raw):
        return regions

    # Some DeepSeek-OCR builds return readable text but omit grounding tags.
    # Keep it as coarse, frame-level evidence instead of discarding a readable
    # transcription just because the model omitted coordinates.
    return [
        OcrRegion(
            text=_plain_text(raw),
            region=BoundingBox(x1=0, y1=0, x2=1, y2=1),
            confidence=None,
            label="frame_text",
        )
    ]
