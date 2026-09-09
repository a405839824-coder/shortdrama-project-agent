"""Account permissions, separate from the independent production/audit actors."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from . import auth
from .config import Settings


ACCESS_CONTRACT = "admin-producer-v1"
# Explicit allowlists: new tools, operations and artifact types fail closed.
PRODUCER_TOOLS = frozenset({
    "get_agent_status", "create_project", "begin_project_task", "import_source_file",
    "read_project_artifact", "query_project_issues", "mark_performance_stage",
    "plan_change_request", "commit_project_artifact", "preflight_storyboard_draft",
    "commit_storyboard", "commit_storyboard_delta", "commit_storyboard_patch",
    "audit_storyboard", "complete_task_validation", "record_capability_gap",
    "migrate_project_state",
})
PRODUCER_OPERATIONS = frozenset({
    "project_intake", "source_lock", "chapter_intake", "novel_analysis",
    "story_diagnosis", "hook_design", "adaptation_plan", "episode_plan",
    "character_arc", "scene_plan", "rewrite_plan", "script_create", "script_rewrite",
    "script_change", "dialogue_rewrite", "change_impact", "storyboard_plan",
    "storyboard_create", "storyboard_regenerate", "storyboard_change",
    "storyboard_audit", "storyboard_repair", "storyboard_draft_preflight",
    "anchor_management", "keyframe_prompt", "keyframe_image", "audio_bgm",
    "voice_design", "format_convert", "production_batch", "statistics", "capability_gap",
})
PRODUCER_ARTIFACTS = frozenset({
    "chapter_intake", "novel_analysis", "story_diagnosis", "hook_plan",
    "adaptation_plan", "episode_plan", "character_arc", "scene_plan", "rewrite_plan",
    "storyboard_plan", "change_impact", "script", "dialogue", "anchors",
    "keyframe_prompt", "keyframe_image", "bgm_plan", "voice_plan",
    "production_batch", "statistics", "conversion",
})
PRODUCER_SOURCE_ROLES = frozenset({"novel", "script", "storyboard", "reference", "asset"})
LIBRARY_ACTIONS = frozenset({
    "status", "register_snapshot", "mount_snapshot", "export_snapshot",
    "commit_snapshot", "unmount_snapshot",
})


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    role: str
    enabled: bool = True
    bootstrap: bool = False

    @property
    def namespace(self) -> str:
        return "local-single-user" if self.issuer == "local" else f"{self.issuer}|{self.subject}"


class AccessStore:
    """Server-owned role database outside importable project/snapshot directories."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.data_dir) / "account_access.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS user_roles (
                    issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','producer')),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS access_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    role TEXT NOT NULL, tool TEXT NOT NULL, project_id TEXT,
                    outcome TEXT NOT NULL, reason TEXT NOT NULL
                );
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def principal(self) -> Principal:
        if self.settings.auth_mode == "none":
            if self.settings.deployment_mode != "local":
                raise PermissionError("authentication_required")
            return Principal("local", "local-single-user", "admin", bootstrap=True)
        token = auth.get_access_token()
        if token is None or not isinstance(token.subject, str) or not token.subject:
            raise PermissionError("authentication_required")
        issuer = str((token.claims or {}).get("iss") or self.settings.oauth_issuer)
        if issuer != self.settings.oauth_issuer:
            raise PermissionError("issuer_mismatch")
        # Only server configuration / database grants privileges. Ignore caller
        # parameters, window roles, token role labels and editable user metadata.
        if token.subject in self.settings.admin_subjects:
            return Principal(issuer, token.subject, "admin", bootstrap=True)
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT role, enabled FROM user_roles WHERE issuer=? AND subject=?",
                (issuer, token.subject),
            ).fetchone()
        return Principal(issuer, token.subject, row["role"] if row else "producer",
                         bool(row["enabled"]) if row else True)

    @staticmethod
    def _audit(db: sqlite3.Connection, principal: Principal, tool: str,
               project_id: str | None, outcome: str, reason: str) -> None:
        db.execute(
            "INSERT INTO access_audit (occurred_at,issuer,subject,role,tool,project_id,outcome,reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), principal.issuer, principal.subject,
             principal.role, tool[:128], str(project_id)[:128] if project_id else None,
             outcome, reason[:1024]),
        )

    def audit(self, principal: Principal, tool: str, project_id: str | None,
              outcome: str, reason: str = "") -> None:
        with closing(self.connect()) as db, db:
            self._audit(db, principal, tool, project_id, outcome, reason)

    def manage(self, actor: Principal, action: str, subject: str | None = None,
               role: str | None = None, enabled: bool = True) -> dict[str, Any]:
        if not actor.enabled or actor.role != "admin":
            raise PermissionError("admin_required")
        issuer = actor.issuer
        with closing(self.connect()) as db, db:
            if action == "list":
                rows = [dict(row) for row in db.execute(
                    "SELECT subject,role,enabled,updated_at FROM user_roles WHERE issuer=? ORDER BY subject",
                    (issuer,),
                )]
                return {"bootstrap_admin_subjects": list(self.settings.admin_subjects), "users": rows,
                        "unassigned_authenticated_user_role": "producer"}
            if action == "audit":
                return {"events": [dict(row) for row in db.execute(
                    "SELECT * FROM access_audit WHERE issuer=? ORDER BY id DESC LIMIT 100", (issuer,)
                )]}
            if action != "set" or not subject or len(subject) > 512 or role not in {"admin", "producer"}:
                raise ValueError("set requires an exact OAuth subject and admin/producer role")
            if subject == actor.subject or subject in self.settings.admin_subjects:
                raise ValueError("self_or_bootstrap_admin_change_requires_server_configuration")
            db.execute(
                "INSERT INTO user_roles VALUES (?,?,?,?,?) ON CONFLICT(issuer,subject) DO UPDATE SET "
                "role=excluded.role,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (issuer, subject, role, int(enabled), datetime.now(timezone.utc).isoformat()),
            )
            self._audit(db, actor, "manage_user_access", None, "role_changed",
                        f"subject={subject};role={role};enabled={enabled}")
        return {"subject": subject, "role": role, "enabled": enabled}


