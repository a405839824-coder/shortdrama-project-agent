"""Real signed archives and SQLite gates, using synthetic temporary state only."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch

import test_prepare_admin_read_backup as fixtures
from shortdrama_mcp import startup_restore as restore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_upgrade_0565_backup as prepare
import arm_upgrade_0565_backup as arm


class Upgrade0565BackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PrepareAdminReadBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data, self.runtime = self.fixture.data, self.fixture.runtime
        self.target = self.data / prepare.DIRECTORY
        with self.database() as db:
            db.execute('CREATE TABLE access_audit (id INTEGER PRIMARY KEY, occurred_at, outcome, trace_id)')
        self.silent = redirect_stdout(io.StringIO())
        self.silent.__enter__()
        self.addCleanup(self.silent.__exit__, None, None, None)

    def database(self):
        db = sqlite3.connect(self.data / 'account_access.sqlite3')
        self.addCleanup(db.close)
        return db

    def gate_present(self):
        with self.database() as db:
            return db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                              (arm.GATE,)).fetchone() is not None

    def prepare(self):
        return prepare.prepare(self.data, self.runtime)

    def publish(self):
        self.prepare()
        with arm.frozen_transfer(self.data, self.runtime, ''):
            return arm.mark_publication(self.data, self.runtime)

    def restore(self):
        return restore._restore(self.data, self.runtime, version=prepare.TARGET_VERSION,
                                directory=prepare.DIRECTORY, contract=prepare.CONTRACT)

    def verify(self, reopen=False):
        with patch('shortdrama_mcp.__version__', '0.5.65'):
            return arm.verify_release(self.data, self.runtime, reopen=reopen)

    def test_contracts_and_gate_match_between_old_container_scripts_and_new_image(self):
        self.assertEqual((prepare.DIRECTORY, prepare.CONTRACT, prepare.TARGET_VERSION),
                         (restore.PRODUCTION_GROUNDING_DIRECTORY, restore.PRODUCTION_GROUNDING_CONTRACT,
                          restore.PRODUCTION_GROUNDING_VERSION))
        self.assertEqual(arm.GATE_SQL, restore.PRODUCTION_GROUNDING_GATE_SQL)
        self.assertEqual(prepare.MAX_EXPANDED, restore.PRODUCTION_GROUNDING_MAX_EXPANDED_BYTES)

    def test_prepare_is_unarmed_and_existing_snapshot_is_never_overwritten(self):
        original = restore._tree(self.runtime)
        result = self.prepare()
        self.assertEqual(result['status'], 'PREBACKUP_VERIFIED_NOT_ARMED')
        self.assertFalse((self.target / 'manifest.json').exists())
        self.assertFalse(self.gate_present())
        self.assertEqual(restore._tree(self.runtime), original)
        draft = json.loads((self.target / 'manifest.draft.json').read_text())
        self.assertEqual(draft['target_version'], '0.5.65')
        self.assertFalse(draft['old_writer_frozen'])
        before = restore._tree(self.target)
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(restore._tree(self.target), before)

    def test_prepare_changed_source_and_size_fail_without_arming_or_deleting_source(self):
        real_inventory = prepare.inventory
        calls = 0
        def changing(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.runtime / 'projects/a/SBP-a/hub.json').write_text('{"revision":8}')
            return real_inventory(root)
        with patch.object(prepare, 'inventory', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'source_changed'):
                self.prepare()
        self.assertFalse(self.target.exists())
        self.assertIn('8', (self.runtime / 'projects/a/SBP-a/hub.json').read_text())
        with patch.object(prepare, 'MAX_ARCHIVE', 32):
            with self.assertRaisesRegex(ValueError, 'compressed_capacity'):
                self.prepare()
        self.assertFalse(self.target.exists())

    def test_gate_blocks_starts_allows_terminal_events_and_abort_disarms_first(self):
        self.prepare()
        with arm.frozen_transfer(self.data, self.runtime, '') as value:
            self.assertEqual(value['status'], 'ARMED_WRITER_FROZEN')
            with self.database() as db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'upgrade_in_progress'):
                    db.execute("INSERT INTO access_audit VALUES (1,'today','started','new')")
                db.execute("INSERT INTO access_audit VALUES (2,'today','success','old')")
        self.assertFalse(self.gate_present())
        self.assertFalse((self.target / 'manifest.json').exists())
        self.assertTrue((self.target / 'manifest.aborted.json').is_file())

    def test_source_changed_or_inflight_call_never_arms(self):
        self.prepare()
        with self.database() as db:
            db.execute("INSERT INTO access_audit VALUES (1,'today','started','pending')")
        with self.assertRaisesRegex(ValueError, 'not_drained'):
            with arm.frozen_transfer(self.data, self.runtime, '', drain_seconds=0):
                self.fail('inflight call armed')
        self.assertFalse(self.gate_present())
        with self.database() as db:
            db.execute("INSERT INTO access_audit VALUES (2,'today','success','pending')")
        (self.runtime / 'projects/a/SBP-a/hub.json').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'runtime_changed'):
            with arm.frozen_transfer(self.data, self.runtime, ''):
                self.fail('changed runtime armed')
        self.assertFalse(self.gate_present())
        self.assertFalse((self.target / 'manifest.json').exists())

    def test_existing_gate_is_never_removed(self):
        self.prepare()
        with self.database() as db:
            db.execute(arm.GATE_SQL)
        with self.assertRaises(sqlite3.OperationalError):
            with arm.frozen_transfer(self.data, self.runtime, ''):
                self.fail('existing gate replaced')
        self.assertTrue(self.gate_present())

    def test_mark_publication_requires_frozen_gate_and_cannot_mark_twice(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'signed_record_missing'):
            arm.mark_publication(self.data, self.runtime)
        with arm.frozen_transfer(self.data, self.runtime, ''):
            arm.mark_publication(self.data, self.runtime)
            with self.assertRaisesRegex(ValueError, 'already_marked'):
                arm.mark_publication(self.data, self.runtime)
        self.assertTrue(self.gate_present())
        self.assertTrue((self.target / 'manifest.json').is_file())

    def test_mark_publication_rejects_a_dead_or_reused_freeze_process(self):
        self.prepare()
        with arm.frozen_transfer(self.data, self.runtime, ''):
            real_identity = arm._process_identity()
            with patch.object(arm, '_process_identity', return_value={**real_identity, 'start_ticks': -1}):
                with self.assertRaisesRegex(ValueError, 'freeze_process_missing_or_changed'):
                    arm.mark_publication(self.data, self.runtime)
            self.assertFalse((self.target / 'deployment-started').exists())
            self.assertTrue(self.gate_present())
        self.assertFalse(self.gate_present())

    def test_full_fresh_restore_preserves_bytes_and_only_explicit_verified_reopen_removes_gate(self):
        before = restore._tree(self.runtime)
        protected = restore.protected_fingerprints(self.data)
        self.publish()
        shutil.rmtree(self.runtime)
        self.assertTrue(self.restore()['restored'])
        self.assertEqual(restore._tree(self.runtime), before)
        self.assertEqual(restore.protected_fingerprints(self.data), protected)
        self.assertTrue(self.gate_present())
        self.assertEqual(self.verify()['status'], 'RESTORE_VERIFIED_GATE_CLOSED')
        self.assertTrue(self.gate_present())
        self.assertEqual(self.verify(reopen=True)['status'], 'RESTORE_VERIFIED_DISPATCH_REOPENED')
        self.assertFalse(self.gate_present())
        with self.database() as db:
            db.execute("INSERT INTO access_audit VALUES (1,'today','started','after-verify')")

    def test_wrong_restore_version_and_verifier_version_keep_gate_closed(self):
        self.publish()
        shutil.rmtree(self.runtime)
        with self.assertRaisesRegex(ValueError, 'manifest_binding'):
            restore._restore(self.data, self.runtime, version='0.5.63',
                             directory=prepare.DIRECTORY, contract=prepare.CONTRACT)
        self.assertTrue(self.gate_present())
        self.assertFalse(self.runtime.exists())
        self.restore()
        with patch('shortdrama_mcp.__version__', '0.5.63'):
            with self.assertRaisesRegex(ValueError, 'target_version'):
                arm.verify_release(self.data, self.runtime, reopen=True)
        self.assertTrue(self.gate_present())

    def test_publication_exception_and_failed_restore_never_reopen_dispatch(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeError, 'deployment failed'):
            with arm.frozen_transfer(self.data, self.runtime, ''):
                arm.mark_publication(self.data, self.runtime)
                raise RuntimeError('deployment failed')
        self.assertTrue(self.gate_present())
        (self.target / 'runtime.tar.gz').write_bytes(b'damaged archive')
        shutil.rmtree(self.runtime)
        with self.assertRaisesRegex(ValueError, 'archive_hash'):
            self.restore()
        with self.assertRaisesRegex(ValueError, 'pending_transfer'):
            self.verify(reopen=True)
        self.assertTrue(self.gate_present())
        with self.database() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO access_audit VALUES (1,'today','started','still-blocked')")

    def test_modified_receipt_or_runtime_does_not_reopen_dispatch(self):
        self.publish()
        self.restore()
        receipt_path = self.target / 'restore-receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt['files_verified'] += 1
        receipt['integrity_hmac'] = restore._signature(receipt, (self.data / '.record_integrity_secret').read_bytes())
        receipt_path.write_bytes(restore._json_bytes(receipt))
        with self.assertRaisesRegex(ValueError, 'receipt_or_publication_binding'):
            self.verify(reopen=True)
        self.assertTrue(self.gate_present())
        receipt['files_verified'] -= 1
        receipt['integrity_hmac'] = restore._signature(receipt, (self.data / '.record_integrity_secret').read_bytes())
        receipt_path.write_bytes(restore._json_bytes(receipt))
        (self.runtime / 'projects/a/SBP-a/hub.json').write_text('new write')
        with self.assertRaisesRegex(ValueError, 'runtime_changed'):
            self.verify(reopen=True)
        self.assertTrue(self.gate_present())

    def test_missing_or_changed_gate_is_rejected_before_restore(self):
        self.publish()
        with self.database() as db:
            db.execute(f'DROP TRIGGER {arm.GATE}')
            db.execute(f'CREATE TRIGGER {arm.GATE} BEFORE INSERT ON access_audit BEGIN SELECT 1; END')
        with self.assertRaisesRegex(ValueError, 'maintenance_gate'):
            self.restore()
        self.assertTrue(self.gate_present())


if __name__ == '__main__':
    unittest.main()
