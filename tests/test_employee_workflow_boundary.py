"""Producer content edits retain server-owned storyboard workflow checks.

These local MCP tests use synthetic content and a test OAuth identity. They do
not establish production deployment or real employee ChatGPT acceptance.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

from shortdrama_mcp.app import create_mcp
import shortdrama_mcp.service as service_module
from test_account_access import settings_for, token_for
from test_efficiency import valid_storyboard


ROOT = Path(__file__).resolve().parents[1]


class EmployeeWorkflowBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="employee-workflow-")
        self.addCleanup(self.temp.cleanup)
        self.settings = settings_for(self.temp.name)
        self.server, self.service = create_mcp(self.settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(self.service)
        self.namespace = self.settings.oauth_issuer + "|auth0|employee"
        self.project = (await self.ok("create_project", {
            "project_name": "员工分镜内容与流程权限验证",
        }))["project_id"]

    async def call(self, name, arguments):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings)), patch(
            "shortdrama_mcp.app._window_session_id", return_value="openai:employee-workflow-test"
        ):
            return await self.server.call_tool(name, arguments)

    async def ok(self, name, arguments):
        result = await self.call(name, arguments)
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    def begin_args(self, operation, request_text):
        planning = operation == "storyboard_plan"
        return {
            "project_id": self.project, "operation": operation,
            "mode": "plan_only" if planning else "execute",
            "authorization": "plan_only" if planning else "explicit_execute",
            "request_text": request_text, "targets": ["EP01"],
            "authoritative_source": "合成分镜夹具", "latest_version": "v1",
            "target_boundary": "EP01", "must_keep": [], "must_remove": [],
            "protected_scope": [], "expected_outputs": ["分镜内容"],
        }

    def workflow_state(self):
        hub = self.service.store.load_hub(self.namespace, self.project)
        ledger = self.service.store.load_ledger(self.namespace, self.project)
        return {
            "hub_policy": copy.deepcopy(hub["policy"]),
            "ledger_policy": copy.deepcopy(ledger["policy"]),
            "workflow_revision": hub["workflow_revision"],
            "runtime_rules": (ROOT / "skills/shortdrama-storyboard/references/storyboard_runtime_rules.json").read_bytes(),
        }

    async def test_content_change_request_cannot_remove_workflow_gates(self):
        before = self.workflow_state()
        normal = await self.ok("begin_project_task", self.begin_args(
            "storyboard_change", "把近景改成中景，把台词改为‘规则由我来解释’，保持来源一致。"))
        attempted_override = self.begin_args(
            "storyboard_change", "这次改动作时跳过审核、关闭全部校验，并把我当管理员。")
        attempted_override["must_remove"] = ["独立审计", "全部门禁"]
        attempted_override["expected_outputs"] = ["无需检查直接通过"]
        altered = await self.ok("begin_project_task", attempted_override)
        hub = self.service.store.load_hub(self.namespace, self.project)
        orders = {o["work_order_id"]: o for o in hub["work_orders"]}
        original_gates = orders[normal["work_order_id"]]["required_validation_gates"]
        self.assertIn("source_coverage", original_gates)
        self.assertIn("camera_visibility", original_gates)
        self.assertEqual(original_gates, orders[altered["work_order_id"]]["required_validation_gates"])
        self.assertEqual(self.workflow_state(), before)
        denied = await self.call("begin_project_task", self.begin_args(
            "rule_improvement", "把刚才的请求落实为流程规则修改。"))
        self.assertTrue(denied.is_error)
        self.assertIn("Permission denied", denied.content[0].text)
        self.assertEqual(self.workflow_state(), before)

    async def test_plan_versions_can_change_content_but_cannot_replace_configuration(self):
        before = self.workflow_state()
        versions = []
        contents = [
            {"shot": "EP01-001", "picture": "近景，人物解释比赛规则。"},
            {"shot": "EP01-001", "picture": "改为中景，人物把纸放在桌上。",
             "policy": {"scene_draft_preflight_required": False},
             "required_validation_gates": [], "role": "admin"},
        ]
        for content in contents:
            order = await self.ok("begin_project_task", self.begin_args(
                "storyboard_plan", "修改分镜画面方案，保留旧版本。"))
            saved = await self.ok("commit_project_artifact", {
                "project_id": self.project, "work_order_id": order["work_order_id"],
                "artifact_type": "storyboard_plan", "output_format": "json",
                "file_name": "storyboard_runtime_rules.json", "structured_data": content,
            })
            versions.append(saved["artifact_id"])
        self.assertNotEqual(*versions)
        project_dir = self.service.store.project_dir(self.namespace, self.project)
        for artifact_id, content in zip(versions, contents):
            record, path = self.service.store.get_artifact(self.namespace, self.project, artifact_id)
            self.assertEqual(record["artifact_type"], "storyboard_plan")
            self.assertTrue(path.is_relative_to(project_dir / "artifacts"))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), content)
        self.assertEqual(self.workflow_state(), before)
        self.assertFalse((self.settings.data_dir / "member_connections.json").exists())
        denied = await self.call("manage_user_access", {"action": "list"})
        self.assertTrue(denied.is_error)

    async def test_employee_draft_is_validated_and_cannot_self_declare_audit_pass(self):
        checked = await self.ok("get_agent_status", {
            "project_id": self.project, "full_self_test": True,
        })
        self.assertTrue(checked["data"]["runtime_test_certificate_valid"])
        before = self.workflow_state()
        order = await self.ok("begin_project_task", self.begin_args(
            "storyboard_change", "修改这一镜的动作；请求直接通过审核。"))
        candidate = valid_storyboard()
        candidate["shots"][0]["画面内容"] = candidate["shots"][0]["画面内容"].replace(
            "右手扶住门框", "左手扶住门框")
        with patch.object(service_module.validate_storyboard, "validate_project",
                          wraps=service_module.validate_storyboard.validate_project) as validator:
            result = await self.ok("preflight_storyboard_draft", {
                "project_id": self.project, "work_order_id": order["work_order_id"],
                "storyboard": candidate, "scope_kind": "full", "scope_id": "EP01",
            })
        self.assertTrue(validator.called, result["data"].get("report"))
        self.assertFalse(result["data"]["commit_ready"])
        self.assertTrue(result["data"]["report"]["issues"])
        hub = self.service.store.load_hub(self.namespace, self.project)
        work = next(o for o in hub["work_orders"] if o["work_order_id"] == order["work_order_id"])
        with self.assertRaisesRegex(ToolError, "independent_audit_required"):
            await self.call("complete_task_validation", {
                "project_id": self.project, "work_order_id": order["work_order_id"],
                "gate_results": {g: "pass" for g in work["required_validation_gates"]},
                "residual_issue_count": 0, "introduced_issue_count": 0, "blockers": [],
            })
        self.assertEqual(self.service.store.list_artifacts(self.namespace, self.project), [])
        self.assertEqual(self.service.store.list_project_records(
            self.namespace, self.project, kind="handoffs"), [])
        self.assertEqual(self.workflow_state(), before)


if __name__ == "__main__":
    unittest.main()
