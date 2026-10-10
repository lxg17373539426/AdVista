from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ad_vista_agent.runtime.http import cancellable_urlopen
from ad_vista_agent.runtime.process import ProcessCancelled
from ad_vista_agent.tools import ToolContext
from ad_vista_agent.tools.qwen import QwenInsightTool


@pytest.fixture
def model_server():
    ready = threading.Event()
    disconnected = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, data, self.headers.get("Authorization")))
            if self.path in {"/headers", "/body", "/stream", "/v1/chat/completions"}:
                if self.path in {"/body", "/stream"}:
                    self.send_response(200)
                    if self.path == "/body":
                        self.send_header("Content-Length", "10")
                    self.end_headers()
                    self.wfile.write(
                        b"x" if self.path == "/body" else b"data: hello\n\n"
                    )
                    self.wfile.flush()
                ready.set()
                self.connection.settimeout(3)
                try:
                    if self.connection.recv(1) == b"":
                        disconnected.set()
                except OSError:
                    pass
                return
            self.send_response(429 if self.path == "/error" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"error":"limit"}'
                if self.path == "/error"
                else (b"not-json" if self.path == "/invalid" else b'{"ok":true}')
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", ready, disconnected, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("path", ["/headers", "/body", "/stream"])
def test_cancel_aborts_header_body_and_stream_waits(model_server, path):
    endpoint, ready, disconnected, _ = model_server
    event = threading.Event()
    original_watchers = {
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("advista-http-")
    }

    def read():
        with cancellable_urlopen(
            urllib.request.Request(endpoint + path, data=b"{}"),
            timeout=20,
            cancel_event=event,
        ) as response:
            return response.read() if path != "/stream" else list(response)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        assert ready.wait(3)
        started = time.monotonic()
        event.set()
        with pytest.raises(ProcessCancelled):
            future.result(timeout=2)
        assert time.monotonic() - started < 1
    assert disconnected.wait(1)
    assert {
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("advista-http-")
    } == original_watchers


def test_stream_delivers_data_before_model_finishes(model_server):
    endpoint, _, disconnected, _ = model_server
    event = threading.Event()
    with cancellable_urlopen(
        urllib.request.Request(endpoint + "/stream", data=b"{}"),
        timeout=20,
        cancel_event=event,
    ) as response:
        assert response.readline() == b"data: hello\n"
        event.set()
        with pytest.raises(ProcessCancelled):
            response.readline()
    assert disconnected.wait(1)


def test_timeout_terminates_waiting_client(model_server):
    endpoint, _, disconnected, _ = model_server
    started = time.monotonic()
    with (
        pytest.raises(TimeoutError),
        cancellable_urlopen(
            urllib.request.Request(endpoint + "/headers", data=b"{}"),
            timeout=0.3,
            cancel_event=threading.Event(),
        ),
    ):
        pass
    assert time.monotonic() - started < 1.5
    assert disconnected.wait(1)


def test_http_error_retains_status_and_readable_body(model_server):
    endpoint, _, _, _ = model_server
    with (
        pytest.raises(urllib.error.HTTPError) as caught,
        cancellable_urlopen(
            urllib.request.Request(endpoint + "/error", data=b"{}"),
            timeout=3,
            cancel_event=threading.Event(),
        ),
    ):
        pass
    assert caught.value.code == 429
    assert json.loads(caught.value.read()) == {"error": "limit"}


def test_normal_response_preserves_request_and_cleans_worker(model_server):
    endpoint, _, _, requests = model_server
    data = json.dumps({"prompt": "private input 中文"}).encode()
    with cancellable_urlopen(
        urllib.request.Request(
            endpoint + "/normal",
            data=data,
            headers={"Authorization": "Bearer private-test"},
        ),
        timeout=3,
        cancel_event=threading.Event(),
    ) as response:
        process = response.process
        command = " ".join(process.args).encode("utf-8")
        assert b"private" not in command
        assert response.status == 200
        assert json.loads(response.read()) == {"ok": True}
    assert process.poll() is not None
    assert requests == [("/normal", data, "Bearer private-test")]


def test_pre_cancelled_request_never_starts_worker(monkeypatch):
    event = threading.Event()
    event.set()

    def forbidden(*args, **kwargs):
        pytest.fail("pre-cancelled request started a worker")

    monkeypatch.setattr("ad_vista_agent.runtime.http.subprocess.Popen", forbidden)
    with (
        pytest.raises(ProcessCancelled),
        cancellable_urlopen(
            urllib.request.Request("http://127.0.0.1:1", data=b"{}"),
            timeout=3,
            cancel_event=event,
        ),
    ):
        pass


def test_qwen_cancellation_closes_real_model_connection(model_server, tmp_path):
    endpoint, ready, disconnected, requests = model_server
    event = threading.Event()
    tool = QwenInsightTool(
        Path("/unused"), 20, "0", runtime="openai", endpoint=endpoint + "/v1"
    )
    arguments = {
        "messages": [],
        "temperature": 0,
        "max_tokens": 10,
        "output_schema": {},
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            tool.run,
            ToolContext(run_id="test", run_dir=tmp_path, cancel_event=event),
            arguments,
        )
        assert ready.wait(3)
        event.set()
        with pytest.raises(ProcessCancelled):
            future.result(timeout=2)
    assert disconnected.wait(1)
    assert len(requests) == 1


def test_caller_json_error_is_not_reclassified_as_worker_failure(model_server):
    endpoint, _, _, _ = model_server
    with (
        pytest.raises(json.JSONDecodeError),
        cancellable_urlopen(
            urllib.request.Request(endpoint + "/invalid", data=b"{}"),
            timeout=3,
            cancel_event=threading.Event(),
        ) as response,
    ):
        json.loads(response.read())


def test_timeout_during_body_wait_is_preserved(model_server):
    endpoint, _, disconnected, _ = model_server
    with (
        pytest.raises(TimeoutError),
        cancellable_urlopen(
            urllib.request.Request(endpoint + "/body", data=b"{}"),
            timeout=0.3,
            cancel_event=threading.Event(),
        ) as response,
    ):
        response.read()
    assert disconnected.wait(1)
