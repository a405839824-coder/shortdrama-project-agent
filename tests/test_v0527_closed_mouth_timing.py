"""Explicit closed-mouth intervals cannot overlap that actor's dialogue."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/"skills/shortdrama-storyboard/scripts"
sys.path.insert(0,str(SCRIPTS))
from timing_events import validate_events
from timing_model import chapter_budget
from author_storyboard import record_attempt
from test_v0527_authoring_serializer import decisions


def check(*, action="闭唇", basis="甲闭唇。", actor="甲", speaker="甲", kind="dialogue",
          end=10.2, speech_start=4.2):
    shot={"镜头编号":"S1","时长":11.0,"画面内容":"【0.0—11.0秒】"+basis}
    event={"id":"A1","shot_id":"S1","kind":"hold","actor":actor,"action":action,
           "basis":basis,"start":0.8,"end":end}
    span={"id":"U1","shot_id":"S1","speaker":speaker,"kind":kind,
          "start":speech_start,"end":9.6,"char_range":[0,2]}
    data={"prevention_evidence":{"audio_workflow":{"timing":{"events":[event]}}}}
    return validate_events(data,[shot],[span])


def closed_overlap(result):
    return any("闭唇" in f["message"] and "重叠" in f["message"] for f in result["faults"])


class ClosedMouthTimingTests(unittest.TestCase):
    def test_same_actor_explicit_closed_interval_blocks_dialogue(self):
        for action in ("闭唇","保持闭唇","合唇","双唇紧闭","嘴唇闭合"):
            with self.subTest(action=action):
                self.assertTrue(closed_overlap(check(action=action,basis="甲"+action+"。")))

    def test_basis_cannot_shorten_explicit_action_interval(self):
        self.assertTrue(closed_overlap(check(action="保持闭唇",basis="甲保持闭唇，内声结束后再说话。")))

    def test_different_actor_and_psychological_tracks_remain_legal(self):
        self.assertEqual([],check(speaker="乙")["faults"])
        for kind in ("psychological","psych","psychological_audio"):
            with self.subTest(kind=kind):
                self.assertEqual([],check(kind=kind)["faults"])

    def test_touching_boundaries_and_precise_closed_then_spoken_are_legal(self):
        self.assertEqual([],check(end=4.2)["faults"])
        self.assertEqual([],check(end=3.3,speech_start=3.6)["faults"])

    def test_long_basis_is_not_a_full_interval_closed_mouth_declaration(self):
        result=check(action="保持",basis="甲保持双手放在桌边，甲先闭唇后说话。")
        self.assertEqual([],result["faults"])

    def test_negation_and_post_negation_are_not_closed_mouth_overlap(self):
        for action in ("保持不闭唇","闭唇动作不发生"):
            with self.subTest(action=action):
                self.assertFalse(closed_overlap(check(action=action,basis="甲"+action+"。")))

    def test_quoted_and_conditional_actions_are_not_affirmative_states(self):
        for action in ('描述“保持闭唇”',"若需要则闭唇","如果需要，保持闭唇"):
            with self.subTest(action=action):
                self.assertFalse(closed_overlap(check(action=action,basis="甲"+action+"。")))

    def test_quote_or_negation_does_not_hide_following_affirmative_clause(self):
        for action in ('提到“无需闭唇”；实际保持闭唇',"不再闭唇，但实际保持闭唇"):
            with self.subTest(action=action):
                self.assertTrue(closed_overlap(check(action=action,basis="甲实际保持闭唇。")))

    def test_generic_hold_basis_cannot_prove_closed_mouth_state(self):
        result=check(action="保持闭唇",basis="甲保持双手放在桌边。",speaker="乙")
        self.assertTrue(any("依据" in f["message"] for f in result["faults"]))

    def test_chapter_budget_consumes_explicit_action_without_promoting_basis(self):
        rules=json.loads((SCRIPTS.parent/"references/storyboard_runtime_rules.json").read_text(encoding="utf-8"))
        for action,expected in (("保持闭唇",True),("保持",False),("不闭唇",False)):
            with self.subTest(action=action):
                budget={"dialogue_seconds":1.2,"action_seconds":1.0,"transition_seconds":0.0,
                  "timeline_events":[
                    {"id":"U1","kind":"dialogue","speaker":"甲","source_index":0,"start":0.0,"end":1.2},
                    {"id":"A1","kind":"action","actor":"甲","action":action,
                     "basis":"甲保持双手在桌边，先闭唇后说话。","start":0.0,"end":1.0}]}
                project={"source_lock":{"output_contract":"R10-SPC-1","target_model":"Seedance 2.5"},
                         "prevention_evidence":{"audio_workflow":{}},"shots":[]}
                result=chapter_budget([{"speaker":"甲","text":"请你马上回来。"}],rules,project,budget=budget)
                self.assertEqual(expected,closed_overlap(result),result)

    def test_actual_record_attempt_preserves_rejected_raw_input(self):
        data=decisions();row=data["shots"][0]
        row["phases"][1]["text"]='甲先保持闭唇；之后才开口。甲在1.3秒开始发言，到2.0秒说完。甲说道：“可以。”'
        row["events"]=[{"id":"A1","kind":"hold","actor":"甲","action":"保持闭唇",
                         "basis":"甲先保持闭唇","start":0.7,"end":3.3}]
        data["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"]=[{
          "id":"U1","kind":"dialogue","speaker":"甲","source":{"shot_id":row["shot_id"],"dialogue_index":0},
          "segments":[{"shot_id":row["shot_id"],"start":1.3,"end":2.0,"char_range":[0,len("可以。")]}]}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/"input.json";raw=json.dumps(data,ensure_ascii=False).encode("utf-8");path.write_bytes(raw)
            report=record_attempt(path,root/"attempts",attempt_id="closed-overlap")
            self.assertEqual(report["status"],"rejected",report)
            self.assertTrue(any("闭唇" in f["message"] and "重叠" in f["message"] for f in report["findings"]),report)
            self.assertEqual(report["input_sha256"],hashlib.sha256(raw).hexdigest())
            attempt=Path(report["report_path"]).parent
            self.assertEqual((attempt/"input.original.json").read_bytes(),raw)
            self.assertFalse((attempt/"canonical.json").exists())
            self.assertTrue(report["include_in_attempt_accounting"])
            row["events"][0]["end"]=1.2
            good=root/"serial.json";good.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
            accepted=record_attempt(good,root/"attempts",attempt_id="closed-then-spoken")
            self.assertEqual(accepted["status"],"rendered_pending_preflight",accepted)


if __name__=="__main__":unittest.main()
