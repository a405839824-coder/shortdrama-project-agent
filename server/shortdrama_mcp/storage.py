from __future__ import annotations

import base64
import calendar
import copy
import contextlib
import contextvars
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
    "draft_observations",
    "migrations",
    "authority_continuations",
    "workbook_migrations",
    "narrative_records",
    "audit_coverage_collections",
    "audit_coverage_pages",
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
    "draft_observations": "observation_id",
    "migrations": "migration_id",
    "authority_continuations": "continuation_id",
    "workbook_migrations": "workbook_migration_id",
    "narrative_records": "record_id",
    "audit_coverage_collections": "collection_id",
    "audit_coverage_pages": "page_id",
}
DEFAULT_SUCCESSOR_LEASE_SECONDS = 300
MAX_SUCCESSOR_LEASE_SECONDS = 86_400
MAX_SUCCESSOR_TRANSACTION_HISTORY = 32
DEFAULT_DRAFT_PREFLIGHT_COMMIT_LEASE_SECONDS = 300
DEFAULT_HANDOFF_CLAIM_LEASE_SECONDS = 300
MAX_CONSUMPTION_LEASE_SECONDS = 3_600
MAX_CONSUMPTION_CLAIM_HISTORY = 16
DEFAULT_AUTHORITY_OPERATION_LEASE_SECONDS = 300
MAX_AUTHORITY_OPERATION_LEASE_SECONDS = 3_600
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_SPOOL_MEMORY_BYTES = 2 * 1024 * 1024
DOWNLOAD_COPY_CHUNK_BYTES = 1024 * 1024
LEGACY_DOWNLOAD_TOKEN_WINDOW_SECONDS = 86_400
SUCCESSOR_KINDS = frozenset({"patch", "delta", "repair"})
UNPUBLISHED_ARTIFACT_STATES = frozenset({"pending", "aborted", "rejected"})
CONTROLLED_HISTORICAL_PREFLIGHT_HANDOFF_IDS = (
    "HND-f6fd29730a074d2c",
    "HND-95e3b1c141a64783",
    "HND-c88ee199d5fb4676",
    "HND-e8ca409208134104",
    "HND-0dd8fa51a9e24cd0",
    "HND-9029110f35d74270",
)
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
    "production_validation_replay",
    "current_validation_status",
    "current_validation_issue_count",
    "current_production_pass_claimed",
    "current_preflight_cache_reusable",
    "requires_full_current_independent_audit",
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
    "recovery_publication_fenced",
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
        "claim_authority_contract",
        "claim_authority_root_id",
        "claim_authority_generation",
        "claim_authority_session_fingerprint",
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


_ACTIVE_AUTHORITY_OPERATION: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("shortdrama_active_authority_operation", default=None)
)

