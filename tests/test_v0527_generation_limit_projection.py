"""A music-only exclusion must not swallow unrelated visual prohibitions."""
from __future__ import annotations

import copy
import unittest

from shortdrama_mcp.materializer import (
    _visual_only_text,
    compile_gpt_keyframe_instruction,
    compile_formal_merged_field,
)
from test_v051_template_projection import compact_storyboard
from validate_storyboard import _visual_only_clauses, project_gpt_keyframe, project_formal_master


class GenerationLimitProjectionTests(unittest.TestCase):
    def shot(self, limits):
        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot["生成限制"] = limits
        return shot

    def assert_projection(self, limits, expected_bans):
        shot = self.shot(limits)
        before = copy.deepcopy(shot)
        standalone = project_gpt_keyframe(shot)
        server = compile_gpt_keyframe_instruction(shot, assume_compact=True)
        self.assertEqual(standalone, server)
        for ban in expected_bans:
            self.assertIn(ban, server)
        self.assertNotIn("BGM", server)
        self.assertNotIn("配乐", server)
        self.assertNotIn("音乐", server)
        self.assertEqual(shot, before)
        return shot

    def test_real_r1_sentence_boundary_keeps_all_visual_bans(self):
        self.assert_projection(
            "9:16竖屏；不看镜头；禁止额外配乐。禁止字幕、水印和现代物件。"
            "禁止塑料皮肤与过度锐化。",
            ["不看镜头", "禁止字幕、水印和现代物件", "禁止塑料皮肤与过度锐化"],
        )

    def test_only_music_item_is_removed_from_shared_prohibition(self):
        cases = (
            ("禁止字幕、水印、BGM、现代物件", ["禁止字幕、水印、现代物件"]),
            ("禁止BGM及字幕；禁止水印；禁止现代物件", ["禁止字幕", "禁止水印", "禁止现代物件"]),
            ("不要音乐和字幕；禁止水印；不得现代物件", ["不要字幕", "禁止水印", "不得现代物件"]),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assert_projection("9:16竖屏；" + value, expected)

    def test_sentence_delimiters_do_not_extend_the_music_clause(self):
        for separator in ("。", "！", "？", "\n", ";", "；"):
            with self.subTest(separator=separator):
                self.assert_projection(
                    "9:16竖屏；禁止BGM" + separator + "禁止字幕、水印和现代物件。",
                    ["禁止字幕、水印和现代物件"],
                )

    def test_formal_master_keeps_music_ban_and_visual_bans(self):
        shot = self.shot("9:16竖屏；禁止额外配乐。禁止字幕、水印和现代物件。")
        standalone = project_formal_master(shot)
        server = compile_formal_merged_field(shot, assume_compact=True)
        self.assertEqual(standalone, server)
        for value in ("禁止额外配乐", "禁止字幕、水印和现代物件"):
            self.assertIn(value, server)

    def test_affirmative_music_after_a_visual_ban_still_fails(self):
        for limits in (
            "禁止字幕。随后加入BGM。",
            "禁止额外配乐。允许背景音乐。",
            "禁止字幕、水印并加入背景音乐。",
        ):
            with self.subTest(limits=limits):
                shot = self.shot("9:16竖屏；" + limits)
                for compiler in (project_gpt_keyframe,
                                 lambda item: compile_gpt_keyframe_instruction(item, assume_compact=True)):
                    with self.assertRaisesRegex(ValueError, "generation_limit_audio_contract_violation"):
                        compiler(shot)

    def test_place_name_suffix_is_preserved_with_or_without_punctuation(self):
        for value in ("南门通道", "南门通道。", "南门通道：", "石板道路", "门外狭窄通道"):
            with self.subTest(value=value):
                expected = value.rstrip("。：")
                self.assertEqual(_visual_only_text(value, strip_dialogue=True), expected)
                self.assertEqual(_visual_only_clauses(value, strip_dialogue=True), expected)
                shot = self.shot("9:16竖屏；禁止额外配乐；禁止字幕")
                shot["空间站位描述"] = value
                standalone = project_gpt_keyframe(shot)
                server = compile_gpt_keyframe_instruction(shot, assume_compact=True)
                self.assertEqual(standalone, server)
                self.assertIn("场景与站位：" + expected, server)

    def test_explicit_speech_delivery_cue_is_removed_without_losing_static_clause(self):
        for speech in ("低声说道", "轻声问道", "开口说"):
            with self.subTest(speech=speech):
                value = "苏清柳坐在南门通道，" + speech + "：“母亲。”"
                expected = "苏清柳坐在南门通道"
                self.assertEqual(_visual_only_text(value, strip_dialogue=True), expected)
                self.assertEqual(_visual_only_clauses(value, strip_dialogue=True), expected)

    def test_ambiguous_speech_attribution_is_not_silently_shortened(self):
        shot = self.shot("9:16竖屏；禁止额外配乐；禁止字幕")
        shot["起始状态"] = "苏清柳说道：“母亲。”"
        for compiler in (project_gpt_keyframe,
                         lambda item: compile_gpt_keyframe_instruction(item, assume_compact=True)):
            with self.assertRaisesRegex(ValueError, "(?:field_content_violation|keyframe_static_contract_violation)"):
                compiler(shot)


if __name__ == "__main__":
    unittest.main()
