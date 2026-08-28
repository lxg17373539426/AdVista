from __future__ import annotations

import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ad_vista_agent.config import DEFAULT_CONFIG, Settings, load_settings
from ad_vista_agent.ingestion import SUPPORTED_SUFFIXES

from .service import WebService


EXPORTS = {
    "report-html": ("report/report.html", "text/html; charset=utf-8"),
    "report-markdown": ("report/report.md", "text/markdown; charset=utf-8"),
    "analysis-json": ("insights/analysis.json", "application/json"),
    "audit-json": ("critic/audit.json", "application/json"),
    "creative-json": ("creative/package.json", "application/json"),
}


def _safe_run_id(value: str) -> bool:
    if len(value) == 27 and value.startswith("ingest_"):
        return all(char in "0123456789abcdef" for char in value[7:])
    if len(value) == 37 and value.startswith("exec_"):
        return all(char in "0123456789abcdef" for char in value[5:])
    return False


def _contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_file() or not path.is_relative_to(root.resolve()):
        raise FileNotFoundError(relative)
    return path


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length > 1_000_000:
        raise ValueError("JSON request is too large")
    value = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


class _MultipartReader:
    def __init__(self, stream: Any, length: int) -> None:
        self.stream = stream
        self.remaining = length
        self.buffer = bytearray()

    def _fill(self) -> None:
        if self.remaining <= 0:
            return
        chunk = self.stream.read(min(1024 * 1024, self.remaining))
        if not chunk:
            raise ValueError("Upload ended before multipart body completed")
        self.remaining -= len(chunk)
        self.buffer.extend(chunk)

    def readline(self) -> bytes:
        while b"\n" not in self.buffer:
            self._fill()
        index = self.buffer.index(10) + 1
        value = bytes(self.buffer[:index])
        del self.buffer[:index]
        return value

    def consume(self, size: int) -> bytes:
        while len(self.buffer) < size:
            self._fill()
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def body_until(self, boundary: bytes, sink: Any, max_bytes: int) -> tuple[int, bool]:
        marker = b"\r\n--" + boundary
        written = 0
        while True:
            index = self.buffer.find(marker)
            if index >= 0:
                payload = bytes(self.buffer[:index])
                del self.buffer[: index + len(marker)]
                if payload:
                    written += len(payload)
                    if written > max_bytes:
                        raise ValueError("Upload exceeds configured size limit")
                    sink(payload)
                suffix = self.consume(2)
                if suffix == b"--":
                    if self.buffer[:2] == b"\r\n":
                        del self.buffer[:2]
                    return written, True
                if suffix != b"\r\n":
                    raise ValueError("Malformed multipart boundary")
                return written, False
            flush = len(self.buffer) - len(marker) + 1
            if flush > 0:
                payload = bytes(self.buffer[:flush])
                del self.buffer[:flush]
                written += len(payload)
                if written > max_bytes:
                    raise ValueError("Upload exceeds configured size limit")
                sink(payload)
            self._fill()


def _multipart_headers(reader: _MultipartReader) -> tuple[str, str | None]:
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("utf-8", errors="replace").partition(":")
        if name and value:
            headers[name.casefold()] = value.strip()
    disposition = headers.get("content-disposition", "")
    name_match = re.search(r'name="([^"]+)"', disposition)
    if not name_match:
        raise ValueError("Multipart field has no name")
    filename_match = re.search(r'filename="([^"]*)"', disposition)
    return name_match.group(1), filename_match.group(1) if filename_match else None


