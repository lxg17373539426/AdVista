from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ad_vista_agent.schemas.speech import TranscriptSegment

from .base import Tool, ToolContext


@dataclass(frozen=True)
class AsrResult:
    language: str | None
    language_probability: float | None
    segments: list[TranscriptSegment]
    versions: dict[str, str]


class FasterWhisperTool(Tool):
    name = "faster_whisper"
    description = "Transcribe a local video with an isolated Faster-Whisper worker."

    def __init__(self, python_executable: Path, timeout_seconds: int, cuda_visible_devices: str) -> None:
        # Resolving a venv interpreter symlink would bypass the venv site-packages.
        self.python_executable = python_executable.expanduser().absolute()
        self.timeout_seconds = timeout_seconds
        self.cuda_visible_devices = cuda_visible_devices

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> AsrResult:
        if not self.python_executable.is_file():
            raise FileNotFoundError(self.python_executable)
        request_path = context.run_dir / "speech" / "worker_request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(arguments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        command = [
            str(self.python_executable),
            "-m",
            "ad_vista_agent.services.asr_worker",
            "--request",
            str(request_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Faster-Whisper timed out after {self.timeout_seconds}s") from exc
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"Faster-Whisper failed: {result.stderr.strip()[-2000:]}")
        try:
            payload = json.loads(result.stdout)
            segments = [TranscriptSegment.model_validate(item) for item in payload.get("segments", [])]
            return AsrResult(
                language=payload.get("language"),
                language_probability=payload.get("language_probability"),
                segments=segments,
                versions={str(key): str(value) for key, value in payload["versions"].items()},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Faster-Whisper returned an invalid response") from exc
