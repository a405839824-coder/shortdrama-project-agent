"""Bounded real-lock and signed-record controls for narrative transactions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from filelock import FileLock, Timeout
import shortdrama_mcp.storage as storage


class NarrativeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = storage.ProjectStore(self.root, "http://localhost")
        self.peer = storage.ProjectStore(self.root / ".", "http://localhost")
        self.namespace = "synthetic-narrative"
        self.project_id = "P-SYNTHETIC"
        self.store.create_project(self.namespace, {"project_id": self.project_id}, {})
        # The production timeout is infinite; controls use the identical real
        # lock with a bounded timeout so a regression fails without hanging.
        self.patch = mock.patch.object(storage, "FileLock", side_effect=lambda path: FileLock(path, timeout=0.6))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def record(self):
        return self.store.save_project_record(self.namespace, self.project_id,
            kind="narrative_records", record_id="NAR-SYNTHETIC",
            value={"record_id": "NAR-SYNTHETIC", "record_type": "attempt", "sequence": 1,
                   "candidate_snapshot": {"text": "A synthetic unchanged draft."}})

    def test_same_thread_multi_store_same_path_nested_transaction(self):
        with self.store.project_write_lock(self.namespace, self.project_id):
            with self.peer.project_write_lock(self.namespace, self.project_id):
                record = self.record()
                with self.peer.mutate_project(self.namespace, self.project_id) as (hub, _ledger):
                    hub["narrative_record_id"] = record["record_id"]
        self.assertEqual(self.store.load_hub(self.namespace, self.project_id)["narrative_record_id"], record["record_id"])

    def test_exception_releases_outer_lock_and_thread_local_entry(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic abort"):
            with self.store.lock("exception"):
                with self.peer.lock("exception"):
                    raise RuntimeError("synthetic abort")
        lock_path = self.root / ".exception.lock"
        with FileLock(str(lock_path), timeout=0.2):
            # A leaked thread-local entry would incorrectly bypass this owner.
            with self.assertRaises(Timeout):
                with self.peer.lock("exception"):
                    self.fail("same-thread leaked ownership bypassed a fresh external lock")

    def test_other_thread_cannot_enter_until_outermost_release(self):
        entered = threading.Event()
        started = threading.Event()
        errors = queue.Queue()

        def worker():
            started.set()
            try:
                with self.peer.lock("exclusive"):
                    entered.set()
            except BaseException as exc:
                errors.put(exc)

        with self.store.lock("exclusive"):
            with self.peer.lock("exclusive"):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                self.assertTrue(started.wait(0.2))
                self.assertFalse(entered.wait(0.1))
            self.assertFalse(entered.wait(0.1))
        thread.join(1.5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors.empty(), repr(list(errors.queue)))
        self.assertTrue(entered.is_set())

    def test_other_process_cannot_enter_held_lock(self):
        script = (
            "from filelock import FileLock, Timeout\nimport sys\n"
            "try:\n with FileLock(sys.argv[1], timeout=0.15): pass\n"
            "except Timeout:\n print('blocked'); sys.exit(0)\n"
            "sys.exit(4)\n"
        )
        with self.store.lock("process-exclusive"):
            result = subprocess.run([sys.executable, "-c", script, str(self.root / ".process-exclusive.lock")],
                                    capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "blocked")

    def test_narrative_record_is_signed_and_replay_does_not_overwrite(self):
        record = self.record()
        self.assertTrue(self.store.project_record_is_signed(record))
        self.assertIn("narrative_records", storage.SIGNED_ONLY_PROJECT_RECORD_KINDS)
        self.assertEqual(self.store.save_project_record(self.namespace, self.project_id,
            kind="narrative_records", record_id=record["record_id"], value=record), record)
        changed = copy.deepcopy(record)
        changed["candidate_snapshot"]["text"] = "A substituted draft."
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.save_project_record(self.namespace, self.project_id,
                kind="narrative_records", record_id=record["record_id"], value=changed)
        self.assertEqual(self.store.get_project_record(self.namespace, self.project_id,
            kind="narrative_records", record_id=record["record_id"]), record)

    def test_unsigned_or_tampered_records_cannot_be_read_or_hidden_in_listing(self):
        record = self.record()
        path = self.store.project_dir(self.namespace, self.project_id) / "narrative_records" / (record["record_id"] + ".json")
        for variant in ("tampered", "unsigned"):
            with self.subTest(variant=variant):
                altered = copy.deepcopy(record)
                if variant == "tampered":
                    altered["candidate_snapshot"]["text"] = "Not the signed draft."
                else:
                    altered.pop("integrity_hmac")
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.store.get_project_record(self.namespace, self.project_id,
                        kind="narrative_records", record_id=record["record_id"])
                with self.assertRaises(ValueError):
                    self.store.list_project_records_strict(self.namespace, self.project_id, kind="narrative_records")
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_general_record_mutator_cannot_resign_narrative_history(self):
        record = self.record()
        path = self.store.project_dir(self.namespace, self.project_id) / "narrative_records" / (record["record_id"] + ".json")
        before = path.read_bytes()
        reached_body = False
        with self.assertRaisesRegex(ValueError, "narrative_record_immutable"):
            with self.store.mutate_project_record(self.namespace, self.project_id,
                    kind="narrative_records", record_id=record["record_id"]) as mutable:
                reached_body = True
                mutable["candidate_snapshot"]["text"] = "A new draft masquerading as the old one."
        self.assertFalse(reached_body)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.get_project_record(self.namespace, self.project_id,
            kind="narrative_records", record_id=record["record_id"]), record)


if __name__ == "__main__":
    unittest.main()