class _Handler(BaseHTTPRequestHandler):
    service: WebService
    settings: Settings
    static_root = Path(__file__).parent / "static"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, value: object, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path, content_type: str | None = None, attachment: bool = False) -> None:
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.parent == self.static_root:
            self.send_header("Cache-Control", "no-store, max-age=0")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                self.wfile.write(chunk)

    def _route(self) -> tuple[list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return [part for part in parsed.path.split("/") if part], parse_qs(parsed.query)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _stream_event(self, value: dict[str, object]) -> None:
        self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_answer(self, result: dict[str, Any]) -> None:
        text = str(result.get("answer", ""))
        paragraphs = text.splitlines(keepends=True)
        for paragraph in paragraphs:
            for offset in range(0, len(paragraph), 120):
                self._stream_event({"type": "delta", "text": paragraph[offset : offset + 120]})
        self._stream_event(
            {
                "type": "done",
                "evidence_refs": result.get("evidence_refs", []),
                "session_id": result.get("session_id"),
                "report_url": result.get("report_url"),
            }
        )

    def do_GET(self) -> None:
        try:
            parts, query = self._route()
            if not parts:
                return self._send_file(self.static_root / "index.html", "text/html; charset=utf-8")
            if parts == ["api", "health"]:
                return self._send_json({"status": "ok", "service": "advista-agent-web"})
            if parts == ["api", "runs"]:
                return self._send_json({"runs": self.service.runs()})
            if parts[0:2] == ["api", "jobs"] and len(parts) == 3:
                return self._send_json(self.service.job(parts[2]))
            if len(parts) >= 3 and parts[0:2] == ["api", "runs"]:
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                run = self.service.run(run_id)
                run_dir = self.service.store.run_dir(str(run["asset_run_id"]))
                artifact_root = self.service.artifact_root(run_id)
                if len(parts) == 3:
                    return self._send_json(run)
                if parts[3] == "messages":
                    return self._send_json(self.service.messages(run_id))
                if parts[3] == "timeline":
                    return self._send_json(self._timeline(run_dir))
                if parts[3] == "insights":
                    return self._artifact_json(artifact_root, "insights/analysis.json")
                if parts[3] == "creative":
                    return self._artifact_json(artifact_root, "creative/package.json")
                if parts[3] == "media":
                    asset = self.service.store.read_json(_contained(run_dir, "asset.json"))
                    return self._send_file(Path(asset["source_path"]), "video/mp4")
                if parts[3] == "keyframes" and len(parts) == 5:
                    return self._send_file(self._keyframe(run_dir, parts[4]), "image/jpeg")
                if parts[3] == "exports" and len(parts) == 5:
                    if parts[4] not in EXPORTS:
                        return self._error(404, "Unknown export")
                    relative, content_type = EXPORTS[parts[4]]
                    export_root = run_dir if parts[4] == "evidence-json" else artifact_root
                    return self._send_file(_contained(export_root, relative), content_type, attachment=True)
            if parts == ["app.js"] or parts == ["styles.css"]:
                path = self.static_root / parts[0]
                return self._send_file(path)
            return self._error(404, "Not found")
        except (FileNotFoundError, KeyError):
            self._error(404, "Run or artifact not found")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc)[-1000:])

    def _artifact_json(self, run_dir: Path, relative: str) -> None:
        path = _contained(run_dir, relative)
        self._send_file(path, "application/json")

    def _timeline(self, run_dir: Path) -> dict[str, object]:
        shots = self.service.store.read_json(_contained(run_dir, "timeline/shots.json"))
        keyframes_path = _contained(run_dir, "timeline/keyframes.jsonl")
        keyframes = [json.loads(line) for line in keyframes_path.read_text(encoding="utf-8").splitlines() if line]
        for item in keyframes:
            item["url"] = f"/api/runs/{run_dir.name}/keyframes/{item['keyframe_id']}"
        return {"shots": shots, "keyframes": keyframes}

    def _keyframe(self, run_dir: Path, keyframe_id: str) -> Path:
        if not keyframe_id.startswith(("keyframe_", "kf_")) or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in keyframe_id):
            raise ValueError("Invalid keyframe ID")
        path = _contained(run_dir, "timeline/keyframes.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("keyframe_id") == keyframe_id:
                artifact = (run_dir / str(item["artifact_path"])).resolve()
                if not artifact.is_file() or not artifact.is_relative_to(run_dir.resolve()):
                    raise FileNotFoundError(keyframe_id)
                return artifact
        raise FileNotFoundError(keyframe_id)

    def do_POST(self) -> None:
        try:
            parts, _ = self._route()
            if parts == ["api", "uploads"]:
                return self._upload()
            if parts == ["api", "chat"]:
                body = _read_json(self)
                return self._send_json(
                    self.service.general_chat(
                        str(body.get("message", "")),
                        str(body["session_id"]) if body.get("session_id") else None,
                    )
                )
            if parts == ["api", "chat", "stream"]:
                body = _read_json(self)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._stream_event({"type": "status", "text": "正在思考…"})
                try:
                    result = self.service.general_chat(
                        str(body.get("message", "")),
                        str(body["session_id"]) if body.get("session_id") else None,
                        on_delta=lambda value: self._stream_event({"type": "delta", "text": value}),
                    )
                except Exception as exc:
                    self._stream_event(
                        {
                            "type": "error",
                            "code": "chat_failed",
                            "message": str(exc)[-1000:],
                            "retryable": True,
                        }
                    )
                    return
                self._stream_event({"type": "done", "session_id": result.get("session_id"), "evidence_refs": []})
                return
            if len(parts) == 5 and parts[0:2] == ["api", "runs"] and parts[3:] == ["messages", "stream"]:
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                body = _read_json(self)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self._stream_event({"type": "status", "text": "正在读取视频证据…"})
                try:
                    result = self.service.chat_stream(
                        run_id,
                        str(body.get("question", "")),
                        lambda value: self._stream_event({"type": "delta", "text": value}),
                    )
                except Exception as exc:
                    self._stream_event(
                        {
                            "type": "error",
                            "code": "answer_validation_failed",
                            "message": str(exc)[-1000:],
                            "retryable": True,
                        }
                    )
                    return
                self._stream_event(
                    {
                        "type": "done",
                        "protocol_version": 1,
                        "session_id": result.get("session_id"),
                        "evidence_refs": result.get("evidence_refs", []),
                        "report_url": result.get("report_url"),
                        "message": {
                            "message_id": result.get("assistant_message_id"),
                            "version_id": result.get("version_id"),
                            "role": "assistant",
                            "content": result.get("answer", ""),
                            "epistemic_status": result.get("epistemic_status"),
                            "unsupported_points": result.get("unsupported_points", []),
                            "citations": result.get("citations", []),
                        },
                    }
                )
                return
            if len(parts) == 4 and parts[0:2] == ["api", "runs"]:
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                if parts[3] == "messages":
                    body = _read_json(self)
                    return self._send_json(self.service.chat(run_id, str(body.get("question", ""))))
                if parts[3] == "feedback":
                    return self._send_json(self.service.feedback(run_id, _read_json(self)))
                if parts[3] == "confirmation":
                    body = _read_json(self)
                    return self._send_json(
                        self.service.confirmation(
                            run_id,
                            str(body.get("decision", "")),
                            str(body.get("reason", "")),
                        )
                    )
                if parts[3] == "creative":
                    return self._send_json(self.service.creative(run_id))
            return self._error(404, "Not found")
        except (FileNotFoundError, KeyError):
            self._error(404, "Run or artifact not found")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc)[-1000:])

    def do_DELETE(self) -> None:
        try:
            parts, _ = self._route()
            if parts[0:2] == ["api", "jobs"] and len(parts) == 3:
                return self._send_json(self.service.cancel_job(parts[2]))
            return self._error(404, "Not found")
        except KeyError:
            self._error(404, "Job not found")
        except Exception as exc:
            self._error(500, str(exc)[-1000:])

    def _upload(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > self.settings.web.max_upload_bytes + 2_000_000:
            raise ValueError("Upload exceeds configured size limit")
        if not self.headers.get_content_type().startswith("multipart/"):
            raise ValueError("Upload must use multipart/form-data")
        boundary = str(self.headers.get_param("boundary"))
        if not boundary:
            raise ValueError("Missing multipart boundary")
        target_dir = self.settings.paths.output_root / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        reader = _MultipartReader(self.rfile, content_length)
        boundary_bytes = boundary.encode("utf-8")
        opening = reader.readline().strip()
        if opening != b"--" + boundary_bytes:
            raise ValueError("Malformed multipart opening boundary")
        fields: dict[str, bytes] = {}
        target: Path | None = None
        temporary: Path | None = None
        display_name = ""
        try:
            while True:
                name, filename = _multipart_headers(reader)
                if name == "video":
                    if not filename:
                        raise ValueError("Missing video upload")
                    suffix = Path(filename).suffix.casefold()
                    if suffix not in SUPPORTED_SUFFIXES:
                        raise ValueError("Unsupported video extension")
                    target = target_dir / f"upload_{os.urandom(12).hex()}{suffix}"
                    temporary = target.with_name(f".{target.name}.part")
                    handle = temporary.open("wb")
                    try:
                        _, final = reader.body_until(
                            boundary_bytes,
                            handle.write,
                            self.settings.web.max_upload_bytes,
                        )
                    finally:
                        handle.close()
                    display_name = Path(filename).name
                else:
                    value = bytearray()
                    _, final = reader.body_until(
                        boundary_bytes,
                        value.extend,
                        1_000_000,
                    )
                    fields[name] = bytes(value)
                if final:
                    break
            if target is None or temporary is None:
                raise ValueError("Missing video upload")
            temporary.replace(target)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if target is not None:
                target.unlink(missing_ok=True)
            raise
        target.with_suffix(target.suffix + ".name").write_text(display_name, encoding="utf-8")
        goal = fields.get("goal", "分析这个广告视频".encode("utf-8")).decode(
            "utf-8", errors="replace"
        )
        mode = fields.get("mode", b"quick").decode("utf-8", errors="replace")
        if mode not in {"quick", "deep"}:
            raise ValueError("Mode must be quick or deep")
        raw_deliverables = fields.get("deliverables", b"[]").decode(
            "utf-8", errors="replace"
        )
        deliverables = json.loads(raw_deliverables)
        if not isinstance(deliverables, list) or not all(
            isinstance(item, str) for item in deliverables
        ):
            raise ValueError("Deliverables must be a JSON string array")
        allowed = {"evidence", "insights", "risk_audit", "report", "creative"}
        unknown = set(deliverables).difference(allowed)
        if unknown:
            raise ValueError(f"Unknown deliverables: {', '.join(sorted(unknown))}")
        return self._send_json(
            self.service.submit(target, goal, list(dict.fromkeys(deliverables)), mode=mode),
            HTTPStatus.ACCEPTED,
        )


def create_server(settings: Settings | None = None) -> ThreadingHTTPServer:
    config = settings or load_settings(DEFAULT_CONFIG)
    service = WebService(config)
    handler = type("AdVistaHandler", (_Handler,), {"service": service, "settings": config})
    server = ThreadingHTTPServer((config.web.host, config.web.port), handler)
    server.service = service  # type: ignore[attr-defined]
    return server


def serve(settings: Settings | None = None) -> None:
    server = create_server(settings)
    try:
        print(f"AdVista Web listening on http://{server.server_address[0]}:{server.server_address[1]}")
        server.serve_forever()
    finally:
        server.service.close()  # type: ignore[attr-defined]
        server.server_close()
