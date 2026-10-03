"""Synthetic deletion safety, concurrency and lifecycle checks, never live data."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filelock import FileLock, Timeout
from shortdrama_mcp.external_storage import ExternalProjectStorage
from shortdrama_mcp.storage import ProjectStore
from shortdrama_mcp.retention import RetentionWorker, ARCHIVE_AGE, EXPORT_GRACE
from shortdrama_mcp.startup_restore import _json_bytes, _signature


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = ProjectStore(self.base / 'data', 'https://example.invalid')
        self.external = ExternalProjectStorage(SimpleNamespace(store=self.store),
            SimpleNamespace(public_base_url='https://example.invalid', deployment_mode='cloud'),
            mode='library', runtime_root=self.base / 'runtime')
        self.worker = RetentionWorker(self.external)
        self.secret = (self.store.root / '.record_integrity_secret').read_bytes()
        self.now = time.time()
        self.namespace, self.project = 'synthetic-user', 'SBP-test'
        self.folder = self.store.project_dir(self.namespace, self.project)
        self.folder.mkdir(parents=True)
        (self.folder / 'hub.json').write_bytes(b'{"source":"preserve"}')

    def signed(self, path, value):
        value['integrity_hmac'] = _signature(value, self.secret)
        path.write_bytes(_json_bytes(value))

    def archive(self, version, *, pending=False, invalid=False):
        directory = self.store.root / f'.upgrade-{version}'
        directory.mkdir()
        archive = directory / 'runtime.tar.gz'
        archive.write_bytes(f'synthetic archive {version}'.encode())
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {'files': {'projects/ns/hub.json': 'test'}, 'contract': 'synthetic',
                    'target_version': str(version), 'archive_sha256': sha}
        self.signed(directory / 'manifest.consumed.json', manifest)
        receipt = {'status': 'pass', 'protected_state_unchanged': True,
                   'completed_before_listen': True, 'existing_files_overwritten': 0,
                   'files_verified': 1, 'manifest_sha256': hashlib.sha256(_json_bytes(manifest)).hexdigest(),
                   'archive_sha256': sha, 'contract': 'synthetic', 'target_version': str(version)}
        self.signed(directory / 'restore-receipt.json', receipt)
        os.utime(archive, (self.now - ARCHIVE_AGE * 2 + version, self.now - ARCHIVE_AGE * 2 + version))
        if pending:
            (directory / 'manifest.json').write_text('pending')
        if invalid:
            archive.write_bytes(b'corrupted')
        return archive

    def test_preview_keeps_every_byte_then_apply_keeps_latest_two_and_receipts(self):
        paths = [self.archive(n) for n in range(4)]
        before = {str(p): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        preview = self.worker.run_once(dry_run=True, now=self.now)
        self.assertEqual(len(preview['candidates']), 2)
        for path, raw in before.items():
            self.assertEqual(Path(path).read_bytes(), raw)
        result = self.worker.run_once(now=self.now)
        self.assertEqual(len(result['deleted']), 2)
        self.assertEqual([p.exists() for p in paths], [False, False, True, True])
        self.assertEqual((self.folder / 'hub.json').read_bytes(), b'{"source":"preserve"}')
        for p in paths:
            self.assertTrue((p.parent / 'manifest.consumed.json').is_file())
            self.assertTrue((p.parent / 'restore-receipt.json').is_file())
        self.assertEqual(self.worker.run_once(now=self.now)['deleted'], [])

    def test_pending_or_invalid_archives_cannot_be_used_as_newer_recovery_proof(self):
        old = self.archive(0)
        newer = self.archive(1)
        self.archive(2, invalid=True)
        self.assertEqual(self.worker.run_once(now=self.now)['deleted'], [])
        self.archive(3, pending=True)
        self.assertEqual(self.worker.run_once(now=self.now)['deleted'], [])
        self.assertTrue(old.exists() and newer.exists())

    def test_active_export_held_then_expired_identical_copy_removed_after_restart(self):
        export = self.external.export_snapshot(self.namespace, self.project)
        path = Path(export['path'])
        self.assertEqual(self.worker.run_once(now=self.now)['deleted'], [])
        self.external._exports.clear()  # signed descriptor survives a process restart
        result = self.worker.run_once(now=export['expires_epoch'] + EXPORT_GRACE + 1)
        self.assertEqual([x['path'] for x in result['deleted']], [str(path)])
        self.assertTrue((self.folder / 'hub.json').exists())
        self.assertTrue(path.with_suffix('.json').exists())

    def test_only_old_export_or_changed_project_kept_and_unsigned_legacy_held(self):
        export = self.external.export_snapshot(self.namespace, self.project)
        path = Path(export['path'])
        (self.folder / 'hub.json').write_bytes(b'new project revision')
        legacy = self.external.exports_dir / ('LEX-' + 'a' * 24 + '.tgz')
        legacy.write_bytes(b'unknown old copy')
        result = self.worker.run_once(now=export['expires_epoch'] + EXPORT_GRACE + 1)
        self.assertEqual(result['deleted'], [])
        self.assertTrue(path.exists() and legacy.exists())
        self.assertTrue(any(x['reason'] == 'only_copy_or_unsaved_changes' for x in result['skipped']))

    def test_tampered_descriptor_not_accepted(self):
        export = self.external.export_snapshot(self.namespace, self.project)
        meta = Path(export['path']).with_suffix('.json')
        value = json.loads(meta.read_text()); value['expires_epoch'] = 0
        meta.write_text(json.dumps(value))
        result = self.worker.run_once(now=self.now + 2 * EXPORT_GRACE)
        self.assertEqual(result['deleted'], [])
        self.assertTrue(any(x['reason'] == 'invalid_record_signature' for x in result['skipped']))

    def test_unlink_error_recorded_not_claimed_as_deleted(self):
        paths = [self.archive(n) for n in range(3)]
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == paths[0]:
                raise OSError('synthetic permission failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail):
            result = self.worker.run_once(now=self.now)
        self.assertEqual(result['summary']['status'], 'error')
        self.assertEqual(result['summary']['deleted_bytes'], 0)
        self.assertEqual(result['summary']['errors'], 1)
        self.assertTrue(paths[0].exists())

    def test_singleton_lock_and_start_stop(self):
        self.worker.root.mkdir()
        with FileLock(str(self.worker.root / 'run.lock')):
            with self.assertRaises(Timeout):
                self.worker.run_once(now=self.now)
        self.worker.start(); first = self.worker.thread
        self.worker.start()
        self.assertIs(first, self.worker.thread)
        self.assertTrue(self.worker.status()['worker_alive'])
        self.assertAlmostEqual(self.worker.status()['next_run_epoch'], time.time() + 300, delta=2)
        self.worker.stop()
        self.assertFalse(first.is_alive())

    def test_symlink_archive_never_follows_target(self):
        paths = [self.archive(n) for n in range(3)]
        outside = self.base / 'precious'; outside.write_bytes(b'do not delete')
        paths[0].unlink()
        try:
            paths[0].symlink_to(outside)
        except OSError:
            self.skipTest('OS does not permit symlinks')
        self.assertEqual(self.worker.run_once(now=self.now)['deleted'], [])
        self.assertEqual(outside.read_bytes(), b'do not delete')


if __name__ == '__main__':
    unittest.main()
