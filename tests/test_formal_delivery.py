"""Real template/production/audit checks; synthetic content is not a drama audit."""
from __future__ import annotations

import copy
import asyncio
import tempfile
import unittest
from unittest.mock import patch
from mcp.server.mcpserver.exceptions import ToolError

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.formal_delivery import CONTRACT, inspect_formal_delivery
from test_account_access import settings_for, token_for
import test_v0535_production_contract as current_contract_tests


class FreshEmployeeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_clients_get_same_server_template_and_cannot_replace_it(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = settings_for(folder)
            for platform in ("ChatGPT", "Codex"):
                server, service = create_mcp(settings)
                with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(settings, subject="staff-"+platform)):
                    status = await server.call_tool("get_agent_status", {})
                    self.assertFalse(status.is_error, status.content)
                    data = status.structured_content["data"]
                    self.assertEqual(data["account_access"]["role"], "producer")
                    policy = data["formal_delivery_contract"]
                    self.assertEqual(policy["contract"], CONTRACT)
                    self.assertEqual(len(policy["columns"]), 10)
                    self.assertFalse(policy["fallback_export_allowed"])
                    self.assertFalse(policy["unverified_counts_as_complete"])
                    denied = await server.call_tool("commit_project_artifact", {"artifact_type":"template"})
                    self.assertTrue(denied.is_error)
                    tools = await server.list_tools()
                    read = next(t for t in tools if t.name == "read_project_artifact")
                    self.assertIn("formal_delivery", read.description)
                if platform == "ChatGPT": first = policy
                else: self.assertEqual(policy, first)


class FormalDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.flow = current_contract_tests.CurrentMcpContractTests()
        self.flow.setUp()
        self.addCleanup(self.flow.tearDown)
        self.f = self.flow.f

    def receipt(self, artifact):
        return inspect_formal_delivery(self.f.service, self.f.namespace, self.f.project, artifact)

    def test_real_current_pipeline_requires_audit_and_rejects_alternative_files(self):
        # Observe the intermediate state without replacing any validation.
        original_audit = self.f.service.audit_storyboard
        def observe(*args, **kwargs):
            before = self.receipt(kwargs["artifact_id"])
            self.assertFalse(before.data["formal_delivery"]["formal_delivery_ready"], before.data)
            self.assertTrue(before.data["formal_delivery"]["template_verified"], before.data)
            self.assertTrue(before.data["formal_delivery"]["production_preflight_verified"], before.data)
            self.assertIsNone(before.download_url)
            return original_audit(*args, **kwargs)
        with patch.object(self.f.service, "audit_storyboard", side_effect=observe):
            self.flow.test_full_current_episode_scene_full_commit_and_signed_review()
        commit = next(r["response"] for r in self.f.responses if r["tool"] == "commit_storyboard")
        artifact = commit["artifact_id"]
        result = self.receipt(artifact)
        proof = result.data["formal_delivery"]
        self.assertTrue(proof["formal_delivery_ready"], result.data)
        self.assertTrue(proof["counts_as_complete"])
        self.assertTrue(self.f.service.store.project_record_is_signed(proof))
        self.assertIsNotNone(result.download_url)
        batch_order = self.f.begin("production_batch")
        batch_args = {"project_id":self.f.project,"work_order_id":batch_order,
            "artifact_type":"production_batch","output_format":"json","file_name":"batch.json"}
        with self.assertRaisesRegex(ToolError, "formal_batch_requires"):
            asyncio.run(self.f.server.call_tool("commit_project_artifact",
                {**batch_args,"structured_data":{"counts_as_complete":True,"shots":["handwritten"]}}))
        batch = self.f.mcp("commit_project_artifact",
            {**batch_args,"structured_data":{"storyboard_artifact_ids":[artifact]}})
        self.assertIsNotNone(batch["artifact_id"])
        via_mcp = self.f.mcp("read_project_artifact", {"project_id":self.f.project,
            "artifact_id":artifact,"section":"formal_delivery"})
        self.assertTrue(via_mcp["data"]["formal_delivery"]["formal_delivery_ready"])
        forged = copy.deepcopy(proof); forged["artifact_id"] = "ART-fake"
        self.assertFalse(self.f.service.store.project_record_is_signed(forged))
        record, path = self.f.service.store.get_artifact(self.f.namespace, self.f.project, artifact)
        raw = path.read_bytes()
        for kind in ("conversion", "storyboard"):
            copied = self.f.service.store.save_artifact_bytes(self.f.namespace,self.f.project,raw,
                file_name="伪装正式分镜.xlsx",artifact_type=kind,version="v1",
                work_order_id=record["work_order_id"],metadata=copy.deepcopy(record["metadata"]))
            blocked = self.receipt(copied["artifact_id"])
            self.assertFalse(blocked.data["formal_delivery"]["formal_delivery_ready"], blocked.data)
            self.assertIsNone(blocked.download_url)
        path.write_bytes(raw+b"changed")
        try:
            with self.assertRaisesRegex(ValueError,"fingerprint_mismatch"): self.receipt(artifact)
        finally: path.write_bytes(raw)
        old = record["metadata"]["template_contract_sha256"]
        self.f.service.store.update_artifact_metadata(self.f.namespace,self.f.project,artifact,
            {"template_contract_sha256":"0"*64})
        self.assertFalse(self.receipt(artifact).data["formal_delivery"]["formal_delivery_ready"])
        hub = self.f.service.store.load_hub(self.f.namespace, self.f.project)
        work = next(w for w in hub["work_orders"] if w["work_order_id"] == batch_order)
        validation = {"project_id": self.f.project, "work_order_id": batch_order,
            "gate_results": {gate: "pass" for gate in work["required_validation_gates"]},
            "residual_issue_count": 0, "introduced_issue_count": 0, "blockers": []}
        with self.assertRaisesRegex(ToolError, "formal_batch_unverified"):
            asyncio.run(self.f.server.call_tool("complete_task_validation", validation))
        stale_batch_args = {**batch_args, "work_order_id": self.f.begin("production_batch")}
        with self.assertRaisesRegex(ToolError, "formal_batch_unverified"):
            asyncio.run(self.f.server.call_tool("commit_project_artifact",
                {**stale_batch_args,"structured_data":{"storyboard_artifact_ids":[artifact]}}))
        self.f.service.store.update_artifact_metadata(self.f.namespace,self.f.project,artifact,
            {"template_contract_sha256":old})
        self.assertTrue(self.receipt(artifact).data["formal_delivery"]["formal_delivery_ready"])
        completed = self.f.mcp("complete_task_validation", validation)
        self.assertEqual(completed["status"], "pass")

    def test_unregistered_local_and_self_reported_pass_never_count(self):
        record = self.f.service.store.save_artifact_bytes(self.f.namespace,self.f.project,b"not an xlsx",
            file_name="正式分镜脚本.xlsx",artifact_type="conversion",version="v1",
            metadata={"formal_delivery_ready":True,"audit_status":"PASS","template_verified":True})
        result = self.receipt(record["artifact_id"])
        self.assertFalse(result.data["formal_delivery"]["counts_as_complete"])
        self.assertFalse(result.data["formal_delivery"]["allows_next_batch"])
        with self.assertRaises(ValueError):
            inspect_formal_delivery(self.f.service,"another-employee",self.f.project,record["artifact_id"])


if __name__ == "__main__": unittest.main()
