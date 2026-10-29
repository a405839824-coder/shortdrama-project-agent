"""Public MCP/service generation bridge; no fake validator or semantic PASS."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"), str(ROOT / "skills/shortdrama-storyboard/scripts")]

import author_storyboard
import shortdrama_mcp.service as service_module
from shortdrama_mcp.canonical import canonical_sha256
import test_v055_quality_migration as service_fixtures
from test_v0527_source_decision_independent_audit import complete_source_input
from test_efficiency import valid_storyboard


class McpProductionAuthoringTests(unittest.TestCase):
    namespace = "local-single-user"

    def setUp(self):
        self.fixture = service_fixtures.V055QualityAndMigrationTests()
        self.fixture.setUp()
        self.server, self.service = self.fixture.server, self.fixture.service
        self.project = self.fixture._project("合成生成入口")
        self.responses = []
        self.decisions, graph_bytes, _ = complete_source_input()
        # New production now requires an explicit style choice before planning.
        from prompt_libraries import make_selection, make_visual_style
        selection = make_selection('animation', '3d')
        self.decisions['project']['source_lock']['prompt_library'] = selection
        self.decisions['project']['source_lock']['visual_style'] = make_visual_style(selection,
            style_id='synthetic-production-look', revision='1', render_mode='stylized_realism',
            character_design='成年角色七头身，椭圆脸型，五官比例保持一致',
            materials='皮肤哑光，头发分束，衣料保持纤维层次',
            palette='青灰与暖棕为主色，小面积朱红', shading='体块明暗清楚，柔和阴影')
        from production_plan_test_support import with_plan
        texts = {r['source_id']:r['source_text'] for r in self.decisions['project']['prevention_evidence']['source_ledger']}
        graph_bytes = with_plan(self.decisions, graph_bytes, texts)
        self.graph_order = self.begin("storyboard_plan", planning=True)
        saved = self.mcp("commit_project_artifact", {
            "project_id": self.project, "work_order_id": self.graph_order,
            "artifact_type": "storyboard_plan", "output_format": "json", "file_name": "source-graph.json",
            "structured_data": json.loads(graph_bytes),
        })
        self.graph_id = saved["artifact_id"]
        self.graph_record, graph_path = self.service.store.get_artifact(self.namespace, self.project, self.graph_id)
        self.graph_bytes = graph_path.read_bytes()
        self.order = self.begin("storyboard_create")
        self.request = {"contract": "production-authoring-request-v1", "decisions": self.decisions,
                        "source_graph_artifact_id": self.graph_id,
                        "source_graph_sha256": hashlib.sha256(self.graph_bytes).hexdigest()}

    def tearDown(self):
        archive = os.environ.get("MCP_PRODUCTION_TEST_ARTIFACTS")
        if archive:
            target = Path(archive) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            folder = self.request_root()
            if folder.exists():
                shutil.copytree(folder, target / "authoring_requests")
            (target / "responses.json").write_text(json.dumps(self.responses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.fixture.tearDown()

    def mcp(self, name, arguments):
        result = asyncio.run(self.server.call_tool(name, arguments))
        self.assertFalse(result.is_error, result.content)
        self.assertIsNotNone(result.structured_content)
        self.responses.append({"tool": name, "response": result.structured_content})
        return result.structured_content

    def begin(self, operation, *, planning=False, project=None):
        return self.mcp("begin_project_task", {
            "project_id": project or self.project, "operation": operation,
            "mode": "plan_only" if planning else "execute",
            "authorization": "plan_only" if planning else "explicit_execute",
            "request_text": "合成来源图与生成入口验证", "targets": ["EP90"],
            "authoritative_source": "合成来源文本，语义仍须独审", "latest_version": "v1",
            "target_boundary": "EP90", "must_keep": ["全部来源条件"], "must_remove": [],
            "protected_scope": [], "expected_outputs": ["来源图" if planning else "分镜"],
            "window_key": operation,
        })["work_order_id"]

    def arguments(self, request=None, order=None):
        scene = self.decisions["shots"][0]["continuity_id"]
        if isinstance(request,dict) and request.get('shots'):
            scene=request['shots'][0].get('场景连续性ID',scene)
        return {"project_id": self.project, "work_order_id": order or self.order,
                "storyboard": self.request if request is None else request,
                "scope_kind": "scene", "scope_id": scene}

    def direct(self, request=None, order=None):
        response = self.service.preflight_storyboard_draft(
            self.namespace, **self.arguments(request, order), request_session_id=None,
        )
        self.responses.append({"tool": "service.preflight_storyboard_draft", "response": response.model_dump()})
        return response

    def request_root(self):
        return self.service.store.project_dir(self.namespace, self.project) / "authoring_requests"

    def records(self):
        root = self.request_root()
        return sorted(root.iterdir()) if root.exists() else []

    def assert_no_publication(self):
        artifacts = self.service.store.list_artifacts(self.namespace, self.project)
        self.assertEqual([row["artifact_id"] for row in artifacts], [self.graph_id])
        self.assertEqual(self.service.store.list_project_records(self.namespace, self.project, kind="handoffs"), [])

    def test_real_mcp_request_reaches_full_validator_and_returns_actual_canonical(self):
        with patch.object(service_module.validate_storyboard, "validate_project",
                          wraps=service_module.validate_storyboard.validate_project) as validator:
            result = self.mcp("preflight_storyboard_draft", self.arguments())
        data = result["data"]
        self.assertTrue(validator.called)
        self.assertEqual(validator.call_args.kwargs["mode"], "audit")
        self.assertEqual(validator.call_args.args[0]["schema_version"], "R10.1")
        self.assertEqual(data["authoring_attempt"]["status"], "rendered_pending_preflight")
        canonical = data["compiled_storyboard"]
        self.assertEqual(canonical_sha256(canonical), data["draft_preflight"]["candidate_sha256"])
        self.assertEqual(canonical_sha256(canonical), data["authoring_attempt"]["canonical_sha256"])
        self.assertTrue(data["report"]["issues"], "The structural fixture deliberately lacks full legacy evidence")
        self.assertFalse(any(row["error_code"] == "E-DRAFT-CONTRACT-001" for row in data["report"]["issues"]))
        self.assertFalse(data["commit_ready"])
        self.assertIsNone(data["authoring_attempt"]["semantic_accuracy_percent"])
        self.assertEqual(canonical["prevention_evidence"]["production_derivation"]["independent_audit_status"], "not_performed")
        folders = self.records()
        self.assertEqual(len(folders), 1)
        self.assertEqual(json.loads((folders[0] / "request.original.json").read_text()), self.request)
        self.assertEqual((folders[0] / "source_graph.original.json").read_bytes(), self.graph_bytes)
        self.assert_no_publication()
        self.assertEqual(len(asyncio.run(self.server.list_tools())), 24)

    def test_wrong_graph_hash_records_rejection_without_story_denominator(self):
        request = copy.deepcopy(self.request)
        request["source_graph_sha256"] = "0" * 64
        with patch.object(service_module.validate_storyboard, "validate_project",
                          wraps=service_module.validate_storyboard.validate_project) as validator:
            result = self.direct(request)
        validator.assert_not_called()
        self.assertEqual(result.status, "authoring_rejected")
        self.assertIsNone(result.data["compiled_storyboard"])
        self.assertIsNone(result.data["quality"])
        self.assertIn("production_authoring_graph_identity", str(result.data["authoring_attempt"]["findings"]))
        folder, = self.records()
        self.assertEqual((folder / "source_graph.original.json").read_bytes(), self.graph_bytes)
        self.assertEqual(self.service.store.list_project_records(self.namespace, self.project, kind="draft_preflights"), [])
        self.assert_no_publication()

    def test_source_mismatch_rejects_real_record_and_keeps_original_decisions(self):
        request = copy.deepcopy(self.request)
        request["decisions"]["project"]["prevention_evidence"]["source_decisions"]["uses"][0]["text"] = "擅改来源文字。"
        result = self.direct(request)
        self.assertEqual(result.status, "authoring_rejected")
        folder, = self.records()
        attempts = list((folder / "compiler_attempts").iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(json.loads((attempts[0] / "input.original.json").read_text()), request["decisions"])
        self.assertFalse((attempts[0] / "canonical.json").exists())
        self.assertTrue(json.loads((attempts[0] / "report.json").read_text())["findings"])
        self.assertIsNone(result.data["quality"])
        self.assert_no_publication()

    def test_wrong_work_order_is_rejected_before_any_graph_read_or_attempt_write(self):
        with patch.object(self.service.store, "get_artifact", wraps=self.service.store.get_artifact) as read:
            with self.assertRaises(ValueError):
                self.direct(order=self.graph_order)
        read.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_concurrent_same_authority_is_fenced_before_second_attempt(self):
        entered, release = threading.Event(), threading.Event()
        original_record = author_storyboard.record_attempt
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise TimeoutError("test did not release compiler")
            return original_record(*args, **kwargs)
        with patch.object(author_storyboard, "record_attempt", side_effect=delayed):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(self.direct)
                try:
                    self.assertTrue(entered.wait(10))
                    with self.assertRaisesRegex(ValueError, "authority_operation_in_progress"):
                        self.direct()
                    self.assertEqual(len(self.records()), 1)
                finally:
                    release.set()
                result = first.result(timeout=20)
        self.assertIsNotNone(result.data["compiled_storyboard"])
        self.assertEqual(len(self.records()), 1)

    def test_legacy_canonical_cannot_bypass_current_production_plan(self):
        self.service.__dict__.pop('_require_current_production', None)
        legacy = valid_storyboard()
        from prompt_libraries import style_fragment
        for key in ('prompt_library', 'visual_style'):
            legacy['source_lock'][key] = copy.deepcopy(self.decisions['project']['source_lock'][key])
        for shot in legacy['shots']:
            shot['生成限制'] += '；' + style_fragment(legacy['source_lock'])
        with patch.object(author_storyboard, "record_attempt", wraps=author_storyboard.record_attempt) as compiler:
            result = self.direct(legacy)
        compiler.assert_not_called()
        self.assertNotIn("authoring_attempt", result.data)
        self.assertNotIn("compiled_storyboard", result.data)
        self.assertEqual(self.records(), [])
        self.assertTrue(any(row["error_code"] == "E-DRAFT-CONTRACT-001" for row in result.data["report"]["issues"]))
        self.assertIn('production_plan_required', str(result.data['report']['issues']))

    def test_mutually_exclusive_wrapper_rejects_extra_canonical_payload(self):
        request = copy.deepcopy(self.request)
        request["canonical"] = valid_storyboard()
        result = self.direct(request)
        self.assertEqual(result.status, "authoring_rejected")
        self.assertIn("production_authoring_request_shape", str(result.data["authoring_attempt"]["findings"]))
        folder, = self.records()
        self.assertEqual(json.loads((folder / "request.original.json").read_text()), request)
        self.assertFalse((folder / "source_graph.original.json").exists())

    def test_foreign_project_graph_cannot_supply_current_request(self):
        other = str(self.service.create_project(self.namespace, "另一合成项目").project_id)
        other_order = self.begin("storyboard_plan", planning=True, project=other)
        saved = self.mcp("commit_project_artifact", {
            "project_id": other, "work_order_id": other_order, "artifact_type": "storyboard_plan",
            "output_format": "json", "file_name": "foreign-graph.json", "structured_data": json.loads(self.graph_bytes),
        })
        request = copy.deepcopy(self.request)
        request["source_graph_artifact_id"] = saved["artifact_id"]
        result = self.direct(request)
        self.assertEqual(result.status, "authoring_rejected")
        self.assertIn("unknown artifact_id", str(result.data["authoring_attempt"]["findings"]))
        folder, = self.records()
        self.assertFalse((folder / "source_graph.original.json").exists())


if __name__ == "__main__":
    unittest.main()
