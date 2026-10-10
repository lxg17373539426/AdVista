from __future__ import annotations

import threading
import urllib.error
import urllib.request

import httpx

from ad_vista_agent.runtime.http import cancellable_urlopen


class CancellableModelTransport(httpx.BaseTransport):
    """Keep the OpenAI SDK protocol while bounding its HTTP wait by cancellation."""

    def __init__(self, cancel_event: threading.Event, timeout: float) -> None:
        self.cancel_event = cancel_event
        self.timeout = timeout

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        call = urllib.request.Request(
            str(request.url), data=request.read(), headers=dict(request.headers),
            method=request.method,
        )
        try:
            with cancellable_urlopen(call, timeout=self.timeout, cancel_event=self.cancel_event) as response:
                return httpx.Response(
                    status_code=response.status, headers=list(response.headers.items()),
                    content=response.read(), request=request,
                )
        except urllib.error.HTTPError as exc:
            return httpx.Response(
                status_code=exc.code, headers=list(exc.headers.items()),
                content=exc.read(), request=request,
            )
