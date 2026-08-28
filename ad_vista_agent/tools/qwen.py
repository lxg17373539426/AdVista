from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext


@dataclass(frozen=True)
class QwenInsightResult:
    text: str
    attempts: list[str]
    attempt_count: int
    prompt_tokens: int
    completion_tokens: int
    versions: dict[str, str]


class QwenInsightTool(Tool):
    name = "qwen_insight"
    description = "Run isolated Qwen3.5 evidence-grounded structured inference."

    def __init__(
        self,
        python_executable: Path,
        timeout_seconds: int,
        cuda_visible_devices: str,
        *,
        runtime: str = "subprocess",
        endpoint: str = "http://127.0.0.1:8000/v1",
        served_model: str = "AdInsight-RL",
    ) -> None:
        self.python_executable = python_executable.expanduser().absolute()
        self.timeout_seconds = timeout_seconds
        self.cuda_visible_devices = cuda_visible_devices
        self.runtime = runtime
        self.endpoint = endpoint.rstrip("/")
        self.served_model = served_model

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> QwenInsightResult:
        if self.runtime == "openai":
            return self._run_openai(arguments)
        if self.runtime != "subprocess":
            raise ValueError(f"Unsupported Qwen runtime: {self.runtime}")
        return self._run_subprocess(context, arguments)

    def _run_openai(self, arguments: dict[str, Any]) -> QwenInsightResult:
        sampling = {
            "temperature": float(arguments["temperature"]),
            "max_tokens": int(arguments["max_tokens"]),
            "seed": 42,
            "structured_outputs": {"json": arguments["output_schema"]},
            "chat_template_kwargs": {"enable_thinking": False},
        }

        def request(messages: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
            payload = json.dumps(
                {"model": self.served_model, "messages": messages, **sampling},
                ensure_ascii=False,
            ).encode("utf-8")
            call = urllib.request.Request(
                self.endpoint + "/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(call, timeout=self.timeout_seconds) as response:
                    value = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Qwen service HTTP {exc.code}: {body[-4000:]}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError(f"Qwen service request failed: {exc}") from exc
            try:
                text = str(value["choices"][0]["message"]["content"])
                raw_usage = value.get("usage") or {}
                usage = {
                    key: int(raw_usage.get(key, 0))
                    for key in ("prompt_tokens", "completion_tokens")
                }
                return text, usage
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Qwen service returned an invalid response") from exc

        text, usage = request(list(arguments["messages"]))
        attempts = [text]
        try:
            json.loads(text)
        except json.JSONDecodeError:
            text, repair_usage = request(
                [
                    {
                        "role": "system",
                        "content": (
                            "You repair JSON syntax only. Preserve every claim, ID, confidence, status, "
                            "and list item from the supplied draft. Return one valid JSON object matching "
                            "the required schema. Do not add evidence or analysis."
                        ),
                    },
                    {"role": "user", "content": text},
                ]
            )
            attempts.append(text)
            usage = {
                key: usage.get(key, 0) + repair_usage.get(key, 0)
                for key in set(usage) | set(repair_usage)
            }
        return QwenInsightResult(
            text=text,
            attempts=attempts,
            attempt_count=len(attempts),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            versions={"vllm": "0.19.1", "torch": "2.10.0", "transformers": "5.13.0"},
        )

    def _run_subprocess(self, context: ToolContext, arguments: dict[str, Any]) -> QwenInsightResult:
        if not self.python_executable.is_file():
            raise FileNotFoundError(self.python_executable)
        insight_dir = context.run_dir / "insights"
        insight_dir.mkdir(parents=True, exist_ok=True)
        request_path = insight_dir / "qwen_request.json"
        response_path = insight_dir / "qwen_response.json"
        payload = dict(arguments)
        payload["response_path"] = str(response_path)
        request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        response_path.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        command = [
            str(self.python_executable),
            "-m",
            "ad_vista_agent.services.qwen_insight_worker",
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
            if result.returncode != 0:
                raise RuntimeError(f"Qwen insight inference failed: {result.stderr.strip()[-4000:]}")
            if not response_path.is_file():
                raise RuntimeError("Qwen insight worker produced no response file")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            return QwenInsightResult(
                text=str(response["text"]),
                attempts=[str(item) for item in response["attempts"]],
                attempt_count=int(response["attempt_count"]),
                prompt_tokens=int(response["prompt_tokens"]),
                completion_tokens=int(response["completion_tokens"]),
                versions={str(key): str(value) for key, value in response["versions"].items()},
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Qwen insight inference timed out after {self.timeout_seconds}s") from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Qwen insight worker returned an invalid response") from exc
        finally:
            request_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)
