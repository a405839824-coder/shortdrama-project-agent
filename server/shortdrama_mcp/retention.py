"""Bounded housekeeping of *copies*, never project, source or audit records.

An expired URL is not a deletion proof. Archives need a signed successful
restore and two newer recovery copies; exports need another verified copy.
Unknown, active, changed or unsaved data stays in place and is reported.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time

from filelock import FileLock, Timeout

from .startup_restore import _atomic, _json_bytes, _signature

LOG = logging.getLogger(__name__)
INTERVAL = 6 * 3600
ARCHIVE_AGE = 7 * 86400
EXPORT_GRACE = 24 * 3600
KEEP_ARCHIVES = 2


def file_sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def signed_read(path: Path, secret: bytes) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing_or_symlink_record')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not hmac.compare_digest(
        str(value.get('integrity_hmac', '')), _signature(value, secret)
    ):
        raise ValueError('invalid_record_signature')
    return value


class RetentionWorker:
    def __init__(self, external):
        self.external = external
        self.data = external.store.root
        self.root = self.data / 'retention'
        self.stop_event = threading.Event()
        self.thread = None
        self.next_run_epoch = None
        self.enabled = (external.settings.deployment_mode == 'cloud' and
                        os.environ.get('SHORTDRAMA_RETENTION_ENABLED', 'true').lower() == 'true')

    def status(self) -> dict:
        try:
            value = json.loads((self.root / 'status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = {'status': 'not_run'}
        # No paths, identities, source contents or secrets in /health.
        summary = {**{k: value[k] for k in ('status', 'last_run_epoch', 'next_run_epoch',
                  'deleted_files', 'deleted_bytes', 'skipped_files', 'errors',
                  'disk_free_bytes', 'disk_used_percent') if k in value},
                'enabled': self.enabled, 'worker_alive': bool(self.thread and self.thread.is_alive()),
                'interval_seconds': INTERVAL, 'archive_min_age_days': 7,
                'archive_keep_latest': KEEP_ARCHIVES, 'export_grace_hours': 24,
                'project_auto_delete': False}
        if self.thread and self.thread.is_alive():
            summary['next_run_epoch'] = self.next_run_epoch
        return summary

    def _archive_candidates(self, now: float, report: dict, secret: bytes):
        directories = sorted(self.data.glob('.upgrade-*'))
        if any((d / 'manifest.json').exists() for d in directories):
            report['skipped'].append({'type': 'upgrade_archives', 'reason': 'pending_restore'})
            return
        valid = []
        for directory in directories:
            archive = directory / 'runtime.tar.gz'
            if not archive.exists():
                continue
            try:
                if (directory.is_symlink() or archive.is_symlink() or
                    directory.resolve().parent != self.data.resolve()):
                    raise ValueError('unsafe_archive_path')
                manifest = signed_read(directory / 'manifest.consumed.json', secret)
                receipt = signed_read(directory / 'restore-receipt.json', secret)
                if (receipt.get('status') != 'pass' or
                    receipt.get('protected_state_unchanged') is not True or
                    receipt.get('completed_before_listen') is not True or
                    receipt.get('existing_files_overwritten') != 0 or
                    receipt.get('files_verified') != len(manifest['files']) or
                    receipt.get('manifest_sha256') != hashlib.sha256(_json_bytes(manifest)).hexdigest() or
                    manifest.get('archive_sha256') != receipt.get('archive_sha256') or
                    manifest.get('contract') != receipt.get('contract') or
                    manifest.get('target_version') != receipt.get('target_version') or
                    file_sha(archive) != manifest.get('archive_sha256')):
                    raise ValueError('unverified_restore_archive')
                valid.append((archive.stat().st_mtime, archive, manifest['archive_sha256']))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report['skipped'].append({'path': str(archive), 'reason': str(exc)})
        valid.sort(key=lambda row: (row[0], str(row[1])), reverse=True)
        for index, (mtime, path, sha) in enumerate(valid):
            if index < KEEP_ARCHIVES or now - mtime < ARCHIVE_AGE:
                report['skipped'].append({'path': str(path), 'reason': 'recovery_retention_window'})
            else:
                yield path, sha, 'consumed_restore_with_two_newer_verified_archives'

    def _export_candidates(self, now: float, report: dict, secret: bytes):
        # Sidecars are signed at export time. Unknown legacy exports stay intact.
        for path in sorted(self.external.exports_dir.glob('LEX-*.tgz')):
            try:
                if path.is_symlink() or not re.fullmatch(r'LEX-[0-9a-f]{24}\.tgz', path.name):
                    raise ValueError('unsafe_export_path')
                meta = signed_read(path.with_suffix('.json'), secret)
                if (meta.get('export_id') != path.stem or
                    meta.get('namespace_key') != self.external.store.namespace_key(meta['namespace']) or
                    path.stat().st_size != meta.get('size_bytes')):
                    raise ValueError('export_metadata_binding')
                if now < float(meta['expires_epoch']) + EXPORT_GRACE:
                    raise ValueError('export_grace_period')
                namespace, project = meta['namespace'], meta['project_id']
                with self.external.store.project_write_lock(namespace, project):
                    if file_sha(path) != meta['bundle_sha256']:
                        raise ValueError('export_content_changed')
                    try:
                        registry = self.external._read_registry(namespace, project)
                    except ValueError:
                        registry = {}
                    durable = (registry.get('bundle_sha256') == meta['bundle_sha256'] and
                               registry.get('tree_sha256') == meta['tree_sha256'])
                    mounted = self.external._mounted_project_dir(namespace, project)
                    duplicate = (mounted.is_dir() and
                                 self.external.tree_manifest(mounted)[0] == meta['tree_sha256'])
                    if not durable and not duplicate:
                        raise ValueError('only_copy_or_unsaved_changes')
                    # Hold the project lock across yield/deletion so unmount cannot race it.
                    yield path, meta['bundle_sha256'], ('verified_library_pointer' if durable else 'identical_mounted_copy')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report['skipped'].append({'path': str(path), 'reason': str(exc)})

    def run_once(self, *, dry_run: bool = False, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink() or self.data.is_symlink():
            raise ValueError('unsafe_retention_root')
        with FileLock(str(self.root / 'run.lock'), timeout=0):
            secret = (self.data / '.record_integrity_secret').read_bytes()
            report = {'contract': 'verified-copy-retention-v1', 'started_epoch': now,
                      'dry_run': dry_run, 'deleted': [], 'candidates': [], 'skipped': [], 'failures': []}
            if self.external.runtime_root.is_symlink() or self.external.exports_dir.is_symlink():
                raise ValueError('unsafe_runtime_root')
            for candidates in (self._archive_candidates(now, report, secret),
                               self._export_candidates(now, report, secret)):
                for path, sha, reason in candidates:
                    item = {'path': str(path), 'sha256': sha, 'bytes': path.stat().st_size, 'reason': reason}
                    report['candidates'].append(item)
                    if dry_run:
                        continue
                    try:
                        # Fail closed if the proof changed since planning; write intent before unlink.
                        if path.is_symlink() or file_sha(path) != sha:
                            raise ValueError('candidate_changed')
                        if path.name == 'runtime.tar.gz' and any(self.data.glob('.upgrade-*/manifest.json')):
                            raise ValueError('restore_started_during_cleanup')
                        intent = {**item, 'attempt_epoch': now, 'state': 'deleting'}
                        intent['integrity_hmac'] = _signature(intent, secret)
                        receipt_path = self.root / (hashlib.sha256(str(path).encode()).hexdigest() + '.json')
                        _atomic(receipt_path, _json_bytes(intent))
                        path.unlink()
                        intent['state'] = 'deleted'
                        intent['integrity_hmac'] = _signature(intent, secret)
                        _atomic(receipt_path, _json_bytes(intent))
                        report['deleted'].append(item)
                        if path.suffix == '.tgz':
                            self.external._exports.pop(path.stem, None)
                            # Keep the tiny signed export descriptor as recovery/accounting evidence.
                    except (OSError, ValueError) as exc:
                        report['failures'].append({'path': str(path), 'reason': str(exc)})
            usage = shutil.disk_usage(self.data)
            summary = {'status': 'error' if report['failures'] else ('warning' if usage.used / usage.total >= .85 else 'ok'),
                       'last_run_epoch': now, 'next_run_epoch': time.time() + INTERVAL,
                       'deleted_files': len(report['deleted']),
                       'deleted_bytes': sum(item['bytes'] for item in report['deleted']),
                       'skipped_files': len(report['skipped']), 'errors': len(report['failures']),
                       'disk_free_bytes': usage.free, 'disk_used_percent': round(100 * usage.used / usage.total, 2),
                       'project_protection': 'No automatic project deletion without a request-level idle lease; existing signed-snapshot unmount remains required.'}
            report['summary'] = summary
            _atomic(self.root / ('preview.json' if dry_run else 'last-run.json'), _json_bytes(report))
            if not dry_run:
                _atomic(self.root / 'status.json', _json_bytes(summary))
                LOG.log(logging.WARNING if summary['status'] != 'ok' else logging.INFO, 'retention %s', json.dumps(summary))
            return report

    def start(self):
        if not self.enabled or (self.thread and self.thread.is_alive()):
            return
        def loop():
            # Allow startup restoration/readback to finish before any deletion.
            delay = 300
            while not self.stop_event.wait(delay):
                try:
                    self.run_once()
                except Timeout:
                    LOG.warning('retention another worker owns the cleanup lock')
                except Exception:
                    LOG.exception('retention run failed; no successful cleanup claimed')
                    with contextlib.suppress(OSError):
                        _atomic(self.root / 'status.json', _json_bytes({'status': 'error',
                            'last_run_epoch': time.time(), 'next_run_epoch': time.time() + INTERVAL,
                            'errors': 1}))
                delay = INTERVAL
                self.next_run_epoch = time.time() + delay
        self.stop_event.clear()
        self.next_run_epoch = time.time() + 300
        self.thread = threading.Thread(target=loop, name='verified-copy-retention', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)
