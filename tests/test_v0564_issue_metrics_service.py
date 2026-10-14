"""Read-only service integration cannot turn classifications into admission."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from shortdrama_mcp.issue_metrics import classify_issue
from shortdrama_mcp.service import StoryboardService
from test_v0564_issue_metrics import event, finding, ledger


class IssueMetricsServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = StoryboardService.__new__(StoryboardService)
        self.service.settings = SimpleNamespace(acceptance_control_enabled=False)
        self.service.bootstrap = {"version": "test", "workflow_revision": "test"}
        self.service._ruleset_digest = "e" * 64
        for name in ("ruleset", "validator", "runtime", "schema"):
            setattr(self.service, "_" + name + "_fingerprint", "e" * 64)

    def query(self, data, *, status="all"):
        self.service.store = Mock()
        self.service.store.load_ledger.return_value = data
        profile = {"patterns": [], "blocking_pattern_count": 0}
        with patch("shortdrama_mcp.service.storyboard_agent.active_prevention_profile", return_value=profile), \
             patch("shortdrama_mcp.service.storyboard_agent.summarize", return_value={"legacy": True}):
            return self.service.query_project_issues(
                "test", project_id="SBP-test", status=status, error_codes=None, episode_ids=None,
                recurrence_states=None, minimum_occurrence_count=0, include_history=True, limit=100, cursor=0)

    def test_query_preserves_original_open_status_and_blocking_observations(self):
        issue = finding(error_code="E-AUDIO-STAGE-001", field="生成限制", blocking=True,
                        evidence="单镜负面音乐限制必须统一逐字写为‘禁止额外配乐’。")
        data = ledger(event("EVT-1", [issue]), event("EVT-2", [issue]))
        data["issues"] = [{"issue_id": "ISS-1", "error_code": issue["error_code"], "status": "open",
                           "occurrence_count": 2, "history": [{"event_id": "EVT-1"}],
                           "first_seen_at": "2026-10-14", "last_seen_at": "2026-10-14", "blocking": True}]
        before = copy.deepcopy(data)
        result = self.query(data)
        self.assertEqual(result.status, "ok")
        self.assertEqual(data, before)
        self.assertEqual(result.data["issues"][0]["status"], "open")
        self.assertTrue(result.data["issues"][0]["blocking"])
        projected = result.data["evidence_metrics"]
        self.assertEqual(projected["historical_observation_count"], 2)
        self.assertEqual(projected["unique_file_version_problem_count"], 1)
        self.assertEqual(projected["problems"][0]["latest_classification"]["category"], "format")
        self.assertFalse(projected["changes_audit_verdict"])
        self.service.store.load_ledger.assert_called_once_with("test", "SBP-test")
        self.assertEqual(set(self.service.store.method_calls[0][0:1]), {"load_ledger"})
        self.assertEqual(len(self.service.store.method_calls), 1)

    def test_legacy_status_filter_does_not_silently_relabel_historical_denominator(self):
        data = ledger(event())
        data["issues"] = [{"issue_id": "ISS-1", "error_code": "E-TIMELINE-001", "status": "resolved",
                           "occurrence_count": 1}]
        result = self.query(data, status="open")
        self.assertEqual(result.data["issues"], [])
        self.assertEqual(result.data["total"], 0)
        metric = result.data["evidence_metrics"]
        self.assertEqual(metric["historical_observation_count"], 1)
        self.assertFalse(metric["scope"]["legacy_status_filter_applied"])
        self.assertTrue(result.data["legacy_metrics_non_causal"])

    def test_diagnosis_does_not_certify_missing_evidence_or_suspected_false_positive(self):
        board = {"shots": [{"镜头编号": "EP36-001", "episode_id": "EP36"}]}
        negative = finding(error_code="E-FIELD-CONTENT-001", field="saved_keyframe_static",
                           evidence="检查实际F的静态指令、当前时刻和独立定位：['eye_process=不定格于半眨眼状态']")
        unknown = finding(error_code="E-SOURCE-001", field="source_lock", location="source_lock",
                          shot_ids=[], evidence="Source Lock缺失")
        report = {"status": "fail_current", "issues": [negative, unknown]}
        before = copy.deepcopy(report)
        with patch.object(self.service, "_storyboard_from_artifact", return_value=(board, {"fingerprint": "f" * 64})), \
             patch.object(self.service, "_validator_version", return_value="test-validator"), \
             patch("shortdrama_mcp.service.validate_storyboard.validate_project", return_value=report):
            result = self.service.diagnose_storyboard_artifact("test", "SBP-test", "ART-test")
        self.assertEqual(result.status, "diagnostic_completed")
        self.assertEqual(result.data["formal_audit_status"], "not_performed")
        self.assertFalse(result.data["certificate_created"])
        self.assertIsNone(result.data["raw_generation_quality"])
        classifications = result.data["issue_classification"]["classification"]
        if "unscoped_issue_classification" in result.data:
            classifications += result.data["unscoped_issue_classification"]["classification"]
        self.assertEqual({row["category"] for row in classifications},
                         {"missing_evidence", "suspected_false_positive"})
        self.assertTrue(all(not row["content_confirmed"] for row in classifications))
        self.assertEqual(report, before)

    def audit_content(self, issue):
        board = {"source_lock": {"episode_id": "EP36"},
                 "shots": [{"镜头编号": "EP36-001", "shot_uid": "EP36-001", "episode_id": "EP36"}]}
        return self.service._audit_event_content(
            project_id="SBP-test", artifact_record={"file_name": "test.xlsx", "fingerprint": "f" * 64, "version": "v1"},
            storyboard=board, report={"issues": [issue]}, ledger=ledger(), cycle_id="AUD-test",
            audit_class="production_preflight", audit_cache_key=None, prior_events=[], audit_round=1)

    def test_event_classification_does_not_remove_original_failure(self):
        issue = finding(error_code="E-AUDIO-STAGE-001", field="生成限制", blocking=True,
                        evidence="单镜负面音乐限制必须统一逐字写为‘禁止额外配乐’。")
        content = self.audit_content(issue)
        self.assertEqual(content["result"], "fail_current")
        self.assertTrue(content["issues"][0]["blocking"])
        self.assertEqual(classify_issue(content["issues"][0])["category"], "format")

    def test_event_keeps_validated_provenance_but_does_not_invent_it_for_legacy(self):
        issue = finding(issue_source="independent_semantic_audit", semantic_issue_id="Q001",
                        semantic_layer="时长节奏", evidence_ids=["shot:EP36-001"])
        content = self.audit_content(issue)
        row = content["issues"][0]
        for field in ("issue_source", "semantic_issue_id", "semantic_layer", "evidence_ids"):
            self.assertEqual(row[field], issue[field])
        # An otherwise valid semantic record still cannot claim independent
        # confirmation without the signed event's independent binding context.
        self.assertNotEqual(classify_issue(row, event=content)["category"], "confirmed_content")
        old = self.audit_content(finding())["issues"][0]
        for field in ("issue_source", "semantic_issue_id", "semantic_layer"):
            self.assertNotIn(field, old)


if __name__ == "__main__":
    unittest.main()
