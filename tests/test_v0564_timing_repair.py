"""Planning evidence and repair dependencies never claim media was rendered."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "skills/shortdrama-storyboard/scripts"), str(ROOT / "tests")]

from timing_model import validate_timing, _profile
from test_timing_model import RULES, project, shot
from shortdrama_mcp.repair_dependencies import repair_dependency_plan


def overlapping_plan():
    row = shot("请坐。", seconds=4, picture='甲说道：“请坐。”')
    row["心理音轨"] = '心理旁白：“先等等。”；不驱动口型。'
    timing = {"contract": "shot-timing-v1", "events": [], "utterances": [
        {"id": "spoken", "kind": "dialogue", "speaker": "甲",
         "source": {"shot_id": "EP01-001", "dialogue_index": 0},
         "segments": [{"shot_id": "EP01-001", "start": 0, "end": 1, "char_range": [0, 3]}]},
        {"id": "inner", "kind": "psychological", "speaker": "甲",
         "source": {"shot_id": "EP01-001"},
         "segments": [{"shot_id": "EP01-001", "start": 0, "end": 1, "char_range": [0, 4]}]},
    ]}
    return project([row], timing)


class TimingEvidenceTests(unittest.TestCase):
    def test_explicit_overlap_is_blocked_as_plan_not_actual_audio(self):
        data = overlapping_plan(); before = copy.deepcopy(data)
        result = validate_timing(data, data["shots"], RULES)
        overlap = [row for row in result["faults"] if row["timing_evidence_kind"] == "declared_schedule_overlap"]
        self.assertEqual(len(overlap), 1, result)
        self.assertEqual(overlap[0]["utterance_ids"], ["spoken", "inner"])
        self.assertEqual(overlap[0]["overlap_seconds"], 1)
        self.assertFalse(overlap[0]["actual_audio_verified"])
        self.assertNotIn("实际重叠", overlap[0]["message"])
        self.assertEqual(result["metrics"]["planning_summary"]["declared_overlap_count"], 1)
        self.assertEqual(data, before)

    def test_inferred_overlap_and_capacity_are_marked_estimates(self):
        data = overlapping_plan()
        data["prevention_evidence"]["audio_workflow"].clear()
        data["shots"][0]["画面内容"] = '甲说道：“' + '完整原句' * 12 + '。”'
        before = copy.deepcopy(data)
        result = validate_timing(data, data["shots"], RULES)
        summary = result["metrics"]["planning_summary"]
        self.assertEqual(summary["capacity_budget_fault_count"], 1)
        self.assertEqual(summary["estimated_overlap_count"], 1)
        self.assertEqual(summary["actual_audio_measurement_status"], "not_provided")
        self.assertFalse(summary["actual_audio_verified"])
        self.assertFalse(summary["grant_approval"])
        self.assertEqual(data, before)

    def test_declared_spans_bind_exact_utterance_and_source(self):
        data = overlapping_plan()
        result = validate_timing(data, data["shots"], RULES)
        span = result["metrics"]["utterances"][0]
        self.assertEqual(span["source_text_sha256"], hashlib.sha256("请坐。".encode()).hexdigest())
        self.assertEqual(span["source_shot_id"], "EP01-001")
        data["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]["source"]["dialogue_index"] = 5
        invalid = validate_timing(data, data["shots"], RULES)
        self.assertTrue(any("源文引用" in row["message"] for row in invalid["faults"]))

    def test_self_reported_measurement_or_replacement_text_cannot_pass(self):
        for key, value in (("actual_audio_verified", True), ("measured_seconds", .1), ("text", "删减后的台词")):
            for level in ("utterance", "segment"):
                with self.subTest(key=key, level=level):
                    data = overlapping_plan()
                    item = data["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]
                    target = item if level == "utterance" else item["segments"][0]
                    target[key] = value
                    result = validate_timing(data, data["shots"], RULES)
                    self.assertTrue(any("不接受" in row["message"] for row in result["faults"]))
                    self.assertFalse(result["metrics"]["actual_audio_verified"])

    def test_reviewed_profile_is_not_this_candidates_measured_audio(self):
        data = project([shot("请坐。", seconds=4)])
        rules = copy.deepcopy(RULES)
        profile = next(row for row in rules["timing_policy"]["profiles"] if row["id"] == rules["timing_policy"]["default_profile_id"])
        profile.update(evidence_status="measured", voice_spec_sha256=hashlib.sha256(data["shots"][0]["音色"].encode()).hexdigest())
        profile["evidence"] = {"source_kind": "reviewed_audio", "sample_sha256": "a" * 64,
                               "alignment_verified": True, "reviewed": True,
                               "model_version": "fixture", "voice": "fixture"}
        result = validate_timing(data, data["shots"], rules)
        self.assertFalse(result["faults"], result)
        self.assertEqual(result["metrics"]["utterances"][0]["profile_evidence_status"], "measured")
        self.assertFalse(result["metrics"]["actual_audio_verified"])
        for key, value in (("reviewed", "false"), ("alignment_verified", 1), ("sample_sha256", "claimed")):
            bad_rules = copy.deepcopy(rules)
            current = next(row for row in bad_rules["timing_policy"]["profiles"] if row["id"] == profile["id"])
            current["evidence"][key] = value
            with self.assertRaises(ValueError):
                _profile(bad_rules)

    def test_authoring_preflight_exposes_summary_and_not_checked(self):
        from authoring_preflight import inspect_typed_decisions
        from test_v0527_authoring_serializer import decisions
        data = decisions(); before = copy.deepcopy(data)
        result = inspect_typed_decisions(data)
        self.assertIn("summary", result["timing_diagnostics"], result)
        self.assertFalse(result["timing_diagnostics"]["summary"]["grant_approval"])
        self.assertEqual(data, before)
        data["shots"][0]["duration"] = True
        incomplete = inspect_typed_decisions(data)
        self.assertEqual(incomplete["timing_diagnostics"]["status"], "not_checked")

    def test_failed_first_authoring_attempt_retains_original_and_timing_diagnostics(self):
        import json
        import tempfile
        from author_storyboard import record_attempt
        from test_v0527_authoring_serializer import decisions
        data = decisions()
        original_line = "请完整保留原句" * 10 + "。"
        data["shots"][0]["phases"][1]["text"] = f'甲说道：“{original_line}”'
        data["shots"][0]["events"] = []
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "input.json"; source.write_bytes(raw)
            report = record_attempt(source, root / "attempts", attempt_id="capacity-failure")
            attempt = root / "attempts" / "capacity-failure"
            self.assertEqual(report["status"], "rejected", report)
            self.assertEqual((attempt / "input.original.json").read_bytes(), raw)
            self.assertFalse((attempt / "canonical.json").exists())
            diagnostics = json.loads((attempt / "check_coverage.json").read_text())["timing_diagnostics"]
            self.assertGreater(diagnostics["summary"]["capacity_budget_fault_count"], 0)
            self.assertEqual(diagnostics["summary"]["status"], "blocked")
            self.assertFalse(diagnostics["summary"]["actual_audio_verified"])


def candidate():
    return {"shots": [{"shot_uid": f"U{i}", "镜头编号": f"EP01-{i:03d}",
                       "时长": 4.0, "累计时长": i * 4.0, "画面内容": '甲说道：“请坐。”',
                       "场景连续性ID": "S1", "空间站位描述": "甲站桌旁。", "音色": "平和。"}
                      for i in range(1, 5)]}


class RepairDependencyTests(unittest.TestCase):
    def test_dialogue_change_rechecks_voice_mouth_listener_timing_without_mutation(self):
        before = candidate(); after = copy.deepcopy(before)
        after["shots"][1]["画面内容"] = '甲说道：“请你坐下。”'
        snapshots = copy.deepcopy((before, after))
        plan = repair_dependency_plan(before, after)
        changed = plan["changed_shots"][0]
        self.assertIn("dialogue_or_speaker_changed", changed["reasons"])
        self.assertTrue({"voice_delivery", "speaker_lip_sync", "listener_reaction", "speech_timeline_and_capacity", "source_dialogue_and_knowledge"} <= set(changed["required_rechecks"]))
        self.assertEqual(plan["preserved_shot_uids"], ["U1", "U3", "U4"])
        self.assertEqual((before, after), snapshots)
        self.assertTrue(plan["full_candidate_regression_required"])
        self.assertEqual(plan["required_audit_layer_count"], 8)
        self.assertFalse(plan["grant_approval"])

    def test_staging_change_rechecks_both_neighbors_and_camera_keyframe(self):
        before = candidate(); after = copy.deepcopy(before)
        after["shots"][1]["空间站位描述"] = "甲站门旁。"
        row = repair_dependency_plan(before, after)["changed_shots"][0]
        self.assertEqual(row["adjacent_pairs"], [["U1", "U2"], ["U2", "U3"]])
        self.assertTrue({"camera_and_visibility", "static_keyframe", "actor_and_prop_state"} <= set(row["required_rechecks"]))

    def test_duration_shift_rechecks_downstream_without_marking_derived_time_as_rewrite(self):
        before = candidate(); after = copy.deepcopy(before)
        after["shots"][1]["时长"] = 4.5
        for row in after["shots"][1:]:
            row["累计时长"] += .5
        plan = repair_dependency_plan(before, after)
        self.assertEqual([row["shot_uid"] for row in plan["changed_shots"]], ["U2"])
        self.assertEqual(plan["derived_only_shot_uids"], ["U3", "U4"])
        self.assertTrue({"U2", "U3", "U4"} <= set(plan["recheck_shot_uids"]))

    def test_cross_shot_audio_closure_is_independent_of_utterance_order(self):
        before = candidate()
        def utterance(name, left, right):
            return {"id": name, "source": {"shot_id": f"EP01-{left:03d}"},
                    "segments": [{"shot_id": f"EP01-{right:03d}"}]}
        before["prevention_evidence"] = {"audio_workflow": {"timing": {"utterances": [
            utterance("later", 2, 4), utterance("earlier", 1, 2)]}}}
        after = copy.deepcopy(before); after["shots"][0]["音色"] = "压低声线。"
        row = repair_dependency_plan(before, after)["changed_shots"][0]
        self.assertEqual(row["linked_utterance_ids"], ["earlier", "later"])
        self.assertEqual(row["related_shot_uids"], ["U1", "U2", "U4"])

    def test_structural_or_timing_only_changes_cannot_evade_full_recheck(self):
        before = candidate(); after = copy.deepcopy(before)
        after["shots"].reverse()
        self.assertTrue(repair_dependency_plan(before, after)["structural_change"])
        after = copy.deepcopy(before)
        after["prevention_evidence"] = {"audio_workflow": {"timing": {"utterances": []}}}
        plan = repair_dependency_plan(before, after)
        self.assertEqual(plan["changed_context_blocks"], ["audio_workflow"])
        self.assertEqual(plan["recheck_shot_uids"], ["U1", "U2", "U3", "U4"])
        self.assertIn("speech_timeline_and_capacity", plan["required_rechecks"])
        self.assertFalse(plan["grant_approval"])


if __name__ == "__main__":
    unittest.main()
