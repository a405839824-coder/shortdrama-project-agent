"""Real scorer snapshots must survive every strict persistence schema.

These are serialization/reading controls, not independent story audits or proof
of a first-generation success rate. No audit authority or signature is forged.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/shortdrama-storyboard/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "server"))

from jsonschema import Draft202012Validator
import project_agent
import storyboard_agent
from shortdrama_mcp.service import (
    StoryboardService, _draft_quality_metrics, _legacy_draft_quality_metrics,
)


def public_draft():
    return {"shots": [
        {"镜头编号": f"{ep}-001", "episode_id": ep, "shot_uid": ep,
         "画面内容": "人物坐在桌旁。"}
        for ep in ("EP01", "EP02")
    ]}


def public_issue(code="E-SOURCE-001", ids=()):
    return {"error_code": code, "field": "画面内容", "shot_ids": list(ids),
            "location": "public-control", "severity": "fail", "blocking": True,
            "evidence": "公开控制缺项", "repair_action": "核对公开控制"}


def persisted_quality(draft, issues, *, complete=False):
    """Use the real writer; bare score output is not its complete envelope."""
    service = object.__new__(StoryboardService)
    event = service._audit_event_content(
        project_id="SBP-quality-schema", artifact_record={"artifact_id": "A1", "fingerprint": "public"},
        storyboard=draft, report={"issues": issues, "independent_evidence_matrix": {
            "semantic_obligation_coverage": {"completed_shot_ids": [s['镜头编号'] for s in draft['shots']],
                                             "passed_shot_ids": [s['镜头编号'] for s in draft['shots']],
                                             "complete": True} if complete else {}}}, ledger={"events": []},
        cycle_id="C1", audit_class="independent_audit", audit_cache_key="public",
        prior_events=[], audit_round=1, known_pattern_keys=set())
    return event["first_draft_quality"]


def legacy_snapshot():
    # Explicit old stored-envelope flags; no current semantic certificate exists.
    quality = _legacy_draft_quality_metrics(public_draft(), [])
    quality.update(threshold_met=True, final_audit_pass=True)
    return quality


class QualitySchemaRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        refs = ROOT / "skills/shortdrama-storyboard/references"
        cls.hub_schema = json.loads((refs / "project_agent_schema.json").read_text(encoding="utf-8"))
        cls.ledger_schema = json.loads((refs / "agent_ledger_schema.json").read_text(encoding="utf-8"))
        cls.hub_validator = Draft202012Validator(cls.hub_schema)
        cls.quality_validators = {
            "hub-quality": Draft202012Validator({
                "$ref": "#/$defs/first_draft_quality", "$defs": cls.hub_schema["$defs"]}),
            "audit-certificate-quality": Draft202012Validator({
                "$ref": "#/$defs/independent_audit_certificate/properties/audit_summary/properties/first_draft_quality",
                "$defs": cls.ledger_schema["$defs"]}),
        }

    def assert_snapshot_roundtrip(self, quality):
        snapshot = json.loads(json.dumps(quality, ensure_ascii=False, allow_nan=False))
        self.assertEqual(snapshot, quality)
        for name, validator in self.quality_validators.items():
            with self.subTest(schema=name):
                self.assertEqual(list(validator.iter_errors(snapshot)), [])
        hub = project_agent.new_hub("公开质量控制", project_id="SBP-quality-schema")
        hub["project_state"]["latest_first_draft_quality"] = snapshot
        self.assertEqual(list(self.hub_validator.iter_errors(hub)), [])
        # Whole-hub business validation additionally needs the service-owned
        # production block for below-threshold grades; do not fabricate it here.
        self.assertEqual(project_agent._validate_first_draft_quality(snapshot, "quality"), [])

    def test_actual_measured_A_and_C_roundtrip_all_snapshot_consumers(self):
        for issues in ([], [public_issue(ids=["EP01-001"])]):
            with self.subTest(issues=bool(issues)):
                # Explicit synthetic completed-review fixture tests serialization;
                # plain machine diagnostics do not establish measured accuracy.
                quality = persisted_quality(public_draft(), issues, complete=True)
                self.assertEqual(quality["measurement_state"], "measured")
                self.assertIn("episode_direct_usability_percent", quality)
                self.assertIn("minimum_episode_direct_usability_percent", quality)
                self.assert_snapshot_roundtrip(quality)

    def test_actual_unknown_snapshots_remain_readable_without_claiming_acceptance(self):
        cases = [({"shots": []}, []),
                 (public_draft(), [public_issue()]),
                 (public_draft(), [public_issue("E-TEMPLATE-001")]),
                 ({"shots": [{"镜头编号": "unsegmented"}]}, [])]
        for draft, issues in cases:
            with self.subTest(draft=draft, issues=issues):
                quality = persisted_quality(draft, issues)
                self.assertEqual(quality["grade"], "unavailable")
                self.assertFalse(quality["commit_eligible"])
                self.assert_snapshot_roundtrip(quality)

    def test_actual_legacy_score_without_new_fields_remains_readable(self):
        quality = legacy_snapshot()
        self.assertNotIn("quality_contract", quality)
        self.assertNotIn("episode_direct_usability_percent", quality)
        self.assert_snapshot_roundtrip(quality)

    def test_wrong_new_metric_types_are_rejected_by_both_schemas_and_reader(self):
        base = persisted_quality(public_draft(), [])
        variants = [
            ("episode_direct_usability_percent", []),
            ("episode_direct_usability_percent", {"EP01": True}),
            ("episode_direct_usability_percent", {"EP01": "98"}),
            ("episode_direct_usability_percent", {"EP01": 101}),
            ("minimum_episode_direct_usability_percent", True),
            ("minimum_episode_direct_usability_percent", "98"),
            ("minimum_episode_direct_usability_percent", -1),
        ]
        for field, bad in variants:
            with self.subTest(field=field, value=bad):
                quality = copy.deepcopy(base)
                quality[field] = bad
                for name, validator in self.quality_validators.items():
                    with self.subTest(schema=name):
                        self.assertTrue(list(validator.iter_errors(quality)))
                self.assertTrue(project_agent._validate_first_draft_quality(quality, "quality"))

    def test_unknown_cannot_claim_A_or_any_acceptance_flag_in_business_reader(self):
        base = persisted_quality(public_draft(), [public_issue()])
        for field, bad in (("grade", "A"), ("commit_eligible", True),
                           ("threshold_met", True), ("final_audit_pass", True)):
            with self.subTest(field=field):
                quality = copy.deepcopy(base)
                quality[field] = bad
                self.assertTrue(project_agent._validate_first_draft_quality(quality, "quality"))

    def test_measured_and_legacy_null_values_are_not_treated_as_valid_unknown(self):
        for name, base in (("v2", persisted_quality(public_draft(), [], complete=True)),
                           ("legacy", legacy_snapshot())):
            with self.subTest(scorer=name):
                quality = copy.deepcopy(base)
                quality["direct_usability_percent"] = None
                self.assertTrue(project_agent._validate_first_draft_quality(quality, "quality"))

    def test_real_audit_content_preserves_quality_snapshot_without_raw_first_grade(self):
        service = object.__new__(StoryboardService)
        event = service._audit_event_content(
            project_id="SBP-quality-schema", artifact_record={"artifact_id": "A1", "fingerprint": "public"},
            storyboard=public_draft(), report={"issues": []}, ledger={"events": []},
            cycle_id="C1", audit_class="independent_audit", audit_cache_key="public",
            prior_events=[], audit_round=1, known_pattern_keys=set())
        self.assertEqual(event["first_draft_quality"], event["audit_summary"]["first_draft_quality"])
        self.assertEqual(event["first_delivery_quality"], event["first_draft_quality"])
        self.assertIsNone(event["raw_generation_quality"])
        self.assert_snapshot_roundtrip(event["first_draft_quality"])

    def test_current_policy_98_and_legacy_96_readability_do_not_rewrite_history(self):
        hub = project_agent.new_hub("公开质量控制", project_id="SBP-quality-schema")
        ledger = storyboard_agent.new_ledger("公开质量控制", project_id="SBP-quality-schema")
        for value in (hub, ledger):
            self.assertEqual(value["policy"]["minimum_episode_correctness_percent"], 98)
            self.assertEqual(value["policy"]["minimum_episode_direct_usability_percent"], 98)
        old_hub = copy.deepcopy(hub)
        old_hub["policy"]["minimum_episode_correctness_percent"] = 96
        old_hub["policy"].pop("minimum_episode_direct_usability_percent")
        old_quality = legacy_snapshot()
        old_hub["project_state"]["latest_first_draft_quality"] = copy.deepcopy(old_quality)
        frozen = copy.deepcopy(old_hub)
        self.assertEqual(project_agent.validate_hub(old_hub), [])
        migrated, _ = project_agent.migrate_hub_to_current(old_hub)
        self.assertEqual(migrated["policy"]["minimum_episode_correctness_percent"], 98)
        self.assertEqual(migrated["policy"]["minimum_episode_direct_usability_percent"], 98)
        self.assertEqual(migrated["project_state"]["latest_first_draft_quality"], old_quality)
        self.assertEqual(old_hub, frozen)
        invalid = copy.deepcopy(hub)
        invalid["policy"]["minimum_episode_correctness_percent"] = 96
        self.assertTrue(project_agent.validate_hub(invalid))


if __name__ == "__main__":
    unittest.main()
