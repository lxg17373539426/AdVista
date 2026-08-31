from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import fcntl

from pydantic import BaseModel

from .fingerprint import sha256_file


class ArtifactStore:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.expanduser().resolve()

    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or ".." in run_id:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        return self.output_root / "runs" / run_id

    def prepare(self, run_id: str) -> Path:
        target = self.run_dir(run_id)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def execution_dir(self, execution_id: str) -> Path:
        if not execution_id.startswith("exec_") or len(execution_id) != 37:
            raise ValueError(f"Invalid execution_id: {execution_id!r}")
        if any(char not in "0123456789abcdef" for char in execution_id[5:]):
            raise ValueError(f"Invalid execution_id: {execution_id!r}")
        return self.output_root / "executions" / execution_id

    def prepare_execution(self, execution_id: str) -> Path:
        target = self.execution_dir(execution_id)
        target.mkdir(parents=True, exist_ok=False)
        return target

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        safe_key = "".join(char if char.isalnum() or char in "_-" else "_" for char in key)
        lock_dir = self.output_root / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        with (lock_dir / f"{safe_key}.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def write_json(self, path: Path, value: BaseModel | dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def read_json(self, path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path}")
        return value

    def write_jsonl(self, path: Path, values: Iterable[BaseModel | dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            for value in values:
                payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        temporary.replace(path)

    def publish_directory(self, staging: Path, target: Path) -> None:
        """Publish a completed stage directory without exposing its partial contents."""
        staging = staging.resolve()
        target = target.resolve()
        if not staging.is_dir():
            raise FileNotFoundError(staging)
        if staging == target or target in staging.parents:
            raise ValueError("Invalid stage publish paths")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.bak")
        if target.exists():
            target.replace(backup)
        try:
            staging.replace(target)
        except BaseException:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        if backup.exists():
            import shutil

            shutil.rmtree(backup)

    @staticmethod
    def validate_file_hashes(hashes: object) -> bool:
        if not isinstance(hashes, dict):
            return False
        for raw_path, raw_hash in hashes.items():
            path = Path(str(raw_path))
            if not path.is_file() or str(raw_hash) != sha256_file(path):
                return False
        return True
