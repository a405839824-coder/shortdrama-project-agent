"""Bound current paper roles and retain-or-reject public static quotations."""
from pathlib import Path
import copy
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]
import validate_storyboard
from shortdrama_mcp import materializer
from test_v0527_written_payload_binding import shot

CASES = [('WP01_title_then_columns', '纸上现有标题：“材料清单”，现有栏目：“材料名、批次、当前数量、经办人”。', True), ('WP02_title_then_arbitrary_role', '纸上现有标题：“保管记录”，现有去向栏：“沿用原批次；不并入甲账；不并入乙账”。', True), ('WP03_paper_part_and_next_role', '同一张纸的下部现有经办栏：“甲可登记日常支出；不得独自转出保管物”，现有查验栏：“每季复核一次”。', True), ('WP04_existing_arbitrary_keys_and_literal_scope', '纸面当前已有三个字段：资源指向为“北门”，许可边界为“未复核，不得转出”，执行日期：“明日”；字迹当前完整。', True), ('WP05_speech_break', '纸上现有标题：“清单”，甲随后说道：“照办。”，现有清点栏：“十件”。', False), ('WP06_future_writing_break', '纸上现有标题：“清单”，随后将写归档栏：“已归档”。', False), ('WP07_narration_history_break', '纸上现有标题：“清单”；旁白转述，查验栏：“昨日已完成”。', False), ('WP08_unknown_static_quote', '纸面平放。核验结果：“待确认”。', False)]

class CurrentPaperScopeTests(unittest.TestCase):
    def check_case(self, text, returned):
        value = shot(text)
        original = copy.deepcopy(value)
        outputs = []
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s: materializer.compile_gpt_keyframe_instruction(s, assume_compact=True)):
            if returned:
                output = fn(value)
                for match in materializer._QUOTED_DIALOGUE_RE.finditer(text):
                    self.assertIn(match.group(0), output)
                outputs.append(output)
            else:
                with self.assertRaisesRegex(ValueError, "keyframe_static_contract_violation: unbound_visual_text"):
                    fn(value)
        if returned:
            self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(value, original)
        self.assertEqual(validate_storyboard.project_formal_master(value),
                         materializer.compile_formal_merged_field(value, assume_compact=True))

    def test_lost_bound_literal_is_rejected(self):
        # The audio clause filter would remove this whole mixed clause, even
        # though the paper payload was recognized. It must not return silence.
        value = shot('环境声中纸面写着“清单”。')
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s: materializer.compile_gpt_keyframe_instruction(s, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, "dropped_visual_text"):
                fn(value)

    def test_legacy_helpers_keep_default_cleanup_contract(self):
        text = '纸面写着“清单”。“旧约”。'
        for fn in (validate_storyboard._visual_only_clauses, materializer._visual_only_text):
            cleaned = fn(text, strip_dialogue=True)
            self.assertIn('“清单”', cleaned)
            self.assertNotIn('“旧约”', cleaned)
        self.assertNotIn('“旧约”', materializer._visual_only_picture_content(text))

    def test_negated_written_object_stays_negated(self):
        self.check_case('纸面没有写着“已通过”。', True)
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s: materializer.compile_gpt_keyframe_instruction(s, assume_compact=True)):
            self.assertIn('没有写着“已通过”', fn(shot('纸面没有写着“已通过”。')))

    def test_same_surface_correction_preserves_both_literals(self):
        self.check_case('纸面不是写着“甲”，而是写着“乙”。', True)


def _case_test(text, returned):
    def run(self):
        self.check_case(text, returned)
    return run

for name, text, returned in CASES:
    setattr(CurrentPaperScopeTests, "test_" + name.lower(), _case_test(text, returned))

if __name__ == "__main__":
    unittest.main()