# A synchronous project transaction may use several existing storage methods.
# Reuse an already held lock only in its owning thread and for the same absolute
# path. Other threads and processes still acquire the cross-process FileLock.
_HELD_STORAGE_LOCKS = threading.local()


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
        key = os.path.normcase(str(lock_path.resolve()))
        held = getattr(_HELD_STORAGE_LOCKS, "paths", None)
        if held is None:
            held = _HELD_STORAGE_LOCKS.paths = set()
        if key in held:
            yield
            return
        with FileLock(str(lock_path)):
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)

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

    @staticmethod
    def _authority_root_id(project_id: str, work_order_id: str) -> str:
        return "AUTH-" + sha256_bytes(
            canonical_json_bytes(
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "contract": "window-role-authority-root-v1",
                }
            )
        )[:24]

    @staticmethod
    def _authority_operation_lease_id(authority_root_id: str) -> str:
        return "AOP-" + sha256_bytes(authority_root_id.encode("utf-8"))[:24]

    def _authority_operation_lease_path(
        self,
        directory: Path,
        authority_root_id: str,
    ) -> Path:
        lease_id = self._authority_operation_lease_id(authority_root_id)
        project_scope = sha256_bytes(
            str(directory.resolve()).encode("utf-8")
        )[:32]
        return (
            self.root
            / ".authority-operation-leases"
            / project_scope
            / f"{lease_id}.json"
        )

    def _active_authority_binding_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        hub: dict[str, Any],
        work_order: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve one signed authority head while the project lock is held."""

        work_order_id = safe_id(work_order.get("work_order_id"), "work_order_id")
        authority_root_id = self._authority_root_id(project_id, work_order_id)
        raw_heads = hub.get("active_authority_bindings", [])
        if not isinstance(raw_heads, list):
            raise ValueError(
                "authority_continuation_head_conflict: active heads must be an array"
            )
        matches = [
            row
            for row in raw_heads
            if isinstance(row, dict)
            and row.get("authority_root_id") == authority_root_id
        ]
        if len(matches) > 1:
            raise ValueError(
                "authority_continuation_head_conflict: duplicate authority root"
            )

        signed_root_records: list[dict[str, Any]] = []
        for record_path in sorted(
            (directory / "authority_continuations").glob("*.json")
        ):
            record = self.read_json(record_path)
            if not isinstance(record, dict):
                raise ValueError("authority_continuation_record_conflict")
            self._verify_record_integrity(record, record_id=record_path.stem)
            self._verify_project_record_primary_id(
                "authority_continuations",
                record,
                record_id=record_path.stem,
            )
            if (
                record.get("project_id") != project_id
                or not self.project_record_is_signed(record)
            ):
                raise ValueError("authority_continuation_record_conflict")
            if record.get("authority_root_id") == authority_root_id:
                signed_root_records.append(record)

        if not matches:
            if signed_root_records:
                raise ValueError(
                    "authority_head_incomplete: signed continuation is not published"
                )
            authority_contract = {
                "work_order_id": work_order_id,
                "operation": str(work_order.get("operation") or ""),
                "mode": str(work_order.get("mode") or ""),
                "authorization": str(work_order.get("authorization") or ""),
                "server_actor_role": str(
                    work_order.get("server_actor_role") or ""
                ),
                "targets": copy.deepcopy(work_order.get("targets") or []),
                "source_lock": copy.deepcopy(
                    work_order.get("source_lock") or {}
                ),
                "protected_scope": copy.deepcopy(
                    work_order.get("protected_scope") or []
                ),
                "expected_outputs": copy.deepcopy(
                    work_order.get("expected_outputs") or []
                ),
            }
            if "window_access_contract" in work_order:
                authority_contract["window_access_contract"] = str(
                    work_order.get("window_access_contract") or ""
                )
                authority_contract["window_access_scope"] = str(
                    work_order.get("window_access_scope") or ""
                )
            return {
                "authority_root_id": authority_root_id,
                "continuation_id": None,
                "authority_epoch_id": f"EPOCH-{authority_root_id[5:]}-0",
                "work_order_id": work_order_id,
                "generation": 0,
                "window_binding_id": work_order.get(
                    "server_window_binding_id"
                ),
                "actor_binding_id": work_order.get(
                    "server_actor_binding_id"
                ),
                "session_fingerprint": work_order.get(
                    "transport_session_fingerprint"
                ),
                "role": work_order.get("server_actor_role"),
                "operation": work_order.get("operation"),
                "mode": work_order.get("mode"),
                "authorization": work_order.get("authorization"),
                "authority_contract_sha256": sha256_bytes(
                    canonical_json_bytes(authority_contract)
                ),
                "plan_sha256": None,
                "status": "active",
            }

        head = matches[0]
        continuation_id = safe_id(
            head.get("continuation_id"), "continuation_id"
        )
        if head.get("work_order_id") != work_order_id:
            raise ValueError(
                "authority_continuation_head_conflict: work order differs"
            )
        generation = head.get("generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise ValueError("authority_continuation_head_conflict: generation")
        record_path = (
            directory / "authority_continuations" / f"{continuation_id}.json"
        )
        if not record_path.is_file():
            raise ValueError(
                "authority_continuation_head_conflict: signed head is missing"
            )
        record = self.read_json(record_path)
        if not isinstance(record, dict):
            raise ValueError("authority_continuation_record_conflict")
        self._verify_record_integrity(record, record_id=continuation_id)
        self._verify_project_record_primary_id(
            "authority_continuations", record, record_id=continuation_id
        )
        if (
            not self.project_record_is_signed(record)
            or record.get("project_id") != project_id
            or record.get("authority_root_id") != authority_root_id
            or record.get("work_order_id") != work_order_id
            or record.get("status") != "active"
            or record.get("active_binding") != head
        ):
            raise ValueError(
                "authority_head_invalid: signed continuation record differs"
            )
        generations = [
            row.get("generation")
            for row in signed_root_records
            if isinstance(row.get("generation"), int)
            and not isinstance(row.get("generation"), bool)
        ]
        if not generations or generation != max(generations):
            raise ValueError(
                "authority_head_rollback: active head is not the newest signed generation"
            )
        return copy.deepcopy(head)

    def _authority_binding_at_generation_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        hub: dict[str, Any],
        work_order: dict[str, Any],
        generation: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Verify and return one binding from a contiguous signed lineage."""

        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise ValueError("audit_authority_generation_invalid")
        current = self._active_authority_binding_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=work_order,
        )
        current_generation = current.get("generation")
        if (
            isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or generation > current_generation
        ):
            raise ValueError("audit_authority_generation_invalid")
        work_order_id = safe_id(
            work_order.get("work_order_id"), "work_order_id"
        )
        authority_root_id = self._authority_root_id(
            project_id, work_order_id
        )
        authority_contract = {
            "work_order_id": work_order_id,
            "operation": str(work_order.get("operation") or ""),
            "mode": str(work_order.get("mode") or ""),
            "authorization": str(work_order.get("authorization") or ""),
            "server_actor_role": str(
                work_order.get("server_actor_role") or ""
            ),
            "targets": copy.deepcopy(work_order.get("targets") or []),
            "source_lock": copy.deepcopy(work_order.get("source_lock") or {}),
            "protected_scope": copy.deepcopy(
                work_order.get("protected_scope") or []
            ),
            "expected_outputs": copy.deepcopy(
                work_order.get("expected_outputs") or []
            ),
        }
        if "window_access_contract" in work_order:
            authority_contract["window_access_contract"] = str(
                work_order.get("window_access_contract") or ""
            )
            authority_contract["window_access_scope"] = str(
                work_order.get("window_access_scope") or ""
            )
        contract_sha256 = sha256_bytes(
            canonical_json_bytes(authority_contract)
        )
        generation_zero = {
            "continuation_id": None,
            "authority_epoch_id": f"EPOCH-{authority_root_id[5:]}-0",
            "authority_root_id": authority_root_id,
            "work_order_id": work_order_id,
            "generation": 0,
            "window_binding_id": work_order.get("server_window_binding_id"),
            "actor_binding_id": work_order.get("server_actor_binding_id"),
            "session_fingerprint": work_order.get(
                "transport_session_fingerprint"
            ),
            "role": work_order.get("server_actor_role"),
            "operation": work_order.get("operation"),
            "mode": work_order.get("mode"),
            "authorization": work_order.get("authorization"),
            "authority_contract_sha256": contract_sha256,
            "plan_sha256": None,
            "status": "active",
        }
        records: list[dict[str, Any]] = []
        for record_path in sorted(
            (directory / "authority_continuations").glob("*.json")
        ):
            record = self.read_json(record_path)
            if not isinstance(record, dict):
                raise ValueError("audit_authority_lineage_invalid")
            self._verify_record_integrity(record, record_id=record_path.stem)
            self._verify_project_record_primary_id(
                "authority_continuations",
                record,
                record_id=record_path.stem,
            )
            if (
                record.get("project_id") != project_id
                or not self.project_record_is_signed(record)
            ):
                raise ValueError("audit_authority_lineage_invalid")
            if record.get("authority_root_id") == authority_root_id:
                records.append(record)
        by_generation: dict[int, dict[str, Any]] = {}
        previous_continuation_id: str | None = None
        expected_binding_fields = set(generation_zero)
        authority_epoch_ids: set[str] = set()
        plan_sha256s: set[str] = set()
        for expected_generation in range(1, current_generation + 1):
            matches = [
                record
                for record in records
                if record.get("generation") == expected_generation
            ]
            if len(matches) != 1:
                raise ValueError(
                    "audit_authority_lineage_invalid: generation gap or duplicate"
                )
            record = matches[0]
            binding = record.get("active_binding")
            if (
                not isinstance(binding, dict)
                or set(binding) != expected_binding_fields
                or record.get("work_order_id") != work_order_id
                or record.get("previous_generation")
                != expected_generation - 1
                or record.get("previous_continuation_id")
                != previous_continuation_id
                or not isinstance(
                    record.get("work_order_snapshot_sha256"), str
                )
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(record.get("work_order_snapshot_sha256") or ""),
                )
                is None
                or record.get("expected_work_order_snapshot_sha256")
                != record.get("work_order_snapshot_sha256")
                or record.get("authority_contract_sha256")
                != contract_sha256
                or record.get("status") != "active"
                or isinstance(record.get("generation"), bool)
                or isinstance(binding.get("generation"), bool)
                or any(
                    record.get(field) != binding.get(field)
                    for field in expected_binding_fields
                )
                or binding.get("generation") != expected_generation
                or binding.get("authority_root_id") != authority_root_id
                or binding.get("work_order_id") != work_order_id
                or binding.get("role") != work_order.get("server_actor_role")
                or binding.get("operation") != work_order.get("operation")
                or binding.get("mode") != work_order.get("mode")
                or binding.get("authorization")
                != work_order.get("authorization")
                or binding.get("authority_contract_sha256")
                != contract_sha256
                or binding.get("continuation_id")
                != record.get("continuation_id")
                or binding.get("authority_epoch_id")
                != record.get("authority_epoch_id")
                or binding.get("plan_sha256")
                != record.get("plan_sha256")
                or binding.get("status") != "active"
            ):
                raise ValueError("audit_authority_lineage_invalid")
            epoch_id = str(binding.get("authority_epoch_id") or "")
            plan_sha256 = str(binding.get("plan_sha256") or "")
            if (
                not epoch_id
                or epoch_id in authority_epoch_ids
                or not plan_sha256
                or plan_sha256 in plan_sha256s
            ):
                raise ValueError(
                    "audit_authority_lineage_invalid: epoch or plan reused"
                )
            authority_epoch_ids.add(epoch_id)
            plan_sha256s.add(plan_sha256)
            previous_continuation_id = str(record["continuation_id"])
            by_generation[expected_generation] = copy.deepcopy(binding)
        if len(records) != current_generation:
            raise ValueError(
                "audit_authority_lineage_invalid: unexpected generation record"
            )
        expected_current = (
            generation_zero
            if current_generation == 0
            else by_generation[current_generation]
        )
        if current != expected_current:
            raise ValueError(
                "audit_authority_lineage_invalid: active head differs"
            )
        return (
            copy.deepcopy(
                generation_zero
                if generation == 0
                else by_generation[generation]
            ),
            copy.deepcopy(current),
        )

    def _work_order_authority_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        work_order_id: str,
        expected_role: str,
        error_code: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve one work order and its current authority under project lock."""

        clean_work_order_id = safe_id(work_order_id, "work_order_id")
        hub_path = directory / "hub.json"
        if not hub_path.is_file():
            raise ValueError(error_code)
        hub = self.read_json(hub_path)
        work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
        matches = [
            row
            for row in (work_orders if isinstance(work_orders, list) else [])
            if isinstance(row, dict)
            and row.get("work_order_id") == clean_work_order_id
        ]
        if (
            len(matches) != 1
            or matches[0].get("server_actor_role") != expected_role
        ):
            raise ValueError(error_code)
        active = self._active_authority_binding_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=matches[0],
        )
        return matches[0], active

    @staticmethod
    def _claim_authority_fields(
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "claim_authority_contract": "window-role-authority-claim-v1",
            "claim_authority_root_id": authority.get("authority_root_id"),
            "claim_authority_generation": authority.get("generation"),
            "claim_authority_session_fingerprint": authority.get(
                "session_fingerprint"
            ),
        }

    def _assert_claim_authority_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        record: dict[str, Any],
        work_order_id: str,
        expected_role: str,
        error_code: str,
    ) -> None:
        """Fence a durable claim to its live window generation and lease."""

        work_order, active = self._work_order_authority_unlocked(
            directory,
            project_id=project_id,
            work_order_id=work_order_id,
            expected_role=expected_role,
            error_code=error_code,
        )
        claimed_root = record.get(
            "claim_authority_root_id"
        ) or self._authority_root_id(project_id, work_order_id)
        claimed_generation = record.get("claim_authority_generation", 0)
        claimed_session = record.get(
            "claim_authority_session_fingerprint",
            work_order.get("transport_session_fingerprint"),
        )
        try:
            lease_expires = float(
                record.get("claim_lease_expires_epoch_seconds") or 0
            )
        except (TypeError, ValueError):
            lease_expires = 0
        if (
            active.get("authority_root_id") != claimed_root
            or active.get("generation") != claimed_generation
            or active.get("session_fingerprint") != claimed_session
            or lease_expires <= time.time()
        ):
            raise ValueError(error_code)

    def _assert_claim_authority_ancestor_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        record: dict[str, Any],
        work_order: dict[str, Any],
        active: dict[str, Any],
        error_code: str,
        require_operation_fence: bool = True,
    ) -> None:
        """Verify that an expired claim belongs to this authority lineage.

        A continuation may take over an expired durable claim, but it must not
        turn a merely matching work-order id into authority.  The old claim's
        root, generation and session must resolve to one signed generation of
        the same logical authority.  Crossing generations additionally
        requires the caller to hold the current authority-operation lease;
        this keeps a stale pre-continuation request from using the recovery
        path directly after its window has been revoked.
        """

        work_order_id = str(work_order.get("work_order_id") or "")
        authority_root_id = self._authority_root_id(
            project_id, work_order_id
        )
        claimed_generation = record.get("claim_authority_generation")
        if (
            record.get("claim_authority_contract")
            != "window-role-authority-claim-v1"
            or record.get("claim_authority_root_id") != authority_root_id
            or isinstance(claimed_generation, bool)
            or not isinstance(claimed_generation, int)
            or claimed_generation < 0
            or active.get("authority_root_id") != authority_root_id
            or not isinstance(active.get("generation"), int)
            or claimed_generation > int(active["generation"])
        ):
            raise ValueError(error_code)

        hub_path = directory / "hub.json"
        if not hub_path.is_file():
            raise ValueError(error_code)
        try:
            claimed_binding, verified_current = (
                self._authority_binding_at_generation_unlocked(
                    directory,
                    project_id=project_id,
                    hub=self.read_json(hub_path),
                    work_order=work_order,
                    generation=claimed_generation,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(error_code) from exc
        if verified_current != active:
            raise ValueError(error_code)
        expected_session = claimed_binding.get("session_fingerprint")
        if (
            record.get("claim_authority_session_fingerprint")
            != expected_session
        ):
            raise ValueError(error_code)

        if (
            require_operation_fence
            and claimed_generation < int(active["generation"])
        ):
            operation = _ACTIVE_AUTHORITY_OPERATION.get()
            if (
                not isinstance(operation, dict)
                or operation.get("project_id") != project_id
                or operation.get("work_order_id") != work_order_id
                or operation.get("authority_root_id")
                != authority_root_id
                or operation.get("authority_generation")
                != active.get("generation")
                or operation.get("session_fingerprint")
                != active.get("session_fingerprint")
            ):
                raise ValueError(error_code)

    def _assert_session_role_history_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        hub: dict[str, Any],
        session_fingerprint: str | None,
        role: str,
    ) -> None:
        """Reject one transport session ever crossing project role domains."""

        if not session_fingerprint:
            return
        if role not in {"production", "independent_audit", "repair"}:
            raise ValueError("session_role_history_invalid: unsupported role")
        conflicting_roles: set[str] = set()
        work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
        for work_order in (
            work_orders if isinstance(work_orders, list) else []
        ):
            if (
                isinstance(work_order, dict)
                and work_order.get("transport_session_fingerprint")
                == session_fingerprint
                and work_order.get("server_actor_role") != role
            ):
                conflicting_roles.add(
                    str(work_order.get("server_actor_role") or "unknown")
                )

        raw_heads = hub.get("active_authority_bindings", [])
        if not isinstance(raw_heads, list):
            raise ValueError(
                "authority_continuation_head_conflict: active heads must be an array"
            )
        for head in raw_heads:
            if (
                isinstance(head, dict)
                and head.get("session_fingerprint") == session_fingerprint
                and head.get("role") != role
            ):
                conflicting_roles.add(str(head.get("role") or "unknown"))

        for record_path in sorted(
            (directory / "authority_continuations").glob("*.json")
        ):
            record = self.read_json(record_path)
            if not isinstance(record, dict):
                raise ValueError("authority_continuation_record_conflict")
            self._verify_record_integrity(record, record_id=record_path.stem)
            self._verify_project_record_primary_id(
                "authority_continuations",
                record,
                record_id=record_path.stem,
            )
            if (
                record.get("project_id") != project_id
                or not self.project_record_is_signed(record)
            ):
                raise ValueError("authority_continuation_record_conflict")
            record_role = str(record.get("role") or "")
            bindings = [record.get("active_binding")]
            previous_binding = record.get("previous_binding")
            if isinstance(previous_binding, dict):
                bindings.append(previous_binding)
            if any(
                isinstance(binding, dict)
                and binding.get("session_fingerprint")
                == session_fingerprint
                for binding in bindings
            ) and record_role != role:
                conflicting_roles.add(record_role or "unknown")
        if conflicting_roles:
            raise ValueError(
                "session_role_separation_required: session has another project role history"
            )

    def assert_session_role_available(
        self,
        namespace: str,
        project_id: str,
        *,
        session_fingerprint: str | None,
        role: str,
    ) -> None:
        """Read-only locked wrapper for role-continuation planning."""

        directory = self.project_dir(namespace, project_id)
        with self.lock(f"{namespace}-{project_id}"):
            hub_path = directory / "hub.json"
            if not hub_path.is_file():
                raise ValueError(f"unknown project_id: {project_id}")
            hub = self.read_json(hub_path)
            self._assert_session_role_history_unlocked(
                directory,
                project_id=project_id,
                hub=hub,
                session_fingerprint=session_fingerprint,
                role=role,
            )

    def _read_authority_operation_lease_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        authority_root_id: str,
    ) -> dict[str, Any] | None:
        lease_path = self._authority_operation_lease_path(
            directory, authority_root_id
        )
        if not lease_path.is_file():
            return None
        lease = self.read_json(lease_path)
        lease_id = lease_path.stem
        if not isinstance(lease, dict):
            raise ValueError("authority_operation_lease_tampered")
        self._verify_record_integrity(lease, record_id=lease_id)
        # Authority-operation leases were introduced by the continuation
        # contract and therefore have no legitimate unsigned legacy form.
        # Accepting an unsigned replacement here would let a later write wash
        # it into a freshly signed lease.
        if not self.project_record_is_signed(lease):
            raise ValueError("authority_operation_lease_tampered")
        if (
            lease.get("lease_id") != lease_id
            or lease.get("record_id") != lease_id
            or lease.get("project_id") != project_id
            or lease.get("authority_root_id") != authority_root_id
            or lease.get("status") != "active"
        ):
            raise ValueError("authority_operation_lease_tampered")
        return lease

    def _assert_authority_operation_context_unlocked(
        self,
        directory: Path,
        *,
        namespace: str,
        project_id: str,
        operation: dict[str, Any],
    ) -> None:
        if (
            operation.get("store_root") != str(self.root)
            or operation.get("namespace") != namespace
            or operation.get("project_id") != project_id
        ):
            raise ValueError("authority_operation_scope_conflict")
        authority_root_id = safe_id(
            operation.get("authority_root_id"), "authority_root_id"
        )
        lease = self._read_authority_operation_lease_unlocked(
            directory,
            project_id=project_id,
            authority_root_id=authority_root_id,
        )
        holders = lease.get("holders") if isinstance(lease, dict) else None
        holder_tokens = {
            str(row.get("claim_token") or "")
            for row in (holders if isinstance(holders, list) else [])
            if isinstance(row, dict)
        }
        if isinstance(lease, dict) and not holder_tokens and lease.get(
            "claim_token"
        ):
            holder_tokens.add(str(lease.get("claim_token")))
        if (
            lease is None
            or operation.get("claim_token") not in holder_tokens
            or lease.get("work_order_id") != operation.get("work_order_id")
            or lease.get("authority_generation")
            != operation.get("authority_generation")
            or lease.get("session_fingerprint")
            != operation.get("session_fingerprint")
        ):
            raise ValueError("authority_operation_fence_revoked")

        hub_path = directory / "hub.json"
        if not hub_path.is_file():
            raise ValueError(f"unknown project_id: {project_id}")
        hub = self.read_json(hub_path)
        work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
        matches = [
            row
            for row in (work_orders if isinstance(work_orders, list) else [])
            if isinstance(row, dict)
            and row.get("work_order_id") == operation.get("work_order_id")
        ]
        if len(matches) != 1:
            raise ValueError("authority_operation_fence_revoked")
        active = self._active_authority_binding_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=matches[0],
        )
        if (
            active.get("authority_root_id") != authority_root_id
            or active.get("generation") != operation.get("authority_generation")
            or active.get("session_fingerprint")
            != operation.get("session_fingerprint")
        ):
            raise ValueError("authority_operation_fence_revoked")

        now = time.time()
        lease_seconds = int(lease.get("claim_lease_seconds") or 0)
        if not 1 <= lease_seconds <= MAX_AUTHORITY_OPERATION_LEASE_SECONDS:
            raise ValueError("authority_operation_lease_tampered")
        lease["claim_lease_expires_epoch_seconds"] = now + lease_seconds
        lease["claim_lease_expires_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lease_seconds)
        )
        lease["last_renewed_at"] = utc_stamp()
        lease["integrity_hmac"] = self._record_integrity_hmac(lease)
        self.atomic_write_json(
            self._authority_operation_lease_path(directory, authority_root_id),
            lease,
        )

    def claim_authority_operation(
        self,
        namespace: str,
        project_id: str,
        *,
        work_order_id: str,
        expected_authority_root_id: str,
        expected_authority_generation: int,
        expected_session_fingerprint: str | None,
        lease_seconds: int = DEFAULT_AUTHORITY_OPERATION_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Acquire a short-lived, signed fence for one role-bound operation."""

        safe_id(work_order_id, "work_order_id")
        safe_id(expected_authority_root_id, "authority_root_id")
        if (
            isinstance(expected_authority_generation, bool)
            or not isinstance(expected_authority_generation, int)
            or expected_authority_generation < 0
        ):
            raise ValueError("authority_operation_generation_invalid")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= MAX_AUTHORITY_OPERATION_LEASE_SECONDS
        ):
            raise ValueError("authority_operation_lease_invalid")
        directory = self.project_dir(namespace, project_id)
        with self.project_write_lock(namespace, project_id):
            hub_path = directory / "hub.json"
            if not hub_path.is_file():
                raise ValueError(f"unknown project_id: {project_id}")
            hub = self.read_json(hub_path)
            work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
            matches = [
                row
                for row in (work_orders if isinstance(work_orders, list) else [])
                if isinstance(row, dict)
                and row.get("work_order_id") == work_order_id
            ]
            if len(matches) != 1:
                raise ValueError("authority_operation_work_order_invalid")
            active = self._active_authority_binding_unlocked(
                directory,
                project_id=project_id,
                hub=hub,
                work_order=matches[0],
            )
            if (
                active.get("authority_root_id")
                != expected_authority_root_id
                or active.get("generation")
                != expected_authority_generation
                or active.get("session_fingerprint")
                != expected_session_fingerprint
            ):
                raise ValueError("authority_operation_fence_stale")

            existing = self._read_authority_operation_lease_unlocked(
                directory,
                project_id=project_id,
                authority_root_id=expected_authority_root_id,
            )
            now = time.time()
            lease_id = self._authority_operation_lease_id(
                expected_authority_root_id
            )
            claim_token = secrets.token_hex(24)
            if existing is not None and float(
                existing.get("claim_lease_expires_epoch_seconds") or 0
            ) > now:
                if (
                    existing.get("work_order_id") != work_order_id
                    or existing.get("authority_generation")
                    != expected_authority_generation
                    or existing.get("session_fingerprint")
                    != expected_session_fingerprint
                ):
                    raise ValueError("authority_operation_in_progress")
                raise ValueError("authority_operation_in_progress")
            else:
                lease = {
                    "lease_id": lease_id,
                    "record_id": lease_id,
                    "project_id": project_id,
                    "authority_root_id": expected_authority_root_id,
                    "work_order_id": work_order_id,
                    "authority_generation": expected_authority_generation,
                    "session_fingerprint": expected_session_fingerprint,
                    "claim_token": claim_token,
                    "holders": [
                        {"claim_token": claim_token, "joined_at": utc_stamp()}
                    ],
                    "claim_epoch_seconds": now,
                    "claim_lease_seconds": lease_seconds,
                    "claim_lease_expires_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lease_seconds)
                    ),
                    "claim_lease_expires_epoch_seconds": now + lease_seconds,
                    "status": "active",
                    "created_at": utc_stamp(),
                }
            lease["integrity_hmac"] = self._record_integrity_hmac(lease)
            self.atomic_write_json(
                self._authority_operation_lease_path(
                    directory, expected_authority_root_id
                ),
                lease,
            )
            return {
                **copy.deepcopy(lease),
                "claim_token": claim_token,
                "namespace": namespace,
                "store_root": str(self.root),
            }

    def release_authority_operation(
        self,
        namespace: str,
        project_id: str,
        *,
        authority_root_id: str,
        claim_token: str,
    ) -> None:
        directory = self.project_dir(namespace, project_id)
        with self.project_write_lock(namespace, project_id):
            lease = self._read_authority_operation_lease_unlocked(
                directory,
                project_id=project_id,
                authority_root_id=authority_root_id,
            )
            if lease is None:
                return
            holders = lease.get("holders")
            if not isinstance(holders, list):
                holders = [
                    {
                        "claim_token": lease.get("claim_token"),
                        "joined_at": lease.get("created_at"),
                    }
                ]
            remaining = [
                row
                for row in holders
                if isinstance(row, dict)
                and row.get("claim_token") != claim_token
            ]
            if len(remaining) == len(holders):
                raise ValueError("authority_operation_claim_owner_mismatch")
            lease_path = self._authority_operation_lease_path(
                directory, authority_root_id
            )
            if not remaining:
                lease_path.unlink()
            else:
                lease["holders"] = remaining
                lease["claim_token"] = remaining[0]["claim_token"]
                lease["last_released_at"] = utc_stamp()
                lease["integrity_hmac"] = self._record_integrity_hmac(lease)
                self.atomic_write_json(lease_path, lease)

    @contextlib.contextmanager
    def bind_authority_operation(
        self, operation: dict[str, Any]
    ) -> Iterator[None]:
        token = _ACTIVE_AUTHORITY_OPERATION.set(copy.deepcopy(operation))
        try:
            yield
        finally:
            _ACTIVE_AUTHORITY_OPERATION.reset(token)

    def current_authority_operation(self) -> dict[str, Any] | None:
        operation = _ACTIVE_AUTHORITY_OPERATION.get()
        return copy.deepcopy(operation) if isinstance(operation, dict) else None

    def assert_authority_continuation_quiescent(
        self,
        namespace: str,
        project_id: str,
        authority_root_id: str,
        work_order_id: str,
    ) -> None:
        """Read-only check used by continuation planning."""

        directory = self.project_dir(namespace, project_id)
        with self.lock(f"{namespace}-{project_id}"):
            lease = self._read_authority_operation_lease_unlocked(
                directory,
                project_id=project_id,
                authority_root_id=authority_root_id,
            )
            if lease is not None and float(
                lease.get("claim_lease_expires_epoch_seconds") or 0
            ) > time.time():
                raise ValueError(
                    "role_continuation_operation_in_progress: wait for the current role operation or its lease expiry"
                )
            self._assert_no_live_authority_claims_unlocked(
                directory,
                project_id=project_id,
                work_order_id=work_order_id,
            )

    def _assert_no_live_authority_claims_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        work_order_id: str,
    ) -> None:
        """Reject rotation while the current role owns a live durable claim."""

        now = time.time()
        for handoff_path in sorted((directory / "handoffs").glob("*.json")):
            handoff = self.read_json(handoff_path)
            if not isinstance(handoff, dict):
                raise ValueError("handoff_state_conflict: signed handoff is invalid")
            self._verify_record_integrity(
                handoff, record_id=handoff_path.stem
            )
            if (
                handoff.get("status") != "consuming"
                or handoff.get("claimed_by_work_order_id") != work_order_id
            ):
                continue
            try:
                expires = float(
                    handoff.get("claim_lease_expires_epoch_seconds") or 0
                )
            except (TypeError, ValueError):
                expires = 0
            if expires <= 0 or expires > now:
                raise ValueError(
                    "role_continuation_claim_in_progress: handoff claim is still owned by this authority"
                )

        for package_path in sorted(
            (directory / "repair_packages").glob("*.json")
        ):
            package = self.read_json(package_path)
            if not isinstance(package, dict):
                raise ValueError(
                    "repair_package_claim_conflict: signed package is invalid"
                )
            self._verify_record_integrity(
                package, record_id=package_path.stem
            )
            if (
                package.get("status") != "consuming"
                or package.get("consumed_by_work_order_id") != work_order_id
            ):
                continue
            try:
                expires = float(
                    package.get("claim_lease_expires_epoch_seconds") or 0
                )
            except (TypeError, ValueError):
                expires = 0
            if expires <= 0 or expires > now:
                raise ValueError(
                    "role_continuation_claim_in_progress: repair package claim is still owned by this authority"
                )

        for preflight_path in sorted(
            (directory / "draft_preflights").glob("*.json")
        ):
            preflight = self.read_json(preflight_path)
            if not isinstance(preflight, dict):
                raise ValueError("draft_preflight_commit_claim_conflict")
            self._verify_record_integrity(
                preflight, record_id=preflight_path.stem
            )
            claim = preflight.get("commit_claim")
            if (
                preflight.get("work_order_id") != work_order_id
                or not isinstance(claim, dict)
                or claim.get("state") == "completed"
            ):
                continue
            try:
                expires = float(claim.get("lease_expires_epoch_seconds") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires <= 0 or expires > now:
                raise ValueError(
                    "role_continuation_claim_in_progress: draft commit claim is still owned by this authority"
                )

    def _assert_authority_continuation_quiescent_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        authority_root_id: str,
        work_order_id: str,
    ) -> None:
        lease = self._read_authority_operation_lease_unlocked(
            directory,
            project_id=project_id,
            authority_root_id=authority_root_id,
        )
        self._assert_no_live_authority_claims_unlocked(
            directory,
            project_id=project_id,
            work_order_id=work_order_id,
        )
        if lease is not None:
            if float(
                lease.get("claim_lease_expires_epoch_seconds") or 0
            ) > time.time():
                raise ValueError(
                    "role_continuation_operation_in_progress: active role operation is fenced"
                )
            # Keep an expired lease byte-identical until a new authority
            # operation atomically replaces it.  Continuation rejection paths
            # must remain zero-write even when another durable claim appears
            # between plan and execute.

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
            active_operation = _ACTIVE_AUTHORITY_OPERATION.get()
            if isinstance(active_operation, dict):
                self._assert_authority_operation_context_unlocked(
                    directory,
                    namespace=namespace,
                    project_id=project_id,
                    operation=active_operation,
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
        payload_sha256: str | None = None,
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
        if payload_sha256 is not None and (
            not isinstance(payload_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", payload_sha256)
        ):
            raise ValueError("invalid draft_preflight commit payload_sha256")
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
            bound_payload = record.get("commit_payload_sha256")
            if bound_payload not in {None, payload_sha256}:
                raise ValueError("draft_preflight_commit_payload_conflict")
            if record.get("consumed_by_commit"):
                return {"claim_status": "consumed", "record": record}
            claim = record.get("commit_claim")
            if isinstance(claim, dict):
                if claim.get("request_sha256") != request_sha256:
                    raise ValueError("draft_preflight_commit_claim_conflict")
                if claim.get("payload_sha256") != payload_sha256:
                    raise ValueError("draft_preflight_commit_payload_conflict")
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
            work_order_id = safe_id(
                record.get("work_order_id"), "work_order_id"
            )
            hub = self.read_json(self.project_dir(namespace, project_id) / "hub.json")
            work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
            work_order_matches = [
                row
                for row in (work_orders if isinstance(work_orders, list) else [])
                if isinstance(row, dict)
                and row.get("work_order_id") == work_order_id
            ]
            if len(work_order_matches) != 1:
                raise ValueError("draft_preflight_commit_authority_invalid")
            authority = self._active_authority_binding_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                hub=hub,
                work_order=work_order_matches[0],
            )
            if record.get("session_fingerprint") != authority.get(
                "session_fingerprint"
            ):
                raise ValueError(
                    "draft_preflight_commit_claim_revoked: preflight belongs to another window generation"
                )
            issuance_authority = {
                "contract": "draft-commit-issuance-authority-v0511",
                "authority_root_id": authority.get("authority_root_id"),
                "authority_epoch_id": authority.get("authority_epoch_id"),
                "authority_generation": authority.get("generation"),
                "authority_continuation_id": authority.get(
                    "continuation_id"
                ),
                "authority_session_fingerprint": authority.get(
                    "session_fingerprint"
                ),
                "work_order_id": work_order_id,
            }
            issuance_authority_sha256 = self.project_state_sha256(
                issuance_authority
            )
            record["commit_claim"] = {
                "claim_token": token,
                "request_sha256": request_sha256,
                "payload_sha256": payload_sha256,
                "issuance_authority_sha256": issuance_authority_sha256,
                "generation": generation,
                "authority_root_id": authority.get("authority_root_id"),
                "authority_generation": authority.get("generation"),
                "authority_session_fingerprint": authority.get(
                    "session_fingerprint"
                ),
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
            record["commit_payload_sha256"] = payload_sha256
            record["commit_issuance_authority"] = issuance_authority
            record[
                "commit_issuance_authority_sha256"
            ] = issuance_authority_sha256
            record["commit_claim_generation"] = generation
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return {
                "claim_status": "claimed",
                "claim_token": token,
                "claim_generation": generation,
                "record": record,
            }

    def _assert_draft_preflight_claim_authority_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        record: dict[str, Any],
        claim: dict[str, Any],
    ) -> None:
        work_order_id = safe_id(record.get("work_order_id"), "work_order_id")
        hub = self.read_json(directory / "hub.json")
        work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
        matches = [
            row
            for row in (work_orders if isinstance(work_orders, list) else [])
            if isinstance(row, dict)
            and row.get("work_order_id") == work_order_id
        ]
        if len(matches) != 1:
            raise ValueError("draft_preflight_commit_claim_revoked")
        active = self._active_authority_binding_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=matches[0],
        )
        claimed_root = claim.get("authority_root_id") or self._authority_root_id(
            project_id, work_order_id
        )
        claimed_generation = claim.get("authority_generation", 0)
        claimed_session = claim.get(
            "authority_session_fingerprint",
            record.get("session_fingerprint"),
        )
        if (
            active.get("authority_root_id") != claimed_root
            or active.get("generation") != claimed_generation
            or active.get("session_fingerprint") != claimed_session
        ):
            raise ValueError(
                "draft_preflight_commit_claim_revoked: claim belongs to an inactive window generation"
            )

    def _descendant_draft_preflight_recovery_context_unlocked(
        self,
        directory: Path,
        *,
        project_id: str,
        draft_preflight_id: str,
        payload_sha256: str,
    ) -> dict[str, Any]:
        """Authorize a read-only takeover of one published commit tail.

        This is intentionally narrower than reclaiming a draft certificate.
        The original claim must be expired, must have reached the signed
        recovery-ready journal stage, and must belong to a strict ancestor of
        the caller's currently fenced authority generation.
        """

        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload_sha256 or "")):
            raise ValueError("draft_preflight_commit_recovery_payload_invalid")
        path = directory / "draft_preflights" / f"{clean_id}.json"
        if not path.is_file():
            raise ValueError(f"unknown draft_preflight_id: {clean_id}")
        record = self.read_json(path)
        self._verify_record_integrity(record, record_id=clean_id)
        if not self.project_record_is_signed(record):
            raise ValueError("draft_preflight_signature_required")
        if record.get("consumed_by_commit"):
            raise ValueError("draft_preflight_already_consumed")
        if record.get("commit_payload_sha256") != payload_sha256:
            raise ValueError("draft_preflight_commit_recovery_payload_conflict")
        claim = record.get("commit_claim")
        progress = record.get("commit_progress")
        descriptor = (
            progress.get("recovery_descriptor")
            if isinstance(progress, dict)
            else None
        )
        issuance = record.get("commit_issuance_authority")
        if (
            not isinstance(claim, dict)
            or claim.get("state") != "claimed"
            or claim.get("payload_sha256") != payload_sha256
            or not isinstance(progress, dict)
            or not isinstance(descriptor, dict)
            or not isinstance(issuance, dict)
        ):
            raise ValueError("draft_preflight_commit_recovery_not_ready")
        descriptor_sha256 = descriptor.get("descriptor_sha256")
        descriptor_body = {
            key: copy.deepcopy(value)
            for key, value in descriptor.items()
            if key != "descriptor_sha256"
        }
        if (
            descriptor.get("contract")
            != "draft-commit-descendant-recovery-v0511"
            or descriptor_sha256 != self.project_state_sha256(descriptor_body)
            or descriptor.get("payload_sha256") != payload_sha256
            or descriptor.get("request_sha256")
            != record.get("commit_request_sha256")
            or progress.get("payload_sha256") != payload_sha256
            or progress.get("request_sha256")
            != record.get("commit_request_sha256")
            or claim.get("request_sha256")
            != record.get("commit_request_sha256")
        ):
            raise ValueError("draft_preflight_commit_recovery_journal_conflict")
        issuance_sha256 = self.project_state_sha256(issuance)
        if (
            issuance.get("contract")
            != "draft-commit-issuance-authority-v0511"
            or record.get("commit_issuance_authority_sha256")
            != issuance_sha256
            or claim.get("issuance_authority_sha256") != issuance_sha256
            or progress.get("issuance_authority_sha256") != issuance_sha256
            or descriptor.get("issuance_authority_sha256")
            != issuance_sha256
        ):
            raise ValueError("draft_preflight_commit_recovery_authority_conflict")

        work_order_id = safe_id(record.get("work_order_id"), "work_order_id")
        hub = self.read_json(directory / "hub.json")
        work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
        matches = [
            row
            for row in (work_orders if isinstance(work_orders, list) else [])
            if isinstance(row, dict)
            and row.get("work_order_id") == work_order_id
        ]
        if len(matches) != 1:
            raise ValueError("draft_preflight_commit_recovery_authority_invalid")
        work_order = matches[0]
        active = self._active_authority_binding_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=work_order,
        )
        claimed_generation = claim.get("authority_generation")
        if (
            isinstance(claimed_generation, bool)
            or not isinstance(claimed_generation, int)
            or claimed_generation < 0
            or not isinstance(active.get("generation"), int)
            or int(active["generation"]) <= claimed_generation
        ):
            raise ValueError("draft_preflight_commit_recovery_descendant_required")
        ancestor, current = self._authority_binding_at_generation_unlocked(
            directory,
            project_id=project_id,
            hub=hub,
            work_order=work_order,
            generation=claimed_generation,
        )
        expected_issuance = {
            "contract": "draft-commit-issuance-authority-v0511",
            "authority_root_id": ancestor.get("authority_root_id"),
            "authority_epoch_id": ancestor.get("authority_epoch_id"),
            "authority_generation": ancestor.get("generation"),
            "authority_continuation_id": ancestor.get("continuation_id"),
            "authority_session_fingerprint": ancestor.get(
                "session_fingerprint"
            ),
            "work_order_id": work_order_id,
        }
        try:
            lease_expires = float(claim.get("lease_expires_epoch_seconds") or 0)
        except (TypeError, ValueError):
            lease_expires = 0
        if (
            issuance != expected_issuance
            or claim.get("authority_root_id")
            != ancestor.get("authority_root_id")
            or claim.get("authority_generation")
            != ancestor.get("generation")
            or claim.get("authority_session_fingerprint")
            != ancestor.get("session_fingerprint")
            or current != active
            or lease_expires <= 0
            or lease_expires > time.time()
        ):
            raise ValueError("draft_preflight_commit_recovery_authority_revoked")
        operation = _ACTIVE_AUTHORITY_OPERATION.get()
        if (
            not isinstance(operation, dict)
            or operation.get("project_id") != project_id
            or operation.get("work_order_id") != work_order_id
            or operation.get("authority_root_id")
            != active.get("authority_root_id")
            or operation.get("authority_generation")
            != active.get("generation")
            or operation.get("session_fingerprint")
            != active.get("session_fingerprint")
        ):
            raise ValueError("draft_preflight_commit_recovery_operation_required")
        return {
            "record": copy.deepcopy(record),
            "claim": copy.deepcopy(claim),
            "progress": copy.deepcopy(progress),
            "descriptor": copy.deepcopy(descriptor),
            "hub": copy.deepcopy(hub),
            "work_order": copy.deepcopy(work_order),
            "active_authority": copy.deepcopy(active),
        }

    def descendant_draft_preflight_commit_recovery_context(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        payload_sha256: str,
    ) -> dict[str, Any]:
        """Return a signed, zero-write recovery context for service checks."""

        directory = self.project_dir(namespace, project_id)
        with self.lock(f"{namespace}-{project_id}"):
            return self._descendant_draft_preflight_recovery_context_unlocked(
                directory,
                project_id=project_id,
                draft_preflight_id=draft_preflight_id,
                payload_sha256=payload_sha256,
            )

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
            self._assert_draft_preflight_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                claim=claim,
            )
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
            self._assert_draft_preflight_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                claim=claim,
            )
            journal = record.setdefault("commit_progress", {})
            if not isinstance(journal, dict):
                journal = {}
                record["commit_progress"] = journal
            journal.update(dict(progress or {}))
            journal.update(
                {
                    "request_sha256": claim.get("request_sha256"),
                    "payload_sha256": claim.get("payload_sha256"),
                    "issuance_authority_sha256": claim.get(
                        "issuance_authority_sha256"
                    ),
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
            self._assert_draft_preflight_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                claim=claim,
            )
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

    def complete_descendant_draft_preflight_commit_recovery(
        self,
        namespace: str,
        project_id: str,
        draft_preflight_id: str,
        *,
        payload_sha256: str,
        expected_state_sha256: dict[str, str],
        telemetry_value: dict[str, Any],
        consumed_by_commit: dict[str, Any],
    ) -> dict[str, Any]:
        """Finish only the missing tail of an ancestor's published commit.

        All immutable publication records and both Hub/Ledger snapshots are
        compare-and-set under the project lock before the first write.  The
        write order (telemetry, Hub projection, signed preflight completion)
        is restart-safe: any prefix can be replayed only when byte-identical.
        """

        required_hashes = {
            "preflight",
            "hub",
            "ledger",
            "artifact_meta",
            "canonical_meta",
            "certificate",
            "handoff",
        }
        if (
            not isinstance(expected_state_sha256, dict)
            or set(expected_state_sha256) != required_hashes
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in expected_state_sha256.values()
            )
        ):
            raise ValueError("draft_preflight_commit_recovery_state_invalid")
        directory = self.project_dir(namespace, project_id)
        clean_id = safe_id(draft_preflight_id, "draft_preflight_id")
        with self.project_write_lock(namespace, project_id):
            context = self._descendant_draft_preflight_recovery_context_unlocked(
                directory,
                project_id=project_id,
                draft_preflight_id=clean_id,
                payload_sha256=payload_sha256,
            )
            record = context["record"]
            descriptor = context["descriptor"]
            hub = context["hub"]
            ledger = self.read_json(directory / "ledger.json")
            artifact_id = safe_id(
                descriptor.get("artifact_id"), "artifact_id"
            )
            canonical_artifact_id = safe_id(
                descriptor.get("canonical_json_artifact_id"),
                "canonical_artifact_id",
            )
            certificate_id = safe_id(
                descriptor.get("certificate_id"), "certificate_id"
            )
            handoff_id = safe_id(
                descriptor.get("handoff_id"), "handoff_id"
            )
            artifact_meta_path = (
                directory / "artifacts" / f"{artifact_id}.meta.json"
            )
            canonical_meta_path = (
                directory
                / "artifacts"
                / f"{canonical_artifact_id}.meta.json"
            )
            certificate_path = (
                directory / "certificates" / f"{certificate_id}.json"
            )
            handoff_path = directory / "handoffs" / f"{handoff_id}.json"
            for path in (
                artifact_meta_path,
                canonical_meta_path,
                certificate_path,
                handoff_path,
            ):
                if not path.is_file():
                    raise ValueError(
                        "draft_preflight_commit_recovery_publication_missing"
                    )
            artifact_meta = self.read_json(artifact_meta_path)
            canonical_meta = self.read_json(canonical_meta_path)
            certificate = self.read_json(certificate_path)
            handoff = self.read_json(handoff_path)
            current_hashes = {
                "preflight": self.project_state_sha256(record),
                "hub": self.project_state_sha256(hub),
                "ledger": self.project_state_sha256(ledger),
                "artifact_meta": self.project_state_sha256(artifact_meta),
                "canonical_meta": self.project_state_sha256(canonical_meta),
                "certificate": self.project_state_sha256(certificate),
                "handoff": self.project_state_sha256(handoff),
            }
            if current_hashes != expected_state_sha256:
                raise ValueError(
                    "draft_preflight_commit_recovery_state_changed"
                )
            self._verify_record_integrity(
                certificate, record_id=certificate_id
            )
            self._verify_record_integrity(handoff, record_id=handoff_id)
            if (
                not self.project_record_is_signed(certificate)
                or not self.project_record_is_signed(handoff)
            ):
                raise ValueError(
                    "draft_preflight_commit_recovery_signature_required"
                )
            for artifact_meta_record in (artifact_meta, canonical_meta):
                relative_path = str(
                    artifact_meta_record.get("relative_path") or ""
                )
                payload_path = (directory / relative_path).resolve()
                if (
                    directory.resolve() not in payload_path.parents
                    or not payload_path.is_file()
                    or sha256_bytes(payload_path.read_bytes())
                    != artifact_meta_record.get("fingerprint")
                ):
                    raise ValueError(
                        "draft_preflight_commit_recovery_artifact_changed"
                    )

            telemetry_blueprint = descriptor.get("telemetry")
            if not isinstance(telemetry_blueprint, dict):
                raise ValueError(
                    "draft_preflight_commit_recovery_telemetry_invalid"
                )
            telemetry_id = safe_id(
                telemetry_blueprint.get("telemetry_id"), "telemetry_id"
            )
            immutable_telemetry = {
                "telemetry_id": telemetry_id,
                "trace_id": telemetry_id,
                "operation": telemetry_blueprint.get("operation"),
                "artifact_id": artifact_id,
                "window_role": telemetry_blueprint.get("window_role"),
                "task_type": telemetry_blueprint.get("task_type"),
                "input_fingerprints": telemetry_blueprint.get(
                    "input_fingerprints"
                ),
                "input_payload_bytes": telemetry_blueprint.get(
                    "input_payload_bytes"
                ),
                "output_payload_bytes": telemetry_blueprint.get(
                    "output_payload_bytes"
                ),
                "affected_shot_count": telemetry_blueprint.get(
                    "affected_shot_count"
                ),
                "preserved_semantic_count": telemetry_blueprint.get(
                    "preserved_semantic_count"
                ),
                "materialization_passes": 1,
            }
            stage_name_map = {
                "validate": "validation",
                "machine_validation": "validation",
                "serialize_and_persist": "export_projection",
                "canonical_load": "canonicalization",
                "base_load": "delta_base_load",
                "scope_verify": "delta_scope_check",
                "delta_merge": "delta_merge",
                "materialize": "canonicalization",
                "ledger": "ledger_handoff",
            }
            raw_stages_ms = telemetry_blueprint.get("stages_ms")
            if not isinstance(raw_stages_ms, dict) or not raw_stages_ms:
                raise ValueError(
                    "draft_preflight_commit_recovery_telemetry_invalid"
                )
            try:
                expected_stages_ms = {
                    str(key): round(float(value), 3)
                    for key, value in raw_stages_ms.items()
                }
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "draft_preflight_commit_recovery_telemetry_invalid"
                ) from exc
            expected_stage_rows = [
                {
                    "stage": stage_name_map.get(key, "validation"),
                    "duration_ms": max(0, int(round(duration))),
                    "shot_count": int(telemetry_blueprint.get("shot_count") or 0),
                    "batch_count": 0,
                    "cache_status": "miss",
                    "cache_reason": telemetry_blueprint.get("cache_reason"),
                    "error_status": "none",
                }
                for key, duration in expected_stages_ms.items()
            ]
            telemetry_path = directory / "telemetry" / f"{telemetry_id}.json"
            if telemetry_path.is_file():
                telemetry = self.read_json(telemetry_path)
                self._verify_record_integrity(
                    telemetry, record_id=telemetry_id
                )
                if not self.project_record_is_signed(telemetry):
                    raise ValueError(
                        "draft_preflight_commit_recovery_telemetry_unsigned"
                    )
            else:
                telemetry = dict(telemetry_value)
                if (
                    telemetry.get("telemetry_id") != telemetry_id
                    or telemetry.get("trace_id") != telemetry_id
                    or telemetry.get("artifact_id") != artifact_id
                ):
                    raise ValueError(
                        "draft_preflight_commit_recovery_telemetry_conflict"
                    )
                telemetry["record_id"] = telemetry_id
                telemetry["project_id"] = project_id
                telemetry.setdefault("created_at", utc_stamp())
                telemetry["integrity_hmac"] = self._record_integrity_hmac(
                    telemetry
                )
            if any(
                telemetry.get(key) != value
                for key, value in immutable_telemetry.items()
            ) or telemetry.get("stages_ms") != expected_stages_ms:
                raise ValueError(
                    "draft_preflight_commit_recovery_telemetry_conflict"
                )
            actual_stage_rows = telemetry.get("stages")
            if (
                not isinstance(actual_stage_rows, list)
                or len(actual_stage_rows) != len(expected_stage_rows)
                or any(
                    any(actual.get(key) != value for key, value in expected.items())
                    for actual, expected in zip(
                        actual_stage_rows, expected_stage_rows
                    )
                    if isinstance(actual, dict)
                )
                or any(not isinstance(row, dict) for row in actual_stage_rows)
                or telemetry.get("cache_hit") is not False
                or telemetry.get("measurement_scope") != "server_only"
                or telemetry.get("legacy_generation_calls") != 0
                or telemetry.get("seedance_batches_generated") != 0
                or telemetry.get("model_authored_derived_chars") != 0
            ):
                raise ValueError(
                    "draft_preflight_commit_recovery_telemetry_conflict"
                )
            if not telemetry_path.is_file():
                self.atomic_write_json(telemetry_path, telemetry)

            performance_traces = hub.setdefault("performance_traces", [])
            if not isinstance(performance_traces, list):
                raise ValueError(
                    "draft_preflight_commit_recovery_hub_conflict"
                )
            trace_matches = [
                row
                for row in performance_traces
                if isinstance(row, dict)
                and row.get("telemetry_id") == telemetry_id
            ]
            if len(trace_matches) > 1 or (
                trace_matches and trace_matches[0] != telemetry
            ):
                raise ValueError(
                    "draft_preflight_commit_recovery_hub_conflict"
                )
            if not trace_matches:
                performance_traces.append(copy.deepcopy(telemetry))
                self.atomic_write_json(directory / "hub.json", hub)

            active = context["active_authority"]
            completion = dict(consumed_by_commit)
            expected_completion = descriptor.get("completion")
            if (
                not isinstance(expected_completion, dict)
                or any(
                    completion.get(key) != value
                    for key, value in expected_completion.items()
                )
                or completion.get("trace_id") != telemetry_id
                or completion.get("artifact_id") != artifact_id
                or completion.get("canonical_json_artifact_id")
                != canonical_artifact_id
            ):
                raise ValueError(
                    "draft_preflight_commit_recovery_completion_conflict"
                )
            completion.update(
                {
                    "commit_payload_sha256": payload_sha256,
                    "recovery_authority_contract": (
                        "draft-commit-descendant-recovery-v0511"
                    ),
                    "completion_authority_root_id": active.get(
                        "authority_root_id"
                    ),
                    "completion_authority_generation": active.get(
                        "generation"
                    ),
                    "completion_authority_session_fingerprint": active.get(
                        "session_fingerprint"
                    ),
                    "issuance_authority_sha256": record.get(
                        "commit_issuance_authority_sha256"
                    ),
                }
            )
            record["consumed_by_commit"] = completion
            record["commit_claim"]["state"] = "completed"
            record["commit_claim"]["completed_at"] = utc_stamp()
            progress = record.setdefault("commit_progress", {})
            progress.update(
                {
                    "stage": "completed",
                    "updated_at": utc_stamp(),
                    "artifact_id": artifact_id,
                    "canonical_json_artifact_id": canonical_artifact_id,
                    "telemetry_id": telemetry_id,
                    "completed_by_authority_generation": active.get(
                        "generation"
                    ),
                }
            )
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(
                directory / "draft_preflights" / f"{clean_id}.json",
                record,
            )
            return {
                "record": copy.deepcopy(record),
                "telemetry": copy.deepcopy(telemetry),
                "active_authority": copy.deepcopy(active),
            }

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

    def append_draft_observation(self, namespace, project_id, value):
        """One project lock covers sequence assignment and immutable append."""
        clean_id = safe_id(value["observation_id"], "record_id")
        directory = self.project_dir(namespace, project_id)
        with self.project_write_lock(namespace, project_id):
            if not (directory / "hub.json").is_file():
                raise ValueError(f"unknown project_id: {project_id}")
            records = [r for r in self.list_project_records_strict(
                namespace, project_id, kind="draft_observations")
                if r.get("lineage_id") == value["lineage_id"]]
            records.sort(key=lambda r: int(r["observation_index"]))
            existing = next((r for r in records if r["observation_id"] == clean_id), None)
            if existing is not None:
                if existing["input_sha256"] != value["input_sha256"] or existing["candidate_snapshot"] != value["candidate_snapshot"]:
                    raise ValueError("draft_observation_identity_conflict")
                return existing
            record = copy.deepcopy(value)
            record.update({"record_id": clean_id, "project_id": project_id,
                           "observation_index": len(records) + 1,
                           "first_observation_id": records[0]["observation_id"] if records else clean_id,
                           "previous_observation_id": records[-1]["observation_id"] if records else None})
            record.setdefault("created_at", utc_stamp())
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(directory / "draft_observations" / f"{clean_id}.json", record)
            return record

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
            if clean_kind in {"draft_observations", "narrative_records", "audit_coverage_collections", "audit_coverage_pages"} and path.exists():
                existing = self.get_project_record(namespace, project_id, kind=clean_kind, record_id=clean_id)
                left = {k: v for k, v in existing.items() if k != "integrity_hmac"}
                right = {k: v for k, v in record.items() if k != "integrity_hmac"}
                if left != right:
                    raise ValueError("draft_observation_immutable: append a new observation")
                return existing
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
        if clean_kind in {"narrative_records", "audit_coverage_collections", "audit_coverage_pages"}:
            raise ValueError("narrative_record_immutable: append a new narrative record")
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

    def commit_authority_continuation(
        self,
        namespace: str,
        project_id: str,
        record: dict[str, Any],
        expected_work_order_snapshot_sha256: str,
    ) -> dict[str, Any]:
        """Append one signed authority generation and publish its active head.

        The work order remains the immutable logical authority.  A continuation
        only rotates its physical window, actor, and transport-session binding.
        The signed record is intentionally durable before the Hub projection:
        if the second replace is interrupted, the exact request can finish the
        same generation, while a different plan for that generation fails
        closed.
        """

        if not isinstance(record, dict):
            raise ValueError("authority_continuation_invalid: record must be an object")
        if not isinstance(expected_work_order_snapshot_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_work_order_snapshot_sha256
        ) is None:
            raise ValueError(
                "authority_continuation_invalid: expected work-order snapshot sha256"
            )

        candidate = copy.deepcopy(record)
        clean_id = safe_id(candidate.get("continuation_id"), "continuation_id")
        authority_root_id = safe_id(
            candidate.get("authority_root_id"), "authority_root_id"
        )
        work_order_id = safe_id(candidate.get("work_order_id"), "work_order_id")
        active_binding = candidate.get("active_binding")
        if not isinstance(active_binding, dict):
            raise ValueError(
                "authority_continuation_invalid: active_binding must be an object"
            )

        binding_fields = (
            "continuation_id",
            "authority_epoch_id",
            "authority_root_id",
            "work_order_id",
            "generation",
            "window_binding_id",
            "actor_binding_id",
            "session_fingerprint",
            "role",
            "operation",
            "mode",
            "authorization",
            "authority_contract_sha256",
            "plan_sha256",
            "status",
        )
        if set(active_binding) != set(binding_fields):
            raise ValueError(
                "authority_continuation_invalid: active_binding fields differ"
            )
        if active_binding.get("continuation_id") != clean_id:
            raise ValueError(
                "authority_continuation_invalid: continuation identity differs"
            )
        safe_id(active_binding.get("authority_epoch_id"), "authority_epoch_id")
        if active_binding.get("authority_root_id") != authority_root_id:
            raise ValueError(
                "authority_continuation_invalid: authority root differs"
            )
        if active_binding.get("work_order_id") != work_order_id:
            raise ValueError(
                "authority_continuation_invalid: work order differs"
            )
        generation = active_binding.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("authority_continuation_invalid: generation")
        if candidate.get("generation", generation) != generation:
            raise ValueError("authority_continuation_invalid: generation differs")
        previous_generation = candidate.get("previous_generation")
        if (
            isinstance(previous_generation, bool)
            or not isinstance(previous_generation, int)
            or previous_generation != generation - 1
        ):
            raise ValueError(
                "authority_continuation_lineage_invalid: previous generation differs"
            )
        previous_continuation_id = candidate.get("previous_continuation_id")
        if generation == 1:
            if previous_generation != 0 or previous_continuation_id is not None:
                raise ValueError(
                    "authority_continuation_lineage_invalid: generation one must descend from generation zero"
                )
        else:
            previous_continuation_id = safe_id(
                previous_continuation_id, "previous_continuation_id"
            )

        window_binding_id = safe_id(
            active_binding.get("window_binding_id"), "window_binding_id"
        )
        actor_binding_id = safe_id(
            active_binding.get("actor_binding_id"), "actor_binding_id"
        )

        def require_sha256(value: Any, field: str) -> str:
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"authority_continuation_invalid: {field}")
            return value

        session_fingerprint = require_sha256(
            active_binding.get("session_fingerprint"), "session_fingerprint"
        )
        authority_contract_sha256 = require_sha256(
            active_binding.get("authority_contract_sha256"),
            "authority_contract_sha256",
        )
        plan_sha256 = require_sha256(
            active_binding.get("plan_sha256"), "plan_sha256"
        )
        for field in ("role", "operation", "mode", "authorization"):
            value = active_binding.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"authority_continuation_invalid: {field}")
        if active_binding.get("role") not in {
            "production",
            "independent_audit",
            "repair",
        }:
            raise ValueError("authority_continuation_invalid: role")
        if active_binding.get("status") != "active":
            raise ValueError("authority_continuation_invalid: status")

        # Duplicate the active binding's safe public fields at record top level.
        # This makes the signed record self-indexing without allowing either
        # representation to drift from the Hub head projection.
        for field in binding_fields:
            if field in candidate and candidate.get(field) != active_binding[field]:
                raise ValueError(
                    f"authority_continuation_invalid: {field} differs"
                )
            candidate[field] = copy.deepcopy(active_binding[field])
        if candidate.get("record_id", clean_id) != clean_id:
            raise ValueError(
                f"project_record_identity_mismatch: {clean_id}"
            )
        if candidate.get("project_id", project_id) != project_id:
            raise ValueError(
                "authority_continuation_invalid: project identity differs"
            )
        if candidate.get(
            "expected_work_order_snapshot_sha256",
            expected_work_order_snapshot_sha256,
        ) != expected_work_order_snapshot_sha256:
            raise ValueError(
                "authority_continuation_invalid: work-order snapshot differs"
            )
        if (
            candidate.get("work_order_snapshot_sha256")
            != expected_work_order_snapshot_sha256
        ):
            raise ValueError(
                "authority_continuation_invalid: signed work-order snapshot differs"
            )
        candidate["record_id"] = clean_id
        candidate["project_id"] = project_id
        candidate["authority_root_id"] = authority_root_id
        candidate["work_order_id"] = work_order_id
        candidate["active_binding"] = copy.deepcopy(active_binding)
        candidate[
            "expected_work_order_snapshot_sha256"
        ] = expected_work_order_snapshot_sha256
        candidate.pop("integrity_hmac", None)
        candidate.pop("idempotent_replay", None)
        candidate.pop("resumed_from_signed_record", None)

        directory = self.project_dir(namespace, project_id)
        hub_path = directory / "hub.json"
        record_path = directory / "authority_continuations" / f"{clean_id}.json"
        with self.project_write_lock(namespace, project_id):
            if not hub_path.is_file():
                raise ValueError(f"unknown project_id: {project_id}")
            hub = self.read_json(hub_path)
            if not isinstance(hub, dict) or hub.get("project_id") != project_id:
                raise ValueError(
                    "authority_continuation_invalid: project Hub identity differs"
                )
            work_orders = hub.get("work_orders")
            if not isinstance(work_orders, list):
                raise ValueError(
                    "authority_continuation_invalid: work_orders must be an array"
                )
            self._assert_session_role_history_unlocked(
                directory,
                project_id=project_id,
                hub=hub,
                session_fingerprint=session_fingerprint,
                role=str(active_binding["role"]),
            )
            work_order_matches = [
                row
                for row in work_orders
                if isinstance(row, dict)
                and row.get("work_order_id") == work_order_id
            ]
            if len(work_order_matches) != 1:
                raise ValueError(
                    "authority_continuation_invalid: work order is missing or ambiguous"
                )
            work_order = work_order_matches[0]
            if (
                self.project_state_sha256(work_order)
                != expected_work_order_snapshot_sha256
            ):
                raise ValueError("authority_continuation_work_order_stale")

            expected_root_id = "AUTH-" + sha256_bytes(
                canonical_json_bytes(
                    {
                        "project_id": project_id,
                        "work_order_id": work_order_id,
                        "contract": "window-role-authority-root-v1",
                    }
                )
            )[:24]
            if authority_root_id != expected_root_id:
                raise ValueError(
                    "authority_continuation_invalid: authority root differs"
                )
            self._assert_authority_continuation_quiescent_unlocked(
                directory,
                project_id=project_id,
                authority_root_id=authority_root_id,
                work_order_id=work_order_id,
            )
            authority_contract = {
                "work_order_id": work_order_id,
                "operation": str(work_order.get("operation") or ""),
                "mode": str(work_order.get("mode") or ""),
                "authorization": str(work_order.get("authorization") or ""),
                "server_actor_role": str(
                    work_order.get("server_actor_role") or ""
                ),
                "targets": copy.deepcopy(work_order.get("targets") or []),
                "source_lock": copy.deepcopy(work_order.get("source_lock") or {}),
                "protected_scope": copy.deepcopy(
                    work_order.get("protected_scope") or []
                ),
                "expected_outputs": copy.deepcopy(
                    work_order.get("expected_outputs") or []
                ),
            }
            if "window_access_contract" in work_order:
                authority_contract["window_access_contract"] = str(
                    work_order.get("window_access_contract") or ""
                )
                authority_contract["window_access_scope"] = str(
                    work_order.get("window_access_scope") or ""
                )
            if authority_contract_sha256 != sha256_bytes(
                canonical_json_bytes(authority_contract)
            ):
                raise ValueError(
                    "authority_continuation_invalid: authority contract differs"
                )
            work_order_binding = {
                "role": work_order.get("server_actor_role"),
                "operation": work_order.get("operation"),
                "mode": work_order.get("mode"),
                "authorization": work_order.get("authorization"),
            }
            if any(
                active_binding[field] != work_order_binding[field]
                for field in work_order_binding
            ):
                raise ValueError(
                    "authority_continuation_invalid: work-order authority differs"
                )

            if "active_authority_bindings" not in hub:
                heads: list[dict[str, Any]] = []
            else:
                raw_heads = hub.get("active_authority_bindings")
                if not isinstance(raw_heads, list):
                    raise ValueError(
                        "authority_continuation_head_conflict: active heads must be an array"
                    )
                heads = copy.deepcopy(raw_heads)
            seen_roots: set[str] = set()
            target_indexes: list[int] = []
            for index, head in enumerate(heads):
                if not isinstance(head, dict):
                    raise ValueError(
                        "authority_continuation_head_conflict: active head is not an object"
                    )
                head_root = safe_id(
                    head.get("authority_root_id"), "authority_root_id"
                )
                if head_root in seen_roots:
                    raise ValueError(
                        "authority_continuation_head_conflict: duplicate authority root"
                    )
                seen_roots.add(head_root)
                if head_root == authority_root_id:
                    target_indexes.append(index)
            if len(target_indexes) > 1:
                raise ValueError(
                    "authority_continuation_head_conflict: duplicate authority root"
                )

            current_head = (
                heads[target_indexes[0]] if target_indexes else None
            )
            current_generation = 0
            current_continuation_id: str | None = None
            prior_physical_binding = {
                "window_binding_id": work_order.get("server_window_binding_id"),
                "actor_binding_id": work_order.get("server_actor_binding_id"),
                "session_fingerprint": work_order.get(
                    "transport_session_fingerprint"
                ),
            }
            if current_head is not None:
                expected_head_fields = set(binding_fields)
                if set(current_head) != expected_head_fields:
                    raise ValueError(
                        "authority_continuation_head_conflict: active head fields differ"
                    )
                if current_head.get("work_order_id") != work_order_id:
                    raise ValueError(
                        "authority_continuation_head_conflict: work order differs"
                    )
                current_continuation_id = safe_id(
                    current_head.get("continuation_id"), "continuation_id"
                )
                current_generation = current_head.get("generation")
                if (
                    isinstance(current_generation, bool)
                    or not isinstance(current_generation, int)
                    or current_generation < 1
                ):
                    raise ValueError(
                        "authority_continuation_head_conflict: generation"
                    )
                current_record_path = (
                    directory
                    / "authority_continuations"
                    / f"{current_continuation_id}.json"
                )
                if not current_record_path.is_file():
                    raise ValueError(
                        "authority_continuation_head_conflict: signed head is missing"
                    )
                current_record = self.read_json(current_record_path)
                if (
                    not isinstance(current_record, dict)
                    or current_record.get("project_id") != project_id
                ):
                    raise ValueError(
                        "authority_continuation_head_conflict: signed head is invalid"
                    )
                self._verify_record_integrity(
                    current_record, record_id=current_continuation_id
                )
                self._verify_project_record_primary_id(
                    "authority_continuations",
                    current_record,
                    record_id=current_continuation_id,
                )
                if (
                    not self.project_record_is_signed(current_record)
                    or current_record.get("authority_root_id")
                    != authority_root_id
                    or current_record.get("work_order_id") != work_order_id
                    or current_record.get("active_binding")
                    != {
                        field: current_head[field]
                        for field in binding_fields
                    }
                ):
                    raise ValueError(
                        "authority_continuation_head_conflict: signed head differs"
                    )
                prior_physical_binding = {
                    field: current_head[field]
                    for field in (
                        "window_binding_id",
                        "actor_binding_id",
                        "session_fingerprint",
                    )
                }

            head_projection = {
                **copy.deepcopy(active_binding),
            }
            head_already_committed = current_head == head_projection
            if head_already_committed:
                if generation != current_generation:
                    raise ValueError(
                        "authority_continuation_head_conflict: generation differs"
                    )
                if current_continuation_id != clean_id:
                    raise ValueError(
                        "authority_continuation_lineage_invalid: active continuation differs"
                    )
            elif generation <= current_generation:
                raise ValueError(
                    "authority_continuation_plan_conflict: authority generation conflicts with another plan"
                )
            elif generation != current_generation + 1:
                raise ValueError(
                    "authority_continuation_head_conflict: stale generation"
                )
            elif (
                previous_generation != current_generation
                or previous_continuation_id != current_continuation_id
            ):
                raise ValueError(
                    "authority_continuation_lineage_invalid: candidate does not descend from active head"
                )
            if not head_already_committed:
                for field, next_value in (
                    ("window_binding_id", window_binding_id),
                    ("actor_binding_id", actor_binding_id),
                    ("session_fingerprint", session_fingerprint),
                ):
                    if prior_physical_binding.get(field) == next_value:
                        raise ValueError(
                            f"authority_continuation_binding_conflict: {field} was not rotated"
                        )

            # The plan is also fenced against an already-issued continuation
            # record, not only the unsigned Hub projection.  This is what makes
            # a signed-record/Hub half-write recoverable without admitting a
            # competing plan for the same generation.
            existing_record: dict[str, Any] | None = None
            if record_path.is_file():
                existing_record = self.read_json(record_path)
                if (
                    not isinstance(existing_record, dict)
                    or existing_record.get("project_id") != project_id
                ):
                    raise ValueError("authority_continuation_record_conflict")
                self._verify_record_integrity(
                    existing_record, record_id=clean_id
                )
                self._verify_project_record_primary_id(
                    "authority_continuations",
                    existing_record,
                    record_id=clean_id,
                )
                if not self.project_record_is_signed(existing_record):
                    raise ValueError("authority_continuation_signature_required")
                candidate["created_at"] = existing_record.get("created_at")
            candidate.setdefault("created_at", utc_stamp())
            if not isinstance(candidate.get("created_at"), str) or not candidate.get(
                "created_at"
            ):
                raise ValueError("authority_continuation_invalid: created_at")
            candidate["integrity_hmac"] = self._record_integrity_hmac(candidate)
            if existing_record is not None and existing_record != candidate:
                if existing_record.get("plan_sha256") != plan_sha256:
                    raise ValueError(
                        "authority_continuation_plan_conflict: authority generation conflicts with another plan"
                    )
                raise ValueError("authority_continuation_record_conflict")

            continuation_records = self.list_project_records_strict(
                namespace,
                project_id,
                kind="authority_continuations",
            )
            root_records_by_generation: dict[int, dict[str, Any]] = {}
            for prior_record in continuation_records:
                prior_binding = prior_record.get("active_binding")
                if not isinstance(prior_binding, dict):
                    raise ValueError("authority_continuation_record_conflict")
                if (
                    prior_record.get("authority_root_id") == authority_root_id
                ):
                    prior_generation = prior_record.get("generation")
                    if (
                        isinstance(prior_generation, bool)
                        or not isinstance(prior_generation, int)
                        or prior_generation < 1
                    ):
                        raise ValueError("authority_continuation_record_conflict")
                    if prior_generation in root_records_by_generation:
                        raise ValueError(
                            "authority_continuation_lineage_invalid: duplicate generation"
                        )
                    root_records_by_generation[prior_generation] = prior_record
                    if prior_generation == generation and prior_record != candidate:
                        raise ValueError(
                            "authority_continuation_plan_conflict: authority generation conflicts with another plan"
                        )
                    if (
                        prior_record.get("plan_sha256") == plan_sha256
                        and prior_record != candidate
                    ):
                        raise ValueError(
                            "authority_continuation_plan_conflict: authority generation conflicts with another plan"
                        )
                if (
                    prior_binding.get("authority_epoch_id")
                    == active_binding["authority_epoch_id"]
                    and prior_record != candidate
                ):
                    raise ValueError("authority_continuation_record_conflict")
                if (
                    prior_binding.get("session_fingerprint")
                    == session_fingerprint
                    and prior_binding.get("role") != active_binding["role"]
                ):
                    raise ValueError(
                        "authority_continuation_binding_conflict: session has cross-role history"
                    )
            if any(
                isinstance(row, dict)
                and row.get("transport_session_fingerprint")
                == session_fingerprint
                and row.get("server_actor_role") != active_binding["role"]
                for row in work_orders
            ):
                raise ValueError(
                    "authority_continuation_binding_conflict: session has cross-role history"
                )

            # Strictly enumerate the signed lineage that is supposed to exist
            # at this lock epoch.  The only record permitted one generation
            # ahead of the Hub head is the exact signed candidate from an
            # interrupted record-first/Hub-second commit.
            expected_existing_generation = (
                generation if existing_record is not None else current_generation
            )
            expected_generations = set(
                range(1, expected_existing_generation + 1)
            )
            if set(root_records_by_generation) != expected_generations:
                raise ValueError(
                    "authority_continuation_lineage_invalid: generation gap or unexpected signed record"
                )

            expected_previous_id: str | None = None
            for expected_generation in range(
                1, expected_existing_generation + 1
            ):
                prior_record = root_records_by_generation[
                    expected_generation
                ]
                prior_binding = prior_record.get("active_binding")
                prior_id = prior_record.get("continuation_id")
                binding_ids_valid = all(
                    isinstance(prior_binding.get(field), str)
                    and SAFE_ID_RE.fullmatch(prior_binding[field]) is not None
                    for field in (
                        "continuation_id",
                        "authority_epoch_id",
                        "authority_root_id",
                        "work_order_id",
                        "window_binding_id",
                        "actor_binding_id",
                    )
                )
                binding_hashes_valid = all(
                    isinstance(prior_binding.get(field), str)
                    and re.fullmatch(
                        r"[0-9a-f]{64}", prior_binding[field]
                    )
                    is not None
                    for field in (
                        "session_fingerprint",
                        "authority_contract_sha256",
                        "plan_sha256",
                    )
                )
                lineage_matches = (
                    isinstance(prior_binding, dict)
                    and set(prior_binding) == set(binding_fields)
                    and binding_ids_valid
                    and binding_hashes_valid
                    and prior_record.get("project_id") == project_id
                    and prior_record.get("record_id") == prior_id
                    and prior_record.get("authority_root_id")
                    == authority_root_id
                    and prior_record.get("work_order_id") == work_order_id
                    and prior_record.get("generation")
                    == expected_generation
                    and prior_record.get("previous_generation")
                    == expected_generation - 1
                    and prior_record.get("previous_continuation_id")
                    == expected_previous_id
                    and isinstance(
                        prior_record.get("work_order_snapshot_sha256"), str
                    )
                    and re.fullmatch(
                        r"[0-9a-f]{64}",
                        prior_record["work_order_snapshot_sha256"],
                    )
                    is not None
                    and prior_record.get("work_order_snapshot_sha256")
                    == prior_record.get(
                        "expected_work_order_snapshot_sha256"
                    )
                    and prior_record.get("authority_contract_sha256")
                    == authority_contract_sha256
                    and prior_record.get("status") == "active"
                    and all(
                        prior_record.get(field) == prior_binding.get(field)
                        for field in binding_fields
                    )
                    and prior_binding.get("continuation_id") == prior_id
                    and prior_binding.get("authority_root_id")
                    == authority_root_id
                    and prior_binding.get("work_order_id") == work_order_id
                    and prior_binding.get("generation")
                    == expected_generation
                    and prior_binding.get("role")
                    == work_order_binding["role"]
                    and prior_binding.get("operation")
                    == work_order_binding["operation"]
                    and prior_binding.get("mode")
                    == work_order_binding["mode"]
                    and prior_binding.get("authorization")
                    == work_order_binding["authorization"]
                    and prior_binding.get("authority_contract_sha256")
                    == authority_contract_sha256
                    and prior_binding.get("status") == "active"
                )
                if not lineage_matches:
                    raise ValueError(
                        "authority_continuation_lineage_invalid: signed generation differs"
                    )
                expected_previous_id = str(prior_id)

            if current_generation > 0:
                current_chain_record = root_records_by_generation.get(
                    current_generation
                )
                if (
                    not isinstance(current_chain_record, dict)
                    or current_chain_record.get("continuation_id")
                    != current_continuation_id
                    or current_chain_record.get("active_binding")
                    != current_head
                ):
                    raise ValueError(
                        "authority_continuation_lineage_invalid: active head differs from signed chain"
                    )

            expected_candidate_previous_id = (
                None
                if generation == 1
                else root_records_by_generation[generation - 1].get(
                    "continuation_id"
                )
            )
            if (
                previous_generation != generation - 1
                or previous_continuation_id
                != expected_candidate_previous_id
            ):
                raise ValueError(
                    "authority_continuation_lineage_invalid: candidate ancestry differs"
                )

            work_orders_before_sha256 = self.project_state_sha256(work_orders)
            hub_after = copy.deepcopy(hub)
            next_heads = copy.deepcopy(heads)
            if target_indexes:
                next_heads[target_indexes[0]] = head_projection
            else:
                next_heads.append(head_projection)
            hub_after["active_authority_bindings"] = next_heads
            if (
                self.project_state_sha256(hub_after.get("work_orders"))
                != work_orders_before_sha256
            ):
                raise RuntimeError("authority_continuation_work_order_mutation")

            record_existed = existing_record is not None
            if not record_existed:
                self.atomic_write_json(record_path, candidate)
            if not head_already_committed:
                self.atomic_write_json(hub_path, hub_after)

            final_record = self.read_json(record_path)
            final_hub = self.read_json(hub_path)
            final_heads = final_hub.get("active_authority_bindings")
            final_matches = [
                row
                for row in final_heads
                if isinstance(row, dict)
                and row.get("authority_root_id") == authority_root_id
            ] if isinstance(final_heads, list) else []
            if final_record != candidate or final_matches != [head_projection]:
                raise RuntimeError("authority_continuation_commit_incomplete")
            if (
                self.project_state_sha256(final_hub.get("work_orders"))
                != work_orders_before_sha256
            ):
                raise RuntimeError("authority_continuation_work_order_mutation")

            result = copy.deepcopy(candidate)
            result["idempotent_replay"] = head_already_committed
            if record_existed and not head_already_committed:
                result["resumed_from_signed_record"] = True
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

    @staticmethod
    def _controlled_preflight_evidence_identity(
        *,
        project_id: str,
        work_order_id: str,
        gap_id: str,
        handoff_id: str,
        old_record_sha256: str,
        current_fingerprints_sha256: str,
        runtime_test_certificate_id: str,
        workbook_projection_proof_sha256: str,
        production_validation_replay_sha256: str,
    ) -> tuple[str, str, str, str]:
        """Rebuild the deterministic PFE authority minted by the service."""

        evidence_key = sha256_bytes(
            canonical_json_bytes(
                {
                    "spec": "controlled-preflight-evidence-reissue-v1",
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "gap_id": gap_id,
                    "old_handoff_id": handoff_id,
                    "old_record_sha256": old_record_sha256,
                    "current_fingerprints_sha256": (
                        current_fingerprints_sha256
                    ),
                    "runtime_test_certificate_id": (
                        runtime_test_certificate_id
                    ),
                    "workbook_projection_proof_sha256": (
                        workbook_projection_proof_sha256
                    ),
                    "production_validation_replay_sha256": (
                        production_validation_replay_sha256
                    ),
                }
            )
        )
        return (
            evidence_key,
            "PFE-"
            + sha256_bytes((evidence_key + ":evidence").encode())[:20],
            "CERT-"
            + sha256_bytes((evidence_key + ":certificate").encode())[:20],
            "TRC-"
            + sha256_bytes((evidence_key + ":evidence-trace").encode())[:20],
        )

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
                historical = (
                    certificate.get("certificate_type")
                    == "historical_production_preflight_attestation"
                )
                expected_certificate_class = (
                    "historical_production_preflight_attestation"
                    if historical
                    else "production_preflight"
                )
                expected_preflight_status = (
                    "historical_contract_pass"
                    if historical
                    else "production_preflight_pass"
                )
                expected_evidence_origin = (
                    "controlled_historical_contract_replay"
                    if historical
                    else "controlled_handoff_recovery_revalidation"
                )
                if (
                    certificate.get("certificate_type")
                    != expected_certificate_class
                    or certificate.get("audit_class")
                    != expected_certificate_class
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
                    != expected_preflight_status
                    or certificate.get("preflight_issue_count") != 0
                    or certificate.get("independent_audit_status")
                    != "PENDING_AUDIT"
                    or certificate.get("evidence_origin")
                    != expected_evidence_origin
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

                if historical:
                    replay = certificate.get("production_validation_replay")
                    if not isinstance(replay, dict):
                        return False
                    replay_without_proof = copy.deepcopy(replay)
                    replay_proof = replay_without_proof.pop(
                        "proof_sha256", None
                    )
                    current_report = certificate.get(
                        "current_validation_report"
                    )
                    historical_report = certificate.get(
                        "historical_validation_report"
                    )
                    evidence_id = str(
                        handoff.get("preflight_evidence_reissue_id") or ""
                    )
                    source_certificate_id = str(
                        handoff.get("source_preflight_certificate_id") or ""
                    )
                    evidence_work_order_id = ""
                    try:
                        clean_evidence_id = safe_id(
                            evidence_id, "preflight_evidence_id"
                        )
                        clean_source_certificate_id = safe_id(
                            source_certificate_id, "certificate_id"
                        )
                        evidence = self.read_json(
                            directory
                            / "preflight_evidence_reissues"
                            / f"{clean_evidence_id}.json"
                        )
                        evidence_work_order_id = safe_id(
                            str(evidence.get("work_order_id") or ""),
                            "work_order_id",
                        )
                        clean_runtime_certificate_id = safe_id(
                            str(
                                evidence.get("runtime_test_certificate_id")
                                or ""
                            ),
                            "certificate_id",
                        )
                        clean_evidence_trace_id = safe_id(
                            str(evidence.get("trace_id") or ""),
                            "stage_trace_id",
                        )
                        clean_batch_trace_id = safe_id(
                            str(evidence.get("batch_trace_id") or ""),
                            "stage_trace_id",
                        )
                        clean_original_certificate_id = safe_id(
                            str(
                                evidence.get("original_certificate_id")
                                or ""
                            ),
                            "certificate_id",
                        )
                        clean_original_trace_id = safe_id(
                            str(
                                evidence.get(
                                    "original_production_trace_id"
                                )
                                or ""
                            ),
                            "stage_trace_id",
                        )
                        source_certificate = self.read_json(
                            directory
                            / "certificates"
                            / f"{clean_source_certificate_id}.json"
                        )
                        evidence_trace = self.read_json(
                            directory
                            / "telemetry"
                            / f"{clean_evidence_trace_id}.json"
                        )
                        batch_trace = self.read_json(
                            directory
                            / "telemetry"
                            / f"{clean_batch_trace_id}.json"
                        )
                        original_certificate = self.read_json(
                            directory
                            / "certificates"
                            / f"{clean_original_certificate_id}.json"
                        )
                        original_trace = self.read_json(
                            directory
                            / "telemetry"
                            / f"{clean_original_trace_id}.json"
                        )
                        runtime_certificate = self.read_json(
                            directory
                            / "certificates"
                            / f"{clean_runtime_certificate_id}.json"
                        )
                        for value, record_id, kind in (
                            (
                                evidence,
                                clean_evidence_id,
                                "preflight_evidence_reissues",
                            ),
                            (
                                source_certificate,
                                clean_source_certificate_id,
                                "certificates",
                            ),
                            (
                                evidence_trace,
                                clean_evidence_trace_id,
                                "telemetry",
                            ),
                            (
                                batch_trace,
                                clean_batch_trace_id,
                                "telemetry",
                            ),
                            (
                                original_certificate,
                                clean_original_certificate_id,
                                "certificates",
                            ),
                            (
                                original_trace,
                                clean_original_trace_id,
                                "telemetry",
                            ),
                            (
                                runtime_certificate,
                                clean_runtime_certificate_id,
                                "certificates",
                            ),
                        ):
                            self._verify_record_integrity(
                                value, record_id=record_id
                            )
                            self._verify_project_record_primary_id(
                                kind, value, record_id=record_id
                            )
                            if not self.project_record_is_signed(value):
                                return False
                    except (OSError, TypeError, ValueError):
                        return False
                    journal_old_before = journal.get("old_before")
                    evidence_current_report = evidence.get(
                        "current_validation_report"
                    )
                    evidence_histogram: dict[str, int] = {}
                    if isinstance(evidence_current_report, dict):
                        for issue in evidence_current_report.get(
                            "issues", []
                        ):
                            if not isinstance(issue, dict):
                                continue
                            code = str(issue.get("error_code") or "UNKNOWN")
                            evidence_histogram[code] = (
                                evidence_histogram.get(code, 0) + 1
                            )
                    expected_batch_trace_id = (
                        "TRC-"
                        + sha256_bytes(
                            (
                                str(evidence.get("plan_sha256") or "")
                                + ":preflight-evidence-trace"
                            ).encode()
                        )[:20]
                    )
                    evidence_fingerprints = evidence.get(
                        "current_fingerprints"
                    )
                    projection_proof = evidence.get(
                        "workbook_projection_revalidation"
                    )
                    if (
                        not isinstance(evidence_fingerprints, dict)
                        or not isinstance(projection_proof, dict)
                    ):
                        return False
                    evidence_fingerprints_sha256 = sha256_bytes(
                        canonical_json_bytes(evidence_fingerprints)
                    )
                    (
                        expected_evidence_key,
                        expected_evidence_id,
                        expected_source_certificate_id,
                        expected_evidence_trace_id,
                    ) = self._controlled_preflight_evidence_identity(
                        project_id=project_id,
                        work_order_id=evidence_work_order_id,
                        gap_id=str(evidence.get("gap_id") or ""),
                        handoff_id=str(
                            evidence.get("old_handoff_id") or ""
                        ),
                        old_record_sha256=str(
                            evidence.get("old_handoff_sha256") or ""
                        ),
                        current_fingerprints_sha256=str(
                            evidence.get("current_fingerprints_sha256") or ""
                        ),
                        runtime_test_certificate_id=(
                            clean_runtime_certificate_id
                        ),
                        workbook_projection_proof_sha256=str(
                            projection_proof.get("proof_sha256") or ""
                        ),
                        production_validation_replay_sha256=str(
                            replay.get("proof_sha256") or ""
                        ),
                    )
                    authority_hub = self.read_json(directory / "hub.json")
                    evidence_work_orders = [
                        row
                        for row in authority_hub.get("work_orders", [])
                        if isinstance(row, dict)
                        and row.get("work_order_id")
                        == evidence_work_order_id
                    ]
                    expected_work_order_targets = {
                        "GAP-1bb91d83751b422f",
                        *CONTROLLED_HISTORICAL_PREFLIGHT_HANDOFF_IDS,
                        "controlled_phase:preflight_evidence_backfill",
                    }
                    if len(evidence_work_orders) != 1:
                        return False
                    evidence_work_order = evidence_work_orders[0]
                    if (
                        not str(certificate.get("cache_key") or "").startswith(
                            "HISTCACHE-"
                        )
                        or handoff.get("audit_class")
                        != "historical_production_preflight_attestation"
                        or handoff.get("preflight_evidence_source")
                        != "controlled_historical_contract_replay"
                        or handoff.get("production_preflight_status")
                        != "historical_contract_pass"
                        or handoff.get("production_validation_replay")
                        != replay
                        or stage_trace.get("production_validation_replay")
                        != replay
                        or replay_proof
                        != sha256_bytes(
                            canonical_json_bytes(replay_without_proof)
                        )
                        or replay.get("historical_contract_status")
                        != "historical_contract_pass"
                        or replay.get("historical_contract_issue_count") != 0
                        or replay.get("current_validation_status")
                        != "fail_current"
                        or not isinstance(
                            replay.get("current_validation_issue_count"), int
                        )
                        or isinstance(
                            replay.get("current_validation_issue_count"), bool
                        )
                        or replay.get("current_validation_issue_count") < 1
                        or not isinstance(current_report, dict)
                        or not isinstance(historical_report, dict)
                        or historical_report != report
                        or sha256_bytes(
                            canonical_json_bytes(historical_report)
                        )
                        != replay.get("historical_report_sha256")
                        or sha256_bytes(canonical_json_bytes(current_report))
                        != replay.get("current_report_sha256")
                        or len(current_report.get("issues", []))
                        != replay.get("current_validation_issue_count")
                        or certificate.get("current_validation_status")
                        != "fail_current"
                        or certificate.get("current_validation_issue_count")
                        != replay.get("current_validation_issue_count")
                        or handoff.get("current_validation_status")
                        != "fail_current"
                        or handoff.get("current_validation_issue_count")
                        != replay.get("current_validation_issue_count")
                        or stage_trace.get("current_validation_status")
                        != "fail_current"
                        or stage_trace.get("current_validation_issue_count")
                        != replay.get("current_validation_issue_count")
                        or any(
                            value.get("current_production_pass_claimed")
                            is not False
                            or value.get("current_preflight_cache_reusable")
                            is not False
                            or value.get(
                                "conclusion_reusable_for_independent_audit"
                            )
                            is not False
                            or value.get(
                                "requires_full_current_independent_audit"
                            )
                            is not True
                            for value in (certificate, handoff, stage_trace)
                        )
                        or certificate.get("source_preflight_evidence_id")
                        != evidence_id
                        or certificate.get(
                            "source_preflight_evidence_certificate_id"
                        )
                        != source_certificate_id
                        or evidence.get("status") != "committed"
                        or evidence.get("project_id") != project_id
                        or evidence.get("gap_id")
                        != "GAP-1bb91d83751b422f"
                        or evidence.get("work_order_id")
                        != evidence_work_order_id
                        or evidence_work_order.get("operation")
                        != "rule_improvement"
                        or evidence_work_order.get("mode") != "execute"
                        or evidence_work_order.get("authorization")
                        != "explicit_execute"
                        or evidence_work_order.get("server_actor_role")
                        != "production"
                        or {
                            str(value)
                            for value in evidence_work_order.get(
                                "targets", []
                            )
                        }
                        != expected_work_order_targets
                        or evidence.get("confirmation")
                        != "REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE"
                        or evidence.get("authorized_handoff_ids")
                        != list(CONTROLLED_HISTORICAL_PREFLIGHT_HANDOFF_IDS)
                        or evidence.get("batch_trace_id")
                        != expected_batch_trace_id
                        or not isinstance(journal_old_before, dict)
                        or evidence.get("old_handoff_sha256")
                        != self.project_state_sha256(journal_old_before)
                        or evidence.get("original_certificate_id")
                        != journal_old_before.get("certificate_id")
                        or evidence.get("original_production_trace_id")
                        != journal_old_before.get("stage_trace_id")
                        or evidence.get("evidence_type")
                        != "controlled_historical_production_preflight_attestation"
                        or evidence.get("old_handoff_id")
                        != journal.get("old_handoff_id")
                        or evidence.get("preflight_evidence_id")
                        != evidence_id
                        or evidence.get("evidence_key")
                        != expected_evidence_key
                        or evidence_id != expected_evidence_id
                        or evidence.get("certificate_id")
                        != source_certificate_id
                        or source_certificate_id
                        != expected_source_certificate_id
                        or evidence.get("trace_id")
                        != expected_evidence_trace_id
                        or evidence.get("runtime_test_certificate_id")
                        != clean_runtime_certificate_id
                        or evidence.get("current_fingerprints")
                        != handoff.get("fingerprints")
                        or evidence.get("current_fingerprints_sha256")
                        != evidence_fingerprints_sha256
                        or evidence.get("current_fingerprints_sha256")
                        != handoff.get("recovery_plan_item", {}).get(
                            "current_fingerprints_sha256"
                        )
                        or evidence.get("artifact_id")
                        != handoff.get("storyboard_artifact_id")
                        or evidence.get("canonical_artifact_id")
                        != handoff.get("canonical_artifact_id")
                        or evidence.get("canonical_sha256")
                        != handoff.get("canonical_sha256")
                        or evidence.get("validation_report_sha256")
                        != replay.get("historical_report_sha256")
                        or evidence.get("checked_shot_count")
                        != handoff.get("checked_shot_count")
                        or not isinstance(
                            evidence.get("production_preflight_issue_count"),
                            int,
                        )
                        or isinstance(
                            evidence.get("production_preflight_issue_count"),
                            bool,
                        )
                        or evidence.get("production_preflight_issue_count")
                        != 0
                        or evidence.get("independent_audit_status")
                        != "PENDING_AUDIT"
                        or evidence.get("first_draft_grade_reconstructed")
                        is not False
                        or evidence.get("authored_artifacts_modified")
                        is not False
                        or not isinstance(
                            evidence.get("history_records_modified"), int
                        )
                        or isinstance(
                            evidence.get("history_records_modified"), bool
                        )
                        or evidence.get("history_records_modified") != 0
                        or evidence.get("independent_audit_verdict_issued")
                        is not False
                        or evidence.get("production_validation_replay")
                        != replay
                        or evidence.get("production_preflight_status")
                        != "historical_contract_pass"
                        or evidence.get("current_validation_status")
                        != "fail_current"
                        or evidence.get("current_validation_issue_count")
                        != replay.get("current_validation_issue_count")
                        or evidence.get(
                            "historical_validation_report_sha256"
                        )
                        != replay.get("historical_report_sha256")
                        or evidence.get(
                            "current_validation_report_sha256"
                        )
                        != replay.get("current_report_sha256")
                        or evidence.get(
                            "current_validation_error_histogram"
                        )
                        != dict(sorted(evidence_histogram.items()))
                        or evidence.get(
                            "current_validation_error_histogram"
                        )
                        != replay.get("current_validation_error_histogram")
                        or not isinstance(
                            evidence.get("historical_validation_report"),
                            dict,
                        )
                        or sha256_bytes(
                            canonical_json_bytes(
                                evidence["historical_validation_report"]
                            )
                        )
                        != replay.get("historical_report_sha256")
                        or not isinstance(evidence_current_report, dict)
                        or sha256_bytes(
                            canonical_json_bytes(evidence_current_report)
                        )
                        != replay.get("current_report_sha256")
                        or evidence.get("certificate_after")
                        != source_certificate
                        or self.project_state_sha256(source_certificate)
                        != evidence.get("certificate_after_sha256")
                        or source_certificate.get("certificate_type")
                        != "historical_production_preflight_attestation"
                        or source_certificate.get("work_order_id")
                        != evidence_work_order_id
                        or source_certificate.get(
                            "evidence_reissue_work_order_id"
                        )
                        != evidence_work_order_id
                        or source_certificate.get("evidence_reissue_gap_id")
                        != evidence.get("gap_id")
                        or source_certificate.get("preflight_evidence_id")
                        != evidence_id
                        or source_certificate.get("evidence_trace_id")
                        != expected_evidence_trace_id
                        or source_certificate.get(
                            "runtime_test_certificate_id"
                        )
                        != clean_runtime_certificate_id
                        or source_certificate.get("cache_components", {}).get(
                            "runtime_test_certificate_id"
                        )
                        != clean_runtime_certificate_id
                        or source_certificate.get("artifact_id")
                        != evidence.get("artifact_id")
                        or source_certificate.get("canonical_artifact_id")
                        != evidence.get("canonical_artifact_id")
                        or source_certificate.get("canonical_sha256")
                        != evidence.get("canonical_sha256")
                        or source_certificate.get("current_fingerprints")
                        != evidence_fingerprints
                        or source_certificate.get(
                            "current_fingerprints_sha256"
                        )
                        != evidence_fingerprints_sha256
                        or source_certificate.get("preflight_status")
                        != "historical_contract_pass"
                        or source_certificate.get("preflight_issue_count")
                        != 0
                        or source_certificate.get("checked_shot_count")
                        != evidence.get("checked_shot_count")
                        or source_certificate.get(
                            "first_draft_grade_reconstructed"
                        )
                        is not False
                        or source_certificate.get("authored_artifacts_modified")
                        is not False
                        or source_certificate.get(
                            "independent_audit_verdict_issued"
                        )
                        is not False
                        or source_certificate.get(
                            "production_validation_replay"
                        )
                        != replay
                        or runtime_certificate.get("certificate_id")
                        != clean_runtime_certificate_id
                        or runtime_certificate.get("certificate_type")
                        != "runtime_full_regression"
                        or runtime_certificate.get("result") != "pass"
                        or certificate.get("cache_components", {}).get(
                            "runtime_test_certificate_id"
                        )
                        != clean_runtime_certificate_id
                        or evidence_trace.get("trace_id")
                        != evidence.get("trace_id")
                        or evidence_trace.get("telemetry_id")
                        != evidence.get("trace_id")
                        or evidence_trace.get("operation")
                        != "recover_storyboard_handoff"
                        or evidence_trace.get("phase")
                        != "preflight_evidence_backfill"
                        or evidence_trace.get("work_order_id")
                        != evidence_work_order_id
                        or evidence_trace.get("authority_record_id")
                        != evidence_id
                        or evidence_trace.get("gap_id")
                        != evidence.get("gap_id")
                        or evidence_trace.get("plan_sha256")
                        != evidence.get("plan_sha256")
                        or evidence_trace.get("authorized_handoff_ids")
                        != evidence.get("authorized_handoff_ids")
                        or evidence_trace.get("artifact_id")
                        != evidence.get("artifact_id")
                        or evidence_trace.get("input_fingerprints")
                        != evidence_fingerprints
                        or evidence_trace.get(
                            "production_validation_replay"
                        )
                        != replay
                        or evidence.get("stage_trace_after")
                        != evidence_trace
                        or self.project_state_sha256(evidence_trace)
                        != evidence.get("stage_trace_after_sha256")
                        or batch_trace.get("trace_id")
                        != evidence.get("batch_trace_id")
                        or batch_trace.get("telemetry_id")
                        != evidence.get("batch_trace_id")
                        or batch_trace.get("operation")
                        != "recover_storyboard_handoff"
                        or batch_trace.get("phase")
                        != "preflight_evidence_backfill"
                        or batch_trace.get("work_order_id")
                        != evidence_work_order_id
                        or batch_trace.get("gap_id")
                        != evidence.get("gap_id")
                        or batch_trace.get("plan_sha256")
                        != evidence.get("plan_sha256")
                        or batch_trace.get("authorized_handoff_ids")
                        != evidence.get("authorized_handoff_ids")
                        or original_certificate.get("certificate_id")
                        != evidence.get("original_certificate_id")
                        or self.project_state_sha256(original_certificate)
                        != evidence.get(
                            "workbook_projection_revalidation", {}
                        ).get("old_certificate_sha256")
                        or original_trace.get("trace_id")
                        != evidence.get("original_production_trace_id")
                        or original_trace.get("telemetry_id")
                        != evidence.get("original_production_trace_id")
                        or original_trace.get("operation")
                        != "commit_storyboard"
                        or original_trace.get("window_role") != "production"
                        or original_trace.get("task_type")
                        != "production_preflight"
                        or self.project_state_sha256(original_trace)
                        != evidence.get(
                            "workbook_projection_revalidation", {}
                        ).get("original_production_trace_sha256")
                        or evidence.get(
                            "workbook_projection_revalidation", {}
                        ).get("old_handoff_sha256")
                        != evidence.get("old_handoff_sha256")
                    ):
                        return False
                elif (
                    not str(certificate.get("cache_key") or "").startswith(
                        "AUDCACHE-"
                    )
                    or any(
                        value.get("production_validation_replay") is not None
                        for value in (certificate, handoff, stage_trace)
                    )
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
            if state != "committed":
                return False
            # A cloud draft-preflight commit persists its deterministic
            # artifact/certificate/handoff set before publishing Hub and
            # telemetry.  Those records are intentionally not consumable
            # until the signed draft WAL is completed; this prevents a crash
            # at recovery_ready (before Hub) from exposing an orphan handoff.
            try:
                directory = self.project_dir(namespace, project_id)
                artifact_id = safe_id(
                    str(handoff.get("storyboard_artifact_id") or ""),
                    "artifact_id",
                )
                artifact_meta = self.read_json(
                    directory / "artifacts" / f"{artifact_id}.meta.json"
                )
                metadata = artifact_meta.get("metadata")
                draft_preflight_id = (
                    metadata.get("draft_preflight_id")
                    if isinstance(metadata, dict)
                    else None
                )
                if (
                    not draft_preflight_id
                    or metadata.get("handoff_id")
                    != handoff.get("handoff_id")
                ):
                    return True
                clean_preflight_id = safe_id(
                    str(draft_preflight_id), "draft_preflight_id"
                )
                preflight = self.read_json(
                    directory
                    / "draft_preflights"
                    / f"{clean_preflight_id}.json"
                )
                self._verify_record_integrity(
                    preflight, record_id=clean_preflight_id
                )
                self._verify_project_record_primary_id(
                    "draft_preflights",
                    preflight,
                    record_id=clean_preflight_id,
                )
                consumed = preflight.get("consumed_by_commit")
                claim = preflight.get("commit_claim")
                telemetry_id = safe_id(
                    str(
                        consumed.get("trace_id")
                        if isinstance(consumed, dict)
                        else ""
                    ),
                    "telemetry_id",
                )
                telemetry = self.read_json(
                    directory / "telemetry" / f"{telemetry_id}.json"
                )
                self._verify_record_integrity(
                    telemetry, record_id=telemetry_id
                )
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
                    and row.get("telemetry_id") == telemetry_id
                ]
                immutable_projection = lambda value: {
                    key: copy.deepcopy(item)
                    for key, item in value.items()
                    if key not in RECOVERY_HANDOFF_AUDIT_MUTABLE_KEYS
                }
                hub_handoff = (
                    handoff_matches[0]
                    if len(handoff_matches) == 1
                    else None
                )
                lifecycle_projection_matches = bool(
                    isinstance(hub_handoff, dict)
                    and immutable_projection(hub_handoff)
                    == immutable_projection(
                        self._hub_record_projection(handoff)
                    )
                    and hub_handoff.get("status")
                    == handoff.get("status")
                    and (
                        handoff.get("status") != "consumed"
                        or hub_handoff.get("audit_certificate_id")
                        == handoff.get("audit_certificate_id")
                    )
                )
                return bool(
                    self.project_record_is_signed(preflight)
                    and self.project_record_is_signed(telemetry)
                    and isinstance(consumed, dict)
                    and isinstance(claim, dict)
                    and claim.get("state") == "completed"
                    and preflight.get("work_order_id")
                    == handoff.get("work_order_id")
                    and consumed.get("artifact_id") == artifact_id
                    and consumed.get("canonical_json_artifact_id")
                    == handoff.get("canonical_artifact_id")
                    and consumed.get("canonical_sha256")
                    == handoff.get("canonical_sha256")
                    and (consumed.get("handoff") or {}).get("handoff_id")
                    == handoff.get("handoff_id")
                    and (consumed.get("certificate") or {}).get(
                        "certificate_id"
                    )
                    == handoff.get("certificate_id")
                    and telemetry.get("artifact_id") == artifact_id
                    and telemetry.get("trace_id")
                    == handoff.get("stage_trace_id")
                    and lifecycle_projection_matches
                    and trace_matches == [telemetry]
                )
            except (OSError, TypeError, ValueError):
                return False
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
            if not self.project_record_is_signed(record):
                raise ValueError("handoff_signature_required")
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
                    **{
                        key: copy.deepcopy(record.get(key))
                        for key in (
                            "claim_authority_contract",
                            "claim_authority_root_id",
                            "claim_authority_generation",
                            "claim_authority_session_fingerprint",
                        )
                        if key in record
                    },
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
            directory = self.project_dir(namespace, project_id)
            _consumer_work_order, authority = (
                self._work_order_authority_unlocked(
                    directory,
                    project_id=project_id,
                    work_order_id=consumer_work_order_id,
                    expected_role=expected_to_role,
                    error_code="handoff_claim_authority_invalid",
                )
            )
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
            record.update(self._claim_authority_fields(authority))
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
            if not self.project_record_is_signed(record):
                raise ValueError("handoff_signature_required")
            if record.get("status") == "consumed":
                return record
            if (
                record.get("status") != "consuming"
                or record.get("claimed_by_work_order_id") != consumer_work_order_id
                or record.get("claim_token") != claim_token
            ):
                raise ValueError("handoff_state_conflict: handoff claim is not current")
            self._assert_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                work_order_id=consumer_work_order_id,
                expected_role="independent_audit",
                error_code=(
                    "handoff_claim_revoked: claim belongs to an inactive "
                    "or expired window generation"
                ),
            )
            released = {
                "claim_token": record.get("claim_token"),
                "claimed_by_work_order_id": consumer_work_order_id,
                "claim_generation": record.get("claim_generation"),
                "claim_epoch_seconds": record.get("claim_epoch_seconds"),
                "claim_lease_expires_epoch_seconds": record.get(
                    "claim_lease_expires_epoch_seconds"
                ),
                **{
                    key: copy.deepcopy(record.get(key))
                    for key in (
                        "claim_authority_contract",
                        "claim_authority_root_id",
                        "claim_authority_generation",
                        "claim_authority_session_fingerprint",
                    )
                    if key in record
                },
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
                "claim_authority_contract",
                "claim_authority_root_id",
                "claim_authority_generation",
                "claim_authority_session_fingerprint",
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
            "claim_authority_contract",
            "claim_authority_root_id",
            "claim_authority_generation",
            "claim_authority_session_fingerprint",
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
        legacy_claim_fields = {
            "claim_token",
            "claim_generation",
            "claim_epoch_seconds",
            "claim_lease_seconds",
            "claim_lease_expires_at",
            "claim_lease_expires_epoch_seconds",
        }
        authority_claim_fields = {
            "claim_authority_contract",
            "claim_authority_root_id",
            "claim_authority_generation",
            "claim_authority_session_fingerprint",
        }
        if not isinstance(claim, dict) or set(claim) not in {
            frozenset(legacy_claim_fields),
            frozenset(legacy_claim_fields | authority_claim_fields),
        }:
            return (
                "reaudit_claim_binding_invalid: consumed successor claim "
                "snapshot is incomplete"
            )
        if authority_claim_fields <= set(claim):
            authority_generation = claim.get("claim_authority_generation")
            authority_session = claim.get(
                "claim_authority_session_fingerprint"
            )
            if (
                claim.get("claim_authority_contract")
                != "window-role-authority-claim-v1"
                or not isinstance(claim.get("claim_authority_root_id"), str)
                or not claim.get("claim_authority_root_id")
                or not isinstance(authority_generation, int)
                or isinstance(authority_generation, bool)
                or authority_generation < 0
                or (
                    authority_session is not None
                    and (
                        not isinstance(authority_session, str)
                        or not authority_session
                    )
                )
            ):
                return (
                    "reaudit_claim_binding_invalid: consumed successor "
                    "authority snapshot is invalid"
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
                for authority_field in (
                    "claim_authority_contract",
                    "claim_authority_root_id",
                    "claim_authority_generation",
                    "claim_authority_session_fingerprint",
                ):
                    if authority_field in prior_claim:
                        prior_live_fields[authority_field] = copy.deepcopy(
                            prior_claim.get(authority_field)
                        )
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
            route_authority_fields = {
                "reaudit_audit_authority_root_id": "authority_root_id",
                "reaudit_audit_authority_epoch_id": "authority_epoch_id",
                "reaudit_audit_authority_generation": "generation",
                "reaudit_audit_authority_continuation_id": "continuation_id",
                "reaudit_audit_window_binding_id": "window_binding_id",
                "reaudit_audit_actor_binding_id": "actor_binding_id",
                "reaudit_audit_session_fingerprint": "session_fingerprint",
            }
            lineage_fields = tuple(route_authority_fields)[:4]
            present_lineage_fields = [
                field for field in lineage_fields if field in successor
            ]
            if present_lineage_fields and len(present_lineage_fields) != len(
                lineage_fields
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: partial route authority snapshot"
                )
            if present_lineage_fields:
                route_generation = successor.get(
                    "reaudit_audit_authority_generation"
                )
                if (
                    isinstance(route_generation, bool)
                    or not isinstance(route_generation, int)
                    or route_generation < 0
                ):
                    raise ValueError(
                        "reaudit_lineage_invalid: route authority generation"
                    )
            else:
                route_generation = 0
            route_authority, current_audit_authority = (
                self._authority_binding_at_generation_unlocked(
                    directory,
                    project_id=project_id,
                    hub=hub_before,
                    work_order=audit_order,
                    generation=route_generation,
                )
            )
            compared_route_fields = (
                route_authority_fields
                if present_lineage_fields
                else {
                    "reaudit_audit_window_binding_id": "window_binding_id",
                    "reaudit_audit_actor_binding_id": "actor_binding_id",
                    "reaudit_audit_session_fingerprint": "session_fingerprint",
                }
            )
            if any(
                successor.get(route_field)
                != route_authority.get(binding_field)
                for route_field, binding_field in compared_route_fields.items()
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: route authority differs from signed generation"
                )
            if (
                not successor_path.is_file()
                and route_authority != current_audit_authority
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: new route is not bound to the active audit generation"
                )
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
        hub = self.read_json(directory / "hub.json")
        if not isinstance(hub, dict) or hub.get("project_id") is None:
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
            audit_work_order_id = str(
                certificate.get("work_order_id") or ""
            )
            audit_orders = [
                row
                for row in hub.get("work_orders", [])
                if isinstance(row, dict)
                and row.get("work_order_id") == audit_work_order_id
            ]
            if len(audit_orders) != 1:
                raise ValueError(error_message)
            certificate_lineage_fields = (
                "audit_authority_root_id",
                "audit_authority_epoch_id",
                "audit_authority_generation",
                "audit_authority_continuation_id",
            )
            present_certificate_lineage = [
                field
                for field in certificate_lineage_fields
                if field in certificate
            ]
            if present_certificate_lineage and len(
                present_certificate_lineage
            ) != len(certificate_lineage_fields):
                raise ValueError(error_message)
            certificate_generation = (
                certificate.get("audit_authority_generation")
                if present_certificate_lineage
                else 0
            )
            if (
                isinstance(certificate_generation, bool)
                or not isinstance(certificate_generation, int)
                or certificate_generation < 0
            ):
                raise ValueError(error_message)
            certificate_authority, _current_authority = (
                self._authority_binding_at_generation_unlocked(
                    directory,
                    project_id=str(hub["project_id"]),
                    hub=hub,
                    work_order=audit_orders[0],
                    generation=certificate_generation,
                )
            )
            certificate_authority_fields = (
                {
                    "audit_authority_root_id": "authority_root_id",
                    "audit_authority_epoch_id": "authority_epoch_id",
                    "audit_authority_generation": "generation",
                    "audit_authority_continuation_id": "continuation_id",
                    "audit_window_binding_id": "window_binding_id",
                    "audit_actor_binding_id": "actor_binding_id",
                    "audit_session_fingerprint": "session_fingerprint",
                }
                if present_certificate_lineage
                else {
                    "audit_actor_binding_id": "actor_binding_id",
                    "audit_session_fingerprint": "session_fingerprint",
                }
            )
            if any(
                certificate.get(certificate_field)
                != certificate_authority.get(binding_field)
                for certificate_field, binding_field in (
                    certificate_authority_fields.items()
                )
            ):
                raise ValueError(error_message)
            authority_continuation_hmac = ""
            if certificate_authority.get("continuation_id"):
                continuation_path = (
                    directory
                    / "authority_continuations"
                    / f"{certificate_authority['continuation_id']}.json"
                )
                if not continuation_path.is_file():
                    raise ValueError(error_message)
                continuation = self.read_json(continuation_path)
                self._verify_record_integrity(
                    continuation,
                    record_id=str(
                        certificate_authority["continuation_id"]
                    ),
                )
                if (
                    not self.project_record_is_signed(continuation)
                    or continuation.get("active_binding")
                    != certificate_authority
                ):
                    raise ValueError(error_message)
                authority_continuation_hmac = str(
                    continuation.get("integrity_hmac") or ""
                )
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
                    "authority_root_id": str(
                        certificate_authority.get("authority_root_id") or ""
                    ),
                    "authority_epoch_id": str(
                        certificate_authority.get("authority_epoch_id") or ""
                    ),
                    "authority_generation": int(
                        certificate_authority.get("generation") or 0
                    ),
                    "authority_continuation_id": (
                        certificate_authority.get("continuation_id")
                    ),
                    "authority_continuation_hmac": (
                        authority_continuation_hmac
                    ),
                    "authority_contract_sha256": str(
                        certificate_authority.get(
                            "authority_contract_sha256"
                        )
                        or ""
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
            if not self.project_record_is_signed(before):
                raise ValueError("handoff_signature_required")
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
                        **{
                            key: copy.deepcopy(before.get(key))
                            for key in (
                                "claim_authority_contract",
                                "claim_authority_root_id",
                                "claim_authority_generation",
                                "claim_authority_session_fingerprint",
                            )
                            if key in before
                        },
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
                    "claim_authority_contract",
                    "claim_authority_root_id",
                    "claim_authority_generation",
                    "claim_authority_session_fingerprint",
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
            _audit_work_order, authority = (
                self._work_order_authority_unlocked(
                    directory,
                    project_id=project_id,
                    work_order_id=clean_consumer_id,
                    expected_role="independent_audit",
                    error_code="reaudit_successor_claim_authority_invalid",
                )
            )
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
            after.update(self._claim_authority_fields(authority))
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
            if not self.project_record_is_signed(before):
                raise ValueError("handoff_signature_required")
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
            self._assert_claim_authority_unlocked(
                directory,
                project_id=project_id,
                record=before,
                work_order_id=consumer_work_order_id,
                expected_role="independent_audit",
                error_code=(
                    "reaudit_successor_claim_revoked: claim belongs to an "
                    "inactive or expired window generation"
                ),
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
                    **{
                        key: copy.deepcopy(before.get(key))
                        for key in (
                            "claim_authority_contract",
                            "claim_authority_root_id",
                            "claim_authority_generation",
                            "claim_authority_session_fingerprint",
                        )
                        if key in before
                    },
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
                "claim_authority_contract",
                "claim_authority_root_id",
                "claim_authority_generation",
                "claim_authority_session_fingerprint",
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
            if not self.project_record_is_signed(before):
                raise ValueError("handoff_signature_required")
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
            self._assert_claim_authority_unlocked(
                directory,
                project_id=project_id,
                record=before,
                work_order_id=clean_consumer_id,
                expected_role="independent_audit",
                error_code=(
                    "reaudit_successor_claim_revoked: claim belongs to an "
                    "inactive or expired window generation"
                ),
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
                    "claim_authority_contract",
                    "claim_authority_root_id",
                    "claim_authority_generation",
                    "claim_authority_session_fingerprint",
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
                "claim_authority_contract",
                "claim_authority_root_id",
                "claim_authority_generation",
                "claim_authority_session_fingerprint",
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
        clean_certificate_id = safe_id(
            audit_certificate_id, "audit_certificate_id"
        )
        path = self.project_dir(namespace, project_id) / "handoffs" / f"{clean_id}.json"
        with self.project_write_lock(namespace, project_id):
            if not path.is_file():
                raise ValueError("handoff_required: signed handoff does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("handoff_signature_required")
            if record.get("status") == "consumed":
                if (
                    record.get("consumed_by_work_order_id") == consumer_work_order_id
                    and record.get("audit_certificate_id") == clean_certificate_id
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
            self._assert_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                work_order_id=consumer_work_order_id,
                expected_role="independent_audit",
                error_code=(
                    "handoff_claim_revoked: claim belongs to an inactive "
                    "or expired window generation"
                ),
            )
            certificate_path = (
                self.project_dir(namespace, project_id)
                / "certificates"
                / f"{clean_certificate_id}.json"
            )
            if not certificate_path.is_file():
                raise ValueError(
                    "handoff_certificate_binding_invalid: certificate is missing"
                )
            certificate = self.read_json(certificate_path)
            self._verify_record_integrity(
                certificate, record_id=clean_certificate_id
            )
            if (
                not self.project_record_is_signed(certificate)
                or certificate.get("certificate_id") != clean_certificate_id
                or certificate.get("audit_class") != "independent_audit"
                or certificate.get("handoff_id") != clean_id
                or certificate.get("work_order_id")
                != consumer_work_order_id
                or certificate.get("artifact_id")
                != record.get("storyboard_artifact_id")
            ):
                raise ValueError(
                    "handoff_certificate_binding_invalid: certificate does not belong to this handoff"
                )
            claim_snapshot = {
                key: copy.deepcopy(record.get(key))
                for key in (
                    "claim_token",
                    "claim_generation",
                    "claim_epoch_seconds",
                    "claim_lease_seconds",
                    "claim_lease_expires_at",
                    "claim_lease_expires_epoch_seconds",
                    "claim_authority_contract",
                    "claim_authority_root_id",
                    "claim_authority_generation",
                    "claim_authority_session_fingerprint",
                )
                if key in record
            }
            claim_snapshot["consumed_by_work_order_id"] = (
                consumer_work_order_id
            )
            record["status"] = "consumed"
            record["consumed_by_work_order_id"] = consumer_work_order_id
            record["audit_certificate_id"] = clean_certificate_id
            record["consumption_claim"] = claim_snapshot
            record["consumed_at"] = utc_stamp()
            for key in (
                "claimed_by_work_order_id",
                "claim_token",
                "claim_epoch_seconds",
                "claim_lease_seconds",
                "claim_lease_expires_at",
                "claim_lease_expires_epoch_seconds",
                "claim_authority_contract",
                "claim_authority_root_id",
                "claim_authority_generation",
                "claim_authority_session_fingerprint",
            ):
                record.pop(key, None)
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
        lease_seconds: int = DEFAULT_HANDOFF_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any]:
        clean_id = safe_id(repair_package_id, "repair_package_id")
        try:
            lease_seconds = int(lease_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid repair package lease_seconds") from exc
        if not 1 <= lease_seconds <= MAX_CONSUMPTION_LEASE_SECONDS:
            raise ValueError(
                "repair package lease_seconds must be between 1 and "
                f"{MAX_CONSUMPTION_LEASE_SECONDS}"
            )
        path = self.project_dir(namespace, project_id) / "repair_packages" / f"{clean_id}.json"
        with self.project_write_lock(namespace, project_id):
            if not path.is_file():
                raise ValueError("repair_package_required: immutable package does not exist")
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("repair_package_signature_required")
            repair_work_order, authority = (
                self._work_order_authority_unlocked(
                    self.project_dir(namespace, project_id),
                    project_id=project_id,
                    work_order_id=work_order_id,
                    expected_role="repair",
                    error_code="repair_package_claim_authority_invalid",
                )
            )
            status = record.get("status")
            if status == "consuming":
                if record.get("consumed_by_work_order_id") != work_order_id:
                    raise ValueError(
                        "repair_package_claim_conflict: package belongs to another repair authority"
                    )
                try:
                    lease_expires_epoch = float(
                        record.get("claim_lease_expires_epoch_seconds") or 0
                    )
                except (TypeError, ValueError):
                    lease_expires_epoch = 0
                if lease_expires_epoch <= 0:
                    raise ValueError(
                        "repair_package_claim_incomplete: consuming legacy package has no bounded lease"
                    )
                if lease_expires_epoch > time.time():
                    raise ValueError(
                        "repair_package_claim_conflict: package is being consumed"
                    )
                self._assert_claim_authority_ancestor_unlocked(
                    self.project_dir(namespace, project_id),
                    project_id=project_id,
                    record=record,
                    work_order=repair_work_order,
                    active=authority,
                    error_code=(
                        "repair_package_claim_revoked: expired claim is not "
                        "an ancestor of the active repair authority"
                    ),
                    require_operation_fence=False,
                )
                expired = {
                    "claim_token": record.get("claim_token"),
                    "consumed_by_work_order_id": work_order_id,
                    "claim_generation": record.get("claim_generation"),
                    "claim_epoch_seconds": record.get("claim_epoch_seconds"),
                    "claim_lease_expires_epoch_seconds": record.get(
                        "claim_lease_expires_epoch_seconds"
                    ),
                    **{
                        key: copy.deepcopy(record.get(key))
                        for key in (
                            "claim_authority_contract",
                            "claim_authority_root_id",
                            "claim_authority_generation",
                            "claim_authority_session_fingerprint",
                        )
                        if key in record
                    },
                    "state": "lease_expired",
                    "expired_at": utc_stamp(),
                }
                history = record.setdefault("claim_history", [])
                if not isinstance(history, list):
                    raise ValueError("repair_package_claim_history_invalid")
                history.append(expired)
                del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            elif status != "issued":
                raise ValueError("repair_package_unavailable: package is not issued")
            try:
                generation = int(record.get("claim_generation") or 0) + 1
            except (TypeError, ValueError):
                generation = 1
            now = time.time()
            record["status"] = "consuming"
            record["consumed_by_work_order_id"] = work_order_id
            record["claim_token"] = f"RCL-{uuid.uuid4().hex}"
            record["claim_generation"] = generation
            record["claim_epoch_seconds"] = now
            record["claim_lease_seconds"] = lease_seconds
            record["claim_lease_expires_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lease_seconds)
            )
            record["claim_lease_expires_epoch_seconds"] = now + lease_seconds
            record.update(self._claim_authority_fields(authority))
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
        claim_token: str,
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
            if not self.project_record_is_signed(record):
                raise ValueError("repair_package_signature_required")
            if record.get("status") == "consumed":
                consumption = record.get("consumption_claim")
                if (
                    successor_artifact_id
                    and successor_canonical_sha256
                    and repair_to_audit_handoff_id
                    and record.get("consumed_by_work_order_id") == work_order_id
                    and isinstance(consumption, dict)
                    and consumption.get("claim_token") == claim_token
                    and consumption.get("consumed_by_work_order_id")
                    == work_order_id
                    and record.get("claim_generation")
                    == consumption.get("claim_generation")
                    and record.get("successor_artifact_id")
                    == successor_artifact_id
                    and record.get("successor_canonical_sha256")
                    == successor_canonical_sha256
                    and record.get("repair_to_audit_handoff_id")
                    == repair_to_audit_handoff_id
                ):
                    # A caller may retry after the signed package write became
                    # durable but its response was lost.  Exact replay is a
                    # read-only success; any changed binding remains closed.
                    return record
                raise ValueError("repair_package_claim_conflict")
            if (
                record.get("status") != "consuming"
                or record.get("consumed_by_work_order_id") != work_order_id
                or not claim_token
                or record.get("claim_token") != claim_token
            ):
                raise ValueError("repair_package_claim_conflict")
            self._assert_claim_authority_unlocked(
                self.project_dir(namespace, project_id),
                project_id=project_id,
                record=record,
                work_order_id=work_order_id,
                expected_role="repair",
                error_code=(
                    "repair_package_claim_revoked: claim belongs to an "
                    "inactive or expired window generation"
                ),
            )
            claim_snapshot = {
                "claim_token": record.get("claim_token"),
                "claim_generation": record.get("claim_generation"),
                "claim_epoch_seconds": record.get("claim_epoch_seconds"),
                "claim_lease_seconds": record.get("claim_lease_seconds"),
                "claim_lease_expires_at": record.get(
                    "claim_lease_expires_at"
                ),
                "claim_lease_expires_epoch_seconds": record.get(
                    "claim_lease_expires_epoch_seconds"
                ),
                **{
                    key: copy.deepcopy(record.get(key))
                    for key in (
                        "claim_authority_contract",
                        "claim_authority_root_id",
                        "claim_authority_generation",
                        "claim_authority_session_fingerprint",
                    )
                    if key in record
                },
                "consumed_by_work_order_id": work_order_id,
            }
            if successor_artifact_id and successor_canonical_sha256:
                if not repair_to_audit_handoff_id:
                    raise ValueError(
                        "repair_package_binding_incomplete: repair_to_audit handoff is required"
                    )
                clean_successor_id = safe_id(
                    successor_artifact_id, "successor_artifact_id"
                )
                clean_handoff_id = safe_id(
                    repair_to_audit_handoff_id,
                    "repair_to_audit_handoff_id",
                )
                if not isinstance(successor_canonical_sha256, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", successor_canonical_sha256
                ):
                    raise ValueError(
                        "repair_package_binding_incomplete: successor canonical hash is invalid"
                    )
                directory = self.project_dir(namespace, project_id)
                successor_path = (
                    directory
                    / "artifacts"
                    / f"{clean_successor_id}.meta.json"
                )
                handoff_path = (
                    directory / "handoffs" / f"{clean_handoff_id}.json"
                )
                if not successor_path.is_file() or not handoff_path.is_file():
                    raise ValueError(
                        "repair_package_binding_incomplete: successor or repair_to_audit handoff is missing"
                    )
                successor = self.read_json(successor_path)
                successor_metadata = successor.get("metadata")
                payload_path = (
                    directory / str(successor.get("relative_path") or "")
                ).resolve()
                if (
                    successor.get("artifact_id") != clean_successor_id
                    or successor.get("project_id") != project_id
                    or successor.get("work_order_id") != work_order_id
                    or not isinstance(successor_metadata, dict)
                    or successor_metadata.get("canonical_sha256")
                    != successor_canonical_sha256
                    or directory.resolve() not in payload_path.parents
                    or not payload_path.is_file()
                    or sha256_bytes(payload_path.read_bytes())
                    != successor.get("fingerprint")
                ):
                    raise ValueError(
                        "repair_package_binding_incomplete: successor artifact differs"
                    )
                repair_handoff = self.read_json(handoff_path)
                self._verify_record_integrity(
                    repair_handoff, record_id=clean_handoff_id
                )
                if (
                    not self.project_record_is_signed(repair_handoff)
                    or repair_handoff.get("handoff_id") != clean_handoff_id
                    or repair_handoff.get("handoff_type")
                    != "repair_to_audit"
                    or repair_handoff.get("work_order_id") != work_order_id
                    or repair_handoff.get("immutable_repair_package_id")
                    != clean_id
                    or repair_handoff.get("storyboard_artifact_id")
                    != clean_successor_id
                    or repair_handoff.get("canonical_sha256")
                    != successor_canonical_sha256
                    or repair_handoff.get("status")
                    not in {"prepared", "consuming", "consumed"}
                ):
                    raise ValueError(
                        "repair_package_binding_incomplete: signed repair_to_audit handoff differs"
                    )
                record["status"] = "consumed"
                record["successor_artifact_id"] = clean_successor_id
                record["successor_canonical_sha256"] = successor_canonical_sha256
                record["repair_to_audit_handoff_id"] = clean_handoff_id
                record["consumption_claim"] = claim_snapshot
            else:
                released = {**claim_snapshot, "state": "released", "released_at": utc_stamp()}
                history = record.setdefault("claim_history", [])
                if not isinstance(history, list):
                    raise ValueError("repair_package_claim_history_invalid")
                history.append(released)
                del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
                record["status"] = "issued"
                record.pop("consumed_by_work_order_id", None)
                record.pop("successor_artifact_id", None)
                record.pop("successor_canonical_sha256", None)
                record.pop("repair_to_audit_handoff_id", None)
                record.pop("consumption_claim", None)
            for field in (
                "claim_token",
                "claim_epoch_seconds",
                "claim_lease_seconds",
                "claim_lease_expires_at",
                "claim_lease_expires_epoch_seconds",
                "claim_authority_contract",
                "claim_authority_root_id",
                "claim_authority_generation",
                "claim_authority_session_fingerprint",
            ):
                record.pop(field, None)
            record["integrity_hmac"] = self._record_integrity_hmac(record)
            self.atomic_write_json(path, record)
            return record

    def rollback_consumed_repair_package(
        self,
        namespace: str,
        project_id: str,
        repair_package_id: str,
        *,
        work_order_id: str,
        claim_token: str,
        claim_generation: int,
        base_artifact_id: str,
        transaction_id: str,
        expected_successor_artifact_id: str,
        expected_successor_canonical_sha256: str,
        repair_to_audit_handoff_id: str,
    ) -> dict[str, Any]:
        """Rollback one exact consumed claim only before successor publication.

        A caller may observe an exception after ``commit_storyboard_successor``
        has durably published the successor.  In that case the repair package is
        already terminally consumed and must never be made reusable.  Bind the
        compensation to the caller's immutable transaction identity and inspect
        the base transaction under the same project lock before changing the
        package.
        """

        clean_id = safe_id(repair_package_id, "repair_package_id")
        clean_base_id = safe_id(base_artifact_id, "base_artifact_id")
        clean_transaction_id = safe_id(transaction_id, "transaction_id")
        clean_successor_id = safe_id(
            expected_successor_artifact_id, "expected_successor_artifact_id"
        )
        clean_handoff_id = safe_id(
            repair_to_audit_handoff_id, "repair_to_audit_handoff_id"
        )
        if not expected_successor_canonical_sha256:
            raise ValueError(
                "repair_package_rollback_identity_invalid: successor canonical hash is required"
            )
        directory = self.project_dir(namespace, project_id)
        path = (
            directory
            / "repair_packages"
            / f"{clean_id}.json"
        )
        base_path = directory / "artifacts" / f"{clean_base_id}.meta.json"
        successor_path = directory / "artifacts" / f"{clean_successor_id}.meta.json"
        handoff_path = directory / "handoffs" / f"{clean_handoff_id}.json"
        with self.project_write_lock(namespace, project_id):
            if not path.is_file():
                raise ValueError(
                    "repair_package_required: immutable package does not exist"
                )
            record = self.read_json(path)
            self._verify_record_integrity(record, record_id=clean_id)
            if not self.project_record_is_signed(record):
                raise ValueError("repair_package_signature_required")
            consumption = record.get("consumption_claim")
            if (
                record.get("status") != "consumed"
                or record.get("consumed_by_work_order_id") != work_order_id
                or not isinstance(consumption, dict)
                or consumption.get("claim_token") != claim_token
                or consumption.get("consumed_by_work_order_id")
                != work_order_id
                or record.get("claim_generation") != claim_generation
                or consumption.get("claim_generation") != claim_generation
            ):
                raise ValueError(
                    "repair_package_claim_conflict: consumed rollback claim is not current"
                )
            if (
                record.get("base_artifact_id") != clean_base_id
                or record.get("successor_artifact_id") != clean_successor_id
                or record.get("successor_canonical_sha256")
                != expected_successor_canonical_sha256
                or record.get("repair_to_audit_handoff_id") != clean_handoff_id
            ):
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: consumed package differs from the fixed transaction"
                )
            if not base_path.is_file():
                raise ValueError(
                    "repair_package_rollback_forbidden: base artifact metadata is missing"
                )
            base_record = self.read_json(base_path)
            base_metadata = base_record.get("metadata")
            if not isinstance(base_metadata, dict):
                raise ValueError(
                    "repair_package_rollback_forbidden: base artifact metadata is malformed"
                )
            transaction = base_metadata.get("successor_transaction")
            if (
                not isinstance(transaction, dict)
                or self._successor_transaction_id(transaction)
                != clean_transaction_id
                or str(transaction.get("base_artifact_id") or clean_base_id)
                != clean_base_id
            ):
                raise ValueError(
                    "successor_transaction_identity_conflict: rollback transaction is not current"
                )

            committed_id = self._committed_successor_id(base_metadata)
            transaction_state = str(transaction.get("state") or "")
            if transaction_state == "committed":
                if (
                    committed_id == clean_successor_id
                    and transaction.get("successor_artifact_id")
                    == clean_successor_id
                    and transaction.get("successor_canonical_hash")
                    == expected_successor_canonical_sha256
                    and base_metadata.get("successor_canonical_hash")
                    == expected_successor_canonical_sha256
                ):
                    # Exact committed state is terminal.  Returning the signed
                    # package unchanged makes compensation retries idempotent
                    # without resurrecting the one-shot repair authority.
                    return record
                raise ValueError(
                    "repair_package_rollback_forbidden: base has a different committed successor"
                )
            if committed_id:
                raise ValueError(
                    "repair_package_rollback_forbidden: committed successor pointer exists"
                )
            history = base_metadata.get("successor_transaction_history") or []
            if not isinstance(history, list):
                raise ValueError(
                    "successor_transaction_identity_conflict: malformed transaction history"
                )
            if any(
                isinstance(item, dict)
                and self._successor_transaction_id(item) == clean_transaction_id
                and item.get("state") == "committed"
                for item in history
            ):
                raise ValueError(
                    "repair_package_rollback_forbidden: committed successor exists in transaction history"
                )
            if transaction_state != "aborted":
                raise ValueError(
                    "repair_package_rollback_forbidden: successor transaction must be aborted before package release"
                )
            try:
                self._assert_claim_authority_unlocked(
                    directory,
                    project_id=project_id,
                    record=consumption,
                    work_order_id=work_order_id,
                    expected_role="repair",
                    error_code=(
                        "repair_package_claim_revoked: rollback belongs to "
                        "an inactive or expired window generation"
                    ),
                )
            except ValueError as exact_authority_error:
                # A new physical window may compensate an already-aborted
                # transaction only when it holds the current operation fence
                # and the consumed claim is a signed ancestor of that exact
                # logical repair authority.  The old token/session cannot use
                # this branch after rotation because it cannot acquire/bind
                # the current-generation operation lease.
                try:
                    repair_work_order, active = (
                        self._work_order_authority_unlocked(
                            directory,
                            project_id=project_id,
                            work_order_id=work_order_id,
                            expected_role="repair",
                            error_code=str(exact_authority_error),
                        )
                    )
                    self._assert_claim_authority_ancestor_unlocked(
                        directory,
                        project_id=project_id,
                        record=consumption,
                        work_order=repair_work_order,
                        active=active,
                        error_code=str(exact_authority_error),
                    )
                except ValueError:
                    raise exact_authority_error
            if not successor_path.is_file():
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: successor artifact metadata is missing"
                )
            successor_record = self.read_json(successor_path)
            successor_metadata = successor_record.get("metadata")
            if not isinstance(successor_metadata, dict):
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: successor artifact metadata is malformed"
                )
            candidate_transaction_id = str(
                successor_metadata.get("successor_transaction_id")
                or successor_metadata.get("repair_transaction_id")
                or ""
            )
            candidate_package_id = str(
                successor_metadata.get("repair_package_id")
                or successor_metadata.get("patch_repair_package_id")
                or ""
            )
            candidate_base_id = str(
                successor_metadata.get("successor_base_artifact_id")
                or successor_metadata.get("repair_base_artifact_id")
                or successor_metadata.get("patch_base_artifact_id")
                or ""
            )
            candidate_scope_hash = str(
                successor_metadata.get("successor_scope_hash")
                or successor_metadata.get("repair_scope_hash")
                or successor_metadata.get("patch_scope_hash")
                or ""
            )
            if (
                successor_record.get("project_id") != project_id
                or successor_record.get("source_artifact_id") != clean_base_id
                or successor_metadata.get("canonical_sha256")
                != expected_successor_canonical_sha256
                or candidate_transaction_id != clean_transaction_id
                or candidate_package_id != clean_id
                or candidate_base_id != clean_base_id
                or not candidate_scope_hash
                or candidate_scope_hash != str(transaction.get("scope_hash") or "")
            ):
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: successor candidate differs from the fixed transaction"
                )
            publication_state = self.artifact_publication_state(successor_record)
            if publication_state not in {"pending", "aborted", "rejected"}:
                raise ValueError(
                    "repair_package_rollback_forbidden: successor candidate is already published"
                )
            if self._artifact_is_published(directory, successor_record):
                raise ValueError(
                    "repair_package_rollback_forbidden: successor candidate has a committed downstream reference"
                )
            if not handoff_path.is_file():
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: repair_to_audit handoff is missing"
                )
            handoff = self.read_json(handoff_path)
            self._verify_record_integrity(handoff, record_id=clean_handoff_id)
            if not self.project_record_is_signed(handoff):
                raise ValueError(
                    "repair_package_rollback_identity_mismatch: repair_to_audit handoff is unsigned"
                )
            forbidden_handoff_fields = (
                "claimed_by_work_order_id",
                "claim_token",
                "consumed_by_work_order_id",
                "consumed_at",
                "consumption_claim",
                "audit_certificate_id",
                "latest_audit_certificate_id",
            )
            if (
                handoff.get("handoff_type") != "repair_to_audit"
                or handoff.get("immutable_repair_package_id") != clean_id
                or handoff.get("work_order_id") != work_order_id
                or handoff.get("storyboard_artifact_id") != clean_successor_id
                or handoff.get("canonical_sha256")
                != expected_successor_canonical_sha256
                or handoff.get("successor_transaction_id")
                != clean_transaction_id
                or handoff.get("successor_base_artifact_id") != clean_base_id
                or handoff.get("status") != "rejected"
                or handoff.get("publication_state")
                not in {"aborted", "rejected"}
                or any(handoff.get(field) for field in forbidden_handoff_fields)
            ):
                raise ValueError(
                    "repair_package_rollback_forbidden: repair_to_audit handoff has a live or mismatched downstream binding"
                )
            hub_path = directory / "hub.json"
            if not hub_path.is_file():
                raise ValueError(
                    "repair_package_rollback_forbidden: Hub projection is missing"
                )
            hub = self.read_json(hub_path)
            hub_handoffs = hub.get("handoffs") if isinstance(hub, dict) else None
            projections = [
                row
                for row in (
                    hub_handoffs if isinstance(hub_handoffs, list) else []
                )
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_handoff_id
            ]
            if len(projections) != 1:
                raise ValueError(
                    "repair_package_rollback_forbidden: Hub handoff projection is incomplete"
                )
            projection = projections[0]
            if (
                projection.get("status") != "rejected"
                or projection.get("publication_state")
                not in {"aborted", "rejected"}
                or any(projection.get(field) for field in forbidden_handoff_fields)
            ):
                raise ValueError(
                    "repair_package_rollback_forbidden: Hub handoff projection has downstream consumption"
                )
            certificate_dir = directory / "certificates"
            for certificate_path in (
                sorted(certificate_dir.glob("*.json"))
                if certificate_dir.is_dir()
                else []
            ):
                certificate = self.read_json(certificate_path)
                if isinstance(certificate, dict) and clean_handoff_id in {
                    str(certificate.get("handoff_id") or ""),
                    str(certificate.get("production_handoff_id") or ""),
                    str(certificate.get("repair_to_audit_handoff_id") or ""),
                }:
                    raise ValueError(
                        "repair_package_rollback_forbidden: independent audit evidence already references the handoff"
                    )
            history = record.setdefault("claim_history", [])
            if not isinstance(history, list):
                raise ValueError("repair_package_claim_history_invalid")
            history.append(
                {
                    **copy.deepcopy(consumption),
                    "state": "rolled_back_after_successor_cas_failure",
                    "rolled_back_at": utc_stamp(),
                }
            )
            del history[:-MAX_CONSUMPTION_CLAIM_HISTORY]
            record["status"] = "issued"
            for field in (
                "consumed_by_work_order_id",
                "successor_artifact_id",
                "successor_canonical_sha256",
                "repair_to_audit_handoff_id",
                "consumption_claim",
            ):
                record.pop(field, None)
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

    def set_successor_candidate_publication(
        self,
        namespace: str,
        project_id: str,
        *,
        work_order_id: str,
        artifact_id: str,
        canonical_artifact_id: str,
        handoff_id: str,
        transaction_id: str,
        state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """CAS all successor publication projections under one project lock.

        The base successor transaction is the publication authority.  Audit
        may claim the handoff immediately after that CAS, so this finalizer
        must read the *current* signed handoff while holding the same lock and
        preserve any legitimate claim/consumption lifecycle fields instead of
        writing an older service-layer snapshot over them.
        """

        clean_work_order_id = safe_id(work_order_id, "work_order_id")
        clean_artifact_id = safe_id(artifact_id, "artifact_id")
        clean_canonical_id = safe_id(
            canonical_artifact_id, "canonical_artifact_id"
        )
        clean_handoff_id = safe_id(handoff_id, "handoff_id")
        clean_transaction_id = safe_id(transaction_id, "transaction_id")
        if state not in {"committed", "aborted"}:
            raise ValueError("invalid successor candidate publication state")

        directory = self.project_dir(namespace, project_id)
        candidate_ids = list(
            dict.fromkeys([clean_artifact_id, clean_canonical_id])
        )
        artifact_paths = {
            candidate_id: directory
            / "artifacts"
            / f"{candidate_id}.meta.json"
            for candidate_id in candidate_ids
        }
        handoff_path = directory / "handoffs" / f"{clean_handoff_id}.json"
        hub_path = directory / "hub.json"

        with self.project_write_lock(namespace, project_id):
            candidate_records: dict[str, dict[str, Any]] = {}
            descriptor: tuple[str, str, str] | None = None
            for candidate_id, candidate_path in artifact_paths.items():
                if not candidate_path.is_file():
                    raise ValueError(
                        "successor publication candidate metadata is missing"
                    )
                candidate = self.read_json(candidate_path)
                metadata = candidate.get("metadata")
                if (
                    candidate.get("artifact_id") != candidate_id
                    or candidate.get("project_id") != project_id
                    or candidate.get("work_order_id") != clean_work_order_id
                    or not isinstance(metadata, dict)
                    or metadata.get("successor_transaction_id")
                    != clean_transaction_id
                ):
                    raise ValueError(
                        "successor_transaction_conflict: artifact candidate belongs to another transaction"
                    )
                payload_path = (directory / str(candidate.get("relative_path") or "")).resolve()
                if (
                    directory.resolve() not in payload_path.parents
                    or not payload_path.is_file()
                    or sha256_bytes(payload_path.read_bytes())
                    != candidate.get("fingerprint")
                ):
                    raise ValueError(
                        "artifact_raw_fingerprint_mismatch: successor candidate bytes changed"
                    )
                current_state = self.artifact_publication_state(candidate)
                allowed_states = (
                    {"pending", "committed"}
                    if state == "committed"
                    else {"pending", "aborted", "rejected"}
                )
                if current_state not in allowed_states:
                    raise ValueError(
                        "successor_publication_state_mismatch: candidate cannot enter requested state"
                    )
                current_descriptor = (
                    str(metadata.get("successor_base_artifact_id") or ""),
                    str(metadata.get("successor_kind") or ""),
                    str(metadata.get("successor_scope_hash") or ""),
                )
                if not all(current_descriptor):
                    raise ValueError(
                        "successor_transaction_conflict: candidate descriptor is incomplete"
                    )
                if descriptor is None:
                    descriptor = current_descriptor
                elif current_descriptor != descriptor:
                    raise ValueError(
                        "successor_transaction_conflict: artifact pair descriptors differ"
                    )
                candidate_records[candidate_id] = candidate

            assert descriptor is not None
            base_artifact_id, successor_kind, scope_hash = descriptor
            base_artifact_id = safe_id(
                base_artifact_id, "successor_base_artifact_id"
            )
            base_path = directory / "artifacts" / f"{base_artifact_id}.meta.json"
            if not base_path.is_file():
                raise ValueError("unknown successor base artifact")
            base_record = self.read_json(base_path)
            base_metadata = base_record.get("metadata")
            base_transaction = (
                base_metadata.get("successor_transaction")
                if isinstance(base_metadata, dict)
                else None
            )
            if (
                not isinstance(base_transaction, dict)
                or self._successor_transaction_id(base_transaction)
                != clean_transaction_id
                or base_transaction.get("successor_kind") != successor_kind
                or base_transaction.get("scope_hash") != scope_hash
                or base_transaction.get("state") != state
            ):
                raise ValueError(
                    "successor_transaction_conflict: base transaction is not in the requested terminal state"
                )
            if state == "committed" and (
                base_transaction.get("successor_artifact_id")
                != clean_artifact_id
                or base_metadata.get("successor_artifact_id")
                != clean_artifact_id
                or base_transaction.get("successor_canonical_hash")
                != candidate_records[clean_artifact_id]
                .get("metadata", {})
                .get("canonical_sha256")
            ):
                raise ValueError(
                    "successor_transaction_conflict: committed base differs from candidate"
                )

            if not handoff_path.is_file():
                raise ValueError("successor publication handoff is missing")
            handoff = self.read_json(handoff_path)
            self._verify_record_integrity(
                handoff, record_id=clean_handoff_id
            )
            if (
                not self.project_record_is_signed(handoff)
                or handoff.get("handoff_id") != clean_handoff_id
                or handoff.get("work_order_id") != clean_work_order_id
                or handoff.get("storyboard_artifact_id")
                != clean_artifact_id
                or handoff.get("canonical_artifact_id")
                != clean_canonical_id
                or handoff.get("successor_transaction_id")
                != clean_transaction_id
                or handoff.get("successor_base_artifact_id")
                != base_artifact_id
                or handoff.get("successor_kind") != successor_kind
                or handoff.get("successor_scope_hash") != scope_hash
            ):
                raise ValueError(
                    "successor_transaction_conflict: signed handoff differs from candidate"
                )
            if state == "committed" and handoff.get("status") == "rejected":
                raise ValueError(
                    "successor_publication_state_mismatch: rejected handoff cannot be published"
                )
            live_handoff_fields = (
                "claimed_by_work_order_id",
                "claim_token",
                "consumed_by_work_order_id",
                "consumption_claim",
                "audit_certificate_id",
                "latest_audit_certificate_id",
            )
            if state == "aborted" and any(
                handoff.get(field) for field in live_handoff_fields
            ):
                raise ValueError(
                    "successor_publication_state_mismatch: claimed handoff cannot be aborted"
                )

            if not hub_path.is_file():
                raise ValueError("successor publication Hub projection is missing")
            hub = self.read_json(hub_path)
            hub_handoffs = hub.get("handoffs") if isinstance(hub, dict) else None
            projection_matches = [
                (index, row)
                for index, row in enumerate(
                    hub_handoffs if isinstance(hub_handoffs, list) else []
                )
                if isinstance(row, dict)
                and row.get("handoff_id") == clean_handoff_id
            ]
            if len(projection_matches) != 1:
                raise ValueError(
                    "successor publication Hub handoff projection is incomplete"
                )
            projection_index, projection = projection_matches[0]
            if (
                projection.get("work_order_id") != clean_work_order_id
                or projection.get("storyboard_artifact_id")
                != clean_artifact_id
                or projection.get("canonical_artifact_id")
                != clean_canonical_id
                or projection.get("successor_transaction_id")
                != clean_transaction_id
            ):
                raise ValueError(
                    "successor publication Hub handoff projection conflicts with signed record"
                )
            work_orders = hub.get("work_orders") if isinstance(hub, dict) else None
            work_order_matches = [
                row
                for row in (
                    work_orders if isinstance(work_orders, list) else []
                )
                if isinstance(row, dict)
                and row.get("work_order_id") == clean_work_order_id
            ]
            if len(work_order_matches) != 1:
                raise ValueError("successor publication work order is missing")

            timestamp: str | None = None

            def change_timestamp() -> str:
                nonlocal timestamp
                if timestamp is None:
                    timestamp = utc_stamp()
                return timestamp

            for candidate_id, candidate in list(candidate_records.items()):
                current_state = self.artifact_publication_state(candidate)
                if current_state == state:
                    continue
                updated_candidate = copy.deepcopy(candidate)
                metadata = updated_candidate.setdefault("metadata", {})
                metadata["publication_state"] = state
                metadata["successor_transaction_id"] = clean_transaction_id
                if state == "committed":
                    metadata["published_at"] = change_timestamp()
                    metadata.pop("publication_aborted_at", None)
                    metadata.pop("publication_abort_reason", None)
                else:
                    metadata["publication_aborted_at"] = change_timestamp()
                    metadata["publication_abort_reason"] = str(
                        reason or "successor transaction aborted"
                    )[:512]
                self.atomic_write_json(
                    artifact_paths[candidate_id], updated_candidate
                )
                candidate_records[candidate_id] = updated_candidate

            handoff_state = str(
                handoff.get("publication_state") or "committed"
            )
            handoff_status = str(handoff.get("status") or "")
            handoff_needs_update = False
            if state == "committed":
                # A claim or consumption can legitimately win immediately
                # after the base CAS.  Its signed record is immutable from the
                # publication finalizer's perspective; the base transaction
                # already makes the pending descriptor consumable.
                handoff_needs_update = (
                    handoff_state != "committed"
                    and handoff_status == "prepared"
                )
            else:
                handoff_needs_update = not (
                    handoff_state == "aborted"
                    and handoff_status == "rejected"
                )
            if handoff_needs_update:
                updated_handoff = copy.deepcopy(handoff)
                updated_handoff["publication_state"] = state
                updated_handoff["successor_transaction_id"] = (
                    clean_transaction_id
                )
                if state == "committed":
                    updated_handoff["published_at"] = change_timestamp()
                    updated_handoff.pop("rejected_at", None)
                    updated_handoff.pop("rejection_reason", None)
                else:
                    updated_handoff["status"] = "rejected"
                    updated_handoff["rejected_at"] = change_timestamp()
                    updated_handoff["rejection_reason"] = str(
                        reason or "successor transaction aborted"
                    )[:512]
                updated_handoff["integrity_hmac"] = (
                    self._record_integrity_hmac(updated_handoff)
                )
                self.atomic_write_json(handoff_path, updated_handoff)
                handoff = updated_handoff

            desired_projection = {
                key: copy.deepcopy(value)
                for key, value in handoff.items()
                if key not in {"record_id", "project_id", "integrity_hmac"}
            }
            hub_dirty = projection != desired_projection
            if hub_dirty:
                hub_handoffs[projection_index] = desired_projection
            deliverable = next(
                (
                    row
                    for row in hub.setdefault("deliverables", [])
                    if isinstance(row, dict)
                    and row.get("deliverable_id") == clean_artifact_id
                ),
                None,
            )
            if deliverable is not None:
                if deliverable.get("publication_state") != state:
                    deliverable["publication_state"] = state
                    hub_dirty = True
                if deliverable.get("status") != "draft":
                    deliverable["status"] = "draft"
                    hub_dirty = True
                latest = hub.setdefault("project_state", {}).setdefault(
                    "latest_artifacts", {}
                ).get("storyboard")
                if isinstance(latest, dict) and latest.get(
                    "deliverable_id"
                ) == clean_artifact_id:
                    hub["project_state"]["latest_artifacts"].pop(
                        "storyboard", None
                    )
                    hub_dirty = True
            work_order = work_order_matches[0]
            if handoff.get("status") not in {"consumed", "rejected"}:
                awaiting_audit = state == "committed"
                if (
                    work_order.get("awaiting_independent_audit")
                    != awaiting_audit
                ):
                    work_order["awaiting_independent_audit"] = awaiting_audit
                    hub_dirty = True
            if state == "aborted" and work_order.get("status") == "validating":
                current = str(work_order["status"])
                work_order["status"] = "failed_current"
                work_order["updated_at"] = change_timestamp()
                history = work_order.setdefault("state_history", [])
                if isinstance(history, list):
                    history.append(
                        {
                            "timestamp": change_timestamp(),
                            "from": current,
                            "to": "failed_current",
                        }
                    )
                hub.setdefault("project_state", {})[
                    "current_stage"
                ] = "failed_current"
                hub_dirty = True
            if hub_dirty:
                hub["updated_at"] = change_timestamp()
                self.atomic_write_json(hub_path, hub)
            return {
                "artifacts": copy.deepcopy(candidate_records),
                "handoff": copy.deepcopy(handoff),
            }

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
