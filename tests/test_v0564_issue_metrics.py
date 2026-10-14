"""Evidence-aware classification must never manufacture a pass or a rate."""
import copy
import unittest

from shortdrama_mcp.issue_metrics import (
    classify_issue, classify_report_issues, issue_metric_key, summarize_issue_metrics,
)


def finding(**changes):
    return {"error_code": "E-TIMELINE-001", "field": "timing",
            "location": "shots[1].timing", "shot_ids": ["EP36-001"],
            "evidence": "两段发声实际重叠：0.5至1.0秒。", "status": "initial", **changes}


def event(event_id="EVT-1", issues=None, **changes):
    return {"event_id": event_id, "timestamp": f"2026-10-14T00:00:0{event_id[-1]}Z",
            "episode_id": "EP36", "file_fingerprint": "f" * 64,
            "source_version": "v1", "audit_round": 1, "issues": [finding()] if issues is None else issues,
            **changes}


def ledger(*events):
    return {"project_id": "SBP-test", "events": list(events), "issues": []}


class IssueClassificationTests(unittest.TestCase):
    def test_machine_failure_is_only_a_content_candidate(self):
        value = classify_issue(finding(severity="fail", blocking=True))
        self.assertEqual(value["category"], "content_candidate")
        self.assertFalse(value["content_confirmed"])
        self.assertFalse(value["changes_audit_verdict"])
        self.assertEqual(value["generation_effect"], "not_measured")

    def test_confirmed_content_requires_bound_per_finding_semantic_evidence(self):
        issue = finding(issue_source="independent_semantic_audit", semantic_issue_id="Q001",
                        semantic_layer="时长节奏", evidence_ids=["shot:EP36-001"])
        source = event(audit_class="independent_audit", semantic_audit_sha256="a" * 64)
        self.assertEqual(classify_issue(issue, event=source)["category"], "confirmed_content")
        for field in ("issue_source", "semantic_issue_id", "semantic_layer", "evidence_ids", "shot_ids", "evidence"):
            with self.subTest(field=field):
                incomplete = copy.deepcopy(issue)
                incomplete.pop(field)
                self.assertNotEqual(classify_issue(incomplete, event=source)["category"], "confirmed_content")
        self.assertNotEqual(classify_issue(issue)["category"], "confirmed_content")

    def test_unknown_and_missing_evidence_never_become_harmless(self):
        for issue in (finding(error_code="E-NEW-999"), finding(evidence=""),
                      finding(evidence="时间安排依据不足，无法判断")):
            result = classify_issue(issue)
            self.assertEqual(result["category"], "missing_evidence")
            self.assertFalse(result["content_confirmed"])

    def test_literal_format_not_actual_music_conflict(self):
        fixed = finding(error_code="E-AUDIO-STAGE-001", field="生成限制",
                        evidence="单镜负面音乐限制必须统一逐字写为‘禁止额外配乐’。")
        self.assertEqual(classify_issue(fixed)["category"], "format")
        conflict = {**fixed, "evidence": "此镜同时要求添加配乐并禁止配乐，发生冲突。"}
        self.assertEqual(classify_issue(conflict)["category"], "content_candidate")

    def test_negated_only_fragment_is_suspected_not_confirmed_false_positive(self):
        issue = finding(error_code="E-FIELD-CONTENT-001", field="saved_keyframe_static",
                        evidence="检查实际F的静态指令、当前时刻和独立定位：['eye_process=不定格于半眨眼状态']")
        self.assertEqual(classify_issue(issue)["category"], "suspected_false_positive")
        self.assertFalse(classify_issue(issue)["false_positive_confirmed"])
        mixed = {**issue, "evidence": "检查实际F的静态指令、当前时刻和独立定位：['eye_process=不定格于半眨眼状态', 'action=转头再走']"}
        self.assertEqual(classify_issue(mixed)["category"], "content_candidate")

    def test_workflow_and_identity_unknown_are_not_content_errors(self):
        for code, field, evidence in (
            ("E-VERSION-001", "project_version", "expected R10 got None"),
            ("E-SOURCE-001", "source_lock", "Source Lock缺失"),
            ("E-GATE-001", "workflow_profile/prevention_evidence", "profile missing"),
            ("E-AUDIT-COVERAGE-UNKNOWN", "audit_coverage", "unresolved"),
            ("E-VIS-001", "可见主体", "input_mapping：肩线身份类型未登记，须核验"),
        ):
            with self.subTest(code=code):
                result = classify_issue(finding(error_code=code, field=field, evidence=evidence))
                self.assertEqual(result["category"], "missing_evidence")


