"""Prepare a verified, UNARMED release backup without stopping the live writer.

The operator must later drain/freeze the writer and verify the exact current
tree before signing an active manifest. This command never arms or deploys.
Run with PYTHONPATH=server against the existing 0.5.62 restore helpers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile

from shortdrama_mcp import startup_restore as restore

DIRECTORY = '.upgrade-admin-read-20261006'
CONTRACT = 'library-runtime-transfer-admin-read-20261006-v1'
RESERVE = 256 * 1024**2
MAX_ARCHIVE = 2 * 1024**3
MAX_EXPANDED = 8 * 1024**3
MAX_FILES = 100_000


class CappedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def write(self, block):
        if self.stream.tell() + len(block) > self.limit:
            raise ValueError('backup_compressed_capacity_exceeded')
        return self.stream.write(block)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('backup_runtime_root_invalid')
    files = {}
    expanded = 0
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if (PurePosixPath(name).parts[0] not in {'projects', 'exports'}
                or '\\' in name or stat.S_ISLNK(info.st_mode)):
            raise ValueError('backup_unsafe_or_active_runtime_entry')
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('backup_nonregular_runtime_entry')
        expanded += info.st_size
        if expanded > MAX_EXPANDED or len(files) >= MAX_FILES:
            raise ValueError('backup_expanded_capacity_exceeded')
        files[name] = restore._file_hash(path)
    if not files or not any(name.endswith('/hub.json') for name in files):
        raise ValueError('backup_no_project_hubs')
    return files, expanded


def prepare(data=Path('/data'), runtime=Path('/tmp/shortdrama-library-runtime')):
    if data.is_symlink() or not data.is_dir():
        raise ValueError('backup_data_root_invalid')
    if any(data.glob('.upgrade-*/manifest.json')):
        raise ValueError('backup_other_transfer_pending')
    target = data / DIRECTORY
    target.mkdir(mode=0o700)  # Refuse to overwrite even an earlier draft.
    part, archive = target / 'runtime.partial', target / 'runtime.tar.gz'
    draft = target / 'manifest.draft.json'
    success = False
    try:
        protected = restore.protected_fingerprints(data)
        print('1/4 Reading and hashing source files...', flush=True)
        files, expanded = inventory(runtime)
        ceiling = min(MAX_ARCHIVE, shutil.disk_usage(data).free - RESERVE)
        if ceiling <= 0:
            raise ValueError('backup_insufficient_free_space')
        print(f'2/4 Compressing {len(files)} files, {expanded} source bytes...', flush=True)
        with part.open('xb') as raw:
            os.chmod(part, 0o600)
            with tarfile.open(fileobj=CappedWriter(raw, ceiling), mode='w:gz') as bundle:
                for name in files:
                    source = runtime / name
                    entry = bundle.gettarinfo(str(source), arcname=name)
                    if not entry.isfile():
                        raise ValueError('backup_source_type_changed')
                    with source.open('rb') as stream:
                        bundle.addfile(entry, stream)
            raw.flush()
            os.fsync(raw.fileno())
        print('3/4 Verifying every compressed file...', flush=True)
        seen = set()
        with tarfile.open(part, 'r:gz') as bundle:
            for entry in bundle:
                if not entry.isfile() or entry.name in seen or entry.name not in files:
                    raise ValueError('backup_archive_member_mismatch')
                with bundle.extractfile(entry) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != files[entry.name]:
                    raise ValueError('backup_archive_content_mismatch')
                seen.add(entry.name)
        if seen != set(files):
            raise ValueError('backup_archive_incomplete')
        print('4/4 Rechecking source files and protected identities...', flush=True)
        if inventory(runtime) != (files, expanded):
            raise ValueError('backup_source_changed_retry_after_quiescing')
        if restore.protected_fingerprints(data) != protected:
            raise ValueError('backup_protected_identity_changed')
        if shutil.disk_usage(data).free < RESERVE:
            raise ValueError('backup_free_space_reserve_exceeded')
        digest = restore._file_hash(part)
        manifest = dict(contract=CONTRACT, target_version='0.5.62',
                        runtime_root=str(runtime), data_dir=str(data),
                        old_writer_frozen=False, files=files,
                        archive_sha256=digest, protected_fingerprints=protected)
        secret = (data / '.record_integrity_secret').read_bytes()
        if len(secret) < 32:
            raise ValueError('backup_integrity_key_invalid')
        manifest['integrity_hmac'] = restore._signature(manifest, secret)
        os.rename(part, archive)
        restore._atomic(draft, restore._json_bytes(manifest) + b'\n')
        directory_fd = os.open(target, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        success = True
        result = dict(status='PREBACKUP_VERIFIED_NOT_ARMED', files=len(files),
                      source_bytes=expanded, archive_bytes=archive.stat().st_size,
                      archive_sha256=digest, free_bytes=shutil.disk_usage(data).free,
                      destination=str(target), old_writer_frozen=False)
        print(json.dumps(result, indent=2), flush=True)
        return result
    finally:
        if not success:
            # Remove only files created by this run; existing backups are never touched.
            for path in (part, archive, draft):
                path.unlink(missing_ok=True)
            try:
                target.rmdir()
            except OSError:
                pass


if __name__ == '__main__':
    prepare()
