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
    assert "下载卖点分析报告（HTML）" in script
    assert "result-action" in script


def test_empty_insights_are_not_presented_as_success() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "系统没有猜测结论" in script
    assert "下载证据账本 JSON" in script


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
