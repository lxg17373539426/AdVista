import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

from ad_vista_agent.errors import ModelTimeoutError
from ad_vista_agent.runtime.process import run_process
from ad_vista_agent.tools.qwen import QwenInsightTool


def test_timeout_terminates_descendant_after_parent_exits(tmp_path):
    ready = tmp_path / "ready"
    child = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(1.5)"
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"ready = Path({str(ready)!r});\n"
        "while not ready.exists(): time.sleep(.005)\n"
    )
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", parent], timeout=.2)
    # The descendant holds stdout/stderr open after its parent has exited.
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("error", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))])
def test_openai_runtime_preserves_timeout_category(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("urllib.request.urlopen", fail)
    tool = QwenInsightTool(Path(sys.executable), 1, "0", runtime="openai")
    with pytest.raises(ModelTimeoutError):
        tool._run_openai({"messages": [], "temperature": 0, "max_tokens": 10, "output_schema": {}})


def test_openai_runtime_rejects_empty_choices_as_invalid_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return b'{"choices": []}'
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    tool = QwenInsightTool(Path(sys.executable), 1, "0", runtime="openai")
    with pytest.raises(RuntimeError, match="无效响应"):
        tool._run_openai({"messages": [], "temperature": 0, "max_tokens": 10, "output_schema": {}})
