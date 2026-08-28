import tempfile
import unittest
from pathlib import Path

from ad_vista_agent.maintenance import apply_cleanup, cleanup_candidates


class MaintenanceTests(unittest.TestCase):
    def test_keeps_selected_run_and_virtual_environments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ad_vista_agent" / "__pycache__").mkdir(parents=True)
            (root / "package.egg-info").mkdir()
            (root / ".venv").mkdir()
            (root / ".venv-asr").mkdir()
            (root / "outputs" / "runs" / "demo").mkdir(parents=True)
            (root / "outputs" / "runs" / "test").mkdir()
            candidates = cleanup_candidates(root, include_runs=True, keep_run="demo")
            relative = {path.relative_to(root) for path in candidates}
            self.assertIn(Path("ad_vista_agent/__pycache__"), relative)
            self.assertIn(Path("package.egg-info"), relative)
            self.assertIn(Path("outputs/runs/test"), relative)
            self.assertNotIn(Path("outputs/runs/demo"), relative)
            self.assertNotIn(Path(".venv"), relative)
            self.assertNotIn(Path(".venv-asr"), relative)

    def test_apply_removes_only_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "tests" / "__pycache__"
            cache.mkdir(parents=True)
            keep = root / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            apply_cleanup(root, [cache])
            self.assertFalse(cache.exists())
            self.assertTrue(keep.exists())

    def test_includes_script_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "benchmark.pyc").write_bytes(b"cache")
            candidates = cleanup_candidates(root, include_runs=False, keep_run=None)
            self.assertIn(cache, candidates)


if __name__ == "__main__":
    unittest.main()
