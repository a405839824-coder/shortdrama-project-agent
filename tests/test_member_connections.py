from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
import time
import unittest
from contextlib import closing
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

import test_account_access as legacy
from shortdrama_mcp.access import PRODUCER_TOOLS
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import JwtTokenVerifier, request_namespace
from shortdrama_mcp.members import current_member, validate_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from configure_member_connections import validate_client, validate_update, write_atomic


def registry_for(settings):
    return {"schema_version": 1, "issuer": settings.oauth_issuer, "audience": settings.oauth_audience,
            "members": [
                {"member_id": "owner", "name": "Owner", "client_id": "owner_private_client", "role": "admin",
                 "enabled": True, "allowed_subjects": ["auth0|owner"], "tokens_valid_after": 0,
                 "legacy_subject": "auth0|owner"},
                *[{"member_id": f"staff{i}", "name": f"Staff {i}", "client_id": f"staff_client_{i}",
                   "role": "producer", "enabled": True, "allowed_subjects": ["auth0|owner"],
                   "tokens_valid_after": 0} for i in range(1, 10)],
            ]}


def token_for(settings, client="staff_client_1", subject="auth0|owner", **claims):
    return AccessToken(token="test-only", client_id=client, subject=subject, scopes=list(settings.oauth_scopes),
                       claims={"iss": settings.oauth_issuer, "azp": client, "iat": int(time.time()), **claims})


class MemberTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="member-access-")
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(legacy.settings_for(self.temp.name), member_connection_access=True)
        self.registry = registry_for(self.settings)
        self.save()
        self.server, self.service = create_mcp(self.settings)

    def save(self):
        write_atomic(self.settings.data_dir / "member_connections.json", self.registry)

    async def call(self, client, name, arguments, subject="auth0|owner", **claims):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, client, subject, **claims)):
            return await self.server.call_tool(name, arguments)

    async def test_shared_google_cannot_inherit_admin_and_tools_fail_closed(self):
        token = token_for(self.settings, role="admin", member_id="owner", user_metadata={"role": "admin"})
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token):
            principal = self.server.access.principal()
            self.assertEqual(principal.role, "producer")
            self.assertFalse(principal.bootstrap)
            self.assertEqual({t.name for t in await self.server.list_tools()}, PRODUCER_TOOLS)
            denied = await self.server.call_tool("manage_user_access", {"action": "set", "subject": "auth0|owner", "role": "admin"})
            self.assertTrue(denied.is_error)
        for client in ("unknown", "https://chatgpt.com/oauth/client.json"):
            self.assertTrue((await self.call(client, "create_project", {"project_name": "denied"})).is_error)
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, "unknown")):
            self.assertEqual(await self.server.list_tools(), [])
        owner = await self.call("owner_private_client", "get_agent_status", {})
        access = owner.structured_content["data"]["account_access"]
        self.assertEqual(access["role"], "admin")
        self.assertEqual(access["member_id"], "owner")
        self.assertFalse(access["subject_admin_grants_active"])

    async def test_projects_are_isolated_for_ten_connections_and_legacy_owner_is_preserved(self):
        old_namespace = f"{self.settings.oauth_issuer}|auth0|owner"
        old = self.service.create_project(old_namespace, "existing owner project")
        owner_view = await self.call("owner_private_client", "get_agent_status", {"project_id": old.project_id})
        self.assertFalse(owner_view.is_error)
        projects = []
        for i in range(1, 10):
            created = await self.call(f"staff_client_{i}", "create_project", {"project_name": f"team {i}"})
            self.assertFalse(created.is_error)
            projects.append(created.structured_content["project_id"])
        for i, project_id in enumerate(projects, 1):
            own = f"{self.settings.oauth_issuer}|member:staff{i}"
            self.assertTrue(self.service.store.project_exists(own, project_id))
            self.assertFalse(self.service.store.project_exists(own, old.project_id))
            other = (i % 9) + 1
            self.assertFalse(self.service.store.project_exists(f"{self.settings.oauth_issuer}|member:staff{other}", project_id))
            with self.assertRaises(ToolError):
                await self.call(f"staff_client_{other}", "read_project_artifact", {"project_id": project_id, "artifact_id": "hub"})
        self.assertTrue(self.service.store.project_exists(old_namespace, old.project_id))
        with closing(self.server.access.connect()) as db:
            actors = {r[0] for r in db.execute("SELECT DISTINCT subject FROM access_audit")}
        self.assertEqual(actors, {"member:owner", *[f"member:staff{i}" for i in range(1, 10)]})

    async def test_new_google_requires_explicit_binding_but_keeps_member_namespace(self):
        self.assertTrue((await self.call("staff_client_1", "get_agent_status", {}, "google-oauth2|new" )).is_error)
        self.registry["members"][1]["allowed_subjects"].append("google-oauth2|new")
        self.save()
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, subject="google-oauth2|new")):
            self.assertEqual(request_namespace(self.settings), f"{self.settings.oauth_issuer}|member:staff1")
        self.assertFalse((await self.call("staff_client_1", "get_agent_status", {}, "google-oauth2|new")).is_error)

    async def test_disable_revocation_and_broken_registry_do_not_fall_back_to_google_admin(self):
        self.registry["members"][1]["enabled"] = False
        self.save()
        result = await self.call("staff_client_1", "create_project", {"project_name": "disabled"})
        self.assertIn("account_disabled", result.content[0].text)
        self.registry["members"][1]["enabled"] = True
        self.registry["members"][1]["tokens_valid_after"] = 100
        self.save()
        self.assertTrue((await self.call("staff_client_1", "get_agent_status", {}, iat=99)).is_error)
        self.assertFalse((await self.call("staff_client_1", "get_agent_status", {}, iat=100)).is_error)
        (self.settings.data_dir / "member_connections.json").write_text("{broken", encoding="utf-8")
        self.assertTrue((await self.call("owner_private_client", "get_agent_status", {})).is_error)
        with self.assertRaises(PermissionError):
            create_mcp(self.settings)

    async def test_identity_is_pinned_during_dispatch_and_context_is_reset(self):
        seen = []
        @self.server.tool(name="member_pin_test")
        async def member_pin_test():
            seen.append(request_namespace(self.settings))
            # Simulate a configuration replacement while an authorized call is in progress.
            self.registry["members"][0]["enabled"] = False
            self.registry["members"][1]["role"] = "admin"
            self.save()
            await asyncio.sleep(0)
            seen.append(request_namespace(self.settings))
            return "ok"
        result = await self.call("owner_private_client", "member_pin_test", {})
        self.assertFalse(result.is_error)
        self.assertEqual(seen, [f"{self.settings.oauth_issuer}|auth0|owner"] * 2)
        self.assertIsNone(current_member.get())
        self.assertTrue((await self.call("owner_private_client", "get_agent_status", {})).is_error)

    async def test_subject_role_management_is_disabled_in_member_mode(self):
        with self.assertRaisesRegex(ToolError, "member_changes_require_host_configuration"):
            await self.call("owner_private_client", "manage_user_access", {"action": "set", "subject": "new", "role": "admin"})
        listed = await self.call("owner_private_client", "manage_user_access", {"action": "list"})
        self.assertIn("members", listed.structured_content["data"])

    async def test_concurrent_dispatches_keep_separate_identity_contexts(self):
        # A test-only tool lets both handlers yield while their identities are pinned.
        self.registry["members"][1]["role"] = "admin"
        self.save()
        token_context = ContextVar("test_tokens")
        arrived, barrier, seen = [], asyncio.Event(), {}
        @self.server.tool(name="concurrent_identity_probe")
        async def probe(label: str):
            before = request_namespace(self.settings)
            arrived.append(label)
            if len(arrived) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=5)
            seen[label] = (before, request_namespace(self.settings))
            return "ok"
        async def run(client, label):
            token_context.set(token_for(self.settings, client))
            return await self.server.call_tool("concurrent_identity_probe", {"label": label})
        with patch("shortdrama_mcp.auth.get_access_token", side_effect=lambda: token_context.get()):
            results = await asyncio.gather(run("owner_private_client", "owner"), run("staff_client_1", "staff"))
        self.assertTrue(all(not result.is_error for result in results))
        self.assertEqual(seen["owner"], (f"{self.settings.oauth_issuer}|auth0|owner",) * 2)
        self.assertEqual(seen["staff"], (f"{self.settings.oauth_issuer}|member:staff1",) * 2)
        self.assertIsNone(current_member.get())

    def test_invalid_and_ambiguous_registries_are_rejected(self):
        mutations = [
            lambda v: v.update(issuer="https://wrong.test/"),
            lambda v: v["members"][1].update(client_id=v["members"][0]["client_id"]),
            lambda v: v["members"][1].update(member_id="owner"),
            lambda v: v["members"][1].update(legacy_subject="auth0|owner"),
            lambda v: v["members"][1].update(enabled="false"),
            lambda v: v["members"][1].update(role=["admin"]),
            lambda v: v["members"][1].update(allowed_subjects=["x@clients"]),
            lambda v: v["members"][1].update(tokens_valid_after=True),
            lambda v: v["members"][1].update(client_secret="must not store secrets"),
            lambda v: v["members"][0].update(enabled=False),
        ]
        for mutate in mutations:
            value = copy.deepcopy(self.registry)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_registry(value, self.settings)
        with self.assertRaises(ValueError):
            replace(self.settings, auth_mode="none").validate()
        with self.assertRaises(ValueError):
            create_mcp(replace(self.settings, member_connection_access=False))
        oversized = copy.deepcopy(self.registry)
        for member in oversized["members"]:
            member["allowed_subjects"] = [str(i) + "x" * 500 for i in range(20)]
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            validate_registry(oversized, self.settings)