class IssueAggregationTests(unittest.TestCase):
    def test_same_problem_counts_once_retains_every_event_reference(self):
        data = ledger(event("EVT-1"), event("EVT-2"), event("EVT-3"))
        frozen = copy.deepcopy(data)
        result = summarize_issue_metrics(data, include_history=True)
        self.assertEqual(result["historical_observation_count"], 3)
        self.assertEqual(result["unique_file_version_problem_count"], 1)
        row = result["problems"][0]
        self.assertEqual((row["observation_count"], row["distinct_audit_count"]), (3, 3))
        self.assertEqual(len(row["observation_references"]), 3)
        self.assertEqual(row["discovery_state"], "reobserved")
        self.assertEqual(data, frozen)

    def test_different_shots_fields_roots_and_source_occurrences_are_not_merged(self):
        base = finding()
        original = issue_metric_key("P", event(), base)
        variants = [
            {"shot_ids": ["EP36-002"]}, {"field": "对白"},
            {"root_cause": "another causal condition"},
            {"evidence": "旁白没有给足时间。"},
            {"source_occurrence_id": "source-occurrence:2"},
            {"location": "shots[1].timing.second_voice"},
        ]
        for change in variants:
            with self.subTest(change=change):
                self.assertNotEqual(original, issue_metric_key("P", event(), {**base, **change}))

    def test_requirement_identity_survives_rephrasing_but_not_subject_change(self):
        base = finding(requirement_id="single_voice_per_interval", subject_id="speakerA")
        original = issue_metric_key("P", event(), base)
        rephrased = {**base, "evidence": "同一个时间段存在同时发声。", "location": "shots[7].timing"}
        self.assertEqual(original, issue_metric_key("P", event(), rephrased))
        self.assertNotEqual(original, issue_metric_key("P", event(), {**rephrased, "subject_id": "speakerB"}))

    def test_file_version_evidence_gaps_do_not_multiply_by_coverage_shot_list(self):
        a = finding(error_code="E-AUDIT-COVERAGE-UNKNOWN", field="audit_coverage",
                    shot_ids=["EP36-001"], evidence="missing obligations 1")
        b = {**a, "shot_ids": ["EP36-001", "EP36-002"], "evidence": "missing obligations 2"}
        result = summarize_issue_metrics(ledger(event("EVT-1", [a]), event("EVT-2", [b])))
        self.assertEqual(result["file_version_evidence_gap_count"], 1)
        self.assertEqual(result["problems"][0]["observation_count"], 2)
        other_version = event("EVT-3", [a], source_version="v2")
        result = summarize_issue_metrics(ledger(event("EVT-1", [a]), other_version))
        self.assertEqual(result["file_version_evidence_gap_count"], 2)

    def test_missing_fingerprints_do_not_deduplicate_by_filename(self):
        a = event("EVT-1", file_fingerprint="", file_name="same.xlsx")
        b = event("EVT-2", file_fingerprint="", file_name="same.xlsx")
        result = summarize_issue_metrics(ledger(a, b))
        self.assertEqual(result["unique_file_version_problem_count"], 2)
        self.assertEqual(result["unbound_identity_observation_count"], 2)
        self.assertEqual(a["file_fingerprint"], "")

    def test_legacy_introduced_does_not_establish_repair_causation(self):
        result = summarize_issue_metrics(ledger(event(issues=[finding(status="introduced")])))
        self.assertIsNone(result["repair_attribution"]["confirmed_introduced_count"])
        self.assertEqual(result["repair_attribution"]["legacy_introduced_observations_requiring_proof"], 1)
        self.assertEqual(result["problems"][0]["repair_attribution"], "not_established")
        self.assertEqual(result["problems"][0]["discovery_state"], "newly_discovered")

    def test_denominators_and_pagination_are_explicit_and_do_not_change_totals(self):
        a = finding()
        b = finding(error_code="E-PUNCT-001", field="对白", evidence="引号格式")
        data = ledger(event("EVT-1", [a, b]), event("EVT-2", [a]))
        result = summarize_issue_metrics(data, limit=1)
        self.assertEqual(len(result["problems"]), 1)
        self.assertEqual(result["next_cursor"], 1)
        rows = {row["category"]: row for row in result["categories"]}
        self.assertEqual(rows["content_candidate"]["observation_share"]["denominator"], 3)
        self.assertEqual(rows["content_candidate"]["observation_share"]["percent"], 66.67)
        self.assertEqual(rows["content_candidate"]["unique_problem_share"]["percent"], 50.0)
        second = summarize_issue_metrics(data, limit=1, cursor=1)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(second["categories"], result["categories"])

    def test_filtered_episodes_use_actual_shot_scope_not_all_project_alias(self):
        result = summarize_issue_metrics(ledger(event(episode_id="全项目", issues=[
            finding(), finding(shot_ids=["EP37-001"])])), episode_ids=["EP36"])
        self.assertEqual(result["historical_observation_count"], 1)
        self.assertEqual(result["problems"][0]["shot_ids"], ["EP36-001"])

    def test_no_adjudication_or_generation_proof_means_null_rates_even_when_empty(self):
        for data in (ledger(), ledger(event()), ledger(event(issues=[]))):
            result = summarize_issue_metrics(data)
            for name, metric in result["quality_metrics"].items():
                if isinstance(metric, dict):
                    with self.subTest(name=name):
                        self.assertIsNone(metric["percent"])
                        self.assertTrue(metric["unavailable_reason"])
            self.assertIsNone(result["quality_metrics"]["image_or_video_generation_failure_rate"])

    def test_compact_report_view_preserves_input_and_uses_report_denominator(self):
        report = {"status": "fail_current", "issues": [finding()]}
        before = copy.deepcopy(report)
        value = classify_report_issues(report)
        self.assertEqual(value["observation_count"], 1)
        self.assertEqual(value["classification"][0]["category"], "content_candidate")
        self.assertIsNone(value["generation_failure_rate"])
        self.assertEqual(report, before)

    def test_confirmed_shot_rate_requires_complete_consistent_bound_coverage(self):
        issue = finding(issue_source="independent_semantic_audit", semantic_issue_id="Q001",
                        semantic_layer="时长节奏", evidence_ids=["shot:EP36-001"])
        quality = {"independent_content_coverage_verified": True, "total_shot_count": 2,
                   "completed_shot_count": 2, "confirmed_problem_shot_ids": ["EP36-001"],
                   "confirmed_problem_shot_count": 1}
        source = event(issues=[issue], audit_class="independent_audit",
                       semantic_audit_sha256="a" * 64, first_delivery_quality=quality)
        result = summarize_issue_metrics(ledger(source))
        rate = result["quality_metrics"]["confirmed_content_shot_rate"]
        self.assertEqual((rate["numerator"], rate["denominator"], rate["percent"]), (1, 2, 50.0))
        # Re-audits of the exact same file/version do not inflate the denominator.
        repeat = {**source, "event_id": "EVT-2", "timestamp": "2026-10-14T00:00:02Z"}
        result = summarize_issue_metrics(ledger(source, repeat))
        self.assertEqual(result["quality_metrics"]["confirmed_content_shot_rate"]["denominator"], 2)
        carried_forward = {**repeat, "audit_round": 2}
        result = summarize_issue_metrics(ledger(source, carried_forward))
        self.assertIsNone(result["quality_metrics"]["confirmed_content_shot_rate"]["percent"])
        self.assertEqual(result["quality_metrics"]["confirmed_content_shot_rate"]["unavailable_reason"],
                         "latest_round_has_no_current_bound_review_quality")
        for change in ({"completed_shot_count": 1}, {"confirmed_problem_shot_ids": []},
                       {"independent_content_coverage_verified": False}):
            result = summarize_issue_metrics(ledger({**source, "first_delivery_quality": {**quality, **change}}))
            self.assertIsNone(result["quality_metrics"]["confirmed_content_shot_rate"]["percent"])
        filtered = summarize_issue_metrics(ledger(source), error_codes=["E-TIMELINE-001"])
        self.assertIsNone(filtered["quality_metrics"]["confirmed_content_shot_rate"]["percent"])


if __name__ == "__main__":
    unittest.main()
