from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ad_vista_agent.runtime.fingerprint import sha256_file

from .base import Tool, ToolContext


@dataclass(frozen=True)
class ExtractedFrame:
    path: Path
    sha256: str
    width: int
    height: int


class FrameExtractTool(Tool):
    name = "frame_extract"
    description = "Extract one JPEG frame at a requested millisecond timestamp with FFmpeg."

    def __init__(
        self,
        executable: str = "ffmpeg",
        probe_executable: str = "ffprobe",
        timeout_seconds: int = 60,
    ) -> None:
        self.executable = executable
        self.probe_executable = probe_executable
        self.timeout_seconds = timeout_seconds

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> ExtractedFrame:
        video_path = Path(str(arguments["video_path"])).expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError(video_path)
        timestamp_ms = int(arguments["timestamp_ms"])
        if timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        relative_output = Path(str(arguments["output_path"]))
        if relative_output.is_absolute() or ".." in relative_output.parts:
            raise ValueError("output_path must be a safe path relative to the run directory")
        output_path = context.run_dir / relative_output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        quality = int(arguments.get("jpeg_quality", 2))

        command = [
            self.executable,
            "-v",
            "error",
            "-ss",
            f"{timestamp_ms / 1000:.3f}",
            "-i",
            str(video_path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-q:v",
            str(quality),
            "-y",
            str(output_path),
        ]
        self._execute(command, "FFmpeg frame extraction")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg did not create a valid frame: {output_path}")
        width, height = self._image_dimensions(output_path)
        return ExtractedFrame(
            path=output_path,
            sha256=sha256_file(output_path),
            width=width,
            height=height,
        )

    def _image_dimensions(self, image_path: Path) -> tuple[int, int]:
        command = [
            self.probe_executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(image_path),
        ]
        result = self._execute(command, "FFprobe image validation")
        try:
            stream = json.loads(result.stdout)["streams"][0]
            return int(stream["width"]), int(stream["height"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read extracted frame dimensions: {image_path}") from exc

    def _execute(self, command: list[str], label: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Executable not found for {label}: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{label} timed out after {self.timeout_seconds}s") from exc
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed: {result.stderr.strip() or 'unknown error'}")
        return result
