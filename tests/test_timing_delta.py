"""Delta must not carry stale authored timing across execution edits."""
from __future__ import annotations

import copy
import unittest

from shortdrama_mcp.canonical import COMPACT_SHOT_FIELDS, compact_storyboard_canonical
from shortdrama_mcp.delta import (
    EVIDENCE_BLOCKS,
    TIMING_TRIGGER_FIELDS,
    ensure_shot_uids,
    merge_storyboard_delta,
)


def _base(*, timing=True):
    # This fixture exercises compact merge/evidence boundaries, not the full
    # content-quality validator. No service, runtime certificate or model call
    # is needed to prove a rejected merge leaves caller-owned data unchanged.
    shots = []
    for index in range(2):
        shot = {field: "无。" for field in COMPACT_SHOT_FIELDS}
        shot.update({
            "镜头编号": f"EP01-{index + 1:03d}", "时长": 4.0,
            "累计时长": 4.0 * (index + 1), "场景连续性ID": "EP01-SC01",
            "episode_id": "EP01", "画面内容": "人物抬眼后收手。",
        })
        shots.append(shot)
    value = {
        "schema_version": "R10.1", "shots": shots,
        "prevention_evidence": {
            "shot_plan": [{"shot_id": shot["镜头编号"], "main_action_stage": shot["画面内容"]} for shot in shots],
            "performance_chains": [{"shot_id": shot["镜头编号"], "body_or_breath": "自然呼吸"} for shot in shots],
            "scene_state_ledger": [{"shot_id": shot["镜头编号"], "scene_id": "EP01-SC01",
                                    "start_state": {"人物位置": "桌旁"},
                                    "end_state": {"人物位置": "桌旁"},
                                    "transition_explanation": "同场连续"} for shot in shots],
            "audio_workflow": {"voice": "原声线"},
            "protected_dialogue_fragments": [],
        },
    }
    if timing:
        value["prevention_evidence"]["audio_workflow"]["timing"] = {
            "contract": "shot-audio-timeline-v1", "utterances": [], "events": [],
        }
    return ensure_shot_uids(compact_storyboard_canonical(value))[0]


