"""Static projection regressions discovered in the frozen real EP32 first draft."""
from __future__ import annotations

import copy
import unittest

from shortdrama_mcp.materializer import (
    compile_formal_merged_field,
    compile_gpt_keyframe_instruction,
)
from test_v051_template_projection import compact_storyboard
from validate_storyboard import project_formal_master, project_gpt_keyframe


class StaticEnvironmentProjectionTests(unittest.TestCase):
    def project_both(self, value, field="空间站位描述"):
        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot[field] = ("文字造型锚：" + value) if field == "参考图锚定" else value
        before = copy.deepcopy(shot)
        results = []
        for compiler in (project_gpt_keyframe,
                         lambda item: compile_gpt_keyframe_instruction(item, assume_compact=True)):
            try:
                results.append(("returned", compiler(shot)))
            except ValueError as exc:
                results.append(("rejected", str(exc)))
        self.assertEqual(shot, before, "Static projection must not rewrite the video canonical.")
        self.assertEqual(results[0], results[1], "The standalone and server compilers must agree.")
        return results[0]

    def test_real_environment_process_phrases_are_rejected(self):
        for value in (
            "窗纸与木构静止；衣摆受人物动作产生细微位移。",
            "廊外树叶有轻微自然摆动，人物衣摆随步幅变化。",
            "袖口随着手臂动作发生小幅位移。",
            "帘布持续起伏。",
            "烛焰轻微摇曳。",
            "水面不断流动。",
            "树叶不停摆动。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "rejected")
                self.assertIn("keyframe_static_contract_violation", detail)
                self.assertIn("environment_process", detail)

    def test_static_appearance_and_completed_results_are_preserved(self):
        for value in (
            "衣摆向右翘起的一角，衣料褶皱清晰。",
            "窗纸停在向内微弯的可见瞬间。",
            "树叶呈扇状舒展，枝条保持弧形。",
            "衣摆摆动后已停在人物右侧。",
            "衣摆已经停止摆动。",
            "衣摆静止，明暗变化表现布料的褶皱层次。",
            "木料颜色变化形成深浅相间的纹路。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "returned", detail)
                self.assertIn(value.rstrip("。"), detail)

    def test_negative_process_instructions_are_not_positive_motion(self):
        for value in (
            "衣摆没有摆动；窗纱不再起伏。",
            "禁止衣摆摆动；禁止树叶晃动。",
            "禁止衣摆摆动、树叶晃动；窗纸纹理清晰。",
            "未见树叶晃动，枝条清晰可辨。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "returned", detail)

    def test_positive_restart_cannot_hide_behind_a_prohibition(self):
        for value in (
            "禁止衣摆摆动，但树叶持续晃动。",
            "没有风，衣摆仍在摆动。",
            "禁止衣摆摆动、树叶正在晃动。",
            "窗纱不再起伏；衣摆随步幅变化。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "rejected", detail)

    def test_not_stopped_and_later_motion_are_not_static_results(self):
        for value in (
            "衣摆没有停止摆动。",
            "树叶并未停止晃动。",
            "衣摆摆动后已停在右侧，树叶仍在晃动。",
            "衣摆持续摆动，画面标注为静态瞬间。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "rejected", detail)

    def test_movement_in_explicitly_visible_text_is_not_an_instruction(self):
        for value in (
            "【匾额】衣摆随步幅变化",
            "牌匾可见文字：树叶持续摆动",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "returned", detail)
                self.assertIn(value, detail)

    def test_process_cannot_move_into_another_static_field(self):
        for field in ("起始状态", "光影", "光源方向", "空间站位描述", "参考图锚定"):
            with self.subTest(field=field):
                status, detail = self.project_both("光斑随着树影产生细微位移。", field)
                self.assertEqual(status, "rejected", detail)

    def test_comma_starts_independent_motion_assertion_after_negation(self):
        for value in (
            "无烟，烛焰摇曳。",
            "没有风，树叶摆动。",
            "禁止衣摆摆动，树叶轻微晃动。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "rejected", detail)

    def test_negative_enumeration_keeps_its_original_delimiter(self):
        value = "禁止衣摆摆动、树叶晃动。"
        status, detail = self.project_both(value)
        self.assertEqual(status, "returned", detail)
        self.assertIn(value.rstrip("。"), detail)

    def test_completed_stop_after_motion_noun_is_static(self):
        for value in (
            "衣摆的摆动已经停止。",
            "树叶晃动已停。",
            "衣摆的摆动已经停止，衣角停在人物右侧。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "returned", detail)
                self.assertIn(value.rstrip("。"), detail)

    def test_uncompleted_or_restarted_stop_is_not_a_static_result(self):
        for value in (
            "衣摆的摆动尚未停止。",
            "衣摆的摆动没有停止。",
            "衣摆的摆动已经停止了吗？",
            "衣摆摆动已停又再次摆动。",
            "衣摆的摆动已经停止，但树叶持续晃动。",
        ):
            with self.subTest(value=value):
                status, detail = self.project_both(value)
                self.assertEqual(status, "rejected", detail)

    def test_music_removal_never_negates_the_next_visual_assertion(self):
        for visual in ("空气清澈", "保留皮肤纹理", "门窗保持敞开"):
            value = "9:16竖屏；禁止额外配乐，" + visual + "。"
            with self.subTest(value=value):
                status, detail = self.project_both(value, "生成限制")
                self.assertEqual(status, "returned", detail)
                self.assertIn("视觉限制：" + visual, detail)
                self.assertNotIn("禁止" + visual, detail)
                shot = copy.deepcopy(compact_storyboard()["shots"][0])
                shot["生成限制"] = value
                formal = project_formal_master(shot)
                self.assertEqual(
                    formal, compile_formal_merged_field(shot, assume_compact=True)
                )
                self.assertIn(visual, formal)
                self.assertNotIn("禁止" + visual, formal)

    def test_music_removal_preserves_a_following_negative_enumeration(self):
        value = "9:16竖屏；禁止额外配乐。禁止字幕、水印和现代物件。"
        status, detail = self.project_both(value, "生成限制")
        self.assertEqual(status, "returned", detail)
        self.assertIn("禁止字幕、水印和现代物件", detail)


if __name__ == "__main__":
    unittest.main()
