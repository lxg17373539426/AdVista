from __future__ import annotations

import ast
import re
import unicodedata

from ad_vista_agent.schemas import BoundingBox, OcrRegion


GROUNDING = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>",
    flags=re.DOTALL,
)


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
    return regions