class TimingDeltaInvalidationTests(unittest.TestCase):
    def merge(self, base, shot, **kwargs):
        return merge_storyboard_delta(
            base, affected_shot_uids=[shot["shot_uid"]], replacement_shots=[shot], **kwargs,
        )

    def assert_timing_rejection(self, base, shot, **kwargs):
        base_before, shot_before = copy.deepcopy(base), copy.deepcopy(shot)
        with self.assertRaisesRegex(ValueError, "delta_timing_evidence_requires_full_commit") as result:
            self.merge(base, shot, **kwargs)
        self.assertIn("full production preflight in the current window", str(result.exception))
        self.assertEqual(base, base_before)
        self.assertEqual(shot, shot_before)

    def test_every_timing_dependent_execution_field_invalidates(self):
        for field in sorted(TIMING_TRIGGER_FIELDS):
            with self.subTest(field=field):
                base = _base()
                shot = copy.deepcopy(base["shots"][0])
                shot[field] = 4.5 if field == "时长" else f"更新{field}的执行描述。"
                self.assert_timing_rejection(base, shot)

    def test_phase_boundary_change_invalidates_unchanged_spoken_text(self):
        base = _base()
        base["shots"][0]["画面内容"] = "【0.0—1.0秒】抬眼。【1.0—3.0秒】江氏说道：“好。”【3.0—4.0秒】收手。"
        shot = copy.deepcopy(base["shots"][0])
        shot["画面内容"] = shot["画面内容"].replace("1.0", "1.5")
        self.assert_timing_rejection(base, shot)

    def test_voice_delivery_change_cannot_reuse_old_profile_timing(self):
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        shot["音色"] = "在句中另停0.8秒，尾音延长。"
        self.assert_timing_rejection(base, shot)

    def test_duration_before_cross_shot_psychological_audio_invalidates(self):
        base = _base()
        base["prevention_evidence"]["audio_workflow"]["timing"]["utterances"] = [{
            "id": "mental-1", "kind": "psychological", "speaker": "江氏",
            "sources": [{"shot_id": "EP01-001"}, {"shot_id": "EP01-002"}],
            "segments": [
                {"shot_id": "EP01-001", "start": 3.0, "end": 4.0, "char_range": [0, 4]},
                {"shot_id": "EP01-002", "start": 0.0, "end": 1.0, "char_range": [4, 8]},
            ],
        }]
        shot = copy.deepcopy(base["shots"][0])
        shot["时长"] = 3.5
        self.assert_timing_rejection(base, shot)

    def test_change_outside_referenced_audio_shots_still_invalidates_global_axis(self):
        base = _base()
        base["prevention_evidence"]["audio_workflow"]["timing"]["utterances"] = [{
            "id": "later-line", "kind": "dialogue", "speaker": "江氏",
            "source": {"shot_id": "EP01-002", "dialogue_index": 0},
            "segments": [{"shot_id": "EP01-002", "start": 0.0, "end": 1.0, "char_range": [0, 2]}],
        }]
        shot = copy.deepcopy(base["shots"][0])
        shot["时长"] = 4.5
        self.assert_timing_rejection(base, shot)

    def test_empty_and_malformed_authored_timing_do_not_bypass_invalidation(self):
        for value in ({}, [], None, False, ""):
            with self.subTest(value=value):
                base = _base()
                base["prevention_evidence"]["audio_workflow"]["timing"] = value
                shot = copy.deepcopy(base["shots"][0])
                shot["时长"] = 4.5
                self.assert_timing_rejection(base, shot)

    def test_source_fragment_dialogue_changes_still_take_existing_full_route(self):
        base = _base()
        base["shots"][0]["画面内容"] = "江氏说道：“大嫂，”"
        base["prevention_evidence"]["protected_dialogue_fragments"] = [{
            "occurrence_id": "source-occurrence:fixture", "shot_id": "EP01-001",
            "dialogue_index": 0, "char_range": [0, 3],
        }]
        shot = copy.deepcopy(base["shots"][0])
        shot["画面内容"] = "江氏说道：“大嫂。”"
        with self.assertRaisesRegex(ValueError, "delta_semantic_requires_full_commit"):
            self.merge(base, shot)

    def test_psychological_text_change_still_takes_existing_full_route(self):
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        shot["心理音轨"] = "江氏心想：事情不对。"
        with self.assertRaisesRegex(ValueError, "delta_semantic_requires_full_commit"):
            self.merge(base, shot)

    def test_static_lighting_edit_preserves_timing_and_existing_checks(self):
        for field in ("光影", "光源来源", "色温倾向"):
            with self.subTest(field=field):
                base = _base()
                shot = copy.deepcopy(base["shots"][0])
                shot[field] = "同一真实漫射光的静态等义描述。"
                merged, manifest = self.merge(base, shot)
                self.assertEqual(merged["shots"][0][field], shot[field])
                self.assertEqual(merged["prevention_evidence"]["audio_workflow"],
                                 base["prevention_evidence"]["audio_workflow"])
                self.assertTrue(manifest["global_evidence_hashes_verified"])

    def test_light_direction_still_requires_fresh_scene_state(self):
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        shot["光源方向"] = "左前侧略亮，向背景柔和衰减。"
        with self.assertRaisesRegex(ValueError, "delta_evidence_missing"):
            self.merge(base, shot)
        scene = copy.deepcopy(base["prevention_evidence"]["scene_state_ledger"][0])
        scene["transition_explanation"] = "同场同源；方向表述调整后仍按原物理状态连续。"
        merged, _manifest = self.merge(base, shot, evidence_updates={"scene_state_ledger": [scene]})
        self.assertEqual(merged["prevention_evidence"]["audio_workflow"],
                         base["prevention_evidence"]["audio_workflow"])

    def test_noop_with_timing_remains_eligible(self):
        base = _base()
        merged, _manifest = self.merge(base, copy.deepcopy(base["shots"][0]))
        self.assertEqual(merged["prevention_evidence"]["audio_workflow"],
                         base["prevention_evidence"]["audio_workflow"])

    def test_server_derived_cumulative_value_cannot_shift_actual_timeline(self):
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        shot["累计时长"] = 999.0
        merged, _manifest = self.merge(base, shot)
        self.assertEqual(merged["shots"][0]["累计时长"], 4.0)
        self.assertEqual(merged["shots"][1]["累计时长"], 8.0)

    def test_execution_edit_without_authored_timing_retains_existing_evidence_route(self):
        base = _base(timing=False)
        shot = copy.deepcopy(base["shots"][0])
        shot["画面内容"] = "人物先收手，再轻微抬眼。"
        performance = copy.deepcopy(base["prevention_evidence"]["performance_chains"][0])
        performance["body_or_breath"] = "收手时呼吸自然，随后抬眼。"
        merged, _manifest = self.merge(base, shot, evidence_updates={"performance_chains": [performance]})
        self.assertEqual(merged["shots"][0]["画面内容"], shot["画面内容"])

    def test_supplied_fresh_timing_does_not_expand_delta_evidence_authority(self):
        self.assertEqual(EVIDENCE_BLOCKS, {"shot_plan", "performance_chains", "scene_state_ledger"})
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        with self.assertRaisesRegex(ValueError, "delta_evidence_global_requires_full_commit"):
            self.merge(base, shot, evidence_updates={"audio_workflow": {"timing": {"fresh": True}}})

    def test_top_level_timing_override_cannot_expand_delta_authority(self):
        base = _base()
        shot = copy.deepcopy(base["shots"][0])
        with self.assertRaisesRegex(ValueError, "delta_top_level_requires_full_commit"):
            self.merge(base, shot, top_level_updates={"prevention_evidence": {"audio_workflow": {"timing": {}}}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
