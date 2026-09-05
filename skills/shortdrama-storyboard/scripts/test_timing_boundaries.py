#!/usr/bin/env python3
"""Observed timing-boundary regressions; fixture proofs are not calibration.

The measured-profile test registers a synthetic, unchanged-rate profile only
in its private in-memory rules copy. No production rules or source are edited.
"""
from __future__ import annotations

import copy
import hashlib
import unittest

from test_timing_model import PROFILE, RULES, project, report, shot
from timing_model import _phase_rows, validate_timing


def utterance_timing(*, start=.2, end=1.4, length=7, shot_id="EP01-001"):
    return {
        "contract": "shot-timing-v1",
        "utterances": [{
            "id": "u1", "kind": "dialogue", "speaker": "甲",
            "source": {"shot_id": "EP01-001", "dialogue_index": 0},
            "segments": [{"shot_id": shot_id, "start": start, "end": end, "char_range": [0, length]}],
        }],
        "events": [],
    }


def short_source_shot():
    return shot(seconds=4, picture=(
        "【0.0—0.8秒】甲看向乙。"
        "【0.8—1.0秒】甲说道：“请你马上回来。”"
        "【1.0—4.0秒】甲闭口站定。"
    ))


def prescribed_pause_shot():
    return shot("甲乙丙丁戊", seconds=2, picture=(
        "【0.0—0.2秒】甲看向乙。"
        "【0.2—1.8秒】甲说道：“甲乙丙丁戊”，甲说完后明确停顿2.0秒。"
        "【1.8—2.0秒】甲保持看向乙。"
    ))


