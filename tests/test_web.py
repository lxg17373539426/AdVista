import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import Future
import json
from pathlib import Path
from unittest.mock import patch

from ad_vista_agent.config import DEFAULT_CONFIG, load_settings
from ad_vista_agent.web.app import _Handler, _MultipartReader, _contained, _multipart_headers, _safe_run_id
from ad_vista_agent.web.service import WebService


class WebSecurityTests(unittest.TestCase):
    def test_streaming_multipart_reader_stops_at_boundary(self) -> None:
        boundary = b"test-boundary"
        payload = (
            b'Content-Disposition: form-data; name="goal"\r\n\r\n'
            b"deep analysis\r\n--test-boundary--\r\n"
        )
        reader = _MultipartReader(io.BytesIO(payload), len(payload))
        name, filename = _multipart_headers(reader)
        value = bytearray()
        size, final = reader.body_until(boundary, value.extend, 1000)
        self.assertEqual(name, "goal")
        self.assertIsNone(filename)
        self.assertEqual(bytes(value), b"deep analysis")
        self.assertEqual(size, len(value))
        self.assertTrue(final)

    def test_run_id_allowlist(self) -> None:
        self.assertTrue(_safe_run_id("ingest_" + "a" * 20))
        self.assertTrue(_safe_run_id("exec_" + "a" * 32))
        self.assertFalse(_safe_run_id("ingest_../secret"))
        self.assertFalse(_safe_run_id("ingest_" + "g" * 20))
        self.assertFalse(_safe_run_id("exec_../secret"))

    def test_contained_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ok.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_contained(root, "ok.json"), root / "ok.json")
            with self.assertRaises(FileNotFoundError):
                _contained(root, "../outside.json")

    def test_keyframe_artifact_path_resolves_from_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            frame = run_dir / "timeline" / "frames" / "shot.jpg"
            frame.parent.mkdir(parents=True)
            frame.write_bytes(b"image")
            keyframes = run_dir / "timeline" / "keyframes.jsonl"
            keyframes.write_text(
                json.dumps(
                    {
                        "keyframe_id": "kf_0001_primary",
                        "artifact_path": "timeline/frames/shot.jpg",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            handler = object.__new__(_Handler)
            self.assertEqual(
                _Handler._keyframe(handler, run_dir, "kf_0001_primary"),
                frame,
            )


class WebServiceTests(unittest.TestCase):
    def test_submit_exposes_job_without_running_inline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(DEFAULT_CONFIG).model_copy(
                update={"paths": load_settings(DEFAULT_CONFIG).paths.model_copy(update={"output_root": Path(directory)})}
            )
            service = WebService(settings)
            try:
                with patch.object(service, "_run") as runner:
                    job = service.submit(
                        Path("video.mp4"),
                        "分析广告",
                        ["report"],
                        mode="deep",
                    )
                    runner.assert_called_once()
                    self.assertEqual(job["status"], "queued")
                    self.assertTrue(job["execution_id"].startswith("exec_"))
                    self.assertEqual(job["mode"], "deep")
                    self.assertEqual(job["deliverables"], ["report"])
                    self.assertTrue(
                        (Path(directory) / "jobs" / f"{job['job_id']}.json").is_file()
                    )
            finally:
                service.close()

    def test_web_run_uses_rule_plan_for_explicit_deliverables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = load_settings(DEFAULT_CONFIG)
            settings = base.model_copy(
                update={"paths": base.paths.model_copy(update={"output_root": Path(directory)})}
            )
            service = WebService(settings)
            try:
                service.jobs["job_test"] = {"job_id": "job_test", "status": "queued"}
                with patch("ad_vista_agent.web.service.rule_plan", return_value="explicit-plan") as rules, patch(
                    "ad_vista_agent.web.service.qwen_plan"
                ) as qwen, patch("ad_vista_agent.web.service.run_agent", return_value={"status": "completed", "run_id": "ingest_test"}) as runner:
                    service._run(
                        "job_test",
                        "exec_" + "a" * 32,
                        Path("video.mp4"),
                        "只生成洞察",
                        ["insights"],
                        "quick",
                        threading.Event(),
                    )
                rules.assert_called_once()
                qwen.assert_not_called()
                self.assertEqual(runner.call_args.kwargs["plan"], "explicit-plan")
            finally:
                service.close()

    def test_web_run_uses_qwen_plan_when_deliverables_are_automatic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = load_settings(DEFAULT_CONFIG)
            settings = base.model_copy(
                update={"paths": base.paths.model_copy(update={"output_root": Path(directory)})}
            )
            service = WebService(settings)
            try:
                service.jobs["job_test"] = {"job_id": "job_test", "status": "queued"}
                with patch("ad_vista_agent.web.service.rule_plan") as rules, patch(
                    "ad_vista_agent.web.service.qwen_plan", return_value="automatic-plan"
                ) as qwen, patch("ad_vista_agent.web.service.run_agent", return_value={"status": "completed", "run_id": "ingest_test"}) as runner:
                    service._run(
                        "job_test",
                        "exec_" + "a" * 32,
                        Path("video.mp4"),
                        "分析这个广告",
                        [],
                        "quick",
                        threading.Event(),
                    )
                qwen.assert_called_once()
                rules.assert_not_called()
                self.assertEqual(runner.call_args.kwargs["plan"], "automatic-plan")
            finally:
                service.close()

    def test_nonterminal_persisted_job_is_recovered_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "jobs"
            job_dir.mkdir(parents=True)
            (job_dir / "job_test.json").write_text(
                json.dumps(
                    {
                        "job_id": "job_test",
                        "execution_id": "exec_" + "a" * 32,
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )
            base = load_settings(DEFAULT_CONFIG)
            settings = base.model_copy(
                update={"paths": base.paths.model_copy(update={"output_root": root})}
            )
            service = WebService(settings)
            try:
                recovered = service.job("job_test")
                self.assertEqual(recovered["status"], "failed")
                self.assertIn("服务重启", recovered["error"])
            finally:
                service.close()

    def test_run_summary_preserves_actionable_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(DEFAULT_CONFIG).model_copy(
                update={
                    "paths": load_settings(DEFAULT_CONFIG).paths.model_copy(
                        update={"output_root": Path(directory)}
                    )
                }
            )
            service = WebService(settings)
            try:
                summary = service._run_summary(
                    {
                        "run_id": "ingest_" + "a" * 20,
                        "artifacts": {"agent": True},
                        "agent": {
                            "status": "waiting_confirmation",
                            "error": None,
                        },
                    }
                )
                self.assertEqual(summary["status"], "waiting_confirmation")
                self.assertEqual(summary["status_label"], "待确认")
                self.assertTrue(summary["requires_action"])
                self.assertNotIn("agent", summary)
            finally:
                service.close()

    def test_cancel_queued_job_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(DEFAULT_CONFIG).model_copy(
                update={
                    "paths": load_settings(DEFAULT_CONFIG).paths.model_copy(
                        update={"output_root": Path(directory)}
                    )
                }
            )
            service = WebService(settings)
            try:
                future: Future[None] = Future()
                service.jobs["job_test"] = {"job_id": "job_test", "status": "queued"}
                service.job_futures["job_test"] = future
                service.job_cancel_events["job_test"] = threading.Event()
                first = service.cancel_job("job_test")
                second = service.cancel_job("job_test")
                self.assertEqual(first["status"], "cancelled")
                self.assertEqual(second["status"], "cancelled")
                self.assertTrue(service.job_cancel_events["job_test"].is_set())
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
