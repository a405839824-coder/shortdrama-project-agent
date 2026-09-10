from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from shortdrama_mcp.access import (
    ACCESS_CONTRACT, AccessStore, Principal, PRODUCER_TOOLS, authorize,
)
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import JwtTokenVerifier
from shortdrama_mcp.config import Settings


def settings_for(path: str) -> Settings:
    return Settings(
        data_dir=Path(path), public_base_url="https://storyboard.example.test",
        host="127.0.0.1", port=8765, deployment_mode="cloud", auth_mode="oauth",
        oauth_issuer="https://issuer.example.test/", oauth_audience="https://storyboard.example.test",
        oauth_jwks_url="https://issuer.example.test/.well-known/jwks.json",
        oauth_scopes=("storyboard:read", "storyboard:write"),
        allow_local_file_urls=False, max_file_bytes=1024 * 1024,
        admin_subjects=("auth0|owner",),
    )


def token_for(settings: Settings, subject="auth0|employee", **claims):
    return AccessToken(token="unit-test-token", client_id="test", scopes=list(settings.oauth_scopes),
                       subject=subject, claims={"iss": settings.oauth_issuer, **claims})


class AccountAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="account-access-")
        self.addCleanup(self.temp.cleanup)
        self.settings = settings_for(self.temp.name)
        self.server, self.service = create_mcp(self.settings)
        self.employee = Principal(self.settings.oauth_issuer, "auth0|employee", "producer")
        self.owner = Principal(self.settings.oauth_issuer, "auth0|owner", "admin", bootstrap=True)

    async def call_as(self, subject, name, args):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, subject)):
            return await self.server.call_tool(name, args)

    async def test_missing_authentication_cannot_create_project(self):
        result = await self.server.call_tool("create_project", {"project_name": "denied"})
        self.assertTrue(result.is_error)
        self.assertIn("authentication_required", result.content[0].text)

    async def test_unassigned_user_is_producer_even_with_forged_admin_claims(self):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(
            self.settings, role="admin", roles=["admin"], user_metadata={"role": "admin"}
        )):
            self.assertEqual(self.server.access.principal().role, "producer")
            result = await self.server.call_tool("manage_user_access", {"action": "list", "role": "admin"})
        self.assertTrue(result.is_error)

    async def test_create_status_and_read_tools_work_for_producer(self):
        created = await self.call_as("auth0|employee", "create_project", {"project_name": "production"})
        self.assertFalse(created.is_error)
        project_id = created.structured_content["project_id"]
        status = await self.call_as("auth0|employee", "get_agent_status", {"project_id": project_id})
        self.assertFalse(status.is_error)
        self.assertEqual(status.structured_content["data"]["account_access"]["role"], "producer")
        self.assertTrue(status.structured_content["data"]["account_access"]["account_role_restrictions"])
        self.assertEqual(status.structured_content["data"]["account_access"]["contract"], ACCESS_CONTRACT)

    async def test_tool_visibility_and_direct_admin_tool_denial(self):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings)):
            self.assertEqual({t.name for t in await self.server.list_tools()}, PRODUCER_TOOLS)
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, "auth0|owner")):
            self.assertEqual(len(await self.server.list_tools()), 24)
        for name in ("recover_storyboard_handoff", "signoff_task", "manage_user_access"):
            result = await self.call_as("auth0|employee", name, {})
            self.assertTrue(result.is_error, name)
            self.assertIn("admin_required", result.content[0].text)

    async def test_new_tool_defaults_to_denied_before_handler_runs(self):
        calls = []
        @self.server.tool(name="future_runtime_update")
        async def future_tool() -> str:
            calls.append(True)
            return "executed"
        denied = await self.call_as("auth0|employee", "future_runtime_update", {})
        self.assertTrue(denied.is_error)
        self.assertEqual(calls, [])
        allowed = await self.call_as("auth0|owner", "future_runtime_update", {})
        self.assertFalse(allowed.is_error)
        self.assertEqual(calls, [True])

    async def test_rule_template_runtime_and_config_routes_are_denied(self):
        cases = [("begin_project_task", {"operation": operation}) for operation in (
            "rule_improvement", "version_management", "runtime_update", "plugin_upgrade",
            "project_state_migration", "archive", "unknown_operation",
        )]
        cases += [("import_source_file", {"source_role": "rule"})]
        cases += [("commit_project_artifact", {"artifact_type": kind}) for kind in (
            "rule", "template", "validator", "error_code", "runtime", "config", "change_notes", "unknown",
        )]
        cases += [("migrate_project_state", {"action": action}) for action in ("plan", "execute", "rollback")]
        for name, args in cases:
            with self.subTest(name=name, args=args):
                result = await self.call_as("auth0|employee", name, args)
                self.assertTrue(result.is_error)
                self.assertIn("Permission denied", result.content[0].text)

    async def test_production_audit_repair_export_parameter_allowlist(self):
        for operation in ("project_intake", "storyboard_create", "storyboard_change", "storyboard_audit", "storyboard_repair"):
            authorize(self.employee, "begin_project_task", {"operation": operation}, self.service)
        for name in ("preflight_storyboard_draft", "commit_storyboard", "commit_storyboard_delta", "commit_storyboard_patch", "audit_storyboard"):
            authorize(self.employee, name, {}, self.service)
        for action in ("register_snapshot", "mount_snapshot", "export_snapshot", "commit_snapshot", "unmount_snapshot"):
            authorize(self.employee, "migrate_project_state", {"storage_action": action}, self.service)
        for source in ("novel", "script", "storyboard", "reference", "asset"):
            authorize(self.employee, "import_source_file", {"source_role": source}, self.service)

    async def test_existing_admin_work_order_and_continuation_cannot_bypass_gate(self):
        created = self.service.create_project(self.employee.namespace, "old-admin-order")
        project_id = created.project_id
        with self.service.store.mutate_project(self.employee.namespace, project_id) as (hub, _):
            hub.setdefault("work_orders", []).append({"work_order_id": "WO-admin", "operation": "rule_improvement"})
        before = self.service.store.load_hub(self.employee.namespace, project_id)
        for name, args in (
            ("complete_task_validation", {"work_order_id": "WO-admin"}),
            ("commit_project_artifact", {"work_order_id": "WO-admin", "artifact_type": "script"}),
            ("begin_project_task", {"operation": "storyboard_create", "resume_work_order_id": "WO-admin",
                                    "action": "execute_role_continuation"}),
        ):
            result = await self.call_as("auth0|employee", name, {"project_id": project_id, **args})
            self.assertTrue(result.is_error)
            self.assertIn("maintenance_work_order", result.content[0].text)
        self.assertEqual(before, self.service.store.load_hub(self.employee.namespace, project_id))

    async def test_admin_can_manage_roles_and_existing_token_observes_revocation(self):
        granted = await self.call_as("auth0|owner", "manage_user_access", {
            "action": "set", "subject": "auth0|employee", "role": "admin",
        })
        self.assertFalse(granted.is_error)
        self.assertFalse((await self.call_as("auth0|employee", "manage_user_access", {"action": "list"})).is_error)
        await self.call_as("auth0|owner", "manage_user_access", {
            "action": "set", "subject": "auth0|employee", "role": "producer",
        })
        self.assertTrue((await self.call_as("auth0|employee", "manage_user_access", {"action": "list"})).is_error)
        await self.call_as("auth0|owner", "manage_user_access", {
            "action": "set", "subject": "auth0|employee", "role": "producer", "enabled": False,
        })
        denied = await self.call_as("auth0|employee", "create_project", {"project_name": "disabled"})
        self.assertTrue(denied.is_error)
        self.assertIn("account_disabled", denied.content[0].text)

    async def test_roles_persist_and_bootstrap_admin_cannot_be_demoted(self):
        self.server.access.manage(self.owner, "set", "auth0|staff", "producer", False)
        reopened = AccessStore(self.settings)
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, "auth0|staff")):
            self.assertFalse(reopened.principal().enabled)
        with self.assertRaisesRegex(ValueError, "bootstrap"):
            reopened.manage(self.owner, "set", self.owner.subject, "producer", False)

    async def test_access_logs_capture_actor_role_result_without_payload(self):
        await self.call_as("auth0|employee", "create_project", {"project_name": "private-content-not-for-log"})
        await self.call_as("auth0|employee", "import_source_file", {"source_role": "rule", "file": {"content": "secret"}})
        events = self.server.access.manage(self.owner, "audit")["events"]
        self.assertEqual({e["outcome"] for e in events}, {"started", "success", "denied"})
        self.assertTrue(all(e["subject"] == "auth0|employee" and e["role"] == "producer" for e in events))
        self.assertNotIn("private-content", json.dumps(events))
        self.assertNotIn("secret", json.dumps(events))

    async def test_namespace_isolation_and_local_mode(self):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, iss="https://other.example/")):
            with self.assertRaisesRegex(PermissionError, "issuer_mismatch"):
                self.server.access.principal()
        local = AccessStore(replace(self.settings, auth_mode="none", deployment_mode="local"))
        self.assertEqual(local.principal().role, "admin")
        self.assertEqual(local.principal().namespace, "local-single-user")

    async def test_configuration_rejects_malformed_admin_subjects(self):
        for value in ('"admin"', '{}', '[null]', '[1]', '[""]'):
            with self.subTest(value=value), patch.dict(os.environ, {"SHORTDRAMA_ADMIN_SUBJECTS": value}):
                with self.assertRaises(ValueError):
                    Settings.from_env()


