"""Regression checks for lossless source import and bounded read cursors."""
from __future__ import annotations

import io
import copy
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from shortdrama_mcp.file_io import extract_file, preview_extracted_bulk
from shortdrama_mcp.external_storage import ExternalProjectStorage
from shortdrama_mcp.models import OpenAIFile
from shortdrama_mcp.service import _recovery_mapping_identity
from shortdrama_mcp.storage import ProjectStore, sha256_bytes


class SourceImportRegressionTests(unittest.TestCase):
    def test_xlsx_and_csv_preserve_colliding_header_columns(self):
        headers = ["A", "A", "A_2", "", "列4", "A"]
        values = ["first", "second", "third", "fourth", "fifth", "sixth"]
        workbook = Workbook()
        workbook.active.append(headers)
        workbook.active.append(values)
        stream = io.BytesIO()
        workbook.save(stream)
        csv_data = (",".join(headers) + "\n" + ",".join(values) + "\n").encode()
        for filename, payload in (("source.xlsx", stream.getvalue()), ("source.csv", csv_data)):
            with self.subTest(filename=filename):
                extracted = extract_file(payload, filename)
                table = extracted["sheets"][0] if "sheets" in extracted else extracted
                self.assertEqual(len(set(table["headers"])), len(headers))
                self.assertEqual([table["rows"][0][key] for key in table["headers"]], values)

    def test_exhausted_budget_keeps_paragraph_and_page_resume_cursors(self):
        text = "abcdefghij"
        for unit in ("paragraphs", "pages"):
            with self.subTest(unit=unit):
                extracted = {"kind": "text", "text": text, unit: [text, "next"]}
                page = preview_extracted_bulk(extracted, [
                    {"unit": "characters", "limit": 3},
                    {"unit": unit, "offset": 0, "inner_offset": 4},
                ], max_chars=3)["ranges"][1]
                self.assertEqual(page["content"], "")
                self.assertEqual((page["next_offset"], page["next_inner_offset"]), (0, 4))
                self.assertTrue(page["partial_item"])
                resumed = preview_extracted_bulk(extracted, [{
                    "unit": unit, "offset": page["next_offset"],
                    "inner_offset": page["next_inner_offset"],
                }], max_chars=16)["ranges"][0]
                self.assertEqual(resumed["content"], "efghij\n\nnext")
                self.assertTrue(resumed["complete"])
                self.assertEqual(resumed["next_inner_offset"], 0)

    def test_partial_item_cursor_advances_without_repeating_text(self):
        extracted = {"kind": "text", "paragraphs": ["abcdefghij"]}
        offset = inner_offset = 0
        chunks = []
        for _ in range(4):
            page = preview_extracted_bulk(extracted, [{
                "unit": "paragraphs", "offset": offset, "inner_offset": inner_offset,
            }], max_chars=3)["ranges"][0]
            chunks.append(page["content"])
            offset, inner_offset = page["next_offset"], page["next_inner_offset"]
        self.assertEqual("".join(chunks), "abcdefghij")
        self.assertTrue(page["complete"])


class RecoveryRegressionTests(unittest.TestCase):
    def test_recovery_comparison_ignores_only_replay_flag(self):
        original = [{"handoff_id": "HND-one", "evidence": {"sha256": "original"},
                     "idempotent_replay": False}]
        replay = copy.deepcopy(original)
        replay[0]["idempotent_replay"] = True
        self.assertEqual(_recovery_mapping_identity(original), _recovery_mapping_identity(replay))
        replay[0]["evidence"]["sha256"] = "changed"
        self.assertNotEqual(_recovery_mapping_identity(original), _recovery_mapping_identity(replay))
        self.assertEqual(original[0]["evidence"], {"sha256": "original"})
        self.assertFalse(original[0]["idempotent_replay"])

    def test_remount_is_idempotent_and_never_erases_unsynced_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ProjectStore(root / "data", "https://example.invalid")
            manager = ExternalProjectStorage(SimpleNamespace(store=store),
                SimpleNamespace(public_base_url="https://example.invalid"),
                mode="library", runtime_root=root / "runtime")
            namespace, project_id = "test-owner", "SBP-remount"
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                entry = tarfile.TarInfo(manager._archive_prefix(namespace, project_id) + "hub.json")
                payload = b'{"project_id":"SBP-remount"}'
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            snapshot = buffer.getvalue()
            source = root / "source"
            source.mkdir()
            (source / "hub.json").write_bytes(payload)
            tree_sha256 = manager.tree_manifest(source)[0]
            manager._write_registry(namespace, project_id, {
                "bundle_sha256": sha256_bytes(snapshot), "tree_sha256": tree_sha256, "generation": 1,
            })
            source_file = OpenAIFile(download_url="https://example.invalid/snapshot",
                                     file_id="file_snapshot", file_name="snapshot.tgz")
            with patch("shortdrama_mcp.external_storage.download_openai_file",
                       return_value=(snapshot, "snapshot.tgz", "application/gzip")):
                manager.mount_snapshot(namespace, project_id=project_id, snapshot=source_file)
                mounted = manager._mounted_project_dir(namespace, project_id)
                hub = mounted / "hub.json"
                initial_stat = hub.stat()
                manager.mount_snapshot(namespace, project_id=project_id, snapshot=source_file)
                self.assertEqual(hub.stat().st_mtime_ns, initial_stat.st_mtime_ns)
                self.assertEqual(hub.read_bytes(), payload)
                pending = mounted / "pending-audit.json"
                pending.write_bytes(b'{"status":"pending"}')
                for action in (
                    lambda: manager.mount_snapshot(namespace, project_id=project_id, snapshot=source_file),
                    lambda: manager.unmount(namespace, project_id),
                ):
                    with self.assertRaisesRegex(ValueError, "library_project_unsynced_changes"):
                        action()
                    self.assertEqual(pending.read_bytes(), b'{"status":"pending"}')
                    self.assertEqual(hub.read_bytes(), payload)
                exported = manager.export_snapshot(namespace, project_id)
                self.assertEqual(manager._extract_project(Path(exported["path"]).read_bytes(),
                    namespace=namespace, project_id=project_id,
                    expected_tree_sha256=exported["tree_sha256"], install=False)[0],
                    manager.tree_manifest(mounted)[0])


if __name__ == "__main__":
    unittest.main()
