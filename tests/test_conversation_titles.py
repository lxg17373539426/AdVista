from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest
from pydantic import SecretStr

from ad_vista_agent.config import DatabaseConfig, load_settings
from ad_vista_agent.database import Base, Database, UserRepository
from ad_vista_agent.runtime.process import ProcessCancelled
from ad_vista_agent.web import titles
from ad_vista_agent.web.app import create_server
from ad_vista_agent.web.service import WebService


@pytest.fixture(params=["database", "legacy_sqlite"])
def service(request, tmp_path):
    base = load_settings()
    config = DatabaseConfig()
    if request.param == "database":
        config = DatabaseConfig(url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'service.db'}"))
        database = Database(config)
        Base.metadata.create_all(database.engine)
        database.close()
    settings = base.model_copy(update={
        "paths": base.paths.model_copy(update={"output_root": tmp_path / "outputs"}),
        "database": config,
    })
    current = WebService(settings)
    try:
        yield current
    finally:
        current.close()


def _new_chat(service, session_id="web_title", *, owner=None):
    store = service._conversation_store(user_id=owner)
    if service.request_repository is not None:
        service.request_repository.ensure_conversation(
            session_id, user_id=owner or "system", run_id=f"general_{session_id}",
            conversation_type="general_chat",
        )
    store.create_session(session_id=session_id, run_id=f"general_{session_id}", source_path=Path("."), goal="普通对话")
    store.add_message(session_id, "user", "请帮我为运动饮料制定营销策略。重点面向年轻人。", [])
    store.add_message(session_id, "assistant", "可以围绕运动场景和目标受众设计传播。", [])
    return store


def test_generated_title_uses_conversation_and_is_saved_once(service, monkeypatch):
    store = _new_chat(service)
    seen = []

    def summarize(settings, messages, **kwargs):
        seen.append(messages)
        return "运动饮料营销策略"

    monkeypatch.setattr("ad_vista_agent.web.service.generate_title", summarize)
    first = service.generate_conversation_title("web_title")
    assert first["title"] == "运动饮料营销策略"
    assert seen[0][0]["content"].startswith("请帮我")
    assert seen[0][1]["role"] == "assistant"
    assert store.session("web_title")["title"] == first["title"]
    assert service.generate_conversation_title("web_title") == first
    assert len(seen) == 1
    assert service.conversations()[0]["title"] == first["title"]
    assert service.general_messages("web_title")["session"]["title"] == first["title"]


