import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ad_vista_agent.runtime import ArtifactStore, sha256_file


class ArtifactStoreTests(unittest.TestCase):
    def test_atomic_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            path = store.prepare("run_1") / "value.json"
            store.write_json(path, {"value": 1})
            self.assertEqual(store.read_json(path), {"value": 1})

    def test_concurrent_json_writes_use_distinct_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            path = store.prepare("run_1") / "value.json"
            barrier = threading.Barrier(8)

            def write(value: int) -> None:
                barrier.wait()
                store.write_json(path, {"value": value})

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write, range(8)))

            self.assertIn(store.read_json(path)["value"], range(8))
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_sha256_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bin"
            path.write_bytes(b"advista")
            self.assertEqual(sha256_file(path), sha256_file(path))
            self.assertEqual(len(sha256_file(path)), 64)

    def test_rejects_unsafe_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            with self.assertRaises(ValueError):
                store.run_dir("../escape")


if __name__ == "__main__":
    unittest.main()
