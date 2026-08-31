from __future__ import annotations

import base64
import calendar
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from filelock import FileLock


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_RECORD_KINDS = {
    "handoffs",
    "handoff_recoveries",
    "certificates",
    "telemetry",
    "diagnostics",
    "repair_packages",
    "draft_preflights",
    "migrations",
}
SIGNED_ONLY_PROJECT_RECORD_KINDS = {
    "draft_preflights",
    "handoff_recoveries",
    "migrations",
}
DEFAULT_SUCCESSOR_LEASE_SECONDS = 300
MAX_SUCCESSOR_LEASE_SECONDS = 86_400
MAX_SUCCESSOR_TRANSACTION_HISTORY = 32
DEFAULT_DRAFT_PREFLIGHT_COMMIT_LEASE_SECONDS = 300
DEFAULT_HANDOFF_CLAIM_LEASE_SECONDS = 300
MAX_CONSUMPTION_LEASE_SECONDS = 3_600
MAX_CONSUMPTION_CLAIM_HISTORY = 16
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_SPOOL_MEMORY_BYTES = 2 * 1024 * 1024
DOWNLOAD_COPY_CHUNK_BYTES = 1024 * 1024
LEGACY_DOWNLOAD_TOKEN_WINDOW_SECONDS = 86_400
SUCCESSOR_KINDS = frozenset({"patch", "delta", "repair"})
UNPUBLISHED_ARTIFACT_STATES = frozenset({"pending", "aborted", "rejected"})


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug_filename(value: str, fallback: str = "artifact") -> str:
    name = Path(str(value or "")).name.strip()
    if not name:
        name = fallback
    stem = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff._-]+", "-", name).strip(".-")
    return stem[:160] or fallback


