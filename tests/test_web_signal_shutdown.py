from __future__ import annotations

import signal
import subprocess
import sys
import textwrap

import pytest

from ad_vista_agent.web import app


@pytest.mark.parametrize("close_fails", [False, True])
def test_sigterm_runs_service_and_socket_cleanup(close_fails):
    code = textwrap.dedent(f"""
        import os, signal
        from ad_vista_agent.web import app
        class Service:
            def close(self):
                print("service-close", flush=True)
                os.kill(os.getpid(), signal.SIGTERM)
                if {close_fails!r}:
                    raise RuntimeError("close failure")
        class Server:
            server_address = ("127.0.0.1", 0)
            service = Service()
            def serve_forever(self):
                os.kill(os.getpid(), signal.SIGTERM)
            def server_close(self):
                print("socket-close", flush=True)
        app.create_server = lambda settings=None: Server()
        app.serve()
    """)
    process = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert "service-close" in process.stdout
    assert "socket-close" in process.stdout
    assert process.returncode == (1 if close_fails else 0)


def test_serve_restores_signal_handlers_after_error(monkeypatch):
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    class Service:
        def close(self):
            pass

    class Server:
        server_address = ("127.0.0.1", 0)
        service = Service()
        closed = False

        def serve_forever(self):
            raise RuntimeError("server error")

        def server_close(self):
            self.closed = True

    server = Server()
    monkeypatch.setattr(app, "create_server", lambda settings=None: server)
    with pytest.raises(RuntimeError, match="server error"):
        app.serve()
    assert server.closed
    assert {sig: signal.getsignal(sig) for sig in previous} == previous
