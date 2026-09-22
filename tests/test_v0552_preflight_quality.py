"""Machine diagnostics neither certify semantic accuracy nor rewrite history."""
import copy
import unittest

from shortdrama_mcp.quality import machine_preflight_quality
from shortdrama_mcp.service import _draft_quality_metrics


class PreflightQualityTests(unittest.TestCase):
    def test_scene_and_full_clean_diagnostics_have_no_semantic_score(self):
        candidate = {"shots": [{"镜头编号": "EP01-001", "episode_id": "EP01"}]}
        score = _draft_quality_metrics(candidate, [])
        before = copy.deepcopy(score)
        for scope in ("scene", "full"):
            quality = machine_preflight_quality(score, scope_kind=scope)
            self.assertIsNone(quality["first_draft_correctness_percent"])
            self.assertIsNone(quality["direct_usability_percent"])
            self.assertEqual(quality["episode_correctness_percent"], {"EP01": None})
            self.assertEqual(quality["grade"], "unavailable")
            self.assertFalse(quality["commit_eligible"])
            self.assertFalse(quality["independent_content_coverage_verified"])
            diagnostics = quality["diagnostic_summary"]
            self.assertEqual(diagnostics["no_reported_content_defect_shot_percent"], 100)
            self.assertEqual(diagnostics["machine_draft_admission_eligible"], scope == "full")
        self.assertEqual(score, before)

    def test_empty_and_blocked_candidates_cannot_admit_a_draft(self):
        for candidate, issues in (({"shots": []}, []),
                ({"shots": [{"镜头编号": "EP01-001"}]},
                 [{"error_code": "E-SOURCE-001", "shot_ids": ["EP01-001"], "severity": "fail"}])):
            score = _draft_quality_metrics(candidate, issues)
            quality = machine_preflight_quality(score, scope_kind="full")
            self.assertFalse(quality["diagnostic_summary"]["machine_draft_admission_eligible"])
            self.assertEqual(quality["issue_point_count"], len(issues))


if __name__ == "__main__":
    unittest.main()
