"""Bounded regressions for three observed EP33/EP34 parser gaps.

These are software controls, never manuscript production or accuracy samples.
"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'shortdrama-storyboard' / 'scripts'))

from lighting_checks import affirmative_matches, lighting_context_faults
from shortdrama_mcp.materializer import compile_gpt_keyframe_instruction
from test_v051_template_projection import compact_storyboard
from validate_storyboard import (
    DAYLIGHT_SOURCE_RE, _current_scene_time_flags, _lighting_context_faults,
    project_gpt_keyframe,
)


def room(space="内室，白昼；南窗关闭，窗纸清楚。"):
    return {
        "空间站位描述": space, "起始状态": "人物坐定，双手空着。",
        "画面内容": "人物保持坐姿。", "光源来源": "南窗纸透入漫射日光。",
        "光源方向": "画面左前上方漫射光落向人物，向纵深衰减。",
        "光影": "人物面部受柔和漫射光；空气清澈、背景结构清晰，曝光正常。",
        "色温倾向": "肤色自然暖，窗纸微冷。", "生成限制": "无雾、无烟、无可见光束。",
    }


class ObservedParserGapTests(unittest.TestCase):
    def project_both(self, text, reject=False):
        shot = copy.deepcopy(compact_storyboard()["shots"][0])
        shot["空间站位描述"] = text
        shot["生成限制"] = "9:16竖屏；禁止额外配乐；不看镜头；禁止字幕、水印；空气清澈、无雾、无烟。"
        before = copy.deepcopy(shot)
        results = []
        for fn in (project_gpt_keyframe,
                   lambda s: compile_gpt_keyframe_instruction(s, assume_compact=True)):
            if reject:
                with self.assertRaisesRegex(ValueError, "keyframe_static_contract_violation"):
                    fn(shot)
            else:
                results.append(fn(shot))
        self.assertEqual(shot, before)
        if not reject:
            self.assertEqual(results[0], results[1])
            self.assertIn(text.rstrip("。"), results[0])

    def test_actual_by_and_legacy_from_ongoing_motion_reject(self):
        for text in (
            "暖轿由西侧岸路接近背风照料区西缘。",
            "暖轿从西侧岸路接近背风照料区西缘。",
            "暖轿正在接近背风照料区西缘。",
        ):
            with self.subTest(text=text):
                self.project_both(text, reject=True)

    def test_explicit_negative_motion_including_subject_is_preserved(self):
        for text in (
            "暖轿没有由西侧岸路接近背风照料区西缘。",
            "暖轿并未从西侧岸路接近背风照料区西缘。",
            "禁止暖轿由西侧岸路接近背风照料区西缘。",
        ):
            with self.subTest(text=text):
                self.project_both(text)

    def test_actual_stopped_result_and_static_distance_are_preserved(self):
        for text in (
            "暖轿由西侧岸路接近后已停稳，门框平直。",
            "暖轿停在背风照料区西缘，车夫在近处，门框平直。",
        ):
            with self.subTest(text=text):
                self.project_both(text)

    def test_later_affirmative_motion_is_not_hidden(self):
        for text in (
            "暖轿没有由西侧岸路接近，但暖轿由南侧空地接近敞门。",
            "暖轿由西侧岸路接近后已停稳，随后又由南侧空地接近敞门。",
            "纸面标签写着“暖轿由西侧岸路接近”，暖轿由南侧空地接近敞门。",
        ):
            with self.subTest(text=text):
                self.project_both(text, reject=True)

    def test_written_approach_is_literal_not_motion(self):
        self.project_both("纸面标签写着“暖轿由西侧岸路接近背风照料区西缘”；纸边平整。")

    def test_negation_of_other_predicate_does_not_waive_motion(self):
        for text in (
            "没有日光的院里暖轿由西侧接近门口。",
            "禁止喧哗时暖轿由西侧接近门口。",
        ):
            with self.subTest(text=text):
                self.project_both(text, reject=True)

    def assert_time(self, shot, expected):
        before = copy.deepcopy(shot)
        self.assertEqual(_current_scene_time_flags(shot), expected)
        faults = lighting_context_faults(shot, legacy_checker=_lighting_context_faults)
        self.assertEqual(any("同时声明日景与夜景" in row for row in faults), all(expected))
        self.assertEqual(any("夜景/黄昏后" in row for row in faults), expected[1])
        self.assertEqual(shot, before)

    def test_question_suffix_is_not_a_time_declaration(self):
        for field in ("原文支撑句", "画面内容"):
            with self.subTest(field=field):
                shot = room()
                shot[field] = "问孩子时，屋内只留府医。"
                self.assert_time(shot, (True, False))

    def test_spoken_written_and_source_times_do_not_change_current_scene(self):
        for field, text in (
            ("画面内容", "人物说道：“子时再来。”"),
            ("画面内容", "他们谈到夜间的那次问话。"),
            ("起始状态", "纸签写着“子时”；人物坐定。"),
            ("原文支撑句", "深夜的那次问话已结束。"),
            ("覆盖原文", "子时内室情节的原文依据"),
        ):
            with self.subTest(field=field):
                shot = room()
                shot[field] = text
                self.assert_time(shot, (True, False))

    def test_explicit_current_time_conflicts_are_not_resolved_by_day_priority(self):
        for field, text in (
            ("空间站位描述", "内室，日间；本镜时间为子时，南窗关闭。"),
            ("起始状态", "本镜当前是子时，人物坐定。"),
        ):
            with self.subTest(field=field):
                shot = room()
                shot[field] = text
                self.assert_time(shot, (True, True))

    def test_legacy_night_location_and_heading_formats_remain_effective(self):
        for field, text in (
            ("空间站位描述", "内室，子时；南窗关闭。"),
            ("空间站位描述", "内室，当前时辰为子时；南窗关闭。"),
            ("空间站位描述", "深夜内室，南窗关闭。"),
            ("空间站位描述", "内室，亥时；南窗关闭。"),
            ("功能类型", "子时内室对话"),
            ("起始状态", "已到子时，人物在内室坐定。"),
            ("起始状态", "当前时辰为“子时”，人物坐定。"),
            ("空间站位描述", "在夜间，内室南窗关闭。"),
            ("起始状态", "到了子时，人物坐定。"),
            ("空间站位描述", "内室于深夜，南窗关闭。"),
        ):
            with self.subTest(field=field, text=text):
                shot = room("内室；南窗关闭。")
                shot[field] = text
                self.assert_time(shot, (False, True))

    def test_source_only_time_does_not_fill_an_unspecified_current_time(self):
        shot = room("内室；南窗关闭。")
        shot["原文支撑句"] = "子时，众人曾在内室说话。"
        self.assert_time(shot, (False, False))

    def assert_daylight(self, text, present):
        shot = room("内室，夜间；南窗关闭，案东油灯点亮。")
        shot["光源来源"] = text
        before = copy.deepcopy(shot)
        faults = lighting_context_faults(shot, legacy_checker=_lighting_context_faults)
        self.assertEqual(any("夜景/黄昏后" in row for row in faults), present, faults)
        self.assertEqual(shot, before)

    def test_negated_daylight_and_bare_enumeration_do_not_conflict(self):
        for text in (
            "案东油灯照明，窗外无日光。",
            "窗外无日光、晨光；仅案东油灯照明。",
        ):
            with self.subTest(text=text):
                self.assert_daylight(text, False)

    def test_positive_restart_and_old_daylight_sources_still_reject(self):
        for text in (
            "案东油灯照明，窗外有日光照入。",
            "窗外无日光，但窗边另有阳光照入。",
            "清晨日光从南窗透入。",
        ):
            with self.subTest(text=text):
                self.assert_daylight(text, True)

    def test_visible_source_label_is_not_the_active_light(self):
        self.assert_daylight("案东油灯照明，纸签写着“日光”。", False)
        self.assert_daylight("本镜主光采用“日光”。", True)

    def test_double_negation_must_not_hide_real_daylight(self):
        for text in ("窗外并非没有日光。", "窗外不是没有日光。"):
            with self.subTest(text=text):
                self.assert_daylight(text, True)

    def test_shared_polarity_keeps_original_match_offsets(self):
        text = "窗外无日光、晨光；但并非没有阳光。"
        matches = affirmative_matches(DAYLIGHT_SOURCE_RE, text)
        self.assertEqual([match.group() for match in matches], ["阳光"])
        self.assertEqual([text[match.start():match.end()] for match in matches], ["阳光"])


if __name__ == "__main__":
    unittest.main()
