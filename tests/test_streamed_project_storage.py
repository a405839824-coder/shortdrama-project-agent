"""Storage-only regressions: no production inputs or content Validator."""
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from shortdrama_mcp.storage import ProjectStore


class StreamedProjectStorageTests(unittest.TestCase):
    def test_binary_buffer_inputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "data.bin"
            for value in (b"value", bytearray(b"value"), memoryview(b"value")):
                ProjectStore.atomic_write_bytes(path, value)
                self.assertEqual(path.read_bytes(), b"value")

    def test_exact_legacy_json_bytes(self):
        value = {"汉字": ["中😀\n\"", 1, 1.5, True, None, {}], "nested": {"z": 2, "a": 3}}
        expected = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.json"
            with patch("shortdrama_mcp.storage.json.dumps", side_effect=AssertionError("full serialization forbidden")):
                ProjectStore.atomic_write_json(path, value)
            self.assertEqual(path.read_bytes(), expected)

    def test_serialization_failure_preserves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.json"
            path.write_bytes(b"original")
            with self.assertRaises(TypeError):
                ProjectStore.atomic_write_json(path, {"started": [1, 2], "invalid": object()})
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_replace_failure_preserves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.json"
            path.write_bytes(b"original")
            with patch("shortdrama_mcp.storage.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    ProjectStore.atomic_write_json(path, {"new": True})
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_large_document_does_not_create_document_sized_buffer(self):
        value = [{"text": "测" * 4096, "index": i} for i in range(2048)]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.json"
            tracemalloc.start()
            try:
                ProjectStore.atomic_write_json(path, value)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertGreater(path.stat().st_size, 24_000_000)
            self.assertLess(peak, 2_000_000)


if __name__ == "__main__":
    unittest.main()
