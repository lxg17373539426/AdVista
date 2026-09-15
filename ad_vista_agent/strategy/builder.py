from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.context import build_context_budget, compress_evidence_context
from ad_vista_agent.creative.builder import _load_context
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.insights.builder import _extract_json
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import AgentRequest, MarketingStrategy
from ad_vista_agent.skills import task_skill_prompt
from ad_vista_agent.tools import QwenInsightTool, ToolContext


def build_strategy(
    video_path: Path,
    settings: Settings,
    *,
    force: bool = False,
    request: AgentRequest | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    del force
    started = time.perf_counter()
    ingestion = ingest_video(video_path, settings)
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    output_root = artifact_root or run_dir
    context, allowed_refs, _ = _load_context(run_dir, artifact_root)
    output_dir = output_root / "strategy"
    staging_root = output_root / f".stage_strategy_{uuid.uuid4().hex}"
    staging_dir = staging_root / "strategy"
    staging_dir.mkdir(parents=True, exist_ok=True)
    task = request.model_dump(mode="json") if request is not None else {
        "goal": "制定广告营销策略", "mode": "quick", "deliverables": ["strategy"]
    }
    context["task"] = task
    context, _ = compress_evidence_context(
        context,
        str(task.get("goal") or "营销策略建议"),
        build_context_budget(settings),
    )
    prompt = task_skill_prompt("strategy") + "\n\n" + (
        "你是证据约束的广告营销策略分析师。只能根据输入的已验证洞察和 Evidence Ledger 制定策略。"
        "输出定位、目标人群线索、核心传播主张、传播策略、内容方向、渠道建议、转化建议和待验证假设。"
        "视频事实、广告主张、营销推论和策略假设必须区分；不得把建议写成视频已经证明的事实。"
        "evidence_refs 只能复制输入中真实存在的 speech_* 或 ocr_cluster_* ID，不得构造或引用洞察 ID。"
        "如果没有证据支持，应写入 unsupported_points；所有字符串必须完整结束。只返回符合 Schema 的 JSON。"
    )
    tool = QwenInsightTool(
        settings.insight.python_executable,
        settings.insight.timeout_seconds,
        settings.hardware.cuda_visible_devices,
        runtime=settings.insight.runtime,
        endpoint=settings.insight.endpoint,
        served_model=settings.insight.served_model,
        max_images_per_prompt=settings.insight.max_images_per_prompt,
    )
    inference = tool.run(
        ToolContext(run_id=run_id, run_dir=run_dir),
        {
            "model_path": str(settings.model_path(settings.insight.model)),
            "gpu_memory_utilization": settings.insight.gpu_memory_utilization,
            "max_model_len": settings.insight.max_model_len,
            "max_tokens": settings.insight.max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
            ],
            "output_schema": MarketingStrategy.model_json_schema(),
        },
    )
    strategy = MarketingStrategy.model_validate(_extract_json(inference.text))
    unknown = set(strategy.evidence_refs).difference(allowed_refs)
    if unknown:
        raise ValueError(f"Strategy output cites unknown evidence: {', '.join(sorted(unknown))}")
    store = ArtifactStore(settings.paths.output_root)
    store.write_json(staging_dir / "strategy.json", strategy)
    store.publish_directory(staging_dir, output_dir)
    shutil.rmtree(staging_root, ignore_errors=True)
    return {
        "status": "ok",
        "cache_hit": False,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "artifact_root": str(output_root),
        "strategy": str(output_dir / "strategy.json"),
        "total_seconds": round(time.perf_counter() - started, 6),
    }
