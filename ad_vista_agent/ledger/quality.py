"""识别不能直接作为产品文本证据的 OCR 内容；不修改原始 Ledger。"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date


def ocr_quality_issue(content: str) -> str | None:
    """返回确定性的质量问题；未发现问题不代表文字与产品相关。"""
    text = content.strip()
    if not text or not any(char.isalnum() for char in text):
        return "empty_or_punctuation"
    if re.search(
        r"your\s+input\s+text|(?:图片|图像)(?:中)?(?:没有|无)(?:可识别)?(?:的)?(?:文字|文本)|"
        r"no\s+(?:recognizable\s+)?(?:text|words)",
        text,
        re.IGNORECASE,
    ):
        return "recognition_placeholder"
    if re.fullmatch(r"[\s\-+•]*\d+[\s.,，。]*", text):
        return "isolated_number"
    numbers = re.findall(r"\d+", text)
    non_numbers = re.sub(r"[\d\s.,，、:：;/\-\[\]()]+", "", text)
    if len(numbers) >= 12 and not non_numbers:
        counts = Counter(numbers)
        if len(counts) / len(numbers) <= 0.3 or counts.most_common(1)[0][1] >= 8:
            return "repeated_numeric_sequence"
    for match in re.finditer(r"(?<!\d)(\d{4})[-./](\d{1,2})(?:[-./](\d{1,2}))?(?!\d)", text):
        year, month, day = match.groups()
        try:
            date(int(year), int(month), int(day or 1))
        except ValueError:
            return "invalid_date"
    return None
