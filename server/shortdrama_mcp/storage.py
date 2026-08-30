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
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_RECORD_KINDS = {
    "handoffs",
    "certificates",
    "telemetry",
    "diagnostics",
    "repair_packages",
}
DEFAULT_SUCCESSOR_LEASE_SECONDS = 300
MAX_SUCCESSOR_LEASE_SECONDS = 86_400
MAX_SUCCESSOR_TRANSACTION_HISTORY = 32
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
    def __init__(self, root: Path, public_base_url: str):
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create_secret()
        self._integrity_secret = self._load_or_create_integrity_secret()

    def _load_or_create_secret(self) -> bytes:
        path = self.root / ".download_secret"
        with self.lock("download-secret"):
            if path.is_file():
                raw = path.read_bytes().strip()
                if len(raw) >= 32:
                    return raw
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
                raw = path.read_bytes().strip()
                if len(raw) >= 32:
                    return raw
            raw = secrets.token_bytes(48)
            self.atomic_write_bytes(path, raw)
            with contextlib.suppress(OSError):
                path.chmod(0o600)
            return raw

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
    ) -> dict[str, Any]:
        directory = self.project_dir(namespace, project_id) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        artifact_id = f"ART-{uuid.uuid4().hex[:16]}"
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

    def _record_integrity_hmac(self, value: dict[str, Any]) -> str:
        unsigned = {key: item for key, item in value.items() if key != "integrity_hmac"}
        payload = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._integrity_secret, payload, hashlib.sha256).hexdigest()

    def project_record_is_signed(self, value: dict[str, Any]) -> bool:
        signature = value.get("integrity_hmac")
        if not isinstance(signature, str):
            return False
        return hmac.compare_digest(signature, self._record_integrity_hmac(value))

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
        expected = self._record_integrity_hmac(value)
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
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
    ) -> dict[str, Any]:
        """Atomically claim one signed handoff, allowing same-consumer retries."""

        clean_id = safe_id(handoff_id, "handoff_id")
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
            if status == "consumed":
                if owner == consumer_work_order_id:
                    result = dict(record)
                    result["idempotent_replay"] = True
                    return result
                raise ValueError("handoff_state_conflict: handoff was consumed by another audit authority")
            if status == "consuming":
                claimed_at = float(record.get("claim_epoch_seconds") or 0)
                if owner != consumer_work_order_id and time.time() - claimed_at < 300:
                    raise ValueError("handoff_state_conflict: handoff is being consumed")
            elif status != "prepared":
                raise ValueError(f"handoff_state_conflict: handoff status is {status!r}")
            record["status"] = "consuming"
            record["claimed_by_work_order_id"] = consumer_work_order_id
            record["claim_epoch_seconds"] = time.time()
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
            ):
                raise ValueError("handoff_state_conflict: handoff claim is not current")
            record["status"] = "consumed"
            record["consumed_by_work_order_id"] = consumer_work_order_id
            record["audit_certificate_id"] = audit_certificate_id
            record["consumed_at"] = utc_stamp()
            record.pop("claimed_by_work_order_id", None)
            record.pop("claim_epoch_seconds", None)
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
        expiry = int(time.time()) + max(60, min(int(lifetime_seconds), 86400))
        payload = json.dumps(
            {
                "namespace_key": self.namespace_key(namespace),
                "project_id": project_id,
                "artifact_id": artifact_id,
                "exp": expiry,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        payload_token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature_token = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        token = f"{payload_token}.{signature_token}"
        return f"{self.public_base_url}/files/{token}/{slug_filename(file_name)}"

    def resolve_download_token(self, token: str) -> tuple[dict[str, Any], Path]:
        try:
            payload_token, signature_token = token.split(".", 1)
            payload = base64.urlsafe_b64decode((payload_token + "=" * (-len(payload_token) % 4)).encode("ascii"))
            signature = base64.urlsafe_b64decode(
                (signature_token + "=" * (-len(signature_token) % 4)).encode("ascii")
            )
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid signature")
            value = json.loads(payload.decode("utf-8"))
            if int(value["exp"]) < int(time.time()):
                raise ValueError("expired")
            namespace_key = safe_id(str(value["namespace_key"]), "namespace_key")
            project_id = safe_id(str(value["project_id"]), "project_id")
            artifact_id = safe_id(str(value["artifact_id"]), "artifact_id")
        except Exception as exc:
            raise ValueError("invalid or expired download token") from exc
        directory = self.projects_dir / namespace_key / project_id
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        if not meta_path.is_file():
            raise ValueError("artifact is unavailable")
        record = self.read_json(meta_path)
        self._assert_artifact_published(directory, record)
        payload_path = (directory / str(record["relative_path"])).resolve()
        if directory.resolve() not in payload_path.parents or not payload_path.is_file():
            raise ValueError("artifact payload is unavailable")
        return record, payload_path
