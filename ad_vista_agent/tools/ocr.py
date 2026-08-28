from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext


@dataclass(frozen=True)
class OcrWorkerResult:
    backend: str
    items: list[dict[str, Any]]
    versions: dict[str, str]


class OcrWorkerTool(Tool):
    name = "ocr_worker"
    description = "Execute an isolated DeepSeek-OCR or PaddleOCR worker."

    def __init__(self, python_executable: Path, timeout_seconds: int, cuda_visible_devices: str) -> None:
        self.python_executable = python_executable.expanduser().absolute()
        self.timeout_seconds = timeout_seconds
        self.cuda_visible_devices = cuda_visible_devices

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> OcrWorkerResult:
        backend = str(arguments["backend"])
        module = {
            "deepseek_ocr": "ad_vista_agent.services.deepseek_ocr_worker",
            "paddleocr": "ad_vista_agent.services.paddle_ocr_worker",
        }.get(backend)
        if module is None:
            raise ValueError(f"Unknown OCR backend: {backend}")
        if not self.python_executable.is_file():
            raise FileNotFoundError(self.python_executable)
        request_path = context.run_dir / "ocr" / f"{backend}_request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(arguments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        command = [str(self.python_executable), "-m", module, "--request", str(request_path)]
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
            raise RuntimeError(f"{backend} timed out after {self.timeout_seconds}s") from exc
        finally:
            request_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"{backend} failed: {result.stderr.strip()[-4000:]}")
        try:
            payload = json.loads(result.stdout)
            return OcrWorkerResult(
                backend=backend,
                items=list(payload["items"]),
                versions={str(key): str(value) for key, value in payload["versions"].items()},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{backend} returned an invalid response") from exc