class TimingBoundaryTests(unittest.TestCase):
    def assert_fault_contains(self, result, fragment):
        self.assertTrue(
            any(fragment in str(row.get("message") or row.get("evidence") or "") for row in result["faults"]),
            result,
        )

    def test_first_segment_cannot_leave_actual_source_phase(self):
        row = short_source_shot()
        self.assert_fault_contains(report([row]), "所在阶段")
        timeline = utterance_timing(start=1, end=2.2)
        self.assert_fault_contains(report([row], timeline), "实际对白所在阶段")

    def test_first_segment_cannot_move_entire_quote_to_next_silent_shot(self):
        row = short_source_shot()
        following = shot("", seconds=4, shot_id="EP01-002", picture=(
            "【0.0—0.8秒】甲闭口看向门口。"
            "【0.8—3.2秒】甲闭口站定。"
            "【3.2—4.0秒】甲闭口坐下。"
        ))
        timeline = utterance_timing(start=.8, end=2, shot_id="EP01-002")
        self.assert_fault_contains(report([row, following], timeline), "实际源文所在镜头")

    def test_pause_is_validated_before_character_segment_filtering(self):
        row = shot("甲乙丙丁戊", seconds=2)
        timeline = utterance_timing(start=.2, end=1.5, length=5)
        timeline["utterances"][0]["pauses"] = [{"id": "p1", "offset": 2, "seconds": .3, "extra": True}]
        self.assertEqual(report([row], timeline)["faults"], [])
        for offset in (-1, 99):
            with self.subTest(offset=offset):
                invalid = copy.deepcopy(timeline)
                invalid["utterances"][0]["pauses"][0]["offset"] = offset
                self.assert_fault_contains(report([row], invalid), "停顿身份、字符位置或时长非法")

    def test_prescribed_pause_requires_a_matching_timing_event(self):
        row = prescribed_pause_shot()
        self.assert_fault_contains(report([row]), "正文明确指定额外停顿时长")
        valid = copy.deepcopy(row)
        valid["画面内容"] = valid["画面内容"].replace("2.0秒。", "0.3秒。")
        timeline = utterance_timing(start=.2, end=1.5, length=5)
        timeline["utterances"][0]["pauses"] = [{"id": "p1", "offset": 5, "seconds": .3, "extra": True}]
        self.assertEqual(report([valid], timeline)["faults"], [])

    def test_unrelated_hold_does_not_waive_the_prescribed_pause(self):
        row = prescribed_pause_shot()
        timeline = {"contract": "shot-timing-v1", "utterances": [], "events": [{
            "id": "h1", "kind": "hold", "shot_id": "EP01-001", "actor": "甲",
            "action": "保持", "basis": "甲保持看向乙", "start": 1.8, "end": 2,
        }]}
        self.assert_fault_contains(report([row], timeline), "正文明确指定额外停顿时长")
        # Matching words and sufficient total seconds still cannot move the
        # hold outside the phase containing its actual action basis.
        timeline["events"][0].update(action="停顿", basis="甲说完后明确停顿2.0秒", start=0, end=2)
        self.assert_fault_contains(report([row], timeline), "动作依据所属阶段")

    def test_quoted_time_range_is_not_a_phase_and_source_stays_unchanged(self):
        row = shot("", seconds=5, picture=(
            "【0.0—0.2秒】甲看向乙。"
            "【0.2—4.8秒】甲说道：“只等1—2秒。”；甲说道：“你先别着急。”"
            "【4.8—5.0秒】甲站定。"
        ))
        before = copy.deepcopy(row)
        result = report([row])
        self.assertEqual(result["faults"], [])
        self.assertEqual(len(_phase_rows(row)), 3)
        self.assertEqual(len(result["metrics"]["utterances"]), 2)
        self.assertEqual(row, before)
        self.assertEqual(row["画面内容"].count("只等1—2秒。"), 1)

    def test_listener_closed_mouth_does_not_silence_the_speaker(self):
        row = shot(seconds=2, picture=(
            "【0.0—0.2秒】甲看向乙。"
            "【0.2—1.8秒】甲说道：“请你马上回来。”，乙闭口聆听。"
            "【1.8—2.0秒】甲看向乙。"
        ))
        row["可见主体"] = "甲、乙"
        timeline = utterance_timing()
        self.assertEqual(report([row], timeline)["faults"], [])
        row["画面内容"] = row["画面内容"].replace("乙闭口聆听", "甲闭口聆听")
        self.assert_fault_contains(report([row], timeline), "闭口/停止发声事实冲突")

    def test_strict_target_must_match_registered_profile_model(self):
        row = shot("甲乙丙丁戊", seconds=2)
        for timeline in (None, utterance_timing(start=.2, end=1.2, length=5)):
            with self.subTest(explicit=timeline is not None):
                data = project([row], timeline)
                self.assertEqual(validate_timing(data, [row], RULES)["faults"], [])
                data["source_lock"]["target_model"] = "Seedance 2.0"
                self.assert_fault_contains(validate_timing(data, [row], RULES), "模型")

    def test_explicit_measured_profile_requires_exact_actual_voice_spec_hash(self):
        row = shot("甲乙丙丁戊", seconds=2)
        voice_hash = hashlib.sha256(row["音色"].encode("utf-8")).hexdigest()
        measured = dict(PROFILE, id="measured-test-fixture", evidence_status="measured", voice="fixture-voice",
                        voice_spec_sha256=voice_hash, evidence={
                            "sample_sha256": "a" * 64, "alignment_verified": True, "reviewed": True,
                            "model_version": "Seedance 2.5", "voice": "fixture-voice",
                        })
        rules = copy.deepcopy(RULES)
        rules["timing_policy"]["profiles"].append(measured)
        timeline = utterance_timing(start=.2, end=1.2, length=5)
        timeline["utterances"][0]["profile_id"] = measured["id"]
        data = project([row], timeline)
        self.assertEqual(validate_timing(data, [row], rules)["faults"], [])
        measured["voice_spec_sha256"] = ("0" if voice_hash[0] != "0" else "1") + voice_hash[1:]
        self.assert_fault_contains(validate_timing(data, [row], rules), "音色规格")

    def test_default_measured_profile_also_requires_actual_voice_spec(self):
        row = shot("甲乙丙丁戊", seconds=2)
        voice_hash = hashlib.sha256(row["音色"].encode("utf-8")).hexdigest()
        measured = dict(PROFILE, id="measured-implicit-fixture", evidence_status="measured",
                        voice_spec_sha256=voice_hash, evidence={
                            "sample_sha256": "a" * 64, "alignment_verified": True, "reviewed": True,
                            "model_version": "Seedance 2.5", "voice": "fixture-voice",
                        })
        rules = copy.deepcopy(RULES)
        rules["timing_policy"]["profiles"].append(measured)
        data = project([row], {"contract": "shot-timing-v1", "default_profile_id": measured["id"], "utterances": [], "events": []})
        self.assertEqual(validate_timing(data, [row], rules)["faults"], [])
        measured["voice_spec_sha256"] = "0" * 64
        self.assert_fault_contains(validate_timing(data, [row], rules), "音色规格")


if __name__ == "__main__":
    unittest.main()
