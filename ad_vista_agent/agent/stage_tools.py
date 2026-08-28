from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ad_vista_agent.config import Settings
from ad_vista_agent.creative.builder import build_creative
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.insights.builder import build_insights
from ad_vista_agent.ledger.builder import build_ledger
from ad_vista_agent.ocr.builder import build_ocr
from ad_vista_agent.reports.builder import build_report
from ad_vista_agent.speech.builder import build_speech
from ad_vista_agent.timeline.builder import build_timeline
from ad_vista_agent.schemas import AgentRequest
from ad_vista_agent.tools import Tool, ToolContext, ToolRegistry


Builder = Callable[[Path, Settings, bool, AgentRequest | None, Path | None], dict[str, Any]]


class StageTool(Tool):
    input_schema = {
        "type": "object",
        "properties": {"force": {"type": "boolean"}},
        "additionalProperties": False,
    }
    output_schema = {"type": "object", "required": ["status", "run_id", "run_dir"]}

    def __init__(
        self,
        name: str,
        description: str,
        builder: Builder,
        settings: Settings,
        *,
        requires_gpu: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.builder = builder
        self.settings = settings
        self.requires_gpu = requires_gpu

    def run(self, context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if context.source_path is None:
            raise ValueError(f"Tool {self.name} requires a source video")
        artifact_root = (
            context.execution_dir / "artifacts" if context.execution_dir is not None else None
        )
        result = self.builder(
            context.source_path,
            self.settings,
            bool(arguments.get("force", False)),
            context.request,
            artifact_root,
        )
        if result.get("status") != "ok":
            raise RuntimeError(f"Tool {self.name} returned non-ok status")
        return result


def _ingest(
    path: Path,
    settings: Settings,
    force: bool,
    request: AgentRequest | None,
    artifact_root: Path | None,
) -> dict[str, Any]:
    del force, request, artifact_root
    return ingest_video(path, settings)


def _timeline(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    del request, artifact_root
    return build_timeline(path, settings, force=force)


def _speech(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    del request, artifact_root
    return build_speech(path, settings, force=force)


def _ocr(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    del request, artifact_root
    return build_ocr(path, settings, force=force)


def _ledger(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    del request, artifact_root
    return build_ledger(path, settings, force=force)


def _insights(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    return build_insights(path, settings, force=force, request=request, artifact_root=artifact_root)


def _report(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    return build_report(path, settings, force=force, request=request, artifact_root=artifact_root)


def _creative(path: Path, settings: Settings, force: bool, request: AgentRequest | None, artifact_root: Path | None) -> dict[str, Any]:
    return build_creative(path, settings, force=force, request=request, artifact_root=artifact_root)


def build_agent_registry(settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        StageTool("ingest", "校验并登记广告视频", _ingest, settings),
        StageTool("timeline", "切分镜头并生成关键帧", _timeline, settings),
        StageTool("speech", "生成带时间戳的语音证据", _speech, settings, requires_gpu=True),
        StageTool("ocr", "生成带区域的画面文字证据", _ocr, settings, requires_gpu=True),
        StageTool("ledger", "构建统一 Evidence Ledger", _ledger, settings),
        StageTool("insights", "生成证据约束的营销洞察", _insights, settings, requires_gpu=True),
        StageTool("report", "审计洞察并生成报告", _report, settings),
        StageTool("creative", "基于证据生成 Hook、脚本、分镜和 A/B 方案", _creative, settings, requires_gpu=True),
    ):
        registry.register(tool)
    return registry
