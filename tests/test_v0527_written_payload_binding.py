"""Physical written payloads survive projection without protecting nearby action."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "skills/shortdrama-storyboard/scripts")]

from shortdrama_mcp import materializer
import validate_storyboard


SYNTHETIC_TITLE = "纸上现有“临时安排”标题。纸面现有可读文字：“临时安排”。"
SYNTHETIC_LIST = (
    "当前已写的“安置提议”标题与程序文字。纸面现有可读文字："
    "“安置提议”；“参与商议：甲、乙、丙、丁。”；"
    "“不得以某人能否入院逼孩子改口。”。"
)


def shot(start: str) -> dict:
    # A small projection control, not a generated first-draft quality fixture.
    return {
        "镜头编号": "CONTROL-001", "时长": 4.0, "累计时长": 4.0,
        "镜头类型": "物件特写", "可见主体": "无人物入画",
        "空间站位描述": "日间室内，纸面平放案上。", "起始状态": start,
        "机位": "摄影机位于纸面南侧朝北，固定物件特写。",
        "画面内容": "【0.0—1.0秒】纸面静置。【1.0—3.0秒】纸面保持平放。【3.0—4.0秒】纸面不变。",
        "音色": "无对白，不适用。", "心理音轨": "无。",
        "环境动态": "木案纹理清楚；空气清澈。",
        "光影": "纸纹清楚，正常曝光，背景不泛白。",
        "光源来源": "东墙关闭纸窗透入日间漫射天光。",
        "光源方向": "主光从画面右侧上方落向纸面。",
        "色温倾向": "自然中性。", "参考图锚定": "无。",
        "生成限制": "9:16竖屏。禁止字幕、水印、现代物件；空气清澈，无雾。",
        "切镜/转场提示词": "硬切。",
    }


class WrittenPayloadBindingTests(unittest.TestCase):
    def scrubbers(self):
        return (validate_storyboard._visual_only_clauses, materializer._visual_only_text)

    def projectors(self):
        return (
            validate_storyboard.project_gpt_keyframe,
            lambda value: materializer.compile_gpt_keyframe_instruction(value, assume_compact=True),
        )

    def assert_kept(self, text, payloads):
        outputs = [fn(shot(text)) for fn in self.projectors()]
        self.assertEqual(outputs[0], outputs[1])
        for output in outputs:
            for payload in payloads:
                self.assertIn(payload, output)

    def assert_unprotected(self, text, payloads):
        for fn in self.scrubbers():
            cleaned = fn(text, strip_dialogue=True)
            for payload in payloads:
                self.assertNotIn(payload, cleaned)

    def test_current_readable_title(self):
        self.assert_kept(SYNTHETIC_TITLE, ["“临时安排”"])

    def test_semicolon_names_and_condition(self):
        self.assert_kept(SYNTHETIC_LIST, [
            "“安置提议”", "“参与商议：甲、乙、丙、丁。”",
            "“不得以某人能否入院逼孩子改口。”",
        ])

    def test_title_marker_after_quote_and_camera_focus(self):
        value = shot("纸面平放。")
        value["机位"] = "物件近景。焦点落在当前已写的“安置提议”标题与程序文字。"
        outputs = [fn(value) for fn in self.projectors()]
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(all("“安置提议”" in result for result in outputs))

    def test_colon_comma_enumeration_and_ascii_quotes(self):
        self.assert_kept('册页可见文字：“甲”： “乙”， “丙”。纸面可读文字："A";"B"。',
                         ["“甲”", "“乙”", "“丙”", '"A"', '"B"'])

    def test_old_physical_inscription_formats(self):
        self.assert_kept("纸上写着“值守册”；木牌刻有“东厢”。", ["“值守册”", "“东厢”"])

    def test_time_and_action_words_inside_written_payload_are_literal(self):
        self.assert_kept("纸面现有可读文字：“随后起身”；“第三秒打开书页”。",
                         ["“随后起身”", "“第三秒打开书页”"])

    def test_actual_speech_and_reading_are_not_written_bindings(self):
        for text in ("甲说道：“请先看完再说。”", "甲说：“请先看完再说。”",
                     "乙低声读纸面文字：“请先看完再说。”"):
            with self.subTest(text=text):
                self.assert_unprotected(text, ["请先看完再说。"])

    def test_inner_voice_and_source_reference_are_not_written_bindings(self):
        for text in ("乙心理内声：“这纸面写着安置提议。”",
                     "原文记载纸面文字：“这纸面写着安置提议。”"):
            with self.subTest(text=text):
                self.assert_unprotected(text, ["这纸面写着安置提议。"])

    def test_historical_paper_is_not_current_readable_text(self):
        for text in ("昨日纸面写着“旧约”。", "纸上曾写有“旧约”。",
                     "他回忆纸面可读文字：“旧约”。"):
            with self.subTest(text=text):
                self.assert_unprotected(text, ["旧约"])

    def test_enumeration_stops_at_speech_and_unrelated_clause(self):
        text = "纸面可读文字：“安置提议”，甲说道：“先看完。”；“再回答。”"
        for fn in self.scrubbers():
            output = fn(text, strip_dialogue=True)
            self.assertIn("“安置提议”", output)
            self.assertNotIn("先看完。", output)
            self.assertNotIn("再回答。", output)
        for fn in self.scrubbers():
            output = fn("纸面写着“清单”；他想起“旧约”。", strip_dialogue=True)
            self.assertIn("“清单”", output)
            self.assertNotIn("旧约", output)

    def test_new_explicit_current_binding_after_historical_clause(self):
        text = "昨日纸面写着“旧约”；现在纸面可读文字：“新提议”。"
        for fn in self.projectors():
            with self.assertRaisesRegex(ValueError, "keyframe_static_contract_violation: unbound_visual_text"):
                fn(shot(text))
        # A current declaration still works; only the mixed static input is
        # rejected instead of silently discarding its historical quotation.
        self.assert_kept("现在纸面可读文字：“新提议”。", ["“新提议”"])
        for fn in self.scrubbers():
            result = fn(text, strip_dialogue=True)
            self.assertNotIn("旧约", result)
            self.assertIn("“新提议”", result)

    def test_adjacent_camera_actor_and_eye_processes_still_rejected(self):
        for action in ("镜头向前推进。", "暖轿由西侧接近门口。",
                       "甲在0.6秒自然眨眼一次。"):
            for fn in self.projectors():
                with self.subTest(action=action, projector=fn):
                    with self.assertRaisesRegex(ValueError, "keyframe_static_contract_violation"):
                        fn(shot("纸面可读文字：“安置提议”；" + action))

    def test_sentence_boundary_does_not_inherit_written_binding(self):
        for fn in self.scrubbers():
            output = fn("纸面写着“清单”。“旧约”。", strip_dialogue=True)
            self.assertIn("“清单”", output)
            self.assertNotIn("旧约", output)

    def test_formal_master_remains_faithful_and_input_is_not_mutated(self):
        value = shot(SYNTHETIC_LIST)
        original = copy.deepcopy(value)
        standalone = validate_storyboard.project_formal_master(value)
        server = materializer.compile_formal_merged_field(value, assume_compact=True)
        self.assertEqual(standalone, server)
        self.assertIn(SYNTHETIC_LIST, standalone)
        self.assertEqual(value, original)

    def test_negative_written_objects_are_preserved_without_polarity_flip(self):
        for text in ('纸面没有写着“已批准”。', '纸面尚未写有“已批准”。', '纸上从未写有“禁入”。'):
            with self.subTest(text=text):
                self.assert_kept(text, [text.rstrip('。')])

    def test_future_historical_and_narrated_contents_are_not_current_stills(self):
        for text in ('纸面将写有“已批准”。', '昨日，纸面写着“旧约”。',
                     '旁白转述，纸面写着“旧约”。', '甲说道，纸面写着“旧约”。',
                     '甲说道：“纸面写着旧约。”'):
            for fn in self.projectors():
                with self.subTest(text=text, projector=fn):
                    with self.assertRaisesRegex(ValueError, 'keyframe_static_contract_violation'):
                        fn(shot(text))

    def test_explicit_current_state_overrides_historical_object_origin(self):
        text = '从前留下的纸面现在仍写着“旧约”。'
        self.assert_kept(text, [text.rstrip('。')])

    def test_correction_keeps_negative_and_positive_labels(self):
        text = '纸面不是写着“甲”，而是写着“乙”。'
        self.assert_kept(text, [text.rstrip('。')])

    def test_history_negation_and_future_inside_written_payload_are_literal(self):
        for text in ('纸面现有文字：“昨日到访”。', '纸面现有可读文字：“不得入内”。',
                     '纸签上“禁止入内”清晰可见。', '纸面现有文字：“纸面将写有某字”。'):
            with self.subTest(text=text):
                self.assert_kept(text, [text.rstrip('。')])

    def test_current_camera_or_narrator_claim_is_not_current_paper_binding(self):
        for text in ('昨日，纸面写着“旧约”；摄影机现在位于南侧。',
                     '旁白转述，现在纸面写着“旧约”。'):
            for fn in self.projectors():
                with self.subTest(text=text, projector=fn):
                    with self.assertRaisesRegex(ValueError, 'keyframe_static_contract_violation'):
                        fn(shot(text))

    def test_correction_and_current_origin_never_hide_adjacent_movement(self):
        for text in ('纸面不是写着“甲”，而是写着“乙”；镜头向前推进。',
                     '从前留下的纸面现在仍写着“旧约”；甲随后抬手。'):
            for fn in self.projectors():
                with self.subTest(text=text, projector=fn):
                    with self.assertRaisesRegex(ValueError, 'keyframe_static_contract_violation'):
                        fn(shot(text))
        # The binding layer must retain even shorthand whose process/pose
        # interpretation belongs to a different gate; it must never mask it.
        for fn in self.scrubbers():
            self.assertIn('甲抬手', fn('从前留下的纸面现在仍写着“旧约”；甲抬手。', strip_dialogue=True))


if __name__ == "__main__":
    unittest.main()
