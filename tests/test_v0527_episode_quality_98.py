"""Public count-based controls for per-episode quality, not semantic fixtures."""
import copy
import unittest

from shortdrama_mcp.quality import measure_quality


def episodes(**sizes):
    return {"shots": [
        {"镜头编号": f"{episode}-{index:03}", "shot_uid": f"{episode}-uid-{index}",
         "episode_id": episode}
        for episode, size in sizes.items() for index in range(1, size + 1)
    ]}


def finding(ids, code="E-LIGHT-002", **extra):
    return {"error_code": code, "shot_ids": list(ids), "severity": "warning",
            "blocking": False, "field": "public-count-control", **extra}


def score(storyboard, issues=()):
    return measure_quality(storyboard, list(issues),
                           resolve_ids=lambda obj, issue: issue.get("shot_ids", []),
                           pattern_key=lambda issue: issue["error_code"])


class EpisodeQuality98Tests(unittest.TestCase):
    def test_clean_explicit_episodes_reach_diagnostic_A(self):
        q = score(episodes(EP01=27, EP02=31))
        self.assertEqual(q["grade"], "A")
        self.assertEqual(q["episode_correctness_percent"], {"EP01": 100, "EP02": 100})
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": 100, "EP02": 100})
        self.assertEqual(q["minimum_episode_direct_usability_percent"], 100)
        self.assertEqual(q["final_independent_audit_required_percent"], 100)

    def test_overall_98_cannot_hide_an_episode_at_96(self):
        q = score(episodes(EP01=25, EP02=75), [finding(["EP01-001", "EP02-001"])])
        self.assertEqual(q["first_draft_correctness_percent"], 98)
        self.assertEqual(q["direct_usability_percent"], 98)
        self.assertEqual(q["episode_correctness_percent"]["EP01"], 96)
        self.assertEqual(q["grade"], "B")
        self.assertFalse(q["commit_eligible"])

    def test_episode_output_contract_failure_does_not_hide_behind_correctness(self):
        q = score(episodes(EP01=25, EP02=75), [finding(["EP01-001"], "E-FIELDORDER-001")])
        self.assertEqual(q["first_draft_correctness_percent"], 100)
        self.assertEqual(q["minimum_episode_correctness_percent"], 100)
        self.assertEqual(q["direct_usability_percent"], 99)
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": 96, "EP02": 100})
        self.assertEqual(q["grade"], "B")

    def test_exact_98_in_each_episode_meets_numeric_A_but_not_zero_issue_commit(self):
        q = score(episodes(EP01=50, EP02=50), [finding(["EP01-001", "EP02-001"])])
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": 98, "EP02": 98})
        self.assertEqual(q["grade"], "A")
        self.assertFalse(q["commit_eligible"])

    def test_rounding_overall_97_9996_to_98_does_not_meet_A(self):
        # One diagnosis affects 100 distinct shots. It is not 100 repeated
        # diagnostics, so this isolates the exact percentage comparison.
        affected = [f"EP01-{index:03}" for index in range(1, 101)]
        q = score(episodes(EP01=4999), [finding(affected)])
        self.assertEqual(q["direct_usability_percent"], 98.0)
        self.assertEqual(q["new_local_issue_shot_rate_percent"], 2.0)
        self.assertEqual(q["systemic_duplicate_count"], 0)
        self.assertEqual(q["grade"], "B")

    def test_rounded_episode_below_98_is_not_rescued_by_clean_second_episode(self):
        affected = [f"EP01-{index:03}" for index in range(1, 101)]
        q = score(episodes(EP01=4999, EP02=5001), [finding(affected, "E-FIELDORDER-001")])
        self.assertEqual(q["direct_usability_percent"], 99)
        self.assertEqual(q["first_draft_correctness_percent"], 100)
        self.assertEqual(q["minimum_episode_direct_usability_percent"], 98.0)
        self.assertEqual(q["grade"], "B")

    def test_unlocated_contract_fault_keeps_episode_usability_unknown(self):
        q = score(episodes(EP01=27, EP02=31), [finding([], "E-TEMPLATE-001")])
        self.assertEqual(q["first_draft_correctness_percent"], 100)
        self.assertIsNone(q["direct_usability_percent"])
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": None, "EP02": None})
        self.assertIsNone(q["minimum_episode_direct_usability_percent"])
        self.assertEqual(q["grade"], "unavailable")

    def test_unlocated_source_fault_is_unknown_not_zero_or_perfect(self):
        q = score(episodes(EP01=27, EP02=31), [finding([], "E-SOURCE-001")])
        self.assertIsNone(q["first_draft_correctness_percent"])
        self.assertIsNone(q["direct_usability_percent"])
        self.assertEqual(q["episode_correctness_percent"], {"EP01": None, "EP02": None})
        self.assertFalse(q["commit_eligible"])

    def test_global_blocker_prevents_A_without_inventing_content_defects(self):
        q = score(episodes(EP01=27, EP02=31), [finding([], "E-AUDIT-001", severity="error", blocking=True)])
        self.assertEqual(q["direct_usability_percent"], 100)
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": 100, "EP02": 100})
        self.assertEqual(q["hard_or_major_issue_count"], 1)
        self.assertEqual(q["workflow_issue_count"], 1)
        self.assertEqual(q["grade"], "C")
        self.assertFalse(q["commit_eligible"])

    def test_registered_shot_numbers_preserve_episode_scope_when_metadata_missing(self):
        draft = episodes(EP01=25, EP02=75)
        for shot in draft["shots"]:
            shot.pop("episode_id")
        q = score(draft, [finding(["EP01-001"])])
        self.assertEqual(q["direct_usability_percent"], 99)
        self.assertEqual(q["episode_direct_usability_percent"], {"EP01": 96, "EP02": 100})
        self.assertEqual(q["grade"], "B")

    def test_conflicting_explicit_episode_and_number_cannot_certify_episode_A(self):
        draft = episodes(EP01=2)
        draft["shots"][0]["episode_id"] = "EP02"
        q = score(draft)
        self.assertIsNone(q["minimum_episode_correctness_percent"])
        self.assertIsNone(q["minimum_episode_direct_usability_percent"])
        self.assertEqual(q["grade"], "unavailable")

    def test_unsegmented_clean_input_is_not_a_proven_per_episode_A(self):
        q = score({"shots": [{"镜头编号": "public-shot", "shot_uid": "stable-public-shot"}]})
        self.assertEqual(q["direct_usability_percent"], 100)
        self.assertEqual(q["episode_direct_usability_percent"], {"全项目": None})
        self.assertEqual(q["grade"], "unavailable")

    def test_invalid_identity_cannot_create_known_episode_minimum(self):
        draft = episodes(EP01=2)
        draft["shots"][1] = copy.deepcopy(draft["shots"][0])
        q = score(draft)
        self.assertIsNone(q["direct_usability_percent"])
        self.assertIsNone(q["minimum_episode_direct_usability_percent"])
        self.assertEqual(q["grade"], "unavailable")


if __name__ == "__main__":
    unittest.main()