class AccountAccessHTTPTests(unittest.TestCase):
    def test_signed_oauth_http_requests_enforce_roles_and_reject_invalid_tokens(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory(prefix="rbac-http-") as directory:
            settings = settings_for(directory)
            verifier = JwtTokenVerifier(settings)
            verifier.jwks_client.get_signing_key_from_jwt = lambda _: SimpleNamespace(key=key.public_key())
            with patch("shortdrama_mcp.app.JwtTokenVerifier", return_value=verifier):
                server, _ = create_mcp(settings)
            app = server.streamable_http_app(json_response=True, stateless_http=True, host="storyboard.example.test")
            with TestClient(app, base_url=settings.public_base_url) as client:
                def call(subject, name, arguments, **overrides):
                    claims = {"sub": subject, "iss": settings.oauth_issuer, "aud": settings.oauth_audience,
                              "iat": int(time.time()), "exp": int(time.time()) + 120,
                              "scope": "storyboard:read storyboard:write", **overrides}
                    token = jwt.encode(claims, key, algorithm="RS256")
                    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}}, headers={
                            "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})
                created = call("auth0|employee", "create_project", {"project_name": "http-producer"})
                self.assertEqual(created.status_code, 200, created.text)
                self.assertFalse(created.json()["result"].get("isError", False), created.text)
                denied = call("auth0|employee", "import_source_file", {"source_role": "rule"})
                self.assertTrue(denied.json()["result"]["isError"], denied.text)
                admin = call("auth0|owner", "manage_user_access", {"action": "list"})
                self.assertFalse(admin.json()["result"].get("isError", False), admin.text)
                for overrides in ({"aud": "wrong"}, {"exp": int(time.time()) - 10}, {"scope": "storyboard:read"}):
                    rejected = call("auth0|owner", "manage_user_access", {"action": "list"}, **overrides)
                    self.assertEqual(rejected.status_code, 401)
                self.assertEqual(client.get("/mcp").status_code, 401)


if __name__ == "__main__":
    unittest.main()
