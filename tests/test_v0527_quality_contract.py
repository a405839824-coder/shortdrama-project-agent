from __future__ import annotations

import copy
import unittest

from shortdrama_mcp.service import StoryboardService, _draft_quality_metrics


def draft(count=100):
    return {"shots": [{"镜头编号": f"EP01-{i:03}", "episode_id": "EP01",
                       "shot_uid": f"shot-{i}", "画面内容": "人物坐在桌旁。"}
                      for i in range(1, count + 1)]}


def defect(code="E-SOURCE-001", field="画面内容", **extra):
    return {"error_code": code, "field": field, "location": "review:Q1",
            "shot_ids": ["EP01-001"], "severity": "fail", "blocking": True,
            "evidence": "原文问答缺失", "repair_action": "补全原文问答", **extra}


class QualityContractTests(unittest.TestCase):
    def test_global_repair_history_failure_blocks_workflow_without_invented_bad_shots(self):
        q = _draft_quality_metrics(draft(3), [defect("E-REGRESSION-001", "repair_history",
            location="repair_history[1]", shot_ids=[])])
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_defect_shot_percent"], 100)
        self.assertEqual(q["content_affected_shot_ids"], [])
        self.assertEqual(q["workflow_issue_count"], 1)
        self.assertFalse(q["commit_eligible"])

    def test_empty_draft_cannot_claim_perfect_quality(self):
        q = _draft_quality_metrics({"shots": []}, [])
        self.assertIsNone(q["first_draft_correctness_percent"])
        self.assertIsNone(q["direct_usability_percent"])
        self.assertFalse(q["commit_eligible"])

    def test_correctness_and_usability_measure_different_defects(self):
        q = _draft_quality_metrics(draft(), [defect("E-FIELDORDER-001", "字段顺序")])
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_defect_shot_percent"], 100)
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_or_contract_defect_shot_percent"], 99)
        self.assertFalse(q["commit_eligible"])

    def test_nonblocking_known_advice_does_not_mark_shot_unusable(self):
        q = _draft_quality_metrics(draft(), [defect("N-TIMING-UNCALIBRATED", "语速标定",
                                  severity="warning", blocking=False)])
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_or_contract_defect_shot_percent"], 100)
        self.assertEqual(q["advisory_count"], 1)

    def test_blocking_warning_cannot_be_downgraded(self):
        q = _draft_quality_metrics(draft(), [defect("E-LIGHT-002", "光源方向",
                                  severity="warning", blocking=True)])
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_or_contract_defect_shot_percent"], 99)
        self.assertFalse(q["commit_eligible"])

    def test_real_lighting_warning_is_still_a_content_fault(self):
        q = _draft_quality_metrics(draft(), [defect("E-LIGHT-002", "光源方向",
                                  severity="warning", blocking=False)])
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_or_contract_defect_shot_percent"], 99)
        self.assertEqual(q["advisory_count"], 0)

    def test_unlocated_source_failure_has_unknown_rate_not_false_100(self):
        q = _draft_quality_metrics(draft(), [defect(shot_ids=[], location="source_coverage")])
        self.assertIsNone(q["first_draft_correctness_percent"])
        self.assertFalse(q["commit_eligible"])

    def test_multiple_defects_on_same_shot_count_once(self):
        q = _draft_quality_metrics(draft(), [defect(), defect("E-CONTINUITY-001")])
        self.assertEqual(q["unique_affected_shot_count"], 1)
        self.assertEqual(q["diagnostic_summary"]["no_reported_content_or_contract_defect_shot_percent"], 99)

    def event(self, report, *, prior=None, current=None):
        service = object.__new__(StoryboardService)
        return service._audit_event_content(
            project_id="P1", artifact_record={"artifact_id": "A1", "fingerprint": "same"},
            storyboard=current or draft(1), report=report, ledger={"events": []},
            cycle_id="C1", audit_class="independent_audit", audit_cache_key="key",
            prior_events=prior or [], audit_round=2 if prior else 1, known_pattern_keys=set())

    def test_changed_report_number_does_not_create_a_new_defect(self):
        old = self.event({"issues": [defect()]})
        new = self.event({"issues": [defect(location="review:Q99")]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "residual")

    def test_clean_diagnostics_do_not_establish_raw_generation_quality(self):
        event = self.event({"issues": []})
        self.assertEqual(event["first_delivery_quality"]["grade"], "unavailable")
        self.assertIsNone(event["raw_generation_quality"])
        self.assertEqual(event["raw_generation_quality_state"], "requires_first_candidate_evidence")

    def test_repair_does_not_replace_first_delivery_score_with_clean_current_score(self):
        first = self.event({"issues": [defect()]})
        frozen_quality = copy.deepcopy(first["first_delivery_quality"])
        repaired = self.event({"issues": []}, prior=[first])
        self.assertEqual(repaired["result"], "pass")
        self.assertEqual(repaired["first_delivery_quality"], frozen_quality)
        self.assertEqual(repaired["first_draft_quality"], frozen_quality)
        self.assertEqual(first["first_delivery_quality"], frozen_quality)
        self.assertIsNone(repaired["raw_generation_quality"])

    def test_new_finding_on_identical_content_is_a_missed_issue(self):
        old = self.event({"issues": []})
        new = self.event({"issues": [defect()]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "missed_in_prior_audit")
        self.assertEqual(new["audit_summary"]["introduced_issue_count"], 0)
        self.assertEqual(new["audit_summary"]["missed_issue_count"], 1)

    def test_change_alone_is_not_proof_of_a_repair_side_effect(self):
        old = self.event({"issues": []})
        changed = copy.deepcopy(draft(1))
        changed["shots"][0]["画面内容"] = "人物站在桌旁。"
        new = self.event({"issues": [defect()]}, prior=[old], current=changed)
        self.assertEqual(new["issues"][0]["status"], "attribution_pending")
        self.assertEqual(new["audit_summary"]["attribution_pending_count"], 1)

    def test_display_renumbering_preserves_issue_identity(self):
        old = self.event({"issues": [defect()]})
        current = draft(1)
        current["shots"][0]["镜头编号"] = "EP01-099"
        new = self.event({"issues": [defect(shot_ids=["EP01-099"])]}, prior=[old], current=current)
        self.assertEqual(new["issues"][0]["status"], "residual")
        self.assertEqual(new["repair_convergence"][0]["required_action"], "root_cause_repair")

    def test_different_missing_conditions_are_not_the_same_residual(self):
        old = self.event({"issues": [defect(evidence="缺少条件甲",
            evidence_ids=["source-unit:condition-a"])]})
        changed = draft(1)
        changed["shots"][0]["画面内容"] = "已补条件甲，但遗漏条件乙。"
        new = self.event({"issues": [defect(evidence="缺少条件乙",
            evidence_ids=["source-unit:condition-b"])]}, prior=[old], current=changed)
        self.assertEqual(new["issues"][0]["status"], "attribution_pending")
        self.assertEqual(new["repair_convergence"], [])
        self.assertEqual(new["audit_summary"]["residual_issue_count"], 0)
        self.assertFalse(new["audit_summary"]["introduced_count_complete"])

    def test_machine_map_preserves_two_defects_in_one_field(self):
        from shortdrama_mcp.quality import machine_issue_map
        issues = [defect(evidence="缺少条件甲"), defect(evidence="缺少条件乙")]
        mapped = machine_issue_map(draft(1), issues, lambda storyboard, issue: issue["shot_ids"])
        self.assertEqual(len(mapped), 2)

    def test_source_occurrences_disambiguate_identical_diagnostic_text(self):
        old = self.event({"issues": [defect(evidence_ids=["source-unit:first"])]})
        new = self.event({"issues": [defect(evidence_ids=["source-unit:second"])]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "missed_in_prior_audit")
        self.assertEqual(new["repair_convergence"], [])

    def test_report_ids_do_not_replace_finding_evidence(self):
        old = self.event({"issues": [defect(semantic_issue_id="Q1", location="review:Q1",
            evidence_ids=["source-unit:first", "shot:EP01-001"])]})
        changed = draft(1)
        changed["shots"][0]["镜头编号"] = "EP01-099"
        new = self.event({"issues": [defect(semantic_issue_id="Q99", location="review:Q99",
            shot_ids=["EP01-099"], evidence_ids=["shot:EP01-099", "source-unit:first"])]},
            prior=[old], current=changed)
        self.assertEqual(new["issues"][0]["status"], "residual")

    def test_changed_rules_are_checked_before_matching_a_residual(self):
        old = self.event({"issues": [defect()]})
        old["assessment_context"]["rules_fingerprint"] = "older-rules"
        new = self.event({"issues": [defect()]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "rule_changed")
        self.assertEqual(new["repair_convergence"], [])

    def test_candidate_cannot_supply_its_own_finding_fingerprint(self):
        from shortdrama_mcp.quality import bind_issue_identity, assessment_context, issue_identity
        context = assessment_context(draft(1))
        old = bind_issue_identity(defect(evidence="缺少甲", finding_fingerprint="same"), context)
        new = bind_issue_identity(defect(evidence="缺少乙", finding_fingerprint="same"), context)
        self.assertNotEqual(issue_identity(old), issue_identity(new))

    def test_reworded_same_field_is_not_proven_previous_audit_miss(self):
        old = self.event({"issues": [defect(evidence="条件甲未交代")]})
        new = self.event({"issues": [defect(evidence="没有说明条件甲")]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "attribution_pending")
        self.assertEqual(new["audit_summary"]["missed_issue_count"], 0)

    def test_same_explicit_requirement_survives_rewording(self):
        old = self.event({"issues": [defect(evidence="条件甲未交代",
            requirement_id="condition-a", subject_id="江氏")]})
        new = self.event({"issues": [defect(evidence="没有说明条件甲",
            requirement_id="condition-a", subject_id="江氏")]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "residual")
        self.assertEqual(new["repair_convergence"][0]["consecutive_observations"], 2)
        self.assertEqual(new["issues"][0]["evidence"], "没有说明条件甲")

    def test_distinct_explicit_requirements_are_separate_findings(self):
        old = self.event({"issues": [defect(evidence="条件未交代",
            requirement_id="condition-a", subject_id="江氏")]})
        new = self.event({"issues": [defect(evidence="条件未交代",
            requirement_id="condition-b", subject_id="江氏")]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "missed_in_prior_audit")

    def test_expanded_overlapping_source_evidence_is_not_a_proven_miss(self):
        old = self.event({"issues": [defect(evidence_ids=["source-unit:a"])]})
        new = self.event({"issues": [defect(evidence_ids=["source-unit:a", "source-unit:b"])]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "attribution_pending")

    def test_reused_requirement_label_does_not_merge_distinct_source_units(self):
        old = self.event({"issues": [defect(requirement_id="required-condition",
            evidence_ids=["source-unit:first"])]})
        new = self.event({"issues": [defect(requirement_id="required-condition",
            evidence_ids=["source-unit:second"])]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "missed_in_prior_audit")

    def test_rule_change_is_not_a_repair_side_effect(self):
        old = self.event({"issues": []})
        old["assessment_context"]["rules_fingerprint"] = "old-rules"
        new = self.event({"issues": [defect()]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "rule_changed")

    def test_runtime_change_is_not_counted_as_previous_auditor_miss(self):
        old = self.event({"issues": []})
        old["assessment_context"]["runtime_fingerprint"] = "previous-parser-runtime"
        new = self.event({"issues": [defect()]}, prior=[old])
        self.assertEqual(new["issues"][0]["status"], "rule_changed")

    def test_failed_attempts_keep_lineage_and_exact_retry_does_not_inflate_rounds(self):
        from shortdrama_mcp.quality import annotate_attempt_event, assessment_context
        context = assessment_context(draft(1))
        events = []
        for index in range(3):
            event = annotate_attempt_event({"event_id": f"attempt-{index}",
                "work_order_id": f"different-order-{index}", "issues": [defect()]}, events, context)
            events.append(event)
        self.assertEqual(events[-1]["audit_round"], 3)
        self.assertEqual(events[-1]["repair_convergence"][0]["required_action"], "root_cause_repair")
        self.assertFalse(events[-1]["repair_convergence"][0]["resolved"])
        repeated = annotate_attempt_event(events[-1], events, context)
        self.assertEqual(repeated, events[-1])
        unrelated = annotate_attempt_event({"event_id": "different-defect", "issues": [defect("E-LIGHT-001")]}, events, context)
        self.assertEqual(unrelated["repair_convergence"], [])

    def test_machine_identity_survives_insertion_before_the_affected_shot(self):
        from shortdrama_mcp.quality import machine_issue_map
        from shortdrama_mcp.service import _issue_shot_ids
        before = draft(2)
        after = copy.deepcopy(before)
        after["shots"].insert(0, {"shot_uid": "inserted", "镜头编号": "EP01-000"})
        old = {"error_code": "E-LIGHT-001", "location": "shots[1].光源来源"}
        new = {**old, "location": "shots[2].光源来源"}
        self.assertEqual(set(machine_issue_map(before, [old], _issue_shot_ids)),
                         set(machine_issue_map(after, [new], _issue_shot_ids)))

    def test_diff_captures_empty_structure_changes_and_affected_scenes(self):
        from shortdrama_mcp.quality import repair_diff
        before = draft(2)
        before["shots"][0]["scene_uid"] = "scene-a"
        after = copy.deepcopy(before)
        after["source_lock"] = {}
        after["shots"][0]["画面内容"] = "人物起身。"
        proof = repair_diff(before, after)
        self.assertIn("source_lock", proof["changed_paths"])
        self.assertEqual(proof["changed_shot_uids"], ["shot-1"])
        self.assertEqual(proof["affected_scene_ids"], ["scene-a"])
        self.assertEqual(proof["semantic_closure"], "requires_independent_audit")

    def test_legacy_event_keeps_legacy_contract_for_replay(self):
        service = object.__new__(StoryboardService)
        event = service._audit_event_content(project_id="P1", artifact_record={},
            storyboard=draft(1), report={"issues": []}, ledger={"events": []}, cycle_id="C1",
            audit_class="independent_audit", audit_cache_key=None, prior_events=[], audit_round=1,
            known_pattern_keys=set(), assessment_contract=None)
        self.assertNotIn("assessment_context", event)
        self.assertNotIn("raw_generation_quality", event)

    def test_changing_only_action_label_is_not_actual_repair(self):
        from shortdrama_mcp.quality import repair_diff
        before = draft(1)
        before["prevention_evidence"] = {"repair_history": [{"action": "repair"}]}
        after = copy.deepcopy(before)
        after["prevention_evidence"]["repair_history"][0]["action"] = "rebuild_upstream"
        self.assertFalse(repair_diff(before, after)["content_changed"])
        after["shots"][0]["画面内容"] = "人物起身。"
        self.assertIn("shots[0].画面内容", repair_diff(before, after)["changed_paths"])

    def test_offscreen_exemption_is_bound_to_actual_speaker(self):
        from shortdrama_mcp.service import validate_storyboard
        check = validate_storyboard.dialogue_mouth_visible
        self.assertFalse(check("江氏", {"画面内容": "江氏画外发言；邹氏在画内听着。"}))
        self.assertFalse(check("江氏", {"机位": "手部特写，江氏口部不入画。"}))
        self.assertTrue(check("邹氏", {"画面内容": "江氏画外发言；邹氏在画内说话。"}))
        self.assertTrue(check("江氏", {"画面内容": "江氏说话，画外有风声。"}))
        self.assertTrue(check("江氏", {"画面内容": "甲说道：“江氏画外发言。”"}))
        self.assertTrue(check("江氏", {"机位": "禁止江氏口部不入画。"}))

    def test_closed_state_aliases_do_not_hide_prop_teleports_or_unknowns(self):
        from shortdrama_mcp.continuity import state_equal
        self.assertTrue(state_equal({"门": "关闭"}, {"门": "已关闭"}))
        self.assertFalse(state_equal({"门": "关闭"}, {"门": "打开"}))
        self.assertFalse(state_equal({"信件": "江氏右手"}, {"信件": "邹氏右手"}))
        self.assertFalse(state_equal({"人物站位": None}, {"人物站位": "门边"}))

    def test_offscreen_dialogue_keeps_voice_contract_but_exempts_lips(self):
        from shortdrama_mcp.service import validate_storyboard
        from test_strict_prompt_contract import strict_fixture
        current = strict_fixture()
        shot = current["shots"][0]
        speakers = validate_storyboard.extract_dialogues(shot["画面内容"])
        self.assertTrue(speakers)
        shot["画面内容"] = shot["画面内容"].replace("口型与对白一致", "")
        first = validate_storyboard.validate_project(current)
        self.assertTrue(any("口型与对白一致" in i.get("evidence", "") for i in first["issues"]))
        shot["机位"] += "；" + "；".join(f"{name}口部不入画" for name, _ in speakers)
        second = validate_storyboard.validate_project(current)
        self.assertFalse(any("可见对白未写" in i.get("evidence", "") for i in second["issues"]))
        shot["音色"] = "无"
        third = validate_storyboard.validate_project(current)
        self.assertTrue(any(i["error_code"] == "E-VOICE-001" and i["location"].endswith("音色") for i in third["issues"]))


if __name__ == "__main__":
    unittest.main()
