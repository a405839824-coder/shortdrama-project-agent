"""Acceptance for truthful new-request binding, not proof of generation quality."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from shortdrama_mcp.production_binding import (
    build_production_binding,
    candidate_observation_fields,
    prepare_new_candidate_context,
)


def context():
    return {"project_version": "server-runtime-test", "workflow_profile": "server-flow-test",
            "schema_version": "server-schema-test", "ruleset_version": "server-rules-test",
            "ruleset_sha256": "a" * 64, "validator_sha256": "b" * 64,
            "schema_sha256": "c" * 64, "runtime_sha256": "d" * 64}


def source():
    payload = "真正导入的原文：甲说道，别走。".encode("utf-8")
    return {"artifact_id": "ART-source", "artifact_type": "source:novel",
            "fingerprint": hashlib.sha256(payload).hexdigest()}, payload


class ProductionBindingTests(unittest.TestCase):
    def binding(self, candidate, **kwargs):
        return build_production_binding(candidate, project_id="project-one", scope_key="EP36",
                                        server_context=context(), **kwargs)

    def test_only_absent_versions_are_filled_from_actual_runtime_without_mutating_input(self):
        raw = {"shots": [{"画面内容": "原始文字"}]}
        frozen = copy.deepcopy(raw)
        prepared, changes = prepare_new_candidate_context(raw, server_context=context())
        self.assertEqual(raw, frozen)
        self.assertEqual(prepared["project_version"], "server-runtime-test")
        self.assertEqual(prepared["workflow_profile"], "server-flow-test")
        self.assertEqual({r["path"] for r in changes}, {"project_version", "workflow_profile"})
        for field in ("source_lock", "coverage", "prevention_evidence", "schema_version"):
            self.assertNotIn(field, prepared)
        prepared["shots"][0]["画面内容"] = "改动后"
        self.assertEqual(raw, frozen)

    def test_explicit_stale_blank_null_and_malformed_versions_are_not_silently_rewritten(self):
        for field in ("project_version", "workflow_profile"):
            for value in ("old", "", None, 64, [], {}):
                with self.subTest(field=field, value=value):
                    raw = {field: value}
                    frozen = copy.deepcopy(raw)
                    with self.assertRaisesRegex(ValueError, "production_context_conflict"):
                        prepare_new_candidate_context(raw, server_context=context())
                    self.assertEqual(raw, frozen)

    def test_schema_and_rule_claim_conflicts_are_rejected_and_not_upgraded(self):
        for field in ("schema_version", "ruleset_version", "ruleset_sha256", "runtime_sha256"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "production_context_conflict"):
                prepare_new_candidate_context({field: "old"}, server_context=context())

    def test_explicit_source_lock_runtime_claim_is_checked_but_source_version_is_not_rewritten(self):
        with self.assertRaisesRegex(ValueError, "production_context_conflict"):
            prepare_new_candidate_context({"source_lock": {"ruleset_version": "old"}},
                                          server_context=context())
        raw = {"source_lock": {"latest_version": "source-document-v1"}}
        prepared, _ = prepare_new_candidate_context(raw, server_context=context())
        self.assertEqual(prepared["source_lock"], raw["source_lock"])

    def test_unavailable_runtime_cannot_be_faked_from_defaults_or_candidate_claims(self):
        for absent in (None, "", "unknown"):
            with self.subTest(absent=absent), self.assertRaisesRegex(ValueError, "production_context_unavailable"):
                prepare_new_candidate_context({}, server_context={"project_version": absent,
                                                                   "workflow_profile": "flow"})

    def test_actual_source_bytes_are_bound_without_creating_source_lock_or_coverage(self):
        record, payload = source()
        raw = {"shots": []}
        binding = self.binding(raw, source_artifact=record, source_bytes=payload)
        self.assertEqual(raw, {"shots": []})
        self.assertEqual(binding["source"]["artifact_id"], record["artifact_id"])
        self.assertEqual(binding["source"]["bytes_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(binding["source"]["bytes_verified"])
        self.assertIn("source_lock", binding["missing_evidence"])
        self.assertIn("source_lock.source_artifact_id", binding["missing_evidence"])
        self.assertIn("coverage", binding["missing_evidence"])
        self.assertIn("prevention_evidence", binding["missing_evidence"])
        self.assertFalse(binding["source_lock_created"])
        self.assertFalse(binding["independent_content_coverage_verified"])
        self.assertFalse(binding["historical_evidence_repaired"])

    def test_missing_source_does_not_acquire_synthetic_fingerprint(self):
        binding = self.binding({})
        self.assertIsNone(binding["source"]["bytes_sha256"])
        self.assertFalse(binding["source"]["bytes_verified"])
        self.assertIn("selected_source.artifact_id", binding["missing_evidence"])
        self.assertIn("selected_source.bytes_sha256", binding["missing_evidence"])

    def test_missing_fingerprints_are_reported_and_caller_coverage_is_not_verification(self):
        server = context()
        del server["runtime_sha256"]
        raw = {"coverage": {"all_source_checked": True}, "prevention_evidence": {"audit_layers": "all pass"}}
        binding = build_production_binding(raw, project_id="project", server_context=server)
        self.assertIn("server.runtime_sha256", binding["missing_evidence"])
        self.assertIsNone(binding["server"]["runtime_sha256"])
        self.assertNotIn("coverage", binding["missing_evidence"])
        self.assertFalse(binding["independent_content_coverage_verified"])

    def test_missing_or_tampered_source_bytes_are_rejected(self):
        record, payload = source()
        for raw in (None, payload + b"tampered"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.binding({}, source_artifact=record, source_bytes=raw)
        with self.assertRaisesRegex(ValueError, "identity_required"):
            self.binding({}, source_bytes=payload)
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            self.binding({}, source_artifact={"artifact_id": "ART-source"}, source_bytes=payload)

    def test_matching_source_claims_are_verified_against_server_record_and_bytes(self):
        record, payload = source()
        raw = {"source_lock": {"source_artifact_id": record["artifact_id"],
                               "authoritative_source_artifact_id": record["artifact_id"],
                               "source_fingerprint": record["fingerprint"].upper(),
                               "source_role": "source:novel"}}
        binding = self.binding(raw, source_artifact=record, source_bytes=payload)
        self.assertNotIn("source_lock.source_artifact_id", binding["missing_evidence"])
        self.assertNotIn("source_lock.source_fingerprint", binding["missing_evidence"])

    def test_conflicting_source_id_hash_role_and_aliases_are_rejected(self):
        record, payload = source()
        for lock in ({"source_artifact_id": "different"}, {"source_fingerprint": "0" * 64},
                     {"source_role": "script"}, {"source_artifact_id": record["artifact_id"],
                                                   "authoritative_source_artifact_id": "different"}):
            with self.subTest(lock=lock), self.assertRaisesRegex(ValueError, "source_claim_conflict"):
                self.binding({"source_lock": lock}, source_artifact=record, source_bytes=payload)

    def test_no_server_source_cannot_be_authorized_by_candidate_assertion(self):
        with self.assertRaisesRegex(ValueError, "source_claim_conflict"):
            self.binding({"source_lock": {"source_artifact_id": "invented"}})

    def test_repeated_global_missing_evidence_has_same_identity_across_runs(self):
        first = self.binding({"shots": [{"画面内容": "首轮"}]})
        second = self.binding({"shots": [{"画面内容": "第二轮"}]})
        self.assertEqual(first["global_missing_evidence"], second["global_missing_evidence"])
        self.assertTrue(all(not row["content_defect"] and not row["is_new_content_defect"]
                            for row in first["global_missing_evidence"]))
        other_scope = build_production_binding({}, project_id="project-one", scope_key="EP37",
                                              server_context=context())
        self.assertNotEqual(first["global_missing_evidence"][0]["identity"],
                            other_scope["global_missing_evidence"][0]["identity"])

    def test_observation_preserves_complete_unnormalized_candidate_and_change_log(self):
        raw = {"shots": [{"生成限制": "不加额外配乐", "对白": "这句不能改。"}]}
        frozen = copy.deepcopy(raw)
        prepared, changes = prepare_new_candidate_context(raw, server_context=context())
        prepared["shots"][0]["生成限制"] = "禁止额外配乐"
        changes.append({"path": "shots[0].生成限制", "before": "不加额外配乐", "after": "禁止额外配乐"})
        result = candidate_observation_fields(raw, prepared_candidate=prepared,
                                              production_binding=self.binding(prepared), changes=changes)
        self.assertEqual(result["candidate_snapshot"], frozen)
        self.assertEqual(result["preparation_changes"], changes)
        self.assertNotEqual(result["input_sha256"], result["prepared_candidate_sha256"])
        self.assertFalse(result["model_first_draft_verified"])
        self.assertIsNone(result["raw_generation_quality"])
        self.assertEqual(result["raw_generation_state"], "generation_provenance_unverified")
        raw["shots"].clear()
        self.assertEqual(result["candidate_snapshot"], frozen)
        changes.clear()
        self.assertEqual(len(result["preparation_changes"]), 3)

    def test_changed_candidate_without_change_log_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changes_missing"):
            candidate_observation_fields({}, prepared_candidate={"project_version": "filled"},
                                         production_binding={}, changes=[])

    def test_json_roundtrip_keeps_snapshot_hash_and_does_not_record_model_first_draft(self):
        raw = {"shots": [], "source_lock": {"episode_id": "EP36"}}
        binding = self.binding(raw)
        fields = candidate_observation_fields(raw, prepared_candidate=raw,
                                              production_binding=binding, changes=[])
        reread = json.loads(json.dumps(fields, ensure_ascii=False))
        self.assertEqual(reread, fields)
        self.assertEqual(fields["input_sha256"], fields["prepared_candidate_sha256"])


if __name__ == "__main__":
    unittest.main()
