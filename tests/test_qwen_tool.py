import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ad_vista_agent.tools.qwen import QwenInsightTool


class QwenHandler(BaseHTTPRequestHandler):
    status = 200
    response = {
        "choices": [{"message": {"content": '{"answer":"ok"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 2}},
    }
    received = None

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).received = json.loads(self.rfile.read(length))
        body = json.dumps(type(self).response).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class QwenInsightToolTests(unittest.TestCase):
    def setUp(self) -> None:
        QwenHandler.status = 200
        QwenHandler.received = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QwenHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def tool(self) -> QwenInsightTool:
        return QwenInsightTool(
            Path("/missing/python"),
            timeout_seconds=2,
            cuda_visible_devices="0",
            runtime="openai",
            endpoint=f"http://127.0.0.1:{self.server.server_port}/v1",
            served_model="test-model",
        )

    def test_openai_runtime_uses_structured_output(self) -> None:
        result = self.tool().run(
            None,  # type: ignore[arg-type]
            {
                "messages": [{"role": "user", "content": "test"}],
                "temperature": 0,
                "max_tokens": 32,
                "output_schema": {"type": "object"},
            },
        )
        self.assertEqual(result.text, '{"answer":"ok"}')
        self.assertEqual(result.prompt_tokens, 10)
        self.assertEqual(result.completion_tokens, 4)
        received = QwenHandler.received
        self.assertIsNotNone(received)
        assert received is not None
        self.assertEqual(received["model"], "test-model")
        self.assertEqual(received["structured_outputs"]["json"], {"type": "object"})

    def test_openai_runtime_surfaces_http_error(self) -> None:
        QwenHandler.status = 503
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.tool().run(
                None,  # type: ignore[arg-type]
                {
                    "messages": [],
                    "temperature": 0,
                    "max_tokens": 32,
                    "output_schema": {"type": "object"},
                },
            )

    def test_subprocess_runtime_still_checks_interpreter(self) -> None:
        tool = QwenInsightTool(Path("/missing/python"), 1, "0", runtime="subprocess")
        with self.assertRaises(FileNotFoundError):
            tool.run(None, {})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
