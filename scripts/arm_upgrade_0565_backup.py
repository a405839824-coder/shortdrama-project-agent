"""Freeze, publish, and explicitly verify/reopen the single 0.5.65 transfer.

Run ``freeze`` in the old container and leave it running. ``mark-publication``
must run immediately before the authorized deployment. Once publication starts,
no timeout or failed restore can reopen dispatch. Only ``verify-and-reopen`` in
the verified 0.5.65 container removes this release's exact SQLite gate.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time

from filelock import FileLock
from prepare_upgrade_0565_backup import (
    CONTRACT, DIRECTORY, MAX_ARCHIVE, TARGET_VERSION, inventory,
)
from shortdrama_mcp import startup_restore as restore

GATE = 'production_grounding_upgrade_gate_0565'
GATE_SQL = (f'CREATE TRIGGER {GATE} BEFORE INSERT ON access_audit '
            "WHEN NEW.outcome='started' BEGIN "
            "SELECT RAISE(ABORT, 'production_grounding_upgrade_in_progress'); END")


def server_start():
    parts = Path('/proc/1/stat').read_text().rsplit(')', 1)[1].split()
    boot = int(next(x.split()[1] for x in Path('/proc/stat').read_text().splitlines()
                    if x.startswith('btime ')))
    return datetime.fromtimestamp(boot + int(parts[19]) / os.sysconf('SC_CLK_TCK'),
                                  timezone.utc).isoformat()


def _process_identity(pid=None):
    if pid is not None and int(pid) < 1:
        raise ValueError('freeze_process_invalid')
    # Read the PID as exposed by this procfs mount; managed exec runtimes can
    # report a different namespace PID from os.getpid().
    stat = Path('/proc/self/stat' if pid is None else f'/proc/{int(pid)}/stat').read_text()
    parts = stat.rsplit(')', 1)[1].split()
    if parts[0] in {'Z', 'X', 'x'}:
        raise ValueError('freeze_process_not_running')
    return dict(pid=int(stat.split(' ', 1)[0]), start_ticks=int(parts[19]),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def _require_live_freezer(manifest):
    identity = manifest.get('freeze_process')
    try:
        valid = isinstance(identity, dict) and identity == _process_identity(identity['pid'])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        valid = False
    if not valid:
        raise ValueError('freeze_process_missing_or_changed')


def active_dispatches(db, since):
    return db.execute("""SELECT count(*) FROM access_audit s
        WHERE s.outcome='started' AND s.occurred_at>=? AND NOT EXISTS
        (SELECT 1 FROM access_audit t WHERE t.trace_id=s.trace_id AND t.id>s.id
         AND t.outcome IN ('success','error','denied'))""", (since,)).fetchone()[0]


def _paths(data, runtime):
    target = data / DIRECTORY
    if (any(p.is_symlink() for p in (data, runtime, target, data / 'retention',
                                    target / 'lifecycle.lock', data / 'retention/run.lock'))
            or not data.is_dir() or not target.is_dir()):
        raise ValueError('transfer_unsafe_path')
    return target


def _secret(data):
    path = data / '.record_integrity_secret'
    if path.is_symlink() or not path.is_file():
        raise ValueError('transfer_integrity_key_invalid')
    secret = path.read_bytes()
    if len(secret) < 32:
        raise ValueError('transfer_integrity_key_invalid')
    return secret


def _signed(path, secret):
    if path.is_symlink() or not path.is_file():
        raise ValueError('transfer_signed_record_missing_or_unsafe')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or not hmac.compare_digest(
            str(value.get('integrity_hmac', '')), restore._signature(value, secret))):
        raise ValueError('transfer_record_signature_invalid')
    return value


def _binding(manifest, data, runtime, *, frozen):
    if (manifest.get('contract') != CONTRACT or manifest.get('target_version') != TARGET_VERSION
            or manifest.get('old_writer_frozen') is not frozen
            or manifest.get('data_dir') != str(data) or manifest.get('runtime_root') != str(runtime)
            or not isinstance(manifest.get('files'), dict) or not manifest['files']
            or (frozen and manifest.get('maintenance_gate') != GATE)):
        raise ValueError('transfer_manifest_binding_invalid')


def _gate(db):
    value = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=? AND tbl_name='access_audit'",
                       (GATE,)).fetchone()
    normalize = lambda sql: ' '.join(sql.split()).rstrip(';')
    if not value or normalize(value[0]) != normalize(GATE_SQL):
        raise ValueError('transfer_maintenance_gate_missing_or_changed')


def _database(data):
    path = data / 'account_access.sqlite3'
    if path.is_symlink() or not path.is_file():
        raise ValueError('transfer_role_database_missing_or_unsafe')
    return path


def _verify_bytes(data, runtime, target, manifest):
    files, expanded = inventory(runtime)
    if files != manifest['files']:
        raise ValueError('runtime_changed_since_draft')
    if restore.protected_fingerprints(data) != manifest['protected_fingerprints']:
        raise ValueError('protected_state_changed_since_draft')
    archive = target / 'runtime.tar.gz'
    if (archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE
            or restore._file_hash(archive) != manifest['archive_sha256']):
        raise ValueError('archive_changed_since_draft')
    return files, expanded


@contextmanager
def frozen_transfer(data, runtime, since, drain_seconds=60):
    target = _paths(data, runtime)
    active = target / 'manifest.json'
    committed = target / 'deployment-started'
    consumed = target / 'manifest.consumed.json'
    if any(data.glob('.upgrade-*/manifest.json')) or committed.exists() or consumed.exists():
        raise ValueError('transfer_already_pending_or_used')
    secret = _secret(data)
    draft = _signed(target / 'manifest.draft.json', secret)
    _binding(draft, data, runtime, frozen=False)
    database = _database(data)
    installed = False
    armed_hash = None
    (data / 'retention').mkdir(mode=0o700, exist_ok=True)
    with FileLock(str(data / 'retention/run.lock'), timeout=5):
        try:
            with closing(sqlite3.connect(database)) as db, db:
                db.execute(GATE_SQL)  # Refuse an existing gate, never replace it.
                installed = True
            deadline = time.monotonic() + drain_seconds
            while True:
                with closing(sqlite3.connect(database)) as db:
                    pending = active_dispatches(db, since)
                if not pending:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('active_dispatches_not_drained')
                time.sleep(1)
            print('DISPATCH_FROZEN_AND_DRAINED; retention lock held', flush=True)
            with FileLock(str(target / 'lifecycle.lock'), timeout=5):
                files, expanded = _verify_bytes(data, runtime, target, draft)
                draft.update(old_writer_frozen=True, maintenance_gate=GATE,
                             freeze_process=_process_identity())
                draft['integrity_hmac'] = restore._signature(draft, secret)
                restore._atomic(active, restore._json_bytes(draft) + b'\n')
                armed_hash = restore._file_hash(active)
            yield dict(status='ARMED_WRITER_FROZEN', target_version=TARGET_VERSION,
                       files=len(files), source_bytes=expanded, manifest_sha256=armed_hash,
                       archive_sha256=draft['archive_sha256'], maintenance_gate=GATE)
        finally:
            # Serialize cancellation with mark-publication. Once publication is
            # committed (or restore consumes the marker), the gate stays closed.
            # Publication rechecks a multi-GB tree. Wait for that operation
            # rather than timing out, dropping the retention lock and racing it.
            with FileLock(str(target / 'lifecycle.lock'), timeout=-1):
                if installed and not committed.exists() and not consumed.exists():
                    if active.exists():
                        if armed_hash is None or restore._file_hash(active) != armed_hash:
                            raise ValueError('cannot_resume_with_unrecognized_manifest')
                        os.replace(active, target / 'manifest.aborted.json')
                    with closing(sqlite3.connect(database)) as db, db:
                        _gate(db)
                        db.execute(f'DROP TRIGGER {GATE}')
                    print('TRANSFER_DISARMED; old tool dispatch resumed', flush=True)


def mark_publication(data=Path('/data'), runtime=Path('/tmp/shortdrama-library-runtime')):
    target = _paths(data, runtime)
    with FileLock(str(target / 'lifecycle.lock'), timeout=5):
        if (target / 'deployment-started').exists() or (target / 'manifest.consumed.json').exists():
            raise ValueError('publication_already_marked_or_consumed')
        secret = _secret(data)
        manifest = _signed(target / 'manifest.json', secret)
        _binding(manifest, data, runtime, frozen=True)
        _require_live_freezer(manifest)
        with closing(sqlite3.connect(_database(data).as_uri() + '?mode=ro', uri=True)) as db:
            _gate(db)
        _verify_bytes(data, runtime, target, manifest)
        _require_live_freezer(manifest)
        value = dict(contract=CONTRACT, target_version=TARGET_VERSION,
                     manifest_sha256=restore._hash(restore._json_bytes(manifest)),
                     marked_at=datetime.now(timezone.utc).isoformat())
        value['integrity_hmac'] = restore._signature(value, secret)
        restore._atomic(target / 'deployment-started', restore._json_bytes(value) + b'\n')
        return dict(status='PUBLICATION_MARKED_GATE_REMAINS_CLOSED', target_version=TARGET_VERSION)


def _verify_restored(data, runtime):
    from shortdrama_mcp import __version__
    if __version__ != TARGET_VERSION:
        raise ValueError('verification_requires_running_target_version')
    target = _paths(data, runtime)
    if any(data.glob('.upgrade-*/manifest.json')):
        raise ValueError('verification_pending_transfer')
    secret = _secret(data)
    manifest = _signed(target / 'manifest.consumed.json', secret)
    _binding(manifest, data, runtime, frozen=True)
    receipt = _signed(target / 'restore-receipt.json', secret)
    publication = _signed(target / 'deployment-started', secret)
    manifest_hash = restore._hash(restore._json_bytes(manifest))
    if (receipt.get('status') != 'pass' or receipt.get('contract') != CONTRACT
            or receipt.get('target_version') != TARGET_VERSION
            or receipt.get('runtime_root') != str(runtime)
            or receipt.get('manifest_sha256') != manifest_hash
            or receipt.get('archive_sha256') != manifest['archive_sha256']
            or receipt.get('files_verified') != len(manifest['files'])
            or receipt.get('existing_files_overwritten') != 0
            or receipt.get('protected_state_unchanged') is not True
            or receipt.get('completed_before_listen') is not True
            or publication.get('contract') != CONTRACT
            or publication.get('target_version') != TARGET_VERSION
            or publication.get('manifest_sha256') != manifest_hash):
        raise ValueError('verification_receipt_or_publication_binding_invalid')
    with closing(sqlite3.connect(_database(data).as_uri() + '?mode=ro', uri=True)) as db:
        _gate(db)
    files, expanded = _verify_bytes(data, runtime, target, manifest)
    return dict(status='RESTORE_VERIFIED_GATE_CLOSED', target_version=TARGET_VERSION,
                files_verified=len(files), source_bytes=expanded,
                manifest_sha256=manifest_hash, protected_state_unchanged=True)


def verify_release(data=Path('/data'), runtime=Path('/tmp/shortdrama-library-runtime'), *, reopen=False):
    target = _paths(data, runtime)
    with FileLock(str(data / 'retention/run.lock'), timeout=5), \
            FileLock(str(target / 'lifecycle.lock'), timeout=5):
        result = _verify_restored(data, runtime)
        if reopen:
            # Every verification above must pass first. A transaction failure
            # rolls back DROP TRIGGER and leaves dispatch fail-closed.
            with closing(sqlite3.connect(_database(data))) as db, db:
                db.execute('BEGIN IMMEDIATE')
                _gate(db)
                db.execute(f'DROP TRIGGER {GATE}')
            result['status'] = 'RESTORE_VERIFIED_DISPATCH_REOPENED'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'mark-publication', 'verify', 'verify-and-reopen'),
                        nargs='?', default='freeze')
    args = parser.parse_args()
    if args.action == 'mark-publication':
        result = mark_publication()
    elif args.action in {'verify', 'verify-and-reopen'}:
        result = verify_release(reopen=args.action == 'verify-and-reopen')
    else:
        data = Path('/data')
        with frozen_transfer(data, Path('/tmp/shortdrama-library-runtime'), server_start()) as result:
            print(json.dumps(result, indent=2), flush=True)
            deadline = time.monotonic() + 900
            while (data / DIRECTORY / 'deployment-started').exists() or time.monotonic() < deadline:
                time.sleep(1)
            print('No publication within 15 minutes; aborting before deployment', flush=True)
        return
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
