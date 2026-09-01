from __future__ import annotations

import base64
import calendar
import copy
import contextlib
import hashlib
import hmac
import json
import math
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

from .canonical import canonical_json_bytes


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_RECORD_KINDS = {
    "handoffs",
    "handoff_recoveries",
    "preflight_evidence_reissues",
    "certificates",
    "telemetry",
    "diagnostics",
    "repair_packages",
    "draft_preflights",
    "migrations",
}
SIGNED_ONLY_PROJECT_RECORD_KINDS = PROJECT_RECORD_KINDS - {
    "handoffs",
    "certificates",
}
PROJECT_RECORD_PRIMARY_ID_KEYS = {
    "handoffs": "handoff_id",
    "handoff_recoveries": "recovery_id",
    "preflight_evidence_reissues": "preflight_evidence_id",
    "certificates": "certificate_id",
    "telemetry": "trace_id",
    "diagnostics": "diagnostic_run_id",
    "repair_packages": "repair_package_id",
    "draft_preflights": "draft_preflight_id",
    "migrations": "migration_id",
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
REAUDIT_IMMUTABLE_IDENTITY_KEYS = (
    "work_order_id",
    "audit_cycle_id",
    "logical_artifact_id",
    "handoff_type",
    "from_window_role",
    "to_window_role",
    "scope",
    "fingerprints",
    "objective_evidence",
    "producer_claims",
    "cache_manifest",
    "publication_state",
    "storyboard_artifact_id",
    "canonical_artifact_id",
    "canonical_sha256",
    "source_fingerprint",
    "boundary_hash",
    "ruleset_version",
    "validator_version",
    "audit_class",
    "certificate_id",
    "cache_key",
    "cache_components",
    "production_preflight_status",
    "production_preflight_issue_count",
    "preflight_evidence_source",
    "preflight_evidence_reissue_id",
    "source_preflight_certificate_id",
    "supersedes_handoff_id",
    "recovery_id",
    "recovery_gap_id",
    "recovery_work_order_id",
    "recovery_trace_id",
    "recovery_plan_sha256",
    "recovery_plan_item",
    "recovery_cache_status",
    "workbook_projection_revalidation",
    "history_preserved",
    "checked_shot_count",
    "facts_reusable",
    "conclusion_reusable_for_independent_audit",
    "from_window_binding_id",
    "from_actor_binding_id",
    "from_session_fingerprint",
    "successor_transaction_id",
    "successor_base_artifact_id",
    "successor_kind",
    "successor_scope_hash",
    "immutable_repair_package_id",
    "return_to_audit_work_order_id",
    "return_to_audit_window_binding_id",
    "return_to_audit_actor_binding_id",
    "return_to_audit_session_fingerprint",
)
RECOVERY_HANDOFF_AUDIT_MUTABLE_KEYS = frozenset(
    {
        "status",
        "claim_authority_work_order_id",
        "claimed_by_work_order_id",
        "claim_token",
        "claim_generation",
        "claim_epoch_seconds",
        "claim_lease_seconds",
        "claim_lease_expires_at",
        "claim_lease_expires_epoch_seconds",
        "claim_history",
        "consumed_by_work_order_id",
        "consumed_at",
        "consumption_claim",
        "audit_certificate_id",
        "audit_certificate_ids",
        "latest_audit_certificate_id",
        "independent_audit_status",
        "reaudit_count",
        "reaudited_at",
    }
)


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

    def _assert_no_project_rollback_fence_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        permitted_migration_id: str | None = None,
    ) -> None:
        """Reject ordinary writes while any signed migration is nonterminal."""

        for migration_path in sorted(
            (directory / "migrations").glob("*.json")
        ):
            record = self.read_json(migration_path)
            record_id = migration_path.stem
            if not isinstance(record, dict):
                raise ValueError("migration_record_authority_conflict")
            self._verify_record_integrity(record, record_id=record_id)
            self._verify_project_record_primary_id(
                "migrations",
                record,
                record_id=record_id,
            )
            if (
                record.get("project_id") != project_id
                or not self.project_record_is_signed(record)
            ):
                raise ValueError("migration_record_authority_conflict")
            status = str(record.get("status") or "")
            if (
                status in {"prepared", "replaying", "rolling_back"}
                and record_id != permitted_migration_id
            ):
                if status == "rolling_back":
                    raise ValueError(
                        "migration_rollback_in_progress: finish rollback before project writes"
                    )
                raise ValueError(
                    "migration_commit_in_progress: finish migration before project writes"
                )

    @contextlib.contextmanager
    def project_write_lock(
        self,
        namespace: str,
        project_id: str,
        *,
        permit_migration_id: str | None = None,
    ) -> Iterator[None]:
        """Serialize a project write and enforce its migration WAL fence."""

        directory = self.project_dir(namespace, project_id)
        with self.lock(f"{namespace}-{project_id}"):
            self._assert_no_project_rollback_fence_unlocked(
                directory,
                project_id=project_id,
                permitted_migration_id=permit_migration_id,
            )
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
            self.atomic_write_json(path, hub)

    def load_ledger(self, namespace: str, project_id: str) -> dict[str, Any]:
        path = self.project_dir(namespace, project_id) / "ledger.json"
        if not path.is_file():
            raise ValueError(f"unknown project_id: {project_id}")
        return self.read_json(path)

    def save_ledger(self, namespace: str, project_id: str, ledger: dict[str, Any]) -> None:
        path = self.project_dir(namespace, project_id) / "ledger.json"
        with self.project_write_lock(namespace, project_id):
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

    def ledger_replay_checkpoint_is_authorized(
        self,
        namespace: str,
        project_id: str,
        checkpoint: dict[str, Any],
    ) -> bool:
        """Verify a legacy-ledger replay checkpoint against its signed CAS journal.

        The checkpoint stored in ``ledger.json`` is only an immutable-prefix
        descriptor.  Authority comes from the separately signed migration
        journal that committed the exact before/after ledger pair.  Keeping
        this verification in storage prevents a caller-provided boolean (or
        the checkpoint's own marker field) from authorizing legacy history.
        """

        if not isinstance(checkpoint, dict):
            return False
        migration_id = checkpoint.get("migration_id")
        if not isinstance(migration_id, str):
            return False
        try:
            clean_id = safe_id(migration_id, "migration_id")
            record = self.get_project_record(
                namespace,
                project_id,
                kind="migrations",
                record_id=clean_id,
            )
        except (OSError, TypeError, ValueError, KeyError):
            return False

        ledger_before = record.get("ledger_before")
        ledger_after = record.get("ledger_after")
        old_ledger_sha = checkpoint.get("old_ledger_canonical_sha256")
        if not isinstance(ledger_before, dict) or not isinstance(ledger_after, dict):
            return False
        return bool(
            record.get("status") == "committed"
            and record.get("migration_id") == clean_id
            and record.get("record_id") == clean_id
            and record.get("project_id") == project_id
            and record.get("preserves_artifacts") is True
            and isinstance(old_ledger_sha, str)
            and record.get("ledger_before_sha256") == old_ledger_sha
            and self.project_state_sha256(ledger_before) == old_ledger_sha
            and record.get("ledger_after_sha256")
            == self.project_state_sha256(ledger_after)
            and ledger_after.get("project_id") == project_id
            and ledger_after.get("replay_checkpoint") == checkpoint
        )

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
        runtime_test_certificate_key: str,
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

        with self.project_write_lock(
            namespace,
            project_id,
            permit_migration_id=clean_id,
        ):
            current_runtime_certificate = self.latest_runtime_certificate(
                namespace,
                project_id,
                certificate_key=runtime_test_certificate_key,
            )
            if (
                not isinstance(current_runtime_certificate, dict)
                or not self.project_record_is_signed(current_runtime_certificate)
                or current_runtime_certificate.get("certificate_id")
                != runtime_test_certificate_id
                or current_runtime_certificate.get("certificate_type")
                != "runtime_full_regression"
                or current_runtime_certificate.get("result") != "pass"
                or current_runtime_certificate.get("certificate_key")
                != runtime_test_certificate_key
                or current_runtime_certificate.get("ruleset_version")
                != ruleset_version
                or current_runtime_certificate.get("validator_version")
                != validator_version
            ):
                raise ValueError("migration_runtime_certificate_conflict")
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
                if record.get("status") == "rolling_back":
                    raise ValueError(
                        "migration_rollback_in_progress: finish rollback before execute"
                    )
                record_hub_before = record.get("hub_before")
                record_ledger_before = record.get("ledger_before")
                record_hub_after = record.get("hub_after")
                record_ledger_after = record.get("ledger_after")
                if (
                    record.get("migration_id") != clean_id
                    or record.get("record_id") != clean_id
                    or record.get("project_id") != project_id
                    or record.get("work_order_id") != work_order_id
                    or record.get("migration_key") != migration_key
                    or record.get("from_version") != from_version
                    or record.get("target_version") != target_version
                    or record.get("runtime_test_certificate_id")
                    != runtime_test_certificate_id
                    or record.get("ruleset_version") != ruleset_version
                    or record.get("validator_version") != validator_version
                    or record.get("changed_paths") != list(changed_paths)
                    or record.get("preserves_artifacts") is not True
                    or record.get("status")
                    not in {
                        "prepared",
                        "replaying",
                        "committed",
                        "rolled_back",
                    }
                    or not isinstance(record_hub_before, dict)
                    or not isinstance(record_ledger_before, dict)
                    or not isinstance(record_hub_after, dict)
                    or not isinstance(record_ledger_after, dict)
                    or record.get("hub_before_sha256") != before_hub_sha
                    or record.get("ledger_before_sha256")
                    != before_ledger_sha
                    or record.get("hub_after_sha256") != after_hub_sha
                    or record.get("ledger_after_sha256") != after_ledger_sha
                    or self.project_state_sha256(record_hub_before)
                    != before_hub_sha
                    or self.project_state_sha256(record_ledger_before)
                    != before_ledger_sha
                    or self.project_state_sha256(record_hub_after)
                    != after_hub_sha
                    or self.project_state_sha256(record_ledger_after)
                    != after_ledger_sha
                ):
                    raise ValueError("migration_record_authority_conflict")
                before_hub_sha = str(record["hub_before_sha256"])
                before_ledger_sha = str(record["ledger_before_sha256"])
                after_hub_sha = str(record["hub_after_sha256"])
                after_ledger_sha = str(record["ledger_after_sha256"])
                hub_after = record["hub_after"]
                ledger_after = record["ledger_after"]

            if (current_hub_sha, current_ledger_sha) == (
                after_hub_sha,
                after_ledger_sha,
            ):
                if record is None and (
                    current_hub_sha,
                    current_ledger_sha,
                ) != (before_hub_sha, before_ledger_sha):
                    raise ValueError(
                        "migration_recovery_required: signed migration journal required"
                    )
                if record is not None and record.get("status") != "committed":
                    record["status"] = "committed"
                    record["committed_at"] = utc_stamp()
                    record["integrity_hmac"] = self._record_integrity_hmac(record)
                    self.atomic_write_json(record_path, record)
                    return record
                if record is not None:
                    return record

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

            if record.get("status") == "rolled_back":
                # Sign replay intent before either after-image is restored. If
                # the process dies between the two replaces, the project-wide
                # fence prevents unrelated writes from stranding recovery.
                record["status"] = "replaying"
                record["replay_started_at"] = utc_stamp()
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
        certificate_after: dict[str, Any],
        stage_trace_after: dict[str, Any],
        hub_before: dict[str, Any],
        hub_after: dict[str, Any],
        runtime_test_certificate_id: str,
        runtime_test_certificate_key: str,
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
        certificate_id = safe_id(
            str(certificate_after.get("certificate_id") or ""),
            "certificate_id",
        )
        certificate_path = directory / "certificates" / f"{certificate_id}.json"
        stage_trace_id = safe_id(
            str(stage_trace_after.get("trace_id") or ""),
            "stage_trace_id",
        )
        stage_trace_path = directory / "telemetry" / f"{stage_trace_id}.json"
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
        certificate_after = sealed(certificate_after, certificate_id)
        stage_trace_after = sealed(stage_trace_after, stage_trace_id)
        stage_traces = hub_after.setdefault("performance_traces", [])
        matching_stage_traces = [
            row
            for row in stage_traces
            if isinstance(row, dict)
            and str(row.get("trace_id") or row.get("telemetry_id") or "")
            == stage_trace_id
        ]
        if matching_stage_traces and matching_stage_traces != [
            stage_trace_after
        ]:
            raise ValueError(
                "handoff_recovery_required: production stage trace already exists in Hub"
            )
        if not matching_stage_traces:
            stage_traces.append(copy.deepcopy(stage_trace_after))
        old_before_snapshot = copy.deepcopy(old_before)
        old_before_sha = self.project_state_sha256(old_before)
        old_after_sha = self.project_state_sha256(old_after)
        new_after_sha = self.project_state_sha256(new_after)
        certificate_after_sha = self.project_state_sha256(certificate_after)
        stage_trace_after_sha = self.project_state_sha256(stage_trace_after)
        hub_before_sha = self.project_state_sha256(hub_before)
        hub_after_sha = self.project_state_sha256(hub_after)

        with self.project_write_lock(namespace, project_id):
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
            current_certificate: dict[str, Any] | None = None
            current_certificate_sha: str | None = None
            if certificate_path.is_file():
                current_certificate = self.read_json(certificate_path)
                self._verify_record_integrity(
                    current_certificate, record_id=certificate_id
                )
                self._verify_project_record_primary_id(
                    "certificates",
                    current_certificate,
                    record_id=certificate_id,
                )
                if not self.project_record_is_signed(current_certificate):
                    raise ValueError(
                        "handoff_recovery_required: unsigned production certificate"
                    )
                current_certificate_sha = self.project_state_sha256(
                    current_certificate
                )
            current_stage_trace: dict[str, Any] | None = None
            current_stage_trace_sha: str | None = None
            if stage_trace_path.is_file():
                current_stage_trace = self.read_json(stage_trace_path)
                self._verify_record_integrity(
                    current_stage_trace, record_id=stage_trace_id
                )
                self._verify_project_record_primary_id(
                    "telemetry",
                    current_stage_trace,
                    record_id=stage_trace_id,
                )
                if not self.project_record_is_signed(current_stage_trace):
                    raise ValueError(
                        "handoff_recovery_required: unsigned production stage trace"
                    )
                current_stage_trace_sha = self.project_state_sha256(
                    current_stage_trace
                )

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
                certificate_after_sha = str(
                    journal["certificate_after_sha256"]
                )
                stage_trace_after_sha = str(
                    journal["stage_trace_after_sha256"]
                )
                hub_before_sha = str(journal["hub_before_sha256"])
                hub_after_sha = str(journal["hub_after_sha256"])
                old_after = dict(journal["old_after"])
                old_before_snapshot = dict(journal["old_before"])
                new_after = dict(journal["new_after"])
                certificate_after = dict(journal["certificate_after"])
                stage_trace_after = dict(journal["stage_trace_after"])
                hub_after = dict(journal["hub_after"])

            expected_hub_old = (
                old_before_snapshot
                if current_hub_sha == hub_before_sha
                else current_old
            )
            current_old_matches = [
                row
                for row in current_hub.get("handoffs", [])
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_old_id
            ]
            if (
                len(current_old_matches) != 1
                or not self._hub_handoff_matches_signed_record(
                    current_old_matches[0], expected_hub_old
                )
            ):
                raise ValueError(
                    "handoff_recovery_required: old Hub projection differs from signed record"
                )

            if (
                current_old_sha == old_after_sha
                and current_new_sha == new_after_sha
                and current_certificate_sha == certificate_after_sha
                and current_stage_trace_sha == stage_trace_after_sha
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
            if current_certificate_sha not in {None, certificate_after_sha}:
                raise ValueError(
                    "handoff_recovery_required: production certificate id is already occupied"
                )
            if current_stage_trace_sha not in {None, stage_trace_after_sha}:
                raise ValueError(
                    "handoff_recovery_required: production stage trace id is already occupied"
                )
            if journal is None and (
                current_old_sha != old_before_sha
                or current_hub_sha != hub_before_sha
                or current_new_sha is not None
                or current_certificate_sha is not None
                or current_stage_trace_sha is not None
            ):
                raise ValueError(
                    "handoff_recovery_required: unsigned partial recovery"
                )

            if journal is None:
                current_runtime = self.latest_runtime_certificate(
                    namespace,
                    project_id,
                    certificate_key=runtime_test_certificate_key,
                )
                if (
                    current_runtime is None
                    or current_runtime.get("certificate_id")
                    != runtime_test_certificate_id
                ):
                    raise ValueError(
                        "handoff_recovery_plan_stale: runtime certificate changed before commit"
                    )
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
                    "certificate_after_sha256": certificate_after_sha,
                    "stage_trace_after_sha256": stage_trace_after_sha,
                    "hub_before_sha256": hub_before_sha,
                    "hub_after_sha256": hub_after_sha,
                    "old_before": old_before_snapshot,
                    "old_after": old_after,
                    "new_after": new_after,
                    "certificate_after": certificate_after,
                    "stage_trace_after": stage_trace_after,
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
            if current_certificate_sha is None:
                self.atomic_write_json(certificate_path, certificate_after)
            if current_stage_trace_sha is None:
                self.atomic_write_json(stage_trace_path, stage_trace_after)
            if current_new_sha is None:
                self.atomic_write_json(new_path, new_after)
            if current_hub_sha == hub_before_sha:
                self.atomic_write_json(hub_path, hub_after)

            final_old_sha = self.project_state_sha256(self.read_json(old_path))
            final_new_sha = self.project_state_sha256(self.read_json(new_path))
            final_certificate_sha = self.project_state_sha256(
                self.read_json(certificate_path)
            )
            final_stage_trace_sha = self.project_state_sha256(
                self.read_json(stage_trace_path)
            )
            final_hub_sha = self.project_state_sha256(self.read_json(hub_path))
            if (
                final_old_sha,
                final_new_sha,
                final_certificate_sha,
                final_stage_trace_sha,
                final_hub_sha,
            ) != (
                old_after_sha,
                new_after_sha,
                certificate_after_sha,
                stage_trace_after_sha,
                hub_after_sha,
            ):
                raise RuntimeError("handoff_recovery_commit_incomplete")
            journal["status"] = "committed"
            journal["committed_at"] = utc_stamp()
            journal["integrity_hmac"] = self._record_integrity_hmac(journal)
            self.atomic_write_json(journal_path, journal)
            return journal

    def completed_handoff_recovery(
        self,
        namespace: str,
        project_id: str,
        *,
        recovery_id: str,
        old_handoff_id: str,
        new_handoff_id: str,
    ) -> dict[str, Any] | None:
        """Verify a committed recovery against the current additive Hub.

        A batch stores one journal per item, so an early item's ``hub_after``
        is only an intermediate snapshot.  Exact idempotent replay therefore
        verifies that item's signed old/new/certificate after-images and their
        unique current Hub projections while allowing later batch items to be
        appended.  Nonterminal journals return ``None`` for normal WAL resume.
        """

        clean_recovery_id = safe_id(recovery_id, "recovery_id")
        clean_old_id = safe_id(old_handoff_id, "old_handoff_id")
        clean_new_id = safe_id(new_handoff_id, "new_handoff_id")
        directory = self.project_dir(namespace, project_id)
        journal_path = (
            directory / "handoff_recoveries" / f"{clean_recovery_id}.json"
        )
        with self.project_write_lock(namespace, project_id):
            if not journal_path.is_file():
                raise ValueError("handoff_recovery_required: journal is missing")
            journal = self.read_json(journal_path)
            self._verify_record_integrity(
                journal, record_id=clean_recovery_id
            )
            self._verify_project_record_primary_id(
                "handoff_recoveries",
                journal,
                record_id=clean_recovery_id,
            )
            if not self.project_record_is_signed(journal):
                raise ValueError("handoff_recovery_signature_required")
            if (
                journal.get("old_handoff_id") != clean_old_id
                or journal.get("new_handoff_id") != clean_new_id
            ):
                raise ValueError("handoff_recovery_id_conflict")
            if journal.get("status") != "committed":
                return None

            old_path = directory / "handoffs" / f"{clean_old_id}.json"
            new_path = directory / "handoffs" / f"{clean_new_id}.json"
            certificate_after = journal.get("certificate_after")
            if not isinstance(certificate_after, dict):
                raise ValueError("handoff_recovery_required: certificate after-image is missing")
            certificate_id = safe_id(
                str(certificate_after.get("certificate_id") or ""),
                "certificate_id",
            )
            certificate_path = (
                directory / "certificates" / f"{certificate_id}.json"
            )
            stage_trace_after = journal.get("stage_trace_after")
            if not isinstance(stage_trace_after, dict):
                raise ValueError(
                    "handoff_recovery_required: production stage trace after-image is missing"
                )
            stage_trace_id = safe_id(
                str(stage_trace_after.get("trace_id") or ""),
                "stage_trace_id",
            )
            stage_trace_path = (
                directory / "telemetry" / f"{stage_trace_id}.json"
            )
            if not (
                old_path.is_file()
                and new_path.is_file()
                and certificate_path.is_file()
                and stage_trace_path.is_file()
            ):
                raise RuntimeError("handoff_recovery_commit_incomplete")
            old = self.read_json(old_path)
            new = self.read_json(new_path)
            certificate = self.read_json(certificate_path)
            stage_trace = self.read_json(stage_trace_path)
            for value, record_id, kind in (
                (old, clean_old_id, "handoffs"),
                (new, clean_new_id, "handoffs"),
                (certificate, certificate_id, "certificates"),
                (stage_trace, stage_trace_id, "telemetry"),
            ):
                self._verify_record_integrity(value, record_id=record_id)
                self._verify_project_record_primary_id(
                    kind, value, record_id=record_id
                )
                if not self.project_record_is_signed(value):
                    raise ValueError("handoff_recovery_signature_required")
            if (
                self.project_state_sha256(old)
                != journal.get("old_after_sha256")
                or self.project_state_sha256(new)
                != journal.get("new_after_sha256")
                or self.project_state_sha256(certificate)
                != journal.get("certificate_after_sha256")
                or self.project_state_sha256(stage_trace)
                != journal.get("stage_trace_after_sha256")
            ):
                raise ValueError(
                    "handoff_recovery_required: committed after-image changed"
                )
            hub = self.read_json(directory / "hub.json")
            old_projection = self._hub_record_projection(old)
            new_projection = self._hub_record_projection(new)
            old_matches = [
                row
                for row in hub.get("handoffs", [])
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_old_id
            ]
            new_matches = [
                row
                for row in hub.get("handoffs", [])
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_new_id
            ]
            trace_matches = [
                row
                for row in hub.get("performance_traces", [])
                if isinstance(row, dict)
                and str(row.get("trace_id") or row.get("telemetry_id") or "")
                == stage_trace_id
            ]
            if old_matches != [old_projection] or new_matches != [new_projection]:
                raise ValueError(
                    "handoff_recovery_required: committed Hub projection changed"
                )
            if trace_matches != [stage_trace]:
                raise ValueError(
                    "handoff_recovery_required: committed production trace projection changed"
                )
            result = dict(journal)
            result["idempotent_replay"] = True
            return result

    def commit_preflight_evidence_reissue(
        self,
        namespace: str,
        project_id: str,
        *,
        preflight_evidence_id: str,
        evidence_key: str,
        old_handoff_id: str,
        old_handoff_sha256: str,
        certificate_after: dict[str, Any],
        stage_trace_after: dict[str, Any],
        evidence_after: dict[str, Any],
        expected_hub_sha256: str,
        runtime_test_certificate_id: str,
        runtime_test_certificate_key: str,
    ) -> dict[str, Any]:
        """Append one crash-recoverable production-preflight evidence successor.

        The old handoff is compare-and-set but never changed.  A signed
        ``prepared`` evidence journal is durable before the deterministic
        certificate is written; only a ``committed`` journal is consumable by
        handoff recovery.  Exact retries finish or reuse the same identities.
        """

        clean_evidence_id = safe_id(
            preflight_evidence_id, "preflight_evidence_id"
        )
        clean_handoff_id = safe_id(old_handoff_id, "old_handoff_id")
        certificate_id = safe_id(
            str(certificate_after.get("certificate_id") or ""),
            "certificate_id",
        )
        directory = self.project_dir(namespace, project_id)
        handoff_path = directory / "handoffs" / f"{clean_handoff_id}.json"
        certificate_path = directory / "certificates" / f"{certificate_id}.json"
        stage_trace_id = safe_id(
            str(stage_trace_after.get("trace_id") or ""),
            "stage_trace_id",
        )
        stage_trace_path = directory / "telemetry" / f"{stage_trace_id}.json"
        evidence_path = (
            directory
            / "preflight_evidence_reissues"
            / f"{clean_evidence_id}.json"
        )

        def sealed(value: dict[str, Any], record_id: str) -> dict[str, Any]:
            result = dict(value)
            result["record_id"] = record_id
            result["project_id"] = project_id
            result["integrity_hmac"] = self._record_integrity_hmac(result)
            return result

        certificate_after = sealed(certificate_after, certificate_id)
        certificate_after_sha256 = self.project_state_sha256(certificate_after)
        stage_trace_after = sealed(stage_trace_after, stage_trace_id)
        stage_trace_after_sha256 = self.project_state_sha256(
            stage_trace_after
        )

        with self.project_write_lock(namespace, project_id):
            if not handoff_path.is_file():
                raise ValueError(
                    "preflight_evidence_reissue_required: old handoff is missing"
                )
            current_handoff = self.read_json(handoff_path)
            self._verify_record_integrity(
                current_handoff, record_id=clean_handoff_id
            )
            if not self.project_record_is_signed(current_handoff):
                raise ValueError(
                    "preflight_evidence_reissue_required: old handoff is unsigned"
                )
            if self.project_state_sha256(current_handoff) != old_handoff_sha256:
                raise ValueError(
                    "preflight_evidence_reissue_plan_stale: old handoff changed after plan"
                )
            hub = self.read_json(directory / "hub.json")
            if self.project_state_sha256(hub) != expected_hub_sha256:
                raise ValueError(
                    "preflight_evidence_reissue_plan_stale: project authority changed before commit"
                )
            matching_projections = [
                row
                for row in hub.get("handoffs", [])
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_handoff_id
            ]
            if (
                len(matching_projections) != 1
                or not self._hub_handoff_matches_signed_record(
                    matching_projections[0], current_handoff
                )
            ):
                raise ValueError(
                    "preflight_evidence_reissue_plan_stale: Hub projection differs from signed handoff"
                )

            evidence_directory = directory / "preflight_evidence_reissues"
            for candidate_path in sorted(evidence_directory.glob("*.json")):
                if candidate_path == evidence_path:
                    continue
                candidate = self.read_json(candidate_path)
                candidate_id = str(
                    candidate.get("preflight_evidence_id")
                    or candidate.get("record_id")
                    or ""
                )
                self._verify_record_integrity(
                    candidate, record_id=candidate_id
                )
                self._verify_project_record_primary_id(
                    "preflight_evidence_reissues",
                    candidate,
                    record_id=candidate_id,
                )
                if not self.project_record_is_signed(candidate):
                    raise ValueError(
                        "preflight_evidence_reissue_signature_required"
                    )
                if (
                    candidate.get("status") in {"prepared", "committed"}
                    and candidate.get("old_handoff_id")
                    == clean_handoff_id
                    and candidate.get("old_handoff_sha256")
                    == old_handoff_sha256
                    and candidate.get("current_fingerprints_sha256")
                    == evidence_after.get("current_fingerprints_sha256")
                    and candidate.get("runtime_test_certificate_id")
                    == evidence_after.get("runtime_test_certificate_id")
                ):
                    raise ValueError(
                        "preflight_evidence_reissue_fact_conflict: current revalidation evidence already exists"
                    )

            journal: dict[str, Any] | None = None
            if evidence_path.is_file():
                journal = self.read_json(evidence_path)
                self._verify_record_integrity(
                    journal, record_id=clean_evidence_id
                )
                self._verify_project_record_primary_id(
                    "preflight_evidence_reissues",
                    journal,
                    record_id=clean_evidence_id,
                )
                if not self.project_record_is_signed(journal):
                    raise ValueError(
                        "preflight_evidence_reissue_signature_required"
                    )
                if (
                    journal.get("evidence_key") != evidence_key
                    or journal.get("old_handoff_id") != clean_handoff_id
                    or journal.get("certificate_id") != certificate_id
                    or journal.get("old_handoff_sha256")
                    != old_handoff_sha256
                    or journal.get("certificate_after_sha256")
                    != certificate_after_sha256
                    or journal.get("stage_trace_after_sha256")
                    != stage_trace_after_sha256
                ):
                    raise ValueError(
                        "preflight_evidence_reissue_id_conflict"
                    )
                certificate_after = dict(journal["certificate_after"])
                stage_trace_after = dict(journal["stage_trace_after"])
                evidence_after = {
                    key: copy.deepcopy(value)
                    for key, value in journal.items()
                    if key not in {"integrity_hmac", "record_id", "project_id"}
                }

            current_certificate_sha256: str | None = None
            if certificate_path.is_file():
                current_certificate = self.read_json(certificate_path)
                self._verify_record_integrity(
                    current_certificate, record_id=certificate_id
                )
                self._verify_project_record_primary_id(
                    "certificates",
                    current_certificate,
                    record_id=certificate_id,
                )
                if not self.project_record_is_signed(current_certificate):
                    raise ValueError(
                        "preflight_evidence_reissue_certificate_unsigned"
                    )
                current_certificate_sha256 = self.project_state_sha256(
                    current_certificate
                )
                if current_certificate_sha256 != certificate_after_sha256:
                    raise ValueError(
                        "preflight_evidence_reissue_certificate_conflict"
                    )
            current_stage_trace_sha256: str | None = None
            if stage_trace_path.is_file():
                current_stage_trace = self.read_json(stage_trace_path)
                self._verify_record_integrity(
                    current_stage_trace, record_id=stage_trace_id
                )
                self._verify_project_record_primary_id(
                    "telemetry",
                    current_stage_trace,
                    record_id=stage_trace_id,
                )
                if not self.project_record_is_signed(current_stage_trace):
                    raise ValueError(
                        "preflight_evidence_reissue_trace_unsigned"
                    )
                current_stage_trace_sha256 = self.project_state_sha256(
                    current_stage_trace
                )
                if current_stage_trace_sha256 != stage_trace_after_sha256:
                    raise ValueError(
                        "preflight_evidence_reissue_trace_conflict"
                    )

            if journal is not None and journal.get("status") == "committed":
                if current_certificate_sha256 != certificate_after_sha256:
                    raise RuntimeError(
                        "preflight_evidence_reissue_commit_incomplete"
                    )
                if current_stage_trace_sha256 != stage_trace_after_sha256:
                    raise RuntimeError(
                        "preflight_evidence_reissue_commit_incomplete"
                    )
                result = dict(journal)
                result["idempotent_replay"] = True
                return result

            if journal is None:
                current_runtime = self.latest_runtime_certificate(
                    namespace,
                    project_id,
                    certificate_key=runtime_test_certificate_key,
                )
                if (
                    current_runtime is None
                    or current_runtime.get("certificate_id")
                    != runtime_test_certificate_id
                ):
                    raise ValueError(
                        "preflight_evidence_reissue_plan_stale: runtime certificate changed before commit"
                    )
                journal = dict(evidence_after)
                journal.update(
                    {
                        "record_id": clean_evidence_id,
                        "preflight_evidence_id": clean_evidence_id,
                        "project_id": project_id,
                        "evidence_key": evidence_key,
                        "old_handoff_id": clean_handoff_id,
                        "old_handoff_sha256": old_handoff_sha256,
                        "certificate_id": certificate_id,
                        "certificate_after_sha256": certificate_after_sha256,
                        "certificate_after": certificate_after,
                        "stage_trace_after_sha256": stage_trace_after_sha256,
                        "stage_trace_after": stage_trace_after,
                        "status": "prepared",
                        "created_at": utc_stamp(),
                    }
                )
                journal["integrity_hmac"] = self._record_integrity_hmac(
                    journal
                )
                self.atomic_write_json(evidence_path, journal)

            if current_certificate_sha256 is None:
                self.atomic_write_json(certificate_path, certificate_after)
            if current_stage_trace_sha256 is None:
                self.atomic_write_json(stage_trace_path, stage_trace_after)
            final_certificate_sha256 = self.project_state_sha256(
                self.read_json(certificate_path)
            )
            if final_certificate_sha256 != certificate_after_sha256:
                raise RuntimeError(
                    "preflight_evidence_reissue_commit_incomplete"
                )
            final_stage_trace_sha256 = self.project_state_sha256(
                self.read_json(stage_trace_path)
            )
            if final_stage_trace_sha256 != stage_trace_after_sha256:
                raise RuntimeError(
                    "preflight_evidence_reissue_commit_incomplete"
                )
            journal["status"] = "committed"
            journal["committed_at"] = utc_stamp()
            journal["integrity_hmac"] = self._record_integrity_hmac(journal)
            self.atomic_write_json(evidence_path, journal)
            result = dict(journal)
            result["idempotent_replay"] = False
            return result

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
        with self.project_write_lock(
            namespace,
            project_id,
            permit_migration_id=clean_id,
        ):
            if not record_path.is_file():
                raise ValueError("unknown migration_id")
            record = self.read_json(record_path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("migration_record_signature_required")
            self._verify_project_record_primary_id(
                "migrations", record, record_id=clean_id
            )
            if record.get("project_id") != project_id:
                raise ValueError("migration_record_authority_conflict")
            for body_field, sha_field in (
                ("hub_before", "hub_before_sha256"),
                ("hub_after", "hub_after_sha256"),
                ("ledger_before", "ledger_before_sha256"),
                ("ledger_after", "ledger_after_sha256"),
            ):
                body = record.get(body_field)
                if (
                    not isinstance(body, dict)
                    or self.project_state_sha256(body)
                    != record.get(sha_field)
                ):
                    raise ValueError("migration_record_authority_conflict")
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
            if status not in {
                "prepared",
                "replaying",
                "committed",
                "rolling_back",
            }:
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
            if status in {"prepared", "replaying", "rolling_back"} and (
                current_hub_sha not in {before_hub_sha, after_hub_sha}
                or current_ledger_sha not in {before_ledger_sha, after_ledger_sha}
            ):
                raise ValueError(
                    "migration_rollback_cas_mismatch: project changed during rollback"
                )
            if status != "rolling_back":
                # Sign intent before either project-state file changes. A retry
                # can then distinguish a legitimate cancellation/half rollback
                # from an unrelated write and complete only the missing side.
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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

        if "record_id" in value and value.get("record_id") != record_id:
            raise ValueError(f"project_record_identity_mismatch: {record_id}")
        if "integrity_hmac" not in value:
            return
        signature = value.get("integrity_hmac")
        if value.get("record_id") != record_id:
            raise ValueError(f"project_record_identity_mismatch: {record_id}")
        if not isinstance(signature, str) or not self._record_integrity_signature_matches(
            value, signature
        ):
            raise ValueError(f"project_record_integrity_mismatch: {record_id}")

    @staticmethod
    def _verify_project_record_primary_id(
        kind: str, value: dict[str, Any], *, record_id: str
    ) -> None:
        """Bind signed and narrowly compatible legacy records to one ID."""

        primary_key = PROJECT_RECORD_PRIMARY_ID_KEYS[kind]
        if value.get(primary_key) != record_id:
            raise ValueError(f"project_record_identity_mismatch: {record_id}")
        if (
            kind == "telemetry"
            and "telemetry_id" in value
            and value.get("telemetry_id") != record_id
        ):
            raise ValueError(f"project_record_identity_mismatch: {record_id}")

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
        if record.get("record_id", clean_id) != clean_id:
            raise ValueError(f"project_record_identity_mismatch: {clean_id}")
        if record.get("project_id", project_id) != project_id:
            raise ValueError(f"invalid {clean_kind} record: {clean_id}")
        primary_key = PROJECT_RECORD_PRIMARY_ID_KEYS[clean_kind]
        if record.get(primary_key) != clean_id:
            raise ValueError(f"project_record_identity_mismatch: {clean_id}")
        record["record_id"] = clean_id
        record["project_id"] = project_id
        record.setdefault("created_at", utc_stamp())
        path = directory / clean_kind / f"{clean_id}.json"
        with self.project_write_lock(namespace, project_id):
            if (
                clean_kind == "certificates"
                and record.get("certificate_type")
                == "runtime_full_regression"
            ):
                record["certificate_sequence"] = (
                    self._next_runtime_certificate_sequence_unlocked(
                        namespace,
                        project_id,
                        certificate_key=str(
                            record.get("certificate_key") or ""
                        ),
                    )
                )
            record["integrity_hmac"] = self._record_integrity_hmac(record)
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
        self._verify_project_record_primary_id(
            clean_kind, value, record_id=clean_id
        )
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
        with self.project_write_lock(namespace, project_id):
            if not path.is_file():
                raise ValueError(f"unknown {clean_kind} record_id: {clean_id}")
            value = self.read_json(path)
            if not isinstance(value, dict) or value.get("project_id") != project_id:
                raise ValueError(f"invalid {clean_kind} record: {clean_id}")
            self._verify_record_integrity(value, record_id=clean_id)
            self._verify_project_record_primary_id(
                clean_kind, value, record_id=clean_id
            )
            if (
                clean_kind in SIGNED_ONLY_PROJECT_RECORD_KINDS
                and not self.project_record_is_signed(value)
            ):
                raise ValueError(f"project_record_signature_required: {clean_id}")
            yield value
            if (
                value.get("record_id") != clean_id
                or value.get("project_id") != project_id
            ):
                raise ValueError(
                    f"project_record_identity_mismatch: {clean_id}"
                )
            primary_key = PROJECT_RECORD_PRIMARY_ID_KEYS[clean_kind]
            if value.get(primary_key) != clean_id:
                raise ValueError(
                    f"project_record_identity_mismatch: {clean_id}"
                )
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
            try:
                value = self.read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or value.get("project_id") != project_id:
                raise ValueError(f"invalid {clean_kind} record: {path.stem}")
            self._verify_record_integrity(value, record_id=path.stem)
            self._verify_project_record_primary_id(
                clean_kind, value, record_id=path.stem
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

    def list_project_records_strict(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
    ) -> list[dict[str, Any]]:
        """Enumerate authority records without hiding malformed entries."""

        clean_kind = self._project_record_kind(kind)
        directory = self.project_dir(namespace, project_id) / clean_kind
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            value = self.read_json(path)
            if not isinstance(value, dict) or value.get("project_id") != project_id:
                raise ValueError(f"invalid {clean_kind} record: {path.stem}")
            record_id = path.stem
            self._verify_record_integrity(value, record_id=record_id)
            self._verify_project_record_primary_id(
                clean_kind, value, record_id=record_id
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

    def _load_handoff_projection_snapshot_unlocked(
        self,
        namespace: str,
        project_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        handoffs = self.list_project_records_strict(
            namespace,
            project_id,
            kind="handoffs",
        )
        hub = self.load_hub(namespace, project_id)
        return handoffs, hub

    def load_handoff_projection_snapshot(
        self,
        namespace: str,
        project_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read signed handoffs and their Hub projections at one lock epoch.

        Successor lifecycle transitions intentionally write the signed authority
        before its unsigned Hub projection.  A reader that enumerates those two
        stores without the project lock can otherwise combine the signed state
        from before a transition with the Hub state from after it (or vice
        versa), producing a projection mismatch that never existed as a durable
        state.  Later claim/finalization methods still perform their own locked
        compare-and-swap checks; this snapshot only makes lineage validation
        linearizable.
        """

        with self.lock(f"{namespace}-{project_id}"):
            return self._load_handoff_projection_snapshot_unlocked(
                namespace,
                project_id,
            )

    def load_handoff_projection_snapshot_while_project_locked(
        self,
        namespace: str,
        project_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read a snapshot when the caller already owns the project lock.

        This narrowly scoped entrypoint avoids attempting to reacquire the
        non-reentrant cross-process ``FileLock`` from sign-off validation.  It
        must only be called by code whose enclosing storage mutation already
        owns ``lock(f"{namespace}-{project_id}")``.
        """

        return self._load_handoff_projection_snapshot_unlocked(
            namespace,
            project_id,
        )

    @staticmethod
    def _hub_record_projection(value: dict[str, Any]) -> dict[str, Any]:
        """Remove signed-record envelope fields from a Hub projection."""

        return {
            key: copy.deepcopy(item)
            for key, item in value.items()
            if key not in {"record_id", "project_id", "integrity_hmac"}
        }

    @classmethod
    def _hub_handoff_matches_signed_record(
        cls,
        hub_handoff: dict[str, Any],
        signed_handoff: dict[str, Any],
    ) -> bool:
        """Accept a canonical projection or the exact legacy signed copy."""

        expected_projection = cls._hub_record_projection(signed_handoff)
        return hub_handoff == expected_projection or hub_handoff == signed_handoff

    def find_project_record(
        self,
        namespace: str,
        project_id: str,
        *,
        kind: str,
        field: str,
        value: Any,
    ) -> dict[str, Any] | None:
        for record in self.list_project_records_strict(
            namespace, project_id, kind=kind
        ):
            if record.get(field) == value:
                return record
        return None

    def _runtime_certificates_for_key_unlocked(
        self,
        namespace: str,
        project_id: str,
        *,
        certificate_key: str,
    ) -> list[dict[str, Any]]:
        if not certificate_key:
            raise ValueError(
                "runtime_certificate_storage_invalid: certificate key is empty"
            )
        matches = [
            record
            for record in self.list_project_records_strict(
                namespace, project_id, kind="certificates"
            )
            if record.get("certificate_key") == certificate_key
        ]
        for record in matches:
            if (
                record.get("certificate_type")
                != "runtime_full_regression"
                or not self.project_record_is_signed(record)
            ):
                raise ValueError(
                    "runtime_certificate_storage_invalid: matching runtime authority is malformed"
                )
            sequence = record.get("certificate_sequence")
            if sequence is not None and (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 1
            ):
                raise ValueError(
                    "runtime_certificate_storage_invalid: certificate sequence is invalid"
                )
        return matches

    def _next_runtime_certificate_sequence_unlocked(
        self,
        namespace: str,
        project_id: str,
        *,
        certificate_key: str,
    ) -> int:
        matches = self._runtime_certificates_for_key_unlocked(
            namespace,
            project_id,
            certificate_key=certificate_key,
        )
        sequences = [
            int(record["certificate_sequence"])
            for record in matches
            if record.get("certificate_sequence") is not None
        ]
        if len(sequences) != len(set(sequences)):
            raise ValueError(
                "runtime_certificate_storage_invalid: duplicate certificate sequence"
            )
        return max(sequences, default=0) + 1

    def latest_runtime_certificate(
        self,
        namespace: str,
        project_id: str,
        *,
        certificate_key: str,
    ) -> dict[str, Any] | None:
        """Resolve one runtime authority by its signed project-local total order."""

        matches = self._runtime_certificates_for_key_unlocked(
            namespace,
            project_id,
            certificate_key=certificate_key,
        )
        if not matches:
            return None
        sequenced = [
            record
            for record in matches
            if record.get("certificate_sequence") is not None
        ]
        if sequenced:
            latest_sequence = max(
                int(record["certificate_sequence"])
                for record in sequenced
            )
            latest = [
                record
                for record in sequenced
                if record.get("certificate_sequence") == latest_sequence
            ]
            if len(latest) != 1:
                raise ValueError(
                    "runtime_certificate_storage_invalid: latest certificate sequence is ambiguous"
                )
            return latest[0]

        created_at_values = [
            record.get("created_at") for record in matches
        ]
        if any(
            not isinstance(value, str) or not value
            for value in created_at_values
        ):
            raise ValueError(
                "runtime_certificate_storage_invalid: legacy certificate timestamp is invalid"
            )
        latest_created_at = max(str(value) for value in created_at_values)
        latest_legacy = [
            record
            for record in matches
            if record.get("created_at") == latest_created_at
        ]
        if len(latest_legacy) != 1:
            raise ValueError(
                "runtime_certificate_storage_invalid: legacy certificate order is ambiguous"
            )
        return latest_legacy[0]

    def handoff_is_published(
        self,
        namespace: str,
        project_id: str,
        handoff: dict[str, Any],
    ) -> bool:
        """Return whether a successor-bound handoff is publicly consumable."""

        if handoff.get("recovery_publication_fenced") is True:
            recovery_id = str(handoff.get("recovery_id") or "")
            try:
                clean_recovery_id = safe_id(recovery_id, "recovery_id")
            except ValueError:
                return False
            journal_path = (
                self.project_dir(namespace, project_id)
                / "handoff_recoveries"
                / f"{clean_recovery_id}.json"
            )
            if not journal_path.is_file():
                return False
            try:
                journal = self.read_json(journal_path)
                self._verify_record_integrity(
                    journal, record_id=clean_recovery_id
                )
                self._verify_project_record_primary_id(
                    "handoff_recoveries",
                    journal,
                    record_id=clean_recovery_id,
                )
                handoff_id = safe_id(
                    str(handoff.get("handoff_id") or ""),
                    "handoff_id",
                )
                self._verify_record_integrity(
                    handoff, record_id=handoff_id
                )
                self._verify_project_record_primary_id(
                    "handoffs", handoff, record_id=handoff_id
                )
                journal_new = journal.get("new_after")
                if not isinstance(journal_new, dict):
                    return False
                base_projection = self._hub_record_projection(journal_new)
                current_projection = self._hub_record_projection(handoff)

                def production_projection(
                    value: dict[str, Any],
                ) -> dict[str, Any]:
                    return {
                        key: copy.deepcopy(item)
                        for key, item in value.items()
                        if key not in RECOVERY_HANDOFF_AUDIT_MUTABLE_KEYS
                    }

                if (
                    not self.project_record_is_signed(journal)
                    or not self.project_record_is_signed(handoff)
                    or journal.get("status") != "committed"
                    or journal.get("new_handoff_id")
                    != handoff.get("handoff_id")
                    or journal.get("recovery_id") != recovery_id
                    or self.project_state_sha256(journal_new)
                    != journal.get("new_after_sha256")
                    or production_projection(current_projection)
                    != production_projection(base_projection)
                ):
                    return False
                certificate_after = journal.get("certificate_after")
                stage_trace_after = journal.get("stage_trace_after")
                if not isinstance(certificate_after, dict) or not isinstance(
                    stage_trace_after, dict
                ):
                    return False
                certificate_id = safe_id(
                    str(certificate_after.get("certificate_id") or ""),
                    "certificate_id",
                )
                stage_trace_id = safe_id(
                    str(stage_trace_after.get("trace_id") or ""),
                    "stage_trace_id",
                )
                directory = self.project_dir(namespace, project_id)
                certificate = self.read_json(
                    directory / "certificates" / f"{certificate_id}.json"
                )
                stage_trace = self.read_json(
                    directory / "telemetry" / f"{stage_trace_id}.json"
                )
                for value, record_id, kind in (
                    (certificate, certificate_id, "certificates"),
                    (stage_trace, stage_trace_id, "telemetry"),
                ):
                    self._verify_record_integrity(value, record_id=record_id)
                    self._verify_project_record_primary_id(
                        kind, value, record_id=record_id
                    )
                    if not self.project_record_is_signed(value):
                        return False
                if (
                    self.project_state_sha256(certificate)
                    != journal.get("certificate_after_sha256")
                    or self.project_state_sha256(stage_trace)
                    != journal.get("stage_trace_after_sha256")
                    or certificate != certificate_after
                    or stage_trace != stage_trace_after
                ):
                    return False
                report = certificate.get("report")
                if (
                    certificate.get("certificate_type")
                    != "production_preflight"
                    or certificate.get("audit_class")
                    != "production_preflight"
                    or certificate.get("certificate_id")
                    != handoff.get("certificate_id")
                    or certificate.get("work_order_id")
                    != journal.get("work_order_id")
                    or certificate.get("production_work_order_id")
                    != handoff.get("work_order_id")
                    or certificate.get("artifact_id")
                    != handoff.get("storyboard_artifact_id")
                    or certificate.get("canonical_artifact_id")
                    != handoff.get("canonical_artifact_id")
                    or certificate.get("canonical_sha256")
                    != handoff.get("canonical_sha256")
                    or certificate.get("source_fingerprint")
                    != handoff.get("source_fingerprint")
                    or certificate.get("boundary_hash")
                    != handoff.get("boundary_hash")
                    or certificate.get("ruleset_version")
                    != handoff.get("ruleset_version")
                    or certificate.get("validator_version")
                    != handoff.get("validator_version")
                    or certificate.get("cache_key")
                    != handoff.get("cache_key")
                    or certificate.get("cache_components")
                    != handoff.get("cache_components")
                    or certificate.get("checked_shot_count")
                    != handoff.get("checked_shot_count")
                    or certificate.get("preflight_status")
                    != "production_preflight_pass"
                    or certificate.get("preflight_issue_count") != 0
                    or certificate.get("independent_audit_status")
                    != "PENDING_AUDIT"
                    or certificate.get("evidence_origin")
                    != "controlled_handoff_recovery_revalidation"
                    or certificate.get("issuing_work_order_id")
                    != journal.get("work_order_id")
                    or certificate.get("recovery_work_order_id")
                    != journal.get("work_order_id")
                    or certificate.get("recovery_gap_id")
                    != journal.get("gap_id")
                    or certificate.get("recovery_plan_sha256")
                    != handoff.get("recovery_plan_sha256")
                    or certificate.get("recovery_trace_id")
                    != handoff.get("recovery_trace_id")
                    or certificate.get("source_handoff_id")
                    != journal.get("old_handoff_id")
                    or certificate.get("current_fingerprints")
                    != handoff.get("fingerprints")
                    or certificate.get("current_fingerprints_sha256")
                    != handoff.get("recovery_plan_item", {}).get(
                        "current_fingerprints_sha256"
                    )
                    or not isinstance(report, dict)
                    or certificate.get("report_sha256")
                    != sha256_bytes(canonical_json_bytes(report))
                    or certificate.get("first_draft_grade_reconstructed")
                    is not False
                    or certificate.get("authored_artifacts_modified")
                    is not False
                    or certificate.get("independent_audit_verdict_issued")
                    is not False
                    or stage_trace.get("trace_id")
                    != handoff.get("stage_trace_id")
                    or stage_trace.get("telemetry_id")
                    != handoff.get("stage_trace_id")
                    or stage_trace.get("trace_id")
                    != handoff.get("recovery_trace_id")
                    or stage_trace.get("trace_id") != journal.get("trace_id")
                    or stage_trace.get("operation")
                    != "recover_storyboard_handoff"
                    or stage_trace.get("phase") != "handoff_recovery"
                    or stage_trace.get("window_role") != "production"
                    or stage_trace.get("task_type")
                    != "production_preflight"
                    or stage_trace.get("work_order_id")
                    != journal.get("work_order_id")
                    or stage_trace.get("production_work_order_id")
                    != handoff.get("work_order_id")
                    or stage_trace.get("gap_id") != journal.get("gap_id")
                    or stage_trace.get("plan_sha256")
                    != handoff.get("recovery_plan_sha256")
                    or stage_trace.get("authority_record_id")
                    != clean_recovery_id
                    or stage_trace.get("artifact_id")
                    != handoff.get("storyboard_artifact_id")
                    or stage_trace.get("input_fingerprints")
                    != handoff.get("fingerprints")
                ):
                    return False

                status = handoff.get("status")
                live_claim_fields = {
                    "claimed_by_work_order_id",
                    "claim_token",
                    "claim_epoch_seconds",
                    "claim_lease_seconds",
                    "claim_lease_expires_at",
                    "claim_lease_expires_epoch_seconds",
                }
                consumed_fields = {
                    "consumed_by_work_order_id",
                    "consumed_at",
                    "audit_certificate_id",
                }
                if status == "prepared":
                    if live_claim_fields & set(handoff) or consumed_fields & set(
                        handoff
                    ):
                        return False
                elif status == "consuming":
                    owner = handoff.get("claimed_by_work_order_id")
                    authority = handoff.get("claim_authority_work_order_id")
                    generation = handoff.get("claim_generation")
                    token = handoff.get("claim_token")
                    lease_seconds = handoff.get("claim_lease_seconds")
                    if (
                        not live_claim_fields <= set(handoff)
                        or consumed_fields & set(handoff)
                        or not isinstance(owner, str)
                        or not owner
                        or owner != authority
                        or not isinstance(token, str)
                        or not token
                        or not isinstance(generation, int)
                        or isinstance(generation, bool)
                        or generation < 1
                        or not isinstance(lease_seconds, int)
                        or isinstance(lease_seconds, bool)
                        or not 1
                        <= lease_seconds
                        <= MAX_CONSUMPTION_LEASE_SECONDS
                    ):
                        return False
                elif status == "consumed":
                    if (
                        live_claim_fields & set(handoff)
                        or not consumed_fields <= set(handoff)
                        or handoff.get("consumed_by_work_order_id")
                        != handoff.get("claim_authority_work_order_id")
                        or not isinstance(
                            handoff.get("audit_certificate_id"), str
                        )
                        or not handoff.get("audit_certificate_id")
                        or not isinstance(handoff.get("consumed_at"), str)
                        or not handoff.get("consumed_at")
                    ):
                        return False
                else:
                    return False
                hub = self.read_json(directory / "hub.json")
                handoff_matches = [
                    row
                    for row in hub.get("handoffs", [])
                    if isinstance(row, dict)
                    and row.get("handoff_id") == handoff.get("handoff_id")
                ]
                trace_matches = [
                    row
                    for row in hub.get("performance_traces", [])
                    if isinstance(row, dict)
                    and str(
                        row.get("trace_id")
                        or row.get("telemetry_id")
                        or ""
                    )
                    == stage_trace_id
                ]
                expected_hub_projection = copy.deepcopy(base_projection)
                if status == "consumed":
                    expected_hub_projection["status"] = "consumed"
                    expected_hub_projection["audit_certificate_id"] = (
                        handoff["audit_certificate_id"]
                    )
                if handoff_matches != [
                    expected_hub_projection
                ] or trace_matches != [stage_trace]:
                    return False
            except (OSError, TypeError, ValueError):
                return False
            return True

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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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

    def _reaudit_consumed_finalization_error(
        self,
        value: dict[str, Any],
    ) -> str | None:
        """Return the exact semantic defect in a consumed successor."""

        if value.get("status") != "consumed":
            return None
        consumed_by = value.get("consumed_by_work_order_id")
        consumed_at = value.get("consumed_at")
        live_claim_fields = {
            "claimed_by_work_order_id",
            "claim_token",
            "claim_epoch_seconds",
            "claim_lease_seconds",
            "claim_lease_expires_at",
            "claim_lease_expires_epoch_seconds",
        }
        if (
            not isinstance(consumed_by, str)
            or not consumed_by
            or consumed_by != value.get("claim_authority_work_order_id")
            or not isinstance(consumed_at, str)
            or not consumed_at
            or live_claim_fields & set(value)
        ):
            return (
                "reaudit_claim_binding_invalid: consumed successor has an "
                "invalid finalization shape"
            )
        audit_certificate_id = str(
            value.get("audit_certificate_id") or ""
        )
        latest_certificate_id = str(
            value.get("latest_audit_certificate_id") or ""
        )
        if (
            not audit_certificate_id
            or latest_certificate_id != audit_certificate_id
        ):
            return (
                "reaudit_certificate_binding_invalid: consumed successor "
                "certificate pointers differ"
            )

        claim = value.get("consumption_claim")
        claim_fields = {
            "claim_token",
            "claim_generation",
            "claim_epoch_seconds",
            "claim_lease_seconds",
            "claim_lease_expires_at",
            "claim_lease_expires_epoch_seconds",
        }
        if not isinstance(claim, dict) or set(claim) != claim_fields:
            return (
                "reaudit_claim_binding_invalid: consumed successor claim "
                "snapshot is incomplete"
            )
        top_generation = value.get("claim_generation")
        claim_generation = claim.get("claim_generation")
        claim_token = claim.get("claim_token")
        claim_epoch = claim.get("claim_epoch_seconds")
        lease_seconds = claim.get("claim_lease_seconds")
        lease_expires_at = claim.get("claim_lease_expires_at")
        lease_expires_epoch = claim.get(
            "claim_lease_expires_epoch_seconds"
        )
        if (
            not isinstance(top_generation, int)
            or isinstance(top_generation, bool)
            or top_generation < 1
            or not isinstance(claim_generation, int)
            or isinstance(claim_generation, bool)
            or claim_generation != top_generation
            or not isinstance(claim_token, str)
            or not claim_token
            or not self._valid_reaudit_claim_epoch(claim_epoch)
            or not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS
            or not self._valid_reaudit_claim_epoch(lease_expires_epoch)
            or abs(
                float(lease_expires_epoch)
                - (float(claim_epoch) + lease_seconds)
            )
            > 1e-6
            or not isinstance(lease_expires_at, str)
            or lease_expires_at
            != time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(float(lease_expires_epoch)),
            )
        ):
            return (
                "reaudit_claim_binding_invalid: consumed successor claim "
                "snapshot differs from its finalized lease"
            )
        preclaim_sha256 = value.get(
            "reaudit_preclaim_projection_sha256"
        )
        if (
            not isinstance(preclaim_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", preclaim_sha256) is None
        ):
            return (
                "reaudit_claim_binding_invalid: consumed successor preclaim "
                "projection binding is invalid"
            )
        preclaim_projection = self._hub_record_projection(value)
        preclaim_projection["status"] = "prepared"
        for key in (
            "consumed_by_work_order_id",
            "audit_certificate_id",
            "latest_audit_certificate_id",
            "consumed_at",
            "consumption_claim",
        ):
            preclaim_projection.pop(key, None)
        if claim_generation == 1:
            preclaim_projection.pop("claim_generation", None)
            preclaim_projection.pop(
                "reaudit_preclaim_projection_sha256", None
            )
        else:
            history = preclaim_projection.get("claim_history")
            prior_index = next(
                (
                    index
                    for index in range(len(history or []) - 1, -1, -1)
                    if isinstance((history or [])[index], dict)
                    and (history or [])[index].get("claim_generation")
                    == claim_generation - 1
                ),
                None,
            )
            prior_claim = (
                history[prior_index]
                if isinstance(history, list)
                and isinstance(prior_index, int)
                else None
            )
            prior_preclaim_sha256 = (
                prior_claim.get("reaudit_preclaim_projection_sha256")
                if isinstance(prior_claim, dict)
                else None
            )
            if (
                not isinstance(prior_preclaim_sha256, str)
                or re.fullmatch(
                    r"[0-9a-f]{64}", prior_preclaim_sha256
                )
                is None
            ):
                return (
                    "reaudit_claim_binding_invalid: consumed successor "
                    "cannot prove its prior preclaim projection"
                )
            prior_state = prior_claim.get("state")
            if prior_state == "released":
                preclaim_projection["claim_generation"] = (
                    claim_generation - 1
                )
                preclaim_projection[
                    "reaudit_preclaim_projection_sha256"
                ] = prior_preclaim_sha256
            elif prior_state == "lease_expired":
                prior_live_fields = {
                    "claimed_by_work_order_id": prior_claim.get(
                        "claimed_by_work_order_id"
                    ),
                    "claim_token": prior_claim.get("claim_token"),
                    "claim_generation": prior_claim.get(
                        "claim_generation"
                    ),
                    "claim_epoch_seconds": prior_claim.get(
                        "claim_epoch_seconds"
                    ),
                    "claim_lease_seconds": prior_claim.get(
                        "claim_lease_seconds"
                    ),
                    "claim_lease_expires_at": prior_claim.get(
                        "claim_lease_expires_at"
                    ),
                    "claim_lease_expires_epoch_seconds": prior_claim.get(
                        "claim_lease_expires_epoch_seconds"
                    ),
                    "reaudit_preclaim_projection_sha256": (
                        prior_preclaim_sha256
                    ),
                }
                if any(
                    value in {None, ""}
                    for value in prior_live_fields.values()
                ):
                    return (
                        "reaudit_claim_binding_invalid: consumed successor "
                        "expired claim history is incomplete"
                    )
                preclaim_projection["status"] = "consuming"
                preclaim_projection.update(prior_live_fields)
                prior_history = copy.deepcopy(history)
                prior_history.pop(prior_index)
                if prior_history:
                    preclaim_projection["claim_history"] = prior_history
                else:
                    preclaim_projection.pop("claim_history", None)
            else:
                return (
                    "reaudit_claim_binding_invalid: consumed successor prior "
                    "claim history state is invalid"
                )
        if self.project_state_sha256(preclaim_projection) != preclaim_sha256:
            return (
                "reaudit_claim_binding_invalid: consumed successor preclaim "
                "projection differs from its finalized claim"
            )
        return None

    @staticmethod
    def _valid_reaudit_claim_epoch(value: Any) -> bool:
        """Accept only finite UTC epochs that ``gmtime`` can represent."""

        return bool(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
            and value <= 253402300799
            and math.isfinite(value)
        )

    def _reaudit_successor_lifecycle_error(
        self,
        value: dict[str, Any],
    ) -> str | None:
        """Validate the exact prepared/consuming/consumed claim shape."""

        if value.get("reaudit_kind") != "independent_audit_successor":
            return "reaudit_lineage_invalid: handoff is not a successor"
        authority = value.get("claim_authority_work_order_id")
        if not isinstance(authority, str) or not authority:
            return (
                "reaudit_claim_binding_invalid: successor claim authority "
                "is missing"
            )
        status = value.get("status")
        live_claim_fields = {
            "claimed_by_work_order_id",
            "claim_token",
            "claim_epoch_seconds",
            "claim_lease_seconds",
            "claim_lease_expires_at",
            "claim_lease_expires_epoch_seconds",
        }
        consumed_fields = {
            "consumed_by_work_order_id",
            "audit_certificate_id",
            "latest_audit_certificate_id",
            "consumed_at",
            "consumption_claim",
        }
        present = set(value)
        if status == "prepared":
            if live_claim_fields & present or consumed_fields & present:
                return (
                    "reaudit_claim_binding_invalid: prepared successor has "
                    "live or consumed claim fields"
                )
            generation = value.get("claim_generation")
            if generation is not None and (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
            ):
                return (
                    "reaudit_claim_binding_invalid: prepared successor claim "
                    "generation is invalid"
                )
            for field in (
                "reaudit_preclaim_projection_sha256",
                "reaudit_prerelease_projection_sha256",
            ):
                binding = value.get(field)
                if binding is not None and (
                    not isinstance(binding, str)
                    or re.fullmatch(r"[0-9a-f]{64}", binding) is None
                ):
                    return (
                        "reaudit_claim_binding_invalid: prepared successor "
                        f"{field} is invalid"
                    )
            return None
        if status == "consuming":
            required_live_fields = live_claim_fields | {"claim_generation"}
            if (
                not required_live_fields <= present
                or consumed_fields & present
                or value.get("claimed_by_work_order_id") != authority
            ):
                return (
                    "reaudit_claim_binding_invalid: consuming successor has "
                    "an invalid claim shape"
                )
            generation = value.get("claim_generation")
            token = value.get("claim_token")
            claim_epoch = value.get("claim_epoch_seconds")
            lease_seconds = value.get("claim_lease_seconds")
            lease_expires_at = value.get("claim_lease_expires_at")
            lease_expires_epoch = value.get(
                "claim_lease_expires_epoch_seconds"
            )
            preclaim_sha256 = value.get(
                "reaudit_preclaim_projection_sha256"
            )
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
                or not isinstance(token, str)
                or not token
                or not self._valid_reaudit_claim_epoch(claim_epoch)
                or not isinstance(lease_seconds, int)
                or isinstance(lease_seconds, bool)
                or not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS
                or not self._valid_reaudit_claim_epoch(lease_expires_epoch)
                or abs(
                    float(lease_expires_epoch)
                    - (float(claim_epoch) + lease_seconds)
                )
                > 1e-6
                or not isinstance(lease_expires_at, str)
                or lease_expires_at
                != time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(float(lease_expires_epoch)),
                )
                or not isinstance(preclaim_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", preclaim_sha256) is None
            ):
                return (
                    "reaudit_claim_binding_invalid: consuming successor claim "
                    "lease or preclaim binding is invalid"
                )
            return None
        if status == "consumed":
            return self._reaudit_consumed_finalization_error(value)
        return (
            "reaudit_claim_binding_invalid: successor lifecycle status is "
            f"{status!r}"
        )

    @staticmethod
    def _reaudit_successor_immutable_identity(
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value.get(key))
            for key in REAUDIT_IMMUTABLE_IDENTITY_KEYS
            if key in value
        }

    @classmethod
    def _reaudit_successor_lineage_payload(
        cls,
        *,
        root: dict[str, Any],
        parent: dict[str, Any],
        successor: dict[str, Any],
    ) -> dict[str, Any]:
        root_identity = cls._reaudit_successor_immutable_identity(root)
        return {
            "spec": "independent-reaudit-successor-v1",
            "root_handoff_id": str(root.get("handoff_id") or ""),
            "parent_handoff_id": str(parent.get("handoff_id") or ""),
            "successor_handoff_id": str(
                successor.get("handoff_id") or ""
            ),
            "reaudit_sequence": int(
                successor.get("reaudit_sequence") or 0
            ),
            "audit_work_order_id": str(
                successor.get("claim_authority_work_order_id") or ""
            ),
            "previous_audit_certificate_id": str(
                successor.get("previous_audit_certificate_id") or ""
            ),
            "previous_audit_certificate_hmac": str(
                successor.get("previous_audit_certificate_hmac") or ""
            ),
            "previous_audit_event_sha256": str(
                successor.get("previous_audit_event_sha256") or ""
            ),
            "production_stage_trace_id": str(
                successor.get("production_stage_trace_id") or ""
            ),
            "route_trace_id": str(
                successor.get("reaudit_route_trace_id") or ""
            ),
            "audit_event_trace_id": str(
                successor.get("reaudit_event_trace_id") or ""
            ),
            "audit_window_binding_id": str(
                successor.get("reaudit_audit_window_binding_id") or ""
            ),
            "audit_actor_binding_id": str(
                successor.get("reaudit_audit_actor_binding_id") or ""
            ),
            "audit_session_fingerprint": str(
                successor.get("reaudit_audit_session_fingerprint") or ""
            ),
            "immutable_identity_sha256": sha256_bytes(
                canonical_json_bytes(root_identity)
            ),
        }

    def commit_reaudit_successor(
        self,
        namespace: str,
        project_id: str,
        *,
        root_handoff_id: str,
        parent_handoff_id: str,
        successor_handoff_id: str,
        consumer_work_order_id: str,
        successor: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically publish one server-derived independent re-audit route.

        A consumed production handoff is immutable audit history.  A forced
        re-audit therefore receives a fresh, single-use handoff instead of
        reopening the consumed record.  The signed record and its Hub
        projection are written under one project lock and compensated on an
        ordinary write failure so callers never observe a one-sided route.
        """

        clean_root_id = safe_id(root_handoff_id, "root_handoff_id")
        clean_parent_id = safe_id(parent_handoff_id, "parent_handoff_id")
        clean_successor_id = safe_id(
            successor_handoff_id, "successor_handoff_id"
        )
        clean_consumer_id = safe_id(
            consumer_work_order_id, "consumer_work_order_id"
        )
        directory = self.project_dir(namespace, project_id)
        root_path = directory / "handoffs" / f"{clean_root_id}.json"
        parent_path = directory / "handoffs" / f"{clean_parent_id}.json"
        successor_path = directory / "handoffs" / f"{clean_successor_id}.json"
        hub_path = directory / "hub.json"

        with self.project_write_lock(namespace, project_id):
            if not root_path.is_file() or not parent_path.is_file():
                raise ValueError(
                    "reaudit_lineage_invalid: root or parent handoff is missing"
                )
            root = self.read_json(root_path)
            parent = self.read_json(parent_path)
            self._verify_record_integrity(root, record_id=clean_root_id)
            self._verify_record_integrity(parent, record_id=clean_parent_id)
            self._verify_project_record_primary_id(
                "handoffs", root, record_id=clean_root_id
            )
            self._verify_project_record_primary_id(
                "handoffs", parent, record_id=clean_parent_id
            )
            if not self.project_record_is_signed(
                root
            ) or not self.project_record_is_signed(parent):
                raise ValueError(
                    "reaudit_lineage_invalid: root or parent handoff is unsigned"
                )
            if (
                root.get("project_id") != project_id
                or parent.get("project_id") != project_id
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: root or parent crosses project authority"
                )
            hub_before = self.read_json(hub_path)
            audit_orders = [
                row
                for row in hub_before.get("work_orders", [])
                if isinstance(row, dict)
                and row.get("work_order_id") == clean_consumer_id
            ]
            if len(audit_orders) != 1:
                raise ValueError(
                    "reaudit_lineage_invalid: audit work-order authority is missing"
                )
            audit_order = audit_orders[0]
            if (
                root.get("reaudit_kind")
                or root.get("status") != "consumed"
                or root.get("consumed_by_work_order_id")
                != clean_consumer_id
                or parent.get("status") != "consumed"
                or parent.get("consumed_by_work_order_id")
                != clean_consumer_id
                or not parent.get("audit_certificate_id")
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: parent is not a completed audit route"
                )
            if clean_parent_id == clean_root_id:
                expected_sequence = 1
            else:
                parent_lifecycle_error = (
                    self._reaudit_successor_lifecycle_error(parent)
                )
                if (
                    parent_lifecycle_error
                    or parent.get("reaudit_root_handoff_id")
                    != clean_root_id
                    or parent.get("reaudit_kind")
                    != "independent_audit_successor"
                ):
                    raise ValueError(
                        "reaudit_lineage_invalid: parent successor authority is invalid"
                    )
                expected_sequence = int(
                    parent.get("reaudit_sequence") or 0
                ) + 1
            parent_certificate_id = safe_id(
                str(parent["audit_certificate_id"]),
                "parent_audit_certificate_id",
            )
            parent_certificate_path = (
                directory
                / "certificates"
                / f"{parent_certificate_id}.json"
            )
            if not parent_certificate_path.is_file():
                raise ValueError(
                    "reaudit_lineage_invalid: parent certificate is missing"
                )
            parent_certificate = self.read_json(parent_certificate_path)
            if not isinstance(parent_certificate, dict):
                raise ValueError(
                    "reaudit_lineage_invalid: parent certificate is malformed"
                )
            self._verify_record_integrity(
                parent_certificate, record_id=parent_certificate_id
            )
            self._verify_project_record_primary_id(
                "certificates",
                parent_certificate,
                record_id=parent_certificate_id,
            )
            ledger = self.read_json(directory / "ledger.json")
            events = ledger.get("events") if isinstance(ledger, dict) else None
            parent_event_id = str(
                parent_certificate.get("event_id") or ""
            )
            matching_parent_events = [
                event
                for event in events or []
                if isinstance(event, dict)
                and str(event.get("event_id") or "") == parent_event_id
            ]
            if (
                not self.project_record_is_signed(parent_certificate)
                or parent_certificate.get("handoff_id") != clean_parent_id
                or parent_certificate.get("work_order_id")
                != clean_consumer_id
                or len(matching_parent_events) != 1
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: parent certificate authority is incomplete"
                )
            parent_event_sha256 = sha256_bytes(
                canonical_json_bytes(matching_parent_events[0])
            )
            if (
                "event_sha256" in parent_certificate
                and parent_certificate.get("event_sha256")
                != parent_event_sha256
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: parent certificate event binding differs"
                )
            if (
                successor.get("previous_audit_certificate_id")
                != parent_certificate_id
                or successor.get("previous_audit_certificate_hmac")
                != parent_certificate.get("integrity_hmac")
                or successor.get("previous_audit_event_sha256")
                != parent_event_sha256
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: successor parent authority bridge differs"
                )

            expected_lineage_sha256 = sha256_bytes(
                canonical_json_bytes(
                    self._reaudit_successor_lineage_payload(
                        root=root,
                        parent=parent,
                        successor=successor,
                    )
                )
            )
            if (
                successor.get("handoff_id") != clean_successor_id
                or successor.get("reaudit_kind")
                != "independent_audit_successor"
                or successor.get("reaudit_root_handoff_id")
                != clean_root_id
                or successor.get("reaudit_parent_handoff_id")
                != clean_parent_id
                or successor.get("reaudit_sequence") != expected_sequence
                or successor.get("claim_authority_work_order_id")
                != clean_consumer_id
                or successor.get("reaudit_audit_window_binding_id")
                != audit_order.get("server_window_binding_id")
                or successor.get("reaudit_audit_actor_binding_id")
                != audit_order.get("server_actor_binding_id")
                or successor.get("reaudit_audit_session_fingerprint")
                != audit_order.get("transport_session_fingerprint")
                or successor.get("server_minted_lineage") is not True
                or successor.get("production_stage_trace_id")
                != root.get("stage_trace_id")
                or successor.get("stage_trace_id")
                != successor.get("reaudit_route_trace_id")
                or self._reaudit_successor_immutable_identity(successor)
                != self._reaudit_successor_immutable_identity(root)
                or successor.get("reaudit_lineage_sha256")
                != expected_lineage_sha256
                or (
                    "record_id" in successor
                    and successor.get("record_id") != clean_successor_id
                )
                or (
                    "project_id" in successor
                    and successor.get("project_id") != project_id
                )
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: successor changed root, parent, "
                    "sequence, audit authority, immutable identity, or lineage"
                )

            route_trace_id = str(
                successor.get("reaudit_route_trace_id") or ""
            )
            event_trace_id = str(
                successor.get("reaudit_event_trace_id") or ""
            )
            if (
                not route_trace_id
                or not event_trace_id
                or route_trace_id == event_trace_id
            ):
                raise ValueError(
                    "reaudit_trace_conflict: route and audit-event traces "
                    "must be distinct and globally fresh"
                )
            safe_id(route_trace_id, "reaudit_route_trace_id")
            safe_id(event_trace_id, "reaudit_event_trace_id")
            finalization_error = self._reaudit_successor_lifecycle_error(
                successor
            )
            if finalization_error:
                raise ValueError(finalization_error)

            sealed = copy.deepcopy(successor)
            sealed["record_id"] = clean_successor_id
            sealed["project_id"] = project_id
            sealed["integrity_hmac"] = self._record_integrity_hmac(sealed)
            expected_projection = self._hub_record_projection(sealed)
            existing: dict[str, Any] | None = None
            if successor_path.is_file():
                existing = self.read_json(successor_path)
                self._verify_record_integrity(
                    existing, record_id=clean_successor_id
                )
                self._verify_project_record_primary_id(
                    "handoffs", existing, record_id=clean_successor_id
                )
                if (
                    not self.project_record_is_signed(existing)
                    or existing.get("record_id") != clean_successor_id
                    or existing.get("project_id") != project_id
                    or self._hub_record_projection(existing)
                    != expected_projection
                ):
                    raise ValueError("reaudit_successor_idempotency_conflict")

            existing_is_exact = existing is not None
            candidate_trace_ids = {route_trace_id, event_trace_id}

            def record_trace_ids(value: Any) -> set[str]:
                if not isinstance(value, dict):
                    return set()
                return {
                    str(item)
                    for key, item in value.items()
                    if isinstance(item, str)
                    and item
                    and (
                        key in {"trace_id", "telemetry_id"}
                        or key.endswith("_trace_id")
                    )
                }

            def reject_reused_trace(trace_ids: set[str]) -> None:
                if candidate_trace_ids & trace_ids:
                    raise ValueError(
                        "reaudit_trace_conflict: route and audit-event traces "
                        "must be distinct and globally fresh"
                    )

            def is_exact_downstream_trace_reference(
                value: Any,
            ) -> bool:
                return bool(
                    existing_is_exact
                    and isinstance(value, dict)
                    and value.get("handoff_type") == "audit_to_repair"
                    and value.get("from_window_role")
                    == "independent_audit"
                    and value.get("to_window_role") == "repair"
                    and value.get("source_audit_handoff_id")
                    == clean_successor_id
                    and value.get("source_audit_route_trace_id")
                    == route_trace_id
                    and value.get("source_audit_event_trace_id")
                    == event_trace_id
                    and value.get("audit_cycle_id")
                    == successor.get("audit_cycle_id")
                    and value.get("logical_artifact_id")
                    == successor.get("logical_artifact_id")
                    and value.get("canonical_sha256")
                    == successor.get("canonical_sha256")
                    and value.get("fingerprints")
                    == successor.get("fingerprints")
                )

            def is_exact_owned_telemetry(value: Any) -> bool:
                if not existing_is_exact or not isinstance(value, dict):
                    return False
                trace_id = str(value.get("trace_id") or "")
                if (
                    value.get("telemetry_id") != trace_id
                    or value.get("artifact_id")
                    != successor.get("storyboard_artifact_id")
                    or value.get("input_fingerprints")
                    != successor.get("fingerprints")
                    or value.get("window_role") != "independent_audit"
                    or value.get("task_type") != "independent_audit"
                ):
                    return False
                return bool(
                    (
                        trace_id == route_trace_id
                        and value.get("operation")
                        == "audit_storyboard_reaudit_route"
                    )
                    or (
                        trace_id == event_trace_id
                        and value.get("operation") == "audit_storyboard"
                    )
                )

            for path in sorted((directory / "handoffs").glob("*.json")):
                if path == successor_path:
                    continue
                candidate = self.read_json(path)
                if not isinstance(candidate, dict):
                    raise ValueError(
                        "reaudit_lineage_invalid: malformed handoff record"
                    )
                candidate_id = path.stem
                self._verify_record_integrity(
                    candidate, record_id=candidate_id
                )
                self._verify_project_record_primary_id(
                    "handoffs", candidate, record_id=candidate_id
                )
                if (
                    candidate.get("project_id") != project_id
                    or not self.project_record_is_signed(candidate)
                ):
                    raise ValueError(
                        "reaudit_lineage_invalid: unsigned or cross-project handoff record"
                    )
                if (
                    candidate.get("reaudit_parent_handoff_id")
                    == clean_parent_id
                ):
                    raise ValueError(
                        "reaudit_successor_conflict: parent already has a successor"
                    )
                candidate_record_trace_ids = record_trace_ids(candidate)
                if (
                    candidate_trace_ids & candidate_record_trace_ids
                    and not is_exact_downstream_trace_reference(candidate)
                ):
                    reject_reused_trace(candidate_record_trace_ids)

            for row in hub_before.get("handoffs", []):
                if (
                    isinstance(row, dict)
                    and row.get("handoff_id") != clean_successor_id
                ):
                    hub_record_trace_ids = record_trace_ids(row)
                    if (
                        candidate_trace_ids & hub_record_trace_ids
                        and not is_exact_downstream_trace_reference(row)
                    ):
                        reject_reused_trace(hub_record_trace_ids)

            for path in sorted((directory / "telemetry").glob("*.json")):
                trace = self.read_json(path)
                if not isinstance(trace, dict):
                    raise ValueError(
                        "reaudit_lineage_invalid: malformed performance trace"
                    )
                trace_id = path.stem
                self._verify_record_integrity(trace, record_id=trace_id)
                self._verify_project_record_primary_id(
                    "telemetry", trace, record_id=trace_id
                )
                if (
                    trace.get("project_id") != project_id
                    or not self.project_record_is_signed(trace)
                ):
                    raise ValueError(
                        "reaudit_lineage_invalid: unsigned or cross-project "
                        "performance trace"
                    )
                trace_ids = record_trace_ids(trace)
                reused = candidate_trace_ids & trace_ids
                if reused and not is_exact_owned_telemetry(trace):
                    reject_reused_trace(trace_ids)

            for trace in (
                *hub_before.get("performance_traces", []),
                *ledger.get("performance_traces", []),
            ):
                trace_ids = record_trace_ids(trace)
                reused = candidate_trace_ids & trace_ids
                if reused and not is_exact_owned_telemetry(trace):
                    reject_reused_trace(trace_ids)

            for event in events or []:
                event_trace_ids = record_trace_ids(event)
                reused = candidate_trace_ids & event_trace_ids
                if not reused:
                    continue
                exact_existing_event = bool(
                    existing_is_exact
                    and reused == {event_trace_id}
                    and event.get("handoff_id") == clean_successor_id
                )
                if not exact_existing_event:
                    reject_reused_trace(event_trace_ids)

            if existing is not None:
                hub_matches = [
                    index
                    for index, row in enumerate(
                        hub_before.get("handoffs", [])
                    )
                    if row.get("handoff_id") == clean_successor_id
                ]
                if not hub_matches:
                    # Recover a crash after the signed prepared route was
                    # durably written but before its Hub projection.  The
                    # exact signed body is the only authority used to repair.
                    hub_after = copy.deepcopy(hub_before)
                    hub_after.setdefault("handoffs", []).append(
                        self._hub_record_projection(existing)
                    )
                    self.atomic_write_json(hub_path, hub_after)
                    return existing
                prerelease_sha256 = str(
                    existing.get("reaudit_prerelease_projection_sha256")
                    or ""
                )
                if (
                    len(hub_matches) == 1
                    and existing.get("status") == "prepared"
                    and prerelease_sha256
                    and self.project_state_sha256(
                        hub_before["handoffs"][hub_matches[0]]
                    )
                    == prerelease_sha256
                ):
                    # Recover the exact release half-write: signed storage
                    # durably removed the claim while Hub still contains the
                    # server-hashed consuming projection that preceded it.
                    # No caller-supplied Hub field is trusted as authority.
                    hub_after = copy.deepcopy(hub_before)
                    hub_after["handoffs"][hub_matches[0]] = (
                        self._hub_record_projection(existing)
                    )
                    self.atomic_write_json(hub_path, hub_after)
                    return existing
                if (
                    len(hub_matches) != 1
                    or hub_before["handoffs"][hub_matches[0]]
                    != self._hub_record_projection(existing)
                ):
                    raise ValueError("reaudit_successor_projection_mismatch")
                return existing

            if any(
                row.get("handoff_id") == clean_successor_id
                for row in hub_before.get("handoffs", [])
            ):
                raise ValueError("reaudit_successor_projection_conflict")
            hub_after = copy.deepcopy(hub_before)
            hub_after.setdefault("handoffs", []).append(expected_projection)
            try:
                self.atomic_write_json(successor_path, sealed)
                self.atomic_write_json(hub_path, hub_after)
            except BaseException:
                # The successor did not exist before this transaction.  An
                # ordinary two-file failure is compensated to exact zero
                # publication side effects.  A hard crash remains fail-closed
                # because the successor-only claim path verifies the Hub
                # projection before making the route consumable.
                with contextlib.suppress(OSError):
                    self.atomic_write_json(hub_path, hub_before)
                with contextlib.suppress(FileNotFoundError, OSError):
                    successor_path.unlink()
                raise
            return sealed

    def _assert_reaudit_lineage_authority_manifest(
        self,
        directory: Path,
        *,
        ordered_routes: list[dict[str, Any]],
        lineage_authority_manifest: list[dict[str, Any]],
        error_message: str,
    ) -> None:
        """Fence every completed HND/CERT/event authority under one lock."""

        if not isinstance(lineage_authority_manifest, list):
            raise ValueError(error_message)
        ledger = self.read_json(directory / "ledger.json")
        if not isinstance(ledger, dict):
            raise ValueError(error_message)
        events = ledger.get("events")
        if not isinstance(events, list):
            raise ValueError(error_message)
        expected_manifest: list[dict[str, Any]] = []
        for route_index, route in enumerate(ordered_routes):
            if route.get("status") != "consumed":
                continue
            handoff_id = str(route.get("handoff_id") or "")
            certificate_id = str(
                route.get("audit_certificate_id") or ""
            )
            certificate_path = (
                directory / "certificates" / f"{certificate_id}.json"
            )
            if not certificate_path.is_file():
                raise ValueError(error_message)
            certificate = self.read_json(certificate_path)
            if not isinstance(certificate, dict):
                raise ValueError(error_message)
            self._verify_record_integrity(
                certificate, record_id=certificate_id
            )
            self._verify_project_record_primary_id(
                "certificates", certificate, record_id=certificate_id
            )
            if (
                not self.project_record_is_signed(certificate)
                or certificate.get("handoff_id") != handoff_id
            ):
                raise ValueError(error_message)
            event_id = str(certificate.get("event_id") or "")
            matching_events = [
                event
                for event in events
                if isinstance(event, dict)
                and str(event.get("event_id") or "") == event_id
            ]
            if len(matching_events) != 1:
                raise ValueError(error_message)
            event = matching_events[0]
            event_sha256 = sha256_bytes(canonical_json_bytes(event))
            child = (
                ordered_routes[route_index + 1]
                if route_index < len(ordered_routes) - 1
                else None
            )
            if child is not None and (
                child.get("previous_audit_certificate_id")
                != certificate_id
                or child.get("previous_audit_certificate_hmac")
                != certificate.get("integrity_hmac")
                or child.get("previous_audit_event_sha256")
                != event_sha256
            ):
                raise ValueError(error_message)
            certificate_has_event_sha = "event_sha256" in certificate
            if (
                certificate_has_event_sha
                and certificate.get("event_sha256") != event_sha256
            ) or (not certificate_has_event_sha and child is None):
                raise ValueError(error_message)
            expected_manifest.append(
                {
                    "handoff_id": handoff_id,
                    "handoff_hmac": str(
                        route.get("integrity_hmac") or ""
                    ),
                    "certificate_id": certificate_id,
                    "certificate_hmac": str(
                        certificate.get("integrity_hmac") or ""
                    ),
                    "event_id": event_id,
                    "event_sha256": event_sha256,
                    "legacy_event_sha_missing": (
                        not certificate_has_event_sha
                    ),
                }
            )
        if canonical_json_bytes(expected_manifest) != canonical_json_bytes(
            lineage_authority_manifest
        ):
            raise ValueError(error_message)

    def assert_latest_reaudit_receipt(
        self,
        namespace: str,
        project_id: str,
        *,
        root_handoff_id: str,
        root_handoff_hmac: str,
        latest_handoff_id: str,
        latest_handoff_hmac: str,
        certificate_id: str,
        certificate_hmac: str,
        lineage_authority_manifest: list[dict[str, Any]],
    ) -> None:
        """Linearize a completed receipt against the signed route tail."""

        clean_root_id = safe_id(root_handoff_id, "root_handoff_id")
        clean_latest_id = safe_id(latest_handoff_id, "latest_handoff_id")
        clean_certificate_id = safe_id(certificate_id, "certificate_id")
        if any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                root_handoff_hmac,
                latest_handoff_hmac,
                certificate_hmac,
            )
        ):
            raise ValueError(
                "reaudit_replay_superseded: completed receipt signature is invalid"
            )
        directory = self.project_dir(namespace, project_id)
        hub_path = directory / "hub.json"
        with self.lock(f"{namespace}-{project_id}"):
            signed_handoffs: dict[str, dict[str, Any]] = {}
            for path in sorted((directory / "handoffs").glob("*.json")):
                row = self.read_json(path)
                if not isinstance(row, dict):
                    raise ValueError(
                        "reaudit_replay_superseded: malformed signed handoff"
                    )
                row_id = str(row.get("record_id") or "")
                if row_id != path.stem or row.get("handoff_id") != row_id:
                    raise ValueError(
                        "reaudit_replay_superseded: handoff identity changed"
                    )
                self._verify_record_integrity(row, record_id=row_id)
                if (
                    row.get("project_id") != project_id
                    or not self.project_record_is_signed(row)
                    or row_id in signed_handoffs
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: handoff authority changed"
                    )
                signed_handoffs[row_id] = row
            root = signed_handoffs.get(clean_root_id)
            expected_latest = signed_handoffs.get(clean_latest_id)
            if (
                not isinstance(root, dict)
                or root.get("reaudit_kind")
                or not isinstance(expected_latest, dict)
                or not hmac.compare_digest(
                    str(root.get("integrity_hmac") or ""),
                    root_handoff_hmac,
                )
            ):
                raise ValueError(
                    "reaudit_replay_superseded: completed route is missing"
                )
            successors = {
                row_id: row
                for row_id, row in signed_handoffs.items()
                if row.get("reaudit_kind")
                == "independent_audit_successor"
                and row.get("reaudit_root_handoff_id") == clean_root_id
            }
            lineage_ids = {clean_root_id, *successors}
            for row in signed_handoffs.values():
                if (
                    (
                        row.get("reaudit_root_handoff_id") == clean_root_id
                        or row.get("reaudit_parent_handoff_id")
                        in lineage_ids
                    )
                    and row.get("reaudit_kind")
                    != "independent_audit_successor"
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: malformed signed lineage record"
                    )
                if (
                    row.get("reaudit_kind")
                    == "independent_audit_successor"
                    and row.get("reaudit_parent_handoff_id")
                    in lineage_ids
                    and row.get("reaudit_root_handoff_id")
                    != clean_root_id
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: successor crosses root lineages"
                    )
            root_immutable_identity = {
                key: copy.deepcopy(root.get(key))
                for key in REAUDIT_IMMUTABLE_IDENTITY_KEYS
                if key in root
            }
            children: dict[str, list[dict[str, Any]]] = {}
            for row in successors.values():
                parent_id = str(
                    row.get("reaudit_parent_handoff_id") or ""
                )
                parent = signed_handoffs.get(parent_id)
                candidate_immutable_identity = {
                    key: copy.deepcopy(row.get(key))
                    for key in REAUDIT_IMMUTABLE_IDENTITY_KEYS
                    if key in row
                }
                lineage_payload = {
                    "spec": "independent-reaudit-successor-v1",
                    "root_handoff_id": clean_root_id,
                    "parent_handoff_id": parent_id,
                    "successor_handoff_id": str(
                        row.get("handoff_id") or ""
                    ),
                    "reaudit_sequence": int(
                        row.get("reaudit_sequence") or 0
                    ),
                    "audit_work_order_id": str(
                        row.get("claim_authority_work_order_id") or ""
                    ),
                    "previous_audit_certificate_id": str(
                        row.get("previous_audit_certificate_id") or ""
                    ),
                    "previous_audit_certificate_hmac": str(
                        row.get("previous_audit_certificate_hmac") or ""
                    ),
                    "previous_audit_event_sha256": str(
                        row.get("previous_audit_event_sha256") or ""
                    ),
                    "production_stage_trace_id": str(
                        row.get("production_stage_trace_id") or ""
                    ),
                    "route_trace_id": str(
                        row.get("reaudit_route_trace_id") or ""
                    ),
                    "audit_event_trace_id": str(
                        row.get("reaudit_event_trace_id") or ""
                    ),
                    "audit_window_binding_id": str(
                        row.get("reaudit_audit_window_binding_id") or ""
                    ),
                    "audit_actor_binding_id": str(
                        row.get("reaudit_audit_actor_binding_id") or ""
                    ),
                    "audit_session_fingerprint": str(
                        row.get("reaudit_audit_session_fingerprint") or ""
                    ),
                    "immutable_identity_sha256": sha256_bytes(
                        canonical_json_bytes(root_immutable_identity)
                    ),
                }
                if (
                    row.get("server_minted_lineage") is not True
                    or parent_id not in lineage_ids
                    or not isinstance(parent, dict)
                    or row.get("status") != "consumed"
                    or not row.get("audit_certificate_id")
                    or row.get("consumed_by_work_order_id")
                    != row.get("claim_authority_work_order_id")
                    or candidate_immutable_identity
                    != root_immutable_identity
                    or row.get("previous_audit_certificate_id")
                    != parent.get("audit_certificate_id")
                    or row.get("audit_cycle_id")
                    != root.get("audit_cycle_id")
                    or row.get("work_order_id")
                    != root.get("work_order_id")
                    or row.get("production_stage_trace_id")
                    != root.get("stage_trace_id")
                    or row.get("reaudit_lineage_sha256")
                    != sha256_bytes(canonical_json_bytes(lineage_payload))
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: successor lineage binding changed"
                    )
                children.setdefault(parent_id, []).append(row)
            if any(len(rows) != 1 for rows in children.values()):
                raise ValueError(
                    "reaudit_replay_superseded: completed route is forked"
                )
            cursor = root
            cursor_id = clean_root_id
            ordered_routes = [root]
            visited: set[str] = set()
            expected_sequence = 1
            while children.get(cursor_id):
                cursor = children[cursor_id][0]
                cursor_id = str(cursor.get("handoff_id") or "")
                if (
                    cursor_id in visited
                    or cursor_id not in successors
                    or cursor.get("reaudit_sequence") != expected_sequence
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: completed route is broken"
                    )
                visited.add(cursor_id)
                ordered_routes.append(cursor)
                expected_sequence += 1
            if (
                len(visited) != len(successors)
                or cursor_id != clean_latest_id
                or cursor.get("status") != "consumed"
                or cursor.get("audit_certificate_id")
                != clean_certificate_id
                or not hmac.compare_digest(
                    str(cursor.get("integrity_hmac") or ""),
                    latest_handoff_hmac,
                )
            ):
                raise ValueError(
                    "reaudit_replay_superseded: a newer completed audit is authoritative"
                )
            self._assert_reaudit_lineage_authority_manifest(
                directory,
                ordered_routes=ordered_routes,
                lineage_authority_manifest=lineage_authority_manifest,
                error_message=(
                    "reaudit_replay_superseded: completed lineage authority changed"
                ),
            )
            certificate_path = (
                directory
                / "certificates"
                / f"{clean_certificate_id}.json"
            )
            if not certificate_path.is_file():
                raise ValueError(
                    "reaudit_replay_superseded: completed certificate is missing"
                )
            certificate = self.read_json(certificate_path)
            self._verify_record_integrity(
                certificate, record_id=clean_certificate_id
            )
            if (
                not self.project_record_is_signed(certificate)
                or certificate.get("record_id") != clean_certificate_id
                or certificate.get("certificate_id")
                != clean_certificate_id
                or certificate.get("handoff_id") != clean_latest_id
                or not hmac.compare_digest(
                    str(certificate.get("integrity_hmac") or ""),
                    certificate_hmac,
                )
            ):
                raise ValueError(
                    "reaudit_replay_superseded: completed certificate changed"
                )
            if successors:
                hub = self.read_json(hub_path)
                hub_successors = [
                    row
                    for row in hub.get("handoffs", [])
                    if isinstance(row, dict)
                    and row.get("reaudit_kind")
                    == "independent_audit_successor"
                    and row.get("reaudit_root_handoff_id") == clean_root_id
                ]
                if (
                    any(
                        isinstance(row, dict)
                        and (
                            row.get("reaudit_root_handoff_id")
                            == clean_root_id
                            or row.get("reaudit_parent_handoff_id")
                            in lineage_ids
                        )
                        and row.get("reaudit_kind")
                        != "independent_audit_successor"
                        for row in hub.get("handoffs", [])
                    )
                    or len(
                        {
                            str(row.get("handoff_id") or "")
                            for row in hub_successors
                        }
                    )
                    != len(hub_successors)
                    or {
                        str(row.get("handoff_id") or "")
                        for row in hub_successors
                    }
                    != set(successors)
                    or any(
                        row
                        != self._hub_record_projection(
                            successors[str(row.get("handoff_id") or "")]
                        )
                        for row in hub_successors
                    )
                ):
                    raise ValueError(
                        "reaudit_replay_superseded: Hub lineage projection changed"
                    )

    def commit_repair_package_route(
        self,
        namespace: str,
        project_id: str,
        *,
        repair_package_id: str,
        package: dict[str, Any],
        handoff_id: str,
        handoff: dict[str, Any],
        expected_root_handoff_id: str,
        expected_root_handoff_hmac: str,
        expected_latest_handoff_id: str,
        expected_latest_handoff_hmac: str,
        expected_latest_certificate_id: str,
        expected_latest_certificate_hmac: str,
        lineage_authority_manifest: list[dict[str, Any]],
        expected_claim_token: str | None,
        expected_claim_generation: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Publish an immutable repair package and route as one recoverable unit."""

        clean_package_id = safe_id(
            repair_package_id, "repair_package_id"
        )
        clean_handoff_id = safe_id(handoff_id, "handoff_id")
        directory = self.project_dir(namespace, project_id)
        package_path = (
            directory / "repair_packages" / f"{clean_package_id}.json"
        )
        handoff_path = directory / "handoffs" / f"{clean_handoff_id}.json"
        hub_path = directory / "hub.json"
        sealed_package = copy.deepcopy(package)
        sealed_package.update(
            {"record_id": clean_package_id, "project_id": project_id}
        )
        sealed_package["integrity_hmac"] = self._record_integrity_hmac(
            sealed_package
        )
        sealed_handoff = copy.deepcopy(handoff)
        sealed_handoff.update(
            {"record_id": clean_handoff_id, "project_id": project_id}
        )
        sealed_handoff["integrity_hmac"] = self._record_integrity_hmac(
            sealed_handoff
        )

        with self.project_write_lock(namespace, project_id):
            hub_before = self.read_json(hub_path)

            clean_root_id = safe_id(
                str(expected_root_handoff_id),
                "expected_root_handoff_id",
            )
            clean_latest_id = safe_id(
                str(expected_latest_handoff_id),
                "expected_latest_handoff_id",
            )
            clean_certificate_id = safe_id(
                str(expected_latest_certificate_id),
                "expected_latest_certificate_id",
            )
            expected_hmacs = (
                expected_root_handoff_hmac,
                expected_latest_handoff_hmac,
                expected_latest_certificate_hmac,
            )
            if any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in expected_hmacs
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: expected authority signature is invalid"
                )

            # Read and authenticate the complete signed handoff set before
            # resolving the root/successor chain.  Performing lineage checks
            # inside this scan would make the result depend on file ordering.
            signed_handoffs: dict[str, dict[str, Any]] = {}
            for candidate_path in sorted(
                (directory / "handoffs").glob("*.json")
            ):
                candidate = self.read_json(candidate_path)
                if not isinstance(candidate, dict):
                    raise ValueError(
                        "repair_route_freshness_invalid: malformed signed handoff"
                    )
                candidate_id = str(candidate.get("record_id") or "")
                if (
                    candidate_id != candidate_path.stem
                    or candidate.get("handoff_id") != candidate_id
                    or candidate_id in signed_handoffs
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: handoff path, record id, or handoff id differs"
                    )
                self._verify_record_integrity(
                    candidate, record_id=candidate_id
                )
                if (
                    candidate.get("project_id") != project_id
                    or not self.project_record_is_signed(candidate)
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: unsigned or cross-project handoff"
                    )
                signed_handoffs[candidate_id] = candidate

            root_authority = signed_handoffs.get(clean_root_id)
            latest_authority = signed_handoffs.get(clean_latest_id)
            if (
                not isinstance(root_authority, dict)
                or not isinstance(latest_authority, dict)
                or root_authority.get("reaudit_kind")
                or not hmac.compare_digest(
                    str(root_authority.get("integrity_hmac") or ""),
                    expected_root_handoff_hmac,
                )
                or not hmac.compare_digest(
                    str(latest_authority.get("integrity_hmac") or ""),
                    expected_latest_handoff_hmac,
                )
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: signed root or latest authority is missing"
                )
            successors = {
                candidate_id: candidate
                for candidate_id, candidate in signed_handoffs.items()
                if candidate.get("reaudit_kind")
                == "independent_audit_successor"
                and candidate.get("reaudit_root_handoff_id")
                == clean_root_id
            }
            lineage_ids = {clean_root_id, *successors}
            for candidate in signed_handoffs.values():
                if (
                    (
                        candidate.get("reaudit_root_handoff_id")
                        == clean_root_id
                        or candidate.get("reaudit_parent_handoff_id")
                        in lineage_ids
                    )
                    and candidate.get("reaudit_kind")
                    != "independent_audit_successor"
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: malformed signed lineage record"
                    )
                if (
                    candidate.get("reaudit_kind")
                    == "independent_audit_successor"
                    and candidate.get("reaudit_parent_handoff_id")
                    in lineage_ids
                    and candidate.get("reaudit_root_handoff_id")
                    != clean_root_id
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: successor crosses root lineages"
                    )
            root_immutable_identity = {
                key: copy.deepcopy(root_authority.get(key))
                for key in REAUDIT_IMMUTABLE_IDENTITY_KEYS
                if key in root_authority
            }
            children: dict[str, list[dict[str, Any]]] = {}
            for candidate in successors.values():
                parent_id = str(
                    candidate.get("reaudit_parent_handoff_id") or ""
                )
                parent = signed_handoffs.get(parent_id)
                candidate_immutable_identity = {
                    key: copy.deepcopy(candidate.get(key))
                    for key in REAUDIT_IMMUTABLE_IDENTITY_KEYS
                    if key in candidate
                }
                lineage_payload = {
                    "spec": "independent-reaudit-successor-v1",
                    "root_handoff_id": clean_root_id,
                    "parent_handoff_id": parent_id,
                    "successor_handoff_id": str(
                        candidate.get("handoff_id") or ""
                    ),
                    "reaudit_sequence": int(
                        candidate.get("reaudit_sequence") or 0
                    ),
                    "audit_work_order_id": str(
                        candidate.get("claim_authority_work_order_id")
                        or ""
                    ),
                    "previous_audit_certificate_id": str(
                        candidate.get("previous_audit_certificate_id")
                        or ""
                    ),
                    "previous_audit_certificate_hmac": str(
                        candidate.get(
                            "previous_audit_certificate_hmac"
                        )
                        or ""
                    ),
                    "previous_audit_event_sha256": str(
                        candidate.get("previous_audit_event_sha256")
                        or ""
                    ),
                    "production_stage_trace_id": str(
                        candidate.get("production_stage_trace_id") or ""
                    ),
                    "route_trace_id": str(
                        candidate.get("reaudit_route_trace_id") or ""
                    ),
                    "audit_event_trace_id": str(
                        candidate.get("reaudit_event_trace_id") or ""
                    ),
                    "audit_window_binding_id": str(
                        candidate.get("reaudit_audit_window_binding_id")
                        or ""
                    ),
                    "audit_actor_binding_id": str(
                        candidate.get("reaudit_audit_actor_binding_id")
                        or ""
                    ),
                    "audit_session_fingerprint": str(
                        candidate.get(
                            "reaudit_audit_session_fingerprint"
                        )
                        or ""
                    ),
                    "immutable_identity_sha256": sha256_bytes(
                        canonical_json_bytes(root_immutable_identity)
                    ),
                }
                if (
                    candidate.get("server_minted_lineage") is not True
                    or parent_id not in lineage_ids
                    or not isinstance(parent, dict)
                    or candidate_immutable_identity
                    != root_immutable_identity
                    or candidate.get("previous_audit_certificate_id")
                    != parent.get("audit_certificate_id")
                    or candidate.get("audit_cycle_id")
                    != root_authority.get("audit_cycle_id")
                    or candidate.get("work_order_id")
                    != root_authority.get("work_order_id")
                    or candidate.get("production_stage_trace_id")
                    != root_authority.get("stage_trace_id")
                    or candidate.get("reaudit_lineage_sha256")
                    != sha256_bytes(canonical_json_bytes(lineage_payload))
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: successor lineage binding differs"
                    )
                children.setdefault(parent_id, []).append(candidate)
            if any(len(rows) != 1 for rows in children.values()):
                raise ValueError(
                    "repair_route_freshness_invalid: re-audit lineage is forked"
                )
            cursor = root_authority
            cursor_id = clean_root_id
            ordered_routes = [root_authority]
            expected_sequence = 1
            visited: set[str] = set()
            while children.get(cursor_id):
                child = children[cursor_id][0]
                child_id = str(child.get("handoff_id") or "")
                if (
                    child_id not in successors
                    or child_id in visited
                    or child.get("reaudit_sequence") != expected_sequence
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: re-audit lineage is broken"
                    )
                visited.add(child_id)
                cursor = child
                cursor_id = child_id
                ordered_routes.append(child)
                expected_sequence += 1
            if len(visited) != len(successors):
                raise ValueError(
                    "repair_route_freshness_invalid: re-audit lineage has an unreachable successor"
                )
            for settled_id in visited - {cursor_id}:
                settled = signed_handoffs[settled_id]
                if (
                    settled.get("status") != "consumed"
                    or not settled.get("audit_certificate_id")
                    or settled.get("consumed_by_work_order_id")
                    != settled.get("claim_authority_work_order_id")
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: every non-tail successor must be a completed audit authority"
                    )
            if cursor_id != clean_latest_id:
                raise ValueError(
                    "repair_route_superseded: a newer audit route is authoritative"
                )

            if cursor.get("status") == "consuming":
                if (
                    not expected_claim_token
                    or cursor.get("claim_token") != expected_claim_token
                    or cursor.get("claim_generation")
                    != expected_claim_generation
                ):
                    raise ValueError(
                        "repair_route_freshness_invalid: consuming audit claim changed"
                    )
                authority_work_order_id = cursor.get(
                    "claimed_by_work_order_id"
                )
            elif (
                cursor.get("status") != "consumed"
                or cursor.get("audit_certificate_id")
                != clean_certificate_id
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: latest audit is not completed by the expected certificate"
                )
            else:
                authority_work_order_id = cursor.get(
                    "consumed_by_work_order_id"
                )

            certificate_path = (
                directory
                / "certificates"
                / f"{clean_certificate_id}.json"
            )
            if not certificate_path.is_file():
                raise ValueError(
                    "repair_route_freshness_invalid: latest certificate is missing"
                )
            latest_certificate = self.read_json(certificate_path)
            self._verify_record_integrity(
                latest_certificate, record_id=clean_certificate_id
            )
            actual_certificate_hmac = str(
                latest_certificate.get("integrity_hmac") or ""
            )
            report = latest_certificate.get("report")
            report_issues = (
                report.get("issues") if isinstance(report, dict) else None
            )
            expected_production_trace_id = str(
                cursor.get("reaudit_route_trace_id")
                or cursor.get("stage_trace_id")
                or ""
            )
            expected_production_stage_trace_id = str(
                cursor.get("production_stage_trace_id")
                or cursor.get("stage_trace_id")
                or ""
            )
            if (
                not self.project_record_is_signed(latest_certificate)
                or not hmac.compare_digest(
                    actual_certificate_hmac,
                    expected_latest_certificate_hmac,
                )
                or latest_certificate.get("record_id")
                != clean_certificate_id
                or latest_certificate.get("certificate_id")
                != clean_certificate_id
                or latest_certificate.get("handoff_id") != clean_latest_id
                or latest_certificate.get("work_order_id")
                != authority_work_order_id
                or latest_certificate.get("production_work_order_id")
                != cursor.get("work_order_id")
                or latest_certificate.get("production_trace_id")
                != expected_production_trace_id
                or latest_certificate.get("production_stage_trace_id")
                != expected_production_stage_trace_id
                or latest_certificate.get("result")
                not in {"fail_current", "blocked"}
                or not isinstance(report_issues, list)
                or not report_issues
                or latest_certificate.get("issue_count")
                != len(report_issues)
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: latest certificate and handoff differ"
                )
            self._assert_reaudit_lineage_authority_manifest(
                directory,
                ordered_routes=ordered_routes,
                lineage_authority_manifest=lineage_authority_manifest,
                error_message=(
                    "repair_route_freshness_invalid: completed lineage authority changed"
                ),
            )

            # Every successor has a single exact Hub projection.  Root audit
            # claims predate synchronized successor projections and therefore
            # remain authoritative in signed storage alone.
            hub_successors = [
                row
                for row in hub_before.get("handoffs", [])
                if isinstance(row, dict)
                and row.get("reaudit_kind")
                == "independent_audit_successor"
                and row.get("reaudit_root_handoff_id") == clean_root_id
            ]
            if any(
                isinstance(row, dict)
                and (
                    row.get("reaudit_root_handoff_id") == clean_root_id
                    or row.get("reaudit_parent_handoff_id") in lineage_ids
                )
                and row.get("reaudit_kind")
                != "independent_audit_successor"
                for row in hub_before.get("handoffs", [])
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: malformed Hub lineage projection"
                )
            if len(
                {str(row.get("handoff_id") or "") for row in hub_successors}
            ) != len(hub_successors):
                raise ValueError(
                    "repair_route_freshness_invalid: duplicate successor Hub projection"
                )
            if {
                str(row.get("handoff_id") or "") for row in hub_successors
            } != set(successors):
                raise ValueError(
                    "repair_route_freshness_invalid: signed and Hub successor sets differ"
                )
            for row in hub_successors:
                signed = successors[str(row["handoff_id"])]
                if row != self._hub_record_projection(signed):
                    raise ValueError(
                        "repair_route_freshness_invalid: successor Hub projection differs"
                    )

            package_bindings = {
                "repair_package_id": clean_package_id,
                "audit_certificate_id": clean_certificate_id,
                "audit_event_id": latest_certificate.get("event_id"),
                "audit_to_repair_handoff_id": clean_handoff_id,
                "production_handoff_id": clean_latest_id,
                "source_audit_handoff_id": clean_latest_id,
                "production_work_order_id": cursor.get("work_order_id"),
                "audit_work_order_id": authority_work_order_id,
                "source_audit_event_trace_id": latest_certificate.get(
                    "stage_trace_id"
                ),
                "source_audit_route_trace_id": latest_certificate.get(
                    "production_trace_id"
                ),
                "source_production_stage_trace_id": latest_certificate.get(
                    "production_stage_trace_id"
                ),
                "fingerprints": cursor.get("fingerprints"),
            }
            repair_handoff_bindings = {
                "handoff_id": clean_handoff_id,
                "work_order_id": authority_work_order_id,
                "audit_cycle_id": cursor.get("audit_cycle_id"),
                "audit_certificate_id": clean_certificate_id,
                "certificate_id": clean_certificate_id,
                "immutable_repair_package_id": clean_package_id,
                "stage_trace_id": latest_certificate.get("stage_trace_id"),
                "source_audit_handoff_id": clean_latest_id,
                "source_audit_event_trace_id": latest_certificate.get(
                    "stage_trace_id"
                ),
                "source_audit_route_trace_id": latest_certificate.get(
                    "production_trace_id"
                ),
                "fingerprints": cursor.get("fingerprints"),
                "scope": cursor.get("scope"),
            }
            if any(
                sealed_package.get(key) != value
                for key, value in package_bindings.items()
            ) or any(
                sealed_handoff.get(key) != value
                for key, value in repair_handoff_bindings.items()
            ):
                raise ValueError(
                    "repair_route_freshness_invalid: package or repair handoff differs from latest authority"
                )

            # A missing initial projection is recoverable only before the
            # authorization advanced.  Recheck this under the project lock so
            # a concurrent repair cannot consume the package between the
            # service preflight and this publication CAS.
            if not package_path.is_file():
                downstream_evidence: list[str] = []
                if any(
                    isinstance(row, dict)
                    and row.get("repair_package_id") == clean_package_id
                    for row in hub_before.get("repair_packages", [])
                ):
                    downstream_evidence.append("hub_repair_package")
                for candidate in signed_handoffs.values():
                    if (
                        candidate.get("handoff_type")
                        == "repair_to_audit"
                        and candidate.get("immutable_repair_package_id")
                        == clean_package_id
                    ):
                        downstream_evidence.append(
                            "repair_to_audit:"
                            + str(candidate.get("handoff_id") or "")
                        )
                artifact_directory = directory / "artifacts"
                for metadata_path in sorted(
                    artifact_directory.glob("ART-*.meta.json")
                ):
                    artifact_record = self.read_json(metadata_path)
                    if not isinstance(artifact_record, dict):
                        raise ValueError(
                            "repair_route_freshness_invalid: malformed artifact metadata"
                        )
                    metadata = artifact_record.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    if clean_package_id in {
                        str(metadata.get("repair_package_id") or ""),
                        str(
                            metadata.get("patch_repair_package_id") or ""
                        ),
                    }:
                        downstream_evidence.append(
                            "successor_artifact:"
                            + str(
                                artifact_record.get("artifact_id") or ""
                            )
                        )
                base_artifact_id = safe_id(
                    str(sealed_package.get("base_artifact_id") or ""),
                    "base_artifact_id",
                )
                base_metadata_path = (
                    artifact_directory
                    / f"{base_artifact_id}.meta.json"
                )
                if base_metadata_path.is_file():
                    base_record = self.read_json(base_metadata_path)
                    base_metadata = (
                        base_record.get("metadata")
                        if isinstance(base_record, dict)
                        else None
                    )
                    transaction = (
                        base_metadata.get("successor_transaction")
                        if isinstance(base_metadata, dict)
                        else None
                    )
                    if (
                        isinstance(transaction, dict)
                        and transaction.get("successor_kind")
                        in {"patch", "repair"}
                        and transaction.get("state")
                        in {"prepared", "committed"}
                    ):
                        downstream_evidence.append(
                            "base_successor_transaction:"
                            + str(
                                transaction.get("transaction_id")
                                or transaction.get("claim_id")
                                or "unknown"
                            )
                        )
                if downstream_evidence:
                    raise ValueError(
                        "repair_route_freshness_invalid: consumed repair authorization cannot be resurrected"
                    )

            def exact_existing(
                path: Path,
                record_id: str,
                expected: dict[str, Any],
            ) -> dict[str, Any] | None:
                if not path.is_file():
                    return None
                existing = self.read_json(path)
                self._verify_record_integrity(
                    existing, record_id=record_id
                )
                if (
                    not self.project_record_is_signed(existing)
                    or existing != expected
                ):
                    raise ValueError(
                        "repair_route_idempotency_conflict: signed record differs"
                    )
                return existing

            existing_package = exact_existing(
                package_path, clean_package_id, sealed_package
            )
            existing_handoff = exact_existing(
                handoff_path, clean_handoff_id, sealed_handoff
            )
            package_matches = [
                row
                for row in hub_before.get("repair_packages", [])
                if row.get("repair_package_id") == clean_package_id
            ]
            handoff_matches = [
                row
                for row in hub_before.get("handoffs", [])
                if row.get("handoff_id") == clean_handoff_id
            ]
            if (
                len(package_matches) > 1
                or len(handoff_matches) > 1
                or (
                    package_matches
                    and package_matches[0] != sealed_package
                )
                or (
                    handoff_matches
                    and handoff_matches[0] != sealed_handoff
                )
            ):
                raise ValueError(
                    "repair_route_projection_mismatch: Hub differs from signed route"
                )

            hub_after = copy.deepcopy(hub_before)
            if not package_matches:
                hub_after.setdefault("repair_packages", []).append(
                    copy.deepcopy(sealed_package)
                )
            if not handoff_matches:
                hub_after.setdefault("handoffs", []).append(
                    copy.deepcopy(sealed_handoff)
                )
            package_created = existing_package is None
            handoff_created = existing_handoff is None
            try:
                if package_created:
                    self.atomic_write_json(package_path, sealed_package)
                if handoff_created:
                    self.atomic_write_json(handoff_path, sealed_handoff)
                if hub_after != hub_before:
                    self.atomic_write_json(hub_path, hub_after)
            except BaseException:
                with contextlib.suppress(OSError):
                    self.atomic_write_json(hub_path, hub_before)
                if handoff_created:
                    with contextlib.suppress(FileNotFoundError, OSError):
                        handoff_path.unlink()
                if package_created:
                    with contextlib.suppress(FileNotFoundError, OSError):
                        package_path.unlink()
                raise
            return sealed_package, sealed_handoff

    def _reaudit_route_is_referenced(
        self,
        directory: Path,
        handoff_id: str,
    ) -> bool:
        """Return whether immutable audit authority already names a route."""

        ledger = self.read_json(directory / "ledger.json")
        if any(
            event.get("handoff_id") == handoff_id
            for event in ledger.get("events", [])
            if isinstance(event, dict)
        ):
            return True
        for certificate_path in sorted(
            (directory / "certificates").glob("*.json")
        ):
            certificate = self.read_json(certificate_path)
            if not isinstance(certificate, dict):
                raise ValueError(
                    "reaudit_lineage_invalid: malformed certificate record"
                )
            certificate_id = str(
                certificate.get("record_id") or certificate_path.stem
            )
            self._verify_record_integrity(
                certificate, record_id=certificate_id
            )
            if not self.project_record_is_signed(certificate):
                raise ValueError(
                    "reaudit_lineage_invalid: unsigned certificate record"
                )
            if certificate.get("handoff_id") == handoff_id:
                return True
        return False

    def cleanup_reaudit_hub_orphans(
        self,
        namespace: str,
        project_id: str,
        *,
        root_handoff_id: str,
    ) -> list[str]:
        """Remove non-authoritative Hub-only successor projections.

        Every normal transition writes the signed record before Hub.  A Hub
        row without that record can therefore never authorize consumption.
        It is safe to remove only when neither ledger nor certificate history
        references it; otherwise the missing signed record is corruption and
        must fail closed.
        """

        clean_root_id = safe_id(root_handoff_id, "root_handoff_id")
        directory = self.project_dir(namespace, project_id)
        hub_path = directory / "hub.json"
        with self.project_write_lock(namespace, project_id):
            signed_ids: set[str] = set()
            for path in sorted((directory / "handoffs").glob("*.json")):
                record = self.read_json(path)
                if not isinstance(record, dict):
                    raise ValueError(
                        "reaudit_lineage_invalid: malformed handoff record"
                    )
                record_id = path.stem
                self._verify_record_integrity(record, record_id=record_id)
                self._verify_project_record_primary_id(
                    "handoffs", record, record_id=record_id
                )
                if (
                    record.get("project_id") != project_id
                    or not self.project_record_is_signed(record)
                ):
                    raise ValueError(
                        "reaudit_lineage_invalid: unsigned or cross-project handoff record"
                    )
                signed_ids.add(str(record.get("handoff_id") or record_id))

            hub = self.read_json(hub_path)
            removed: list[str] = []
            kept: list[dict[str, Any]] = []
            for row in hub.get("handoffs", []):
                if not isinstance(row, dict):
                    raise ValueError(
                        "reaudit_successor_projection_mismatch: malformed Hub handoff"
                    )
                row_id = str(row.get("handoff_id") or "")
                is_root_successor = (
                    row.get("reaudit_root_handoff_id") == clean_root_id
                )
                if not is_root_successor or row_id in signed_ids:
                    kept.append(row)
                    continue
                clean_row_id = safe_id(row_id, "handoff_id")
                if (
                    row.get("reaudit_kind")
                    != "independent_audit_successor"
                    or row.get("server_minted_lineage") is not True
                    or not row.get("reaudit_route_trace_id")
                    or not row.get("reaudit_event_trace_id")
                ):
                    raise ValueError(
                        "reaudit_successor_projection_mismatch: Hub-only route is not a valid server projection"
                    )
                if self._reaudit_route_is_referenced(
                    directory, clean_row_id
                ):
                    raise ValueError(
                        "reaudit_successor_projection_mismatch: referenced route lost its signed record"
                    )
                removed.append(clean_row_id)
                # Hub is an unsigned projection.  Never use its trace id to
                # delete signed telemetry: a corrupt row could point at an
                # unrelated production or audit trace.  Preserve it for
                # forensic inspection unless a signed route proves ownership.
            if removed:
                hub["handoffs"] = kept
                self.atomic_write_json(hub_path, hub)
            return removed

    def claim_reaudit_successor(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        lease_seconds: int = DEFAULT_HANDOFF_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Claim one successor while keeping signed storage and Hub identical."""

        clean_id = safe_id(handoff_id, "handoff_id")
        clean_consumer_id = safe_id(
            consumer_work_order_id, "consumer_work_order_id"
        )
        try:
            lease_seconds = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid handoff lease_seconds") from exc
        if not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS:
            raise ValueError(
                f"handoff lease_seconds must be between 1 and {MAX_CONSUMPTION_LEASE_SECONDS}"
            )
        directory = self.project_dir(namespace, project_id)
        path = directory / "handoffs" / f"{clean_id}.json"
        hub_path = directory / "hub.json"

        with self.project_write_lock(namespace, project_id):
            before = self.read_json(path)
            self._verify_record_integrity(before, record_id=clean_id)
            lifecycle_error = self._reaudit_successor_lifecycle_error(before)
            if lifecycle_error:
                raise ValueError(lifecycle_error)
            hub_before = self.read_json(hub_path)
            matches = [
                index
                for index, row in enumerate(hub_before.get("handoffs", []))
                if row.get("handoff_id") == clean_id
            ]
            if (
                before.get("reaudit_kind")
                != "independent_audit_successor"
                or before.get("claim_authority_work_order_id")
                != clean_consumer_id
                or len(matches) != 1
            ):
                raise ValueError(
                    "reaudit_successor_projection_mismatch: route is not server-bound in both stores"
                )
            hub_projection = hub_before["handoffs"][matches[0]]
            status = before.get("status")
            if status == "consuming":
                preclaim_sha256 = str(
                    before.get("reaudit_preclaim_projection_sha256") or ""
                )
                if (
                    hub_projection != self._hub_record_projection(before)
                    and preclaim_sha256
                    and self.project_state_sha256(hub_projection)
                    == preclaim_sha256
                ):
                    # Recover the only safe half-write: signed storage moved
                    # to consuming while Hub still contains the exact,
                    # server-hashed pre-claim projection.
                    hub_before["handoffs"][matches[0]] = (
                        self._hub_record_projection(before)
                    )
                    self.atomic_write_json(hub_path, hub_before)
                    hub_projection = self._hub_record_projection(before)
                if hub_projection != self._hub_record_projection(before):
                    raise ValueError(
                        "reaudit_successor_projection_mismatch: consuming route projections differ"
                    )
                if before.get("claimed_by_work_order_id") != clean_consumer_id:
                    raise ValueError(
                        "handoff_state_conflict: successor is bound to another audit authority"
                    )
                try:
                    lease_expires = float(
                        before.get("claim_lease_expires_epoch_seconds") or 0
                    )
                except (TypeError, ValueError):
                    lease_expires = 0
                if lease_expires > time.time():
                    raise ValueError(
                        "handoff_state_conflict: successor is being consumed"
                    )
                history = before.setdefault("claim_history", [])
                if not isinstance(history, list):
                    history = []
                    before["claim_history"] = history
                history.append(
                    {
                        "claim_token": before.get("claim_token"),
                        "claimed_by_work_order_id": clean_consumer_id,
                        "claim_generation": before.get("claim_generation"),
                        "claim_epoch_seconds": before.get(
                            "claim_epoch_seconds"
                        ),
                        "claim_lease_seconds": before.get(
                            "claim_lease_seconds"
                        ),
                        "claim_lease_expires_at": before.get(
                            "claim_lease_expires_at"
                        ),
                        "claim_lease_expires_epoch_seconds": before.get(
                            "claim_lease_expires_epoch_seconds"
                        ),
                        "reaudit_preclaim_projection_sha256": before.get(
                            "reaudit_preclaim_projection_sha256"
                        ),
                        "state": "lease_expired",
                        "expired_at": utc_stamp(),
                    }
                )
                del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
                before["status"] = "prepared"
                for key in (
                    "claimed_by_work_order_id",
                    "claim_token",
                    "claim_epoch_seconds",
                    "claim_lease_seconds",
                    "claim_lease_expires_at",
                    "claim_lease_expires_epoch_seconds",
                ):
                    before.pop(key, None)
                status = "prepared"
            elif hub_projection != self._hub_record_projection(before):
                raise ValueError(
                    "reaudit_successor_projection_mismatch: route projections differ"
                )
            if status != "prepared":
                raise ValueError(
                    f"handoff_state_conflict: successor status is {status!r}"
                )
            now = time.time()
            after = copy.deepcopy(before)
            after.update(
                {
                    "status": "consuming",
                    "claimed_by_work_order_id": clean_consumer_id,
                    "claim_token": f"HCL-{uuid.uuid4().hex}",
                    "claim_generation": int(before.get("claim_generation") or 0)
                    + 1,
                    "claim_epoch_seconds": now,
                    "claim_lease_seconds": lease_seconds,
                    "claim_lease_expires_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(now + lease_seconds),
                    ),
                    "claim_lease_expires_epoch_seconds": now + lease_seconds,
                    "reaudit_preclaim_projection_sha256": (
                        self.project_state_sha256(hub_projection)
                    ),
                }
            )
            after["integrity_hmac"] = self._record_integrity_hmac(after)
            hub_after = copy.deepcopy(hub_before)
            hub_after["handoffs"][matches[0]] = self._hub_record_projection(
                after
            )
            try:
                self.atomic_write_json(path, after)
                self.atomic_write_json(hub_path, hub_after)
            except BaseException:
                with contextlib.suppress(OSError):
                    self.atomic_write_json(path, before)
                with contextlib.suppress(OSError):
                    self.atomic_write_json(hub_path, hub_before)
                raise
            return after

    def release_reaudit_successor_claim(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        claim_token: str,
        reason: str,
    ) -> dict[str, Any]:
        """Release one successor claim and synchronize both projections."""

        clean_id = safe_id(handoff_id, "handoff_id")
        directory = self.project_dir(namespace, project_id)
        path = directory / "handoffs" / f"{clean_id}.json"
        hub_path = directory / "hub.json"

        with self.project_write_lock(namespace, project_id):
            before = self.read_json(path)
            self._verify_record_integrity(before, record_id=clean_id)
            lifecycle_error = self._reaudit_successor_lifecycle_error(before)
            if lifecycle_error:
                raise ValueError(lifecycle_error)
            if before.get("status") == "consumed":
                return before
            if (
                before.get("reaudit_kind")
                != "independent_audit_successor"
                or before.get("status") != "consuming"
                or before.get("claimed_by_work_order_id")
                != consumer_work_order_id
                or before.get("claim_token") != claim_token
            ):
                raise ValueError(
                    "handoff_state_conflict: successor claim is not current"
                )
            hub_before = self.read_json(hub_path)
            matches = [
                index
                for index, row in enumerate(hub_before.get("handoffs", []))
                if row.get("handoff_id") == clean_id
            ]
            if (
                len(matches) != 1
                or hub_before["handoffs"][matches[0]]
                != self._hub_record_projection(before)
            ):
                raise ValueError("reaudit_successor_projection_mismatch")
            after = copy.deepcopy(before)
            history = after.setdefault("claim_history", [])
            if not isinstance(history, list):
                history = []
                after["claim_history"] = history
            history.append(
                {
                    "claim_token": claim_token,
                    "claimed_by_work_order_id": consumer_work_order_id,
                    "claim_generation": before.get("claim_generation"),
                    "reaudit_preclaim_projection_sha256": before.get(
                        "reaudit_preclaim_projection_sha256"
                    ),
                    "state": "released",
                    "released_at": utc_stamp(),
                    "release_reason": str(reason or "audit_failed")[:256],
                }
            )
            del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            after["status"] = "prepared"
            for key in (
                "claimed_by_work_order_id",
                "claim_token",
                "claim_epoch_seconds",
                "claim_lease_seconds",
                "claim_lease_expires_at",
                "claim_lease_expires_epoch_seconds",
            ):
                after.pop(key, None)
            after["reaudit_prerelease_projection_sha256"] = (
                self.project_state_sha256(
                    self._hub_record_projection(before)
                )
            )
            after["integrity_hmac"] = self._record_integrity_hmac(after)
            hub_after = copy.deepcopy(hub_before)
            hub_after["handoffs"][matches[0]] = self._hub_record_projection(
                after
            )
            try:
                self.atomic_write_json(path, after)
                self.atomic_write_json(hub_path, hub_after)
            except BaseException:
                with contextlib.suppress(OSError):
                    self.atomic_write_json(path, before)
                with contextlib.suppress(OSError):
                    self.atomic_write_json(hub_path, hub_before)
                raise
            return after

    def finish_reaudit_successor(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        audit_certificate_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        """Consume a re-audit successor and synchronize its Hub projection."""

        clean_id = safe_id(handoff_id, "handoff_id")
        clean_consumer_id = safe_id(
            consumer_work_order_id, "consumer_work_order_id"
        )
        clean_certificate_id = safe_id(
            audit_certificate_id, "audit_certificate_id"
        )
        directory = self.project_dir(namespace, project_id)
        path = directory / "handoffs" / f"{clean_id}.json"
        certificate_path = (
            directory / "certificates" / f"{clean_certificate_id}.json"
        )
        hub_path = directory / "hub.json"

        with self.project_write_lock(namespace, project_id):
            if not path.is_file():
                raise ValueError("handoff_required: signed successor does not exist")
            before = self.read_json(path)
            self._verify_record_integrity(before, record_id=clean_id)
            if before.get("reaudit_kind") != "independent_audit_successor":
                raise ValueError("reaudit_lineage_invalid: handoff is not a successor")
            finalization_error = self._reaudit_successor_lifecycle_error(before)
            if finalization_error:
                raise ValueError(finalization_error)
            if not certificate_path.is_file():
                raise ValueError(
                    "reaudit_certificate_binding_invalid: certificate is missing"
                )
            certificate = self.read_json(certificate_path)
            self._verify_record_integrity(
                certificate, record_id=clean_certificate_id
            )
            if (
                not self.project_record_is_signed(certificate)
                or certificate.get("handoff_id") != clean_id
                or certificate.get("work_order_id") != clean_consumer_id
                or certificate.get("stage_trace_id")
                != before.get("reaudit_event_trace_id")
                or certificate.get("production_trace_id")
                != before.get("reaudit_route_trace_id")
            ):
                raise ValueError(
                    "reaudit_certificate_binding_invalid: certificate does not belong to this route"
                )
            hub_before = self.read_json(hub_path)
            hub_matches = [
                index
                for index, row in enumerate(hub_before.get("handoffs", []))
                if row.get("handoff_id") == clean_id
            ]
            if len(hub_matches) != 1:
                raise ValueError("reaudit_successor_projection_mismatch")
            hub_projection = hub_before["handoffs"][hub_matches[0]]
            if (
                before.get("status") == "consuming"
                and hub_projection != self._hub_record_projection(before)
                and str(
                    before.get("reaudit_preclaim_projection_sha256") or ""
                )
                and self.project_state_sha256(hub_projection)
                == before.get("reaudit_preclaim_projection_sha256")
            ):
                # Finalization can directly bridge the signed-consuming /
                # exact pre-claim Hub boundary after a crash.
                hub_before["handoffs"][hub_matches[0]] = (
                    self._hub_record_projection(before)
                )
            if before.get("status") == "consumed":
                if (
                    before.get("consumed_by_work_order_id") == clean_consumer_id
                    and before.get("audit_certificate_id")
                    == clean_certificate_id
                ):
                    hub_projection = hub_before["handoffs"][hub_matches[0]]
                    if hub_projection != self._hub_record_projection(before):
                        claim = before.get("consumption_claim")
                        if not isinstance(claim, dict):
                            raise ValueError(
                                "reaudit_successor_projection_mismatch"
                            )
                        half_written = copy.deepcopy(before)
                        half_written["status"] = "consuming"
                        half_written["claimed_by_work_order_id"] = (
                            clean_consumer_id
                        )
                        for key, value in claim.items():
                            half_written[key] = copy.deepcopy(value)
                        for key in (
                            "consumed_by_work_order_id",
                            "audit_certificate_id",
                            "latest_audit_certificate_id",
                            "consumed_at",
                            "consumption_claim",
                        ):
                            half_written.pop(key, None)
                        if hub_projection != self._hub_record_projection(
                            half_written
                        ):
                            raise ValueError(
                                "reaudit_successor_projection_mismatch"
                            )
                        hub_after = copy.deepcopy(hub_before)
                        hub_after["handoffs"][hub_matches[0]] = (
                            self._hub_record_projection(before)
                        )
                        self.atomic_write_json(hub_path, hub_after)
                    return before
                raise ValueError(
                    "handoff_state_conflict: successor finalization differs"
                )
            if (
                before.get("status") != "consuming"
                or before.get("claimed_by_work_order_id") != clean_consumer_id
                or before.get("claim_token") != claim_token
            ):
                raise ValueError(
                    "handoff_state_conflict: successor claim is not current"
                )
            if (
                hub_before["handoffs"][hub_matches[0]]
                != self._hub_record_projection(before)
            ):
                raise ValueError("reaudit_successor_projection_mismatch")
            after = copy.deepcopy(before)
            after["consumption_claim"] = {
                key: copy.deepcopy(before.get(key))
                for key in (
                    "claim_token",
                    "claim_generation",
                    "claim_epoch_seconds",
                    "claim_lease_seconds",
                    "claim_lease_expires_at",
                    "claim_lease_expires_epoch_seconds",
                )
                if key in before
            }
            after["status"] = "consumed"
            after["consumed_by_work_order_id"] = clean_consumer_id
            after["audit_certificate_id"] = clean_certificate_id
            after["latest_audit_certificate_id"] = clean_certificate_id
            after["consumed_at"] = utc_stamp()
            for key in (
                "claimed_by_work_order_id",
                "claim_token",
                "claim_epoch_seconds",
                "claim_lease_seconds",
                "claim_lease_expires_at",
                "claim_lease_expires_epoch_seconds",
            ):
                after.pop(key, None)
            finalization_error = self._reaudit_successor_lifecycle_error(after)
            if finalization_error:
                raise ValueError(finalization_error)
            after["integrity_hmac"] = self._record_integrity_hmac(after)
            hub_after = copy.deepcopy(hub_before)
            hub_after["handoffs"][hub_matches[0]] = (
                self._hub_record_projection(after)
            )
            try:
                self.atomic_write_json(path, after)
                self.atomic_write_json(hub_path, hub_after)
            except BaseException:
                with contextlib.suppress(OSError):
                    self.atomic_write_json(path, before)
                with contextlib.suppress(OSError):
                    self.atomic_write_json(hub_path, hub_before)
                raise
            return after

    def rollback_reaudit_successor(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        route_trace_id: str,
    ) -> None:
        """Compensate an unconsumed successor after a handled audit failure."""

        clean_id = safe_id(handoff_id, "handoff_id")
        clean_trace_id = safe_id(route_trace_id, "route_trace_id")
        directory = self.project_dir(namespace, project_id)
        path = directory / "handoffs" / f"{clean_id}.json"
        telemetry_path = directory / "telemetry" / f"{clean_trace_id}.json"
        hub_path = directory / "hub.json"
        with self.project_write_lock(namespace, project_id):
            hub = self.read_json(hub_path)
            hub_matches = [
                row
                for row in hub.get("handoffs", [])
                if row.get("handoff_id") == clean_id
            ]
            if len(hub_matches) > 1:
                raise ValueError("reaudit_successor_projection_mismatch")
            if not path.is_file():
                if hub_matches:
                    hub_only = hub_matches[0]
                    if (
                        hub_only.get("reaudit_kind")
                        != "independent_audit_successor"
                        or hub_only.get("server_minted_lineage") is not True
                        or hub_only.get("reaudit_route_trace_id")
                        != clean_trace_id
                    ):
                        raise ValueError(
                            "reaudit_successor_projection_mismatch"
                        )
                    if self._reaudit_route_is_referenced(
                        directory, clean_id
                    ):
                        raise ValueError(
                            "reaudit_successor_projection_mismatch: referenced route lost its signed record"
                        )
                    hub["handoffs"] = [
                        row
                        for row in hub.get("handoffs", [])
                        if row.get("handoff_id") != clean_id
                    ]
                    self.atomic_write_json(hub_path, hub)
                # Without the signed route, the supplied/Hub trace id cannot
                # prove telemetry ownership.  Retain any trace for forensics.
                return
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if (
                record.get("reaudit_kind")
                != "independent_audit_successor"
                or record.get("reaudit_route_trace_id") != clean_trace_id
                or record.get("status") not in {"prepared", "consuming"}
                or record.get("audit_certificate_id")
            ):
                return
            if self._reaudit_route_is_referenced(directory, clean_id):
                # Once an immutable event refers to the route, deleting it
                # would make the ledger less recoverable.  Leave the prepared
                # successor in place for an exact retry instead.
                return
            if hub_matches:
                hub_projection = hub_matches[0]
                exact_projection = self._hub_record_projection(record)
                preclaim_sha256 = str(
                    record.get("reaudit_preclaim_projection_sha256") or ""
                )
                provable_preclaim = bool(
                    record.get("status") == "consuming"
                    and preclaim_sha256
                    and self.project_state_sha256(hub_projection)
                    == preclaim_sha256
                )
                prerelease_sha256 = str(
                    record.get("reaudit_prerelease_projection_sha256") or ""
                )
                provable_prerelease = bool(
                    record.get("status") == "prepared"
                    and prerelease_sha256
                    and self.project_state_sha256(hub_projection)
                    == prerelease_sha256
                )
                if (
                    hub_projection != exact_projection
                    and not provable_preclaim
                    and not provable_prerelease
                ):
                    raise ValueError(
                        "reaudit_successor_projection_mismatch"
                    )
            hub["handoffs"] = [
                row
                for row in hub.get("handoffs", [])
                if row.get("handoff_id") != clean_id
            ]
            # The signed route proves exclusive ownership of this telemetry
            # id.  Delete it before removing the authority needed to resume
            # cleanup; a failure here leaves Hub and signed storage untouched,
            # so an identical rollback can safely retry.
            with contextlib.suppress(FileNotFoundError):
                telemetry_path.unlink()
            self.atomic_write_json(hub_path, hub)
            path.unlink()

    def finish_handoff(
        self,
        namespace: str,
        project_id: str,
        handoff_id: str,
        *,
        consumer_work_order_id: str,
        audit_certificate_id: str,
        claim_token: str | None = None,
    ) -> dict[str, Any]:
        clean_id = safe_id(handoff_id, "handoff_id")
        path = self.project_dir(namespace, project_id) / "handoffs" / f"{clean_id}.json"
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
            with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
        with self.project_write_lock(namespace, project_id):
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
