from __future__ import annotations

import base64
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from typing import IO, Any

from .process import ProcessCancelled


def _stop_worker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    except ProcessLookupError:
        process.wait()


class _WorkerResponse:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        cancel_event: threading.Event,
        timeout: float,
    ) -> None:
        assert process.stdout is not None
        self.status = 0
        self.headers = Message()
        self.process = process
        self.stream: IO[bytes] = process.stdout
        self.cancel_event = cancel_event
        self.timeout = timeout
        self.started = time.monotonic()
        self.stopped = threading.Event()
        self.expired = threading.Event()
        self.watcher = threading.Thread(
            target=self._watch, name=f"advista-http-{process.pid}", daemon=True
        )
        self.watcher.start()

    def _watch(self) -> None:
        while not self.stopped.wait(0.05):
            if self.cancel_event.is_set():
                _stop_worker(self.process)
                return
            if time.monotonic() - self.started >= self.timeout:
                self.expired.set()
                _stop_worker(self.process)
                return

    def _check(self) -> None:
        if self.cancel_event.is_set():
            raise ProcessCancelled("Model HTTP request cancelled")
        if self.expired.is_set() or self.process.poll() == 2:
            raise TimeoutError("Model HTTP request timed out")
        if self.process.poll() not in {None, 0}:
            raise urllib.error.URLError("Model HTTP request failed")

    def read(self, size: int = -1) -> bytes:
        self._check()
        value = self.stream.read(size)
        self._check()
        return value

    def readline(self, size: int = -1) -> bytes:
        self._check()
        value = self.stream.readline(size)
        self._check()
        return value

    def __iter__(self) -> Iterator[bytes]:
        while line := self.readline():
            yield line

    def close(self) -> None:
        self.stopped.set()
        _stop_worker(self.process)
        self.watcher.join()
        self.stream.close()
        if self.process.stdin is not None:
            self.process.stdin.close()


@contextmanager
def cancellable_urlopen(
    request: urllib.request.Request,
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> Iterator[Any]:
    """urllib-compatible model response; cancellation terminates its isolated HTTP client.

    A worker bounds connection, header and body waits, including a stalled stream.
    Stdin carries the request, so prompts and credentials never enter process argv.
    Without a cancellation scope the existing urllib transport is preserved.
    """
    from ad_vista_agent.tools.base import get_active_cancel_event

    event = cancel_event if cancel_event is not None else get_active_cancel_event()
    if event is None:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            yield response
        return
    if event.is_set():
        raise ProcessCancelled("Model HTTP request cancelled before connection")
    if timeout <= 0:
        raise ValueError("Model HTTP timeout must be positive")
    data = request.data
    if data is not None and not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Model HTTP request body must be bytes")
    payload = {
        "url": request.full_url,
        "method": request.get_method(),
        "data": base64.b64encode(data).decode("ascii") if data is not None else None,
        "headers": dict(request.header_items()),
        "timeout": timeout,
    }
    process = subprocess.Popen(
        [sys.executable, "-I", "-u", str(Path(__file__).with_name("http_worker.py"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    response = _WorkerResponse(process, event, timeout)
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            process.stdin.flush()
            metadata = json.loads(response.readline())
        except (BrokenPipeError, json.JSONDecodeError) as exc:
            response._check()
            raise urllib.error.URLError(
                "Model HTTP worker returned an invalid response"
            ) from exc
        if metadata.get("error") == "timeout":
            raise TimeoutError("Model HTTP request timed out")
        if metadata.get("error"):
            raise urllib.error.URLError("Model HTTP request failed")
        response.status = int(metadata["status"])
        response.headers = Message()
        for name, value in metadata["headers"]:
            response.headers[name] = value
        if response.status >= 400:
            body = response.read()
            raise urllib.error.HTTPError(
                request.full_url,
                response.status,
                "Model HTTP error",
                response.headers,
                io.BytesIO(body),
            )
        yield response
    finally:
        response.close()
