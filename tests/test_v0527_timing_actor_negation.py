"""Real EP34 actor-name regression; genuine action negation remains binding."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"))
from timing_events import validate_events


class ActorNameNegationTests(unittest.TestCase):
    def check(self, actor, text, action="点头", basis=None, spans=None):
        shot = {"镜头编号": "EP34-019", "时长": 4.0, "画面内容": f"【0.0—4.0秒】{text}"}
        event = {"id": "E1", "shot_id": "EP34-019", "kind": "action", "actor": actor,
                 "action": action, "basis": text if basis is None else basis, "start": 0.5, "end": 1.0}
        obj = {"prevention_evidence": {"audio_workflow": {"timing": {"events": [event]}}}}
        return validate_events(obj, [shot], spans or [])

    def test_real_unknown_witness_mouth_event_is_not_negated(self):
        basis = "未知证人口型与本句对白同步"
        text = '未知证人说道：“我不知道。”未知证人口型与本句对白同步。'
        self.assertEqual(self.check("未知证人", text, "口型与本句对白同步", basis)["faults"], [])

    def test_exact_names_containing_negation_characters_are_nouns(self):
        for actor in ("未知证人", "无名氏", "未央", "不系舟"):
            with self.subTest(actor=actor):
                self.assertEqual(self.check(actor, actor + "点头。")["faults"], [])

    def test_actual_negation_after_name_is_still_rejected(self):
        for negation in ("未", "没有", "不", "不再", "不得", "无需"):
            with self.subTest(negation=negation):
                self.assertTrue(self.check("未知证人", "未知证人" + negation + "点头。")["faults"])

    def test_negation_before_name_is_not_erased(self):
        self.assertTrue(self.check("未知证人", "禁止未知证人点头。")["faults"])

    def test_single_character_name_does_not_swallow_following_negation(self):
        for actor in ("未", "不", "无"):
            with self.subTest(actor=actor):
                self.assertEqual(self.check(actor, actor + "点头。")["faults"], [])
                self.assertTrue(self.check(actor, actor + actor + "点头。")["faults"])

    def test_inherited_subject_keeps_actual_negation(self):
        self.assertTrue(self.check("未知证人", "未知证人站定，随后没有点头。", basis="随后没有点头")["faults"])

    def test_inherited_positive_subject_is_accepted(self):
        self.assertEqual(self.check("未知证人", "未知证人站定，随后点头。", basis="随后点头")["faults"], [])

    def test_wrong_actor_does_not_borrow_named_subject(self):
        self.assertTrue(self.check("府医", "未知证人点头。")["faults"])

    def test_quoted_action_does_not_become_physical_event(self):
        self.assertTrue(self.check("未知证人", '未知证人说道：“未知证人点头。”', basis="未知证人点头")["faults"])

    def test_serial_action_full_speech_still_rejected_for_named_witness(self):
        text = '未知证人先点头，随后说道：“我不知道。”'
        span = {"id": "U1", "shot_id": "EP34-019", "speaker": "未知证人", "kind": "dialogue",
                "start": 0.0, "end": 4.0, "char_range": [0, 5]}
        faults = self.check("未知证人", text, basis="先点头", spans=[span])["faults"]
        self.assertTrue(any(f["code"] == "E-BATCH-003" and "没有正时长" in f["message"] for f in faults))


if __name__ == "__main__":
    unittest.main()
