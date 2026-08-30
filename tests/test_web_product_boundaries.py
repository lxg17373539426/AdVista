from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "ad_vista_agent" / "web" / "static"


def test_web_tasks_have_clear_single_purpose_boundaries() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "广告洞察" not in html
    assert "完整报告" not in html
    assert "查看结果" not in html
    assert "卖点分析报告" in html
    assert "证据提取" in html
    assert 'type="radio"' in html


def test_generated_downloads_are_rendered_in_conversation() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "resultActions" in script
    assert "查看卖点分析报告" in script
    assert "查看证据提取文档" in script
    assert "result-action" in script


def test_empty_insights_are_not_presented_as_success() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "系统没有猜测结论" in script
    assert "evidence-html" in script


def test_workspace_uses_codex_inspired_neutral_palette() -> None:
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert "--rail: #efefeb" in styles
    assert "--canvas: #f7f7f5" in styles
    assert "--accent: #d97745" in styles


def test_switching_runs_isolated_from_stale_requests() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'if(loadMessages){$("#messages").innerHTML=""' in script
    assert "if(token===state.operation)" in script


def test_empty_report_download_is_not_offered() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'const available=empty?generated.filter(item=>item==="evidence"):generated' in script
    assert 'selected.has("risk_audit")&&!includeEvidence' in script


def test_documents_are_single_intent_and_evidence_has_real_exports() -> None:
    from ad_vista_agent.agent.planner import rule_plan
    from ad_vista_agent.schemas import AgentRequest

    plan = rule_plan(AgentRequest(goal="请提取证据", deliverables=[]))
    assert plan.deliverables == ["evidence"]
    assert rule_plan(AgentRequest(goal="请分析卖点", deliverables=[])).deliverables == ["insights"]
    assert rule_plan(AgentRequest(goal="请生成报告", deliverables=[])).deliverables == ["report"]

    app = (STATIC.parents[1] / "web" / "app.py").read_text(encoding="utf-8")
    evidence = (STATIC.parents[1] / "reports" / "evidence.py").read_text(encoding="utf-8")
    assert '"evidence-html"' in app
    assert 'evidence.html' in evidence
    assert 'evidence.md' in evidence


def test_visual_insight_service_allows_keyframes() -> None:
    script = (STATIC.parents[2] / "scripts" / "start_qwen_server.sh").read_text(encoding="utf-8")

    assert 'QWEN_MAX_IMAGES_PER_PROMPT:-8' in script


def test_all_keyframes_are_analyzed_in_small_batches() -> None:
    builder = (STATIC.parents[1] / "insights" / "builder.py").read_text(encoding="utf-8")

    assert "batch_size = settings.insight.max_images_per_prompt" in builder
    assert "batch = keyframes[start : start + batch_size]" in builder
    assert "_visual_batch_starts(len(keyframes), batch_size, overlap)" in builder
    assert '"visual_keyframe_count": len(keyframes)' in builder
    assert '"allowed_keyframe_ids": [item.keyframe_id for item in keyframes]' in builder


def test_video_chat_respects_image_limit_and_ad_vista_identity() -> None:
    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")
    tool = (STATIC.parents[1] / "tools" / "qwen.py").read_text(encoding="utf-8")
    service = (STATIC.parents[1] / "web" / "service.py").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "MAX_IMAGES_PER_PROMPT = 8" in chat
    assert "limit: int = MAX_IMAGES_PER_PROMPT" in chat
    assert "if image_count > self.max_images_per_prompt" in tool
    assert "messages = list(arguments.get(\"messages\") or [])" in tool
    assert "你的名称是 AdVista" in chat
    assert "你的名称是 AdVista" in service
    assert "AdInsight-RL 自动识别卖点" not in script


def test_marketing_requests_are_not_routed_to_generic_report() -> None:
    from ad_vista_agent.agent.chat import _is_marketing_request, _is_report_request

    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")

    assert "def _is_marketing_request" in chat
    assert 'elif _is_marketing_request(value):' in chat
    assert 'intent="marketing"' in chat
    assert 'output_dir / "marketing.html"' in chat
    assert '"marketing-html"' in (STATIC.parents[1] / "web" / "app.py").read_text(encoding="utf-8")
    assert _is_marketing_request("我需要的是营销报告")
    assert _is_marketing_request("请给我一份产品推广方案")
    assert _is_report_request("我需要的是营销报告")


def test_general_chat_persists_and_restores_session() -> None:
    service = (STATIC.parents[1] / "web" / "service.py").read_text(encoding="utf-8")
    app = (STATIC.parents[1] / "web" / "app.py").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "ConversationStore" in service
    assert "def general_messages" in service
    assert 'parts == ["api", "chat", "messages"]' in app
    assert "advista.general.session" in script
    assert "event.session_id&&!state.run" in script


def test_chat_evidence_is_not_rendered_to_users() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "function renderCitations(){return}" in script


def test_video_conversation_seeds_upload_and_completion_messages() -> None:
    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")

    assert "def _seed_conversation" in chat
    assert "state.request.goal" in chat


def test_visual_observations_are_persisted_for_video_chat() -> None:
    builder = (STATIC.parents[1] / "insights" / "builder.py").read_text(encoding="utf-8")
    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")

    assert 'visual_observations_path = output_dir / "visual_observations.json"' in builder
    assert '"visual_observations": "insights/visual_observations.json"' in builder
    assert '"visual_observations": visual_observations' in chat
    assert "observed_keyframe_refs" in chat


