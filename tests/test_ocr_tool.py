import tempfile
import unittest
from pathlib import Path

from ad_vista_agent.tools.ocr import OcrWorkerTool


class OcrWorkerToolTests(unittest.TestCase):
    def test_preserves_conda_environment_interpreter_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "base-python"
            target.write_text("", encoding="utf-8")
            env_python = root / "env-python"
            env_python.symlink_to(target)
            tool = OcrWorkerTool(env_python, timeout_seconds=1, cuda_visible_devices="0")
            self.assertEqual(tool.python_executable, env_python.absolute())
            self.assertNotEqual(tool.python_executable, env_python.resolve())


if __name__ == "__main__":
    unittest.main()
