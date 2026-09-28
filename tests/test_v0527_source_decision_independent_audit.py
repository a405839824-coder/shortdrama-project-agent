"""Synthetic source obligations through the real independent-audit consumer.

The minimal machine report isolates the evidence attachment layer. It is not a
machine audit, a service certificate, or a claim that an episode passed. The
derivation inspector and source/action compilers are never mocked.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "server", ROOT / "skills/shortdrama-storyboard/scripts", ROOT / "tests"):
    sys.path.insert(0, str(directory))

from author_storyboard import _render_decisions, record_attempt
from production_decisions import lower_production_decisions
from production_derivation import attach_derivation, inspect_derivation
from source_decisions import compile_source_decisions
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES
from shortdrama_mcp.service import StoryboardService, canonical_storyboard_sha256, _stable_json_bytes
from shortdrama_mcp.audit_coverage import build_obligations, coverage_context
from semantic_audit_helpers import synthetic_coverage
from test_v0527_production_decisions import action, production_fixture, performance_fixture
import test_v0527_source_decisions as source_controls


def source_input(mode="paper"):
    if mode == "narration":
        graph, uses, texts = source_controls.fixture()
        uses["uses"][0].update(mode="objective_narration", wording="authored", actor=None,
                                text="商议安排在明日。")
    else:
        graph, uses, texts = source_controls.SourceDecisionTests().paper()
        uses["written_objects"][0]["carrier_id"] = "信纸"
    data = production_fixture()
    evidence = data["project"]["prevention_evidence"]
    evidence["source_decisions"] = uses
    evidence["source_ledger"] = [
        {"source_id": sid, "source_text": text,
         "target_shots": [data["shots"][0]["shot_id"]], "treatment": "保留",
         "authorization": "合成来源审计控制"} for sid, text in texts.items()
    ]
    if mode == "narration":
        data["shots"][0]["actions"] = [action("narrate", source_use_id="U1")]
    else:
        data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
    data["shots"][0]["source_unit_ids"] = list(texts)
    data["shots"][0]["editorial"]["原文支撑句"] = next(iter(texts.values()))
    raw_graph = json.dumps(graph, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return data, raw_graph, texts


def complete_source_input(mode="paper"):
    """Explicit full input for production recording, not consumer-only tests.

    Source bytes/uses are unchanged. The visible actor's authored performance
    comes from the existing public performance_fixture whose real eye/timing
    controls predate this entry. No source truth or formal evidence is added.
    The original source_input intentionally remains incomplete for regression.
    """
    data, graph, texts = source_input(mode)
    performance = performance_fixture()
    data["initial_world"]["points"]["观察点"] = copy.deepcopy(
        performance["initial_world"]["points"]["观察点"])
    for key in ("gaze", "performance_state"):
        data["initial_world"]["actors"]["甲"][key] = copy.deepcopy(
            performance["initial_world"]["actors"]["甲"][key])
    data["shots"][0]["actions"].extend(copy.deepcopy(performance["shots"][0]["actions"]))
    return data, graph, texts


def bound_fixture(mode="paper"):
    data, graph, texts = source_input(mode)
    compiled = compile_source_decisions(
        graph, data["project"]["prevention_evidence"]["source_decisions"],
        source_texts=texts, expected_graph_sha256=hashlib.sha256(graph).hexdigest(),
    )
    lowered = lower_production_decisions(data, source_compilation=compiled)
    canonical = _render_decisions(lowered["typed_decisions"])
    return attach_derivation(
        canonical, data, graph, production=lowered, source_compilation=compiled,
    )


def submission_for(storyboard, requirements):
    identity = canonical_storyboard_sha256(storyboard)
    shots = storyboard["shots"]
    pairs = [f"{a['镜头编号']}->{b['镜头编号']}" for a, b in zip(shots, shots[1:])]
    layers = []
    for layer in SEMANTIC_AUDIT_LAYER_NAMES:
        refs = [f"canonical:{identity}"]
        if layer == "空间连续":
            refs.extend(f"pair:{pair}" for pair in pairs)
        if layer == "禁项与声音":
            refs.extend(f"field:{s['镜头编号']}.音色" for s in shots if "音色" in s)
        if layer == "机位可见性":
            refs.extend(f"field:{s['镜头编号']}.{f}" for s in shots
                        for f in ("光影", "光源来源", "光源方向", "色温倾向"))
        if layer == "源文对白":
            refs.extend(row["evidence_id"] for row in requirements)
            refs.extend(dict.fromkeys(ref for row in requirements for ref in row["field_evidence_ids"]))
        layers.append({"layer": layer, "status": "pass",
                       "summary": "合成样例的独立审查输入；非生产签证。", "evidence_ids": refs})
    return {"canonical_sha256": identity, "reviewed_shot_count": len(shots),
            "reviewed_batch_count": 0, "reviewed_adjacent_pair_count": len(pairs),
            "adjacent_pair_order_sha256": hashlib.sha256(_stable_json_bytes(pairs)).hexdigest(),
            "layers": layers, "issues": [],
            "conclusion": {"status": "pass", "summary": "本合成控制用于核验独审证据消费。"}}


class SourceDecisionIndependentAuditTests(unittest.TestCase):
    def setUp(self):
        self.storyboard = bound_fixture()
        self.proof = inspect_derivation(self.storyboard)
        self.assertTrue(self.proof["valid"], self.proof["faults"])
        self.submission = submission_for(self.storyboard, self.proof["source_obligations"])
        self.report = {"status": "pass", "issues": [], "quality_metrics": {
            "adjacent_continuity": {"expected_pair_count": 0, "reviewed_pair_count": 0,
                                   "ordered_pair_sha256": self.submission["adjacent_pair_order_sha256"],
                                   "pair_ids": [], "failed_pair_count": 0, "proof_verified": True}}}
        self.service = object.__new__(StoryboardService)
        self.service._validator_fingerprint = "a" * 64
        self.service._ruleset_fingerprint = "b" * 64
        self.service._runtime_fingerprint = "c" * 64
        self.service._schema_fingerprint = "d" * 64

    def source_layer(self):
        return next(row for row in self.submission["layers"] if row["layer"] == "源文对白")

    def refresh_identity(self):
        identity = canonical_storyboard_sha256(self.storyboard)
        self.submission["canonical_sha256"] = identity
        for layer in self.submission["layers"]:
            layer["evidence_ids"] = [f"canonical:{identity}" if ref.startswith("canonical:") else ref
                                     for ref in layer["evidence_ids"]]

    def attach(self):
        # Current test-only coverage accompanies the independently constructed
        # source-decision evidence; it cannot replace missing source references.
        submitted = copy.deepcopy(self.submission)
        submitted["coverage"] = synthetic_coverage(
            build_obligations(self.storyboard, coverage_context(self.service))
        )
        return self.service._attach_independent_evidence_matrix(
            self.report, self.storyboard, submitted,
        )

    def test_current_source_obligations_close_only_in_enriched_report(self):
        original = copy.deepcopy(self.storyboard)
        result = self.attach()
        coverage = result["independent_evidence_matrix"]["source_decision_coverage"]
        self.assertEqual(coverage["state"], "verified_source_uses")
        self.assertEqual(coverage["expected_count"], 1)
        self.assertEqual(coverage["reviewed_count"], 1)
        self.assertEqual(coverage["canonical_sha256"], self.submission["canonical_sha256"])
        self.assertEqual(coverage["semantic_audit_sha256"], result["semantic_audit_sha256"])
        self.assertFalse(coverage["unprovided_content_certified"])
        self.assertEqual(self.storyboard, original)
        binding = self.storyboard["prevention_evidence"]["production_derivation"]
        self.assertEqual(binding["source_compilation"]["semantic_status"], "pending_independent_review")
        self.assertEqual(binding["independent_audit_status"], "not_performed")
        self.assertNotIn("certificate_id", result)

    def test_missing_obligation_remains_pending(self):
        layer = self.source_layer()
        layer["evidence_ids"] = [ref for ref in layer["evidence_ids"] if not ref.startswith("source-decision:")]
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_pending"):
            self.attach()

    def test_stale_or_unrelated_obligation_cannot_replace_current_id(self):
        layer = self.source_layer()
        layer["evidence_ids"] = ["source-decision:" + "0" * 64 if ref.startswith("source-decision:") else ref
                                 for ref in layer["evidence_ids"]]
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_binding_mismatch"):
            self.attach()

    def test_source_obligation_cannot_be_moved_to_another_layer(self):
        target = next(row for row in self.submission["layers"] if row["layer"] == "统计闭合")
        target["evidence_ids"].append(self.proof["source_obligations"][0]["evidence_id"])
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_layer_mismatch"):
            self.attach()

    def test_missing_actual_current_paper_field_remains_pending(self):
        layer = self.source_layer()
        layer["evidence_ids"] = [ref for ref in layer["evidence_ids"] if not ref.endswith(".起始状态")]
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_pending"):
            self.attach()

    def test_actual_objective_narration_requires_its_owning_voiceover_field(self):
        self.storyboard = bound_fixture("narration")
        proof = inspect_derivation(self.storyboard)
        self.assertTrue(proof["valid"], proof["faults"])
        fields = proof["source_obligations"][0]["field_evidence_ids"]
        self.assertEqual(set(fields), {"field:EP90-001.心理音轨"})
        self.assertIn("商议安排在明日。", self.storyboard["shots"][0]["心理音轨"])
        self.assertNotIn("商议安排在明日。", self.storyboard["shots"][0]["画面内容"])
        self.assertNotIn("商议安排在明日。", self.storyboard["shots"][0]["起始状态"])
        self.submission = submission_for(self.storyboard, proof["source_obligations"])
        self.assertEqual(self.attach()["independent_evidence_matrix"]["source_decision_coverage"]["state"],
                         "verified_source_uses")
        self.source_layer()["evidence_ids"].remove("field:EP90-001.心理音轨")
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_pending"):
            self.attach()

    def test_current_ids_do_not_replace_existing_source_unit_evidence(self):
        # Review attachments may be added without rewriting generated fields.
        self.storyboard["prevention_evidence"]["dialogue_adaptation_audit"] = {
            "entries": [{"source_unit_id": "DOC", "scene_semantics": "非会话叙述", "treatment": "可视动作"}]}
        self.refresh_identity()
        with self.assertRaisesRegex(ValueError, "semantic_audit_conversation_scene_coverage_incomplete"):
            self.attach()
        self.source_layer()["evidence_ids"].append("source-unit:DOC")
        self.assertEqual(self.attach()["independent_evidence_matrix"]["source_decision_coverage"]["state"],
                         "verified_source_uses")

    def test_existing_machine_source_issue_is_never_cleared_by_new_ids(self):
        issue = {"error_code": "E-ADAPTED-DIALOGUE-001", "severity": "fail", "blocking": True,
                 "shot_ids": ["EP90-001"], "field": "画面内容", "message": "合成控制：原改编证据仍缺失。"}
        self.report["issues"] = [issue]
        result = self.attach()
        self.assertEqual(result["issues"], [issue])
        self.assertEqual(result["status"], "fail_current")
        self.assertEqual(result["independent_evidence_matrix"]["source_decision_coverage"]["state"], "fail_current")

    def test_author_pass_metadata_cannot_close_independent_review(self):
        self.storyboard["prevention_evidence"]["production_derivation"]["independent_audit_status"] = "pass"
        self.refresh_identity()
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_derivation_invalid"):
            self.attach()

    def test_actual_paper_edit_invalidates_old_derivation_with_new_canonical_id(self):
        self.storyboard["shots"][0]["起始状态"] += "纸上另有第二条件。"
        self.refresh_identity()
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_derivation_invalid"):
            self.attach()

    def test_missing_derivation_cannot_fall_back_when_required_marker_remains(self):
        del self.storyboard["prevention_evidence"]["production_derivation"]
        self.refresh_identity()
        with self.assertRaisesRegex(ValueError, "semantic_audit_source_decision_derivation_invalid"):
            self.attach()

    def test_legacy_canonical_has_no_new_source_obligation_requirement(self):
        from test_v0519_adapted_dialogue import AdaptedDialogueIndependentAuditTests
        legacy = AdaptedDialogueIndependentAuditTests()
        legacy.setUp()
        result = legacy.attach()
        self.assertNotIn("source_decision_coverage", result["independent_evidence_matrix"])
        self.assertEqual(result["status"], "pass")

    def test_real_record_attempt_emits_pending_binding_consumable_by_independent_audit(self):
        data, graph, _ = complete_source_input()
        with tempfile.TemporaryDirectory(prefix="source-decision-record-") as temporary:
            base = Path(os.environ.get("SOURCE_DECISION_TEST_ARTIFACTS") or temporary)
            base.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            path, graph_path = base / "input.json", base / "graph.json"
            path.write_bytes(raw)
            graph_path.write_bytes(graph)
            attempt = record_attempt(path, base / "attempts", source_graph_path=graph_path,
                                     source_graph_sha256=hashlib.sha256(graph).hexdigest())
            self.assertEqual(attempt["status"], "rendered_pending_preflight", attempt.get("findings"))
            recorded = Path(attempt["report_path"]).parent
            self.assertEqual((recorded / "input.original.json").read_bytes(), raw)
            self.storyboard = json.loads(Path(attempt["canonical_path"]).read_text(encoding="utf-8"))
            proof = inspect_derivation(self.storyboard)
            self.assertTrue(proof["valid"], proof["faults"])
            self.submission = submission_for(self.storyboard, proof["source_obligations"])
            result = self.attach()
            self.assertEqual(result["independent_evidence_matrix"]["source_decision_coverage"]["state"],
                             "verified_source_uses")
            self.assertFalse(attempt["commit_eligible"])
            self.assertIsNone(attempt["semantic_accuracy_percent"])


if __name__ == "__main__":
    unittest.main()
