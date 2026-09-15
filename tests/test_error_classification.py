from ad_vista_agent.errors import ModelTimeoutError, PlanError
from ad_vista_agent.schemas import AgentRequest
from ad_vista_agent.web.service import WebService


def test_typed_model_timeout_is_classified_without_message_matching() -> None:
    result = WebService._classify_failure("opaque internal detail", ModelTimeoutError("x"))
    assert result["code"] == "model_timeout"
    assert result["category"] == "model_unavailable"
    assert result["retryable"] is True


def test_typed_plan_error_is_classified_through_cause_chain() -> None:
    cause = PlanError("private model response")
    try:
        raise RuntimeError("wrapper") from cause
    except RuntimeError as error:
        result = WebService._classify_failure(str(error), error)
    assert result["code"] == "output_invalid"
    assert result["category"] == "validation"


def test_legacy_failure_text_remains_supported() -> None:
    result = WebService._classify_failure("model server timeout")
    assert result["code"] == "model_timeout"


def test_planner_falls_back_only_for_open_requests(monkeypatch) -> None:
    import ad_vista_agent.web.service as service_module

    fallback = object()
    monkeypatch.setattr(service_module, "qwen_plan", lambda *args: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(service_module, "rule_plan", lambda request: fallback)
    service = object.__new__(WebService)
    service.settings = None  # type: ignore[assignment]
    assert service._select_plan(AgentRequest(goal="分析卖点"), object()) is fallback
    assert service._select_plan(AgentRequest(goal="分析卖点", deliverables=["report"]), object()) is fallback
