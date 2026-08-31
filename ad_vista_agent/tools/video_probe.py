from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from ad_vista_agent.schemas.assets import AudioStream, MediaMetadata, VideoStream

from .base import Tool, ToolContext
from ad_vista_agent.runtime.process import ProcessCancelled, run_process


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _milliseconds(value: Any) -> int | None:
    try:
        return max(0, round(float(value) * 1000))
    except (TypeError, ValueError):
        return None


def _fps(value: Any) -> float:
    try:
        result = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        result = 0.0
    if result <= 0:
        raise ValueError(f"Invalid video frame rate: {value!r}")
    return result


class VideoProbeTool(Tool):
    name = "video_probe"
    description = "Inspect a local video with ffprobe and return normalized metadata."

    def __init__(self, executable: str = "ffprobe", timeout_seconds: int = 30) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> MediaMetadata:
        video_path = Path(str(arguments["video_path"])).expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError(video_path)

        command = [
            self.executable,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(video_path),
        ]
        try:
            result = run_process(
                command,
                timeout=self.timeout_seconds,
                cancel_event=context.cancel_event,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"ffprobe executable not found: {self.executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ffprobe timed out after {self.timeout_seconds}s: {video_path}") from exc
        except ProcessCancelled as exc:
            raise RuntimeError(f"ffprobe cancelled: {video_path}") from exc

        if result.returncode != 0:
            message = result.stderr.strip() or "unknown ffprobe error"
            raise RuntimeError(f"ffprobe failed for {video_path}: {message}")

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ffprobe returned invalid JSON") from exc

        format_data = payload.get("format") or {}
        streams = payload.get("streams") or []
        format_duration = _milliseconds(format_data.get("duration"))
        videos: list[VideoStream] = []
        audios: list[AudioStream] = []

        for stream in streams:
            kind = stream.get("codec_type")
            if kind == "video":
                videos.append(
                    VideoStream(
                        index=int(stream["index"]),
                        codec=str(stream.get("codec_name") or "unknown"),
                        width=int(stream["width"]),
                        height=int(stream["height"]),
                        fps=_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                        duration_ms=_milliseconds(stream.get("duration")),
                        frame_count=_optional_int(stream.get("nb_frames")),
                    )
                )
            elif kind == "audio":
                audios.append(
                    AudioStream(
                        index=int(stream["index"]),
                        codec=str(stream.get("codec_name") or "unknown"),
                        sample_rate=_optional_int(stream.get("sample_rate")),
                        channels=_optional_int(stream.get("channels")),
                        duration_ms=_milliseconds(stream.get("duration")),
                    )
                )

        if not videos:
            raise ValueError(f"No video stream found: {video_path}")
        duration_candidates = [format_duration]
        duration_candidates.extend(item.duration_ms for item in videos)
        duration_candidates.extend(item.duration_ms for item in audios)
        duration_ms = max((item for item in duration_candidates if item is not None), default=0)

        return MediaMetadata(
            format_name=str(format_data.get("format_name") or video_path.suffix.lstrip(".")),
            duration_ms=duration_ms,
            bit_rate=_optional_int(format_data.get("bit_rate")),
            video_streams=videos,
            audio_streams=audios,
        )
