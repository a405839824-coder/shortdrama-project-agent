from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse

from .file_io import download_openai_file
from .models import OpenAIFile, ToolResponse
from .storage import ProjectStore, safe_id, sha256_bytes, utc_stamp


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STORAGE_MODES = frozenset({"persistent", "library"})
DEFAULT_SNAPSHOT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_SNAPSHOT_MAX_FILES = 20_000
DEFAULT_EXPANDED_MAX_BYTES = 2 * 1024 * 1024 * 1024


def _strict_sha256(value: str, field: str) -> str:
    clean = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(clean):
        raise ValueError(f"invalid {field}")
    return clean


def _strict_library_id(value: str, field: str, prefix: str) -> str:
    clean = str(value or "").strip()
    if not clean.startswith(prefix):
        raise ValueError(f"invalid {field}")
    return safe_id(clean, field)


def _strict_library_path(value: str) -> str:
    clean = str(value or "").strip()
    if (
        not clean.startswith("/")
        or len(clean) > 2_048
        or any(ord(character) < 32 for character in clean)
    ):
        raise ValueError("invalid library_path")
    return clean


class ExternalProjectStorage:
    """Bridge persistent Library snapshots to an ephemeral project runtime.

    The ChatGPT host owns Library authentication and transfers snapshot bytes
    through an ``openai/fileParams`` argument.  The plugin persists only a
    signed pointer registry.  A mounted project lives below the operating
    system temporary directory and therefore never consumes the Railway data
    volume.
    """

    def __init__(
        self,
        service: Any,
        settings: Any,
        *,
        mode: str | None = None,
        runtime_root: Path | None = None,
    ) -> None:
        self.service = service
        self.settings = settings
        self.store: ProjectStore = service.store
        configured = str(
            mode
            or os.getenv("SHORTDRAMA_PROJECT_STORAGE_MODE", "persistent")
        ).strip().lower()
        if configured not in STORAGE_MODES:
            raise RuntimeError(
                "SHORTDRAMA_PROJECT_STORAGE_MODE must be persistent or library"
            )
        self.mode = configured
        self.persistent_projects_dir = self.store.projects_dir
        configured_runtime_root = os.getenv("SHORTDRAMA_RUNTIME_DATA_DIR")
        self.runtime_root = (
            runtime_root
            or (
                Path(configured_runtime_root).expanduser().resolve()
                if configured_runtime_root
                else Path(tempfile.gettempdir()) / "shortdrama-library-runtime"
            )
        )
        self.runtime_projects_dir = self.runtime_root / "projects"
        self.exports_dir = self.runtime_root / "exports"
        self.registry_dir = self.store.root / "library_project_registry"
        self.runtime_projects_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._exports: dict[str, dict[str, Any]] = {}
        if self.mode == "library":
            self.store.projects_dir = self.runtime_projects_dir

    def _namespace_registry_dir(self, namespace: str) -> Path:
        path = self.registry_dir / self.store.namespace_key(namespace)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _registry_path(self, namespace: str, project_id: str) -> Path:
        clean_project_id = safe_id(project_id, "project_id")
        return self._namespace_registry_dir(namespace) / f"{clean_project_id}.json"

    def _read_registry(self, namespace: str, project_id: str) -> dict[str, Any]:
        path = self._registry_path(namespace, project_id)
        if not path.is_file():
            raise ValueError(f"unknown library project snapshot: {project_id}")
        record = self.store.read_json(path)
        if (
            not isinstance(record, dict)
            or record.get("project_id") != project_id
            or record.get("namespace_key") != self.store.namespace_key(namespace)
            or not isinstance(record.get("integrity_hmac"), str)
            or not self.store._record_integrity_signature_matches(
                record, str(record.get("integrity_hmac"))
            )
        ):
            raise ValueError("library_project_registry_integrity_mismatch")
        return record

    def _write_registry(
        self, namespace: str, project_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        record = dict(value)
        record["record_type"] = "chatgpt_library_project_pointer"
        record["project_id"] = project_id
        record["namespace_key"] = self.store.namespace_key(namespace)
        record["integrity_hmac"] = self.store._record_integrity_hmac(record)
        self.store.atomic_write_json(self._registry_path(namespace, project_id), record)
        return record

    @staticmethod
    def _directory_size(path: Path) -> int:
        if not path.is_dir():
            return 0
        total = 0
        for candidate in path.rglob("*"):
            with contextlib.suppress(OSError):
                if candidate.is_file() and not candidate.is_symlink():
                    total += candidate.stat().st_size
        return total

    @staticmethod
    def tree_manifest(project_dir: Path) -> tuple[str, int, int]:
        if not project_dir.is_dir():
            raise ValueError("project snapshot source is unavailable")
        digest = hashlib.sha256()
        count = 0
        size = 0
        for path in sorted(
            (candidate for candidate in project_dir.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(project_dir).as_posix(),
        ):
            relative = path.relative_to(project_dir).as_posix()
            payload = path.read_bytes()
            file_sha256 = sha256_bytes(payload)
            digest.update(
                json.dumps(
                    [relative, len(payload), file_sha256],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
            count += 1
            size += len(payload)
        return digest.hexdigest(), count, size

    def _archive_prefix(self, namespace: str, project_id: str) -> str:
        return (
            f"projects/{self.store.namespace_key(namespace)}/"
            f"{safe_id(project_id, 'project_id')}/"
        )

    def _mounted_project_dir(self, namespace: str, project_id: str) -> Path:
        return (
            self.runtime_projects_dir
            / self.store.namespace_key(namespace)
            / safe_id(project_id, "project_id")
        )

    def _persistent_project_dir(self, namespace: str, project_id: str) -> Path:
        return (
            self.persistent_projects_dir
            / self.store.namespace_key(namespace)
            / safe_id(project_id, "project_id")
        )

    def _verified_snapshot(
        self,
        namespace: str,
        *,
        project_id: str,
        snapshot: OpenAIFile,
        expected_bundle_sha256: str,
        expected_tree_sha256: str,
        install: bool,
    ) -> dict[str, Any]:
        data, file_name, _mime_type = download_openai_file(snapshot, self.settings)
        if len(data) > DEFAULT_SNAPSHOT_MAX_BYTES:
            raise ValueError("library_project_snapshot_payload_limit")
        actual_bundle_sha256 = sha256_bytes(data)
        if not hmac.compare_digest(
            actual_bundle_sha256,
            _strict_sha256(expected_bundle_sha256, "bundle_sha256"),
        ):
            raise ValueError("library_project_snapshot_bundle_mismatch")
        tree_sha256, file_count, expanded_size = self._extract_project(
            data,
            namespace=namespace,
            project_id=project_id,
            expected_tree_sha256=_strict_sha256(
                expected_tree_sha256, "expected_tree_sha256"
            ),
            install=install,
        )
        return {
            "file_name": file_name,
            "bundle_sha256": actual_bundle_sha256,
            "tree_sha256": tree_sha256,
            "file_count": file_count,
            "expanded_size_bytes": expanded_size,
        }

    def register_existing_snapshot(
        self,
        namespace: str,
        *,
        project_id: str,
        bundle_library_file_id: str,
        bundle_file_id: str,
        library_path: str,
        bundle_sha256: str,
        expected_tree_sha256: str,
        expected_generation: int = 0,
    ) -> dict[str, Any]:
        clean_bundle_sha256 = _strict_sha256(bundle_sha256, "bundle_sha256")
        clean_tree_sha256 = _strict_sha256(
            expected_tree_sha256, "expected_tree_sha256"
        )
        library_file_id = _strict_library_id(
            bundle_library_file_id, "bundle_library_file_id", "libfile_"
        )
        file_id = _strict_library_id(bundle_file_id, "bundle_file_id", "file_"
        )
        clean_library_path = _strict_library_path(library_path)
        if (
            not isinstance(expected_generation, int)
            or isinstance(expected_generation, bool)
            or expected_generation < 0
        ):
            raise ValueError("invalid expected_generation")

        project_dir = self._persistent_project_dir(namespace, project_id)
        actual_tree_sha256, file_count, expanded_size = self.tree_manifest(project_dir)
        if not hmac.compare_digest(actual_tree_sha256, clean_tree_sha256):
            raise ValueError("project_snapshot_tree_mismatch")

        lock_name = (
            f"library-project-registry-{self.store.namespace_key(namespace)}-"
            f"{project_id}"
        )
        with self.store.lock(lock_name):
            path = self._registry_path(namespace, project_id)
            previous: dict[str, Any] | None = None
            if path.is_file():
                previous = self._read_registry(namespace, project_id)
                same_snapshot = bool(
                    previous.get("bundle_sha256") == clean_bundle_sha256
                    and previous.get("tree_sha256") == actual_tree_sha256
                    and previous.get("bundle_library_file_id") == library_file_id
                    and previous.get("bundle_file_id") == file_id
                    and previous.get("library_path") == clean_library_path
                )
                if same_snapshot:
                    return previous
                if int(previous.get("generation") or 0) != expected_generation:
                    raise ValueError("library_project_registry_generation_conflict")
            elif expected_generation != 0:
                raise ValueError("library_project_registry_generation_conflict")

            record = self._write_registry(
                namespace,
                project_id,
                {
                    "generation": expected_generation + 1,
                    "status": "registered_pending_library_mode",
                    "bundle_library_file_id": library_file_id,
                    "bundle_file_id": file_id,
                    "library_path": clean_library_path,
                    "bundle_sha256": clean_bundle_sha256,
                    "archive_prefix": self._archive_prefix(namespace, project_id),
                    "tree_sha256": actual_tree_sha256,
                    "file_count": file_count,
                    "expanded_size_bytes": expanded_size,
                    "registered_at": utc_stamp(),
                    "previous_generation": (
                        int(previous.get("generation") or 0) if previous else None
                    ),
                },
            )
        return record

    @staticmethod
    def _safe_archive_member(name: str) -> PurePosixPath:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("library_project_snapshot_unsafe_member")
        return path

    def _extract_project(
        self,
        data: bytes,
        *,
        namespace: str,
        project_id: str,
        expected_tree_sha256: str,
        install: bool = True,
    ) -> tuple[str, int, int]:
        prefix = self._archive_prefix(namespace, project_id)
        expanded_bytes = 0
        selected_count = 0
        with tempfile.TemporaryDirectory(
            prefix="mount-", dir=self.runtime_root
        ) as staging_value:
            staging = Path(staging_value)
            try:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                    selected: list[tarfile.TarInfo] = []
                    for member in archive.getmembers():
                        member_path = self._safe_archive_member(member.name)
                        if not member_path.as_posix().startswith(prefix):
                            continue
                        if member.issym():
                            raise ValueError("library_project_snapshot_symlink_forbidden")
                        if member.islnk():
                            link_path = self._safe_archive_member(member.linkname)
                            if not link_path.as_posix().startswith(prefix):
                                raise ValueError(
                                    "library_project_snapshot_external_hardlink_forbidden"
                                )
                        if member.isfile():
                            expanded_bytes += int(member.size)
                            selected_count += 1
                        if selected_count > DEFAULT_SNAPSHOT_MAX_FILES:
                            raise ValueError("library_project_snapshot_file_limit")
                        if expanded_bytes > DEFAULT_EXPANDED_MAX_BYTES:
                            raise ValueError("library_project_snapshot_expanded_limit")
                        selected.append(member)
                    if not selected or selected_count == 0:
                        raise ValueError("library_project_snapshot_project_missing")
                    archive.extractall(staging, members=selected, filter="data")
            except (OSError, tarfile.TarError) as exc:
                raise ValueError("library_project_snapshot_invalid") from exc

            extracted = staging / prefix.rstrip("/")
            actual_tree_sha256, file_count, actual_size = self.tree_manifest(extracted)
            if not hmac.compare_digest(actual_tree_sha256, expected_tree_sha256):
                raise ValueError("library_project_snapshot_tree_mismatch")
            if install:
                target = self._mounted_project_dir(namespace, project_id)
                target.parent.mkdir(parents=True, exist_ok=True)
                with self.store.lock(
                    f"library-project-mount-{self.store.namespace_key(namespace)}-{project_id}"
                ):
                    if target.exists():
                        shutil.rmtree(target)
                    os.replace(extracted, target)
            return actual_tree_sha256, file_count, actual_size

    def mount_snapshot(
        self,
        namespace: str,
        *,
        project_id: str,
        snapshot: OpenAIFile,
    ) -> dict[str, Any]:
        if self.mode != "library":
            raise ValueError("library_project_mode_inactive")
        registry = self._read_registry(namespace, project_id)
        verified = self._verified_snapshot(
            namespace=namespace,
            project_id=project_id,
            snapshot=snapshot,
            expected_bundle_sha256=str(registry["bundle_sha256"]),
            expected_tree_sha256=str(registry["tree_sha256"]),
            install=True,
        )
        return {
            "project_id": project_id,
            **verified,
            "generation": registry["generation"],
            "mounted_at": utc_stamp(),
            "storage_location": "ephemeral_runtime",
        }

    def export_snapshot(self, namespace: str, project_id: str) -> dict[str, Any]:
        if self.mode != "library":
            raise ValueError("library_project_mode_inactive")
        project_dir = self._mounted_project_dir(namespace, project_id)
        tree_sha256, file_count, expanded_size = self.tree_manifest(project_dir)
        export_id = f"LEX-{uuid.uuid4().hex[:24]}"
        path = self.exports_dir / f"{export_id}.tgz"
        prefix = self._archive_prefix(namespace, project_id).rstrip("/")
        with tarfile.open(path, "w:gz", compresslevel=6) as archive:
            archive.add(project_dir, arcname=prefix, recursive=True)
        bundle_sha256 = sha256_bytes(path.read_bytes())
        value = {
            "export_id": export_id,
            "namespace_key": self.store.namespace_key(namespace),
            "project_id": project_id,
            "path": str(path),
            "file_name": f"{project_id}_library_snapshot.tgz",
            "bundle_sha256": bundle_sha256,
            "tree_sha256": tree_sha256,
            "file_count": file_count,
            "expanded_size_bytes": expanded_size,
            "size_bytes": path.stat().st_size,
            "created_at": utc_stamp(),
            "expires_epoch": int(time.time()) + 3_600,
        }
        self._exports[export_id] = value
        return {**value, "download_url": self._signed_export_url(value)}

    def _signed_export_url(self, value: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "export_id": value["export_id"],
                "namespace_key": value["namespace_key"],
                "project_id": value["project_id"],
                "bundle_sha256": value["bundle_sha256"],
                "size_bytes": value["size_bytes"],
                "exp": value["expires_epoch"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self.store._secret, payload, hashlib.sha256).digest()
        encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        encoded_signature = (
            base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )
        return (
            f"{self.settings.public_base_url}/library-project-export/"
            f"{encoded_payload}.{encoded_signature}/"
            f"{quote(str(value['file_name']))}"
        )

    def resolve_export(self, token: str) -> dict[str, Any]:
        try:
            payload_token, signature_token = token.split(".", 1)
            payload = base64.urlsafe_b64decode(
                (payload_token + "=" * (-len(payload_token) % 4)).encode("ascii")
            )
            signature = base64.urlsafe_b64decode(
                (signature_token + "=" * (-len(signature_token) % 4)).encode("ascii")
            )
            expected = hmac.new(self.store._secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload.decode("utf-8"))
            export_id = safe_id(str(value["export_id"]), "export_id")
            if int(value["exp"]) < int(time.time()):
                raise ValueError
            export = self._exports[export_id]
            path = Path(str(export["path"]))
            if (
                export.get("namespace_key") != value.get("namespace_key")
                or export.get("project_id") != value.get("project_id")
                or export.get("bundle_sha256") != value.get("bundle_sha256")
                or export.get("size_bytes") != value.get("size_bytes")
                or not path.is_file()
                or path.stat().st_size != int(value["size_bytes"])
                or sha256_bytes(path.read_bytes()) != value["bundle_sha256"]
            ):
                raise ValueError
            return export
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid or expired library project export") from exc

    def commit_export_pointer(
        self,
        namespace: str,
        *,
        project_id: str,
        export_id: str,
        expected_generation: int,
        bundle_library_file_id: str,
        bundle_file_id: str,
        library_path: str,
        bundle_sha256: str,
        tree_sha256: str,
    ) -> dict[str, Any]:
        clean_export_id = safe_id(export_id, "export_id")
        export = self._exports.get(clean_export_id)
        if not export or export.get("project_id") != project_id:
            raise ValueError("unknown library project export")
        clean_bundle_sha256 = _strict_sha256(bundle_sha256, "bundle_sha256")
        clean_tree_sha256 = _strict_sha256(tree_sha256, "tree_sha256")
        if (
            export.get("namespace_key") != self.store.namespace_key(namespace)
            or not hmac.compare_digest(
                str(export.get("bundle_sha256") or ""), clean_bundle_sha256
            )
            or not hmac.compare_digest(
                str(export.get("tree_sha256") or ""), clean_tree_sha256
            )
        ):
            raise ValueError("library_project_export_binding_mismatch")
        library_file_id = _strict_library_id(
            bundle_library_file_id, "bundle_library_file_id", "libfile_"
        )
        file_id = _strict_library_id(bundle_file_id, "bundle_file_id", "file_")
        clean_library_path = _strict_library_path(library_path)
        lock_name = (
            f"library-project-registry-{self.store.namespace_key(namespace)}-"
            f"{project_id}"
        )
        with self.store.lock(lock_name):
            path = self._registry_path(namespace, project_id)
            previous = self._read_registry(namespace, project_id) if path.is_file() else None
            current_generation = int(previous.get("generation") or 0) if previous else 0
            exact_replay = bool(
                previous
                and previous.get("source_export_id") == clean_export_id
                and int(previous.get("previous_generation") or 0)
                == expected_generation
                and previous.get("bundle_library_file_id") == library_file_id
                and previous.get("bundle_file_id") == file_id
                and previous.get("library_path") == clean_library_path
                and previous.get("bundle_sha256") == clean_bundle_sha256
                and previous.get("tree_sha256") == clean_tree_sha256
            )
            if exact_replay:
                return previous
            if current_generation != expected_generation:
                raise ValueError("library_project_registry_generation_conflict")
            record = self._write_registry(
                namespace,
                project_id,
                {
                    "generation": current_generation + 1,
                    "status": "active",
                    "bundle_library_file_id": library_file_id,
                    "bundle_file_id": file_id,
                    "library_path": clean_library_path,
                    "bundle_sha256": clean_bundle_sha256,
                    "archive_prefix": self._archive_prefix(namespace, project_id),
                    "tree_sha256": clean_tree_sha256,
                    "file_count": int(export["file_count"]),
                    "expanded_size_bytes": int(export["expanded_size_bytes"]),
                    "registered_at": utc_stamp(),
                    "previous_generation": current_generation,
                    "source_export_id": clean_export_id,
                },
            )
        return record

    def unmount(self, namespace: str, project_id: str) -> dict[str, Any]:
        if self.mode != "library":
            raise ValueError("library_project_mode_inactive")
        registry = self._read_registry(namespace, project_id)
        project_dir = self._mounted_project_dir(namespace, project_id)
        if not project_dir.is_dir():
            return {"project_id": project_id, "status": "already_unmounted"}
        tree_sha256, _count, _size = self.tree_manifest(project_dir)
        if not hmac.compare_digest(
            tree_sha256, str(registry.get("tree_sha256") or "")
        ):
            raise ValueError(
                "library_project_unsynced_changes: export and commit a new Library "
                "snapshot before unmount"
            )
        with self.store.lock(
            f"library-project-mount-{self.store.namespace_key(namespace)}-{project_id}"
        ):
            shutil.rmtree(project_dir)
        return {"project_id": project_id, "status": "unmounted"}

    def status(self, namespace: str, project_id: str | None = None) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        requested_project_registered: bool | None = None
        if project_id is not None:
            try:
                records = [self._read_registry(namespace, project_id)]
                requested_project_registered = True
            except ValueError:
                requested_project_registered = False
        else:
            for path in sorted(self._namespace_registry_dir(namespace).glob("SBP-*.json")):
                with contextlib.suppress(ValueError, OSError, TypeError):
                    records.append(self._read_registry(namespace, path.stem))
        summaries = []
        for record in records:
            candidate = self._mounted_project_dir(namespace, str(record["project_id"]))
            summaries.append(
                {
                    "project_id": record["project_id"],
                    "generation": record["generation"],
                    "status": record["status"],
                    "library_file_id": record["bundle_library_file_id"],
                    "library_path": record["library_path"],
                    "bundle_sha256": record["bundle_sha256"],
                    "tree_sha256": record["tree_sha256"],
                    "file_count": record["file_count"],
                    "expanded_size_bytes": record["expanded_size_bytes"],
                    "mounted": candidate.is_dir(),
                    "mounted_size_bytes": self._directory_size(candidate),
                }
            )
        return {
            "mode": self.mode,
            "persistent_content_bytes": self._directory_size(
                self.persistent_projects_dir
            ),
            "ephemeral_runtime_bytes": self._directory_size(
                self.runtime_projects_dir
            ),
            "pointer_registry_bytes": self._directory_size(self.registry_dir),
            "registered_project_count": len(summaries),
            "requested_project_registered": requested_project_registered,
            "projects": summaries,
            "server_retention_contract": {
                "persistent": [
                    "rules_and_runtime_image",
                    "integrity_and_download_secrets",
                    "signed_library_ids_hashes_status_and_generation_pointers",
                ],
                "ephemeral_only": [
                    "mounted_project_state",
                    "source_files",
                    "storyboards",
                    "canonical_json",
                    "audit_evidence",
                    "handoff_and_recovery_full_records",
                ],
            },
        }

    def handle_action(
        self,
        namespace: str,
        *,
        storage_action: str,
        project_id: str,
        snapshot: OpenAIFile | None = None,
        bundle_library_file_id: str | None = None,
        bundle_file_id: str | None = None,
        library_path: str | None = None,
        bundle_sha256: str | None = None,
        expected_tree_sha256: str | None = None,
        tree_sha256: str | None = None,
        expected_generation: int = 0,
        export_id: str | None = None,
    ) -> ToolResponse:
        if storage_action == "status":
            value = self.status(namespace, project_id)
            return ToolResponse(
                status="ok",
                summary=(
                    f"内容存储模式｜{value['mode']}｜"
                    f"已登记{value['registered_project_count']}个项目"
                ),
                project_id=project_id,
                data=value,
            )
        if storage_action == "register_snapshot":
            if snapshot is None:
                raise ValueError(
                    "snapshot is required to verify the Library readback"
                )
            verified = self._verified_snapshot(
                namespace,
                project_id=project_id,
                snapshot=snapshot,
                expected_bundle_sha256=str(bundle_sha256 or ""),
                expected_tree_sha256=str(expected_tree_sha256 or ""),
                install=False,
            )
            record = self.register_existing_snapshot(
                namespace,
                project_id=project_id,
                bundle_library_file_id=str(bundle_library_file_id or ""),
                bundle_file_id=str(bundle_file_id or ""),
                library_path=str(library_path or ""),
                bundle_sha256=str(bundle_sha256 or ""),
                expected_tree_sha256=str(expected_tree_sha256 or ""),
                expected_generation=expected_generation,
            )
            return ToolResponse(
                status="registered",
                summary=(
                    f"Library项目快照已登记｜{project_id}｜"
                    f"代次{record['generation']}"
                ),
                project_id=project_id,
                data={
                    "pointer": record,
                    "library_readback_verified": verified,
                    "content_deleted": False,
                },
            )
        if storage_action == "mount_snapshot":
            if snapshot is None:
                raise ValueError("snapshot is required for mount_snapshot")
            result = self.mount_snapshot(
                namespace, project_id=project_id, snapshot=snapshot
            )
            return ToolResponse(
                status="mounted",
                summary=f"Library项目已临时挂载｜{project_id}",
                project_id=project_id,
                data=result,
            )
        if storage_action == "export_snapshot":
            result = self.export_snapshot(namespace, project_id)
            return ToolResponse(
                status="exported_pending_library_commit",
                summary=f"项目快照已导出｜{project_id}｜待保存到Library",
                project_id=project_id,
                download_url=str(result["download_url"]),
                data={key: value for key, value in result.items() if key != "path"},
            )
        if storage_action == "commit_snapshot":
            if snapshot is None:
                raise ValueError(
                    "snapshot is required to verify the Library readback"
                )
            verified = self._verified_snapshot(
                namespace,
                project_id=project_id,
                snapshot=snapshot,
                expected_bundle_sha256=str(bundle_sha256 or ""),
                expected_tree_sha256=str(tree_sha256 or ""),
                install=False,
            )
            record = self.commit_export_pointer(
                namespace,
                project_id=project_id,
                export_id=str(export_id or ""),
                expected_generation=expected_generation,
                bundle_library_file_id=str(bundle_library_file_id or ""),
                bundle_file_id=str(bundle_file_id or ""),
                library_path=str(library_path or ""),
                bundle_sha256=str(bundle_sha256 or ""),
                tree_sha256=str(tree_sha256 or ""),
            )
            return ToolResponse(
                status="committed",
                summary=(
                    f"Library项目快照已提交｜{project_id}｜"
                    f"代次{record['generation']}"
                ),
                project_id=project_id,
                data={
                    "pointer": record,
                    "library_readback_verified": verified,
                },
            )
        if storage_action == "unmount_snapshot":
            result = self.unmount(namespace, project_id)
            return ToolResponse(
                status=str(result["status"]),
                summary=f"Library项目临时副本｜{result['status']}｜{project_id}",
                project_id=project_id,
                data=result,
            )
        raise ValueError("unsupported storage_action")

    def register_routes(self, server: Any) -> None:
        manager = self

        @server.custom_route(
            "/library-project-export/{token}/{filename}",
            methods=["GET"],
            include_in_schema=False,
        )
        async def download_library_project_export(
            request: Request,
        ) -> FileResponse | PlainTextResponse:
            try:
                export = manager.resolve_export(request.path_params["token"])
                path = Path(str(export["path"]))
                return FileResponse(
                    path,
                    media_type="application/gzip",
                    filename=str(export["file_name"]),
                    headers={
                        "Cache-Control": "private, no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            except ValueError as exc:
                return PlainTextResponse(str(exc), status_code=404)
