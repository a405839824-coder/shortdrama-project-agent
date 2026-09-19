"""Exercise diagnostic routes through public MCP dispatch, never live projects."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.models import ToolResponse
from test_efficiency import valid_storyboard


class PublicDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-diagnostic-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), public_base_url="http://127.0.0.1:8765",
            host="127.0.0.1", port=8765, deployment_mode="local", auth_mode="none",
            oauth_issuer=None, oauth_audience=None, oauth_jwks_url=None, oauth_scopes=(),
            allow_local_file_urls=False, max_file_bytes=1024 * 1024)
        self.server, self.service = create_mcp(self.settings)
        self.namespace = "local-single-user"
        self.project = self.service.create_project(self.namespace, "synthetic-read-only").project_id
        self.record = self.service.store.save_artifact_json(self.namespace, self.project, valid_storyboard(),
            artifact_type="storyboard", file_name="synthetic.json", version="v1")
        self.args = {"project_id": self.project, "artifact_id": self.record["artifact_id"]}

    def project_bytes(self):
        directory = self.service.store.project_dir(self.namespace, self.project)
        return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}

    async def read(self, **kwargs):
        result = await self.server.call_tool("read_project_artifact", self.args | kwargs)
        self.assertFalse(result.is_error)
        return result.structured_content

    def canned_report(self):
        return ToolResponse(status="diagnostic_completed", summary="只读机器诊断，未签发审计结论",
            project_id=self.project, artifact_id=self.record["artifact_id"], data={
            "read_only": True, "artifact_fingerprint": "a" * 64,
            "ruleset_version": "rule-1", "validator_version": "validator-1",
            "requested_episode_ids": ["EP01"], "checked_shot_count": 60,
            "issues": [{"error_code": "E-FIELD-CONTENT-001", "location": "shots[0]", "evidence": "长问题🌿" * 900}]
                + [{"error_code": "E-CONTENT-001", "location": f"shots[{i}]", "evidence": f"证据{i}"}
                   for i in range(1, 57)],
            "unscoped_or_dependency_issues": [{"error_code": "E-TEMPLATE-001", "evidence": "映射"}],
            "outside_scope_issues": [{"error_code": "E-CONTENT-001", "location": "shots[60]", "evidence": "范围外"}],
            "formal_audit_status": "not_performed", "certificate_created": False,
            "historical_evidence_repaired": False, "raw_generation_quality": None})

    async def test_actual_diagnosis_is_public_and_preserves_project_bytes(self):
        before = self.project_bytes()
        result = await self.read(diagnose_storyboard=True, max_chars=1000, limit=3)
        data = result["data"]
        self.assertTrue(data["read_only"])
        self.assertEqual(data["formal_audit_status"], "not_performed")
        self.assertFalse(data["certificate_created"])
        self.assertIsNone(result["download_url"])
        self.assertEqual(before, self.project_bytes())
        tool = next(t for t in await self.server.list_tools() if t.name == "read_project_artifact")
        self.assertTrue(tool.annotations.read_only_hint)

    async def test_public_continuation_calls_deliver_all_rows_and_exact_large_detail(self):
        report = self.canned_report()
        original = copy.deepcopy(report.model_dump())
        args = self.args | {"diagnose_storyboard": True, "diagnostic_episode_ids": ["EP01"],
                            "offset": 0, "limit": 7, "max_chars": 512}
        seen, detail_args = [], None
        before = self.project_bytes()
        with patch.object(self.service, "diagnose_storyboard_artifact", return_value=report):
            for page_number in range(80):
                result = await self.server.call_tool("read_project_artifact", args)
                data = result.structured_content["data"]
                rows = sum((data[key] for key in ("issues", "unscoped_or_dependency_issues", "outside_scope_issues")), [])
                seen.extend(row["issue_index"] for row in rows)
                self.assertLessEqual(len(json.dumps(rows, ensure_ascii=False, separators=(",", ":"))), 512)
                if page_number == 0:
                    self.assertTrue(rows[0]["detail_required"])
                    detail_args = self.args | {"diagnose_storyboard": True, "diagnostic_episode_ids": ["EP01"],
                        "section": rows[0]["detail_section"], "offset": 0, "max_chars": 512}
                self.assertFalse(data["certificate_created"])
                if not data["page"]["has_more"]:
                    break
                next_args = data["page"]["next_call"]
                self.assertGreater(next_args["offset"], args["offset"])
                args = next_args  # no hand-built translation: published continuation is executable
            else:
                self.fail("pagination did not terminate")
            self.assertEqual(seen, list(range(59)))
            chunks = []
            for _ in range(30):
                result = await self.server.call_tool("read_project_artifact", detail_args)
                data = result.structured_content["data"]
                chunks.append(data["detail"]["text"])
                if not data["page"]["has_more"]:
                    break
                next_args = data["page"]["next_call"]
                self.assertGreater(next_args["offset"], detail_args["offset"])
                detail_args = next_args
            else:
                self.fail("detail pagination did not terminate")
        self.assertEqual(json.loads("".join(chunks)), report.data["issues"][0])
        self.assertEqual(report.model_dump(), original)
        self.assertEqual(before, self.project_bytes())

    async def test_public_stale_snapshot_rejects_changed_findings(self):
        report = self.canned_report()
        with patch.object(self.service, "diagnose_storyboard_artifact", return_value=report):
            first = await self.read(diagnose_storyboard=True, limit=1)
            continuation = first["data"]["page"]["next_call"]
            report.data["issues"][0]["evidence"] = "changed after first page"
            with self.assertRaisesRegex(Exception, "diagnostic_snapshot_changed"):
                await self.server.call_tool("read_project_artifact", continuation)

    async def test_incompatible_parameters_fail_before_diagnostic_execution(self):
        combinations = [
            {"diagnose_storyboard": True, "if_none_match": "a" * 64},
            {"diagnose_storyboard": True, "bulk_ranges": []},
            {"diagnose_storyboard": True, "section": "formal_delivery"},
            {"diagnose_storyboard": True, "section": "audit_obligations"},
            {"diagnostic_episode_ids": ["EP01"]},
            {"section": "diagnostic:" + "a" * 64},
        ]
        with patch.object(self.service, "diagnose_storyboard_artifact") as diagnose:
            for kwargs in combinations:
                with self.subTest(kwargs=kwargs), self.assertRaises(Exception):
                    await self.read(**kwargs)
            diagnose.assert_not_called()

    async def test_legacy_read_arguments_still_route_to_legacy_reader(self):
        response = ToolResponse(status="ok", summary="legacy preview", data={"content": "legacy"})
        with patch.object(self.service, "read_artifact", return_value=response) as legacy, \
                patch.object(self.service, "diagnose_storyboard_artifact") as diagnose:
            result = await self.read(automatic_bulk=False, section="shots", offset=2, limit=3)
        self.assertEqual(result["data"]["read_mode"], "legacy")
        legacy.assert_called_once_with(self.namespace, project_id=self.project,
            artifact_id=self.record["artifact_id"], section="shots", offset=2, limit=3)
        diagnose.assert_not_called()

    async def test_obligations_route_is_read_only_and_does_not_grant_acceptance(self):
        before = self.project_bytes()
        first = await self.read(section="audit_obligations", limit=2, max_chars=64000)
        self.assertEqual(first["status"], "audit_obligations")
        self.assertEqual(len(first["data"]["obligations"]), 2)
        self.assertEqual(first["data"]["next_offset"], 2)
        second = await self.read(section="audit_obligations", offset=2, limit=2, max_chars=64000)
        self.assertEqual(first["data"]["manifest_sha256"], second["data"]["manifest_sha256"])
        self.assertNotEqual(first["data"]["obligations"][0]["obligation_id"],
                            second["data"]["obligations"][0]["obligation_id"])
        self.assertFalse(first["data"]["semantic_truth_guaranteed"])
        self.assertNotIn("_values", first["data"])
        self.assertIsNone(first["download_url"])
        self.assertEqual(before, self.project_bytes())

    async def test_status_with_no_committed_delivery_cannot_claim_completion(self):
        result = await self.server.call_tool("get_agent_status", {"project_id": self.project, "full_self_test": False})
        state = result.structured_content["data"]["workflow_state"]
        self.assertFalse(state["counts_as_complete"])
        self.assertFalse(state["allows_next_batch"])
        self.assertEqual(state["formal_delivery_state"], "not_verified")

    async def test_http_tool_endpoint_exposes_diagnostic_page(self):
        report = self.canned_report()
        app = self.server.streamable_http_app(json_response=True, stateless_http=True, host="127.0.0.1")
        with patch.object(self.service, "diagnose_storyboard_artifact", return_value=report), \
                TestClient(app, base_url=self.settings.public_base_url) as client:
            response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "read_project_artifact", "arguments": self.args |
                           {"diagnose_storyboard": True, "limit": 1, "max_chars": 512}}},
                headers={"Accept": "application/json, text/event-stream"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()["result"]
        self.assertFalse(payload.get("isError", False))
        self.assertTrue(payload["structuredContent"]["data"]["page"]["has_more"])
        self.assertFalse(payload["structuredContent"]["data"]["certificate_created"])


if __name__ == "__main__":
    unittest.main()
