"""Signed evidence pagination and public projection, with a real isolated store."""
import copy
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shortdrama_mcp.audit_evidence_view import compact_audit_response, read_audit_evidence
from shortdrama_mcp.canonical import stable_json_hash
from shortdrama_mcp.models import ToolResponse
from shortdrama_mcp.storage import ProjectStore


class AuditEvidenceViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="audit-evidence-view-")
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name), "http://localhost:8765")
        self.service = SimpleNamespace(store=self.store)
        self.namespace, self.project = "local-single-user", "SBP-1234567890ab"
        self.artifact, self.certificate_id = "ART-1234567890abcdef", "CERT-1234567890abcdef"
        self.store.create_project(self.namespace, {"project_id": self.project}, {})
        self.checks = [{"obligation_id": "OBL-0", "judgement": "较长观察🌿" * 600, "status": "pass"}]
        self.checks += [{"obligation_id": f"OBL-{i}", "judgement": "逐项实际观察", "status": "pass"}
                       for i in range(1, 19)]
        self.report = {"semantic_audit": {"layers": [{"status": "pass"}],
            "coverage": {"contract": "semantic-obligation-coverage-v1", "checks": self.checks}},
            "issues": [{"error_code": "E-SAMPLE", "evidence": "must remain visible"}],
            "independent_evidence_matrix": {"unresolved": 1}}
        self.certificate = self.store.save_project_record(self.namespace, self.project,
            kind="certificates", record_id=self.certificate_id, value={
                "certificate_id": self.certificate_id, "artifact_id": self.artifact,
                "certificate_type": "independent_audit", "semantic_audit_contract": "historical-v8",
                "report": self.report, "report_sha256": stable_json_hash(self.report)})

    def read(self, **kwargs):
        return read_audit_evidence(self.service, self.namespace, self.project,
            kwargs.pop("artifact_id", self.artifact), self.certificate_id, **kwargs)

    def bytes(self):
        root = Path(self.temp.name)
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def certificate_path(self):
        return self.store.project_dir(self.namespace, self.project) / "certificates" / f"{self.certificate_id}.json"

    def test_compaction_changes_only_response_projection_and_has_exact_read_call(self):
        response = ToolResponse(status="fail_current", summary="audit complete with findings",
            project_id=self.project, artifact_id=self.artifact, data={
                "report": self.report, "certificate": {k: v for k, v in self.certificate.items() if k != "report"},
                "cache_hit": True})
        original, signed = copy.deepcopy(response.model_dump()), self.bytes()
        result = compact_audit_response(response)
        projection = result.data["report"]["semantic_audit"]["coverage"]["checks"]
        self.assertTrue(projection["omitted"])
        self.assertEqual(projection["count"], len(self.checks))
        self.assertEqual(projection["sha256"], stable_json_hash(self.checks))
        self.assertEqual(projection["read_call"]["section"], f"audit_evidence:{self.certificate_id}")
        self.assertEqual(projection["read_call"]["artifact_id"], self.artifact)
        self.assertEqual(result.data["report"]["issues"], self.report["issues"])
        self.assertEqual(result.data["report"]["independent_evidence_matrix"], self.report["independent_evidence_matrix"])
        self.assertEqual(response.model_dump(), original)
        self.assertEqual(self.bytes(), signed)
        self.assertEqual(result.status, "fail_current")
        self.assertLess(len(result.model_dump_json()), len(response.model_dump_json()))

    def test_public_mcp_compact_response_and_signed_read_route_match_service_hash(self):
        from shortdrama_mcp.app import create_mcp
        from shortdrama_mcp.service import _stable_json_bytes
        from test_tool_audit_diagnostics import settings_for
        self.assertEqual(self.certificate["report_sha256"],
                         hashlib.sha256(_stable_json_bytes(self.report)).hexdigest())
        server, service = create_mcp(settings_for(self.temp.name))
        response = ToolResponse(status="fail_current", summary="synthetic route fixture",
            project_id=self.project, artifact_id=self.artifact, data={"report": self.report,
                "certificate": {k: v for k, v in self.certificate.items() if k != "report"}})
        original = copy.deepcopy(response.model_dump())
        signed_bytes = self.certificate_path().read_bytes()
        with patch.object(service, "audit_storyboard", return_value=response):
            audited = asyncio.run(server.call_tool("audit_storyboard", {
                "project_id": self.project, "artifact_id": self.artifact}))
        compact = audited.structured_content["data"]["report"]["semantic_audit"]["coverage"]["checks"]
        self.assertTrue(compact["omitted"])
        read = asyncio.run(server.call_tool("read_project_artifact", compact["read_call"] | {"max_chars": 512}))
        page = read.structured_content["data"]
        self.assertEqual(page["certificate_id"], self.certificate_id)
        self.assertEqual(page["report_sha256"], self.certificate["report_sha256"])
        detail_call = page["checks"][0]["detail_call"]
        detail = asyncio.run(server.call_tool("read_project_artifact", detail_call)).structured_content["data"]
        self.assertEqual(detail["detail"]["offset"], 0)
        self.assertTrue(detail["page"]["has_more"])
        continued = asyncio.run(server.call_tool("read_project_artifact", detail["page"]["next_call"]))
        self.assertEqual(continued.structured_content["data"]["detail"]["offset"], 512)
        self.assertFalse(page["counts_as_complete"])
        self.assertEqual(self.certificate_path().read_bytes(), signed_bytes)
        self.assertEqual(response.model_dump(), original)

    def test_without_exact_certificate_identity_no_evidence_is_silently_dropped(self):
        for certificate in (None, {"certificate_id": self.certificate_id, "artifact_id": "ART-other"}):
            response = ToolResponse(status="blocked", summary="no certificate", project_id=self.project,
                artifact_id=self.artifact, data={"report": self.report, "certificate": certificate})
            self.assertEqual(compact_audit_response(response).model_dump(), response.model_dump())

    def test_all_pages_and_large_detail_reconstruct_exact_signed_checks_without_writes(self):
        before = self.bytes()
        seen, recovered, offset, first_detail = [], {}, 0, None
        for _ in range(30):
            response = self.read(offset=offset, limit=3, max_chars=512)
            data = response.data
            self.assertFalse(data["counts_as_complete"])
            self.assertEqual(data["current_release_eligibility"], "not_verified")
            self.assertTrue(data["certificate_signature_verified"])
            self.assertEqual(data["checks_sha256"], stable_json_hash(self.checks))
            self.assertLessEqual(len(json.dumps(data["checks"], ensure_ascii=False, separators=(",", ":"))), 512)
            for row in data["checks"]:
                index = row["check_index"]
                seen.append(index)
                if row.get("detail_required"):
                    first_detail = row["detail_call"]
                else:
                    recovered[index] = row["check"]
            if not data["page"]["has_more"]:
                break
            next_call = data["page"]["next_call"]
            self.assertEqual(next_call["project_id"], self.project)
            self.assertEqual(next_call["artifact_id"], self.artifact)
            self.assertGreater(next_call["offset"], offset)
            offset = next_call["offset"]
        else:
            self.fail("check pagination did not terminate")
        self.assertEqual(seen, list(range(len(self.checks))))
        self.assertIsNotNone(first_detail)
        detail_index = int(first_detail["section"].rsplit(":", 1)[1])
        chunks, offset = [], 0
        for _ in range(30):
            data = self.read(detail_index=detail_index, offset=offset, max_chars=512).data
            chunks.append(data["detail"]["text"])
            if not data["page"]["has_more"]:
                break
            next_call = data["page"]["next_call"]
            self.assertEqual(next_call["section"], first_detail["section"])
            self.assertGreater(next_call["offset"], offset)
            offset = next_call["offset"]
        else:
            self.fail("detail pagination did not terminate")
        recovered[detail_index] = json.loads("".join(chunks))
        self.assertEqual([recovered[i] for i in seen], self.checks)
        self.assertEqual(self.bytes(), before)

    def test_wrong_artifact_is_rejected_even_with_valid_signed_certificate(self):
        with self.assertRaisesRegex(ValueError, "artifact_binding_mismatch"):
            self.read(artifact_id="ART-ffffffffffffffff")

    def test_tampered_and_unsigned_certificates_are_rejected_without_repair(self):
        for mutation in ("signature", "content", "unsigned"):
            value = copy.deepcopy(self.certificate)
            if mutation == "signature":
                value["integrity_hmac"] = "0" * 64
            elif mutation == "content":
                value["report"]["issues"] = []
            else:
                value.pop("integrity_hmac")
            self.store.atomic_write_json(self.certificate_path(), value)
            before = self.bytes()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.read()
            self.assertEqual(self.bytes(), before)

    def test_validly_resigned_but_mismatched_report_hash_is_rejected(self):
        value = copy.deepcopy(self.certificate)
        value["report_sha256"] = "0" * 64
        self.store.save_project_record(self.namespace, self.project, kind="certificates",
            record_id=self.certificate_id, value=value)
        with self.assertRaisesRegex(ValueError, "report_hash_mismatch"):
            self.read()

    def test_historical_missing_coverage_stays_unknown_and_read_only(self):
        value = copy.deepcopy(self.certificate)
        value["report"]["semantic_audit"].pop("coverage")
        value["report_sha256"] = stable_json_hash(value["report"])
        self.store.save_project_record(self.namespace, self.project, kind="certificates",
            record_id=self.certificate_id, value=value)
        before = self.bytes()
        data = self.read().data
        self.assertFalse(data["coverage_available"])
        self.assertIsNone(data["check_count"])
        self.assertIsNone(data["checks_sha256"])
        self.assertFalse(data["counts_as_complete"])
        self.assertEqual(self.bytes(), before)

    def test_invalid_pagination_is_rejected_and_budget_is_capped(self):
        for kwargs in ({"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": True},
                       {"max_chars": 511}, {"detail_index": -1}, {"detail_index": 30},
                       {"detail_index": 0, "offset": 100000}, {"offset": 100000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.read(**kwargs)
        self.assertEqual(self.read(max_chars=100000).data["page"]["max_check_chars"], 16000)


if __name__ == "__main__":
    unittest.main()
