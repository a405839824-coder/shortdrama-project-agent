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
MAINTENANCE_DIRECTORY = ".upgrade-retention-20261003"
MAINTENANCE_CONTRACT = "library-runtime-transfer-retention-20261003-v1"
AUDIT_ENVIRONMENT_DIRECTORY = ".upgrade-audit-environment-20261004"
AUDIT_ENVIRONMENT_CONTRACT = "library-runtime-transfer-audit-environment-20261004-v1"
AUDIT_ENVIRONMENT_VERSION = "0.5.61"
AUDIT_STORAGE_DIRECTORY = ".upgrade-audit-storage-20261004"
AUDIT_STORAGE_CONTRACT = "library-runtime-transfer-audit-storage-20261004-v1"
AUDIT_STORAGE_VERSION = "0.5.62"
ADMIN_READ_DIRECTORY = ".upgrade-admin-read-20261006"
ADMIN_READ_CONTRACT = "library-runtime-transfer-admin-read-20261006-v1"
ADMIN_READ_VERSION = "0.5.62"
RULE_EXECUTION_DIRECTORY = ".upgrade-rule-execution-0563"
RULE_EXECUTION_CONTRACT = "library-runtime-transfer-rule-execution-0563-v1"
RULE_EXECUTION_VERSION = "0.5.63"
AUDIT_GENERATION_DIRECTORY = ".upgrade-audit-generation-0564"
AUDIT_GENERATION_CONTRACT = "library-runtime-transfer-audit-generation-0564-v1"
AUDIT_GENERATION_VERSION = "0.5.64"
AUDIT_GENERATION_GATE = "audit_generation_upgrade_gate_0564"
AUDIT_GENERATION_GATE_SQL = (
    f"CREATE TRIGGER {AUDIT_GENERATION_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'audit_generation_upgrade_in_progress'); END"
)
PRODUCTION_GROUNDING_DIRECTORY = ".upgrade-production-grounding-0565"
PRODUCTION_GROUNDING_CONTRACT = "library-runtime-transfer-production-grounding-0565-v1"
PRODUCTION_GROUNDING_VERSION = "0.5.65"
PRODUCTION_GROUNDING_GATE = "production_grounding_upgrade_gate_0565"
PRODUCTION_GROUNDING_GATE_SQL = (
    f"CREATE TRIGGER {PRODUCTION_GROUNDING_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'production_grounding_upgrade_in_progress'); END"
)
PRODUCTION_GROUNDING_MAX_EXPANDED_BYTES = 8 * 1024**3
SURFACE_VOICE_DIRECTORY = ".upgrade-surface-voice-0566"
SURFACE_VOICE_CONTRACT = "library-runtime-transfer-surface-voice-0566-v1"
SURFACE_VOICE_VERSION = "0.5.66"
SURFACE_VOICE_GATE = "surface_voice_upgrade_gate_0566"
SURFACE_VOICE_GATE_SQL = (
    f"CREATE TRIGGER {SURFACE_VOICE_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'surface_voice_upgrade_in_progress'); END"
)
SURFACE_VOICE_MAX_EXPANDED_BYTES = 8 * 1024**3
SCRIPT_SCOPE_DIRECTORY = ".upgrade-script-scope-0567"
SCRIPT_SCOPE_CONTRACT = "library-runtime-transfer-script-scope-0567-v1"
SCRIPT_SCOPE_VERSION = "0.5.67"
SCRIPT_SCOPE_GATE = "script_scope_upgrade_gate_0567"
SCRIPT_SCOPE_GATE_SQL = (
    f"CREATE TRIGGER {SCRIPT_SCOPE_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'script_scope_upgrade_in_progress'); END"
)
SCRIPT_SCOPE_MAX_EXPANDED_BYTES = 8 * 1024**3
GESTURE_REPAIR_DIRECTORY = ".upgrade-gesture-repair-0568"
GESTURE_REPAIR_CONTRACT = "library-runtime-transfer-gesture-repair-0568-v1"
GESTURE_REPAIR_VERSION = "0.5.68"
GESTURE_REPAIR_GATE = "gesture_repair_upgrade_gate_0568"
GESTURE_REPAIR_GATE_SQL = (
    f"CREATE TRIGGER {GESTURE_REPAIR_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'gesture_repair_upgrade_in_progress'); END"
)
GESTURE_REPAIR_MAX_EXPANDED_BYTES = 8 * 1024**3
PROMPT_LIBRARY_DIRECTORY = ".upgrade-prompt-library-0569"
PROMPT_LIBRARY_CONTRACT = "library-runtime-transfer-prompt-library-0569-v1"
PROMPT_LIBRARY_VERSION = "0.5.69"
PROMPT_LIBRARY_GATE = "prompt_library_upgrade_gate_0569"
PROMPT_LIBRARY_GATE_SQL = (
    f"CREATE TRIGGER {PROMPT_LIBRARY_GATE} BEFORE INSERT ON access_audit "
    "WHEN NEW.outcome='started' BEGIN "
    "SELECT RAISE(ABORT, 'prompt_library_upgrade_in_progress'); END"
)
PROMPT_LIBRARY_MAX_EXPANDED_BYTES = 8 * 1024**3
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 4 * 1024**3
# The current live project tree alone exceeds 5 GB. Only this fresh, signed
# transfer may use the larger ceiling; older release contracts keep 4 GiB.
ADMIN_READ_MAX_EXPANDED_BYTES = 8 * 1024**3
RULE_EXECUTION_MAX_EXPANDED_BYTES = 8 * 1024**3
AUDIT_GENERATION_MAX_EXPANDED_BYTES = 8 * 1024**3
MAX_FILES = 100_000


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
                result[path.relative_to(root).as_posix()] = _file_hash(path)
    return result


