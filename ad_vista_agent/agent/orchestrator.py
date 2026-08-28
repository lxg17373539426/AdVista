from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ad_vista_agent.config import Settings
from ad_vista_agent.ingestion import ingest_video
from ad_vista_agent.insights.builder import build_insights
from ad_vista_agent.ledger.builder import build_ledger
from ad_vista_agent.ocr.builder import build_ocr
from ad_vista_agent.reports.builder import build_report
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import PipelineStageState, PipelineState, PipelineStatus, StepStatus
from ad_vista_agent.speech.builder import build_speech
from ad_vista_agent.timeline.builder import build_timeline


PIPELINE_VERSION = "1"
PIPELINE_STAGE_NAMES = ("ingest", "timeline", "speech", "ocr", "ledger", "insights", "report")
StageRunner = Callable[[Path, Settings, bool], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ingest(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    del force
    return ingest_video(video, settings)


def _timeline(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_timeline(video, settings, force=force)


def _speech(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_speech(video, settings, force=force)


def _ocr(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_ocr(video, settings, force=force)


def _ledger(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_ledger(video, settings, force=force)


def _insights(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_insights(video, settings, force=force)


def _report(video: Path, settings: Settings, force: bool) -> dict[str, Any]:
    return build_report(video, settings, force=force)


DEFAULT_STAGE_RUNNERS: dict[str, StageRunner] = {
    "ingest": _ingest,
    "timeline": _timeline,
    "speech": _speech,
    "ocr": _ocr,
    "ledger": _ledger,
    "insights": _insights,
    "report": _report,
}


def _state_path(run_dir: Path) -> Path:
    return run_dir / "orchestration" / "state.json"


def _write_state(store: ArtifactStore, run_dir: Path, state: PipelineState) -> None:
    state.updated_at = _now()
    store.write_json(_state_path(run_dir), state)


def _load_state(store: ArtifactStore, run_id: str) -> tuple[Path, PipelineState]:
    run_dir = store.run_dir(run_id)
    path = _state_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(f"No pipeline state for run: {run_id}")
    return run_dir, PipelineState.model_validate(store.read_json(path))


def _new_state(run_id: str, source: Path, config_path: Path) -> PipelineState:
    now = _now()
    return PipelineState(
        pipeline_version=PIPELINE_VERSION,
        run_id=run_id,
        source_path=source,
        config_path=config_path,
        status=PipelineStatus.RUNNING,
        stages=[PipelineStageState(name=name) for name in PIPELINE_STAGE_NAMES],
        created_at=now,
        updated_at=now,
    )


def _validate_stage_names(names: set[str]) -> None:
    unknown = names.difference(PIPELINE_STAGE_NAMES)
    if unknown:
        raise ValueError(f"Unknown pipeline stage: {', '.join(sorted(unknown))}")


def _reset_from(state: PipelineState, start_index: int) -> None:
    for stage in state.stages[start_index:]:
        stage.status = StepStatus.PENDING
        stage.cache_hit = None
        stage.started_at = None
        stage.completed_at = None
        stage.duration_seconds = None
        stage.error = None
    state.status = PipelineStatus.RUNNING
    state.current_stage = None
    state.audit_status = None
    state.review_approved = False
    state.report_markdown = None
    state.report_html = None
    state.error = None


def _result(state: PipelineState, run_dir: Path) -> dict[str, Any]:
    return {
        "status": state.status.value,
        "run_id": state.run_id,
        "run_dir": str(run_dir),
        "current_stage": state.current_stage,
        "audit_status": state.audit_status,
        "review_approved": state.review_approved,
        "markdown": str(state.report_markdown) if state.report_markdown else None,
        "html": str(state.report_html) if state.report_html else None,
        "state": str(_state_path(run_dir)),
        "error": state.error,
        "stages": [stage.model_dump(mode="json") for stage in state.stages],
    }


def _execute(
    state: PipelineState,
    run_dir: Path,
    settings: Settings,
    *,
    force_stages: set[str],
    stage_runners: Mapping[str, StageRunner],
) -> dict[str, Any]:
    store = ArtifactStore(settings.paths.output_root)
    source = state.source_path
    for stage in state.stages:
        if stage.status == StepStatus.COMPLETED:
            continue
        runner = stage_runners[stage.name]
        state.status = PipelineStatus.RUNNING
        state.current_stage = stage.name
        state.error = None
        stage.status = StepStatus.RUNNING
        stage.attempts += 1
        stage.started_at = _now()
        stage.error = None
        _write_state(store, run_dir, state)
        started = time.perf_counter()
        try:
            output = runner(source, settings, stage.name in force_stages)
            if output.get("status") != "ok":
                raise RuntimeError(f"Stage {stage.name} returned non-ok status: {output.get('status')}")
        except Exception as exc:
            stage.status = StepStatus.FAILED
            stage.duration_seconds = round(time.perf_counter() - started, 6)
            stage.error = str(exc)[-4000:]
            state.status = PipelineStatus.FAILED
            state.error = f"{stage.name}: {stage.error}"
            _write_state(store, run_dir, state)
            raise RuntimeError(f"Pipeline failed at {stage.name}: {exc}") from exc
        stage.status = StepStatus.COMPLETED
        stage.cache_hit = bool(output.get("cache_hit", False))
        stage.completed_at = _now()
        stage.duration_seconds = round(time.perf_counter() - started, 6)
        if stage.name == "report":
            state.audit_status = str(output["audit_status"])
            state.report_markdown = Path(str(output["markdown"]))
            state.report_html = Path(str(output["html"]))
        _write_state(store, run_dir, state)

    state.current_stage = None
    if state.audit_status == "fail":
        state.status = PipelineStatus.FAILED
        state.error = "Critic audit failed; inspect critic/audit.json before retrying."
    elif state.audit_status == "review" and not state.review_approved:
        state.status = PipelineStatus.WAITING_CONFIRMATION
        state.error = None
    else:
        state.status = PipelineStatus.COMPLETED
        state.error = None
    _write_state(store, run_dir, state)
    return _result(state, run_dir)


def run_pipeline(
    video_path: Path,
    settings: Settings,
    *,
    config_path: Path,
    from_stage: str | None = None,
    force_stages: set[str] | None = None,
    stage_runners: Mapping[str, StageRunner] | None = None,
) -> dict[str, Any]:
    source = video_path.expanduser().resolve()
    runners = stage_runners or DEFAULT_STAGE_RUNNERS
    force = set(force_stages or set())
    requested = force | ({from_stage} if from_stage else set())
    _validate_stage_names(requested)
    missing_runners = set(PIPELINE_STAGE_NAMES).difference(runners)
    if missing_runners:
        raise ValueError(f"Missing pipeline runners: {', '.join(sorted(missing_runners))}")

    ingestion = runners["ingest"](source, settings, "ingest" in force)
    if ingestion.get("status") != "ok":
        raise RuntimeError(f"Ingestion returned non-ok status: {ingestion.get('status')}")
    run_id = str(ingestion["run_id"])
    run_dir = Path(str(ingestion["run_dir"]))
    store = ArtifactStore(settings.paths.output_root)
    path = _state_path(run_dir)
    if path.is_file():
        state = PipelineState.model_validate(store.read_json(path))
        if state.source_path.resolve() != source:
            raise ValueError("Pipeline source path does not match persisted state")
    else:
        state = _new_state(run_id, source, config_path.expanduser().resolve())

    explicit_indices = [PIPELINE_STAGE_NAMES.index(name) for name in requested]
    if not explicit_indices and state.status in {
        PipelineStatus.COMPLETED,
        PipelineStatus.WAITING_CONFIRMATION,
    }:
        return _result(state, run_dir)

    ingest_state = state.stages[0]
    ingest_state.status = StepStatus.COMPLETED
    ingest_state.attempts += 1
    ingest_state.cache_hit = bool(ingestion.get("cache_hit", False))
    ingest_state.started_at = ingest_state.started_at or _now()
    ingest_state.completed_at = _now()
    ingest_state.duration_seconds = 0.0

    if explicit_indices:
        _reset_from(state, min(explicit_indices))
        if min(explicit_indices) > 0:
            ingest_state.status = StepStatus.COMPLETED
    _write_state(store, run_dir, state)
    return _execute(state, run_dir, settings, force_stages=force, stage_runners=runners)


def resume_pipeline(
    run_id: str,
    settings: Settings,
    *,
    approve_review: bool = False,
    force_stages: set[str] | None = None,
    stage_runners: Mapping[str, StageRunner] | None = None,
) -> dict[str, Any]:
    store = ArtifactStore(settings.paths.output_root)
    run_dir, state = _load_state(store, run_id)
    runners = stage_runners or DEFAULT_STAGE_RUNNERS
    force = set(force_stages or set())
    _validate_stage_names(force)

    if approve_review:
        if state.status != PipelineStatus.WAITING_CONFIRMATION or state.audit_status != "review":
            raise ValueError("Review approval is only valid for a waiting review pipeline")
        state.review_approved = True
        state.status = PipelineStatus.COMPLETED
        state.current_stage = None
        state.error = None
        _write_state(store, run_dir, state)
        return _result(state, run_dir)

    if state.status == PipelineStatus.WAITING_CONFIRMATION:
        return _result(state, run_dir)
    if state.status == PipelineStatus.COMPLETED and not force:
        return _result(state, run_dir)
    if force:
        _reset_from(state, min(PIPELINE_STAGE_NAMES.index(name) for name in force))
    elif state.status == PipelineStatus.FAILED:
        failed = next((index for index, stage in enumerate(state.stages) if stage.status == StepStatus.FAILED), None)
        if failed is not None:
            _reset_from(state, failed)
    return _execute(state, run_dir, settings, force_stages=force, stage_runners=runners)
