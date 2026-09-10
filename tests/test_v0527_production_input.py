"""Unified-input controls, written before the root's single scheduled run.

Public synthetic source_input deliberately lacks performance. The positive
complete_source_input adds only the pre-existing performance_fixture decisions;
neither fixture supplies an independent audit or a fabricated semantic PASS.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"),
               str(ROOT / "skills/shortdrama-storyboard/scripts")]

import author_storyboard
import production_input
from production_decisions import CONTRACT
from production_input import construct_production_input, inspect_production_input
from test_v0527_authoring_serializer import decisions as legacy_decisions
from test_v0527_production_decisions import action
from test_v0527_source_decision_independent_audit import source_input, complete_source_input


class ProductionInputTests(unittest.TestCase):
    def inspect(self, raw, graph):
        return inspect_production_input(
            raw, graph_bytes=graph,
            expected_graph_sha256=hashlib.sha256(graph).hexdigest() if graph is not None else None)

    def record(self, raw, graph):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        payload = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        source = root / "input.json"
        source.write_bytes(payload)
        graph_path = root / "graph.json"
        if graph is not None:
            graph_path.write_bytes(graph)
        result = author_storyboard.record_attempt(
            source, root / "attempts", attempt_id="input-control",
            source_graph_path=graph_path if graph is not None else None,
            source_graph_sha256=hashlib.sha256(graph).hexdigest() if graph is not None else None)
        return result, root / "attempts/input-control", payload

    def test_constructor_copies_without_inspecting_or_filling(self):
        raw, _, _ = source_input()
        before = copy.deepcopy(raw)
        with patch.object(production_input, "inspect_production_input",
                          side_effect=AssertionError("constructor must not pretest")):
            made = construct_production_input(project=raw["project"],
                                              initial_world=raw["initial_world"], shots=raw["shots"])
        self.assertEqual(made, raw)
        self.assertEqual(made["contract"], CONTRACT)
        made["shots"][0]["actions"].clear()
        self.assertEqual(raw, before)
        self.assertNotIn("performance_state", raw["initial_world"]["actors"]["甲"])

    def test_complete_existing_performance_input_is_ready_but_not_semantically_approved(self):
        raw, graph, _ = complete_source_input()
        before = copy.deepcopy(raw)
        report = self.inspect(raw, graph)
        self.assertTrue(report["ready_for_canonical"], report["issues"])
        self.assertEqual(report["issues"], [])
        self.assertFalse(report["canonical_created"])
        self.assertFalse(report["commit_eligible"])
        self.assertIsNone(report["semantic_accuracy_percent"])
        self.assertEqual(report["semantic_status"], "pending_independent_review")
        self.assertTrue(any("independent" in row["check"] for row in report["not_checked"]))
        self.assertNotIn("lowered", report)
        self.assertNotIn("shots", report)
        json.dumps(report)
        self.assertEqual(raw, before)

    def test_project_nonempty_and_missing_requirements_collected_before_assembly(self):
        raw, graph, _ = complete_source_input()
        evidence = raw["project"]["prevention_evidence"]
        evidence["cognition_matrix"] = []
        del evidence["change_lock"]
        report = self.inspect(raw, graph)
        self.assertFalse(report["ready_for_canonical"])
        schema_issues = [row for row in report["issues"] if row["code"] == "project_schema"]
        self.assertTrue(any("cognition_matrix" in row["path"] for row in schema_issues))
        self.assertTrue(any("change_lock" in row["message"] for row in schema_issues))
        self.assertTrue(any(row["check"] == "shots[0].camera" and row["status"] == "pass"
                            for row in report["checks"]))
        self.assertTrue(any(row["check"] == "shots[0].required_performance"
                            for row in report["checks"]))

    def test_schema_requirements_are_read_from_the_real_schema_not_a_second_list(self):
        raw, graph, _ = complete_source_input()
        schema = json.loads(production_input.SCHEMA_PATH.read_bytes())
        schema["properties"]["prevention_evidence"]["properties"]["cognition_matrix"]["minItems"] = 2
        with tempfile.TemporaryDirectory() as directory:
            schema_path = Path(directory) / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            with patch.object(production_input, "SCHEMA_PATH", schema_path):
                report = self.inspect(raw, graph)
        self.assertTrue(any(row["code"] == "project_schema" and "cognition_matrix" in row["path"]
                            for row in report["issues"]))

    def test_missing_graph_defers_paper_and_performance_but_checks_independent_camera(self):
        raw, _, _ = complete_source_input()
        raw["shots"][0]["camera"]["hfov_deg"] = 0
        report = self.inspect(raw, None)
        self.assertFalse(report["ready_for_canonical"])
        self.assertTrue(any(row["code"] == "source_graph_unavailable" for row in report["issues"]))
        self.assertTrue(any("camera" in row["path"] for row in report["issues"]))
        self.assertFalse(any("unknown compiled paper" in row["message"] for row in report["issues"]))
        pending = {row["check"] for row in report["not_checked"]}
        self.assertIn("source_references", pending)
        self.assertIn("required_performance", pending)

    def test_source_mode_and_dependency_errors_use_existing_compiler(self):
        raw, graph, _ = complete_source_input()
        model = json.loads(graph)
        dependency = copy.deepcopy(model["facts"][0])
        dependency.update(id="CONDITION", event_id="OTHER", required=True)
        model["facts"].append(dependency)
        model["facts"][0]["depends_on"] = ["CONDITION"]
        raw["project"]["prevention_evidence"]["source_decisions"]["uses"][0]["wording"] = "authored"
        graph = json.dumps(model, ensure_ascii=False).encode("utf-8")
        report = self.inspect(raw, graph)
        codes = {row["code"] for row in report["issues"]}
        self.assertIn("wording_mode", codes)
        self.assertIn("use_dependency_missing", codes)
        self.assertIn("required_fact_unrepresented", codes)
        self.assertTrue(any(row.get("global_issue") for row in report["issues"]))

    def test_real_move_requires_prior_hand_contact_not_just_reach_radius(self):
        raw, graph, _ = complete_source_input()
        raw["initial_world"]["points"]["案左"]["position_xyz"] = [0.6, 0.3, 1.0]
        # This control isolates hand contact. The source-inscribed paper stays
        # protected; a plain staging object uses the identical tested anchor.
        raw["initial_world"]["props"]["无字试件"] = {
            "anchor": {"point": "案左"}, "written_ref": None}
        raw["shots"][0]["actions"] = [
            action("move_prop", prop="无字试件", hand="right", to="案右")]
        report = self.inspect(raw, graph)
        self.assertFalse(report["ready_for_canonical"])
        self.assertTrue(any("hand must already reach" in row["message"] for row in report["issues"]),
                        report["issues"])
        self.assertTrue(any(row["check"] == "required_performance" for row in report["not_checked"]))

    def test_visible_missing_performance_remains_blocked_without_automatic_actions(self):
        raw, graph, _ = source_input()
        before = copy.deepcopy(raw)
        report = self.inspect(raw, graph)
        self.assertFalse(report["ready_for_canonical"])
        messages = [row["message"] for row in report["issues"] if row["code"] == "E-EXPRESSION-003"]
        self.assertTrue(any("眼睑" in message for message in messages))
        self.assertTrue(any("眨眼" in message for message in messages))
        self.assertEqual(raw, before)

    def test_legitimate_hidden_eyes_do_not_require_visible_eye_actions(self):
        raw, graph, _ = source_input()
        raw["shots"][0]["camera"]["position_xyz"] = [0, -4, 1.5]
        report = self.inspect(raw, graph)
        self.assertTrue(report["ready_for_canonical"], report["issues"])
        performance = next(row for row in report["checks"]
                           if row["check"] == "shots[0].required_performance")
        self.assertEqual(performance["status"], "pass")
        self.assertFalse(any(performance["visibility"].values()))
        self.assertFalse(any(value == "visible_window" for value in performance["eye_applicability"].values()))

    def test_unknown_initial_tension_is_not_replaced_with_invented_before_state(self):
        raw, graph, _ = complete_source_input()
        del raw["initial_world"]["actors"]["甲"]["performance_state"]
        before = copy.deepcopy(raw)
        report = self.inspect(raw, graph)
        self.assertTrue(report["ready_for_canonical"], report["issues"])
        self.assertEqual(raw, before)
        result, folder, original = self.record(raw, graph)
        self.assertEqual(result["status"], "rendered_pending_preflight", result.get("findings"))
        self.assertEqual((folder / "input.original.json").read_bytes(), original)
        canonical = json.loads((folder / "canonical.json").read_bytes())
        self.assertNotIn("由紧转松", canonical["shots"][0]["画面内容"])
        self.assertEqual(canonical["prevention_evidence"]["scene_state_ledger"][0]
                         ["start_state"]["动作进度"]["表演状态"]["甲"]["eyelid"], "unknown")

    def test_rejected_complete_record_preserves_raw_and_never_assembles_canonical(self):
        raw, graph, _ = source_input()
        raw["project"]["prevention_evidence"]["cognition_matrix"] = []
        with patch.object(author_storyboard, "_render_decisions",
                          side_effect=AssertionError("blocked input must not assemble canonical")):
            result, folder, original = self.record(raw, graph)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual((folder / "input.original.json").read_bytes(), original)
        self.assertFalse((folder / "canonical.json").exists())
        report = json.loads((folder / "check_coverage.json").read_bytes())
        codes = {row["code"] for row in report["issues"]}
        self.assertIn("project_schema", codes)
        self.assertIn("E-EXPRESSION-003", codes)
        self.assertIsNone(result["semantic_accuracy_percent"])

    def test_legacy_typed_record_keeps_its_original_entry(self):
        with patch.object(production_input, "_inspect_with_intermediates",
                          side_effect=AssertionError("legacy typed must not enter production input")):
            result, _, _ = self.record(legacy_decisions(), None)
        self.assertEqual(result["status"], "rendered_pending_preflight", result.get("findings"))


if __name__ == "__main__":
    unittest.main()