def _restore(data_dir: Path, runtime_root: Path, *, version: str,
             directory: str = DIRECTORY, contract: str = CONTRACT) -> dict | None:
    target_version = {
        (DIRECTORY, CONTRACT): TARGET_VERSION,
        (MAINTENANCE_DIRECTORY, MAINTENANCE_CONTRACT): TARGET_VERSION,
        (AUDIT_ENVIRONMENT_DIRECTORY, AUDIT_ENVIRONMENT_CONTRACT): AUDIT_ENVIRONMENT_VERSION,
        (AUDIT_STORAGE_DIRECTORY, AUDIT_STORAGE_CONTRACT): AUDIT_STORAGE_VERSION,
        (ADMIN_READ_DIRECTORY, ADMIN_READ_CONTRACT): ADMIN_READ_VERSION,
        (RULE_EXECUTION_DIRECTORY, RULE_EXECUTION_CONTRACT): RULE_EXECUTION_VERSION,
        (AUDIT_GENERATION_DIRECTORY, AUDIT_GENERATION_CONTRACT): AUDIT_GENERATION_VERSION,
        (PRODUCTION_GROUNDING_DIRECTORY, PRODUCTION_GROUNDING_CONTRACT): PRODUCTION_GROUNDING_VERSION,
        (SURFACE_VOICE_DIRECTORY, SURFACE_VOICE_CONTRACT): SURFACE_VOICE_VERSION,
        (SCRIPT_SCOPE_DIRECTORY, SCRIPT_SCOPE_CONTRACT): SCRIPT_SCOPE_VERSION,
        (GESTURE_REPAIR_DIRECTORY, GESTURE_REPAIR_CONTRACT): GESTURE_REPAIR_VERSION,
        (PROMPT_LIBRARY_DIRECTORY, PROMPT_LIBRARY_CONTRACT): PROMPT_LIBRARY_VERSION,
    }.get((directory, contract))
    if target_version is None:
        raise ValueError('startup_restore_undeclared_transfer')
    expanded_limit = {
        (ADMIN_READ_DIRECTORY, ADMIN_READ_CONTRACT): ADMIN_READ_MAX_EXPANDED_BYTES,
        (RULE_EXECUTION_DIRECTORY, RULE_EXECUTION_CONTRACT): RULE_EXECUTION_MAX_EXPANDED_BYTES,
        (AUDIT_GENERATION_DIRECTORY, AUDIT_GENERATION_CONTRACT): AUDIT_GENERATION_MAX_EXPANDED_BYTES,
        (PRODUCTION_GROUNDING_DIRECTORY, PRODUCTION_GROUNDING_CONTRACT): PRODUCTION_GROUNDING_MAX_EXPANDED_BYTES,
        (SURFACE_VOICE_DIRECTORY, SURFACE_VOICE_CONTRACT): SURFACE_VOICE_MAX_EXPANDED_BYTES,
        (SCRIPT_SCOPE_DIRECTORY, SCRIPT_SCOPE_CONTRACT): SCRIPT_SCOPE_MAX_EXPANDED_BYTES,
        (GESTURE_REPAIR_DIRECTORY, GESTURE_REPAIR_CONTRACT): GESTURE_REPAIR_MAX_EXPANDED_BYTES,
        (PROMPT_LIBRARY_DIRECTORY, PROMPT_LIBRARY_CONTRACT): PROMPT_LIBRARY_MAX_EXPANDED_BYTES,
    }.get((directory, contract), MAX_EXPANDED_BYTES)
    transfer = data_dir / directory
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
    if (manifest.get("contract") != contract or manifest.get("target_version") != version or version != target_version
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
    gate_contracts = {
        AUDIT_GENERATION_CONTRACT: (AUDIT_GENERATION_GATE, AUDIT_GENERATION_GATE_SQL),
        PRODUCTION_GROUNDING_CONTRACT: (PRODUCTION_GROUNDING_GATE, PRODUCTION_GROUNDING_GATE_SQL),
        SURFACE_VOICE_CONTRACT: (SURFACE_VOICE_GATE, SURFACE_VOICE_GATE_SQL),
        SCRIPT_SCOPE_CONTRACT: (SCRIPT_SCOPE_GATE, SCRIPT_SCOPE_GATE_SQL),
        GESTURE_REPAIR_CONTRACT: (GESTURE_REPAIR_GATE, GESTURE_REPAIR_GATE_SQL),
        PROMPT_LIBRARY_CONTRACT: (PROMPT_LIBRARY_GATE, PROMPT_LIBRARY_GATE_SQL),
    }
    if contract in gate_contracts:
        gate_name, gate_sql = gate_contracts[contract]
        # New release transfers must prove the persistent gate is still active;
        # a signed assertion alone must not reopen the replaced writer.
        database = data_dir / "account_access.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            gate = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=? AND tbl_name='access_audit'",
                (gate_name,),
            ).fetchone()
        normalize = lambda sql: " ".join(sql.split()).rstrip(";")
        if (manifest.get("maintenance_gate") != gate_name or not gate
                or normalize(gate[0]) != normalize(gate_sql)):
            raise ValueError("startup_restore_maintenance_gate_missing_or_changed")
    archive_path = transfer / "runtime.tar.gz"
    if (archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES
            or _file_hash(archive_path) != manifest.get("archive_sha256")):
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
                if expanded > expanded_limit:
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
        receipt = {"contract": contract, "status": "pass", "target_version": version,
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
    pending = [(d, c) for d, c in ((DIRECTORY, CONTRACT), (MAINTENANCE_DIRECTORY, MAINTENANCE_CONTRACT),
                                 (AUDIT_ENVIRONMENT_DIRECTORY, AUDIT_ENVIRONMENT_CONTRACT),
                                 (AUDIT_STORAGE_DIRECTORY, AUDIT_STORAGE_CONTRACT),
                                 (ADMIN_READ_DIRECTORY, ADMIN_READ_CONTRACT),
                                 (RULE_EXECUTION_DIRECTORY, RULE_EXECUTION_CONTRACT),
                                 (AUDIT_GENERATION_DIRECTORY, AUDIT_GENERATION_CONTRACT),
                                 (PRODUCTION_GROUNDING_DIRECTORY, PRODUCTION_GROUNDING_CONTRACT),
                                 (SURFACE_VOICE_DIRECTORY, SURFACE_VOICE_CONTRACT),
                                 (SCRIPT_SCOPE_DIRECTORY, SCRIPT_SCOPE_CONTRACT),
                                 (GESTURE_REPAIR_DIRECTORY, GESTURE_REPAIR_CONTRACT),
                                 (PROMPT_LIBRARY_DIRECTORY, PROMPT_LIBRARY_CONTRACT))
               if (settings.data_dir / d / 'manifest.json').exists()]
    if not pending:
        return None
    if len(pending) != 1:
        raise ValueError('startup_restore_multiple_transfers')
    root = Path(os.environ.get("SHORTDRAMA_RUNTIME_DATA_DIR") or "/tmp/shortdrama-library-runtime")
    if (settings.data_dir != Path("/data") or root != Path("/tmp/shortdrama-library-runtime")
            or settings.deployment_mode != "cloud"
            or os.environ.get("SHORTDRAMA_PROJECT_STORAGE_MODE", "persistent") != "library"):
        raise ValueError("startup_restore_explicit_library_cloud_required")
    from . import __version__
    return _restore(settings.data_dir, root, version=__version__, directory=pending[0][0], contract=pending[0][1])
