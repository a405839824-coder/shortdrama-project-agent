"""Synthetic software cases; no user workbook or content acceptance is used."""
import copy
import hashlib
import io
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from shortdrama_mcp.canonical import stable_json_hash
from shortdrama_mcp.file_io import extract_xlsx
from shortdrama_mcp.service import StoryboardService
from semantic_audit_helpers import semantic_audit_for_artifact
from test_efficiency import valid_storyboard
from test_v0556_audit_intake_coverage import workbook_bytes
from continuity_checks import audit_adjacent_text
import validate_storyboard


def unplanned_project():
    base = copy.deepcopy(valid_storyboard()["shots"][0])
    shots = []
    for index in range(1, 4):
        shot = copy.deepcopy(base)
        shot.update({"镜头编号": f"EP01-{index:03}", "累计时长": index * 4,
                     "可见主体": "人物：陈宁", "起始状态": "陈宁站着。",
                     "画面内容": "陈宁看向桌面。", "场景连续性ID": "ROOM-A"})
        shots.append(shot)
    return {"project_version": "0.5.1", "schema_version": "R10.1", "shots": shots}


class AuditIntakePairProofTests(unittest.TestCase):
    def test_no_profile_or_source_ledger_still_checks_every_actual_pair(self):
        project = unplanned_project()
        before = copy.deepcopy(project)
        report = validate_storyboard.validate_project(project, mode="audit")
        proof = report["quality_metrics"]["adjacent_continuity"]
        ids = ["EP01-001->EP01-002", "EP01-002->EP01-003"]
        self.assertEqual(proof["pair_ids"], ids)
        self.assertEqual(proof["ordered_pair_sha256"], stable_json_hash(ids))
        self.assertEqual(proof["reviewed_pair_count"], 2)
        self.assertEqual(proof["unknown_pair_count"], 2)
        self.assertFalse(proof["proof_verified"])
        self.assertTrue(proof["machine_review_complete"])
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any(row["error_code"] == "E-VERSION-001" for row in report["issues"]))
        self.assertEqual(project, before)
        for pair in proof["pair_results"]:
            for reference, value in pair["actual_fields"].items():
                self.assertEqual(pair["field_sha256"][reference],
                                 hashlib.sha256(str(value or "").encode()).hexdigest())
        semantic = [row for row in report["quality_metrics"]["semantic_checks"]
                    if row["check_id"] == "adjacent_text_continuity"]
        self.assertEqual([row["status"] for row in semantic], ["unknown", "unknown"])
        current = copy.deepcopy(project)
        current["project_version"] = validate_storyboard.VERSION
        current_report = validate_storyboard.validate_project(current)
        self.assertEqual(current_report["quality_metrics"]["adjacent_continuity"]["pair_ids"], ids)
        self.assertEqual(current_report["quality_metrics"]["adjacent_continuity"]["unknown_pair_count"], 2)
        self.assertFalse(any(row["location"] == "project_version" for row in current_report["issues"]))

    def test_explicit_terminal_conflict_is_real_failure_with_two_shot_binding(self):
        project = unplanned_project()
        project["shots"][0]["画面内容"] = "陈宁走到椅边。镜末：陈宁坐着。"
        report = validate_storyboard.validate_project(project)
        proof = report["quality_metrics"]["adjacent_continuity"]
        self.assertEqual(proof["pair_results"][0]["continuity_status"], "fail")
        self.assertEqual(proof["pair_results"][1]["continuity_status"], "unknown")
        issue = next(row for row in report["issues"] if row.get("check_id") == "adjacent_text_continuity")
        self.assertEqual(issue["shot_ids"], ["EP01-001", "EP01-002"])
        self.assertIn("陈宁", issue["evidence"])

    def test_matching_explicit_endpoint_is_observation_not_proof(self):
        project = unplanned_project()
        project["shots"][0]["画面内容"] = "镜末：陈宁站着。"
        proof, _ = audit_adjacent_text(project, project["shots"])
        self.assertEqual(proof["pair_results"][0]["continuity_status"], "unknown")
        self.assertFalse(proof["proof_verified"])
        self.assertEqual(proof["unknown_pair_count"], 2)
        changed = copy.deepcopy(project)
        changed["shots"][0]["画面内容"] += "窗外风停。"
        other, _ = audit_adjacent_text(changed, changed["shots"])
        ref = "field:EP01-001.画面内容"
        self.assertNotEqual(proof["pair_results"][0]["field_sha256"][ref], other["pair_results"][0]["field_sha256"][ref])
        for text in ("陈宁姿态无法确定。", "陈宁状态待审。"):
            project["shots"][0]["画面内容"] = "镜末：" + text
            project["shots"][1]["起始状态"] = text
            same, _ = audit_adjacent_text(project, project["shots"])
            self.assertEqual(same["pair_results"][0]["continuity_status"], "unknown")

    def test_prior_actions_quotes_conditionals_and_cross_scene_cannot_prove_a_cut(self):
        for picture in ("陈宁坐着，随后站起。", "陈宁说道：“镜末：陈宁坐着。”",
                        "镜末：如果有人进门，陈宁坐着。"):
            with self.subTest(picture=picture):
                project = unplanned_project()
                project["shots"][0]["画面内容"] = picture
                proof, _ = audit_adjacent_text(project, project["shots"])
                self.assertEqual(proof["pair_results"][0]["continuity_status"], "unknown")
        project["shots"][0]["画面内容"] = "镜末：陈宁坐着。"
        project["shots"][1]["场景连续性ID"] = "ROOM-B"
        proof, _ = audit_adjacent_text(project, project["shots"])
        self.assertEqual(proof["pair_results"][0]["continuity_status"], "unknown")

    def test_author_manifest_cannot_supply_missing_machine_observations(self):
        project = unplanned_project()
        project["prevention_evidence"] = {"adjacent_continuity_audit": {
            "pair_ids": ["FAKE"], "proof_verified": True, "pair_results": [{"continuity_status": "pass"}]}}
        report = validate_storyboard.validate_project(project)
        proof = report["quality_metrics"]["adjacent_continuity"]
        self.assertNotIn("FAKE", proof["pair_ids"])
        self.assertEqual(proof["unknown_pair_count"], 2)
        self.assertFalse(proof["proof_verified"])
        for evidence in (None, "invalid", []):
            project["prevention_evidence"] = evidence
            self.assertEqual(validate_storyboard.validate_project(project)["quality_metrics"]["adjacent_continuity"]["expected_pair_count"], 2)

    def test_existing_production_state_proof_is_retained(self):
        report = validate_storyboard.validate_project(valid_storyboard())
        proof = report["quality_metrics"]["adjacent_continuity"]
        self.assertNotIn("machine_review_contract", proof)
        self.assertTrue(proof["proof_verified"])

    def test_two_shot_external_bytes_reach_fail_unknown_report_without_production(self):
        book = load_workbook(io.BytesIO(workbook_bytes()))
        master = book["正式分镜母版"]
        values = [cell.value for cell in master[2]]
        values[0], values[2] = "EP10-002", 8
        master.append(values)
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        raw = stream.getvalue()
        candidate = extract_xlsx(raw, audit_only=True)["canonical_storyboard"]
        before = copy.deepcopy(candidate)
        service = StoryboardService.__new__(StoryboardService)
        for name in ("ruleset", "validator", "runtime", "schema"):
            setattr(service, "_" + name + "_fingerprint", "e" * 64)
        with patch.object(service, "_storyboard_from_artifact", return_value=(candidate, {})), \
                patch("semantic_audit_helpers.request_namespace", return_value="test"):
            submission = semantic_audit_for_artifact(service, None, "P", "A")
        # Independent inputs are explicitly synthetic and unknown, not fabricated PASS.
        for check in submission["coverage"]["checks"]:
            check["status"] = "unknown"
            check["judgement"] = "合成软件回归：完整绑定实际字段，内容语义未知，不声明真实分镜通过。"
        report = validate_storyboard.validate_project(candidate, mode="audit")
        attached = service._attach_independent_evidence_matrix(report, candidate, submission)
        self.assertEqual(attached["status"], "fail_current")
        self.assertEqual(attached["quality_metrics"]["adjacent_continuity"]["unknown_pair_count"], 1)
        self.assertEqual(attached["independent_evidence_matrix"]["audit_layer_results"]["空间连续"]["status"], "unknown")
        self.assertEqual(candidate, before)
        self.assertEqual(candidate["audit_workbook_evidence"]["raw_sha256"], hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
