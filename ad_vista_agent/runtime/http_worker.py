"""Standard-library HTTP client worker. Request data arrives through stdin only."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    headers_sent = False
    try:
        value = json.loads(sys.stdin.buffer.readline())
        request = urllib.request.Request(
            value["url"],
            data=base64.b64decode(value["data"]) if value["data"] is not None else None,
            headers=value["headers"],
            method=value["method"],
        )
        try:
            response = urllib.request.urlopen(request, timeout=value["timeout"])
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            metadata = {
                "status": response.status,
                "headers": list(response.headers.items()),
                "url": response.geturl(),
            }
            sys.stdout.buffer.write(json.dumps(metadata).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
            headers_sent = True
            while chunk := response.read1(65536):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        return 0
    except (TimeoutError, urllib.error.URLError) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(
            getattr(exc, "reason", None), TimeoutError
        )
        if not headers_sent:
            sys.stdout.buffer.write(
                json.dumps({"error": "timeout" if timed_out else "unavailable"}).encode(
                    "utf-8"
                )
                + b"\n"
            )
            sys.stdout.buffer.flush()
        return 2 if timed_out else 3
    except Exception:  # noqa: BLE001 — 隔离网络客户端失败，以退出码报告，不输出请求或凭据。
        if not headers_sent:
            sys.stdout.buffer.write(b'{"error":"unavailable"}\n')
            sys.stdout.buffer.flush()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
