"""Exercise the real SQLite gate and byte verification with synthetic state."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest

import test_prepare_admin_read_backup as preparation_tests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import arm_admin_read_backup as arm


class ArmAdminReadBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation_tests.PrepareAdminReadBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data = self.fixture.data
        self.runtime = self.fixture.runtime
        self.target = self.fixture.destination
        (self.data / 'retention').mkdir()
        with self.database() as db:
            db.execute('CREATE TABLE access_audit (id INTEGER PRIMARY KEY, occurred_at, outcome, trace_id)')
        self.fixture.prepare()

    def database(self):
        db = sqlite3.connect(self.data / 'account_access.sqlite3')
        self.addCleanup(db.close)
        return db

    def gate_present(self):
        with self.database() as db:
            return db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name=?", (arm.GATE,)).fetchone() is not None

    def test_gate_blocks_new_dispatch_allows_drain_and_abort_disarms_first(self):
        with redirect_stdout(io.StringIO()), arm.frozen_transfer(self.data, self.runtime, '') as result:
            self.assertEqual(result['status'], 'ARMED_WRITER_FROZEN')
            with self.database() as db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'upgrade_in_progress'):
                    db.execute("INSERT INTO access_audit VALUES (1,'today','started','new')")
                db.execute("INSERT INTO access_audit VALUES (2,'today','success','existing')")
            self.assertTrue((self.target / 'manifest.json').is_file())
        self.assertFalse(self.gate_present())
        self.assertFalse((self.target / 'manifest.json').exists())
        self.assertTrue((self.target / 'manifest.aborted.json').is_file())
        with self.database() as db:
            db.execute("INSERT INTO access_audit VALUES (3,'today','started','resumed')")

    def test_changed_source_is_not_armed_and_gate_is_removed(self):
        (self.runtime / 'projects/a/SBP-a/hub.json').write_text('{"revision":8}')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'runtime_changed'):
            with arm.frozen_transfer(self.data, self.runtime, ''):
                self.fail('changed source armed')
        self.assertFalse(self.gate_present())
        self.assertFalse((self.target / 'manifest.json').exists())

    def test_inflight_dispatch_prevents_arming(self):
        with self.database() as db:
            db.execute("INSERT INTO access_audit VALUES (1,'today','started','pending')")
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'not_drained'):
            with arm.frozen_transfer(self.data, self.runtime, '', drain_seconds=0):
                self.fail('inflight source armed')
        self.assertFalse(self.gate_present())

    def test_publication_keeps_gate_and_signed_manifest_for_new_container(self):
        with redirect_stdout(io.StringIO()), arm.frozen_transfer(self.data, self.runtime, ''):
            (self.target / 'deployment-started').write_text('synthetic test')
        self.assertTrue(self.gate_present())
        manifest = json.loads((self.target / 'manifest.json').read_text())
        self.assertTrue(manifest['old_writer_frozen'])
        self.assertEqual(manifest['maintenance_gate'], arm.GATE)

    def test_existing_gate_is_never_removed(self):
        with self.database() as db:
            db.execute(arm.GATE_SQL)
        with self.assertRaises(sqlite3.OperationalError):
            with arm.frozen_transfer(self.data, self.runtime, ''):
                self.fail('existing gate accepted')
        self.assertTrue(self.gate_present())


if __name__ == '__main__':
    unittest.main()
