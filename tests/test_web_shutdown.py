from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_http_cancellation import (
    model_server as model_server,  # noqa: PLC0414 — 复用隔离 HTTP fixture。
)

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database
from ad_vista_agent.runtime.process import ProcessCancelled
from ad_vista_agent.tools import ToolContext
from ad_vista_agent.web.service import WebService


@pytest.fixture
def service(tmp_path, monkeypatch):
    base = load_settings()
    settings = base.model_copy(
        update={
            "paths": base.paths.model_copy(
                update={"output_root": tmp_path / "outputs", "video_data": tmp_path}
            ),
            "database": DatabaseConfig(
                url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'service.db'}")
            ),
            "web": base.web.model_copy(update={"analysis_workers": 1}),
        }
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.close()
    instance = WebService(settings)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"isolated test")
    monkeypatch.setattr(instance, "validate_uploaded_video", lambda _path: None)
    yield instance, video
    instance.close()


def test_cancel_queued_job_updates_database_and_json(service):
    instance, video = service
    release = threading.Event()
    blocker = instance.executor.submit(release.wait)
    try:
        job = instance.submit(video, "test", [])
        result = instance.cancel_job(job["job_id"])
        assert result["status"] == "cancelled"
        assert (
            instance.store.read_json(instance._job_path(job["job_id"]))["status"]
            == "cancelled"
        )
        assert (
            instance.request_repository.get_request(job["request_id"]).status
            == "cancelled"
        )
        assert (
            instance.execution_repository.get(job["execution_id"]).status == "cancelled"
        )
    finally:
        release.set()
        blocker.result(timeout=5)


def test_close_signals_running_job_and_waits_before_database_disposal(
    service, monkeypatch
):
    instance, video = service
    entered = threading.Event()
    release = threading.Event()
    closed_database = threading.Event()
    original_close = instance.database.close
    closed_count = []

    def close_database():
        closed_count.append(True)
        closed_database.set()
        original_close()

    monkeypatch.setattr(instance.database, "close", close_database)

    def plan(*_args):
        entered.set()
        release.wait(timeout=5)
        raise ProcessCancelled("isolated planning cancellation")

    monkeypatch.setattr(instance, "_select_plan", plan)
    job = instance.submit(video, "test", [])
    assert entered.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(instance.close)
        try:
            assert instance.job_cancel_events[job["job_id"]].wait(timeout=1)
            assert not closed_database.is_set()
            assert not closing.done()
        finally:
            release.set()
            closing.result(timeout=5)
            instance.executor.shutdown(wait=True)
    assert closed_database.is_set()
    assert instance.jobs[job["job_id"]]["status"] == "cancelled"
    instance.close()
    assert len(closed_count) == 1


def test_close_cancels_queued_jobs_and_rejects_new_calls(service, monkeypatch):
    instance, video = service
    release = threading.Event()
    blocker = instance.executor.submit(release.wait)
    job = instance.submit(video, "test", [])
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(instance.close)
        try:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with instance.guard:
                    if instance.jobs[job["job_id"]]["status"] == "cancelled":
                        break
                time.sleep(0.01)
            assert instance.jobs[job["job_id"]]["status"] == "cancelled"
            assert (
                instance.request_repository.get_request(job["request_id"]).status
                == "cancelled"
            )
            assert (
                instance.execution_repository.get(job["execution_id"]).status
                == "cancelled"
            )
        finally:
            release.set()
            blocker.result(timeout=5)
            closing.result(timeout=5)
    with pytest.raises(RuntimeError, match="clos"):
        instance.submit(video, "test", [])
    with pytest.raises(RuntimeError, match="clos"):
        instance.general_chat("test")
    with pytest.raises(RuntimeError, match="clos"):
        instance.chat("missing", "test")


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_submit_registers_future_before_cancel_or_close_can_see_job(
    service, monkeypatch, action
):
    instance, video = service
    release = threading.Event()
    attempted = threading.Event()
    errors = []
    threads = []
    original_submit = instance.executor.submit

    def planning(*_args):
        release.wait(timeout=5)
        raise ProcessCancelled("isolated cancellation")

    monkeypatch.setattr(instance, "_select_plan", planning)

    def concurrent_action():
        attempted.set()
        try:
            if action == "close":
                instance.close()
            else:
                instance.cancel_job(next(iter(instance.jobs)))
        except (KeyError, RuntimeError) as exc:
            errors.append(exc)
        finally:
            release.set()

    def pause_submit(*args, **kwargs):
        thread = threading.Thread(target=concurrent_action)
        threads.append(thread)
        thread.start()
        assert attempted.wait(timeout=2)
        time.sleep(0.05)
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(instance.executor, "submit", pause_submit)
    try:
        instance.submit(video, "test", [])
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        instance.executor.shutdown(wait=True)
    assert errors == []


def test_planning_cancellation_is_cancelled_not_failed(service, monkeypatch):
    instance, video = service

    def planning(*_args):
        raise ProcessCancelled("isolated cancellation")

    monkeypatch.setattr(instance, "_select_plan", planning)
    job = instance.submit(video, "test", [])
    instance.job_futures[job["job_id"]].result(timeout=5)
    assert instance.jobs[job["job_id"]]["status"] == "cancelled"
    assert (
        instance.request_repository.get_request(job["request_id"]).status == "cancelled"
    )
    assert instance.execution_repository.get(job["execution_id"]).status == "cancelled"


