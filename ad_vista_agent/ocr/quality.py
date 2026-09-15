from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ad_vista_agent.schemas import OcrFrameResult

from .parse import ocr_quality_flags


def _normalized_lines(value: str) -> list[str]:
    return [
        re.sub(r"[^\w\u4e00-\u9fff]+", "", line.casefold())
        for line in value.splitlines()
        if line.strip()
    ]


def build_ocr_quality_report(frames: list[OcrFrameResult]) -> dict[str, Any]:
    """Summarize OCR cleanliness without pretending to know ground truth."""
    empty_frames = 0
    suspicious_regions = 0
    total_regions = 0
    all_lines: list[str] = []
    for frame in frames:
        if not frame.regions:
            empty_frames += 1
        for region in frame.regions:
            total_regions += 1
            if ocr_quality_flags(region.text):
                suspicious_regions += 1
            all_lines.extend(line for line in _normalized_lines(region.text) if line)

    counts = Counter(all_lines)
    repeated_lines = sum(count - 1 for count in counts.values() if count > 1)
    frame_count = len(frames)
    empty_ratio = empty_frames / frame_count if frame_count else 0.0
    suspicious_ratio = suspicious_regions / total_regions if total_regions else 0.0
    repetition_ratio = repeated_lines / len(all_lines) if all_lines else 0.0
    score = max(
        0,
        round(100 - empty_ratio * 30 - suspicious_ratio * 35 - repetition_ratio * 35),
    )

    warnings: list[str] = []
    if frame_count and empty_ratio > 0.6:
        warnings.append("多数关键帧没有识别到文字；如果视频本身有字幕，请抽查原始关键帧。")
    if suspicious_ratio > 0.2:
        warnings.append("较多文本包含短字符或编码异常，需要人工复核。")
    if repetition_ratio > 0.35:
        warnings.append("跨帧重复文字较多，可能包含平台水印或静态叠字。")

    return {
        "schema_version": "1",
        "engine": "deepseek_ocr",
        "status": "review" if warnings else "pass",
        "score": score,
        "frame_count": frame_count,
        "empty_frame_count": empty_frames,
        "region_count": total_regions,
        "suspicious_region_count": suspicious_regions,
        "repeated_line_count": repeated_lines,
        "empty_frame_ratio": round(empty_ratio, 4),
        "suspicious_region_ratio": round(suspicious_ratio, 4),
        "repetition_ratio": round(repetition_ratio, 4),
        "warnings": warnings,
        "limitations": [
            "质量分数只衡量空结果、格式噪声和重复文本，不代表字符识别准确率。",
            "品牌名、价格、电话号码和非拉丁文字仍应对照关键帧人工复核。",
        ],
    }
