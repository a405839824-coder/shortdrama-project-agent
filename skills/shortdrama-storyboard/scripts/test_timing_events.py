"""Bounded action-event and causal listener-reaction regressions."""
import copy
import unittest

from timing_events import validate_events


def shot(shot_id="S1", picture="甲点头；甲喝水；乙点头；甲停住。", duration=4.0):
    return {"镜头编号": shot_id, "时长": duration, "画面内容": picture}


def event(event_id="a1", **overrides):
    value = {"id": event_id, "shot_id": "S1", "kind": "action", "actor": "甲", "start": 0.0, "end": 0.2, "action": "点头", "basis": "甲点头", "after": []}
    value.update(overrides)
    return value


def speech(**overrides):
    value = {"id": "u1", "utterance_id": "u1", "shot_id": "S1", "speaker": "甲", "kind": "dialogue", "start": 0.5, "end": 2.5, "global_start": 0.5, "global_end": 2.5, "char_range": [0, 10]}
    value.update(overrides)
    return value


def project(events):
    return {"prevention_evidence": {"audio_workflow": {"timing": {"events": events}}}}


class TimingEventTests(unittest.TestCase):
    def test_parallel_nod_is_allowed_without_fixed_fee(self):
        events = [event(start=1.0, end=1.01)]
        result = validate_events(project(events), [shot()], [speech()])
        self.assertEqual([], result["faults"])
        self.assertEqual(1.01, result["events"][0]["end"])

    def test_same_action_different_ids_remain_distinct(self):
        events = [event("a1"), event("a2", start=3.0, end=3.2)]
        result = validate_events(project(events), [shot()], [speech()])
        self.assertEqual([], result["faults"])
        self.assertEqual(["a1", "a2"], [row["id"] for row in result["events"]])

    def test_duplicate_unknown_shot_and_nonfinite_bounds_fail(self):
        for rows in ([event(), event()], [event(2)], [event(shot_id="missing")], [event(end=float("inf"))], [event(start=-0.1)], [event(end=0.0)], [event(end=4.1)]):
            with self.subTest(rows=rows):
                faults = validate_events(project(rows), [shot()], []) ["faults"]
                self.assertTrue(faults)
                self.assertTrue(all(row["code"] in {"E-TIMELINE-001", "E-BATCH-003"} and row.get("shot_id") for row in faults))
                self.assertTrue(all(row["message"] == row["evidence"] for row in faults))
        picture = "【0.0—0.5秒】甲站着。【0.5—2.2秒】甲点头。【2.2—4.0秒】甲停住。"
        too_long = event(start=1.8, end=2.6)
        faults = validate_events(project([too_long]), [shot(picture=picture)], [])["faults"]
        self.assertTrue(any(row["code"] == "E-BATCH-003" and "所属阶段" in row["message"] for row in faults))
        self.assertEqual([], validate_events(project([dict(too_long, end=2.2)]), [shot(picture=picture)], [])["faults"])

    def test_after_dependency_requires_real_predecessor_end(self):
        good = event(start=2.5, end=2.7, after=["u1"])
        self.assertEqual([], validate_events(project([good]), [shot()], [speech()])["faults"])
        early = dict(good, start=2.4)
        self.assertTrue(validate_events(project([early]), [shot()], [speech()])["faults"])
        self.assertTrue(validate_events(project([dict(good, after=["unknown"])]), [shot()], [speech()])["faults"])

    def test_same_speaker_drinking_overlaps_voice_but_other_actor_does_not(self):
        drink = event(start=1.0, end=1.3, action="喝水", basis="甲喝水")
        self.assertTrue(validate_events(project([drink]), [shot()], [speech()])["faults"])
        self.assertEqual([], validate_events(project([drink]), [shot()], [speech(speaker="乙")])["faults"])

    def test_negative_or_spoken_action_is_not_source_evidence(self):
        for picture, basis in (("甲不点头。", "甲不点头"), ("甲不点头。", "点头"), ("甲说道：“甲点头。”", "甲点头"), ("甲看着乙，乙点头。", "乙点头")):
            with self.subTest(picture=picture, basis=basis):
                self.assertTrue(validate_events(project([event(basis=basis)]), [shot(picture=picture)], []) ["faults"])
        self.assertEqual([], validate_events(project([event(basis="轻轻点头")]), [shot(picture="甲看向茶盏，轻轻点头。")], [])["faults"])
        self.assertEqual([], validate_events(project([event(basis="轻轻点头")]), [shot(picture="甲看着乙，轻轻点头。")], [])["faults"])

    def test_reaction_waits_for_relevant_character_not_entire_sentence(self):
        reaction = event(kind="reaction", actor="乙", basis="乙点头", start=1.5, end=1.7, trigger={"utterance_id": "u1", "offset": 5})
        self.assertEqual([], validate_events(project([reaction]), [shot()], [speech()])["faults"])
        self.assertTrue(validate_events(project([dict(reaction, start=1.4)]), [shot()], [speech()])["faults"])

    def test_reaction_invalid_offset_or_missing_trigger_fails(self):
        reaction = event(kind="reaction", actor="乙", basis="乙点头", start=2.5, end=2.7)
        for trigger in (None, {"utterance_id": "u1", "offset": 11}, {"utterance_id": "missing", "offset": 2}, {"utterance_id": "u1", "offset": True}):
            with self.subTest(trigger=trigger):
                self.assertTrue(validate_events(project([dict(reaction, trigger=trigger)]), [shot()], [speech()])["faults"])

    def test_cross_shot_dependency_uses_global_clock(self):
        later = event(shot_id="S2", start=0.0, end=0.2, after=["u1"])
        self.assertEqual([], validate_events(project([later]), [shot(), shot("S2")], [speech()])["faults"])

    def test_repeated_utterance_segments_trigger_uses_right_character_range(self):
        spans = [speech(id="p1", start=0.0, end=1.0, global_start=0.0, global_end=1.0, char_range=[0, 5]), speech(id="p2", start=2.0, end=3.0, global_start=2.0, global_end=3.0, char_range=[5, 10])]
        reaction = event(kind="reaction", actor="乙", basis="乙点头", start=2.5, end=2.7, trigger={"utterance_id": "u1", "offset": 8})
        self.assertTrue(validate_events(project([reaction]), [shot()], spans)["faults"])
        self.assertEqual([], validate_events(project([dict(reaction, start=2.6)]), [shot()], spans)["faults"])

    def test_natural_serial_action_cannot_use_full_speech_phase(self):
        picture = "【0.0—1.0秒】甲先点头后说道：“你好。”【1.0—2.0秒】甲站着。【2.0—4.0秒】甲停住。"
        span = speech(start=0.0, end=1.0, global_start=0.0, global_end=1.0)
        self.assertTrue(validate_events({}, [shot(picture=picture)], [span])["faults"])
        unrelated = event(kind="hold", action="停住", basis="甲停住", start=3.0, end=3.2)
        self.assertTrue(validate_events(project([unrelated]), [shot(picture=picture)], [span])["faults"])
        self.assertEqual([], validate_events({}, [shot(picture=picture)], [dict(span, end=0.8, global_end=0.8)])["faults"])

    def test_parallel_or_negated_natural_actions_have_no_fee(self):
        for body in ("甲边点头边说道：“你好。”", "甲不点头，直接说道：“你好。”", "甲不先点头后说话。"):
            with self.subTest(body=body):
                picture = "【0.0—1.0秒】" + body + "【1.0—2.0秒】甲站着。【2.0—4.0秒】甲停住。"
                self.assertEqual([], validate_events({}, [shot(picture=picture)], [speech(start=0.0, end=1.0, global_start=0.0, global_end=1.0)])["faults"])


if __name__ == "__main__":
    unittest.main()
