import tempfile
import unittest
from pathlib import Path

from ad_vista_agent.tools.asr import FasterWhisperTool


class FasterWhisperToolTests(unittest.TestCase):
    def test_preserves_virtual_environment_interpreter_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "base-python"
            target.write_text("", encoding="utf-8")
            venv_python = root / "venv-python"
            venv_python.symlink_to(target)
            tool = FasterWhisperTool(venv_python, timeout_seconds=1, cuda_visible_devices="0")
            self.assertEqual(tool.python_executable, venv_python.absolute())
            self.assertNotEqual(tool.python_executable, venv_python.resolve())


if __name__ == "__main__":
    unittest.main()
