from __future__ import annotations

import argparse
import json
import math
import sys
from importlib.metadata import version
from pathlib import Path

from faster_whisper import WhisperModel


def milliseconds(value: float) -> int:
    return max(0, round(value * 1000))


def confidence(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    return max(0.0, min(1.0, math.exp(avg_logprob)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))

    model = WhisperModel(
        request["model_path"],
        device=request["device"],
        device_index=int(request["device_index"]),
        compute_type=request["compute_type"],
    )
    segments, info = model.transcribe(
        request["video_path"],
        beam_size=int(request["beam_size"]),
        vad_filter=bool(request["vad_filter"]),
        condition_on_previous_text=bool(request["condition_on_previous_text"]),
        word_timestamps=bool(request["word_timestamps"]),
    )
    output_segments = []
    for index, segment in enumerate(segments, 1):
        words = []
        for word in segment.words or []:
            words.append(
                {
                    "start_ms": milliseconds(word.start),
                    "end_ms": milliseconds(word.end),
                    "text": word.word,
                    "probability": word.probability,
                }
            )
        output_segments.append(
            {
                "segment_id": f"raw_{index:04d}",
                "start_ms": milliseconds(segment.start),
                "end_ms": milliseconds(segment.end),
                "text": segment.text,
                "confidence": confidence(segment.avg_logprob),
                "words": words,
            }
        )

    response = {
        "language": info.language,
        "language_probability": info.language_probability,
        "segments": output_segments,
        "versions": {
            "faster_whisper": version("faster-whisper"),
            "ctranslate2": version("ctranslate2"),
        },
    }
    json.dump(response, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