def test_manual_rename_wins_race_and_survives_further_chat(service, monkeypatch):
    store = _new_chat(service)

    def delayed_summary(*args, **kwargs):
        service.rename_conversation("web_title", "我的饮料项目 v1.2")
        return "不应覆盖人工名称"

    monkeypatch.setattr("ad_vista_agent.web.service.generate_title", delayed_summary)
    assert service.generate_conversation_title("web_title")["title"] == "我的饮料项目 v1.2"
    monkeypatch.setattr("ad_vista_agent.web.service.cancellable_urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps({"choices": [{"message": {"content": "后续回答"}}]}).encode()))
    service.general_chat("继续讨论渠道", "web_title")
    assert store.session("web_title")["title"] == "我的饮料项目 v1.2"
    assert service.generate_conversation_title("web_title")["title"] == "我的饮料项目 v1.2"


@pytest.mark.parametrize("title", [None, 123, "  \n ", "x" * 81, "含\x00控制符"])
def test_invalid_manual_title_keeps_original(service, title):
    store = _new_chat(service)
    service.rename_conversation("web_title", "保留标题")
    with pytest.raises(ValueError):
        service.rename_conversation("web_title", title)
    assert store.session("web_title")["title"] == "保留标题"


def test_other_user_cannot_list_rename_or_generate_title(service, monkeypatch):
    if service.database is None:
        pytest.skip("Legacy SQLite is a single-user store")
    owner, _ = UserRepository(service.database).create_api_user("title-owner")
    other, _ = UserRepository(service.database).create_api_user("title-other")
    store = _new_chat(service, owner=owner.user_id)
    monkeypatch.setattr("ad_vista_agent.web.service.generate_title", lambda *args, **kwargs: pytest.fail("unauthorized summary call"))
    assert service.conversations(user_id=other.user_id) == []
    with pytest.raises(PermissionError):
        service.rename_conversation("web_title", "非法名称", user_id=other.user_id)
    with pytest.raises(PermissionError):
        service.generate_conversation_title("web_title", user_id=other.user_id)
    assert store.session("web_title")["title"] is None


def test_video_rename_uses_real_legacy_session_id_and_preserves_goal(service, monkeypatch, tmp_path):
    state = SimpleNamespace(
        session_id="conversation_legacy_video", run_id="ingest_old", execution_id="exec_old",
        source_path=tmp_path / "video.mp4", request=SimpleNamespace(goal="分析广告的卖点"),
    )
    monkeypatch.setattr("ad_vista_agent.web.service.load_agent_session", lambda *args: (tmp_path, tmp_path, state))
    monkeypatch.setattr(service, "artifact_root", lambda *args: tmp_path)
    assert service.rename_run("exec_old", "春季广告分析")["session_id"] == state.session_id
    monkeypatch.setattr("ad_vista_agent.web.service.generate_title", lambda *args, **kwargs: pytest.fail("manual video title replaced"))
    assert service.generate_run_title("exec_old")["title"] == "春季广告分析"
    assert state.request.goal == "分析广告的卖点"
    assert service._saved_video_title(state.session_id) == "春季广告分析"
    assert service.conversations() == []


def test_title_model_structured_output_and_bounded_context(monkeypatch):
    settings = load_settings().model_copy(update={"insight": load_settings().insight.model_copy(update={"runtime": "openai"})})
    requests = []

    def respond(request, **kwargs):
        requests.append((json.loads(request.data), kwargs))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"title":"运动饮料营销策略"}'}}]}).encode())

    monkeypatch.setattr(titles, "cancellable_urlopen", respond)
    answer = titles.generate_title(settings, [{"role": "user", "content": "x" * 5000}])
    assert answer == "运动饮料营销策略"
    assert requests[0][1]["timeout"] <= 10
    assert len(json.loads(requests[0][0]["messages"][1]["content"])[0]["content"]) == 1500
    assert "structured_outputs" in requests[0][0]


@pytest.mark.parametrize("response", [b"invalid-json", b'{"choices":[]}', json.dumps({"choices": [{"message": {"content": '{"title":""}'}}]}).encode()])
def test_bad_title_model_reply_uses_content_fallback(monkeypatch, response):
    settings = load_settings().model_copy(update={"insight": load_settings().insight.model_copy(update={"runtime": "openai"})})
    monkeypatch.setattr(titles, "cancellable_urlopen", lambda *args, **kwargs: io.BytesIO(response))
    assert titles.generate_title(settings, [{"role": "user", "content": "请帮我总结运动饮料的卖点。第二句话"}]) == "总结运动饮料的卖点"


def test_title_model_failure_does_not_break_completed_conversation(monkeypatch):
    settings = load_settings().model_copy(update={"insight": load_settings().insight.model_copy(update={"runtime": "openai"})})

    def unavailable(*args, **kwargs):
        raise URLError("offline")

    monkeypatch.setattr(titles, "cancellable_urlopen", unavailable)
    assert titles.generate_title(settings, [{"role": "user", "content": "请分析广告受众"}]) == "分析广告受众"
    event = threading.Event()
    event.set()
    with pytest.raises(ProcessCancelled):
        titles.generate_title(settings, [], cancel_event=event)


def test_http_title_routes_and_validation(service, monkeypatch):
    _new_chat(service)
    monkeypatch.setattr("ad_vista_agent.web.app.WebService", lambda settings: service)
    monkeypatch.setattr("ad_vista_agent.web.service.generate_title", lambda *args, **kwargs: "运动饮料营销策略")
    settings = service.settings.model_copy(update={"web": service.settings.web.model_copy(update={"port": 0})})
    server = create_server(settings)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body):
        request = Request(base_url + path, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    try:
        assert post("/api/conversations/web_title/title/generate", {})["title"] == "运动饮料营销策略"
        assert post("/api/conversations/web_title/title", {"title": "手动名称 <v1.2>"})["title"] == "手动名称 <v1.2>"
        with urlopen(base_url + "/api/conversations", timeout=5) as response:
            assert json.load(response)["conversations"][0]["title"] == "手动名称 <v1.2>"
        assert post("/api/conversations/web_title/title/generate", {})["title"] == "手动名称 <v1.2>"
        with pytest.raises(URLError) as error:
            post("/api/conversations/web_title/title", {"title": None})
        assert error.value.code == 400
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