def test_relevant_keyframes_use_visual_text_and_temporal_fallback() -> None:
    from ad_vista_agent.agent.chat import _relevant_keyframes

    frames = [
        {"id": "kf_1", "timestamp_ms": 0, "artifact_path": "1.jpg"},
        {"id": "kf_2", "timestamp_ms": 1000, "artifact_path": "2.jpg"},
        {"id": "kf_3", "timestamp_ms": 2000, "artifact_path": "3.jpg"},
        {"id": "kf_4", "timestamp_ms": 3000, "artifact_path": "4.jpg"},
    ]
    context = {
        "visual_keyframes": frames,
        "visual_observations": [
            {"keyframe_id": "kf_2", "description": "人物打开蓝色瓶盖"},
            {"keyframe_id": "kf_3", "visible_text": "清爽控油"},
        ],
    }

    assert [item["id"] for item in _relevant_keyframes("瓶盖是什么颜色", context, 2)] == ["kf_2", "kf_1"]
    assert [item["id"] for item in _relevant_keyframes("完整介绍一下", context, 2)] == ["kf_1", "kf_4"]
    assert _relevant_keyframes("最后展示了什么", context, 2)[0]["id"] == "kf_4"
    assert _relevant_keyframes("完整介绍一下", context) == frames


def test_planner_response_mode_controls_uploaded_video_result() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    planner = (STATIC.parents[1] / "agent" / "planner.py").read_text(encoding="utf-8")
    assert "response_mode" in planner
    assert 'value.response_mode==="answer"' in script
    assert "def _is_video_question" not in (STATIC.parents[1] / "web" / "service.py").read_text(encoding="utf-8")
    assert "response_mode=answer" in planner


def test_answer_plan_has_no_artifact_deliverables() -> None:
    from ad_vista_agent.agent.planner import PlannerDecision, compile_decision
    from ad_vista_agent.schemas import AgentRequest

    request = AgentRequest(goal="这个模特是男生还是女生呢？")
    decision = PlannerDecision(
        goal=request.goal,
        selected_tools=["ingest", "timeline", "speech", "ocr", "ledger"],
        deliverables=["insights", "report"],
        response_mode="answer",
    )

    plan = compile_decision(decision, request)
    assert plan.response_mode == "answer"
    assert plan.deliverables == []
    assert [step.tool for step in plan.steps] == ["ingest", "timeline", "speech", "ocr", "ledger"]


def test_answer_plan_cannot_run_artifact_tools() -> None:
    from ad_vista_agent.agent.planner import PlannerDecision, compile_decision
    from ad_vista_agent.schemas import AgentRequest

    request = AgentRequest(goal="这个视频的模特是男的还是女的")
    decision = PlannerDecision(
        goal=request.goal,
        selected_tools=["ingest", "timeline", "speech", "ocr", "ledger", "insights", "report"],
        deliverables=["insights", "report"],
        response_mode="answer",
    )

    plan = compile_decision(decision, request)
    assert plan.response_mode == "answer"
    assert plan.deliverables == []
    assert [step.tool for step in plan.steps] == ["ingest", "timeline", "speech", "ocr", "ledger"]


def test_video_chat_can_use_ledger_without_insight_artifact() -> None:
    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")
    assert "if analysis_path.is_file()" in chat
    assert "analysis.asset_id if analysis is not None" in chat


def test_video_chat_always_receives_selected_keyframes() -> None:
    chat = (STATIC.parents[1] / "agent" / "chat.py").read_text(encoding="utf-8")
    assert "def _needs_visual_context" not in chat
    assert "include_images=True" in chat


def test_blank_conversation_references_are_removed_before_validation() -> None:
    from ad_vista_agent.agent.chat import normalize_conversation_answer, validate_conversation_answer
    from ad_vista_agent.schemas import ConversationAnswer

    answer = normalize_conversation_answer(
        ConversationAnswer(
            answer="画面中有一位人物。",
            evidence_refs=["", "  ", "kf_0001_primary", "kf_0001_primary"],
            epistemic_status="visual",
        )
    )

    assert answer.evidence_refs == ["kf_0001_primary"]
    validate_conversation_answer(answer, {"kf_0001_primary"})


def test_visual_batches_overlap_without_duplicate_only_batch() -> None:
    from ad_vista_agent.insights.builder import _visual_batch_starts

    assert _visual_batch_starts(0, 8, 1) == []
    assert _visual_batch_starts(1, 8, 1) == [0]
    assert _visual_batch_starts(8, 8, 1) == [0]
    assert _visual_batch_starts(9, 8, 1) == [0, 7]
    assert _visual_batch_starts(15, 8, 1) == [0, 7]
    assert _visual_batch_starts(16, 8, 1) == [0, 7, 14]


def test_qwen_runtime_defaults_are_consistent() -> None:
    root = STATIC.parents[2]
    server = (root / "scripts" / "start_qwen_server.sh").read_text(encoding="utf-8")
    service = (root / "scripts" / "start_qwen_service.sh").read_text(encoding="utf-8")
    config = (root / "configs" / "default.yaml").read_text(encoding="utf-8")

    assert 'QWEN_MAX_MODEL_LEN:-262144' in server
    assert 'QWEN_MAX_IMAGES_PER_PROMPT:-8' in server
    assert 'QWEN_MAX_NUM_SEQS:-2' in server
    assert 'QWEN_MAX_NUM_SEQS="2"' in service
    assert 'QWEN_MAX_MODEL_LEN="262144"' in service
    assert "max_model_len: 262144" in config
    assert "max_images_per_prompt: 8" in config
    assert "visual_batch_overlap: 1" in config
