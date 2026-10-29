"""Project-scoped export compatibility; synthetic tests are not drama audits."""
import copy
import json
import unittest
from unittest.mock import patch

from shortdrama_mcp.formal_delivery import delivery_contract
from shortdrama_mcp.project_template import (DEFAULT, MASTER_LIGHTING, parse_request,
    canonical_variant, selected_variant, assert_project_template)
from shortdrama_mcp.materializer import formal_mother_template_rows
from shortdrama_mcp.file_io import extract_xlsx
import test_v0535_production_contract as current


class ProjectTemplateTests(unittest.TestCase):
    def test_bounded_request_and_explicit_scope(self):
        kwargs = dict(operation="rule_improvement", mode="execute",
                      authorization="explicit_execute", action="create")
        text = 'project-template-v1:'+json.dumps({"contract":"project-template-v1","variant":MASTER_LIGHTING})
        self.assertEqual(parse_request(text, **kwargs), MASTER_LIGHTING)
        for change in ({"operation":"storyboard_change"}, {"mode":"plan_only"},
                       {"authorization":"read_only"}, {"action":"execute_role_continuation"}):
            with self.assertRaises(ValueError): parse_request(text, **(kwargs|change))
        for value in ('project-template-v1:{"contract":"project-template-v1","variant":"bad"}',
                      'project-template-v1:{"contract":"project-template-v1","variant":"standard-v1","variant":"master-lighting-v1"}'):
            with self.assertRaises(ValueError): parse_request(value, **kwargs)
        self.assertEqual(canonical_variant({}), DEFAULT)
        self.assertEqual(selected_variant({}), DEFAULT)
        with self.assertRaises(ValueError):
            assert_project_template({"canonical_extension":{"project_template_variant":MASTER_LIGHTING}}, {})
        self.assertNotEqual(delivery_contract()["template_contract_sha256"],
                            delivery_contract(MASTER_LIGHTING)["template_contract_sha256"])

    def test_3d_style_with_project_lighting_and_signed_delivery(self):
        from test_seedance25_prompt_libraries import lock
        data, graph = current.complete_episode()
        data['project']['source_lock'].update(lock('animation', '3d'))
        with patch.object(current, 'complete_episode', return_value=(data, graph)):
            self.test_real_project_export_and_signed_receipt()

    def test_real_project_export_and_signed_receipt(self):
        flow = current.CurrentMcpContractTests()
        flow.setUp()
        self.addCleanup(flow.tearDown)
        f = flow.f
        def select(variant):
            return f.mcp("begin_project_task", {
                "project_id":f.project,"operation":"rule_improvement","mode":"execute",
                "authorization":"explicit_execute","request_text":"明确授权仅本合成项目E含光影",
                "targets":["project template"],"authoritative_source":"合成验收要求",
                "latest_version":"current","target_boundary":"仅当前项目",
                "must_keep":["全部审核"],"must_remove":[],"protected_scope":["其他项目"],
                "expected_outputs":["受签名模板选项"],
                "confirmation":"project-template-v1:"+json.dumps({"contract":"project-template-v1","variant":variant})})
        select(MASTER_LIGHTING)
        data, graph = current.complete_episode()
        data["project"].setdefault("canonical_extension", {})["project_template_variant"] = MASTER_LIGHTING
        with patch.object(current, "complete_episode", return_value=(data, graph)):
            flow.test_full_current_episode_scene_full_commit_and_signed_review()
        commit = next(r["response"] for r in f.responses if r["tool"] == "commit_storyboard")
        aid = commit["artifact_id"]
        good = f.mcp("read_project_artifact", {"project_id":f.project,"artifact_id":aid,"section":"formal_delivery"})
        self.assertTrue(good["data"]["formal_delivery"]["formal_delivery_ready"], good)
        self.assertEqual(good["data"]["delivery_contract"]["project_template_variant"], MASTER_LIGHTING)
        obj, record = f.service._storyboard_from_artifact(f.namespace,f.project,aid,
            persist_recovered_canonical=False,require_bound_projection=True)
        _, path = f.service.store.get_artifact(f.namespace, f.project, aid)
        extracted = extract_xlsx(path.read_bytes())
        self.assertTrue(extracted["canonical_sidecar_present"])
        from shortdrama_mcp.audit_coverage import build_obligations, _obligation_rule_definitions
        manifest = build_obligations(obj, {})
        actual = next(x for x in manifest['obligations'] if x['rule_id'] == 'standalone_execution')
        self.assertIn('本项目E列须包含', actual['question'])
        self.assertEqual(actual['question'], _obligation_rule_definitions(obj)['standalone_execution']['question'])
        self.assertIn('E列不拼光影', _obligation_rule_definitions()['standalone_execution']['question'])
        rows = formal_mother_template_rows(obj)
        default = copy.deepcopy(obj)
        default["canonical_extension"].pop("project_template_variant")
        previous = formal_mother_template_rows(default)
        for row, old in zip(rows, previous):
            self.assertEqual(len(row), 11)
            self.assertIn(row["光影"], row["正式分镜母版"])
            for name in ("光影", "光源来源", "光源方向", "色温倾向"):
                self.assertIn(name+"：", row["正式分镜母版"])
                self.assertNotIn(name+"：", old["正式分镜母版"])
            for key in row:
                if key != "正式分镜母版": self.assertEqual(row[key], old[key])
        # A post-export lighting edit must not reuse the original signature.
        from openpyxl import load_workbook
        from shortdrama_mcp.formal_delivery import inspect_formal_delivery
        original_bytes = path.read_bytes()
        for column in ('E', 'K'):
            workbook = load_workbook(path)
            workbook['正式分镜母版'][column+'2'] = str(workbook['正式分镜母版'][column+'2'].value) + '\n篡改光影'
            workbook.save(path)
            try:
                with self.assertRaisesRegex(ValueError, 'fingerprint_mismatch'):
                    inspect_formal_delivery(f.service, f.namespace, f.project, aid)
            finally:
                path.write_bytes(original_bytes)
        # The current project choice invalidates a receipt for a different export.
        select(DEFAULT)
        stale = f.mcp("read_project_artifact", {"project_id":f.project,"artifact_id":aid,"section":"formal_delivery"})
        self.assertFalse(stale["data"]["formal_delivery"]["formal_delivery_ready"])
        self.assertIsNone(stale["download_url"])


class TemplatePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_employee_cannot_select_project_template(self):
        import tempfile
        from test_account_access import settings_for, token_for
        from shortdrama_mcp.app import create_mcp
        with tempfile.TemporaryDirectory() as folder:
            settings = settings_for(folder)
            server, _ = create_mcp(settings)
            with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(settings)):
                created = await server.call_tool("create_project", {"project_name":"template permission"})
                pid = created.structured_content["project_id"]
                denied = await server.call_tool("begin_project_task", {
                    "project_id":pid,"operation":"rule_improvement","mode":"execute",
                    "authorization":"explicit_execute","request_text":"template",
                    "targets":["template"],"authoritative_source":"test","latest_version":"current",
                    "target_boundary":"project","must_keep":[],"must_remove":[],"protected_scope":[],
                    "expected_outputs":["template"],
                    "confirmation":"project-template-v1:"+json.dumps({"contract":"project-template-v1","variant":MASTER_LIGHTING})})
                self.assertTrue(denied.is_error, denied)


if __name__ == "__main__": unittest.main()
