"""Typed paper scope is derived from actual static bytes, never metadata."""
from pathlib import Path
from dataclasses import FrozenInstanceError
import copy
import hashlib
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]
import validate_storyboard
from shortdrama_mcp import materializer
from written_payload import discover_current_written_payloads
from test_v0527_written_payload_binding import shot

CASES = [('TWP01_previous_sentence_surface', '纸面平放案上，纸面高0.8米，页首朝北。现有经办记录：“甲已登记。”其下落名处为空白。', True), ('TWP02_arbitrary_current_roles', '纸上现有权限记录：“未复核，不得交付。”现有核验结论：“已登记；不等于已放行。”', True), ('TWP03_exhaustive_literal', '纸面平放，四边完整。纸上全文只有：“材料收讫”。没有其他文字，手部不入画。', True), ('TWP04_narration_scope', '纸面平放。旁白转述，现有经办记录：“已登记。”', False), ('TWP05_future_plan_role', '纸面平放。现有将来写字计划：“已登记。”', False), ('TWP06_unknown_scope', '纸面平放。是否现有经办记录：“已登记。”尚不清楚。', False), ('TWP07_speaking_role', '纸面平放。现有旁白：“已登记。”', False), ('TWP08_payload_is_literal', '纸面平放。现有文本：“明日再说；未核验，不得交付。”', True), ('TWP09_adjacent_dynamic', '纸面平放。现有文本：“材料收讫”。甲正在书写。', False), ('TWP10_changed_domain', '纸面平放。甲说道，现有经办记录：“已登记。”', False)]

class TypedWrittenPayloadTests(unittest.TestCase):
    def check_case(self, value, expected):
        original = copy.deepcopy(value)
        projectors = (validate_storyboard.project_gpt_keyframe,
                      lambda s: materializer.compile_gpt_keyframe_instruction(s, assume_compact=True))
        results = []
        for fn in projectors:
            if expected:
                result = fn(value)
                for quote in materializer._QUOTED_DIALOGUE_RE.finditer(value["起始状态"]):
                    self.assertIn(quote.group(), result)
                results.append(result)
            else:
                with self.assertRaisesRegex(ValueError, "(?:field_content_violation|keyframe_static_contract_violation: (?:unbound_visual_text|dynamic=authored_process))"):
                    fn(value)
        if results:
            self.assertEqual(results[0], results[1])
        self.assertEqual(value, original)
        if '旁白：' in value['起始状态']:
            for formal in (validate_storyboard.project_formal_master, lambda s: materializer.compile_formal_merged_field(s, assume_compact=True)):
                with self.assertRaisesRegex(ValueError, 'field_content_violation: 起始状态'):
                    formal(value)
            return
        self.assertEqual(validate_storyboard.project_formal_master(value),
                         materializer.compile_formal_merged_field(value, assume_compact=True))

    def test_exact_immutable_field_and_spans(self):
        text = CASES[0][1]
        payload, = discover_current_written_payloads(text, "起始状态")
        self.assertEqual(payload.field_name, "起始状态")
        self.assertEqual(payload.field_sha256, hashlib.sha256(text.encode("utf-8")).hexdigest())
        for span in (payload.surface, payload.assertion, payload.quote):
            self.assertEqual(text[span.start:span.end], span.literal)
        with self.assertRaises(FrozenInstanceError):
            payload.quote = None
        changed, = discover_current_written_payloads(text.replace("甲", "乙"), "起始状态")
        self.assertNotEqual(payload.field_sha256, changed.field_sha256)
        self.assertNotEqual(payload.quote.literal, changed.quote.literal)

    def test_external_metadata_cannot_authorize_a_quote(self):
        text = CASES[3][1]
        self.assertEqual(discover_current_written_payloads(text), ())
        with self.assertRaises(TypeError):
            discover_current_written_payloads(text, bindings={"current": True})
        value = shot(text)
        value["current_written_payloads"] = {"current": True, "quote": "已登记。"}
        for fn in (validate_storyboard.project_gpt_keyframe,
                   lambda s: materializer.compile_gpt_keyframe_instruction(s, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, "unbound_visual_text"):
                fn(value)

    def test_newline_cannot_borrow_another_fields_paper(self):
        self.assertEqual(discover_current_written_payloads('纸面平放。\n现有经办记录：“甲已登记。”'), ())


def _test(text, expected):
    def run(self):
        self.check_case(shot(text), expected)
    return run

for name, text, expected in CASES:
    setattr(TypedWrittenPayloadTests, "test_" + name.lower(), _test(text, expected))

if __name__ == "__main__":
    unittest.main()
