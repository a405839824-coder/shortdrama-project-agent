#!/usr/bin/env python3
"""Executable budget contracts; no test claims measured speech calibration."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from timing_model import chapter_budget, estimate_utterance, validate_timing


SCRIPT_ROOT = Path(__file__).resolve().parent
RULES = json.loads(
    (SCRIPT_ROOT.parent / "references" / "storyboard_runtime_rules.json").read_text(
        encoding="utf-8"
    )
)
PROFILE = next(
    row for row in RULES["timing_policy"]["profiles"]
    if row["id"] == RULES["timing_policy"]["default_profile_id"]
)


def shot(text: str = "请你马上回来。", *, seconds: float = 2.0,
         picture: str | None = None, shot_id: str = "EP01-001") -> dict:
    return {
        "镜头编号": shot_id,
        "时长": seconds,
        "累计时长": seconds,
        "可见主体": "甲",
        "画面内容": picture if picture is not None else (
            f"【0.0—0.2秒】甲注视乙。"
            f"【0.2—{seconds - 0.2:.1f}秒】甲说道：“{text}”"
            f"【{seconds - 0.2:.1f}—{seconds:.1f}秒】甲视线落在乙脸上。"
        ),
        "音色": "自然连贯，不额外拖音。",
        "心理音轨": "无。",
    }


def project(shots: list[dict], timing: dict | None = None) -> dict:
    audio = {} if timing is None else {"timing": timing}
    return {
        "source_lock": {"output_contract": "R10-SPC-1", "target_model": "Seedance 2.5"},
        "prevention_evidence": {"audio_workflow": audio},
        "shots": shots,
    }


def report(shots: list[dict], timing: dict | None = None) -> dict:
    data = project(shots, timing)
    return validate_timing(data, shots, RULES)


class TimingModelTests(unittest.TestCase):
    def test_default_five_is_provisional_not_measured(self) -> None:
        result = estimate_utterance("字" * 25, PROFILE)
        self.assertAlmostEqual(result["seconds"], 5.0)
        self.assertEqual(result["evidence_status"], "provisional")

    def test_normal_punctuation_is_already_in_average_rate(self) -> None:
        ordinary = estimate_utterance("甲乙丙丁戊", PROFILE)
        punctuated = estimate_utterance("甲——乙……丙？！丁，戊。", PROFILE)
        self.assertAlmostEqual(ordinary["seconds"], 1.0)
        self.assertAlmostEqual(punctuated["seconds"], ordinary["seconds"])

    def test_same_explicit_pause_event_is_counted_once(self) -> None:
        pause = {"id": "pause-1", "offset": 2, "seconds": 0.3, "extra": True}
        result = estimate_utterance("甲——乙丙丁戊", PROFILE, [pause, copy.deepcopy(pause)])
        self.assertAlmostEqual(result["seconds"], 1.3)

    def test_distinct_pause_positions_are_not_collapsed(self) -> None:
        pauses = [
            {"id": "pause-1", "offset": 1, "seconds": 0.3, "extra": True},
            {"id": "pause-2", "offset": 4, "seconds": 0.3, "extra": True},
        ]
        result = estimate_utterance("甲乙丙丁戊", PROFILE, pauses)
        self.assertAlmostEqual(result["seconds"], 1.6)

    def test_local_short_phase_does_not_borrow_unused_shot_time(self) -> None:
        row = shot(seconds=4.0, picture=(
            "【0.0—0.8秒】甲看向乙。"
            "【0.8—1.0秒】甲说道：“请你马上回来。”"
            "【1.0—4.0秒】甲视线落在乙脸上。"
        ))
        result = report([row])
        self.assertTrue(result["faults"], result)
        self.assertTrue(any(f.get("shot_id") == "EP01-001" for f in result["faults"]))

    def test_psychological_audio_has_real_capacity(self) -> None:
        row = shot(seconds=4.0, picture=(
            "【0.0—0.8秒】甲看向门口。"
            "【0.8—3.2秒】甲在桌旁审视信封。"
            "【3.2—4.0秒】甲视线落在信封上。"
        ))
        row["心理音轨"] = (
            "心理旁白：“" + "念" * 450 + "”；不张嘴、不驱动口型、不生成字幕、不计入对白。"
        )
        result = report([row])
        self.assertTrue(result["faults"], result)
        self.assertTrue(any(f.get("shot_id") == "EP01-001" for f in result["faults"]))

    def test_parallel_nod_shares_the_speech_window(self) -> None:
        row = shot(picture=(
            "【0.0—0.2秒】甲看向乙。"
            "【0.2—1.4秒】甲边点头边说，甲说道：“请你马上回来。”"
            "【1.4—2.0秒】甲视线落在乙脸上。"
        ))
        result = report([row])
        self.assertEqual(result["faults"], [], result)

    def test_serial_nod_requires_time_before_speech(self) -> None:
        row = shot(picture=(
            "【0.0—0.2秒】甲看向乙。"
            "【0.2—1.4秒】甲先点头后再说，甲说道：“请你马上回来。”"
            "【1.4—2.0秒】甲视线落在乙脸上。"
        ))
        result = report([row])
        self.assertTrue(result["faults"], result)

    def test_negated_action_is_not_charged(self) -> None:
        row = shot(picture=(
            "【0.0—0.2秒】甲看向乙。"
            "【0.2—1.4秒】甲不点头继续说，甲说道：“请你马上回来。”"
            "【1.4—2.0秒】甲视线落在乙脸上。"
        ))
        result = report([row])
        self.assertEqual(result["faults"], [], result)

    def test_one_utterance_can_span_three_phases_without_repeating_text(self) -> None:
        text = "甲乙丙丁戊己庚辛壬癸"
        row = shot(text=text, picture=(
            f"【0.0—0.2秒】甲说道：“{text}”"
            "【0.2—1.8秒】甲自然连续发声。"
            "【1.8—2.0秒】尾音落稳，甲视线落在乙脸上。"
        ))
        timing = {
            "contract": "shot-timing-v1",
            "utterances": [{
                "id": "utterance-1", "kind": "dialogue", "speaker": "甲",
                "source": {"shot_id": "EP01-001", "dialogue_index": 0},
                "segments": [
                    {"shot_id": "EP01-001", "start": 0.0, "end": 0.2, "char_range": [0, 1]},
                    {"shot_id": "EP01-001", "start": 0.2, "end": 1.8, "char_range": [1, 9]},
                    {"shot_id": "EP01-001", "start": 1.8, "end": 2.0, "char_range": [9, 10]},
                ],
            }],
            "events": [],
        }
        before = copy.deepcopy(row)
        result = report([row], timing)
        self.assertEqual(result["faults"], [], result)
        self.assertEqual(row, before)
        self.assertEqual(row["画面内容"].count(text), 1)

    def test_chapter_estimate_uses_the_same_profile(self) -> None:
        result = chapter_budget(
            [{"speaker": "甲", "text": "字" * 25}], RULES, project([])
        )
        self.assertAlmostEqual(result["required_dialogue_seconds"], 5.0)
        self.assertEqual(result["faults"], [], result)

    def test_project_cannot_invent_an_unregistered_fast_profile(self) -> None:
        row = shot("字" * 50)
        timing = {
            "contract": "shot-timing-v1", "default_profile_id": "invented-999-cps",
            "utterances": [], "events": [],
        }
        result = report([row], timing)
        self.assertTrue(result["faults"], result)

    def test_chapter_keeps_duplicate_occurrences_and_bound_profile_pauses(self) -> None:
        rules = copy.deepcopy(RULES)
        slower = dict(PROFILE, id="provisional-slow-test", units_per_second=2.5)
        rules["timing_policy"]["profiles"].append(slower)
        text = "甲乙丙丁戊己庚辛壬癸。"
        rows = [shot(text, seconds=5.0), shot(text, seconds=5.0, shot_id="EP01-002")]
        timing = {
            "contract": "shot-timing-v1", "events": [],
            "utterances": [{
                "id": "second-occurrence", "kind": "dialogue", "speaker": "甲",
                "profile_id": slower["id"],
                "source": {"shot_id": "EP01-002", "dialogue_index": 0},
                "segments": [{"shot_id": "EP01-002", "start": 0.2, "end": 4.5,
                              "char_range": [0, len(text)]}],
                "pauses": [{"id": "special-pause", "offset": 5, "seconds": 0.3, "extra": True}],
            }],
        }
        protected = [{"speaker": "甲", "text": text}, {"speaker": "甲", "text": text}]
        result = chapter_budget(protected, rules, project(rows, timing))
        self.assertEqual(result["faults"], [], result)
        self.assertAlmostEqual(result["required_dialogue_seconds"], 6.3)
        self.assertIn(slower["id"], result["profile_ids"])

    def test_chapter_complete_parallel_timeline_includes_offset_and_transition(self) -> None:
        protected = [{"speaker": "甲", "text": "字" * 25}]
        budget = {
            "dialogue_seconds": 5.0, "action_seconds": 1.0, "transition_seconds": 0.5,
            "timeline_events": [
                {"id": "speech", "kind": "dialogue", "speaker": "甲", "source_index": 0,
                 "start": 0.0, "end": 5.0},
                {"id": "gesture", "kind": "action", "actor": "甲", "basis": "甲在说话中抬手回应",
                 "start": 2.0, "end": 3.0},
                {"id": "cut", "kind": "transition", "basis": "转入相邻的院外场景",
                 "after": ["speech"], "start": 5.0, "end": 5.5},
            ],
        }
        result = chapter_budget(protected, RULES, project([]), budget=budget)
        self.assertEqual(result["faults"], [], result)
        self.assertAlmostEqual(result["required_total_seconds"], 5.5)
        self.assertEqual(result["timeline_coverage"], "validated_chapter_timeline")

    def test_chapter_uses_the_existing_validated_parallel_project_events(self) -> None:
        text = "字" * 25
        row = shot(text, seconds=5.4, picture=(
            "【0.0—0.2秒】甲看向乙。"
            f"【0.2—5.2秒】甲说道：“{text}”；甲在说话中抬手回应。"
            "【5.2—5.4秒】甲视线落到乙脸上。"
        ))
        timing = {
            "contract": "shot-timing-v1", "utterances": [],
            "events": [{
                "id": "gesture", "shot_id": "EP01-001", "kind": "action", "actor": "甲",
                "action": "抬手", "basis": "甲在说话中抬手回应", "start": 0.5, "end": 1.5,
            }],
        }
        result = chapter_budget(
            [{"speaker": "甲", "text": text}], RULES, project([row], timing),
            budget={"dialogue_seconds": 5.0, "action_seconds": 1.0, "transition_seconds": 0.0},
        )
        self.assertEqual(result["faults"], [], result)
        self.assertAlmostEqual(result["required_total_seconds"], 5.2)
        self.assertEqual(result["timeline_coverage"], "validated_project_timeline")

    def test_chapter_invalid_dependency_retains_serial_budget_and_failure(self) -> None:
        protected = [{"speaker": "甲", "text": "字" * 25}]
        budget = {
            "dialogue_seconds": 5.0, "action_seconds": 1.0, "transition_seconds": 0.0,
            "timeline_events": [
                {"id": "speech", "kind": "dialogue", "speaker": "甲", "source_index": 0,
                 "start": 0.0, "end": 5.0},
                {"id": "gesture", "kind": "action", "actor": "甲", "basis": "甲听完后抬手",
                 "after": ["speech"], "start": 2.0, "end": 3.0},
            ],
        }
        result = chapter_budget(protected, RULES, project([]), budget=budget)
        self.assertTrue(result["faults"], result)
        self.assertAlmostEqual(result["required_total_seconds"], 6.0)
        self.assertEqual(result["timeline_coverage"], "serial_fallback")

    def test_chapter_incomplete_event_coverage_does_not_hide_serial_action(self) -> None:
        protected = [{"speaker": "甲", "text": "字" * 25}]
        budget = {
            "dialogue_seconds": 5.0, "action_seconds": 1.0, "transition_seconds": 0.0,
            "timeline_events": [{
                "id": "speech", "kind": "dialogue", "speaker": "甲", "source_index": 0,
                "start": 0.0, "end": 5.0,
            }],
        }
        result = chapter_budget(protected, RULES, project([]), budget=budget)
        self.assertEqual(result["faults"], [], result)
        self.assertAlmostEqual(result["required_total_seconds"], 6.0)
        self.assertEqual(result["timeline_coverage"], "serial_fallback")

    def test_chapter_cannot_retime_an_existing_canonical_utterance(self) -> None:
        text = "请你马上回来。"
        rows = [shot(text, seconds=2.0)]
        budget = {
            "dialogue_seconds": 1.2, "action_seconds": 0.0, "transition_seconds": 0.0,
            "timeline_events": [{
                "id": "speech", "kind": "dialogue", "speaker": "甲", "source_index": 0,
                "start": 0.0, "end": 1.2,
            }],
        }
        result = chapter_budget([{"speaker": "甲", "text": text}], RULES, project(rows), budget=budget)
        self.assertTrue(result["faults"], result)
        self.assertTrue(any("canonical" in row.get("message", "") for row in result["faults"]))

    def test_chapter_drinking_cannot_share_same_person_speech_time(self) -> None:
        budget = {
            "dialogue_seconds": 1.2, "action_seconds": 1.0, "transition_seconds": 0.0,
            "timeline_events": [
                {"id": "speech", "kind": "dialogue", "speaker": "甲", "source_index": 0,
                 "start": 0.0, "end": 1.2},
                {"id": "drink", "kind": "action", "actor": "甲", "basis": "甲喝水",
                 "start": 0.0, "end": 1.0},
            ],
        }
        result = chapter_budget([{"speaker": "甲", "text": "请你马上回来。"}], RULES, project([]), budget=budget)
        self.assertTrue(result["faults"], result)
        self.assertAlmostEqual(result["required_total_seconds"], 2.2)


if __name__ == "__main__":
    unittest.main()
