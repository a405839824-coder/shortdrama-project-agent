"""Quiesce audited tool dispatch, verify a prepared archive, then arm it.

Run in the old Railway container. Keep this process alive across deployment:
it holds the retention lock. New tool dispatches are blocked by a persistent
SQLite trigger; terminal audit events can still drain. The new container must
keep that gate until its restored bytes and identities have been verified.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time

from filelock import FileLock
from prepare_admin_read_backup import CONTRACT, DIRECTORY, inventory
from shortdrama_mcp import startup_restore as restore

GATE = 'admin_read_upgrade_gate_20261006'
GATE_SQL = (f'CREATE TRIGGER {GATE} BEFORE INSERT ON access_audit '
            "WHEN NEW.outcome='started' BEGIN "
            "SELECT RAISE(ABORT, 'admin_read_upgrade_in_progress'); END")


def server_start():
    parts = Path('/proc/1/stat').read_text().rsplit(')', 1)[1].split()
    boot = int(next(x.split()[1] for x in Path('/proc/stat').read_text().splitlines()
                    if x.startswith('btime ')))
    return datetime.fromtimestamp(boot + int(parts[19]) / os.sysconf('SC_CLK_TCK'),
                                  timezone.utc).isoformat()


def active_dispatches(db, since):
    return db.execute("""SELECT count(*) FROM access_audit s
        WHERE s.outcome='started' AND s.occurred_at>=? AND NOT EXISTS
        (SELECT 1 FROM access_audit t WHERE t.trace_id=s.trace_id AND t.id>s.id
         AND t.outcome IN ('success','error','denied'))""", (since,)).fetchone()[0]


@contextmanager
def frozen_transfer(data, runtime, since, drain_seconds=60):
    target = data / DIRECTORY
    active = target / 'manifest.json'
    committed = target / 'deployment-started'
    consumed = target / 'manifest.consumed.json'
    if any(data.glob('.upgrade-*/manifest.json')) or committed.exists() or consumed.exists():
        raise ValueError('transfer_already_pending_or_used')
    draft = json.loads((target / 'manifest.draft.json').read_text())
    secret = (data / '.record_integrity_secret').read_bytes()
    if (draft.get('contract') != CONTRACT or draft.get('target_version') != '0.5.62'
            or draft.get('old_writer_frozen') is not False
            or draft.get('data_dir') != str(data) or draft.get('runtime_root') != str(runtime)
            or not hmac.compare_digest(str(draft.get('integrity_hmac', '')),
                                       restore._signature(draft, secret))):
        raise ValueError('draft_binding_or_signature_invalid')
    installed = False
    armed_hash = None
    with FileLock(str(data / 'retention/run.lock'), timeout=5):
        try:
            with closing(sqlite3.connect(data / 'account_access.sqlite3')) as db, db:
                db.execute(GATE_SQL)  # Refuse an existing gate; never overwrite it.
                installed = True
            deadline = time.monotonic() + drain_seconds
            while True:
                with closing(sqlite3.connect(data / 'account_access.sqlite3')) as db:
                    pending = active_dispatches(db, since)
                if not pending:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('active_dispatches_not_drained')
                time.sleep(1)
            print('DISPATCH_FROZEN_AND_DRAINED; retention lock held', flush=True)
            files, expanded = inventory(runtime)
            if files != draft['files']:
                raise ValueError('runtime_changed_since_draft')
            if restore.protected_fingerprints(data) != draft['protected_fingerprints']:
                raise ValueError('protected_state_changed_since_draft')
            if restore._file_hash(target / 'runtime.tar.gz') != draft['archive_sha256']:
                raise ValueError('archive_changed_since_draft')
            draft.update(old_writer_frozen=True, maintenance_gate=GATE)
            draft['integrity_hmac'] = restore._signature(draft, secret)
            restore._atomic(active, restore._json_bytes(draft) + b'\n')
            armed_hash = restore._file_hash(active)
            yield dict(status='ARMED_WRITER_FROZEN', files=len(files),
                       source_bytes=expanded, manifest_sha256=armed_hash,
                       archive_sha256=draft['archive_sha256'], maintenance_gate=GATE)
        finally:
            # Before publication, cancellation disarms first, then reopens tools.
            # After publication starts, keep the persistent gate until the new
            # container's signed receipt and complete tree have been verified.
            if installed and not committed.exists() and not consumed.exists():
                if active.exists():
                    if armed_hash is None or restore._file_hash(active) != armed_hash:
                        raise ValueError('cannot_resume_with_unrecognized_manifest')
                    os.replace(active, target / 'manifest.aborted.json')
                with closing(sqlite3.connect(data / 'account_access.sqlite3')) as db, db:
                    db.execute(f'DROP TRIGGER {GATE}')
                print('TRANSFER_DISARMED; tool dispatch resumed', flush=True)


if __name__ == '__main__':
    data = Path('/data')
    target = data / DIRECTORY
    with frozen_transfer(data, Path('/tmp/shortdrama-library-runtime'), server_start()) as result:
        print(json.dumps(result, indent=2), flush=True)
        deadline = time.monotonic() + 900
        while (target / 'deployment-started').exists() or time.monotonic() < deadline:
            time.sleep(1)
        print('No deployment committed within 15 minutes; aborting safely', flush=True)
