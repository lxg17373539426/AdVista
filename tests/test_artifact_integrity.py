from pathlib import Path
from tempfile import TemporaryDirectory

from ad_vista_agent.runtime import ArtifactStore, sha256_file


def test_publish_directory_replaces_target_only_after_staging_exists() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "stage"
        target.mkdir()
        (target / "old.txt").write_text("old", encoding="utf-8")
        staging = root / ".stage.tmp"
        staging.mkdir()
        (staging / "new.txt").write_text("new", encoding="utf-8")

        ArtifactStore(root).publish_directory(staging, target)

        assert not staging.exists()
        assert not (target / "old.txt").exists()
        assert (target / "new.txt").read_text(encoding="utf-8") == "new"


def test_validate_file_hashes_detects_tampering() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "artifact.json"
        path.write_text("original", encoding="utf-8")
        hashes = {str(path): sha256_file(path)}
        store = ArtifactStore(Path(directory))

        assert store.validate_file_hashes(hashes)
        path.write_text("tampered", encoding="utf-8")
        assert not store.validate_file_hashes(hashes)
