from __future__ import annotations

import base64
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
PROJECT_RECORD_KINDS = {"handoffs", "certificates", "telemetry", "repair_packages"}


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

    def get_artifact(self, namespace: str, project_id: str, artifact_id: str) -> tuple[dict[str, Any], Path]:
        safe_id(artifact_id, "artifact_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        if not meta_path.is_file():
            raise ValueError(f"unknown artifact_id: {artifact_id}")
        record = self.read_json(meta_path)
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

    def list_artifacts(self, namespace: str, project_id: str) -> list[dict[str, Any]]:
        directory = self.project_dir(namespace, project_id) / "artifacts"
        result = []
        for meta_path in sorted(directory.glob("ART-*.meta.json")):
            with contextlib.suppress(Exception):
                result.append(self.read_json(meta_path))
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

        safe_id(artifact_id, "artifact_id")
        safe_id(claim_id, "claim_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            metadata = record.setdefault("metadata", {})
            if metadata.get("canonical_sha256") != expected_canonical_hash:
                raise ValueError("canonical_hash_conflict: base canonical hash changed")
            if metadata.get("patch_successor_artifact_id") or metadata.get("active_patch_claim"):
                raise ValueError("canonical_hash_conflict: base canonical hash was already consumed")
            metadata["active_patch_claim"] = {
                "claim_id": claim_id,
                "expected_canonical_hash": expected_canonical_hash,
                "claimed_at": utc_stamp(),
            }
            self.atomic_write_json(meta_path, record)
            return record

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

        safe_id(artifact_id, "artifact_id")
        safe_id(claim_id, "claim_id")
        if successor_artifact_id is not None:
            safe_id(successor_artifact_id, "successor_artifact_id")
        directory = self.project_dir(namespace, project_id)
        meta_path = directory / "artifacts" / f"{artifact_id}.meta.json"
        with self.lock(f"{namespace}-{project_id}"):
            if not meta_path.is_file():
                raise ValueError(f"unknown artifact_id: {artifact_id}")
            record = self.read_json(meta_path)
            metadata = record.setdefault("metadata", {})
            claim = metadata.get("active_patch_claim")
            if not isinstance(claim, dict) or claim.get("claim_id") != claim_id:
                raise ValueError("canonical_hash_conflict: patch claim is no longer current")
            metadata["active_patch_claim"] = None
            if successor_artifact_id is not None:
                metadata["patch_successor_artifact_id"] = successor_artifact_id
                metadata["patch_successor_canonical_hash"] = successor_canonical_hash
                metadata["patch_completed_at"] = utc_stamp()
            self.atomic_write_json(meta_path, record)
            return record

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
        payload_path = (directory / str(record["relative_path"])).resolve()
        if directory.resolve() not in payload_path.parents or not payload_path.is_file():
            raise ValueError("artifact payload is unavailable")
        return record, payload_path
