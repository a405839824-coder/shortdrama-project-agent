"""Voice choices follow one registry across authoring, checking and export."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"),
               str(ROOT / "skills/shortdrama-storyboard/scripts")]

from authoring_contract_blocks import ContractBlockError, voice_block
from field_content import field_policy, voice_content_findings
from test_v0527_production_decisions import voice_spec
from validate_storyboard import _voice_template_errors
from voice_sequence import build_voice_sequence


ROWS = [("孟怀澈", "她既识趣，明日陪她走一趟也无妨。")]


def spec(expression="冷淡，意在告知安排。"):
    result = voice_spec()
    result.update(speech_act="应允陪同回门。", current_expression=expression, emphasis="‘明日’。")
    return result


def block(expression="冷淡，意在告知安排。"):
    return voice_block(mode="spoken", dialogue_rows=ROWS, **spec(expression))


class VoiceRegistryContractTests(unittest.TestCase):
    def assert_both_reject(self, value, role):
        findings = voice_content_findings(value, dialogue_rows=ROWS)
        self.assertTrue(any(role in row["message"] or role == row["role"] for row in findings), findings)
        self.assertTrue(_voice_template_errors(value, ROWS[0][1], ROWS))

    def test_actual_approved_expressions_are_preserved_by_all_routes(self):
        from shortdrama_mcp.materializer import compile_formal_merged_field
        from test_v0518_voice_template import compact_shot
        for expression in ("冷淡", "利落", "平稳", "利落，略带冷淡"):
            with self.subTest(expression=expression):
                value = block(expression + "，意在告知安排。")
                self.assertEqual(voice_content_findings(value, dialogue_rows=ROWS), [])
                self.assertEqual(_voice_template_errors(value, ROWS[0][1], ROWS), [])
                self.assertIn("言语行为：应允陪同回门。", value)
                shot = compact_shot()
                shot.update(音色=value, 画面内容=f'{ROWS[0][0]}说道：“{ROWS[0][1]}”')
                formal = compile_formal_merged_field(shot, assume_compact=True)
                self.assertIn(value, formal)

    def test_original_missing_intent_is_an_actual_error_in_both_gates(self):
        with self.assertRaisesRegex(ContractBlockError, "潜在意图"):
            block("利落，略带冷淡。")
        value = block().replace("冷淡，意在告知安排。", "利落，略带冷淡。")
        self.assert_both_reject(value, "潜在意图")
        self.assert_both_reject(block().replace("冷淡，", ""), "表层情绪")
        self.assert_both_reject(block().replace("冷淡，", "表面略带，"), "表层情绪")

    def test_unknown_and_extra_foreign_clauses_are_not_licensed_by_one_good_clause(self):
        for old, new in (("冷淡，", "冷淡，玄妙悠远，"),
                         ("应允陪同回门。", "未知话语类别。"),
                         ("应允陪同回门。", "应允陪同回门。机位推近。")):
            with self.subTest(new=new):
                self.assert_both_reject(block().replace(old, new), "unsupported_owned_clause")

    def test_one_registry_edit_reaches_author_and_content_gate_without_wordlist_change(self):
        # A grammar extension is visible in both consumers; this is not a new
        # production vocabulary and is restored before leaving the test.
        patterns = field_policy()["owned_clause_patterns"]["音色.当前表达"]
        original = patterns[0]
        try:
            patterns[0] = "(?:" + original + "|澄明)"
            value = block("澄明，意在告知安排。")
            self.assertEqual(voice_content_findings(value, dialogue_rows=ROWS), [])
            self.assertEqual(_voice_template_errors(value, ROWS[0][1], ROWS), [])
        finally:
            patterns[0] = original
        self.assert_both_reject(value, "unsupported_owned_clause")

    def test_each_actual_speaker_requires_its_own_expression_components(self):
        rows = ROWS + [("辛三", "她既识趣，明日陪她走一趟也无妨。")]
        value = build_voice_sequence([(speaker, text, copy.deepcopy(spec())) for speaker, text in rows])
        self.assertEqual(voice_content_findings(value, dialogue_rows=rows), [])
        bad = value.replace("〔2:辛三〕冷淡，意在告知安排。", "〔2:辛三〕冷淡。")
        self.assertTrue(any("潜在意图" in row["message"] for row in voice_content_findings(bad, dialogue_rows=rows)))
        self.assertTrue(any("第2句辛三" in error for error in _voice_template_errors(bad, "".join(t for _, t in rows), rows)))


if __name__ == "__main__":
    unittest.main()
