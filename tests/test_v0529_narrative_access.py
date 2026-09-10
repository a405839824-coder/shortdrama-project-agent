"""Account authorization through MCP, with synthetic prose and distinct actors.

These tests exercise the release integration, not manuscript accuracy or a real
employee's OAuth acceptance. The HTTP case uses a locally signed test JWT.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from shortdrama_mcp.access import PRODUCER_TOOLS
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import JwtTokenVerifier
from test_account_access import settings_for, token_for
from test_narrative_contract import make_plan, make_candidate, make_review


NARRATIVE_TOOLS = {
    "preflight_narrative_plan", "review_narrative_artifact",
    "record_narrative_attempt", "read_narrative_record",
}


class NarrativeAccountAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="narrative-account-access-")
        self.addCleanup(self.temp.cleanup)
        self.settings = settings_for(self.temp.name)
        self.server, self.service = create_mcp(self.settings)
        self.ns = self.settings.oauth_issuer + "|auth0|employee"
        self.project = (await self.ok("create_project", {"project_name": "synthetic narrative access"}))["project_id"]
        self.plan, source, self.request = make_plan()
        # Real stored source bytes provide the input fixture; all four new
        # workflow tools and both production/review work orders use MCP dispatch.
        record = self.service.store.save_artifact_bytes(
            self.ns, self.project, source.encode("utf-8"), file_name="synthetic.txt",
            artifact_type="source:novel", version="v1", metadata={"source_role": "novel"})
        self.plan.update(source_artifact_id=record["artifact_id"], source_sha256=record["fingerprint"])
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _):
            entry = {"artifact_id": record["artifact_id"], "fingerprint": record["fingerprint"],
                     "source_role": "novel", "file_name": "synthetic.txt", "version": "v1"}
            hub["project_state"]["latest_artifacts"]["source:novel"] = entry
            hub["project_state"].setdefault("authoritative_sources", []).append(entry)
        self.order = (await self.ok("begin_project_task", self.begin_args()))["work_order_id"]

    async def call(self, name, args, *, subject="auth0|employee", claims=None):
        token = token_for(self.settings, subject, **(claims or {}))
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token), patch(
            "shortdrama_mcp.app._window_session_id", return_value="openai:synthetic-shared-window"
        ):
            return await self.server.call_tool(name, args)

    async def ok(self, name, args, **kwargs):
        result = await self.call(name, args, **kwargs)
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    def begin_args(self, target=None):
        return {"project_id": self.project, "operation": "narrative_audit" if target else "script_create",
                "mode": "audit_only" if target else "execute",
                "authorization": "read_only" if target else "explicit_execute",
                "request_text": self.request, "targets": [target] if target else ["S1"],
                "authoritative_source": self.plan["source_artifact_id"], "latest_version": "v1",
                "target_boundary": "synthetic", "must_keep": [], "must_remove": [],
                "protected_scope": [], "expected_outputs": ["review"] if target else ["script"],
                "window_key": "same-user-window"}

    def records(self):
        return self.service._narrative_records(self.ns, self.project)

    async def preflight(self):
        return await self.ok("preflight_narrative_plan", {
            "project_id": self.project, "work_order_id": self.order, "plan": self.plan})

    async def review(self, target, target_sha, candidate=None):
        audit = (await self.ok("begin_project_task", self.begin_args(target)))["work_order_id"]
        review = make_review(self.plan, candidate)
        review["target_sha256"] = target_sha
        result = await self.ok("review_narrative_artifact", {"project_id": self.project,
            "work_order_id": audit, "target_id": target, "review": review})
        return audit, result

    def validation_args(self):
        hub = self.service.store.load_hub(self.ns, self.project)
        order = next(o for o in hub["work_orders"] if o["work_order_id"] == self.order)
        return {"project_id": self.project, "work_order_id": self.order,
                "gate_results": {gate: "pass" for gate in order["required_validation_gates"]},
                "residual_issue_count": 0, "introduced_issue_count": 0, "blockers": []}

    async def test_producer_completes_narrative_flow_with_distinct_audit_authority(self):
        attempt = await self.ok("record_narrative_attempt", {"project_id": self.project,
            "work_order_id": self.order, "outcome": "incomplete", "reason": "公开夹具尚未开始正文"})
        read = await self.ok("read_narrative_record", {"project_id": self.project,
            "record_id": attempt["data"]["record_id"]})
        self.assertEqual(json.loads(read["data"]["content"])["outcome"], "incomplete")
        plan = await self.preflight()
        self.assertEqual(plan["status"], "plan_checked_pending_review")
        plan_id = plan["data"]["plan_id"]
        audit_id, plan_review = await self.review(plan_id, plan["data"]["plan_sha256"])
        self.assertEqual(plan_review["status"], "independent_review_pass")
        hub = self.service.store.load_hub(self.ns, self.project)
        orders = {o["work_order_id"]: o for o in hub["work_orders"]}
        self.assertEqual(orders[self.order]["server_actor_role"], "production")
        self.assertEqual(orders[audit_id]["server_actor_role"], "independent_audit")
        plan_record = self.service._narrative_record(self.ns, self.project, plan_id)
        review_record = self.service._narrative_record(self.ns, self.project, plan_review["data"]["review_id"])
        for key in ("window_binding_id", "actor_binding_id"):
            self.assertNotEqual(plan_record["producer_binding"][key], review_record["reviewer_binding"][key])
        candidate = make_candidate(self.plan)
        saved = await self.ok("commit_project_artifact", {"project_id": self.project,
            "work_order_id": self.order, "artifact_type": "script", "output_format": "text",
            "file_name": "synthetic.txt", "source_artifact_id": self.plan["source_artifact_id"],
            "narrative_candidate": {"plan_id": plan_id, "candidate": candidate}})
        self.assertEqual(saved["status"], "saved_pending_independent_review")
        with self.assertRaisesRegex(ToolError, "independent_review_required"):
            await self.call("complete_task_validation", self.validation_args())
        await self.review(saved["data"]["publication_id"], saved["data"]["artifact"]["fingerprint"], candidate)
        validated = await self.ok("complete_task_validation", self.validation_args())
        self.assertEqual(validated["status"], "pass")
        denied = await self.call("signoff_task", {"project_id": self.project,
            "work_order_id": self.order, "decision": "approve", "signer": "caller claim"})
        self.assertTrue(denied.is_error)
        self.assertIn("admin_required", denied.content[0].text)

    async def test_role_catalogs_include_exact_narrative_tools_and_preserve_admin_exclusions(self):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings)):
            producer = {tool.name for tool in await self.server.list_tools()}
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, "auth0|owner")):
            admin = {tool.name for tool in await self.server.list_tools()}
        self.assertEqual(producer, PRODUCER_TOOLS)
        self.assertEqual(len(producer), 21)
        self.assertEqual(len(admin), 24)
        self.assertTrue(NARRATIVE_TOOLS <= producer)
        self.assertEqual(admin - producer, {"manage_user_access", "signoff_task", "recover_storyboard_handoff"})

    async def test_production_order_cannot_sign_review_even_with_forged_role_claim(self):
        plan = await self.preflight()
        before = self.records()
        review = make_review(self.plan)
        review["target_sha256"] = plan["data"]["plan_sha256"]
        with self.assertRaisesRegex(ToolError, "narrative_authority_required"):
            await self.call("review_narrative_artifact", {"project_id": self.project,
                "work_order_id": self.order, "target_id": plan["data"]["plan_id"], "review": review},
                claims={"role": "admin", "roles": ["independent_audit"], "user_metadata": {"role": "admin"}})
        self.assertEqual(before, self.records())

    async def test_audit_order_cannot_be_used_to_produce_or_record_production_attempt(self):
        plan = await self.preflight()
        audit = (await self.ok("begin_project_task", self.begin_args(plan["data"]["plan_id"])))["work_order_id"]
        before = self.records()
        for name, payload in (
            ("preflight_narrative_plan", {"plan": self.plan}),
            ("record_narrative_attempt", {"outcome": "incomplete", "reason": "cannot borrow audit authority"}),
        ):
            with self.subTest(tool=name):
                with self.assertRaisesRegex(ToolError, "narrative_authority_required"):
                    await self.call(name, {"project_id": self.project, "work_order_id": audit, **payload})
        self.assertEqual(before, self.records())

    async def test_narrative_audit_rejects_execution_mode_or_write_authorization(self):
        before = self.service.store.load_hub(self.ns, self.project)
        for key, value in (("mode", "execute"), ("mode", "audit_and_repair"),
                           ("authorization", "explicit_execute")):
            with self.subTest(key=key, value=value):
                args = self.begin_args("NAR-synthetic-target")
                args[key] = value
                with self.assertRaises(ToolError):
                    await self.call("begin_project_task", args)
        self.assertEqual(before, self.service.store.load_hub(self.ns, self.project))

    async def test_new_write_tools_cannot_consume_maintenance_order(self):
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _):
            hub["work_orders"].append({"work_order_id": "WO-maintenance", "operation": "rule_improvement"})
        before = self.records()
        for name, payload in (
            ("preflight_narrative_plan", {"plan": self.plan}),
            ("record_narrative_attempt", {"outcome": "failed", "reason": "invalid maintenance scope"}),
            ("review_narrative_artifact", {"target_id": "NAR-any", "review": {}}),
        ):
            with self.subTest(tool=name):
                result = await self.call(name, {"project_id": self.project, "work_order_id": "WO-maintenance", **payload})
                self.assertTrue(result.is_error)
                self.assertIn("producer_cannot_use_maintenance_work_order", result.content[0].text)
        self.assertEqual(before, self.records())

    async def test_disabled_account_loses_all_new_tools_before_handler(self):
        owner = self.server.access.principal
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, "auth0|owner")):
            self.server.access.manage(owner(), "set", "auth0|employee", "producer", False)
        before = self.records()
        for name in NARRATIVE_TOOLS:
            with self.subTest(tool=name):
                denied = await self.call(name, {"project_id": self.project})
                self.assertTrue(denied.is_error)
                self.assertIn("account_disabled", denied.content[0].text)
        self.assertEqual(before, self.records())

    async def test_narrative_record_read_keeps_account_project_isolation(self):
        attempt = await self.ok("record_narrative_attempt", {"project_id": self.project,
            "work_order_id": self.order, "outcome": "refused", "reason": "synthetic private fixture"})
        with self.assertRaisesRegex(ToolError, "unknown narrative_records record_id") as raised:
            await self.call("read_narrative_record", {"project_id": self.project,
                "record_id": attempt["data"]["record_id"]}, subject="auth0|different-employee")
        self.assertNotIn("synthetic private fixture", str(raised.exception))


class NarrativeAccessHTTPTests(unittest.TestCase):
    def test_signed_producer_http_discovers_and_reads_narrative_contract_without_admin_access(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory(prefix="narrative-access-http-") as directory:
            settings = settings_for(directory)
            verifier = JwtTokenVerifier(settings)
            verifier.jwks_client.get_signing_key_from_jwt = lambda _: SimpleNamespace(key=key.public_key())
            with patch("shortdrama_mcp.app.JwtTokenVerifier", return_value=verifier):
                server, _ = create_mcp(settings)
            app = server.streamable_http_app(json_response=True, stateless_http=True, host="storyboard.example.test")
            claims = {"sub": "auth0|employee", "iss": settings.oauth_issuer, "aud": settings.oauth_audience,
                      "iat": int(time.time()), "exp": int(time.time()) + 120,
                      "scope": "storyboard:read storyboard:write", "role": "admin"}
            token = jwt.encode(claims, key, algorithm="RS256")
            with TestClient(app, base_url=settings.public_base_url) as client:
                def rpc(method, params):
                    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()["result"]
                catalog = {tool["name"] for tool in rpc("tools/list", {})["tools"]}
                self.assertEqual(catalog, PRODUCER_TOOLS)
                self.assertEqual(len(catalog), 21)
                created = rpc("tools/call", {"name": "create_project", "arguments": {"project_name": "http-synthetic"}})
                self.assertFalse(created.get("isError", False), created)
                project = created["structuredContent"]["project_id"]
                contract = rpc("tools/call", {"name": "read_narrative_record", "arguments": {
                    "project_id": project, "record_id": "contract"}})
                self.assertFalse(contract.get("isError", False), contract)
                self.assertEqual(contract["structuredContent"]["data"]["record_type"], "workflow_reference")
                denied = rpc("tools/call", {"name": "manage_user_access", "arguments": {"action": "list"}})
                self.assertTrue(denied["isError"])
                self.assertIn("admin_required", denied["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
