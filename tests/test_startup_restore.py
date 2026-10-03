"""Synthetic files only: single-use startup restoration never mutates live data."""
import copy
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shortdrama_mcp import startup_restore as restore


class StartupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'; self.data.mkdir()
        self.runtime = self.root / 'runtime'
        self.transfer = self.data / restore.DIRECTORY; self.transfer.mkdir()
        for name in ('.download_secret', '.record_integrity_secret'):
            (self.data / name).write_bytes(b'synthetic testing secret bytes only ' * 2)
        for name in ('member_connections.json', '.legacy_download_token_window.json', '.v055_security_epoch.json'):
            (self.data / name).write_text('{}', encoding='utf-8')
        registry = self.data / 'library_project_registry'; registry.mkdir()
        (registry / 'pointer.json').write_text('{"generation":7}', encoding='utf-8')
        with closing(sqlite3.connect(self.data / 'account_access.sqlite3')) as db, db:
            db.execute('CREATE TABLE user_roles (issuer TEXT,subject TEXT,role TEXT,enabled INTEGER,updated_at TEXT)')
            db.execute("INSERT INTO user_roles VALUES ('test','test','producer',1,'2026-09-20')")
        self.files = {'projects/namespace/SBP-test/hub.json': b'{"revision":5}',
                      'projects/namespace/SBP-test/ledger.json': b'{"signed":"unchanged"}',
                      'exports/test.tar.gz': b'export sample'}
        self.arm()

    def arm(self, files=None, extra_member=None):
        files = self.files if files is None else files
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for name, raw in files.items():
                entry = tarfile.TarInfo(name); entry.size = len(raw); entry.mode = 0o600
                archive.addfile(entry, io.BytesIO(raw))
            if extra_member is not None:
                archive.addfile(extra_member)
        payload = buffer.getvalue()
        (self.transfer / 'runtime.tar.gz').write_bytes(payload)
        self.manifest = {'contract': restore.CONTRACT, 'target_version': restore.TARGET_VERSION,
            'data_dir': str(self.data), 'runtime_root': str(self.runtime), 'old_writer_frozen': True,
            'files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
            'archive_sha256': hashlib.sha256(payload).hexdigest(),
            'protected_fingerprints': restore.protected_fingerprints(self.data)}
        self.sign()

    def sign(self):
        self.manifest['integrity_hmac'] = restore._signature(self.manifest, (self.data / '.record_integrity_secret').read_bytes())
        (self.transfer / 'manifest.json').write_bytes(restore._json_bytes(self.manifest))

    def run_restore(self):
        return restore._restore(self.data, self.runtime, version=restore.TARGET_VERSION)

    def test_complete_bytes_and_protected_roles_preserved(self):
        protected = restore.protected_fingerprints(self.data)
        receipt = self.run_restore()
        self.assertTrue(receipt['completed_before_listen'])
        self.assertEqual(restore._tree(self.runtime), self.manifest['files'])
        self.assertEqual(restore.protected_fingerprints(self.data), protected)
        self.assertTrue((self.transfer / 'manifest.consumed.json').is_file())
        self.assertFalse((self.transfer / 'manifest.json').exists())

    def test_consumed_marker_never_rolls_back_new_writes_or_cold_restart(self):
        self.run_restore()
        hub = self.runtime / next(iter(self.files))
        hub.write_bytes(b'new production revision')
        self.assertIsNone(self.run_restore())
        self.assertEqual(hub.read_bytes(), b'new production revision')
        import shutil
        shutil.rmtree(self.runtime)
        self.assertIsNone(self.run_restore())
        self.assertFalse(self.runtime.exists())

    def test_no_marker_is_zero_write_and_restrictive_settings_only_apply_when_armed(self):
        (self.transfer / 'manifest.json').unlink()
        settings = SimpleNamespace(data_dir=self.data, deployment_mode='local')
        before = restore._tree(self.data)
        self.assertIsNone(restore.restore_release_runtime_if_requested(settings))
        self.assertEqual(before, restore._tree(self.data))
        self.sign()
        with self.assertRaisesRegex(ValueError, 'explicit_library_cloud_required'):
            restore.restore_release_runtime_if_requested(settings)

    def test_modified_manifest_or_archive_rejected_without_published_files(self):
        self.manifest['files']['projects/fake/hub.json'] = '0' * 64
        (self.transfer / 'manifest.json').write_bytes(restore._json_bytes(self.manifest))
        with self.assertRaisesRegex(ValueError, 'manifest_signature'): self.run_restore()
        self.arm()
        (self.transfer / 'runtime.tar.gz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'archive_hash'): self.run_restore()
        self.assertFalse(self.runtime.exists())

    def test_changed_key_roles_or_pointer_fail_before_restore(self):
        key = self.data / '.record_integrity_secret'; original = key.read_bytes()
        key.write_bytes(b'different synthetic key' * 3)
        with self.assertRaisesRegex(ValueError, 'manifest_signature'): self.run_restore()
        key.write_bytes(original)
        with closing(sqlite3.connect(self.data / 'account_access.sqlite3')) as db, db:
            db.execute("UPDATE user_roles SET role='admin'")
        with self.assertRaisesRegex(ValueError, 'protected_state_changed'): self.run_restore()
        self.arm()
        (self.data / 'library_project_registry/pointer.json').write_text('{"generation":8}')
        with self.assertRaisesRegex(ValueError, 'protected_state_changed'): self.run_restore()
        self.assertFalse(self.runtime.exists())

    def test_bad_paths_links_and_duplicates_rejected(self):
        for name in ('../outside', '/absolute', 'projects/a/../../outside', 'projects\\evil', 'projects/./x'):
            with self.subTest(name=name):
                self.arm({**self.files, name: b'evil'})
                with self.assertRaisesRegex(ValueError, 'unsafe_archive_member'): self.run_restore()
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            member = tarfile.TarInfo('projects/evil'); member.type = kind; member.linkname = '/data/.record_integrity_secret'
            self.arm(extra_member=member)
            with self.assertRaisesRegex(ValueError, 'unsafe_archive_member'): self.run_restore()
        member = tarfile.TarInfo(next(iter(self.files))); member.size = 0
        self.arm(extra_member=member)
        with self.assertRaisesRegex(ValueError, 'unsafe_archive_member'): self.run_restore()
        self.assertFalse(self.runtime.exists())

    def test_existing_conflict_is_never_overwritten(self):
        self.runtime.mkdir(); (self.runtime / 'already-written.txt').write_bytes(b'new data')
        with self.assertRaisesRegex(ValueError, 'existing_runtime_conflict'): self.run_restore()
        self.assertEqual((self.runtime / 'already-written.txt').read_bytes(), b'new data')
        self.assertTrue((self.transfer / 'manifest.json').exists())

    def test_atomic_publish_failure_leaves_no_partial_runtime_and_retry_works(self):
        real_replace = restore.os.replace
        def fail_publish(src, dst):
            if Path(dst) == self.runtime: raise OSError('synthetic interruption')
            return real_replace(src, dst)
        with patch.object(restore.os, 'replace', side_effect=fail_publish):
            with self.assertRaises(OSError): self.run_restore()
        self.assertFalse(self.runtime.exists())
        self.assertFalse(list(self.root.glob('.shortdrama-restore-*')))
        self.assertTrue(self.run_restore()['restored'])

    def test_crash_after_publish_can_resume_only_exact_tree(self):
        with patch.object(restore, '_atomic', side_effect=OSError('synthetic receipt crash')):
            with self.assertRaises(OSError): self.run_restore()
        self.assertEqual(restore._tree(self.runtime), self.manifest['files'])
        self.assertFalse(self.run_restore()['restored'])

    def test_marker_commit_crash_retries_but_rearming_consumed_marker_rejects(self):
        real_replace = restore.os.replace
        def fail_marker(src, dst):
            if Path(dst).name == 'manifest.consumed.json': raise OSError('synthetic marker crash')
            return real_replace(src, dst)
        with patch.object(restore.os, 'replace', side_effect=fail_marker):
            with self.assertRaises(OSError): self.run_restore()
        self.assertTrue((self.transfer / 'restore-receipt.json').exists())
        self.assertFalse(self.run_restore()['restored'])
        self.sign()
        with self.assertRaisesRegex(ValueError, 'already_consumed'): self.run_restore()

    def test_limits_and_wrong_release_fail_closed(self):
        with patch.object(restore, 'MAX_EXPANDED_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'archive_size'): self.run_restore()
        with self.assertRaisesRegex(ValueError, 'manifest_binding'):
            restore._restore(self.data, self.runtime, version='0.5.46')
        self.assertFalse(self.runtime.exists())

    def test_v0560_r3_binding_rejects_signed_previous_release(self):
        self.assertEqual((restore.TARGET_VERSION, restore.CONTRACT, restore.DIRECTORY),
                         ('0.5.60', 'library-runtime-transfer-0560-r3-v1', '.upgrade-0560-r3'))
        self.manifest.update(target_version='0.5.60', contract='library-runtime-transfer-0560-r2-v1')
        self.sign()
        before = restore._tree(self.data)
        with self.assertRaisesRegex(ValueError, 'manifest_binding'):
            self.run_restore()
        self.assertFalse(self.runtime.exists())
        self.assertEqual(restore._tree(self.data), before)

    def test_previous_release_archive_and_markers_remain_untouched(self):
        previous = self.data / '.upgrade-0560-r2'
        previous.mkdir()
        for name in ('runtime.tar.gz', 'manifest.json', 'manifest.consumed.json', 'restore-receipt.json'):
            (previous / name).write_bytes(b'historical release bytes: ' + name.encode())
        before = restore._tree(previous)
        self.assertTrue(self.run_restore()['restored'])
        self.assertEqual(restore._tree(previous), before)
        self.assertEqual(restore._tree(self.runtime), self.manifest['files'])
        import shutil
        shutil.rmtree(self.runtime)
        self.assertIsNone(self.run_restore())
        self.assertFalse(self.runtime.exists())
        self.assertEqual(restore._tree(previous), before)

    def test_startup_gate_precedes_create_and_listener_on_both_transports(self):
        import main
        settings = SimpleNamespace(host='127.0.0.1', port=12345)
        for argv in (['server/main.py', '--http'], ['server/main.py', '--stdio']):
            with self.subTest(argv=argv), patch.object(main.sys, 'argv', argv), \
                    patch.object(main.Settings, 'from_env', return_value=settings), \
                    patch.object(main, 'restore_release_runtime_if_requested', side_effect=ValueError('gate blocked')), \
                    patch.object(main, 'create_mcp') as create:
                with self.assertRaisesRegex(ValueError, 'gate blocked'): main.main()
                create.assert_not_called()

    def test_maintenance_transfer_has_separate_binding_and_preserves_old_receipt(self):
        self.run_restore()
        old = restore._tree(self.transfer)
        self.transfer = self.data / restore.MAINTENANCE_DIRECTORY
        self.transfer.mkdir()
        self.arm()
        self.manifest['contract'] = restore.MAINTENANCE_CONTRACT
        self.sign()
        result = restore._restore(self.data, self.runtime, version=restore.TARGET_VERSION,
            directory=restore.MAINTENANCE_DIRECTORY, contract=restore.MAINTENANCE_CONTRACT)
        self.assertFalse(result['restored'])
        self.assertEqual(restore._tree(self.data / restore.DIRECTORY), old)
        with self.assertRaisesRegex(ValueError, 'undeclared_transfer'):
            restore._restore(self.data, self.runtime, version=restore.TARGET_VERSION,
                directory='../escape', contract='custom')


if __name__ == '__main__': unittest.main()