class MemberHTTPTests(unittest.TestCase):
    def test_signed_http_tokens_refresh_claims_revocation_and_tampering(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory(prefix="member-http-") as directory:
            settings = replace(legacy.settings_for(directory), member_connection_access=True)
            registry = registry_for(settings)
            path = settings.data_dir / "member_connections.json"
            write_atomic(path, registry)
            verifier = JwtTokenVerifier(settings)
            verifier.jwks_client.get_signing_key_from_jwt = lambda _: SimpleNamespace(key=key.public_key())
            with patch("shortdrama_mcp.app.JwtTokenVerifier", return_value=verifier):
                server, _ = create_mcp(settings)
            app = server.streamable_http_app(json_response=True, stateless_http=True, host="storyboard.example.test")
            with TestClient(app, base_url=settings.public_base_url) as client:
                def call(azp="staff_client_1", signing_key=key, **overrides):
                    now = int(time.time())
                    claims = {"sub": "auth0|owner", "azp": azp, "iss": settings.oauth_issuer,
                              "aud": settings.oauth_audience, "iat": now - 2, "exp": now + 120,
                              "scope": "storyboard:read storyboard:write", **overrides}
                    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "get_agent_status", "arguments": {}}}, headers={
                            "Authorization": "Bearer " + jwt.encode(claims, signing_key, algorithm="RS256"),
                            "Accept": "application/json, text/event-stream"})
                initial = call()
                self.assertEqual(initial.status_code, 200, initial.text)
                profile = initial.json()["result"]["structuredContent"]["data"]["account_access"]
                self.assertEqual((profile["role"], profile["member_id"]), ("producer", "staff1"))
                # Simulate a refreshed, signed access token. Auth0 refresh exchange itself is a live acceptance step.
                fresh = call(iat=int(time.time()), jti="another-grant-token")
                self.assertEqual(fresh.json()["result"]["structuredContent"]["data"]["account_access"]["member_id"], "staff1")
                for override in ({"azp": None}, {"azp": "unknown"}, {"client_id": "owner_private_client"},
                                 {"sub": "google-oauth2|unapproved"}, {"iss": "https://wrong.test/"},
                                 {"aud": "wrong"}, {"exp": 1}, {"scope": "storyboard:read"}):
                    self.assertEqual(call(**override).status_code, 401)
                other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                self.assertEqual(call(signing_key=other_key).status_code, 401)
                registry["members"][1]["enabled"] = False
                write_atomic(path, registry)
                disabled = call()
                self.assertEqual(disabled.status_code, 401)
                self.assertIn("resource_metadata", disabled.headers["www-authenticate"])
                self.assertEqual(call("owner_private_client").status_code, 200)


class ProvisioningTests(unittest.TestCase):
    def test_provisioner_requires_confidential_code_only_clients_and_exact_callbacks(self):
        valid = {"client_id": "one", "app_type": "regular_web", "is_first_party": False,
                 "token_endpoint_auth_method": "client_secret_post", "grant_types": ["authorization_code", "refresh_token"],
                 "callbacks": ["https://chatgpt.com/connector/oauth/actual_callback_id"]}
        validate_client(valid, "one")
        for change in ({"token_endpoint_auth_method": "none"}, {"grant_types": ["implicit", "authorization_code", "refresh_token"]},
                       {"grant_types": ["authorization_code", "refresh_token", "client_credentials"]}, {"is_first_party": True},
                       {"callbacks": ["https://chatgpt.com/connector/oauth/*"]}, {"callbacks": ["https://evil.test/callback"]}):
            with self.assertRaises(ValueError):
                validate_client({**valid, **change}, "one")

    def test_updates_cannot_reassign_clients_or_erase_namespace_history(self):
        registry = registry_for(legacy.settings_for("unused"))
        for change in (lambda v: v["members"].pop(),
                       lambda v: v["members"][1].update(client_id="owner_private_client"),
                       lambda v: v["members"][0].pop("legacy_subject")):
            modified = copy.deepcopy(registry)
            change(modified)
            with self.assertRaises(ValueError):
                validate_update(registry, modified)
        modified = copy.deepcopy(registry)
        modified["members"][1]["enabled"] = False
        validate_update(registry, modified)


if __name__ == "__main__":
    unittest.main()
