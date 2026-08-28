import tempfile
import unittest
from pathlib import Path

from ad_vista_agent.agent.orchestrator import PIPELINE_STAGE_NAMES, resume_pipeline, run_pipeline
from ad_vista_agent.config import DEFAULT_CONFIG, load_settings
from ad_vista_agent.runtime import ArtifactStore
from ad_vista_agent.schemas import PipelineState


class FakePipeline:
    def __init__(self, root: Path, *, audit_status: str = "pass", fail_once: str | None = None) -> None:
        self.root = root
        self.audit_status = audit_status
        self.fail_once = fail_once
        self.calls: list[str] = []
        self.failed = False

    def runner(self, name: str):
        def run(video: Path, settings, force: bool):
            del settings, force
            self.calls.append(name)
            run_id = "ingest_test"
            run_dir = self.root / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            if name == self.fail_once and not self.failed:
                self.failed = True
                raise RuntimeError("injected failure")
            result = {
                "status": "ok",
                "cache_hit": name != "ingest",
                "run_id": run_id,
                "run_dir": str(run_dir),
            }
            if name == "ingest":
                result["asset"] = {"source_path": str(video)}
            if name == "report":
                report_dir = run_dir / "report"
                report_dir.mkdir(parents=True, exist_ok=True)
                markdown = report_dir / "report.md"
                html = report_dir / "report.html"
                markdown.write_text("report", encoding="utf-8")
                html.write_text("<html></html>", encoding="utf-8")
                result.update(
                    audit_status=self.audit_status,
                    markdown=str(markdown),
                    html=str(html),
                )
            return result

        return run

    @property
    def runners(self):
        return {name: self.runner(name) for name in PIPELINE_STAGE_NAMES}


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.video = self.root / "ad.mp4"
        self.video.write_bytes(b"video")
        settings = load_settings(DEFAULT_CONFIG)
        self.settings = settings.model_copy(
            update={"paths": settings.paths.model_copy(update={"output_root": self.root})}
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_review_waits_and_approval_does_not_rerun_stages(self) -> None:
        fake = FakePipeline(self.root, audit_status="review")
        result = run_pipeline(
            self.video,
            self.settings,
            config_path=DEFAULT_CONFIG,
            stage_runners=fake.runners,
        )
        self.assertEqual(result["status"], "waiting_confirmation")
        self.assertEqual(fake.calls, list(PIPELINE_STAGE_NAMES))

        approved = resume_pipeline(
            "ingest_test",
            self.settings,
            approve_review=True,
            stage_runners=fake.runners,
        )
        self.assertEqual(approved["status"], "completed")
        self.assertTrue(approved["review_approved"])
        self.assertEqual(fake.calls, list(PIPELINE_STAGE_NAMES))

    def test_resume_restarts_at_failed_stage(self) -> None:
        fake = FakePipeline(self.root, fail_once="ocr")
        with self.assertRaisesRegex(RuntimeError, "Pipeline failed at ocr"):
            run_pipeline(
                self.video,
                self.settings,
                config_path=DEFAULT_CONFIG,
                stage_runners=fake.runners,
            )
        self.assertEqual(fake.calls, ["ingest", "timeline", "speech", "ocr"])

        result = resume_pipeline("ingest_test", self.settings, stage_runners=fake.runners)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fake.calls[4:], ["ocr", "ledger", "insights", "report"])
        state = PipelineState.model_validate(
            ArtifactStore(self.root).read_json(
                self.root / "runs" / "ingest_test" / "orchestration" / "state.json"
            )
        )
        self.assertEqual(state.stages[3].attempts, 2)

    def test_force_stage_resets_dependent_stages(self) -> None:
        fake = FakePipeline(self.root)
        run_pipeline(
            self.video,
            self.settings,
            config_path=DEFAULT_CONFIG,
            stage_runners=fake.runners,
        )
        fake.calls.clear()
        result = resume_pipeline(
            "ingest_test",
            self.settings,
            force_stages={"ledger"},
            stage_runners=fake.runners,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fake.calls, ["ledger", "insights", "report"])

    def test_repeated_terminal_run_does_not_mutate_attempts(self) -> None:
        fake = FakePipeline(self.root)
        first = run_pipeline(
            self.video,
            self.settings,
            config_path=DEFAULT_CONFIG,
            stage_runners=fake.runners,
        )
        second = run_pipeline(
            self.video,
            self.settings,
            config_path=DEFAULT_CONFIG,
            stage_runners=fake.runners,
        )
        self.assertEqual(first["stages"], second["stages"])
        self.assertEqual(fake.calls.count("timeline"), 1)

    def test_unknown_stage_is_rejected(self) -> None:
        fake = FakePipeline(self.root)
        with self.assertRaisesRegex(ValueError, "Unknown pipeline stage"):
            run_pipeline(
                self.video,
                self.settings,
                config_path=DEFAULT_CONFIG,
                force_stages={"unknown"},
                stage_runners=fake.runners,
            )


if __name__ == "__main__":
    unittest.main()
