from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from shortdrama_mcp.models import ToolResponse
from shortdrama_mcp.service import _with_repair_mode_advice
import repair_mode_advice as advice


def issue(code="E-VOICE-001", *, shots=None, **extra):
    return {"error_code": code, "shot_ids": shots or ["S001"],
            "location": "shots[0].音色", "severity": "fail",
            "repair_action": "按锁定人物补全本镜声线。", **extra}


class RepairModeAdviceTests(unittest.TestCase):
    def recommend(self, issues, status="fail_current", **kwargs):
        return advice.build_repair_mode_advice({"issues": issues}, audit_status=status, **kwargs)

    def test_many_independent_mechanical_edits_stay_low(self):
        rows = [issue("E-PUNCT-001", shots=[f"S{i:03}"], location=f"shots[{i}].对白") for i in range(100)]
        result = self.recommend(rows)
        self.assertEqual(result["recommended_effort"], "low")
        self.assertEqual(len(result["scope"]["shot_ids"]), 100)

    def test_local_semantics_medium_and_missing_fix_not_mechanical(self):
        for row in [issue(), issue("E-PUNCT-001", repair_action="")]:
            self.assertEqual(self.recommend([row])["recommended_effort"], "medium")

    def test_one_cross_shot_dependency_requires_joint_reasoning(self):
        self.assertEqual(self.recommend([issue(shots=["S001", "S002"])])["recommended_effort"], "xhigh")
        self.assertEqual(self.recommend([issue("E-SOURCE-001")])["recommended_effort"], "xhigh")

    def test_rule_conflict_deep_but_not_all_structural_work(self):
        self.assertEqual(self.recommend([issue(issue_type="rule_conflict")])["recommended_effort"], "max")
        self.assertEqual(self.recommend([issue("E-SHOTLOAD-001")])["recommended_effort"], "xhigh")

    def test_mixed_batch_preserves_simple_group_and_locations(self):
        result = self.recommend([issue("E-PUNCT-001"), issue(shots=["S002", "S003"])])
        self.assertEqual(result["recommended_effort"], "xhigh")
        self.assertEqual([g["recommended_effort"] for g in result["groups"]], ["low", "xhigh"])
        self.assertEqual(result["scope"]["shot_ids"], ["S001", "S002", "S003"])
        self.assertTrue(all(g["reasons"] and g["escalation_conditions"] for g in result["groups"]))

    def test_targets_supply_exact_shots_without_changing_input(self):
        row = issue();row.pop("shot_ids")
        package = {"issue_targets": [{"issue_id": "E-VOICE-001", "location": row["location"], "shot_id": "S009"}]}
        original = copy.deepcopy((row, package))
        self.assertEqual(self.recommend([row], repair_package=package)["scope"]["shot_ids"], ["S009"])
        self.assertEqual((row, package), original)

    def test_clean_pass_and_only_calibration_notice_need_no_repair(self):
        for rows in [[], [issue(severity="warning", blocking=False)]]:
            result = self.recommend(rows, "pass")
            self.assertEqual(result["status"], "no_repair")
            self.assertEqual(result["recommendation"], "无需修正")
            self.assertIsNone(result["recommended_effort"])

    def test_blocking_warning_still_gets_repair_advice(self):
        self.assertEqual(self.recommend([issue(severity="warning", blocking=True)])["status"], "recommended")

    def test_incomplete_or_blocked_audit_is_not_no_repair(self):
        for status in ["blocked", "fail_current"]:
            self.assertEqual(self.recommend([], status)["status"], "supplement_required")
        self.assertEqual(advice.build_repair_mode_advice({}, audit_status="pass")["status"], "supplement_required")

    def test_model_and_effort_mapping_are_configurable(self):
        policy, revision = advice.load_policy()
        policy["model_context"] = {"model": "future-model", "available_efforts": ["custom"]}
        policy["tiers"]["local"].update(effort="custom", label="当前界面档位")
        result = self.recommend([issue()], policy=policy, policy_version=revision)
        self.assertEqual(result["recommended_effort"], "custom")
        self.assertEqual(result["model_context"]["model"], "future-model")

    def test_response_annex_preserves_signed_data_on_all_return_kinds(self):
        for status, rows in [("pass", []), ("fail_current", [issue()]), ("blocked", [])]:
            for cached in [False, True]:
                response = ToolResponse(status=status, summary="原结论", data={
                    "report": {"issues": rows}, "certificate": {"signature": "immutable"}, "cache_hit": cached})
                original = copy.deepcopy(response.model_dump())
                wrapped = _with_repair_mode_advice(lambda _self: response)
                result = wrapped(None).model_dump()
                annex = result["data"].pop("repair_mode_recommendation")
                self.assertFalse(annex["affects_audit_verdict"])
                self.assertEqual(result, original)

    def test_advice_failure_does_not_fail_completed_audit(self):
        response = ToolResponse(status="pass", summary="原结论", data={"report": {"issues": []}})
        with patch.object(advice, "build_repair_mode_advice", side_effect=OSError("advice unavailable")):
            result = _with_repair_mode_advice(lambda _self: response)(None)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.data["repair_mode_recommendation"]["status"], "supplement_required")

    def test_actual_audit_errors_are_not_swallowed(self):
        @_with_repair_mode_advice
        def failed(_self):
            raise ValueError("audit authorization failed")
        with self.assertRaisesRegex(ValueError, "audit authorization"):
            failed(None)


if __name__ == "__main__":
    unittest.main()