@pytest.mark.parametrize("streaming", [False, True])
def test_close_cancels_and_joins_active_general_model_chat(
    service, monkeypatch, streaming
):
    from contextlib import contextmanager

    from sqlalchemy import select

    from ad_vista_agent.database.models import Request

    instance, _ = service
    entered = threading.Event()
    database_closed = threading.Event()
    original_close = instance.database.close
    statuses_at_close = []

    def close_database():
        with instance.database.session() as session:
            statuses_at_close.extend(session.scalars(select(Request.status)))
        database_closed.set()
        original_close()

    monkeypatch.setattr(instance.database, "close", close_database)

    @contextmanager
    def wait_for_cancellation(_call, *, timeout, cancel_event=None):
        assert cancel_event is not None
        assert (
            ToolContext(run_id="test", run_dir=Path(".")).cancel_event is cancel_event
        )
        entered.set()
        assert cancel_event.wait(timeout=5)
        assert not database_closed.is_set()
        raise ProcessCancelled("isolated model cancellation")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "ad_vista_agent.web.service.cancellable_urlopen", wait_for_cancellation
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        chat = pool.submit(
            instance.general_chat,
            "test",
            on_delta=(lambda _text: None) if streaming else None,
        )
        assert entered.wait(timeout=5)
        closing = pool.submit(instance.close)
        with pytest.raises(ProcessCancelled):
            chat.result(timeout=5)
        closing.result(timeout=5)
    assert database_closed.is_set()
    assert statuses_at_close == ["cancelled"]


def test_close_cancels_and_joins_active_video_chat(service, monkeypatch):
    instance, _ = service
    entered = threading.Event()
    release = threading.Event()
    database_closed = threading.Event()
    original_close = instance.database.close

    def close_database():
        database_closed.set()
        original_close()

    monkeypatch.setattr(instance.database, "close", close_database)
    monkeypatch.setattr(instance, "_asset_run_id", lambda _identifier: "asset")

    def chat(*_args, **_kwargs):
        event = ToolContext(run_id="test", run_dir=Path(".")).cancel_event
        assert event is not None
        entered.set()
        assert event.wait(timeout=5)
        release.wait(timeout=5)
        assert not database_closed.is_set()
        raise ProcessCancelled("isolated video chat cancellation")

    monkeypatch.setattr(instance, "_video_chat", chat)
    with ThreadPoolExecutor(max_workers=3) as pool:
        active_chat = pool.submit(instance.chat, "video", "test")
        assert entered.wait(timeout=5)
        closing = pool.submit(instance.close)
        second_close = pool.submit(instance.close)
        try:
            assert instance._shutdown_event.wait(timeout=5)
            assert not closing.done()
            assert not second_close.done()
        finally:
            release.set()
        with pytest.raises(ProcessCancelled):
            active_chat.result(timeout=5)
        closing.result(timeout=5)
        second_close.result(timeout=5)
    assert database_closed.is_set()


def test_executor_rejection_leaves_terminal_job_and_request(service, monkeypatch):
    instance, video = service

    def reject(*_args, **_kwargs):
        raise RuntimeError("isolated broken executor")

    monkeypatch.setattr(instance.executor, "submit", reject)
    with pytest.raises(RuntimeError, match="broken executor"):
        instance.submit(video, "test", [])
    job = next(iter(instance.jobs.values()))
    assert job["status"] == "failed"
    assert instance.execution_repository.get(job["execution_id"]).status == "failed"
    assert instance.request_repository.get_request(job["request_id"]).status == "failed"
    assert (
        instance.store.read_json(instance._job_path(job["job_id"]))["status"]
        == "failed"
    )


def test_close_still_drains_workers_when_cancellation_persistence_fails(
    service, monkeypatch
):
    instance, video = service
    release = threading.Event()
    blocker = instance.executor.submit(release.wait)
    instance.submit(video, "test", [])
    closed_database = threading.Event()
    original_close = instance.database.close

    def close_database():
        closed_database.set()
        original_close()

    monkeypatch.setattr(instance.database, "close", close_database)

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("isolated database write failure")

    monkeypatch.setattr(instance, "_update_db_execution", fail_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(instance.close)
        try:
            assert instance._shutdown_event.wait(timeout=5)
            assert not closed_database.is_set()
        finally:
            release.set()
            blocker.result(timeout=5)
        with pytest.raises(RuntimeError, match="could not be persisted"):
            closing.result(timeout=5)
    assert closed_database.is_set()
    instance.close()


@pytest.mark.parametrize("operation", ["general", "stream", "planning"])
def test_shutdown_closes_real_stalled_model_http_connection(
    service, model_server, monkeypatch, operation
):
    from sqlalchemy import select

    from ad_vista_agent.database.models import Request

    instance, video = service
    endpoint, ready, disconnected, requests = model_server
    instance.settings.insight.endpoint = endpoint + "/v1"
    instance.settings.insight.timeout_seconds = 20
    statuses_at_close = []
    original_close = instance.database.close

    def close_database():
        with instance.database.session() as session:
            statuses_at_close.extend(session.scalars(select(Request.status)))
        original_close()

    monkeypatch.setattr(instance.database, "close", close_database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        if operation == "planning":
            job = instance.submit(video, "理解视频内容", [])
            chat = None
        else:
            chat = pool.submit(
                instance.general_chat,
                "test",
                on_delta=(lambda _text: None) if operation == "stream" else None,
            )
        assert ready.wait(timeout=3)
        started = time.monotonic()
        closing = pool.submit(instance.close)
        if chat is not None:
            with pytest.raises(ProcessCancelled):
                chat.result(timeout=3)
        closing.result(timeout=3)
    assert time.monotonic() - started < 1.5
    assert disconnected.wait(timeout=1)
    assert len(requests) == 1
    assert statuses_at_close == ["cancelled"]
    if operation == "planning":
        assert instance.jobs[job["job_id"]]["status"] == "cancelled"
