"""One declared release transfer, before a server can accept requests.

This never executes persistent-volume code. The operator must freeze the old
writer before arming a signed manifest. A consumed manifest is never replayed:
later Library mounts and writes must not be rolled back to an upgrade snapshot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from contextlib import closing
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tarfile
import tempfile

TARGET_VERSION = "0.5.60"
CONTRACT = "library-runtime-transfer-0560-r3-v1"
DIRECTORY = ".upgrade-0560-r3"
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
MAX_FILES = 100_000


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _signature(value, secret: bytes) -> str:
    return hmac.new(secret, _json_bytes({k: v for k, v in value.items() if k != "integrity_hmac"}), hashlib.sha256).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".restore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def protected_fingerprints(data_dir: Path) -> dict:
    """Only hashes leave this helper; no registry, identity or key is returned."""
    files = {}
    for name in (".download_secret", ".record_integrity_secret", "member_connections.json",
                 ".legacy_download_token_window.json", ".v055_security_epoch.json"):
        path = data_dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("startup_restore_protected_state_missing")
        files[name] = _hash(path.read_bytes())
    for path in sorted((data_dir / "library_project_registry").rglob("*.json")):
        if path.is_symlink():
            raise ValueError("startup_restore_registry_symlink")
        files[path.relative_to(data_dir).as_posix()] = _hash(path.read_bytes())
    database = data_dir / "account_access.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise ValueError("startup_restore_role_database_missing")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        roles = db.execute("SELECT issuer,subject,role,enabled,updated_at FROM user_roles ORDER BY issuer,subject").fetchall()
    return {"files": files, "user_roles_sha256": _hash(_json_bytes(roles))}


def _tree(root: Path) -> dict[str, str]:
    result = {}
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("startup_restore_runtime_symlink")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = _hash(path.read_bytes())
    return result


def _restore(data_dir: Path, runtime_root: Path, *, version: str) -> dict | None:
    transfer = data_dir / DIRECTORY
    marker = transfer / "manifest.json"
    if not marker.exists():
        return None
    # No app/store has been initialized, so any existing nonmatching tree is
    # meaningful state. Never merge or overwrite it a file at a time.
    if transfer.is_symlink() or marker.is_symlink() or data_dir.is_symlink() or runtime_root.is_symlink():
        raise ValueError("startup_restore_path_symlink")
    if (transfer / "manifest.consumed.json").exists():
        raise ValueError("startup_restore_already_consumed")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if (manifest.get("contract") != CONTRACT or manifest.get("target_version") != version or version != TARGET_VERSION
            or manifest.get("runtime_root") != str(runtime_root)
            or manifest.get("data_dir") != str(data_dir)
            or manifest.get("old_writer_frozen") is not True):
        raise ValueError("startup_restore_manifest_binding")
    secret_path = data_dir / ".record_integrity_secret"
    if secret_path.is_symlink():
        raise ValueError("startup_restore_protected_state_missing")
    secret = secret_path.read_bytes()
    if len(secret) < 32 or not hmac.compare_digest(str(manifest.get("integrity_hmac", "")), _signature(manifest, secret)):
        raise ValueError("startup_restore_manifest_signature")
    if manifest.get("protected_fingerprints") != protected_fingerprints(data_dir):
        raise ValueError("startup_restore_protected_state_changed")
    archive_path = transfer / "runtime.tar.gz"
    if (archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES
            or _hash(archive_path.read_bytes()) != manifest.get("archive_sha256")):
        raise ValueError("startup_restore_archive_hash")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not 1 <= len(expected) <= MAX_FILES or not any(n.endswith("/hub.json") for n in expected):
        raise ValueError("startup_restore_empty_or_invalid_manifest")
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".shortdrama-restore-", dir=runtime_root.parent))
    try:
        seen, expanded = set(), 0
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                name = member.name
                relative = PurePosixPath(name)
                if (not member.isfile() or name in seen or relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or relative.parts[0] not in {"projects", "exports"}
                        or relative.as_posix() != name or "\\" in name or name not in expected
                        or len(seen) >= MAX_FILES):
                    raise ValueError("startup_restore_unsafe_archive_member")
                seen.add(name)
                expanded += member.size
                if expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("startup_restore_archive_size")
                target = stage.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                digest = hashlib.sha256()
                with target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(target, member.mode & 0o777 & ~0o022)
                if digest.hexdigest() != expected[name]:
                    raise ValueError("startup_restore_member_hash")
        if seen != set(expected) or _tree(stage) != expected:
            raise ValueError("startup_restore_manifest_files")
        if runtime_root.exists():
            if _tree(runtime_root) != expected:
                raise ValueError("startup_restore_existing_runtime_conflict")
            restored = False
        else:
            os.replace(stage, runtime_root)
            restored = True
        receipt = {"contract": CONTRACT, "status": "pass", "target_version": version,
                   "archive_sha256": manifest["archive_sha256"], "manifest_sha256": _hash(_json_bytes(manifest)),
                   "runtime_root": str(runtime_root), "files_verified": len(expected),
                   "restored": restored, "existing_files_overwritten": 0,
                   "protected_state_unchanged": True, "completed_before_listen": True}
        receipt["integrity_hmac"] = _signature(receipt, secret)
        _atomic(transfer / "restore-receipt.json", _json_bytes(receipt) + b"\n")
        # Last operation: a failed restore keeps the armed marker for safe retry.
        # A successful one is single-use, even if a future process has no /tmp.
        os.replace(marker, transfer / "manifest.consumed.json")
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def restore_release_runtime_if_requested(settings) -> dict | None:
    marker = settings.data_dir / DIRECTORY / "manifest.json"
    if not marker.exists():
        return None
    root = Path(os.environ.get("SHORTDRAMA_RUNTIME_DATA_DIR") or "/tmp/shortdrama-library-runtime")
    if (settings.data_dir != Path("/data") or root != Path("/tmp/shortdrama-library-runtime")
            or settings.deployment_mode != "cloud"
            or os.environ.get("SHORTDRAMA_PROJECT_STORAGE_MODE", "persistent") != "library"):
        raise ValueError("startup_restore_explicit_library_cloud_required")
    from . import __version__
    return _restore(settings.data_dir, root, version=__version__)
