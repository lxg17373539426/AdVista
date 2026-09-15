from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated

import typer

from .agent import (
    PIPELINE_STAGE_NAMES,
    ask_agent,
    build_agent_registry,
    qwen_plan,
    resume_agent,
    resume_pipeline,
    rule_plan,
    run_agent,
    run_pipeline,
    record_feedback,
    show_conversation,
)
from .config import DEFAULT_CONFIG, load_settings
from .creative.builder import build_creative
from .ingestion import ingest_video
from .insights.builder import _model_identity, build_insights
from .ledger.builder import build_ledger
from .maintenance import apply_cleanup, cleanup_candidates
from .ocr.builder import build_ocr
from .ocr.quality import build_ocr_quality_report
from .reports.builder import build_report
from .schemas import AgentRequest
from .timeline.builder import build_timeline
from .speech.builder import build_speech


app = typer.Typer(
    name="advista-agent",
    help="AdVista Insight Agent command line interface.",
    no_args_is_help=True,
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _print(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command()
def doctor(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Validate Stage 1 paths, tools, and the one-GPU configuration boundary."""
    settings = load_settings(config)
    asr_python = settings.asr.python_executable.expanduser().absolute()
    deepseek_python = settings.ocr.deepseek_python.expanduser().absolute()
    insight_python = settings.insight.python_executable.expanduser().absolute()
    ffprobe = shutil.which(settings.tools.ffprobe_executable)
    model_checks = {
        name: settings.model_path(model).is_dir()
        for name, model in settings.models.model_dump().items()
    }
    configured_qwen = settings.model_path(settings.insight.model).resolve()
    try:
        qwen_identity = _model_identity(configured_qwen)
    except FileNotFoundError:
        qwen_identity = None
    checks: dict[str, object] = {
        "config": str(config.expanduser().resolve()),
        "model_root": settings.paths.model_root.is_dir(),
        "video_data": settings.paths.video_data.is_dir(),
        "output_parent": settings.paths.output_root.parent.is_dir(),
        "ffprobe": ffprobe,
        "asr_python": str(asr_python) if asr_python.is_file() else None,
        "deepseek_ocr_python": str(deepseek_python) if deepseek_python.is_file() else None,
        "insight_python": str(insight_python) if insight_python.is_file() else None,
        "gpu_limit": settings.hardware.max_gpus,
        "cuda_visible_devices": settings.hardware.cuda_visible_devices,
        "models": model_checks,
        "qwen_model": qwen_identity,
    }
    insight_service = None
    if settings.insight.runtime == "openai":
        try:
            with urllib.request.urlopen(
                settings.insight.endpoint.rstrip("/") + "/models", timeout=5
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            available_models = {
                str(item.get("id")): item for item in payload.get("data", [])
            }
            active_model = available_models.get(settings.insight.served_model)
            service_root = (
                Path(str(active_model.get("root"))).resolve()
                if isinstance(active_model, dict) and active_model.get("root")
                else None
            )
            insight_service = {
                "endpoint": settings.insight.endpoint,
                "served_model": settings.insight.served_model,
                "available": active_model is not None,
                "root": str(service_root) if service_root is not None else None,
                "matches_configured_model": service_root == configured_qwen,
            }
        except (OSError, ValueError, urllib.error.URLError):
            insight_service = {
                "endpoint": settings.insight.endpoint,
                "served_model": settings.insight.served_model,
                "available": False,
                "root": None,
                "matches_configured_model": False,
            }
    checks["insight_runtime"] = settings.insight.runtime
    checks["insight_service"] = insight_service
    gpu_name = None
    if shutil.which("nvidia-smi"):
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader", "-i", settings.hardware.cuda_visible_devices],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            gpu_name = result.stdout.strip()
    checks["configured_gpu"] = gpu_name
    required = [
        checks["model_root"],
        checks["video_data"],
        checks["output_parent"],
        bool(ffprobe),
        asr_python.is_file(),
        deepseek_python.is_file(),
        insight_python.is_file(),
        qwen_identity is not None,
    ]
    required.extend(model_checks.values())
    if insight_service is not None:
        required.append(bool(insight_service["available"]))
        required.append(bool(insight_service["matches_configured_model"]))
    checks["status"] = "ok" if all(required) else "failed"
    _print(checks)
    if checks["status"] != "ok":
        raise typer.Exit(1)


@app.command()
def ingest(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Register one video and write deterministic Stage 1 artifacts."""
    try:
        result = ingest_video(video, load_settings(config))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def timeline(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    scene_threshold: Annotated[float | None, typer.Option("--scene-threshold")] = None,
    min_shot_ms: Annotated[int | None, typer.Option("--min-shot-ms")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Build a normalized shot timeline and one primary keyframe per shot."""
    try:
        settings = load_settings(config)
        overrides = {}
        if scene_threshold is not None:
            overrides["scene_threshold"] = scene_threshold
        if min_shot_ms is not None:
            overrides["min_shot_ms"] = min_shot_ms
        if overrides:
            settings = settings.model_copy(
                update={"timeline": settings.timeline.model_copy(update=overrides)}
            )
        result = build_timeline(video, settings, force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def speech(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Generate timestamped Faster-Whisper speech evidence for one video."""
    try:
        result = build_speech(video, load_settings(config), force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def ocr(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Generate timestamped OCR evidence from Stage 2 keyframes."""
    try:
        result = build_ocr(video, load_settings(config), force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("ocr-quality")
def ocr_quality(
    raw: Annotated[Path, typer.Argument(help="Path to OCR raw.jsonl")],
    strict: Annotated[bool, typer.Option("--strict", help="Exit with code 1 when review is required")] = False,
) -> None:
    """Audit DeepSeek-OCR output for empty frames, noise, and repetition."""
    from .schemas import OcrFrameResult

    try:
        frames = [
            OcrFrameResult.model_validate(json.loads(line))
            for line in raw.expanduser().resolve().read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: invalid OCR result file: {exc}", err=True)
        raise typer.Exit(1) from exc
    result = build_ocr_quality_report(frames)
    _print(result)
    if strict and result["status"] != "pass":
        raise typer.Exit(1)


@app.command()
def ledger(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Build the unified deterministic Speech/OCR Evidence Ledger."""
    try:
        result = build_ledger(video, load_settings(config), force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def insights(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Generate Qwen3.5 evidence-grounded structured advertising insights."""
    try:
        result = build_insights(video, load_settings(config), force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def report(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Audit accepted insights and generate Markdown/HTML deliverables."""
    try:
        result = build_report(video, load_settings(config), force=force)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("run")
def run_all(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    from_stage: Annotated[
        str | None,
        typer.Option("--from-stage", help=f"Re-run from: {', '.join(PIPELINE_STAGE_NAMES)}"),
    ] = None,
    force_stage: Annotated[
        list[str] | None,
        typer.Option("--force-stage", help="Ignore a stage cache; repeat for multiple stages"),
    ] = None,
) -> None:
    """Run the complete Stage 1-7 pipeline with persistent recovery state."""
    try:
        result = run_pipeline(
            video,
            load_settings(config),
            config_path=config,
            from_stage=from_stage,
            force_stages=set(force_stage or []),
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="Persisted ingest_<hash> run ID")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    approve_review: Annotated[
        bool,
        typer.Option("--approve-review", help="Approve a Critic review without changing its audit"),
    ] = False,
    force_stage: Annotated[
        list[str] | None,
        typer.Option("--force-stage", help="Re-run this stage and all dependent stages"),
    ] = None,
) -> None:
    """Resume a failed pipeline or approve a report waiting for review."""
    try:
        result = resume_pipeline(
            run_id,
            load_settings(config),
            approve_review=approve_review,
            force_stages=set(force_stage or []),
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("agent-plan")
def agent_plan(
    goal: Annotated[str, typer.Option("--goal", help="Natural-language advertising analysis goal")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    mode: Annotated[str, typer.Option("--mode", help="quick or deep")] = "quick",
    deliverable: Annotated[
        list[str] | None,
        typer.Option("--deliverable", help="report, strategy, or creative"),
    ] = None,
    max_tool_calls: Annotated[int, typer.Option("--max-tool-calls")] = 12,
    qwen: Annotated[bool, typer.Option("--qwen", help="Use Qwen instead of the deterministic planner")] = False,
) -> None:
    """Create and validate an Agent plan without executing tools."""
    try:
        settings = load_settings(config)
        request = AgentRequest(
            goal=goal,
            mode=mode,
            deliverables=deliverable or [],
            max_tool_calls=max_tool_calls,
        )
        registry = build_agent_registry(settings)
        plan = qwen_plan(request, registry, settings) if qwen else rule_plan(request)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(plan.model_dump(mode="json"))


@app.command("agent")
def agent_run(
    video: Annotated[Path, typer.Argument(help="Path to a local advertising video")],
    goal: Annotated[str, typer.Option("--goal", help="Natural-language advertising analysis goal")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    mode: Annotated[str, typer.Option("--mode", help="quick or deep")] = "quick",
    deliverable: Annotated[
        list[str] | None,
        typer.Option("--deliverable", help="report, strategy, or creative"),
    ] = None,
    max_tool_calls: Annotated[int, typer.Option("--max-tool-calls")] = 12,
    qwen_planner: Annotated[
        bool,
        typer.Option("--qwen-planner", help="Use Qwen to select the validated tool plan"),
    ] = False,
    force_tool: Annotated[
        list[str] | None,
        typer.Option("--force-tool", help="Bypass this selected tool's content cache"),
    ] = None,
    backend: Annotated[
        str | None,
        typer.Option("--backend", help="Agent backend: langgraph or legacy"),
    ] = None,
) -> None:
    """Run the bounded goal-driven Agent with validated tool selection."""
    try:
        settings = load_settings(config)
        if backend is not None:
            settings = settings.model_copy(
                update={"agent": settings.agent.model_copy(update={"backend": backend})}
            )
        request = AgentRequest(
            goal=goal,
            mode=mode,
            deliverables=deliverable or [],
            max_tool_calls=max_tool_calls,
        )
        registry = build_agent_registry(settings)
        plan = qwen_plan(request, registry, settings) if qwen_planner else rule_plan(request)
        result = run_agent(
            video,
            settings,
            request,
            plan=plan,
            registry=registry,
            force_tools=set(force_tool or []),
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("agent-resume")
def agent_resume(
    run_id: Annotated[str, typer.Argument(help="Persisted ingest_<hash> run ID")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    approve: Annotated[
        bool,
        typer.Option("--approve", help="Approve the current human-confirmation gate"),
    ] = False,
) -> None:
    """Resume a failed Agent session or approve its pending confirmation."""
    try:
        settings = load_settings(config)
        result = resume_agent(
            run_id,
            settings,
            approve=approve,
            registry=build_agent_registry(settings),
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("chat")
def chat(
    run_id: Annotated[str, typer.Argument(help="Agent run ID with an existing Ledger")],
    question: Annotated[str, typer.Argument(help="Follow-up question grounded in existing evidence")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Ask a follow-up question without rerunning ASR, OCR, or video parsing."""
    try:
        result = ask_agent(run_id, question, load_settings(config))
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("creative")
def creative(
    video_path: Annotated[Path, typer.Argument(help="广告视频路径")],
    force: Annotated[bool, typer.Option("--force")] = False,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Generate evidence-grounded Hooks, script, storyboard, and A/B variants."""
    try:
        result = build_creative(video_path, load_settings(config), force=force)
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("serve")
def serve(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Start the local Stage 12 Web workspace."""
    logging.basicConfig(
        level=os.getenv("ADVISTA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from .web.app import serve as serve_web

    serve_web(load_settings(config))


@app.command("database-check")
def database_check(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Check the configured external service database connection."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database

        database = Database(settings.database)
        database.check()
    except Exception as exc:
        typer.echo(f"Error: database connection failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", "database": "reachable"})


@app.command("user-create")
def user_create(
    username: Annotated[str, typer.Argument(help="External user name")],
    role: Annotated[str, typer.Option("--role", help="user or admin")] = "user",
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Create a database API user and print its one-time API key."""
    if role not in {"user", "admin"}:
        raise typer.BadParameter("role must be user or admin")
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, UserRepository

        database = Database(settings.database)
        database.check()
        user, api_key = UserRepository(database).create_api_user(username, role=role)
    except Exception as exc:
        typer.echo(f"Error: user creation failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", "user_id": user.user_id, "username": user.username, "role": user.role, "api_key": api_key})


@app.command("migrate-conversations")
def migrate_conversations(
    sqlite_path: Annotated[Path | None, typer.Option("--sqlite-path")] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Copy existing SQLite conversations into the configured PostgreSQL database."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .agent.chat import conversation_db
        from .database import Database, migrate_sqlite_conversations

        database = Database(settings.database)
        database.check()
        result = migrate_sqlite_conversations(
            (sqlite_path or conversation_db(settings)).expanduser().resolve(), database
        )
    except Exception as exc:
        typer.echo(f"Error: conversation migration failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", **result})


@app.command("migrate-jobs")
def migrate_jobs(
    job_root: Annotated[Path | None, typer.Option("--job-root")] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Backfill legacy Web Job JSON files into database execution records."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, migrate_json_jobs

        database = Database(settings.database)
        database.check()
        result = migrate_json_jobs(
            (job_root or settings.paths.output_root / "jobs").expanduser().resolve(),
            database,
        )
    except Exception as exc:
        typer.echo(f"Error: job migration failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", **result})


@app.command("migrate-contexts")
def migrate_contexts(
    job_root: Annotated[Path | None, typer.Option("--job-root")] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Backfill context versions from existing Jobs and their artifacts."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, migrate_json_contexts

        database = Database(settings.database)
        database.check()
        result = migrate_json_contexts(
            (job_root or settings.paths.output_root / "jobs").expanduser().resolve(),
            settings.paths.output_root,
            database,
        )
    except Exception as exc:
        typer.echo(f"Error: context migration failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", **result})


@app.command("repair-messages")
def repair_messages(
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Complete orphaned pending user messages left by terminal requests."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, RequestRepository

        database = Database(settings.database)
        database.check()
        result = RequestRepository(database).repair_pending_user_messages()
    except Exception as exc:
        typer.echo(f"Error: message repair failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", **result})


@app.command("quality-report")
def quality_report(
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Write a redacted execution quality report suitable for cron jobs."""
    database = None
    try:
        from datetime import datetime, timezone
        from .database import Database, DiagnosticRepository
        from .runtime import ArtifactStore

        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        database = Database(settings.database)
        database.check()
        diagnostics = DiagnosticRepository(database)
        generated_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema_version": "1",
            "generated_at": generated_at,
            "stats": diagnostics.stats(),
            "recent_failures": diagnostics.export_executions(status="failed", limit=100),
        }
        target = (
            output.expanduser().resolve()
            if output is not None
            else settings.paths.output_root / "diagnostics" / "quality-report.json"
        )
        ArtifactStore(settings.paths.output_root).write_json(target, payload)
    except Exception as exc:
        typer.echo(f"Error: quality report failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", "report": str(target), "generated_at": generated_at})


@app.command("user-delete")
def user_delete(
    user_id: Annotated[str, typer.Argument(help="Database user ID")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually delete database records")] = False,
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Preview or permanently delete a user's database records."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, DiagnosticRepository, UserRepository

        database = Database(settings.database)
        database.check()
        executions = DiagnosticRepository(database).executions(user_id=user_id, limit=500)
        if not apply:
            _print({"status": "preview", "user_id": user_id, "execution_count": len(executions)})
            return
        deleted = UserRepository(database).delete_user(user_id)
    except Exception as exc:
        typer.echo(f"Error: user deletion failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", "user_id": user_id, **deleted})


@app.command("user-status")
def user_status(
    user_id: Annotated[str, typer.Argument(help="Database user ID")],
    status: Annotated[str, typer.Argument(help="active, revoked, or disabled")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Activate, revoke, or disable a database API user."""
    database = None
    try:
        settings = load_settings(config)
        if not settings.database.configured:
            raise ValueError("ADVISTA_DATABASE_URL is not configured")
        from .database import Database, UserRepository

        database = Database(settings.database)
        database.check()
        UserRepository(database).set_status(user_id, status)
    except Exception as exc:
        typer.echo(f"Error: user status update failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if database is not None:
            database.close()
    _print({"status": "ok", "user_id": user_id, "user_status": status})


@app.command("session-show")
def session_show(
    run_id: Annotated[str, typer.Argument(help="Agent run ID")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
) -> None:
    """Show persisted Agent session and conversation messages."""
    try:
        result = show_conversation(run_id, load_settings(config))
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command("feedback")
def feedback(
    run_id: Annotated[str, typer.Argument(help="Agent run ID")],
    decision: Annotated[str, typer.Argument(help="approve, reject, or revise")],
    note: Annotated[str, typer.Argument(help="Feedback note")],
    config: Annotated[Path, typer.Option("--config", "-c")] = DEFAULT_CONFIG,
    message_id: Annotated[str | None, typer.Option("--message-id")] = None,
) -> None:
    """Persist a user decision about an Agent answer or deliverable."""
    try:
        result = record_feedback(
            run_id,
            decision,
            note,
            load_settings(config),
            message_id=message_id,
        )
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


@app.command()
def clean(
    apply: Annotated[bool, typer.Option("--apply", help="Delete listed files; otherwise preview only")] = False,
    runs: Annotated[bool, typer.Option("--runs", help="Also remove non-retained run directories")] = False,
    keep_run: Annotated[
        str | None,
        typer.Option("--keep-run", help="Run directory name retained when --runs is used"),
    ] = "ingest_eabdf3d87eb58a38feab",
) -> None:
    """Preview or remove generated Python files and optional test runs."""
    candidates = cleanup_candidates(PROJECT_ROOT, include_runs=runs, keep_run=keep_run)
    deleted = apply_cleanup(PROJECT_ROOT, candidates) if apply else []
    _print(
        {
            "mode": "apply" if apply else "dry-run",
            "candidate_count": len(candidates),
            "candidates": [str(path.relative_to(PROJECT_ROOT)) for path in candidates],
            "deleted_count": len(deleted),
            "kept_run": keep_run if runs else None,
        }
    )


if __name__ == "__main__":
    app()