def authorize(principal: Principal, name: str, arguments: dict[str, Any], service: Any) -> None:
    if not principal.enabled:
        raise PermissionError("account_disabled")
    if principal.role == "admin":
        return
    if principal.role != "producer" or name not in PRODUCER_TOOLS:
        raise PermissionError("admin_required_for_tool")
    if name == "begin_project_task" and arguments.get("operation") not in PRODUCER_OPERATIONS:
        raise PermissionError("producer_cannot_run_maintenance_operation")
    if name == "import_source_file" and arguments.get("source_role") not in PRODUCER_SOURCE_ROLES:
        raise PermissionError("producer_cannot_import_rules")
    if name == "commit_project_artifact" and arguments.get("artifact_type") not in PRODUCER_ARTIFACTS:
        raise PermissionError("producer_cannot_write_system_artifact")
    if name == "migrate_project_state" and arguments.get("storage_action") not in LIBRARY_ACTIONS:
        raise PermissionError("producer_cannot_migrate_or_rollback_system_state")
    # Check existing work orders as well, including previously-created admin
    # orders and continuation attempts after the account was demoted.
    for key in ("work_order_id", "resume_work_order_id"):
        order_id = arguments.get(key)
        if order_id:
            project_id = arguments.get("project_id")
            if not isinstance(project_id, str) or not isinstance(order_id, str):
                raise PermissionError("invalid_work_order_reference")
            try:
                hub = service.store.load_hub(principal.namespace, project_id)
            except (ValueError, FileNotFoundError) as exc:
                raise PermissionError("invalid_work_order_reference") from exc
            orders = [row for row in hub.get("work_orders", [])
                      if isinstance(row, dict) and row.get("work_order_id") == order_id]
            if len(orders) != 1 or orders[0].get("operation") not in PRODUCER_OPERATIONS:
                raise PermissionError("producer_cannot_use_maintenance_work_order")


class AccessControlledMCPServer(MCPServer):
    """One dispatch gate covers both HTTP and stdio, including future tools."""

    access: AccessStore
    storyboard_service: Any

    async def list_tools(self):
        tools = await super().list_tools()
        # Schema introspection outside a request remains possible; the call gate
        # always requires authentication. HTTP list requests already require OAuth.
        try:
            principal = self.access.principal()
        except PermissionError:
            return tools
        if not principal.enabled:
            return []
        return tools if principal.role == "admin" else [tool for tool in tools if tool.name in PRODUCER_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        principal = None
        try:
            principal = self.access.principal()
            authorize(principal, name, arguments, self.storyboard_service)
        except PermissionError as exc:
            if principal is not None:
                self.access.audit(principal, name, arguments.get("project_id"), "denied", str(exc))
            return CallToolResult(is_error=True, content=[TextContent(
                type="text", text=f"Permission denied: {exc}")])
        self.access.audit(principal, name, arguments.get("project_id"), "started")
        try:
            result = await super().call_tool(name, arguments, context)
        except Exception:
            self.access.audit(principal, name, arguments.get("project_id"), "error")
            raise
        self.access.audit(principal, name, arguments.get("project_id"),
                          "error" if getattr(result, "is_error", False) else "success")
        return result
