from ad_vista_agent.agent.chat import (
    _answer_is_incomplete,
    _answer_matches_intent,
    _ground_presented_visual_answer,
    _normalize_model_references,
    _normalize_visual_claim_language,
    _sanitize_answer_timing,
)
from ad_vista_agent.agent.planner import rule_plan
from ad_vista_agent.schemas import AgentRequest, ConversationAnswer


def test_model_internal_references_expand_to_real_evidence_ids() -> None:
    answer = ConversationAnswer(
        answer="卖点来自已有洞察和关键画面。",
        evidence_refs=[
            "validated_insights:selling_01",
            "visual_observations:kf_0002_primary",
        ],
        epistemic_status="grounded",
    )
    context = {
        "validated_insights": [
            {"id": "selling_01", "evidence_refs": ["speech_0001", "ocr_cluster_0002"]}
        ],
        "visual_observations": [{"keyframe_id": "kf_0002_primary"}],
    }

    normalized = _normalize_model_references(answer, context)

    assert normalized.evidence_refs == [
        "speech_0001",
        "ocr_cluster_0002",
        "kf_0002_primary",
    ]


def test_rule_plan_preserves_multiple_requested_deliverables() -> None:
    request = AgentRequest(
        goal="生成证据、报告和创意方案",
        deliverables=["evidence", "report", "creative"],
    )

    plan = rule_plan(request)

    assert plan.deliverables == ["evidence", "report", "creative"]
    assert [step.tool for step in plan.steps] == [
        "ingest",
        "timeline",
        "speech",
        "ocr",
        "ledger",
        "insights",
        "report",
        "creative",
    ]


def test_impossible_answer_timestamps_are_removed() -> None:
    answer = ConversationAnswer(
        answer="开头（约21秒）展示外套，最后（约12秒）展示全身搭配。",
        evidence_refs=["kf_0001_primary"],
        epistemic_status="visual",
    )

    sanitized = _sanitize_answer_timing(answer, {"duration_ms": 13600})

    assert "21秒" not in sanitized.answer
    assert "12秒" in sanitized.answer


def test_heading_only_answers_are_incomplete() -> None:
    answer = ConversationAnswer(
        answer="基于视频内容，为您制定以下营销方案：",
        epistemic_status="unknown",
    )

    assert _answer_is_incomplete(answer)


def test_unknown_answer_does_not_get_a_fixed_prefix() -> None:
    from ad_vista_agent.agent.chat import normalize_conversation_answer, validate_conversation_answer

    answer = normalize_conversation_answer(
        ConversationAnswer(
            answer="无法确认视频中的具体价格。",
            epistemic_status="unknown",
            unsupported_points=["当前证据没有明确价格信息。"],
        )
    )

    validate_conversation_answer(answer, set())
    assert answer.answer == "无法确认视频中的具体价格。"
    assert not answer.answer.startswith("当前证据不足")


def test_model_generated_uncertainty_prefix_is_removed() -> None:
    from ad_vista_agent.agent.chat import normalize_conversation_answer

    answer = normalize_conversation_answer(
        ConversationAnswer(
            answer="当前证据不足：无法确认视频中的具体价格。",
            epistemic_status="unknown",
        )
    )

    assert answer.answer == "无法确认视频中的具体价格。"


def test_missing_refs_downgrade_without_rewriting_answer() -> None:
    from ad_vista_agent.agent.chat import normalize_conversation_answer

    answer = normalize_conversation_answer(
        ConversationAnswer(
            answer="视频中展示了一款越野车。",
            epistemic_status="visual",
        )
    )

    assert answer.epistemic_status == "unknown"
    assert answer.answer == "视频中展示了一款越野车。"


def test_visual_summary_can_use_presented_keyframes_when_model_omits_refs() -> None:
    answer = ConversationAnswer(
        answer="当前证据不足：视频开头展示米色外套，中间切换白色马甲，结尾展示棕色外套和格纹围巾，整体按多套秋冬穿搭推进。",
        epistemic_status="unknown",
        unsupported_points=["模型遗漏了引用"],
    )

    grounded = _ground_presented_visual_answer(
        answer,
        "视频开头、中间和结尾分别展示了什么？",
        [{"id": "kf_0001_primary"}, {"id": "kf_0009_primary"}],
    )

    assert grounded.epistemic_status == "visual"
    assert grounded.evidence_refs == ["kf_0001_primary", "kf_0009_primary"]
    assert not grounded.answer.startswith("当前证据不足")


def test_visual_summary_can_use_numeric_time_range() -> None:
    answer = ConversationAnswer(
        answer="当前证据不足：视频的前五秒中，一位呈女性化风格的模特站在玩具货架前，画面出现开场文字。",
        epistemic_status="unknown",
    )

    grounded = _ground_presented_visual_answer(
        answer,
        "视频前五秒讲了什么？",
        [{"id": "kf_0001_primary"}],
    )

    assert grounded.epistemic_status == "visual"
    assert grounded.evidence_refs == ["kf_0001_primary"]
    assert not grounded.answer.startswith("当前证据不足")


def test_price_unknown_is_not_promoted_to_visual() -> None:
    answer = ConversationAnswer(
        answer="当前证据不足：没有发现价格或优惠信息。",
        epistemic_status="unknown",
    )

    grounded = _ground_presented_visual_answer(
        answer,
        "视频里有没有价格优惠？",
        [{"id": "kf_0001_primary"}],
    )

    assert grounded.epistemic_status == "unknown"
    assert grounded.evidence_refs == []


def test_marketing_answer_requires_layered_sections() -> None:
    incomplete = ConversationAnswer(
        answer="这里是一份营销方案，包括渠道和折扣建议。",
        epistemic_status="unknown",
    )
    complete = ConversationAnswer(
        answer="视频事实：展示多套外套。分析推断：强调百搭。营销假设：建议测试限时优惠。",
        epistemic_status="visual",
        evidence_refs=["kf_0001_primary"],
    )

    assert not _answer_matches_intent(incomplete, "marketing")
    assert _answer_matches_intent(complete, "marketing")


def test_creative_answer_requires_core_deliverables() -> None:
    answer = ConversationAnswer(
        answer="视频事实：展示多套外套。创意假设：三个 Hook。15秒脚本。分镜。A/B 版本。",
        epistemic_status="visual",
        evidence_refs=["kf_0001_primary"],
    )

    assert _answer_matches_intent(answer, "creative")


def test_visual_identity_and_material_claims_are_softened() -> None:
    answer = ConversationAnswer(
        answer="一位女性模特展示羊羔毛外套。",
        epistemic_status="visual",
        evidence_refs=["kf_0001_primary"],
    )

    normalized = _normalize_visual_claim_language(answer)

    assert "呈女性化风格" in normalized.answer
    assert "羊羔毛外观的毛绒外套" in normalized.answer
