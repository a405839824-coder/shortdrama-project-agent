"""Read other active administrators' projects without changing write identity.

Only the four explicitly wired read handlers use this resolver. No namespace
from a tool argument is trusted, and no project data is copied or merged.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import Any

from .members import load_registry
from .storage import safe_id


READ_CONTRACT = "admin-mutual-project-read-v1"


@dataclass(frozen=True)
class ProjectOwner:
    owner_id: str
    name: str
    namespace: str
    member_id: str | None = None


class ProjectReads:
    def __init__(self, access, service, external_storage):
        self.actor = access.principal()
        if not self.actor.enabled:
            raise PermissionError("account_disabled")
        self.store = service.store
        self.external = external_storage
        self.settings = access.settings
        member = self.actor.member
        self.own = ProjectOwner(self.actor.subject, member.name if member else self.actor.subject,
                                self.actor.namespace, member.member_id if member else None)
        self.owners = [self.own]
        if self.actor.role != "admin" or self.actor.issuer == "local":
            return
        if self.settings.member_connection_access:
            for row in load_registry(self.settings)["members"]:
                if row["role"] != "admin" or not row["enabled"]:
                    continue
                subject = row.get("legacy_subject") or f"member:{row['member_id']}"
                owner = ProjectOwner(f"member:{row['member_id']}", row["name"],
                                     f"{self.actor.issuer}|{subject}", row["member_id"])
                if owner.namespace != self.own.namespace:
                    self.owners.append(owner)
        else:
            with closing(access.connect()) as db:
                subjects = {row["subject"] for row in db.execute(
                    "SELECT subject FROM user_roles WHERE issuer=? AND role='admin' AND enabled=1",
                    (self.actor.issuer,),
                )}
            subjects.update(self.settings.admin_subjects)
            for subject in sorted(subjects - {self.actor.subject}):
                self.owners.append(ProjectOwner(subject, subject, f"{self.actor.issuer}|{subject}"))

    def info(self, owner: ProjectOwner) -> dict[str, Any]:
        shared = owner.namespace != self.own.namespace
        return {"contract": READ_CONTRACT, "owner_id": owner.owner_id,
                "owner_member_id": owner.member_id, "owner_name": owner.name,
                "shared": shared, "read_only": shared, "can_write": not shared}

    def _registered(self, owner: ProjectOwner, project_id: str) -> bool:
        if self.external.mode != "library":
            return False
        path = self.external.registry_dir / self.store.namespace_key(owner.namespace) / f"{project_id}.json"
        if not path.is_file():
            return False
        # An on-disk filename alone cannot authorize a project: verify its signed
        # pointer and namespace binding before using it for resolution.
        self.external._read_registry(owner.namespace, project_id)
        return True

    def resolve(self, project_id: str | None, owner_id: str | None = None) -> ProjectOwner:
        if project_id is None:
            if owner_id is not None:
                raise ValueError("owner_id_requires_project_id")
            return self.own
        safe_id(project_id, "project_id")
        owners = self.owners
        if owner_id is not None:
            owners = [owner for owner in owners if owner.owner_id == owner_id]
            if not owners:
                raise PermissionError("project_owner_not_readable")
        matches = [owner for owner in owners
                   if self.store.project_exists(owner.namespace, project_id)
                   or self._registered(owner, project_id)]
        if len(matches) > 1:
            raise ValueError("ambiguous_project_id: pass owner_id from the project list")
        if matches:
            return matches[0]
        # Keep the existing missing-project / missing-Library-pointer response;
        # never search unregistered directories or producer namespaces.
        return owners[0] if owner_id is not None else self.own

    def list_projects(self) -> list[dict[str, Any]]:
        rows = []
        for owner in self.owners:
            mounted = self.store.list_projects(owner.namespace)
            seen = {row["project_id"] for row in mounted}
            for row in mounted:
                rows.append({**row, "project_access": self.info(owner), "mounted": True})
            if self.external.mode == "library":
                registry_dir = self.external.registry_dir / self.store.namespace_key(owner.namespace)
                for path in sorted(registry_dir.glob("SBP-*.json")):
                    if path.stem in seen:
                        continue
                    try:
                        pointer = self.external._read_registry(owner.namespace, path.stem)
                    except (OSError, ValueError, TypeError):
                        continue
                    rows.append({"project_id": path.stem, "generation": pointer["generation"],
                                 "library_file_id": pointer["bundle_library_file_id"],
                                 "library_path": pointer["library_path"],
                                 "bundle_sha256": pointer["bundle_sha256"],
                                 "project_access": self.info(owner),
                                 "mounted": False, "mount_required": True})
        return sorted(rows, key=lambda row: (str(row.get("updated_at") or ""),
                                             row["project_access"]["owner_id"], row["project_id"]), reverse=True)

    def annotate(self, response, owner: ProjectOwner):
        response.data["project_access"] = self.info(owner)
        # Keep paged reads bound to the same owner even when project IDs collide.
        for container in (response.data, response.data.get("page", {})):
            next_call = container.get("next_call") if isinstance(container, dict) else None
            if isinstance(next_call, dict) and next_call.get("project_id") == response.project_id:
                next_call["owner_id"] = owner.owner_id
        if owner.namespace != self.own.namespace:
            # Workflow recommendations describe the owner's pending work, not
            # authority to execute it from the reading administrator's account.
            workflow_keys = ("execution_available", "execution_mode", "required_next_action",
                             "action_required", "allowed_storyboard_operations", "recovery_operations")
            response.data["owner_workflow"] = {key: response.data[key] for key in workflow_keys
                                               if key in response.data}
            response.data.update(execution_available=False, execution_mode="shared_read_only",
                                 window_access_capabilities=["read"], continuation_supported=False,
                                 recovery_available=False, allowed_storyboard_operations=[],
                                 recovery_operations=[], action_required=False,
                                 required_next_action=None)
            if response.status == "mount_required":
                response.data["required_action"] = "project_owner_must_mount_verified_snapshot"
            response.summary += "｜跨管理员只读"
            for row in response.data.get("resumable_authorities", []):
                row.update(resumable=False, reason="shared_project_read_only")
        return response
