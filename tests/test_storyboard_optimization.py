"""Regression checks for shared parsing and reproducible generation evidence."""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]

import production_derivation as derivation
import repair_observations as repair
from voice_sequence import extract_dialogues


class StoryboardOptimizationTests(unittest.TestCase):
    def test_state_history_keeps_former_identities_without_reparsing_every_snapshot(self):
        from production_state import ProductionStateChain, ProductionStateError, StateSnapshot
        from test_v0527_production_state import state

        original = state()
        decode = StateSnapshot.to_dict
        decodes = []

        def export(snapshot):
            decodes.append(snapshot.label)
            return decode(snapshot)

        with patch.object(StateSnapshot, "to_dict", export):
            chain = ProductionStateChain(original, label="first", reason="explicit state")
            later = copy.deepcopy(original)
            del later["人物位置"]["乙"]
            del later["视线落点"]["乙"]
            later["视线落点"]["甲"] = {"kind": "point", "name": "案角", "status": "resolved",
                                      "scene_id": "ROOM", "position_xy": [0, 1]}
            for number in range(10):
                chain.advance(later, label=f"shot-{number}", reason="explicit unchanged state")
            self.assertLessEqual(len(decodes), 12)
        saved = chain.history
        later["视线落点"]["甲"]["name"] = "乙"
        with self.assertRaisesRegex(ProductionStateError, "relabeled"):
            chain.advance(later, label="rejected", reason="cannot turn former actor into a point")
        self.assertEqual(chain.history, saved)
        later["视线落点"]["甲"]["name"] = "新锚点"
        chain.advance(later, label="rejected", reason="failed label was not committed")
        self.assertEqual(chain.history[0].to_dict(), original)

    def test_source_obligations_share_plan_computation_without_mutable_aliases(self):
        from dataclasses import replace
        import json
        import production_plan
        from test_v0527_source_decisions import build, fixture

        compilation = build(*fixture())
        plan = {"shots": [{"shot_id": "A", "purpose_fact_ids": ["F1"]}], "narrative": {"segments": []}}
        compilation = replace(compilation, production_plan_json=json.dumps(plan).encode(),
                              uses=tuple(replace(compilation.uses[0], id=f"U{i}") for i in range(3)))
        with patch.object(production_plan, "digest", wraps=production_plan.digest) as digest:
            first = compilation.to_dict()
        self.assertEqual(digest.call_count, 2)
        for obligation in first["review_obligations"]:
            review = obligation["binding"]["production_plan_review"]
            self.assertEqual(review["plan_sha256"], production_plan.digest(plan))
            self.assertEqual(review["scene_narrative_review"]["plan_sha256"], production_plan.digest(plan["narrative"]))
            self.assertEqual(review["related_shots"], ["A"])
        first["production_plan"]["shots"].clear()
        first["review_obligations"][0]["binding"]["production_plan_review"]["required_questions"].clear()
        self.assertTrue(first["review_obligations"][1]["binding"]["production_plan_review"]["required_questions"])
        self.assertEqual(compilation.to_dict()["production_plan"], plan)

    def test_mixed_dialogue_syntax_preserves_actual_order_and_repetitions(self):
        text = '【甲】“先走。”；乙说道：“等等！”；【甲】“先走。”；乙说道：“知道了。”'
        self.assertEqual(extract_dialogues(text), [
            ("甲", "先走。"), ("乙", "等等！"), ("甲", "先走。"), ("乙", "知道了。")])

    def test_dependency_bytes_are_read_once_and_missing_required_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "skills/storyboard/scripts"
            scripts.mkdir(parents=True)
            required = scripts / "required.py"
            helper = scripts / "helper.py"
            required.write_bytes(b"required")
            helper.write_bytes(b"helper")
            reads = []
            original = Path.read_bytes

            def read(path):
                reads.append(path)
                return original(path)

            with patch.object(derivation, "SCRIPT_DIR", scripts), \
                    patch.object(derivation, "DEPENDENCIES", (required.name,)), \
                    patch.object(Path, "read_bytes", read):
                self.assertEqual(derivation.compiler_fingerprints(), {
                    "required.py": hashlib.sha256(b"required").hexdigest(),
                    "helper.py": hashlib.sha256(b"helper").hexdigest()})
                self.assertEqual(reads.count(required), 1)
                self.assertEqual(reads.count(helper), 1)
                required.unlink()
                with self.assertRaises(FileNotFoundError):
                    derivation.compiler_fingerprints()

    def test_repair_checker_detects_same_size_same_timestamp_code_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "runtime.py"
            source.write_bytes(b"value = 1\n")
            before = source.stat()
            with patch.object(repair, "__file__", str(Path(directory) / "repair_observations.py")):
                fingerprint = repair.checker_digest({})
                source.write_bytes(b"value = 2\n")
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertNotEqual(repair.checker_digest({}), fingerprint)

    def test_source_inspection_compiles_once_and_keeps_every_source_error(self):
        import production_input
        import source_decisions
        from test_v0527_source_decision_independent_audit import complete_source_input

        raw, graph, _ = complete_source_input()
        fingerprint = hashlib.sha256(graph).hexdigest()
        with patch.object(source_decisions, "_inspect", wraps=source_decisions._inspect) as inspect:
            _, compilation, report = production_input._inspect_with_intermediates(
                raw, graph_bytes=graph, expected_graph_sha256=fingerprint)
        self.assertEqual(inspect.call_count, 1)
        self.assertIsNotNone(compilation, report["issues"])
        self.assertFalse(report["commit_eligible"])

        bad = copy.deepcopy(raw)
        bad["project"]["prevention_evidence"]["source_decisions"]["uses"][0]["wording"] = "authored"
        expected = source_decisions.validate_source_decisions(
            graph, bad["project"]["prevention_evidence"]["source_decisions"],
            source_texts={r["source_id"]: r["source_text"] for r in bad["project"]["prevention_evidence"]["source_ledger"]},
            expected_graph_sha256=fingerprint)
        _, compilation, report = production_input._inspect_with_intermediates(
            bad, graph_bytes=graph, expected_graph_sha256=fingerprint)
        self.assertIsNone(compilation)
        actual = [r for r in report["issues"] if r["stage"] == "source_compilation"]
        self.assertEqual({r["code"] for r in actual}, {r.code for r in expected})

    def test_generation_binding_and_warm_replay_remain_isolated_and_tamper_evident(self):
        from test_v0527_source_decision_independent_audit import complete_source_input
        import author_storyboard
        import json

        raw, graph, _ = complete_source_input()
        derivation._replay_compilation.cache_clear()
        self.addCleanup(derivation._replay_compilation.cache_clear)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "input.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            (folder / "graph.json").write_bytes(graph)
            with patch.object(derivation, "_obligations", wraps=derivation._obligations) as obligations:
                record = author_storyboard.record_attempt(
                    folder / "input.json", folder / "attempts", source_graph_path=folder / "graph.json")
                self.assertEqual(record["status"], "rendered_pending_preflight", record)
                self.assertEqual(obligations.call_count, 1)
            candidate = json.loads(Path(record["canonical_path"]).read_text(encoding="utf-8"))
        evidence = candidate["prevention_evidence"][derivation.FIELD]
        self.assertEqual(evidence["content_sha256"], derivation.digest(derivation.owned_content(candidate)))
        self.assertEqual(evidence["binding_sha256"], derivation.digest(
            {key: value for key, value in evidence.items() if key != "binding_sha256"}))
        first = derivation.inspect_derivation(candidate)
        self.assertTrue(first["valid"], first)
        first["source_obligations"][0]["actual_binding"]["field_evidence_ids"].clear()
        second = derivation.inspect_derivation(candidate)
        self.assertTrue(second["valid"], second)
        self.assertTrue(second["source_obligations"][0]["actual_binding"]["field_evidence_ids"])
        self.assertEqual(derivation._replay_compilation.cache_info().hits, 1)
        candidate["shots"][0]["画面内容"] += "改变原意。"
        self.assertFalse(derivation.inspect_derivation(candidate)["valid"])


if __name__ == "__main__":
    unittest.main()
