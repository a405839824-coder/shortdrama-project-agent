"""Actual record boundary for a complete source input and rejected free prose."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_v0527_source_decision_independent_audit import complete_source_input
import author_storyboard
from production_derivation import inspect_derivation


class SourceRecordIntegrationTests(unittest.TestCase):
    def record(self, raw, graph):
        persistent = os.environ.get("SOURCE_RECORD_EVIDENCE_ROOT")
        if persistent:
            folder = Path(persistent) / self._testMethodName
            folder.mkdir(parents=True, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            folder = Path(temporary.name)
        original = json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
        (folder / "input.json").write_bytes(original)
        (folder / "graph.json").write_bytes(graph)
        result = author_storyboard.record_attempt(folder / "input.json", folder / "attempts", attempt_id="first",
                    source_graph_path=folder / "graph.json", source_graph_sha256=hashlib.sha256(graph).hexdigest())
        (folder / "actual_record_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        attempt = folder / "attempts/first"
        self.assertEqual((attempt / "input.original.json").read_bytes(), original)
        self.assertEqual((attempt / "source_graph.original.json").read_bytes(), graph)
        return result, attempt

    def test_complete_source_candidate_records_with_recompilable_pending_derivation(self):
        raw, graph, _ = complete_source_input()
        result, attempt = self.record(raw, graph)
        self.assertEqual(result["status"], "rendered_pending_preflight", result.get("findings"))
        canonical = json.loads((attempt / "canonical.json").read_bytes())
        inspected = inspect_derivation(canonical)
        self.assertTrue(inspected["present"])
        self.assertTrue(inspected["valid"], inspected.get("faults"))
        self.assertTrue(inspected["source_obligations"])
        self.assertFalse(result["commit_eligible"])
        self.assertIsNone(result["semantic_accuracy_percent"])

    def test_free_environment_prose_retains_rejected_raw_without_a_canonical(self):
        raw, graph, _ = complete_source_input()
        raw["shots"][0]["editorial"]["环境动态"] = "纸页被风翻动，窗扇开启。"
        with patch.object(author_storyboard, "_render_decisions", side_effect=AssertionError("unbound environment cannot reach canonical assembly")):
            result, attempt = self.record(raw, graph)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse((attempt / "canonical.json").exists())
        self.assertTrue(any("free environmental prose" in finding.get("message", "") for finding in result["findings"]), result["findings"])
        self.assertFalse(result["commit_eligible"])
        self.assertIsNone(result["semantic_accuracy_percent"])


if __name__ == "__main__":
    unittest.main()
