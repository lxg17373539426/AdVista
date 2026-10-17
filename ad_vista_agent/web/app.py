from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import signal
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

from ad_vista_agent.config import DEFAULT_CONFIG, Settings, load_settings
from ad_vista_agent.errors import ConversationBusyError, JobCapacityError
from ad_vista_agent.ingestion import SUPPORTED_SUFFIXES

from .service import WebService

EXPORTS = {
    "evidence-html": ("ledger/evidence.html", "text/html; charset=utf-8"),
    "evidence-markdown": ("ledger/evidence.md", "text/markdown; charset=utf-8"),
    "evidence-json": ("ledger/evidence.jsonl", "application/json"),
    "report-html": ("report/report.html", "text/html; charset=utf-8"),
    "report-markdown": ("report/report.md", "text/markdown; charset=utf-8"),
    "analysis-json": ("insights/analysis.json", "application/json"),
    "audit-json": ("critic/audit.json", "application/json"),
    "creative-json": ("creative/package.json", "application/json"),
    "strategy-json": ("strategy/strategy.json", "application/json"),
    "marketing-html": ("marketing/marketing.html", "text/html; charset=utf-8"),
    "marketing-markdown": ("marketing/marketing.md", "text/markdown; charset=utf-8"),
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
    if length <= 0:
        raise ValueError("JSON request length must be positive")
    if length > 1_000_000:
        raise ValueError("JSON request is too large")
    value = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")  # noqa: TRY004 — 保留请求内容验证的 ValueError 契约。
    return value


class _MultipartReader:
    def __init__(self, stream: Any, length: int) -> None:
        self.stream = stream
        self.remaining = length
        self.buffer = bytearray()

    def _fill(self) -> None:
        if self.remaining <= 0:
            raise ValueError("Upload ended before multipart body completed")
        chunk = self.stream.read(min(1024 * 1024, self.remaining))
        if not chunk:
            raise ValueError("Upload ended before multipart body completed")
        self.remaining -= len(chunk)
        self.buffer.extend(chunk)

    def readline(self) -> bytes:
        while b"\n" not in self.buffer:
            if len(self.buffer) > 65536:
                raise ValueError("Multipart header is too large")
            self._fill()
        index = self.buffer.index(10) + 1
        if index > 65536:
            raise ValueError("Multipart header is too large")
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
    header_bytes = 0
    while True:
        line = reader.readline()
        header_bytes += len(line)
        if header_bytes > 65536:
            raise ValueError("Multipart headers are too large")
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
    _rate_lock = threading.RLock()
    _rate_windows: ClassVar[dict[str, list[float]]] = {}
    session_cookie = "advista_session"

    def _identity(self) -> dict[str, str] | None:
        return getattr(self, "identity", None)

    def _session_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        value = cookie.get(self.session_cookie)
        return value.value if value is not None else ""

    def _require_admin(self) -> bool:
        identity = self._identity()
        if identity is None:
            supplied = self.headers.get("X-API-Key", "")
            authorization = self.headers.get("Authorization", "")
            if not supplied and authorization.startswith("Bearer "):
                supplied = authorization[7:]
            service = getattr(self, "service", None)
            identity = service.authenticate_api_key(supplied) if service is not None else None
            if identity is not None:
                self.identity = identity
        if identity is not None and identity.get("role") == "admin":
            return True
        self._error(403, "Administrator access required")
        return False

    def _idempotency_key(self) -> str | None:
        value = self.headers.get("Idempotency-Key", "").strip()
        return value or None

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

    def _send_session(self, token: str, identity: dict[str, str]) -> None:
        payload = json.dumps(
            {"status": "ok", "user": identity}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Set-Cookie",
            f"{self.session_cookie}={token}; Path=/; Max-Age={self.settings.web.session_ttl_seconds}; HttpOnly; Secure; SameSite=Strict",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _clear_session(self) -> None:
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Set-Cookie",
            f"{self.session_cookie}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict",
        )
        self.end_headers()

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

    def _require_api_access(self, parts: list[str]) -> bool:
        if not parts or parts[0] != "api":
            return True
        configured = self.settings.web.api_token
        local = self.settings.web.host in {"127.0.0.1", "localhost", "::1"}
        supplied = self.headers.get("X-API-Key", "")
        authorization = self.headers.get("Authorization", "")
        if not supplied and authorization.startswith("Bearer "):
            supplied = authorization[7:]
        service = getattr(self, "service", None)
        identity = service.authenticate_api_key(supplied) if service is not None else None
        if identity is None and service is not None:
            identity = service.authenticate_web_session(self._session_token())
        if identity is not None:
            self.identity = identity
            subject = identity.get("user_id", "unknown")
        elif configured and secrets.compare_digest(supplied, configured):
            self.identity = {"user_id": "system", "username": "system", "role": "admin"}
            subject = "system"
        elif (
            local
            and not configured
            and not getattr(self.settings.web, "require_api_auth", False)
            and not supplied
        ):
            subject = "anonymous-local"
        else:
            self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return False
        if not self._within_rate_limit(subject):
            self._send_json({"error": "Rate limit exceeded"}, HTTPStatus.TOO_MANY_REQUESTS)
            return False
        return True

    def _within_rate_limit(self, subject: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        with self._rate_lock:
            values = [item for item in self._rate_windows.get(subject, []) if item > cutoff]
            allowed = len(values) < self.settings.web.api_requests_per_minute
            if allowed:
                values.append(now)
            self._rate_windows[subject] = values
            return allowed

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
                "report_label": result.get("report_label"),
            }
        )

    def do_GET(self) -> None:
        try:
            parts, query = self._route()
            if not self._require_api_access(parts):
                return
            if not parts:
                return self._send_file(self.static_root / "index.html", "text/html; charset=utf-8")
            if parts == ["admin"] or parts == ["admin.html"]:
                return self._send_file(self.static_root / "admin.html", "text/html; charset=utf-8")
            if parts == ["api", "health"]:
                return self._send_json(
                    {"service": "advista-agent-web", **self.service.health()}
                )
            if parts == ["api", "auth", "me"]:
                return self._send_json({"user": self._identity()})
            if parts == ["api", "runs"]:
                identity = self._identity()
                return self._send_json({"runs": self.service.runs(
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                )})
            if parts == ["api", "chat", "messages"]:
                session_id = query.get("session_id", [""])[0]
                return self._send_json(self.service.general_messages(
                    session_id, user_id=(self._identity() or {}).get("user_id"),
                ))
            if parts == ["api", "conversations"]:
                return self._send_json(self.service.conversation_page(
                    user_id=(self._identity() or {}).get("user_id"),
                    limit=int(query.get("limit", ["100"])[0]),
                    offset=int(query.get("offset", ["0"])[0]),
                ))
            if parts == ["api", "admin", "executions"]:
                if not self._require_admin():
                    return
                status = query.get("status", [None])[0]
                raw_limit = query.get("limit", ["100"])[0]
                return self._send_json(
                    {"executions": self.service.diagnostic_executions(
                        status=status,
                        limit=int(raw_limit),
                    )}
                )
            if parts == ["api", "admin", "stats"]:
                if not self._require_admin():
                    return
                return self._send_json(self.service.diagnostic_stats())
            if parts == ["api", "admin", "executions", "export"]:
                if not self._require_admin():
                    return
                status = query.get("status", [None])[0]
                raw_limit = query.get("limit", ["500"])[0]
                return self._send_json(
                    {
                        "status": "ok",
                        "records": self.service.diagnostic_export(
                            status=status,
                            limit=int(raw_limit),
                        ),
                    }
                )
            if parts[:2] == ["api", "admin"] and len(parts) == 4 and parts[2] == "executions":
                if not self._require_admin():
                    return
                return self._send_json(self.service.diagnostic_execution(parts[3]))
            if parts[0:2] == ["api", "jobs"] and len(parts) == 3:
                identity = self._identity()
                return self._send_json(self.service.job(
                    parts[2],
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                ))
            if len(parts) >= 3 and parts[0:2] == ["api", "runs"]:
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                identity = self._identity()
                self.service.authorize_run(
                    run_id,
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                )
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
                    return self._send_file(
                        self.service.allowed_media_path(Path(asset["source_path"])),
                        "video/mp4",
                    )
                if parts[3] == "keyframes" and len(parts) == 5:
                    return self._send_file(self._keyframe(run_dir, parts[4]), "image/jpeg")
                if parts[3] == "exports" and len(parts) == 5:
                    if parts[4] not in EXPORTS:
                        return self._error(404, "Unknown export")
                    if parts[4] in {"report-html", "report-markdown", "audit-json"} and not self.service.has_reportable_insights(run_id):
                        return self._error(409, "当前视频没有可验证洞察，无法下载空报告")
                    relative, content_type = EXPORTS[parts[4]]
                    export_root = run_dir if parts[4].startswith("evidence-") else artifact_root
                    download = query.get("download", [""])[0] == "1"
                    # HTML exports open in the browser unless the user explicitly downloads them.
                    return self._send_file(
                        _contained(export_root, relative),
                        content_type,
                        attachment=download or not content_type.startswith("text/html"),
                    )
            if parts == ["app.js"] or parts == ["styles.css"]:
                path = self.static_root / parts[0]
                return self._send_file(path)
            return self._error(404, "Not found")
        except (FileNotFoundError, KeyError):
            self._error(404, "Run or artifact not found")
        except PermissionError as exc:
            self._error(403, str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except Exception:  # noqa: BLE001 — HTTP 边界统一输出脱敏错误，保留异常兜底。
            self._error(500, "服务暂时无法完成请求，请稍后重试。")

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
            if parts == ["api", "auth", "login"]:
                subject = f"login:{self.client_address[0]}"
                if not self._within_rate_limit(subject):
                    return self._send_json(
                        {"error": "Rate limit exceeded"}, HTTPStatus.TOO_MANY_REQUESTS
                    )
                body = _read_json(self)
                session = self.service.create_web_session(str(body.get("api_key", "")))
                if session is None:
                    return self._send_json(
                        {"error": "Invalid API key"}, HTTPStatus.UNAUTHORIZED
                    )
                return self._send_session(*session)
            if parts == ["api", "auth", "logout"]:
                self.service.delete_web_session(self._session_token())
                return self._clear_session()
            if not self._require_api_access(parts):
                return
            if parts == ["api", "uploads"]:
                return self._upload()
            if len(parts) in {4, 5} and parts[:2] == ["api", "conversations"] and parts[3:] in (["title"], ["title", "generate"]):
                body = _read_json(self)
                owner = (self._identity() or {}).get("user_id")
                if parts[3:] == ["title", "generate"]:
                    result = self.service.generate_conversation_title(parts[2], user_id=owner)
                else:
                    result = self.service.rename_conversation(parts[2], body.get("title"), user_id=owner)
                return self._send_json(result)
            if len(parts) in {4, 5} and parts[:2] == ["api", "runs"] and parts[3:] in (["title"], ["title", "generate"]):
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                identity = self._identity()
                owner = identity.get("user_id") if identity else None
                self.service.authorize_run(run_id, user_id=owner, is_admin=bool(identity and identity.get("role") == "admin"))
                body = _read_json(self)
                if parts[3:] == ["title", "generate"]:
                    result = self.service.generate_run_title(run_id, user_id=owner)
                else:
                    result = self.service.rename_run(run_id, body.get("title"), user_id=owner)
                return self._send_json(result)
            if parts == ["api", "chat"]:
                body = _read_json(self)
                return self._send_json(
                    self.service.general_chat(
                        str(body.get("message", "")),
                        str(body["session_id"]) if body.get("session_id") else None,
                        user_id=(self._identity() or {}).get("user_id"),
                        idempotency_key=self._idempotency_key(),
                    )
                )
            if parts == ["api", "chat", "stream"]:
                body = _read_json(self)
                if body.get("session_id"):
                    self.service.authorize_conversation(
                        str(body["session_id"]), user_id=(self._identity() or {}).get("user_id"), allow_missing=True,
                    )
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
                        user_id=(self._identity() or {}).get("user_id"),
                        idempotency_key=self._idempotency_key(),
                    )
                except Exception:  # noqa: BLE001 — HTTP 边界统一输出脱敏错误，保留异常兜底。
                    self._stream_event(
                        {
                            "type": "error",
                            "code": "chat_failed",
                            "message": "视频问答暂时无法完成，请稍后重试；如果问题持续，请重新上传视频。",
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
                identity = self._identity()
                self.service.authorize_run(
                    run_id,
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                )
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
                        user_id=identity.get("user_id") if identity else None,
                        idempotency_key=self._idempotency_key(),
                    )
                except Exception:  # noqa: BLE001 — HTTP 边界统一输出脱敏错误，保留异常兜底。
                    self._stream_event(
                        {
                            "type": "error",
                            "code": "answer_validation_failed",
                            "message": "视频问答暂时无法完成，请稍后重试；如果问题持续，请重新上传视频。",
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
                        "context_version": result.get("context_version"),
                        "report_url": result.get("report_url"),
                        "report_label": result.get("report_label"),
                        "message": {
                            "message_id": result.get("assistant_message_id"),
                            "version_id": result.get("version_id"),
                            "role": "assistant",
                            "content": result.get("answer", ""),
                            "epistemic_status": result.get("epistemic_status"),
                            "unsupported_points": result.get("unsupported_points", []),
                            "context_version": result.get("context_version"),
                            "citations": result.get("citations", []),
                            "confirmation_required": result.get("confirmation_required", False),
                            "confirmations": result.get("confirmations", []),
                        },
                    }
                )
                return
            if len(parts) == 4 and parts[0:2] == ["api", "runs"]:
                run_id = parts[2]
                if not _safe_run_id(run_id):
                    return self._error(400, "Invalid run ID")
                identity = self._identity()
                self.service.authorize_run(
                    run_id,
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                )
                if parts[3] == "messages":
                    body = _read_json(self)
                    return self._send_json(self.service.chat(
                        run_id,
                        str(body.get("question", "")),
                        user_id=identity.get("user_id") if identity else None,
                        idempotency_key=self._idempotency_key(),
                    ))
                if parts[3] == "feedback":
                    return self._send_json(
                        self.service.feedback(
                            run_id,
                            _read_json(self),
                            user_id=(self._identity() or {}).get("user_id"),
                        )
                    )
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
        except PermissionError as exc:
            self._error(403, str(exc))
        except JobCapacityError as exc:
            self._error(HTTPStatus.TOO_MANY_REQUESTS, str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
        except Exception:  # noqa: BLE001 — HTTP 边界统一输出脱敏错误，保留异常兜底。
            self._error(500, "服务暂时无法完成请求，请稍后重试。")

    def do_DELETE(self) -> None:
        parts: list[str] = []
        try:
            parts, _ = self._route()
            if not self._require_api_access(parts):
                return
            if parts[:2] == ["api", "conversations"] and len(parts) == 3:
                return self._send_json(self.service.delete_conversation(
                    parts[2], user_id=(self._identity() or {}).get("user_id"),
                ))
            if parts[:2] == ["api", "runs"] and len(parts) == 3:
                if not _safe_run_id(parts[2]):
                    return self._error(400, "Invalid run ID")
                return self._send_json(self.service.delete_run(
                    parts[2], user_id=(self._identity() or {}).get("user_id"),
                ))
            if parts[0:2] == ["api", "jobs"] and len(parts) == 3:
                identity = self._identity()
                return self._send_json(self.service.cancel_job(
                    parts[2],
                    user_id=identity.get("user_id") if identity else None,
                    is_admin=bool(identity and identity.get("role") == "admin"),
                ))
            return self._error(404, "Not found")
        except PermissionError as exc:
            self._error(403, str(exc))
        except ConversationBusyError as exc:
            self._error(409, str(exc))
        except (FileNotFoundError, KeyError):
            self._error(404, "会话不存在或已删除。" if parts[:2] in (["api", "conversations"], ["api", "runs"]) else "Job not found")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception:  # noqa: BLE001 — HTTP 边界统一输出脱敏错误，保留异常兜底。
            self._error(500, "会话暂时无法删除，请稍后重试。" if parts[:2] in (["api", "conversations"], ["api", "runs"]) else "任务状态暂时无法读取，请稍后重试。")

    def _upload(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("Upload request length must be positive")
        if content_length > self.settings.web.max_upload_bytes + 2_000_000:
            raise ValueError("Upload exceeds configured size limit")
        if self.headers.get_content_type() != "multipart/form-data":
            raise ValueError("Upload must use multipart/form-data")
        boundary = self.headers.get_param("boundary")
        if not isinstance(boundary, str) or not boundary:
            raise ValueError("Missing multipart boundary")
        if len(boundary) > 70 or any(ord(char) < 32 or ord(char) > 126 for char in boundary):
            raise ValueError("Invalid multipart boundary")
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
        part_count = 0
        field_bytes = 0
        try:
            while True:
                part_count += 1
                if part_count > 32:
                    raise ValueError("Too many multipart fields")
                name, filename = _multipart_headers(reader)
                if name == "video":
                    if target is not None:
                        raise ValueError("Only one video upload is allowed")
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
                        1_000_000 - field_bytes,
                    )
                    field_bytes += len(value)
                    fields[name] = bytes(value)
                if final:
                    break
            if target is None or temporary is None:
                raise ValueError("Missing video upload")
            temporary.replace(target)
            self.service.validate_uploaded_video(target)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if target is not None:
                target.unlink(missing_ok=True)
            raise
        target.with_suffix(target.suffix + ".name").write_text(display_name, encoding="utf-8")
        try:
            goal = fields.get("goal", "分析这个广告视频".encode()).decode(
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
            allowed = {"report", "strategy", "creative"}
            unknown = set(deliverables).difference(allowed)
            if unknown:
                raise ValueError(f"Unknown deliverables: {', '.join(sorted(unknown))}")
            return self._send_json(
                self.service.submit(
                    target,
                    goal,
                    list(dict.fromkeys(deliverables)),
                    mode=mode,
                    user_id=(self._identity() or {}).get("user_id"),
                    idempotency_key=self._idempotency_key(),
                ),
                HTTPStatus.ACCEPTED,
            )
        except Exception:
            target.unlink(missing_ok=True)
            target.with_suffix(target.suffix + ".name").unlink(missing_ok=True)
            raise


def create_server(settings: Settings | None = None) -> ThreadingHTTPServer:
    config = settings or load_settings(DEFAULT_CONFIG)
    service = WebService(config)
    handler = type("AdVistaHandler", (_Handler,), {"service": service, "settings": config})
    server = ThreadingHTTPServer((config.web.host, config.web.port), handler)
    server.service = service  # type: ignore[attr-defined]
    return server


def serve(settings: Settings | None = None) -> None:
    server = create_server(settings)
    previous: dict[signal.Signals, Any] = {}
    shutting_down = False

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(0)

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)
    try:
        address = server.server_address[0]
        host = address.decode() if isinstance(address, bytes) else address
        print(f"AdVista Web listening on http://{host}:{server.server_address[1]}")
        server.serve_forever()
    finally:
        shutting_down = True
        try:
            server.service.close()  # type: ignore[attr-defined]
        finally:
            try:
                server.server_close()
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
