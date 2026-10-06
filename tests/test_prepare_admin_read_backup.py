"""Synthetic backup/restore checks; no live paths or processes are used."""
from contextlib import closing, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from shortdrama_mcp import startup_restore as restore

spec = importlib.util.spec_from_file_location('backup_prepare',
    Path(__file__).resolve().parents[1] / 'scripts/prepare_admin_read_backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class PrepareAdminReadBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.runtime = self.root / 'runtime'
        self.files = {'projects/a/SBP-a/hub.json': b'{"revision":7}',
                      'projects/b/SBP-b/ledger.json': b'unchanged history' * 50,
                      'exports/proof.tgz': bytes(range(256))}
        for name, raw in self.files.items():
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        for name in ('.download_secret', '.record_integrity_secret'):
            (self.data / name).write_bytes(b'only synthetic key material for tests' * 2)
        for name in ('member_connections.json', '.legacy_download_token_window.json', '.v055_security_epoch.json'):
            (self.data / name).write_text('{}')
        with closing(sqlite3.connect(self.data / 'account_access.sqlite3')) as db, db:
            db.execute('CREATE TABLE user_roles (issuer,subject,role,enabled,updated_at)')
            db.execute("INSERT INTO user_roles VALUES ('test','owner','admin',1,'today')")
        self.destination = self.data / backup.DIRECTORY

    def prepare(self):
        with redirect_stdout(io.StringIO()):
            return backup.prepare(self.data, self.runtime)

    def test_verified_draft_does_not_arm_and_can_restore_exact_bytes(self):
        original = restore._tree(self.runtime)
        protected = restore.protected_fingerprints(self.data)
        result = self.prepare()
        self.assertEqual(result['status'], 'PREBACKUP_VERIFIED_NOT_ARMED')
        self.assertFalse((self.destination / 'manifest.json').exists())
        self.assertEqual(restore._tree(self.runtime), original)
        self.assertEqual(restore.protected_fingerprints(self.data), protected)
        draft = json.loads((self.destination / 'manifest.draft.json').read_text())
        self.assertFalse(draft['old_writer_frozen'])
        # Test-only: there is no writer in this fixture. Simulate a future, verified arming step.
        draft['old_writer_frozen'] = True
        draft['integrity_hmac'] = restore._signature(draft, (self.data / '.record_integrity_secret').read_bytes())
        (self.destination / 'manifest.json').write_bytes(restore._json_bytes(draft))
        shutil.rmtree(self.runtime)
        receipt = restore._restore(self.data, self.runtime, version='0.5.62',
                                   directory=backup.DIRECTORY, contract=backup.CONTRACT)
        self.assertTrue(receipt['restored'])
        self.assertEqual(restore._tree(self.runtime), original)
        self.assertEqual(restore.protected_fingerprints(self.data), protected)

    def test_existing_directory_and_backups_are_never_overwritten(self):
        self.prepare()
        original = restore._tree(self.destination)
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(restore._tree(self.destination), original)

    def test_live_source_change_refuses_draft_and_cleans_only_own_partial(self):
        real_inventory = backup.inventory
        calls = 0
        def changing_inventory(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.runtime / 'projects/a/SBP-a/hub.json').write_bytes(b'{"revision":8}')
            return real_inventory(root)
        with patch.object(backup, 'inventory', side_effect=changing_inventory):
            with self.assertRaisesRegex(ValueError, 'source_changed'):
                self.prepare()
        self.assertFalse(self.destination.exists())
        self.assertEqual((self.runtime / 'projects/a/SBP-a/hub.json').read_bytes(), b'{"revision":8}')

    def test_compressed_limit_refuses_without_changing_source(self):
        original = restore._tree(self.runtime)
        with patch.object(backup, 'MAX_ARCHIVE', 32):
            with self.assertRaisesRegex(ValueError, 'compressed_capacity'):
                self.prepare()
        self.assertFalse(self.destination.exists())
        self.assertEqual(restore._tree(self.runtime), original)

    def test_symlink_or_active_mount_directory_is_rejected(self):
        (self.runtime / 'projects/a/linked').symlink_to(self.runtime / 'exports')
        with self.assertRaisesRegex(ValueError, 'unsafe_or_active'):
            self.prepare()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
