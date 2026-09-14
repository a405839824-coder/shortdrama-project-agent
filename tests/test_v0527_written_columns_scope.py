"""Current written columns keep arbitrary keys without lending speech a surface."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
from shortdrama_mcp import materializer
import validate_storyboard
from test_v0527_written_payload_binding import shot


class WrittenColumnScopeTests(unittest.TestCase):
    def modules(self):
        return (validate_storyboard,materializer)

    def gpt(self,value):
        return [validate_storyboard.project_gpt_keyframe(value),
                materializer.compile_gpt_keyframe_instruction(value,assume_compact=True)]

    def kept(self,text,payloads):
        value=shot(text);original=copy.deepcopy(value)
        outputs=self.gpt(value)
        self.assertEqual(outputs[0],outputs[1])
        for output in outputs:
            for payload in payloads:self.assertIn(payload,output)
        self.assertEqual(validate_storyboard.project_formal_master(value),
                         materializer.compile_formal_merged_field(value,assume_compact=True))
        self.assertEqual(value,original)

    def test_two_named_columns_preserve_internal_commas_and_negative_value(self):
        text='纸面现有两栏：姓名为“甲、乙”，条件为“未复核，不得交付”；全部字迹当前清楚可读。'
        self.kept(text,['“甲、乙”','“未复核，不得交付”'])

    def test_three_arbitrary_keys_use_grammar_not_field_name_list(self):
        text='纸面当前已有三个字段：资源指向为“北门”，入场边界为“有票，仍须登记”，核对依据：“附页甲”；字迹清楚。'
        self.kept(text,['“北门”','“有票，仍须登记”','“附页甲”'])

    def test_future_and_negation_inside_value_remain_literal(self):
        text='纸面现有两栏：阶段为“明日复核”，约束为“未核验，不得交付”；当前字迹完整。'
        self.kept(text,['“明日复核”','“未核验，不得交付”'])

    def test_speech_stops_column_inheritance_for_following_values(self):
        text='纸面现有两栏：姓名为“甲”，甲随后说道：“现在交付。”；条件为“再回答。”'
        for mod in self.modules():
            masked,protected=mod._protect_visual_text_fragments(text)
            self.assertIn('“甲”',protected.values())
            self.assertNotIn('“现在交付。”',protected.values())
            self.assertNotIn('“再回答。”',protected.values())
            self.assertIn('甲随后说道',masked)
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s:materializer.compile_gpt_keyframe_instruction(s,assume_compact=True)):
            with self.assertRaisesRegex(ValueError,'(?:field_content_violation|keyframe_static_contract_violation)'):fn(shot(text))

    def test_explicit_future_writing_keeps_existing_public_rejection(self):
        text='纸面现有两栏：姓名为“甲”，纸面将写有条件：“已复核”；字迹位置预留。'
        # Protecting a literal object does not assert its current existence:
        # its unmasked future predicate must still reach the static gate.
        for mod in self.modules():
            masked,_=mod._protect_visual_text_fragments(text)
            self.assertIn('纸面将写有条件',masked)
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s:materializer.compile_gpt_keyframe_instruction(s,assume_compact=True)):
            with self.assertRaisesRegex(ValueError,'(?:field_content_violation|keyframe_static_contract_violation)'):fn(shot(text))

    def test_narration_is_not_a_column_key(self):
        text='纸面现有两栏：姓名为“甲”，旁白转述，条件为“昨日已交付”。'
        for mod in self.modules():
            masked,protected=mod._protect_visual_text_fragments(text)
            self.assertNotIn('“昨日已交付”',protected.values())
            self.assertIn('旁白转述',masked)
        # A different, existing downstream guard may still need this wording;
        # this scope test does not assert a public projection rejection.

    def test_sentence_boundary_resets_named_field_inheritance(self):
        text='纸面现有两栏：姓名为“甲”。条件为“未证实”。'
        for mod in self.modules():
            _,protected=mod._protect_visual_text_fragments(text)
            self.assertIn('“甲”',protected.values())
            self.assertNotIn('“未证实”',protected.values())

    def test_following_writer_process_is_not_masked_as_column_text(self):
        text='纸面现有两栏：姓名为“甲”。甲正在写下条件：“已核验”。'
        for mod in self.modules():
            masked,protected=mod._protect_visual_text_fragments(text)
            self.assertNotIn('“已核验”',protected.values())
            self.assertIn('甲正在写下条件',masked)

    def test_no_current_surface_stops_column_inheritance(self):
        text='纸面现有两栏：姓名为“甲”，此刻纸面已不存在，条件为“乙”。'
        for mod in self.modules():
            masked,protected=mod._protect_visual_text_fragments(text)
            self.assertNotIn('“乙”',protected.values())
            self.assertIn('纸面已不存在',masked)


if __name__=='__main__':unittest.main()
