import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from ad_vista_agent.config import load_settings
from ad_vista_agent.runtime.process import ProcessCancelled, run_process
from ad_vista_agent.web.app import _Handler
from ad_vista_agent.web.service import WebService


def test_remote_web_host_requires_a_long_api_token() -> None:
    base = load_settings()

    with pytest.raises(ValueError, match="api_token"):
        base.model_validate(
            base.model_dump(mode="json")
            | {"web": base.web.model_dump(mode="json") | {"host": "0.0.0.0"}}
        )

    configured = base.model_validate(
        base.model_dump(mode="json")
        | {
            "web": base.web.model_dump(mode="json")
            | {"host": "0.0.0.0", "api_token": "a" * 32}
        }
    )
    assert configured.web.api_token == "a" * 32


def test_web_media_path_is_limited_to_configured_roots() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        base = load_settings()
        settings = base.model_copy(
            update={
                "paths": base.paths.model_copy(
                    update={
                        "output_root": root / "outputs",
                        "video_data": root / "videos",
                    }
                )
            }
        )
        (root / "videos").mkdir()
        allowed = root / "videos" / "ad.mp4"
        allowed.write_bytes(b"not a real video")
        outside = root / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        service = WebService(settings)
        try:
            assert service.allowed_media_path(allowed) == allowed.resolve()
            with pytest.raises(ValueError, match="outside configured media roots"):
                service.allowed_media_path(outside)
        finally:
            service.close()


def test_remote_api_requests_are_rejected_without_token() -> None:
    handler = object.__new__(_Handler)
    handler.settings = type(  # type: ignore[assignment]
        "SettingsStub",
        (),
        {"web": type("WebStub", (), {"host": "0.0.0.0", "api_token": "b" * 32})()},
    )()
    handler.headers = {}  # type: ignore[assignment]
    sent: list[tuple[object, object]] = []
    handler._send_json = lambda value, status=200: sent.append((value, status))

    assert handler._require_api_access(["api", "runs"]) is False
    assert sent == [({"error": "Unauthorized"}, 401)]


def test_external_process_can_be_cancelled_as_a_process_group() -> None:
    event = threading.Event()

    def cancel() -> None:
        time.sleep(0.1)
        event.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        with pytest.raises(ProcessCancelled):
            run_process(
                ["/usr/bin/python3", "-c", "import time; time.sleep(10)"],
                timeout=30,
                cancel_event=event,
            )
    finally:
        thread.join(timeout=2)
