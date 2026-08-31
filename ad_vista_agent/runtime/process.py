from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence


class ProcessCancelled(RuntimeError):
    """Raised when a cancellable external process is stopped by the caller."""


def _terminate_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_process(
    command: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(  # type: ignore[call-overload]
        [str(item) for item in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    started = time.monotonic()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_group(process)
                stdout, stderr = process.communicate()
                raise ProcessCancelled(
                    f"External process cancelled: {stderr.strip()[-1000:] or command[0]}"
                )
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                _terminate_group(process)
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            try:
                stdout, stderr = process.communicate(timeout=min(remaining, 0.5))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if process.poll() is None:
            _terminate_group(process)
        raise
