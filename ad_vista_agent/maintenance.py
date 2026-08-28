from __future__ import annotations

import shutil
from pathlib import Path


def cleanup_candidates(
    root: Path,
    *,
    include_runs: bool,
    keep_run: str | None,
) -> list[Path]:
    root = root.expanduser().resolve()
    candidates: set[Path] = set()
    for parent in (root / "ad_vista_agent", root / "scripts", root / "tests"):
        if not parent.is_dir():
            continue
        candidates.update(path for path in parent.rglob("__pycache__") if path.is_dir())
        candidates.update(
            path
            for path in parent.rglob("*.pyc")
            if path.is_file() and "__pycache__" not in path.parts
        )
    candidates.update(path for path in root.glob("*.egg-info") if path.is_dir())
    if include_runs:
        runs = root / "outputs" / "runs"
        if runs.is_dir():
            candidates.update(
                path for path in runs.iterdir() if path.is_dir() and path.name != keep_run
            )
    return sorted(candidates, key=lambda path: str(path))


def apply_cleanup(root: Path, candidates: list[Path]) -> list[Path]:
    root = root.expanduser().resolve()
    deleted: list[Path] = []
    for candidate in candidates:
        target = candidate.absolute()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Refusing to delete outside project root: {target}") from exc
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        deleted.append(target)
    return deleted
