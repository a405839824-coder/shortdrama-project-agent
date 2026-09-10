from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from shortdrama_mcp.service import validate_storyboard as validator
from test_strict_prompt_contract import strict_fixture


def history_row(round_number, residual=0, introduced=0):
    return {"round": round_number, "initial_issue_count": 2,
            "residual_issue_count": residual, "introduced_issue_count": introduced,
            "repeat_rounds": 1, "action": "root_cause_repair"}


def repaired_fixture():
    obj = strict_fixture()
    obj["prevention_evidence"]["repair_history"] = [
        history_row(1, residual=2, introduced=2), history_row(2)]
    return obj


class CurrentRepairObservationTests(unittest.TestCase):
    def assert_bound_fault(self, obj):
        report = validator.validate_project(obj, mode="repair")
        self.assertTrue(any(i["location"].endswith("repair_history.current_check")
                            for i in report["issues"]), report)
        return report

    def test_measured_clean_candidate_closes_current_gate_preserving_failed_history(self):
        raw = repaired_fixture()
        frozen = copy.deepcopy(raw)
        current = validator.bind_current_repair_check(raw)
        report = validator.validate_project(current, mode="repair")
        self.assertEqual(report["issues"], [])
        self.assertEqual(raw, frozen)
        self.assertEqual(current["prevention_evidence"]["repair_history"][0],
                         frozen["prevention_evidence"]["repair_history"][0])
        self.assertEqual(current["prevention_evidence"]["repair_history"][-1]
                         ["current_check"]["semantic_closure"], "requires_independent_audit")

    def test_appended_zero_without_run_cannot_close_history_gate(self):
        self.assert_bound_fault(repaired_fixture())

    def test_current_fault_survives_zero_count_and_fresh_binding(self):
        obj = repaired_fixture()
        obj["shots"][0]["机位"] += "；同上。"
        current = validator.bind_current_repair_check(obj)
        report = validator.validate_project(current, mode="repair")
        self.assertTrue(any(i["error_code"] == "E-I2V-INDEP-001" for i in report["issues"]))
        self.assertGreater(current["prevention_evidence"]["repair_history"][-1]
                           ["current_check"]["current_issue_count"], 0)

    def test_body_change_invalidates_old_clean_observation(self):
        obj = validator.bind_current_repair_check(repaired_fixture())
        obj["shots"][0]["机位"] += "；同上。"
        self.assert_bound_fault(obj)

    def test_history_tamper_invalidates_current_observation(self):
        obj = validator.bind_current_repair_check(repaired_fixture())
        obj["prevention_evidence"]["repair_history"][0]["residual_issue_count"] = 0
        self.assert_bound_fault(obj)

    def test_fabricated_latest_count_invalidates_observation(self):
        obj = validator.bind_current_repair_check(repaired_fixture())
        obj["prevention_evidence"]["repair_history"][-1]["initial_issue_count"] = 99
        self.assert_bound_fault(obj)

    def test_changed_checker_invalidates_observation(self):
        obj = validator.bind_current_repair_check(repaired_fixture())
        with patch.object(validator.repair_observations, "checker_digest", return_value="0" * 64):
            self.assert_bound_fault(obj)

    def test_archived_malformed_history_stays_blocking(self):
        obj = repaired_fixture()
        obj["prevention_evidence"]["repair_history"][0]["initial_issue_count"] = -1
        obj = validator.bind_current_repair_check(obj)
        report = validator.validate_project(obj, mode="repair")
        self.assertTrue(any(i["location"] == "repair_history[1]" for i in report["issues"]))

    def test_unknown_origin_is_retained_without_being_converted_to_zero(self):
        obj = repaired_fixture()
        first = obj["prevention_evidence"]["repair_history"][0]
        first["introduced_issue_count"] = None
        first["introduced_count_state"] = "attribution_pending"
        obj = validator.bind_current_repair_check(obj)
        self.assertEqual(validator.validate_project(obj, mode="repair")["issues"], [])
        self.assertIsNone(obj["prevention_evidence"]["repair_history"][0]["introduced_issue_count"])

    def test_unexplained_null_is_not_accepted_as_a_real_measurement(self):
        obj = repaired_fixture()
        obj["prevention_evidence"]["repair_history"][0]["introduced_issue_count"] = None
        obj = validator.bind_current_repair_check(obj)
        self.assertTrue(validator.validate_project(obj, mode="repair")["issues"])

    def test_current_unknown_attribution_and_zero_denominator_stay_unknown_metrics(self):
        obj = repaired_fixture()
        latest = obj["prevention_evidence"]["repair_history"][-1]
        latest.update(initial_issue_count=0, introduced_issue_count=None,
                      introduced_count_state="attribution_pending")
        obj = validator.bind_current_repair_check(obj)
        metrics = validator.validate_project(obj, mode="repair")["quality_metrics"]
        self.assertIsNone(metrics["side_effect_rate_percent"])
        self.assertIsNone(metrics["first_repair_effectiveness_percent"])
        self.assertIsNone(metrics["residual_rate_percent"])

    def test_malformed_evidence_returns_diagnostics_instead_of_crashing(self):
        for value in ([], "invalid", None):
            with self.subTest(value=value):
                obj = strict_fixture()
                obj["prevention_evidence"] = value
                self.assertTrue(validator.validate_project(obj, mode="repair")["issues"])
                with self.assertRaises(ValueError):
                    validator.bind_current_repair_check(obj)


if __name__ == "__main__":
    unittest.main()