class ProjectStore:
    def __init__(
        self,
        root: Path,
        public_base_url: str,
        *,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ):
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.max_download_bytes = max(1, int(max_download_bytes))
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create_secret()
        self._download_verification_secrets = self._verification_secret_candidates(
            self._secret
        )
        self._integrity_secret = self._load_or_create_integrity_secret()
        self._integrity_verification_secrets = self._verification_secret_candidates(
            self._integrity_secret
        )
        self._legacy_download_token_accept_until = (
            self._load_or_create_legacy_download_token_window()
        )
        # Pure pre-commit work is guarded only inside this process.  It avoids
        # duplicate expensive projections without writing a durable claim
        # that an exception or process crash could strand.  The durable,
        # cross-process claim is acquired later at the persistence boundary.
        self._draft_preparation_guard = threading.Lock()
        self._draft_preparations: set[tuple[str, str, str]] = set()

    @staticmethod
    def _verification_secret_candidates(raw: bytes) -> tuple[bytes, ...]:
        """Return the exact key plus the one legacy text-trimmed variant.

        Releases through 0.5.4 reloaded binary keys with ``bytes.strip()``.
        A deployment that had already restarted may therefore have signed
        records and download tokens with the trimmed key even though the key
        file still contains the original edge bytes.  New signatures always
        use the exact binary key; verification accepts the legacy variant only
        when it is distinct and still a valid-strength key.
        """

        candidates = [raw]
        legacy = raw.strip()
        if legacy != raw and len(legacy) >= 32:
            candidates.append(legacy)
        return tuple(candidates)

    def _load_or_create_secret(self) -> bytes:
        path = self.root / ".download_secret"
        with self.lock("download-secret"):
            if path.is_file():
                # This is an opaque binary key, not text.  Trimming it makes
                # the in-memory key used on first boot differ from the key
                # reloaded after a restart whenever a random edge byte happens
                # to be ASCII whitespace, invalidating every existing token.
                raw = path.read_bytes()
                if len(raw) >= 32 and raw.strip():
                    return raw
                raise RuntimeError(
                    "download_secret_invalid: existing key is shorter than 32 "
                    "bytes or contains only whitespace"
                )
            if any(self.projects_dir.iterdir()):
                raise RuntimeError(
                    "download_secret_missing_for_existing_state: restore the "
                    "matching persistent secret before startup"
                )
            raw = secrets.token_bytes(48)
            self.atomic_write_bytes(path, raw)
            with contextlib.suppress(OSError):
                path.chmod(0o600)
            return raw

    def _load_or_create_integrity_secret(self) -> bytes:
        """Return a restart-stable key dedicated to internal record integrity.

        It is deliberately separate from the download-link key and from every
        OAuth/Auth0 secret.  Railway's persistent data directory keeps the key
        stable across deployments without exposing it in tool output.
        """

        path = self.root / ".record_integrity_secret"
        with self.lock("record-integrity-secret"):
            if path.is_file():
                # Preserve all bytes exactly.  secrets.token_bytes() can
                # legitimately produce whitespace-valued edge bytes; treating
                # the key as text would make signed records fail after restart.
                raw = path.read_bytes()
                if len(raw) >= 32 and raw.strip():
                    return raw
                raise RuntimeError(
                    "record_integrity_secret_invalid: existing key is shorter "
                    "than 32 bytes or contains only whitespace"
                )
            if any(self.projects_dir.iterdir()):
                raise RuntimeError(
                    "record_integrity_secret_missing_for_existing_state: restore "
                    "the matching persistent secret before startup"
                )
            raw = secrets.token_bytes(48)
            self.atomic_write_bytes(path, raw)
            with contextlib.suppress(OSError):
                path.chmod(0o600)
            return raw

    def _load_or_create_legacy_download_token_window(self) -> int:
        """Persist a crash-safe, non-renewable per-install legacy deadline."""

        window_path = self.root / ".legacy_download_token_window.json"
        sentinel_path = self.root / ".v055_security_epoch.json"
        anchor_path = self.projects_dir / ".v055_security_epoch.anchor.json"

        def signed_record(
            *, record_type: str, cutoff: int, epoch_id: str, status: str
        ) -> dict[str, Any]:
            record = {
                "record_id": record_type,
                "record_type": record_type,
                "epoch_id": epoch_id,
                "status": status,
                "created_at": utc_stamp(),
                "accept_until_epoch": cutoff,
            }
            record["integrity_hmac"] = self._record_integrity_hmac_with_secret(
                record, self._integrity_secret
            )
            return record

        def load_raw_signed(path: Path, *, record_type: str) -> dict[str, Any]:
            try:
                record = self.read_json(path)
                if not isinstance(record, dict):
                    raise TypeError("security record is not an object")
                signature = record.get("integrity_hmac")
                expected_signature = self._record_integrity_hmac_with_secret(
                    record, self._integrity_secret
                )
                cutoff = record.get("accept_until_epoch")
                epoch_id = record.get("epoch_id")
                if (
                    record.get("record_type") != record_type
                    or not isinstance(signature, str)
                    or not hmac.compare_digest(signature, expected_signature)
                    or not isinstance(cutoff, int)
                    or isinstance(cutoff, bool)
                    or cutoff <= 0
                    or not isinstance(epoch_id, str)
                    or not epoch_id
                ):
                    raise ValueError("invalid signed security record")
                return record
            except (OSError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "legacy_download_token_window_invalid: restore the matching "
                    "persistent state"
                ) from exc

        with self.lock("legacy-download-token-window"):
            sentinel_exists = sentinel_path.is_file()
            window_exists = window_path.is_file()
            anchor_exists = anchor_path.is_file()
            anchor = (
                load_raw_signed(
                    anchor_path, record_type="v055_security_epoch_anchor"
                )
                if anchor_exists
                else None
            )
            if sentinel_exists:
                sentinel = load_raw_signed(
                    sentinel_path, record_type="v055_security_epoch"
                )
                sentinel_status = sentinel.get("status")
                if sentinel_status not in {"preparing", "committed"}:
                    raise RuntimeError("legacy_download_token_window_invalid")
                cutoff = int(sentinel["accept_until_epoch"])
                epoch_id = str(sentinel["epoch_id"])
                if anchor is None:
                    anchor = signed_record(
                        record_type="v055_security_epoch_anchor",
                        cutoff=cutoff,
                        epoch_id=epoch_id,
                        status=str(sentinel_status),
                    )
                    self.atomic_write_json(anchor_path, anchor)
                elif (
                    anchor.get("epoch_id") != epoch_id
                    or anchor.get("accept_until_epoch") != cutoff
                    or anchor.get("status") not in {"preparing", "committed"}
                ):
                    raise RuntimeError("v055_security_epoch_anchor_mismatch")
                if not window_exists:
                    if sentinel_status != "preparing":
                        raise RuntimeError(
                            "legacy_download_token_window_missing: committed "
                            "security epoch cannot be renewed"
                        )
                    window = signed_record(
                        record_type="legacy_download_token_window",
                        cutoff=cutoff,
                        epoch_id=epoch_id,
                        status="active",
                    )
                    self.atomic_write_json(window_path, window)
                else:
                    window = load_raw_signed(
                        window_path,
                        record_type="legacy_download_token_window",
                    )
                    if (
                        window.get("epoch_id") != epoch_id
                        or window.get("accept_until_epoch") != cutoff
                        or window.get("status") != "active"
                    ):
                        raise RuntimeError(
                            "legacy_download_token_window_epoch_mismatch"
                        )
                if sentinel_status == "preparing":
                    sentinel["status"] = "committed"
                    sentinel["committed_at"] = utc_stamp()
                    sentinel["integrity_hmac"] = (
                        self._record_integrity_hmac_with_secret(
                            sentinel, self._integrity_secret
                        )
                    )
                    self.atomic_write_json(sentinel_path, sentinel)
                if anchor.get("status") == "preparing":
                    anchor["status"] = "committed"
                    anchor["committed_at"] = utc_stamp()
                    anchor["integrity_hmac"] = (
                        self._record_integrity_hmac_with_secret(
                            anchor, self._integrity_secret
                        )
                    )
                    self.atomic_write_json(anchor_path, anchor)
                return cutoff

            if window_exists:
                # Recover an interim/pre-sentinel 0.5.5 state without opening
                # a new window. The existing window must carry a valid raw-key
                # signature, and its exact epoch/cutoff become immutable.
                try:
                    window = load_raw_signed(
                        window_path,
                        record_type="legacy_download_token_window",
                    )
                except RuntimeError:
                    try:
                        interim = self.read_json(window_path)
                        interim_signature = interim.get("integrity_hmac")
                        interim_expected = self._record_integrity_hmac_with_secret(
                            interim, self._integrity_secret
                        )
                        interim_cutoff = interim.get("accept_until_epoch")
                        if (
                            not isinstance(interim_signature, str)
                            or not hmac.compare_digest(
                                interim_signature, interim_expected
                            )
                            or interim.get("record_type")
                            != "legacy_download_token_window"
                            or not isinstance(interim_cutoff, int)
                            or isinstance(interim_cutoff, bool)
                            or interim_cutoff <= 0
                            or "epoch_id" in interim
                            or "status" in interim
                        ):
                            raise ValueError("invalid interim security window")
                        interim_epoch_id = (
                            "SEC-"
                            + sha256_bytes(
                                f"{interim_signature}:{interim_cutoff}".encode(
                                    "utf-8"
                                )
                            )[:32]
                        )
                        window = signed_record(
                            record_type="legacy_download_token_window",
                            cutoff=interim_cutoff,
                            epoch_id=interim_epoch_id,
                            status="active",
                        )
                        self.atomic_write_json(window_path, window)
                    except (AttributeError, OSError, TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "legacy_download_token_window_invalid"
                        ) from exc
                if window.get("status") != "active":
                    raise RuntimeError("legacy_download_token_window_invalid")
                if anchor is not None and (
                    anchor.get("epoch_id") != window.get("epoch_id")
                    or anchor.get("accept_until_epoch")
                    != window.get("accept_until_epoch")
                    or anchor.get("status") not in {"preparing", "committed"}
                ):
                    raise RuntimeError("v055_security_epoch_anchor_mismatch")
                sentinel = signed_record(
                    record_type="v055_security_epoch",
                    cutoff=int(window["accept_until_epoch"]),
                    epoch_id=str(window["epoch_id"]),
                    status="committed",
                )
                sentinel["committed_at"] = utc_stamp()
                sentinel["integrity_hmac"] = (
                    self._record_integrity_hmac_with_secret(
                        sentinel, self._integrity_secret
                    )
                )
                self.atomic_write_json(sentinel_path, sentinel)
                if anchor is None:
                    anchor = signed_record(
                        record_type="v055_security_epoch_anchor",
                        cutoff=int(window["accept_until_epoch"]),
                        epoch_id=str(window["epoch_id"]),
                        status="committed",
                    )
                    anchor["committed_at"] = utc_stamp()
                    anchor["integrity_hmac"] = (
                        self._record_integrity_hmac_with_secret(
                            anchor, self._integrity_secret
                        )
                    )
                    self.atomic_write_json(anchor_path, anchor)
                elif anchor.get("status") == "preparing":
                    anchor["status"] = "committed"
                    anchor["committed_at"] = utc_stamp()
                    anchor["integrity_hmac"] = (
                        self._record_integrity_hmac_with_secret(
                            anchor, self._integrity_secret
                        )
                    )
                    self.atomic_write_json(anchor_path, anchor)
                return int(window["accept_until_epoch"])
            if anchor is not None:
                if anchor.get("status") != "preparing":
                    raise RuntimeError(
                        "v055_security_epoch_files_missing: committed anchor "
                        "forbids security-window rebootstrap"
                    )
                cutoff = int(anchor["accept_until_epoch"])
                epoch_id = str(anchor["epoch_id"])
                sentinel = signed_record(
                    record_type="v055_security_epoch",
                    cutoff=cutoff,
                    epoch_id=epoch_id,
                    status="preparing",
                )
                self.atomic_write_json(sentinel_path, sentinel)
                window = signed_record(
                    record_type="legacy_download_token_window",
                    cutoff=cutoff,
                    epoch_id=epoch_id,
                    status="active",
                )
                self.atomic_write_json(window_path, window)
                sentinel["status"] = "committed"
                sentinel["committed_at"] = utc_stamp()
                sentinel["integrity_hmac"] = (
                    self._record_integrity_hmac_with_secret(
                        sentinel, self._integrity_secret
                    )
                )
                self.atomic_write_json(sentinel_path, sentinel)
                anchor["status"] = "committed"
                anchor["committed_at"] = utc_stamp()
                anchor["integrity_hmac"] = (
                    self._record_integrity_hmac_with_secret(
                        anchor, self._integrity_secret
                    )
                )
                self.atomic_write_json(anchor_path, anchor)
                return cutoff
            if self._project_state_declares_v055():
                raise RuntimeError(
                    "v055_security_epoch_missing_for_existing_state: restore "
                    "the matching security records"
                )

            cutoff = int(time.time()) + LEGACY_DOWNLOAD_TOKEN_WINDOW_SECONDS
            epoch_id = f"SEC-{secrets.token_hex(16)}"
            sentinel = signed_record(
                record_type="v055_security_epoch",
                cutoff=cutoff,
                epoch_id=epoch_id,
                status="preparing",
            )
            self.atomic_write_json(sentinel_path, sentinel)
            anchor = signed_record(
                record_type="v055_security_epoch_anchor",
                cutoff=cutoff,
                epoch_id=epoch_id,
                status="preparing",
            )
            self.atomic_write_json(anchor_path, anchor)
            window = signed_record(
                record_type="legacy_download_token_window",
                cutoff=cutoff,
                epoch_id=epoch_id,
                status="active",
            )
            self.atomic_write_json(window_path, window)
            sentinel["status"] = "committed"
            sentinel["committed_at"] = utc_stamp()
            sentinel["integrity_hmac"] = self._record_integrity_hmac_with_secret(
                sentinel, self._integrity_secret
            )
            self.atomic_write_json(sentinel_path, sentinel)
            anchor["status"] = "committed"
            anchor["committed_at"] = utc_stamp()
            anchor["integrity_hmac"] = self._record_integrity_hmac_with_secret(
                anchor, self._integrity_secret
            )
            self.atomic_write_json(anchor_path, anchor)
            return cutoff

    def _project_state_declares_v055(self) -> bool:
        for path in self.projects_dir.rglob("hub.json"):
            try:
                hub = self.read_json(path)
                policy = hub.get("policy") if isinstance(hub, dict) else None
                version = (
                    policy.get("compact_master_version")
                    if isinstance(policy, dict)
                    else None
                )
                if version == "0.5.5":
                    return True
            except (OSError, TypeError, ValueError):
                continue
        for path in self.projects_dir.rglob("migrations/*.json"):
            try:
                record = self.read_json(path)
                if (
                    isinstance(record, dict)
                    and record.get("target_version") == "0.5.5"
                ):
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    @contextlib.contextmanager
    def lock(self, name: str) -> Iterator[None]:
        lock_name = slug_filename(name, "state")
        lock_path = self.root / f".{lock_name}.lock"
        with FileLock(str(lock_path)):
            yield

    @staticmethod
    def atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    @classmethod
    def atomic_write_json(cls, path: Path, value: Any) -> None:
        cls.atomic_write_bytes(
            path,
            (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    @staticmethod
    def read_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def namespace_dir(self, namespace: str) -> Path:
        namespace_key = self.namespace_key(namespace)
        path = self.projects_dir / namespace_key
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def namespace_key(namespace: str) -> str:
        return hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:24]

    def project_dir(self, namespace: str, project_id: str) -> Path:
        safe_id(project_id, "project_id")
        return self.namespace_dir(namespace) / project_id

    def project_exists(self, namespace: str, project_id: str) -> bool:
        return (self.project_dir(namespace, project_id) / "hub.json").is_file()

    def create_project(self, namespace: str, hub: dict[str, Any], ledger: dict[str, Any]) -> None:
        project_id = safe_id(str(hub["project_id"]), "project_id")
        directory = self.project_dir(namespace, project_id)
        with self.lock(f"{namespace}-{project_id}"):
            if (directory / "hub.json").exists():
                raise ValueError("project already exists")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "artifacts").mkdir(parents=True, exist_ok=True)
            self.atomic_write_json(directory / "hub.json", hub)
            self.atomic_write_json(directory / "ledger.json", ledger)

    def list_projects(self, namespace: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        base = self.namespace_dir(namespace)
        for hub_path in sorted(base.glob("*/hub.json")):
            try:
                hub = self.read_json(hub_path)
                result.append(
                    {
                        "project_id": hub.get("project_id"),
                        "project_name": hub.get("project_name"),
                        "updated_at": hub.get("updated_at"),
                        "current_stage": hub.get("project_state", {}).get("current_stage"),
                        "work_order_count": len(hub.get("work_orders", [])),
                        "deliverable_count": len(hub.get("deliverables", [])),
                    }
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        result.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return result

    def load_hub(self, namespace: str, project_id: str) -> dict[str, Any]:
        path = self.project_dir(namespace, project_id) / "hub.json"
        if not path.is_file():
            raise ValueError(f"unknown project_id: {project_id}")
        return self.read_json(path)

    def save_hub(self, namespace: str, project_id: str, hub: dict[str, Any]) -> None:
        path = self.project_dir(namespace, project_id) / "hub.json"
        with self.lock(f"{namespace}-{project_id}"):
            self.atomic_write_json(path, hub)

    def load_ledger(self, namespace: str, project_id: str) -> dict[str, Any]:
        path = self.project_dir(namespace, project_id) / "ledger.json"
        if not path.is_file():
            raise ValueError(f"unknown project_id: {project_id}")
        return self.read_json(path)

    def save_ledger(self, namespace: str, project_id: str, ledger: dict[str, Any]) -> None:
        path = self.project_dir(namespace, project_id) / "ledger.json"
        with self.lock(f"{namespace}-{project_id}"):
            self.atomic_write_json(path, ledger)

    @staticmethod
    def project_state_sha256(value: Any) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256_bytes(payload)

    def commit_project_state_migration(
        self,
        namespace: str,
        project_id: str,
        *,
        migration_id: str,
        migration_key: str,
        work_order_id: str,
        from_version: str,
        target_version: str,
        hub_before: dict[str, Any],
        ledger_before: dict[str, Any],
        hub_after: dict[str, Any],
        ledger_after: dict[str, Any],
        runtime_test_certificate_id: str,
        ruleset_version: str,
        validator_version: str,
        changed_paths: list[str],
    ) -> dict[str, Any]:
        """Commit or recover one journaled hub+ledger migration.

        The signed before/after journal makes an interrupted two-file replace
        recoverable.  Any state outside the journal's CAS identities fails
        closed instead of overwriting subsequent project work.
        """

        clean_id = safe_id(migration_id, "migration_id")
        directory = self.project_dir(namespace, project_id)
        hub_path = directory / "hub.json"
        ledger_path = directory / "ledger.json"
        record_path = directory / "migrations" / f"{clean_id}.json"
        before_hub_sha = self.project_state_sha256(hub_before)
        before_ledger_sha = self.project_state_sha256(ledger_before)
        after_hub_sha = self.project_state_sha256(hub_after)
        after_ledger_sha = self.project_state_sha256(ledger_after)

        with self.lock(f"{namespace}-{project_id}"):
            current_hub = self.read_json(hub_path)
            current_ledger = self.read_json(ledger_path)
            current_hub_sha = self.project_state_sha256(current_hub)
            current_ledger_sha = self.project_state_sha256(current_ledger)
            record: dict[str, Any] | None = None
            if record_path.is_file():
                record = self.read_json(record_path)
                self._verify_record_integrity(record, record_id=clean_id)
                if not self.project_record_is_signed(record):
                    raise ValueError("migration_record_signature_required")
                if record.get("migration_key") != migration_key:
                    raise ValueError("migration_id_conflict")
                before_hub_sha = str(record["hub_before_sha256"])
                before_ledger_sha = str(record["ledger_before_sha256"])
                after_hub_sha = str(record["hub_after_sha256"])
                after_ledger_sha = str(record["ledger_after_sha256"])
                hub_after = record["hub_after"]
                ledger_after = record["ledger_after"]

            if (current_hub_sha, current_ledger_sha) == (after_hub_sha, after_ledger_sha):
                if record is not None and record.get("status") != "committed":
                    record["status"] = "committed"
                    record["committed_at"] = utc_stamp()
                    record["integrity_hmac"] = self._record_integrity_hmac(record)
                    self.atomic_write_json(record_path, record)
                return record or {
                    "migration_id": clean_id,
                    "migration_key": migration_key,
                    "status": "committed",
                    "idempotent_replay": True,
                }

            valid_hub = current_hub_sha in {before_hub_sha, after_hub_sha}
            valid_ledger = current_ledger_sha in {before_ledger_sha, after_ledger_sha}
            if not valid_hub or not valid_ledger:
                raise ValueError("migration_recovery_required: project state changed outside migration CAS")
            if record is None and (current_hub_sha, current_ledger_sha) != (
                before_hub_sha,
                before_ledger_sha,
            ):
                raise ValueError("migration_recovery_required: unsigned partial migration")

            if record is None:
                record = {
                    "record_id": clean_id,
                    "migration_id": clean_id,
                    "migration_key": migration_key,
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "status": "prepared",
                    "created_at": utc_stamp(),
                    "from_version": from_version,
                    "target_version": target_version,
                    "hub_before_sha256": before_hub_sha,
                    "ledger_before_sha256": before_ledger_sha,
                    "hub_after_sha256": after_hub_sha,
                    "ledger_after_sha256": after_ledger_sha,
                    "hub_before": hub_before,
                    "ledger_before": ledger_before,
                    "hub_after": hub_after,
                    "ledger_after": ledger_after,
                    "runtime_test_certificate_id": runtime_test_certificate_id,
                    "ruleset_version": ruleset_version,
                    "validator_version": validator_version,
                    "changed_paths": list(changed_paths),
                    "preserves_artifacts": True,
                }
                record["integrity_hmac"] = self._record_integrity_hmac(record)
                self.atomic_write_json(record_path, record)

            if current_hub_sha == before_hub_sha:
                self.atomic_write_json(hub_path, hub_after)
            if current_ledger_sha == before_ledger_sha:
                self.atomic_write_json(ledger_path, ledger_after)
            final_hub_sha = self.project_state_sha256(self.read_json(hub_path))
            final_ledger_sha = self.project_state_sha256(self.read_json(ledger_path))
            if (final_hub_sha, final_ledger_sha) != (after_hub_sha, after_ledger_sha):
                raise RuntimeError("migration_commit_incomplete")
            record["status"] = "committed"
            record["committed_at"] = utc_stamp()
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(record_path, record)
            return record

    def commit_handoff_recovery(
        self,
        namespace: str,
        project_id: str,
        *,
        recovery_id: str,
        recovery_key: str,
        work_order_id: str,
        gap_id: str,
        trace_id: str,
        old_handoff_id: str,
        new_handoff_id: str,
        old_before: dict[str, Any],
        old_after: dict[str, Any],
        new_after: dict[str, Any],
        hub_before: dict[str, Any],
        hub_after: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit or recover one journaled handoff supersession.

        The signed journal is the durable transaction descriptor.  The old
        handoff is written first so an interrupted transaction fails closed:
        independent audit can never claim stale evidence while the successor
        is still missing.  A retry may only complete the exact journaled
        before/after identities; unrelated project changes fail the CAS.
        """

        clean_recovery_id = safe_id(recovery_id, "recovery_id")
        clean_old_id = safe_id(old_handoff_id, "old_handoff_id")
        clean_new_id = safe_id(new_handoff_id, "new_handoff_id")
        directory = self.project_dir(namespace, project_id)
        old_path = directory / "handoffs" / f"{clean_old_id}.json"
        new_path = directory / "handoffs" / f"{clean_new_id}.json"
        hub_path = directory / "hub.json"
        journal_path = (
            directory / "handoff_recoveries" / f"{clean_recovery_id}.json"
        )

        def sealed(value: dict[str, Any], record_id: str) -> dict[str, Any]:
            result = dict(value)
            result["record_id"] = record_id
            result["project_id"] = project_id
            result["integrity_hmac"] = self._record_integrity_hmac(result)
            return result

        old_after = sealed(old_after, clean_old_id)
        new_after = sealed(new_after, clean_new_id)
        old_before_sha = self.project_state_sha256(old_before)
        old_after_sha = self.project_state_sha256(old_after)
        new_after_sha = self.project_state_sha256(new_after)
        hub_before_sha = self.project_state_sha256(hub_before)
        hub_after_sha = self.project_state_sha256(hub_after)

        with self.lock(f"{namespace}-{project_id}"):
            if not old_path.is_file():
                raise ValueError("handoff_recovery_required: old handoff is missing")
            current_old = self.read_json(old_path)
            self._verify_record_integrity(current_old, record_id=clean_old_id)
            if not self.project_record_is_signed(current_old):
                raise ValueError("handoff_reissue_required: unsigned handoff")
            current_hub = self.read_json(hub_path)
            current_old_sha = self.project_state_sha256(current_old)
            current_hub_sha = self.project_state_sha256(current_hub)
            current_new: dict[str, Any] | None = None
            current_new_sha: str | None = None
            if new_path.is_file():
                current_new = self.read_json(new_path)
                self._verify_record_integrity(current_new, record_id=clean_new_id)
                if not self.project_record_is_signed(current_new):
                    raise ValueError("handoff_recovery_required: unsigned successor")
                current_new_sha = self.project_state_sha256(current_new)

            journal: dict[str, Any] | None = None
            if journal_path.is_file():
                journal = self.read_json(journal_path)
                self._verify_record_integrity(
                    journal, record_id=clean_recovery_id
                )
                if not self.project_record_is_signed(journal):
                    raise ValueError("handoff_recovery_signature_required")
                if (
                    journal.get("recovery_key") != recovery_key
                    or journal.get("old_handoff_id") != clean_old_id
                    or journal.get("new_handoff_id") != clean_new_id
                ):
                    raise ValueError("handoff_recovery_id_conflict")
                old_before_sha = str(journal["old_before_sha256"])
                old_after_sha = str(journal["old_after_sha256"])
                new_after_sha = str(journal["new_after_sha256"])
                hub_before_sha = str(journal["hub_before_sha256"])
                hub_after_sha = str(journal["hub_after_sha256"])
                old_after = dict(journal["old_after"])
                new_after = dict(journal["new_after"])
                hub_after = dict(journal["hub_after"])

            if (
                current_old_sha == old_after_sha
                and current_new_sha == new_after_sha
                and current_hub_sha == hub_after_sha
            ):
                if journal is not None and journal.get("status") != "committed":
                    journal["status"] = "committed"
                    journal["committed_at"] = utc_stamp()
                    journal["integrity_hmac"] = self._record_integrity_hmac(
                        journal
                    )
                    self.atomic_write_json(journal_path, journal)
                result = dict(journal or {})
                result.update(
                    {
                        "recovery_id": clean_recovery_id,
                        "old_handoff_id": clean_old_id,
                        "new_handoff_id": clean_new_id,
                        "status": "committed",
                        "idempotent_replay": True,
                    }
                )
                return result

            if current_old_sha not in {old_before_sha, old_after_sha}:
                raise ValueError(
                    "handoff_recovery_required: old handoff changed outside recovery CAS"
                )
            if current_hub_sha not in {hub_before_sha, hub_after_sha}:
                raise ValueError(
                    "handoff_recovery_required: project hub changed outside recovery CAS"
                )
            if current_new_sha not in {None, new_after_sha}:
                raise ValueError(
                    "handoff_recovery_required: successor id is already occupied"
                )
            if journal is None and (
                current_old_sha != old_before_sha
                or current_hub_sha != hub_before_sha
                or current_new_sha is not None
            ):
                raise ValueError(
                    "handoff_recovery_required: unsigned partial recovery"
                )

            if journal is None:
                journal = {
                    "record_id": clean_recovery_id,
                    "recovery_id": clean_recovery_id,
                    "recovery_key": recovery_key,
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "gap_id": gap_id,
                    "trace_id": trace_id,
                    "old_handoff_id": clean_old_id,
                    "new_handoff_id": clean_new_id,
                    "status": "prepared",
                    "created_at": utc_stamp(),
                    "old_before_sha256": old_before_sha,
                    "old_after_sha256": old_after_sha,
                    "new_after_sha256": new_after_sha,
                    "hub_before_sha256": hub_before_sha,
                    "hub_after_sha256": hub_after_sha,
                    "old_after": old_after,
                    "new_after": new_after,
                    "hub_after": hub_after,
                    "history_preserved": True,
                    "independent_audit_status": "PENDING_AUDIT",
                }
                journal["integrity_hmac"] = self._record_integrity_hmac(
                    journal
                )
                self.atomic_write_json(journal_path, journal)

            # Fail closed first: the stale handoff becomes unclaimable before
            # the replacement is made visible.  The signed journal makes every
            # following interruption idempotently recoverable.
            if current_old_sha == old_before_sha:
                self.atomic_write_json(old_path, old_after)
            if current_new_sha is None:
                self.atomic_write_json(new_path, new_after)
            if current_hub_sha == hub_before_sha:
                self.atomic_write_json(hub_path, hub_after)

            final_old_sha = self.project_state_sha256(self.read_json(old_path))
            final_new_sha = self.project_state_sha256(self.read_json(new_path))
            final_hub_sha = self.project_state_sha256(self.read_json(hub_path))
            if (final_old_sha, final_new_sha, final_hub_sha) != (
                old_after_sha,
                new_after_sha,
                hub_after_sha,
            ):
                raise RuntimeError("handoff_recovery_commit_incomplete")
            journal["status"] = "committed"
            journal["committed_at"] = utc_stamp()
            journal["integrity_hmac"] = self._record_integrity_hmac(journal)
            self.atomic_write_json(journal_path, journal)
            return journal

    def rollback_project_state_migration(
        self,
        namespace: str,
        project_id: str,
        migration_id: str,
    ) -> dict[str, Any]:
        """CAS rollback with a signed journal and interrupted-write recovery."""

        clean_id = safe_id(migration_id, "migration_id")
        directory = self.project_dir(namespace, project_id)
        record_path = directory / "migrations" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not record_path.is_file():
                raise ValueError("unknown migration_id")
            record = self.read_json(record_path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("migration_record_signature_required")
            hub_path = directory / "hub.json"
            ledger_path = directory / "ledger.json"
            current_hub_sha = self.project_state_sha256(self.read_json(hub_path))
            current_ledger_sha = self.project_state_sha256(
                self.read_json(ledger_path)
            )
            if record.get("status") == "rolled_back":
                if (
                    current_hub_sha == record.get("hub_before_sha256")
                    and current_ledger_sha == record.get("ledger_before_sha256")
                ):
                    result = dict(record)
                    result["idempotent_replay"] = True
                    return result
                raise ValueError(
                    "migration_rollback_cas_mismatch: project changed after rollback"
                )
            status = str(record.get("status") or "")
            if status not in {"committed", "rolling_back"}:
                raise ValueError(
                    f"migration_rollback_state_conflict: migration status is {status!r}"
                )
            before_hub_sha = str(record.get("hub_before_sha256") or "")
            before_ledger_sha = str(record.get("ledger_before_sha256") or "")
            after_hub_sha = str(record.get("hub_after_sha256") or "")
            after_ledger_sha = str(record.get("ledger_after_sha256") or "")
            if status == "committed" and (
                current_hub_sha,
                current_ledger_sha,
            ) != (after_hub_sha, after_ledger_sha):
                raise ValueError(
                    "migration_rollback_cas_mismatch: project changed after migration"
                )
            if status == "rolling_back" and (
                current_hub_sha not in {before_hub_sha, after_hub_sha}
                or current_ledger_sha not in {before_ledger_sha, after_ledger_sha}
            ):
                raise ValueError(
                    "migration_rollback_cas_mismatch: project changed during rollback"
                )
            if status == "committed":
                # Sign intent before either project-state file changes. A retry
                # can then distinguish a legitimate half rollback from an
                # unrelated write and complete only the missing side.
                record["status"] = "rolling_back"
                record["rollback_started_at"] = utc_stamp()
                record["integrity_hmac"] = self._record_integrity_hmac(record)
                self.atomic_write_json(record_path, record)
            if current_hub_sha == after_hub_sha:
                self.atomic_write_json(hub_path, record["hub_before"])
            if current_ledger_sha == after_ledger_sha:
                self.atomic_write_json(ledger_path, record["ledger_before"])
            final_hub_sha = self.project_state_sha256(self.read_json(hub_path))
            final_ledger_sha = self.project_state_sha256(
                self.read_json(ledger_path)
            )
            if (final_hub_sha, final_ledger_sha) != (
                before_hub_sha,
                before_ledger_sha,
            ):
                raise RuntimeError("migration_rollback_incomplete")
            record["status"] = "rolled_back"
            record["rolled_back_at"] = utc_stamp()
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(record_path, record)
            return record

    @contextlib.contextmanager
    def draft_preflight_commit_preparation(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
    ) -> Iterator[None]:
        """Guard pure pre-commit work without creating recoverable state."""

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        key = (self.namespace_key(namespace), safe_id(project_id, "project_id"), clean_id)
        with self._draft_preparation_guard:
            if key in self._draft_preparations:
                raise ValueError("draft_preflight_commit_in_progress")
            self._draft_preparations.add(key)
        try:
            yield
        finally:
            with self._draft_preparation_guard:
                self._draft_preparations.discard(key)

    def claim_draft_preflight_commit(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        request_sha256: str,
        lease_seconds: int = DEFAULT_DRAFT_PREFLIGHT_COMMIT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Atomically reserve one passed draft certificate for one commit.

        A same-request retry may take over only after the bounded lease has
        expired.  A different request can never repurpose the certificate,
        even after expiry.  The claim token fences a late owner at completion.
        """

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        if not isinstance(request_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", request_sha256
        ):
            raise ValueError("invalid draft_preflight commit request_sha256")
        try:
            lease_seconds = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid draft_preflight commit lease_seconds") from exc
        if not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS:
            raise ValueError(
                "draft_preflight commit lease_seconds must be between "
                f"1 and {MAX_CONSUMPTION_LEASE_SECONDS}"
            )
        path = (
            self.project_dir(namespace, project_id)
            / "draft_preflights"
            / f"{clean_id}.json"
        )
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError(f"unknown draft_preflight_id: {clean_id}")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("draft_preflight_signature_required")
            bound_request = record.get("commit_request_sha256")
            if bound_request not in {None, request_sha256}:
                raise ValueError("draft_preflight_commit_claim_conflict")
            if record.get("consumed_by_commit"):
                return {"claim_status": "consumed", "record": record}
            claim = record.get("commit_claim")
            if isinstance(claim, dict):
                if claim.get("request_sha256") != request_sha256:
                    raise ValueError("draft_preflight_commit_claim_conflict")
                try:
                    lease_expires_epoch = float(
                        claim.get("lease_expires_epoch_seconds") or 0
                    )
                except (TypeError, ValueError):
                    lease_expires_epoch = 0
                if lease_expires_epoch <= 0:
                    try:
                        claimed_epoch = float(
                            claim.get("claim_epoch_seconds")
                            or path.stat().st_mtime
                        )
                    except (TypeError, ValueError, OSError):
                        claimed_epoch = time.time()
                    lease_expires_epoch = claimed_epoch + lease_seconds
                if lease_expires_epoch > time.time():
                    return {"claim_status": "in_progress", "record": record}
                expired = dict(claim)
                expired["state"] = "lease_expired"
                expired["expired_at"] = utc_stamp()
                history = record.setdefault("commit_claim_history", [])
                if not isinstance(history, list):
                    history = []
                    record["commit_claim_history"] = history
                history.append(expired)
                del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            now = time.time()
            token = f"DPC-{uuid.uuid4().hex}"
            try:
                generation = int(record.get("commit_claim_generation") or 0) + 1
            except (TypeError, ValueError):
                generation = 1
            record["commit_claim"] = {
                "claim_token": token,
                "request_sha256": request_sha256,
                "generation": generation,
                "state": "claimed",
                "claimed_at": utc_stamp(),
                "claim_epoch_seconds": now,
                "lease_seconds": lease_seconds,
                "lease_expires_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lease_seconds)
                ),
                "lease_expires_epoch_seconds": now + lease_seconds,
            }
            record["commit_request_sha256"] = request_sha256
            record["commit_claim_generation"] = generation
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return {
                "claim_status": "claimed",
                "claim_token": token,
                "claim_generation": generation,
                "record": record,
            }

    def release_draft_preflight_commit(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        claim_token: str,
        reason: str,
    ) -> dict[str, Any]:
        """Release an unconsumed claim, fenced to its exact owner token."""

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        path = (
            self.project_dir(namespace, project_id)
            / "draft_preflights"
            / f"{clean_id}.json"
        )
        with self.lock(f"{namespace}-{project_id}"):
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("draft_preflight_signature_required")
            if record.get("consumed_by_commit"):
                return record
            claim = record.get("commit_claim")
            if not isinstance(claim, dict) or claim.get("claim_token") != claim_token:
                raise ValueError("draft_preflight_commit_claim_owner_mismatch")
            released = dict(claim)
            released["state"] = "released"
            released["released_at"] = utc_stamp()
            released["release_reason"] = str(reason or "commit_failed")[:256]
            history = record.setdefault("commit_claim_history", [])
            if not isinstance(history, list):
                history = []
                record["commit_claim_history"] = history
            history.append(released)
            del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            record.pop("commit_claim", None)
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def update_draft_preflight_commit_progress(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        claim_token: str,
        stage: str,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance the fenced, restart-stable commit recovery journal."""

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        path = (
            self.project_dir(namespace, project_id)
            / "draft_preflights"
            / f"{clean_id}.json"
        )
        if not isinstance(stage, str) or not stage or len(stage) > 64:
            raise ValueError("invalid draft_preflight commit progress stage")
        with self.lock(f"{namespace}-{project_id}"):
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("draft_preflight_signature_required")
            if record.get("consumed_by_commit"):
                return record
            claim = record.get("commit_claim")
            if not isinstance(claim, dict) or claim.get("claim_token") != claim_token:
                raise ValueError("draft_preflight_commit_claim_owner_mismatch")
            journal = record.setdefault("commit_progress", {})
            if not isinstance(journal, dict):
                journal = {}
                record["commit_progress"] = journal
            journal.update(dict(progress or {}))
            journal.update(
                {
                    "request_sha256": claim.get("request_sha256"),
                    "claim_generation": claim.get("generation"),
                    "stage": stage,
                    "updated_at": utc_stamp(),
                }
            )
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def complete_draft_preflight_commit(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        claim_token: str,
        consumed_by_commit: dict[str, Any],
    ) -> dict[str, Any]:
        """Complete only the atomic claim owner; exact repeats are idempotent."""

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        path = (
            self.project_dir(namespace, project_id)
            / "draft_preflights"
            / f"{clean_id}.json"
        )
        with self.lock(f"{namespace}-{project_id}"):
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("draft_preflight_signature_required")
            if record.get("consumed_by_commit"):
                return record
            claim = record.get("commit_claim")
            if not isinstance(claim, dict) or claim.get("claim_token") != claim_token:
                raise ValueError("draft_preflight_commit_claim_owner_mismatch")
            record["consumed_by_commit"] = dict(consumed_by_commit)
            record["commit_claim"]["state"] = "completed"
            record["commit_claim"]["completed_at"] = utc_stamp()
            journal = record.setdefault("commit_progress", {})
            if isinstance(journal, dict):
                journal.update(
                    {
                        "stage": "completed",
                        "updated_at": utc_stamp(),
                        "artifact_id": consumed_by_commit.get("artifact_id"),
                        "canonical_json_artifact_id": consumed_by_commit.get(
                            "canonical_json_artifact_id"
                        ),
                    }
                )
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    @contextlib.contextmanager
    def mutate_project(self, namespace: str, project_id: str) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        directory = self.project_dir(namespace, project_id)
        hub_path = directory / "hub.json"
        ledger_path = directory / "ledger.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not hub_path.is_file() or not ledger_path.is_file():
                raise ValueError(f"unknown project_id: {project_id}")
            hub = self.read_json(hub_path)
            ledger = self.read_json(ledger_path)
            yield hub, ledger
            self.atomic_write_json(hub_path, hub)
            self.atomic_write_json(ledger_path, ledger)

    def save_artifact_bytes(
        self,
        namespace: str,
        project_id: str,
        data: bytes,
        *,
        file_name: str,
        artifact_type: str,
        version: str,
        work_order_id: str | None = None,
        source_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        directory = self.project_dir(namespace, project_id) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        artifact_id = (
            safe_id(artifact_id, "artifact_id")
            if artifact_id is not None
            else f"ART-{uuid.uuid4().hex[:16]}"
        )
        safe_name = slug_filename(file_name)
        payload_path = directory / f"{artifact_id}__{safe_name}"
        meta_path = directory / f"{artifact_id}.meta.json"
        record = {
            "artifact_id": artifact_id,
            "project_id": project_id,
            "artifact_type": artifact_type,
            "file_name": safe_name,
            "version": version,
            "work_order_id": work_order_id,
            "source_artifact_id": source_artifact_id,
            "created_at": utc_stamp(),
            "size_bytes": len(data),
            "fingerprint": sha256_bytes(data),
            "relative_path": str(payload_path.relative_to(self.project_dir(namespace, project_id))),
            "metadata": metadata or {},
        }
        with self.lock(f"{namespace}-{project_id}"):
            if meta_path.is_file():
                existing = self.read_json(meta_path)
                existing_path = self.project_dir(namespace, project_id) / str(
                    existing.get("relative_path") or ""
                )
                requested_commit = (metadata or {}).get(
                    "draft_commit_request_sha256"
                )
                existing_commit = (existing.get("metadata") or {}).get(
                    "draft_commit_request_sha256"
                )
                resumable_same_commit = bool(
                    requested_commit and requested_commit == existing_commit
                )
                immutable_matches = (
                    existing.get("artifact_id") == artifact_id
                    and existing.get("project_id") == project_id
                    and existing.get("artifact_type") == artifact_type
                    and existing.get("file_name") == safe_name
                    and existing.get("version") == version
                    and existing.get("work_order_id") == work_order_id
                    and existing.get("source_artifact_id") == source_artifact_id
                    and existing_path.is_file()
                    and sha256_bytes(existing_path.read_bytes())
                    == existing.get("fingerprint")
                    and (
                        resumable_same_commit
                        or existing.get("fingerprint") == sha256_bytes(data)
                    )
                )
                if immutable_matches:
                    return existing
                raise ValueError(
                    "artifact_idempotency_conflict: deterministic artifact id "
                    f"{artifact_id} is already bound to different bytes or metadata"
                )
            self.atomic_write_bytes(payload_path, data)
            self.atomic_write_json(meta_path, record)
        return record

    def save_artifact_json(
        self,
        namespace: str,
        project_id: str,
        value: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return self.save_artifact_bytes(namespace, project_id, data, **kwargs)

    @staticmethod
    def artifact_publication_state(record: dict[str, Any]) -> str:
        """Return the effective publication state of an artifact.

        Artifacts created before successor transactions did not carry this
        field and remain published for backwards compatibility.  Only an
        explicit fail-closed state is hidden from normal readers.
        """

        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            return "committed"
        return str(
            metadata.get("publication_state")
            or metadata.get("successor_publication_state")
            or "committed"
        )

    @classmethod
    def _successor_descriptor_committed(
        cls,
        directory: Path,
        *,
        base_artifact_id: str,
        transaction_id: str,
        successor_artifact_id: str,
    ) -> bool:
        if not all((base_artifact_id, transaction_id, successor_artifact_id)):
            return False
        try:
            safe_id(base_artifact_id, "base_artifact_id")
            base_path = directory / "artifacts" / f"{base_artifact_id}.meta.json"
            if not base_path.is_file():
                return False
            base_record = cls.read_json(base_path)
            base_metadata = base_record.get("metadata", {})
            transaction = base_metadata.get("successor_transaction")
            return bool(
                isinstance(transaction, dict)
                and transaction.get("state") == "committed"
                and cls._successor_transaction_id(transaction) == transaction_id
                and str(transaction.get("successor_artifact_id") or "")
                == successor_artifact_id
                and str(base_metadata.get("successor_artifact_id") or "")
                == successor_artifact_id
            )
        except (OSError, TypeError, ValueError):
            return False

    @classmethod
    def _artifact_is_published(
        cls, directory: Path, record: dict[str, Any]
    ) -> bool:
        state = cls.artifact_publication_state(record)
        metadata = record.get("metadata", {})
        transaction_id = str(metadata.get("successor_transaction_id") or "")
        if not transaction_id:
            return state == "committed"
        if state in {"aborted", "rejected"}:
            return False
        owner_artifact_id = str(
            metadata.get("publication_owner_artifact_id")
            or record.get("artifact_id")
            or ""
        )
        return cls._successor_descriptor_committed(
            directory,
            base_artifact_id=str(
                metadata.get("successor_base_artifact_id") or ""
            ),
            transaction_id=transaction_id,
            successor_artifact_id=owner_artifact_id,
        )

    @classmethod
    def _assert_artifact_published(
        cls, directory: Path, record: dict[str, Any]
    ) -> None:
        state = cls.artifact_publication_state(record)
        if not cls._artifact_is_published(directory, record):
            raise ValueError(
                f"artifact_not_committed: publication state is {state!r}"
            )

    def get_artifact(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        include_uncommitted: bool = False,
    ) -> tuple[dict[str, Any], Path]:
        safe_id(artifact_id, "artifact_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        if not meta_path.is_file():
            raise ValueError(f"unknown artifact_id: {artifact_id}")
        record = self.read_json(meta_path)
        if not include_uncommitted:
            self._assert_artifact_published(directory, record)
        payload_path = (directory / str(record["relative_path"])).resolve()
        if directory.resolve() not in payload_path.parents or not payload_path.is_file():
            raise ValueError("artifact payload is unavailable")
        # Artifact metadata is only an index.  Recompute the bytes identity on
        # every read so a workbook (or its canonical sidecar) changed outside
        # the service can never inherit a previously issued audit certificate.
        actual_fingerprint = sha256_bytes(payload_path.read_bytes())
        if actual_fingerprint != record.get("fingerprint"):
            raise ValueError(
                f"artifact_raw_fingerprint_mismatch: payload bytes changed for {artifact_id}"
            )
        return record, payload_path

    @staticmethod
    def _record_integrity_hmac_with_secret(
        value: dict[str, Any], secret: bytes
    ) -> str:
        unsigned = {key: item for key, item in value.items() if key != "integrity_hmac"}
        payload = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def _record_integrity_hmac(self, value: dict[str, Any]) -> str:
        return self._record_integrity_hmac_with_secret(value, self._integrity_secret)

    def _record_integrity_signature_matches(
        self, value: dict[str, Any], signature: str
    ) -> bool:
        return any(
            hmac.compare_digest(
                signature,
                self._record_integrity_hmac_with_secret(value, secret),
            )
            for secret in self._integrity_verification_secrets
        )

    def project_record_is_signed(self, value: dict[str, Any]) -> bool:
        signature = value.get("integrity_hmac")
        if not isinstance(signature, str):
            return False
        return self._record_integrity_signature_matches(value, signature)

    def _verify_record_integrity(self, value: dict[str, Any], *, record_id: str) -> None:
        """Verify new signed records while accepting pre-0.3 legacy records.

        Handoffs and certificates written before the integrity upgrade have no
        signature and remain readable for backwards compatibility.  Every new
        record is signed, so changing a report and recomputing a public SHA is
        still detected before repair or sign-off.
        """

        signature = value.get("integrity_hmac")
        if signature is None:
            return
        if not isinstance(signature, str) or not self._record_integrity_signature_matches(
            value, signature
        ):
            raise ValueError(f"project_record_integrity_mismatch: {record_id}")

    def list_artifacts(
        self,
        namespace: str,
        project_id: str,
        *,
        include_uncommitted: bool = False,
    ) -> list[dict[str, Any]]:
        directory = self.project_dir(namespace, project_id) / "artifacts"
        result = []
        for meta_path in sorted(directory.glob("ART-*.meta.json")):
            with contextlib.suppress(Exception):
                record = self.read_json(meta_path)
                if (
                    include_uncommitted
                    or self._artifact_is_published(
                        self.project_dir(namespace, project_id), record
                    )
                ):
                    result.append(record)
        result.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return result

    def find_artifact_by_fingerprint(
        self,
        namespace: str,
        project_id: str,
        *,
        fingerprint: str,
        artifact_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the newest matching artifact inside one OAuth/project boundary."""

        for record in self.list_artifacts(namespace, project_id):
            if record.get("fingerprint") != fingerprint:
                continue
            if artifact_type is not None and record.get("artifact_type") != artifact_type:
                continue
            return record
        return None

    @staticmethod
    def _project_record_kind(kind: str) -> str:
        if kind not in PROJECT_RECORD_KINDS:
            raise ValueError(f"unsupported project record kind: {kind}")
        return kind

    def save_project_record(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
        record_id: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically save a small project-scoped runtime record.

        Handoffs, audit certificates, and telemetry intentionally live outside
        the downloadable artifact namespace.  This keeps their cache keys and
        timings private to the authenticated OAuth namespace and project.
        """

        clean_kind = self._project_record_kind(kind)
        clean_id = safe_id(record_id, "record_id")
        directory = self.project_dir(namespace, project_id)
        if not (directory / "hub.json").is_file():
            raise ValueError(f"unknown project_id: {project_id}")
        record = dict(value)
        record.setdefault("record_id", clean_id)
        record.setdefault("project_id", project_id)
        record.setdefault("created_at", utc_stamp())
        record["integrity_hmac"] = self._record_integrity_hmac(record)
        path = directory / clean_kind / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            self.atomic_write_json(path, record)
        return record

    def get_project_record(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
        record_id: str,
    ) -> dict[str, Any]:
        clean_kind = self._project_record_kind(kind)
        clean_id = safe_id(record_id, "record_id")
        path = self.project_dir(namespace, project_id) / clean_kind / f"{clean_id}.json"
        if not path.is_file():
            raise ValueError(f"unknown {clean_kind} record_id: {clean_id}")
        value = self.read_json(path)
        if not isinstance(value, dict) or value.get("project_id") != project_id:
            raise ValueError(f"invalid {clean_kind} record: {clean_id}")
        self._verify_record_integrity(value, record_id=clean_id)
        if (
            clean_kind in SIGNED_ONLY_PROJECT_RECORD_KINDS
            and not self.project_record_is_signed(value)
        ):
            raise ValueError(f"project_record_signature_required: {clean_id}")
        return value

    @contextlib.contextmanager
    def mutate_project_record(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
        record_id: str,
    ) -> Iterator[dict[str, Any]]:
        """Atomically mutate one signed project record.

        Diagnostic checkpoints are advanced by separate MCP calls. Keeping
        the read, sequence check, signature refresh, and write under the same
        project lock prevents concurrent windows from appending two different
        stages to the same run.
        """

        clean_kind = self._project_record_kind(kind)
        clean_id = safe_id(record_id, "record_id")
        path = self.project_dir(namespace, project_id) / clean_kind / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError(f"unknown {clean_kind} record_id: {clean_id}")
            value = self.read_json(path)
            if not isinstance(value, dict) or value.get("project_id") != project_id:
                raise ValueError(f"invalid {clean_kind} record: {clean_id}")
            self._verify_record_integrity(value, record_id=clean_id)
            if (
                clean_kind in SIGNED_ONLY_PROJECT_RECORD_KINDS
                and not self.project_record_is_signed(value)
            ):
                raise ValueError(f"project_record_signature_required: {clean_id}")
            yield value
            value["integrity_hmac"] = self._record_integrity_hmac(value)
            self.atomic_write_json(path, value)

    def list_project_records(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
    ) -> list[dict[str, Any]]:
        clean_kind = self._project_record_kind(kind)
        directory = self.project_dir(namespace, project_id) / clean_kind
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            with contextlib.suppress(Exception):
                value = self.read_json(path)
                if isinstance(value, dict) and value.get("project_id") == project_id:
                    self._verify_record_integrity(
                        value, record_id=str(value.get("record_id") or path.stem)
                    )
                    if (
                        clean_kind in SIGNED_ONLY_PROJECT_RECORD_KINDS
                        and not self.project_record_is_signed(value)
                    ):
                        raise ValueError(
                            f"project_record_signature_required: {path.stem}"
                        )
                    result.append(value)
        result.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return result

    def find_project_record(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
        field: str,
        value: Any,
    ) -> dict[str, Any] | None:
        for record in self.list_project_records(namespace, project_id, kind=kind):
            if record.get(field) == value:
                return record
        return None

    def handoff_is_published(
        self,
        namespace: str,
        project_id: str,
        handoff: dict[str, Any],
    ) -> bool:
        """Return whether a successor-bound handoff is publicly consumable."""

        state = str(handoff.get("publication_state") or "committed")
        transaction_id = str(handoff.get("successor_transaction_id") or "")
        if not transaction_id:
            return state == "committed"
        if state in {"aborted", "rejected"}:
            return False
        return self._successor_descriptor_committed(
            self.project_dir(namespace, project_id),
            base_artifact_id=str(
                handoff.get("successor_base_artifact_id") or ""
            ),
            transaction_id=transaction_id,
            successor_artifact_id=str(
                handoff.get("storyboard_artifact_id") or ""
            ),
        )

    def claim_handoff(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        expected_to_role: str,
        expected_types: set[str],
        lease_seconds: int = DEFAULT_HANDOFF_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Atomically claim one signed handoff with a fenced, bounded lease."""

        clean_id = safe_id(handoff_id, "handoff_id")
        try:
            lease_seconds = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid handoff lease_seconds") from exc
        if not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS:
            raise ValueError(
                f"handoff lease_seconds must be between 1 and {MAX_CONSUMPTION_LEASE_SECONDS}"
            )
        path = self.project_dir(namespace, project_id) / "handoffs" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError("handoff_required: signed handoff does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if (
                record.get("to_window_role") != expected_to_role
                or record.get("handoff_type") not in expected_types
            ):
                raise ValueError("handoff_binding_mismatch: type or destination role is invalid")
            bound_authority = record.get("claim_authority_work_order_id")
            if bound_authority not in {None, consumer_work_order_id}:
                raise ValueError(
                    "handoff_state_conflict: handoff is bound to another audit authority"
                )
            publication_state = str(record.get("publication_state") or "committed")
            if not self.handoff_is_published(
                namespace, project_id, record
            ):
                raise ValueError(
                    "handoff_state_conflict: handoff belongs to an uncommitted "
                    f"successor ({publication_state})"
                )
            status = record.get("status")
            owner = record.get("consumed_by_work_order_id") or record.get("claimed_by_work_order_id")
            if status == "superseded":
                raise ValueError(
                    "handoff_superseded: use successor "
                    f"{record.get('superseded_by_handoff_id') or 'unknown'}"
                )
            if status == "consumed":
                if owner == consumer_work_order_id:
                    result = dict(record)
                    result["idempotent_replay"] = True
                    return result
                raise ValueError("handoff_state_conflict: handoff was consumed by another audit authority")
            if status == "consuming":
                if owner != consumer_work_order_id:
                    # Once an audit authority has claimed this signed handoff,
                    # expiry may recover only that same authority.  It must
                    # never transfer the audit to a competing work order.
                    raise ValueError(
                        "handoff_state_conflict: handoff is bound to another audit authority"
                    )
                try:
                    lease_expires_epoch = float(
                        record.get("claim_lease_expires_epoch_seconds") or 0
                    )
                except (TypeError, ValueError):
                    lease_expires_epoch = 0
                if lease_expires_epoch <= 0:
                    try:
                        claimed_at = float(record.get("claim_epoch_seconds") or 0)
                    except (TypeError, ValueError):
                        claimed_at = 0
                    lease_expires_epoch = claimed_at + lease_seconds
                if lease_expires_epoch > time.time():
                    raise ValueError("handoff_state_conflict: handoff is being consumed")
                expired = {
                    "claim_token": record.get("claim_token"),
                    "claimed_by_work_order_id": owner,
                    "claim_generation": record.get("claim_generation"),
                    "claim_epoch_seconds": record.get("claim_epoch_seconds"),
                    "claim_lease_expires_epoch_seconds": record.get(
                        "claim_lease_expires_epoch_seconds"
                    ),
                    "state": "lease_expired",
                    "expired_at": utc_stamp(),
                }
                history = record.setdefault("claim_history", [])
                if not isinstance(history, list):
                    history = []
                    record["claim_history"] = history
                history.append(expired)
                del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            elif status != "prepared":
                raise ValueError(f"handoff_state_conflict: handoff status is {status!r}")
            now = time.time()
            try:
                generation = int(record.get("claim_generation") or 0) + 1
            except (TypeError, ValueError):
                generation = 1
            record["status"] = "consuming"
            record["claim_authority_work_order_id"] = consumer_work_order_id
            record["claimed_by_work_order_id"] = consumer_work_order_id
            record["claim_token"] = f"HCL-{uuid.uuid4().hex}"
            record["claim_generation"] = generation
            record["claim_epoch_seconds"] = now
            record["claim_lease_seconds"] = lease_seconds
            record["claim_lease_expires_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lease_seconds)
            )
            record["claim_lease_expires_epoch_seconds"] = now + lease_seconds
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def release_handoff_claim(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        claim_token: str,
        reason: str,
    ) -> dict[str, Any]:
        """Release an audit claim only for its exact authority and token."""

        clean_id = safe_id(handoff_id, "handoff_id")
        path = self.project_dir(namespace, project_id) / "handoffs" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if record.get("status") == "consumed":
                return record
            if (
                record.get("status") != "consuming"
                or record.get("claimed_by_work_order_id") != consumer_work_order_id
                or record.get("claim_token") != claim_token
            ):
                raise ValueError("handoff_state_conflict: handoff claim is not current")
            released = {
                "claim_token": record.get("claim_token"),
                "claimed_by_work_order_id": consumer_work_order_id,
                "claim_generation": record.get("claim_generation"),
                "claim_epoch_seconds": record.get("claim_epoch_seconds"),
                "claim_lease_expires_epoch_seconds": record.get(
                    "claim_lease_expires_epoch_seconds"
                ),
                "state": "released",
                "released_at": utc_stamp(),
                "release_reason": str(reason or "audit_failed")[:256],
            }
            history = record.setdefault("claim_history", [])
            if not isinstance(history, list):
                history = []
                record["claim_history"] = history
            history.append(released)
            del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            record["status"] = "prepared"
            for key in (
                "claimed_by_work_order_id",
                "claim_token",
                "claim_epoch_seconds",
                "claim_lease_seconds",
                "claim_lease_expires_at",
                "claim_lease_expires_epoch_seconds",
            ):
                record.pop(key, None)
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def finish_handoff(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        audit_certificate_id: str,
        claim_token: str | None = None,
        allow_reaudit: bool = False,
    ) -> dict[str, Any]:
        clean_id = safe_id(handoff_id, "handoff_id")
        path = self.project_dir(namespace, project_id) / "handoffs" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError("handoff_required: signed handoff does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if record.get("status") == "consumed":
                if (
                    record.get("consumed_by_work_order_id") == consumer_work_order_id
                    and record.get("audit_certificate_id") == audit_certificate_id
                ):
                    return record
                if (
                    allow_reaudit
                    and record.get("consumed_by_work_order_id")
                    == consumer_work_order_id
                ):
                    certificate_history = [
                        str(value)
                        for value in record.get("audit_certificate_ids", [])
                        if str(value)
                    ]
                    previous_certificate_id = str(
                        record.get("audit_certificate_id") or ""
                    )
                    if previous_certificate_id:
                        certificate_history.append(previous_certificate_id)
                    certificate_history.append(audit_certificate_id)
                    record["audit_certificate_ids"] = list(
                        dict.fromkeys(certificate_history)
                    )
                    record["audit_certificate_id"] = audit_certificate_id
                    record["reaudit_count"] = max(
                        0, len(record["audit_certificate_ids"]) - 1
                    )
                    record["reaudited_at"] = utc_stamp()
                    record["integrity_hmac"] = self._record_integrity_hmac(
                        record
                    )
                    self.atomic_write_json(path, record)
                    return record
                raise ValueError("handoff_state_conflict: handoff finalization differs")
            if (
                record.get("status") != "consuming"
                or record.get("claimed_by_work_order_id") != consumer_work_order_id
                or not claim_token
                or record.get("claim_token") != claim_token
            ):
                raise ValueError("handoff_state_conflict: handoff claim is not current")
            record["status"] = "consumed"
            record["consumed_by_work_order_id"] = consumer_work_order_id
            record["audit_certificate_id"] = audit_certificate_id
            record["consumed_at"] = utc_stamp()
            record.pop("claimed_by_work_order_id", None)
            record.pop("claim_token", None)
            record.pop("claim_epoch_seconds", None)
            record.pop("claim_lease_seconds", None)
            record.pop("claim_lease_expires_at", None)
            record.pop("claim_lease_expires_epoch_seconds", None)
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def claim_repair_package(
        self,
        namespace: str,
        project_id: str,
        repair_package_id: str,
        *,
        work_order_id: str,
    ) -> dict[str, Any]:
        clean_id = safe_id(repair_package_id, "repair_package_id")
        path = self.project_dir(namespace, project_id) / "repair_packages" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError("repair_package_required: immutable package does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if record.get("status") != "issued":
                raise ValueError("repair_package_unavailable: package is not issued")
            record["status"] = "consuming"
            record["consumed_by_work_order_id"] = work_order_id
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def finish_repair_package(
        self,
        namespace: str,
        project_id: str,
        repair_package_id: str,
        *,
        work_order_id: str,
        successor_artifact_id: str | None,
        successor_canonical_sha256: str | None,
        repair_to_audit_handoff_id: str | None = None,
    ) -> dict[str, Any]:
        clean_id = safe_id(repair_package_id, "repair_package_id")
        path = self.project_dir(namespace, project_id) / "repair_packages" / f"{clean_id}.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not path.is_file():
                raise ValueError("repair_package_required: immutable package does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if (
                record.get("status") != "consuming"
                or record.get("consumed_by_work_order_id") != work_order_id
            ):
                raise ValueError("repair_package_claim_conflict")
            if successor_artifact_id and successor_canonical_sha256:
                if not repair_to_audit_handoff_id:
                    raise ValueError(
                        "repair_package_binding_incomplete: repair_to_audit handoff is required"
                    )
                record["status"] = "consumed"
                record["successor_artifact_id"] = successor_artifact_id
                record["successor_canonical_sha256"] = successor_canonical_sha256
                record["repair_to_audit_handoff_id"] = repair_to_audit_handoff_id
            else:
                record["status"] = "issued"
                record.pop("consumed_by_work_order_id", None)
                record.pop("successor_artifact_id", None)
                record.pop("successor_canonical_sha256", None)
                record.pop("repair_to_audit_handoff_id", None)
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def update_artifact_metadata(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        safe_id(artifact_id, "artifact_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            record.setdefault("metadata", {}).update(updates)
            self.atomic_write_json(meta_path, record)
            return record

    def claim_storyboard_patch(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        expected_canonical_hash: str,
        claim_id: str,
    ) -> dict[str, Any]:
        """Atomically reserve one canonical base for a patch attempt.

        The compare-and-set prevents two repair windows from successfully
        applying the same stale base hash at the same time.  A failed final
        validation can release the claim; a successful patch records its
        successor permanently.
        """

        return self.claim_storyboard_successor(
            namespace,
            project_id,
            artifact_id,
            expected_canonical_hash=expected_canonical_hash,
            expected_raw_fingerprint=None,
            claim_id=claim_id,
            successor_kind="patch",
            scope_hash=sha256_bytes(b"legacy-patch-scope"),
        )

    def finish_storyboard_patch(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        claim_id: str,
        successor_artifact_id: str | None,
        successor_canonical_hash: str | None,
    ) -> dict[str, Any]:
        """Release a failed patch claim or bind a successful successor."""

        record = self.finish_storyboard_successor(
            namespace,
            project_id,
            artifact_id,
            claim_id=claim_id,
            successor_artifact_id=successor_artifact_id,
            successor_canonical_hash=successor_canonical_hash,
        )
        if successor_artifact_id is not None:
            directory = self.project_dir(namespace, project_id)
            meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
            with self.lock(f"{namespace}-{project_id}"):
                record = self.read_json(meta_path)
                metadata = record.setdefault("metadata", {})
                existing = metadata.get("patch_successor_artifact_id")
                if existing not in {None, successor_artifact_id}:
                    raise ValueError(
                        "canonical_hash_conflict: patch successor differs from committed transaction"
                    )
                metadata["patch_successor_artifact_id"] = successor_artifact_id
                metadata["patch_successor_canonical_hash"] = successor_canonical_hash
                metadata.setdefault("patch_completed_at", utc_stamp())
                self.atomic_write_json(meta_path, record)
        return record

    @staticmethod
    def _successor_transaction_id(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("transaction_id") or value.get("claim_id") or "")
        if value in (None, "", False):
            return ""
        return str(value)

    @staticmethod
    def _successor_lease_expiry(value: dict[str, Any], fallback_epoch: float) -> float:
        for key in ("lease_expires_epoch_seconds", "lease_expires_epoch"):
            try:
                expiry = float(value.get(key))
            except (TypeError, ValueError):
                continue
            if expiry > 0:
                return expiry
        try:
            claimed_epoch = float(
                value.get("claim_epoch_seconds")
                or value.get("prepared_epoch_seconds")
            )
        except (TypeError, ValueError):
            claimed_epoch = 0.0
        if not claimed_epoch:
            claimed_at = str(value.get("claimed_at") or value.get("prepared_at") or "")
            if claimed_at:
                with contextlib.suppress(ValueError, OverflowError):
                    claimed_epoch = float(
                        calendar.timegm(time.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ"))
                    )
        if not claimed_epoch:
            claimed_epoch = fallback_epoch
        try:
            lease_seconds = int(value.get("lease_seconds") or DEFAULT_SUCCESSOR_LEASE_SECONDS)
        except (TypeError, ValueError):
            lease_seconds = DEFAULT_SUCCESSOR_LEASE_SECONDS
        return claimed_epoch + max(1, min(lease_seconds, MAX_SUCCESSOR_LEASE_SECONDS))

    @classmethod
    def _active_successor_transaction(
        cls,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates: tuple[tuple[str, Any], ...] = (
            ("pending", metadata.get("pending_successor")),
            ("generic", metadata.get("active_successor_claim")),
            ("patch", metadata.get("active_patch_claim")),
            ("delta", metadata.get("active_delta_claim")),
            ("repair", metadata.get("active_repair_claim")),
            ("transaction", metadata.get("successor_transaction")),
        )
        for inferred_kind, raw in candidates:
            if not raw:
                continue
            if isinstance(raw, dict):
                value = dict(raw)
                if inferred_kind == "transaction" and value.get("state") != "prepared":
                    continue
            else:
                value = {"transaction_id": str(raw)}
            transaction_id = cls._successor_transaction_id(value)
            if not transaction_id:
                continue
            value.setdefault("transaction_id", transaction_id)
            value.setdefault("claim_id", transaction_id)
            if inferred_kind in SUCCESSOR_KINDS:
                value.setdefault("successor_kind", inferred_kind)
            value.setdefault("state", "prepared")
            return value
        return None

    @staticmethod
    def _committed_successor_id(metadata: dict[str, Any]) -> str | None:
        values = {
            str(value)
            for value in (
                metadata.get("successor_artifact_id"),
                metadata.get("patch_successor_artifact_id"),
                metadata.get("delta_successor_artifact_id"),
                metadata.get("repair_successor_artifact_id"),
            )
            if value
        }
        if len(values) > 1:
            raise ValueError(
                "successor_metadata_conflict: committed successor aliases disagree"
            )
        return next(iter(values), None)

    @staticmethod
    def _successor_request_matches(
        value: dict[str, Any],
        *,
        transaction_id: str,
        successor_kind: str,
        expected_canonical_hash: str,
        expected_raw_fingerprint: str | None,
        scope_hash: str,
    ) -> bool:
        if ProjectStore._successor_transaction_id(value) != transaction_id:
            return False
        expected = {
            "successor_kind": successor_kind,
            "expected_canonical_hash": expected_canonical_hash,
            "expected_raw_fingerprint": expected_raw_fingerprint,
            "scope_hash": scope_hash,
        }
        for key, requested in expected.items():
            recorded = value.get(key)
            # Pre-0.4.0 active claims may not have every new transaction field.
            if recorded not in (None, "") and recorded != requested:
                return False
        return True

    @staticmethod
    def _archive_successor_transaction(
        metadata: dict[str, Any], value: dict[str, Any]
    ) -> None:
        history = metadata.setdefault("successor_transaction_history", [])
        if not isinstance(history, list):
            history = []
            metadata["successor_transaction_history"] = history
        transaction_id = ProjectStore._successor_transaction_id(value)
        state = str(value.get("state") or "")
        if not any(
            ProjectStore._successor_transaction_id(item) == transaction_id
            and str(item.get("state") or "") == state
            for item in history
            if isinstance(item, dict)
        ):
            history.append(dict(value))
        if len(history) > MAX_SUCCESSOR_TRANSACTION_HISTORY:
            del history[:-MAX_SUCCESSOR_TRANSACTION_HISTORY]

    @classmethod
    def _clear_active_successor_transaction(
        cls, metadata: dict[str, Any], transaction_id: str
    ) -> None:
        pending = metadata.get("pending_successor")
        if cls._successor_transaction_id(pending) == transaction_id:
            metadata["pending_successor"] = None
        for key in (
            "active_successor_claim",
            "active_patch_claim",
            "active_delta_claim",
            "active_repair_claim",
        ):
            if cls._successor_transaction_id(metadata.get(key)) == transaction_id:
                metadata[key] = None

    def prepare_storyboard_successor(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        expected_canonical_hash: str,
        expected_raw_fingerprint: str | None,
        transaction_id: str,
        successor_kind: str,
        scope_hash: str,
        lease_seconds: int = DEFAULT_SUCCESSOR_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Prepare an exclusive, leased successor transaction.

        Preparation creates only a pending transaction record.  The base's
        public successor pointers remain empty until ``commit`` atomically
        binds a validated artifact.  The same transaction may retry safely;
        an expired transaction may renew itself or be recovered by a new one.
        """

        safe_id(artifact_id, "artifact_id")
        safe_id(transaction_id, "transaction_id")
        if successor_kind not in SUCCESSOR_KINDS:
            raise ValueError("invalid successor_kind")
        if not isinstance(scope_hash, str) or not scope_hash or len(scope_hash) > 512:
            raise ValueError("invalid successor scope_hash")
        try:
            lease_seconds = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid successor lease_seconds") from exc
        if not 1 <= lease_seconds <= MAX_SUCCESSOR_LEASE_SECONDS:
            raise ValueError(
                f"successor lease_seconds must be between 1 and {MAX_SUCCESSOR_LEASE_SECONDS}"
            )
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            metadata = record.setdefault("metadata", {})
            if metadata.get("canonical_sha256") != expected_canonical_hash:
                raise ValueError("canonical_hash_conflict: base canonical hash changed")
            if expected_raw_fingerprint and record.get("fingerprint") != expected_raw_fingerprint:
                raise ValueError("artifact_raw_fingerprint_mismatch: base artifact bytes changed")

            committed_id = self._committed_successor_id(metadata)
            latest = metadata.get("successor_transaction")
            if committed_id:
                if (
                    isinstance(latest, dict)
                    and latest.get("state") == "committed"
                    and self._successor_request_matches(
                        latest,
                        transaction_id=transaction_id,
                        successor_kind=successor_kind,
                        expected_canonical_hash=expected_canonical_hash,
                        expected_raw_fingerprint=expected_raw_fingerprint,
                        scope_hash=scope_hash,
                    )
                ):
                    return record
                raise ValueError(
                    "canonical_hash_conflict: base canonical hash was already consumed"
                )

            now = time.time()
            fallback_epoch = meta_path.stat().st_mtime
            active = self._active_successor_transaction(metadata)
            if active is not None:
                active_id = self._successor_transaction_id(active)
                expiry = self._successor_lease_expiry(active, fallback_epoch)
                if active_id == transaction_id:
                    if not self._successor_request_matches(
                        active,
                        transaction_id=transaction_id,
                        successor_kind=successor_kind,
                        expected_canonical_hash=expected_canonical_hash,
                        expected_raw_fingerprint=expected_raw_fingerprint,
                        scope_hash=scope_hash,
                    ):
                        raise ValueError(
                            "successor_transaction_conflict: transaction id was reused with different parameters"
                        )
                    if expiry > now:
                        return record
                    renewed_from = dict(active)
                    renewed_from["state"] = "lease_expired"
                    renewed_from["expired_at"] = utc_stamp()
                    self._archive_successor_transaction(metadata, renewed_from)
                    self._clear_active_successor_transaction(metadata, active_id)
                else:
                    if expiry > now:
                        raise ValueError(
                            "canonical_hash_conflict: base canonical has an active successor lease"
                        )
                    recovered = dict(active)
                    recovered["state"] = "recovered"
                    recovered["recovered_at"] = utc_stamp()
                    recovered["recovered_by_transaction_id"] = transaction_id
                    recovered["recovery_reason"] = "lease_expired"
                    self._archive_successor_transaction(metadata, recovered)
                    self._clear_active_successor_transaction(metadata, active_id)

            previous = metadata.get("successor_transaction")
            if isinstance(previous, dict) and previous.get("state") in {
                "aborted",
                "recovered",
                "lease_expired",
            }:
                self._archive_successor_transaction(metadata, previous)

            prepared_at = utc_stamp()
            lease_expires_epoch = now + lease_seconds
            try:
                generation = int(metadata.get("successor_transaction_generation") or 0) + 1
            except (TypeError, ValueError):
                generation = 1
            transaction = {
                "record_version": 1,
                "transaction_id": transaction_id,
                "claim_id": transaction_id,
                "generation": generation,
                "state": "prepared",
                "base_artifact_id": artifact_id,
                "successor_kind": successor_kind,
                "expected_canonical_hash": expected_canonical_hash,
                "expected_raw_fingerprint": expected_raw_fingerprint,
                "scope_hash": scope_hash,
                "prepared_at": prepared_at,
                "prepared_epoch_seconds": now,
                "lease_seconds": lease_seconds,
                "lease_expires_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(lease_expires_epoch)
                ),
                "lease_expires_epoch_seconds": lease_expires_epoch,
            }
            metadata["pending_successor"] = dict(transaction)
            metadata["successor_transaction"] = dict(transaction)
            metadata["successor_transaction_generation"] = generation
            compatibility_claim = {
                "claim_id": transaction_id,
                "transaction_id": transaction_id,
                "generation": generation,
                "successor_kind": successor_kind,
                "expected_canonical_hash": expected_canonical_hash,
                "expected_raw_fingerprint": expected_raw_fingerprint,
                "scope_hash": scope_hash,
                "claimed_at": prepared_at,
                "claim_epoch_seconds": now,
                "lease_seconds": lease_seconds,
                "lease_expires_at": transaction["lease_expires_at"],
                "lease_expires_epoch_seconds": lease_expires_epoch,
            }
            metadata["active_successor_claim"] = dict(compatibility_claim)
            metadata[f"active_{successor_kind}_claim"] = dict(compatibility_claim)
            self.atomic_write_json(meta_path, record)
            return record

    def commit_storyboard_successor(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        transaction_id: str,
        successor_artifact_id: str,
        successor_canonical_hash: str,
    ) -> dict[str, Any]:
        """Commit a prepared successor exactly once and expose its pointer."""

        safe_id(artifact_id, "artifact_id")
        safe_id(transaction_id, "transaction_id")
        safe_id(successor_artifact_id, "successor_artifact_id")
        if successor_artifact_id == artifact_id:
            raise ValueError("successor artifact cannot be its own base")
        if not successor_canonical_hash:
            raise ValueError("successor_canonical_hash is required")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        successor_meta_path = (
            directory / "artifacts" / f"{successor_artifact_id}.meta.json"
        )
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            metadata = record.setdefault("metadata", {})
            committed_id = self._committed_successor_id(metadata)
            latest = metadata.get("successor_transaction")
            if committed_id:
                if (
                    committed_id == successor_artifact_id
                    and isinstance(latest, dict)
                    and latest.get("state") == "committed"
                    and self._successor_transaction_id(latest) == transaction_id
                    and latest.get("successor_canonical_hash")
                    == successor_canonical_hash
                ):
                    return record
                raise ValueError(
                    "canonical_hash_conflict: base canonical already has a different successor"
                )

            active = self._active_successor_transaction(metadata)
            if active is None or self._successor_transaction_id(active) != transaction_id:
                raise ValueError(
                    "canonical_hash_conflict: successor transaction is no longer current"
                )
            if self._successor_lease_expiry(active, meta_path.stat().st_mtime) <= time.time():
                raise ValueError(
                    "successor_lease_expired: renew the transaction before commit"
                )
            expected_base_hash = active.get("expected_canonical_hash")
            if (
                expected_base_hash
                and metadata.get("canonical_sha256") != expected_base_hash
            ):
                raise ValueError(
                    "canonical_hash_conflict: base canonical changed during successor transaction"
                )
            expected_raw = active.get("expected_raw_fingerprint")
            if expected_raw and record.get("fingerprint") != expected_raw:
                raise ValueError(
                    "artifact_raw_fingerprint_mismatch: base bytes changed during successor transaction"
                )
            if not successor_meta_path.is_file():
                raise ValueError(
                    f"unknown successor_artifact_id: {successor_artifact_id}"
                )
            successor_record = self.read_json(successor_meta_path)
            if successor_record.get("project_id") != project_id:
                raise ValueError("successor artifact belongs to a different project")
            recorded_hash = successor_record.get("metadata", {}).get("canonical_sha256")
            if recorded_hash != successor_canonical_hash:
                raise ValueError(
                    "successor_canonical_hash_mismatch: artifact metadata differs"
                )
            linked_base = successor_record.get("source_artifact_id")
            if linked_base != artifact_id:
                raise ValueError("successor artifact is linked to a different base")
            successor_metadata = successor_record.get("metadata", {})
            publication_state = self.artifact_publication_state(successor_record)
            descriptor_transaction = successor_metadata.get(
                "successor_transaction_id"
            )
            if descriptor_transaction is not None:
                if publication_state != "pending":
                    raise ValueError(
                        "successor_publication_state_mismatch: candidate is not pending"
                    )
                if str(descriptor_transaction) != transaction_id:
                    raise ValueError(
                        "successor_transaction_conflict: candidate belongs to another transaction"
                    )
                if successor_metadata.get("successor_base_artifact_id") != artifact_id:
                    raise ValueError(
                        "successor_transaction_conflict: candidate base binding differs"
                    )
                if successor_metadata.get("successor_kind") != active.get(
                    "successor_kind"
                ):
                    raise ValueError(
                        "successor_transaction_conflict: candidate kind binding differs"
                    )
                if successor_metadata.get("successor_scope_hash") != active.get(
                    "scope_hash"
                ):
                    raise ValueError(
                        "successor_transaction_conflict: candidate scope binding differs"
                    )

            committed_at = utc_stamp()
            transaction = dict(active)
            transaction.update(
                {
                    "record_version": 1,
                    "transaction_id": transaction_id,
                    "claim_id": transaction_id,
                    "state": "committed",
                    "base_artifact_id": artifact_id,
                    "successor_artifact_id": successor_artifact_id,
                    "successor_canonical_hash": successor_canonical_hash,
                    "committed_at": committed_at,
                }
            )
            successor_kind = str(transaction.get("successor_kind") or "")
            if successor_kind not in SUCCESSOR_KINDS:
                raise ValueError("invalid successor_kind in prepared transaction")
            self._clear_active_successor_transaction(metadata, transaction_id)
            metadata["successor_transaction"] = transaction
            metadata["successor_artifact_id"] = successor_artifact_id
            metadata["successor_canonical_hash"] = successor_canonical_hash
            metadata["successor_kind"] = successor_kind
            metadata["successor_scope_hash"] = transaction.get("scope_hash")
            metadata["successor_completed_at"] = committed_at
            metadata[f"{successor_kind}_successor_artifact_id"] = successor_artifact_id
            metadata[f"{successor_kind}_successor_canonical_hash"] = successor_canonical_hash
            metadata.setdefault(f"{successor_kind}_completed_at", committed_at)
            self.atomic_write_json(meta_path, record)
            return record

    def abort_storyboard_successor(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        transaction_id: str,
        reason: str = "caller_aborted",
    ) -> dict[str, Any]:
        """Abort a prepared transaction without publishing a successor."""

        safe_id(artifact_id, "artifact_id")
        safe_id(transaction_id, "transaction_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            metadata = record.setdefault("metadata", {})
            latest = metadata.get("successor_transaction")
            if (
                isinstance(latest, dict)
                and self._successor_transaction_id(latest) == transaction_id
            ):
                if latest.get("state") in {"aborted", "recovered"}:
                    return record
                if latest.get("state") == "committed":
                    raise ValueError(
                        "successor_transaction_committed: a committed successor cannot be aborted"
                    )
            active = self._active_successor_transaction(metadata)
            if active is None or self._successor_transaction_id(active) != transaction_id:
                raise ValueError(
                    "canonical_hash_conflict: successor transaction is no longer current"
                )
            aborted = dict(active)
            aborted.update(
                {
                    "record_version": 1,
                    "transaction_id": transaction_id,
                    "claim_id": transaction_id,
                    "state": "aborted",
                    "aborted_at": utc_stamp(),
                    "abort_reason": str(reason or "caller_aborted")[:512],
                }
            )
            self._clear_active_successor_transaction(metadata, transaction_id)
            metadata["successor_transaction"] = aborted
            self.atomic_write_json(meta_path, record)
            return record

    def claim_storyboard_successor(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        expected_canonical_hash: str,
        expected_raw_fingerprint: str | None,
        claim_id: str,
        successor_kind: str,
        scope_hash: str,
        lease_seconds: int = DEFAULT_SUCCESSOR_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Backward-compatible alias for ``prepare_storyboard_successor``."""

        return self.prepare_storyboard_successor(
            namespace,
            project_id,
            artifact_id,
            expected_canonical_hash=expected_canonical_hash,
            expected_raw_fingerprint=expected_raw_fingerprint,
            transaction_id=claim_id,
            successor_kind=successor_kind,
            scope_hash=scope_hash,
            lease_seconds=lease_seconds,
        )

    def finish_storyboard_successor(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        *,
        claim_id: str,
        successor_artifact_id: str | None,
        successor_canonical_hash: str | None,
    ) -> dict[str, Any]:
        """Backward-compatible commit/abort wrapper for legacy callers."""

        if successor_artifact_id is None:
            return self.abort_storyboard_successor(
                namespace,
                project_id,
                artifact_id,
                transaction_id=claim_id,
                reason="legacy_finish_without_successor",
            )
        if successor_canonical_hash is None:
            raise ValueError("successor_canonical_hash is required")
        return self.commit_storyboard_successor(
            namespace,
            project_id,
            artifact_id,
            transaction_id=claim_id,
            successor_artifact_id=successor_artifact_id,
            successor_canonical_hash=successor_canonical_hash,
        )

    def signed_download_url(
        self,
        namespace: str,
        project_id: str,
        artifact_id: str,
        file_name: str,
        *,
        lifetime_seconds: int = 3600,
    ) -> str:
        directory = self.project_dir(namespace, project_id)
        clean_artifact_id = safe_id(artifact_id, "artifact_id")
        record, payload_path = self._load_artifact_record_path(
            directory,
            project_id=project_id,
            artifact_id=clean_artifact_id,
        )
        verified, verified_size = self._open_verified_artifact_payload(
            record, payload_path
        )
        verified.close()

        issued_at = int(time.time())
        expiry = issued_at + max(60, min(int(lifetime_seconds), 86400))
        payload = json.dumps(
            {
                "sig_v": 2,
                "iat": issued_at,
                "namespace_key": self.namespace_key(namespace),
                "project_id": project_id,
                "artifact_id": clean_artifact_id,
                "fingerprint": record.get("fingerprint"),
                "size_bytes": verified_size,
                "file_name": record.get("file_name"),
                "exp": expiry,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        payload_token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature_token = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        token = f"{payload_token}.{signature_token}"
        return (
            f"{self.public_base_url}/files/{token}/"
            f"{slug_filename(str(record.get('file_name') or file_name))}"
        )

    def _load_artifact_record_path(
        self,
        directory: Path,
        *,
        project_id: str,
        artifact_id: str,
    ) -> tuple[dict[str, Any], Path]:
        """Load a complete artifact index and normalize failures to fail closed."""

        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        try:
            record = self.read_json(meta_path)
            if not isinstance(record, dict):
                raise TypeError("artifact metadata is not an object")
            required_strings = ("artifact_id", "project_id", "file_name", "relative_path", "fingerprint")
            if any(
                not isinstance(record.get(field), str) or not record.get(field)
                for field in required_strings
            ):
                raise ValueError("artifact metadata is incomplete")
            if (
                record["artifact_id"] != artifact_id
                or record["project_id"] != project_id
            ):
                raise ValueError("artifact metadata identity mismatch")
            if "metadata" in record and not isinstance(record["metadata"], dict):
                raise ValueError("artifact publication metadata is invalid")
            size = record.get("size_bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("artifact metadata size is invalid")
            payload_path = (directory / record["relative_path"]).resolve()
            if (
                directory.resolve() not in payload_path.parents
                or not payload_path.is_file()
            ):
                raise ValueError("artifact payload is unavailable")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError("artifact metadata or payload is unavailable") from exc
        return record, payload_path

    def _resolve_download_record_path(
        self, token: str
    ) -> tuple[dict[str, Any], Path]:
        try:
            payload_token, signature_token = token.split(".", 1)
            payload = base64.urlsafe_b64decode((payload_token + "=" * (-len(payload_token) % 4)).encode("ascii"))
            signature = base64.urlsafe_b64decode(
                (signature_token + "=" * (-len(signature_token) % 4)).encode("ascii")
            )
            value = json.loads(payload.decode("utf-8"))
            untrusted_signature_version = value.get("sig_v")
            is_v2 = (
                isinstance(untrusted_signature_version, int)
                and not isinstance(untrusted_signature_version, bool)
                and untrusted_signature_version == 2
            )
            if is_v2:
                verification_secrets = (self._secret,)
            elif untrusted_signature_version is None:
                verification_secrets = self._download_verification_secrets
            else:
                verification_secrets = (self._secret,)
            signature_valid = any(
                hmac.compare_digest(
                    signature,
                    hmac.new(secret, payload, hashlib.sha256).digest(),
                )
                for secret in verification_secrets
            )
            if not signature_valid:
                raise ValueError("invalid signature")
            now = int(time.time())
            expiry_value = value.get("exp")
            if not isinstance(expiry_value, int) or isinstance(expiry_value, bool):
                raise ValueError("invalid expiry")
            expiry = expiry_value
            if expiry < now:
                raise ValueError("expired")
            signature_version = value.get("sig_v")
            if signature_version is None:
                # Every pre-0.5.5 token was capped to a 24-hour lifetime. Keep
                # only the persisted one-time forward-compatibility window;
                # restarts never extend it and far-future tokens are rejected.
                if (
                    now > self._legacy_download_token_accept_until
                    or expiry > self._legacy_download_token_accept_until
                ):
                    raise ValueError("legacy token compatibility window expired")
            elif (
                not isinstance(signature_version, int)
                or isinstance(signature_version, bool)
                or signature_version != 2
            ):
                raise ValueError("unsupported signature version")
            else:
                issued_at = value.get("iat")
                if (
                    not isinstance(issued_at, int)
                    or isinstance(issued_at, bool)
                    or issued_at > now + 300
                    or expiry < issued_at
                    or expiry - issued_at > 86_400
                ):
                    raise ValueError("invalid signed token lifetime")
            namespace_key = safe_id(str(value["namespace_key"]), "namespace_key")
            project_id = safe_id(str(value["project_id"]), "project_id")
            artifact_id = safe_id(str(value["artifact_id"]), "artifact_id")
        except Exception as exc:
            raise ValueError("invalid or expired download token") from exc
        directory = self.projects_dir / namespace_key / project_id
        record, payload_path = self._load_artifact_record_path(
            directory,
            project_id=project_id,
            artifact_id=artifact_id,
        )
        self._assert_artifact_published(directory, record)
        if value.get("sig_v") == 2:
            token_size = value.get("size_bytes")
            if (
                not isinstance(token_size, int)
                or isinstance(token_size, bool)
                or value.get("fingerprint") != record.get("fingerprint")
                or token_size != record.get("size_bytes")
                or value.get("file_name") != record.get("file_name")
            ):
                raise ValueError("download_token_artifact_binding_mismatch")
        return record, payload_path

    def _open_verified_artifact_payload(
        self, record: dict[str, Any], payload_path: Path
    ) -> tuple[BinaryIO, int]:
        """Copy once into a bounded spool and bind it to immutable metadata.

        A signed URL proves who issued the token, not that the payload on disk
        is still the artifact that was signed. Copy from one opened descriptor,
        verify size and raw fingerprint, then stream that exact snapshot. Large
        files spill to disk instead of occupying one response-sized byte array.
        """

        expected_size = record.get("size_bytes")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise ValueError("artifact_size_metadata_invalid")
        if expected_size > self.max_download_bytes:
            raise ValueError("artifact_payload_exceeds_download_limit")
        expected = record.get("fingerprint")
        if not isinstance(expected, str):
            raise ValueError("artifact_fingerprint_metadata_invalid")

        spool = tempfile.SpooledTemporaryFile(
            max_size=min(DOWNLOAD_SPOOL_MEMORY_BYTES, self.max_download_bytes),
            mode="w+b",
        )
        digest = hashlib.sha256()
        copied = 0
        try:
            if payload_path.stat().st_size != expected_size:
                raise ValueError("artifact_raw_size_mismatch")
            with payload_path.open("rb") as source:
                while True:
                    chunk = source.read(DOWNLOAD_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > self.max_download_bytes:
                        raise ValueError("artifact_payload_exceeds_download_limit")
                    digest.update(chunk)
                    spool.write(chunk)
        except ValueError:
            spool.close()
            raise
        except OSError as exc:
            spool.close()
            raise ValueError("artifact payload is unavailable") from exc

        if copied != expected_size:
            spool.close()
            raise ValueError("artifact_raw_size_mismatch")
        if not hmac.compare_digest(digest.hexdigest(), expected):
            spool.close()
            raise ValueError(
                "artifact_raw_fingerprint_mismatch: payload bytes changed for "
                f"{record.get('artifact_id') or 'artifact'}"
            )
        spool.seek(0)
        return spool, copied

    def resolve_download_token(self, token: str) -> tuple[dict[str, Any], Path]:
        record, payload_path = self._resolve_download_record_path(token)
        stream, _size = self._open_verified_artifact_payload(record, payload_path)
        stream.close()
        return record, payload_path

    def open_verified_download(
        self, token: str
    ) -> tuple[dict[str, Any], BinaryIO, int]:
        record, payload_path = self._resolve_download_record_path(token)
        stream, size = self._open_verified_artifact_payload(record, payload_path)
        return record, stream, size
