#!/usr/bin/env python3
"""Positive, negative and semantic-continuity regressions for 0.5.24."""
import copy
import json
from pathlib import Path
import unittest

from lighting_checks import (
    affirmative_matches,
    classify_scene,
    lighting_context_faults,
    scene_lighting_continuity_faults,
)


def clean_room():
    return {
        "镜头编号": "EP01-001", "场景连续性ID": "SC1",
        "空间站位描述": "清晨卧房，左窗关闭，窗纸完整。",
        "起始状态": "女子坐在床边。", "画面内容": "女子自然看向对手。",
        "光影": "空气清澈，背景木构清晰可辨，高光不过曝，不泛白、不雾化。",
        "光源来源": "闭合窗纸透入天光，浅墙反射弱补光。",
        "光源方向": "左前侧略亮，向背景平缓衰减，无可见光束。",
        "色温倾向": "中性微暖，木墙保持本色。", "机位": "摄影机位于场景西侧。",
    }


class LightingChecksTest(unittest.TestCase):
    def check_fault(self, changes, expected, anchors=None):
        shot = {**clean_room(), **changes}
        faults = lighting_context_faults(shot, anchors)
        self.assertTrue(any(expected in fault for fault in faults), faults)

    def test_complete_clear_room_passes(self):
        self.assertEqual(lighting_context_faults(clean_room()), [])

    def test_kitchen_aliases_are_equivalent(self):
        for room in ("古代厨房", "室内厨房", "灶房", "库房", "寝宫"):
            with self.subTest(room=room):
                shot = {**clean_room(), "空间站位描述": f"清晨，{room}，窗户闭合。"}
                self.assertEqual(classify_scene(shot)["space_type"], "indoor")
                shot["光影"] = "空气清澈，但背景泛白，高光不过曝。"
                self.assertTrue(any("相反肯定事实" in row for row in lighting_context_faults(shot)))

    def test_bookroom_door_exterior_not_indoor(self):
        shot = {**clean_room(), "空间站位描述": "书房门外廊下，清晨。", "原文支撑句": "她准备进入书房。"}
        self.assertEqual(classify_scene(shot)["space_type"], "outdoor")

    def test_window_view_does_not_relocate_room(self):
        shot = {**clean_room(), "空间站位描述": "卧室靠近窗外庭院的一侧。"}
        self.assertEqual(classify_scene(shot)["space_type"], "indoor")

    def test_anchor_resolves_unknown_alias(self):
        shot = {**clean_room(), "空间站位描述": "清晨，春和轩，窗户关闭。"}
        anchors = {"SC1": {"space_type": "indoor"}}
        self.assertEqual(lighting_context_faults(shot, anchors), [])
        self.assertEqual(classify_scene(shot, anchors)["provenance"], "scene_anchor")

    def test_anchor_does_not_silently_override_contradictory_location(self):
        self.check_fault({}, "空间站位", {"SC1": {"space_type": "outdoor"}})

    def test_unrelated_anchor_not_used(self):
        self.assertEqual(classify_scene(clean_room(), {"OTHER": {"space_type": "outdoor"}})["space_type"], "indoor")

    def test_duplicate_anchor_fails(self):
        self.check_fault({}, "重复锚点", [{"scene_id": "SC1", "space_type": "indoor"}, {"scene_id": "SC1", "space_type": "outdoor"}])

    def test_transition_requires_current_side(self):
        self.check_fault({"空间站位描述": "回廊，女子站定。"}, "本镜所在", {"SC1": {"space_type": "transition"}})

    def test_transition_alias_without_anchor_requires_current_side(self):
        for location in ("回廊，女子站定。", "门槛处，女子站定。"):
            with self.subTest(location=location):
                self.check_fault({"空间站位描述": location}, "本镜所在")

    def test_transition_alias_can_name_current_side_without_anchor(self):
        for side, kind in (("室内", "indoor"), ("室外", "outdoor")):
            with self.subTest(side=side):
                shot = {**clean_room(), "空间站位描述": f"门槛处，女子站在{side}侧。"}
                self.assertEqual(classify_scene(shot)["space_type"], kind)
                self.assertEqual(lighting_context_faults(shot), [])

    def test_negated_transition_side_does_not_resolve_location(self):
        self.check_fault({"空间站位描述": "门槛处，女子并不在室内侧。"}, "本镜所在")

    def test_explicit_transition_side_still_enforces_indoor_clarity(self):
        self.check_fault({"空间站位描述": "门槛处，女子站在室内侧。", "光影": "空气清澈，但背景泛白，高光不过曝。"}, "相反肯定事实")

    def test_transition_side_is_anchored_without_extra_shot_fields(self):
        shot = {**clean_room(), "空间站位描述": "门槛处，女子站定。"}
        anchor = {"SC1": {"space_type": "transition", "shot_space_types": {"EP01-001": "indoor"}}}
        self.assertEqual(lighting_context_faults(shot, anchor), [])

    def test_transition_side_cannot_override_authored_opposite_side(self):
        anchor = {"SC1": {"space_type": "transition", "shot_space_types": {"EP01-001": "outdoor"}}}
        self.check_fault({}, "本镜锚定侧", anchor)

    def test_clarity_requires_all_three_predicates(self):
        cases = [
            ("背景木构清晰可辨，高光不过曝。", "空气状态"),
            ("空气清澈，高光不过曝。", "背景结构"),
            ("空气清澈，背景木构清晰可辨。", "曝光依据"),
        ]
        for light, missing in cases:
            with self.subTest(missing=missing):
                self.check_fault({"光影": light}, missing)

    def test_clear_slogan_cannot_cancel_white_background(self):
        self.check_fault({"光影": clean_room()["光影"] + "但背景泛白。"}, "相反肯定事实")

    def test_negative_clear_air_is_not_evidence(self):
        self.check_fault({"光影": "没有清澈的空气，背景清楚，高光不过曝。"}, "空气状态")

    def test_unreadable_background_is_not_positive_evidence(self):
        self.check_fault({"光影": "空气清澈，背景不可辨，高光不过曝。"}, "相反肯定事实")

    def test_normal_exposure_does_not_cancel_overexposure(self):
        self.check_fault({"光影": clean_room()["光影"] + "然而窗纸过曝。"}, "曝光过度")

    def test_negated_air_turbidity_is_not_an_adverse_fact(self):
        for statement in ("空气并不浑浊。", "空气不混浊。", "空气不是雾蒙蒙。"):
            with self.subTest(statement=statement):
                shot = {**clean_room(), "光影": clean_room()["光影"] + statement}
                self.assertEqual(lighting_context_faults(shot), [])

    def test_negated_air_fact_does_not_hide_later_turbidity(self):
        self.check_fault({"光影": clean_room()["光影"] + "空气并不浑浊，但空气浑浊。"}, "空气浑浊")

    def test_negated_air_fact_does_not_hide_white_background(self):
        self.check_fault({"光影": clean_room()["光影"] + "空气并不浑浊，但背景泛白。"}, "背景或画面泛白")

    def test_unreadable_claim_does_not_satisfy_readable_predicate(self):
        self.check_fault({"光影": "空气清澈，背景不可辨，高光不过曝。"}, "背景结构可辨证据")

    def test_counterfact_in_physical_fields_also_rejected(self):
        self.check_fault({"环境动态": "背景泛白，木墙模糊不清。"}, "相反肯定事实")

    def test_negative_haze_enumeration_is_not_positive(self):
        shot = {**clean_room(), "生成限制": "无烟雾、细烟、halation，不生成光柱。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_negation_ends_at_affirmative_clause(self):
        self.check_fault({"光影": clean_room()["光影"] + "无烟雾、但出现细烟。"}, "烟气缺")

    def test_candle_does_not_authorize_smoke(self):
        self.check_fault({"起始状态": "案上红烛燃着。", "光影": clean_room()["光影"] + "局部一缕细烟。"}, "不自动授权烟雾")

    def test_real_local_smoke_preserves_other_area_clarity(self):
        shot = {**clean_room(), "起始状态": "案上香炉已点燃。", "光影": "仅香炉口上方一缕细烟，不扩散至人物和背景；其他区域空气清澈，背景木构清晰可辨，高光不过曝。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_outdoor_backlight_without_haze_passes(self):
        shot = {**clean_room(), "空间站位描述": "午后室外庭院。", "光源来源": "真实太阳日光为主光，天空弱补面部。", "光影": "空气清澈，肩线克制轮廓光，高光不过曝。", "光源方向": "太阳从左后侧照亮人物。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_outdoor_backlight_needs_source(self):
        self.check_fault({"空间站位描述": "午后庭院。", "光源来源": "无来源的美学光。", "光影": "人物轮廓光。"}, "室外逆光缺")

    def test_outdoor_visible_beam_needs_medium(self):
        self.check_fault({"空间站位描述": "午后庭院，空气清澈。", "光影": "太阳形成可见光束。"}, "散射介质")

    def test_negative_medium_does_not_authorize_beam(self):
        self.check_fault({"空间站位描述": "室外庭院，无薄雾、烟尘。", "光影": "太阳形成光柱。"}, "散射介质")

    def test_established_outdoor_mist_authorizes_beam(self):
        shot = {**clean_room(), "空间站位描述": "室外庭院，晨间雨雾仍在。", "光影": "太阳形成局部可见光束，高光不过曝。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_real_flame_edge_micro_halation_passes(self):
        shot = {**clean_room(), "空间站位描述": "夜晚卧房，左窗关闭。", "起始状态": "案上红烛燃着。", "光源来源": "烛光为主光，浅墙反射弱补。", "光影": clean_room()["光影"] + "仅烛火边缘出现局部极轻微halation。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_first_local_halation_does_not_authorize_second_global(self):
        shot = {**clean_room(), "起始状态": "案上红烛燃着。", "光源来源": "烛光为主光。", "光影": clean_room()["光影"] + "仅烛火边缘出现局部极轻微halation。全画面halation使人物朦胧。"}
        self.assertTrue(any("仅限" in row for row in lighting_context_faults(shot)))

    def test_material_reflection_is_not_halation(self):
        self.check_fault({"光影": clean_room()["光影"] + "金属边缘出现局部极轻微halation。"}, "局部反射高光")

    def test_material_specular_highlights_pass(self):
        shot = {**clean_room(), "光影": clean_room()["光影"] + "铜镜与湿石板仅有局部反射高光。"}
        self.assertEqual(lighting_context_faults(shot), [])

    def test_legacy_checker_is_preserved_and_input_not_mutated(self):
        shot = clean_room()
        before = copy.deepcopy(shot)
        captured = []
        def old_check(value):
            captured.append(value)
            return ["原逐窗门禁结果"]
        self.assertIn("原逐窗门禁结果", lighting_context_faults(shot, legacy_checker=old_check))
        self.assertEqual(shot, before)
        self.assertTrue(captured[0]["空间站位描述"].startswith("室内；"))


class LightingContinuityTest(unittest.TestCase):
    def shots(self):
        left = clean_room()
        return left, {**left, "镜头编号": "EP01-002"}

    def test_equivalent_falloff_does_not_warn(self):
        left, right = self.shots()
        right["光源方向"] = right["光源方向"].replace("平缓衰减", "柔和衰减")
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_reversed_light_path_does_not_collapse_to_same_side_set(self):
        left, right = self.shots()
        left["光源方向"] = "画面左侧至右侧"
        right["光源方向"] = "画面右侧至左侧"
        self.assertTrue(any("反打机位依据" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_equivalent_light_path_and_falloff_wording_passes(self):
        left, right = self.shots()
        left["光源方向"] = "画面左侧至右侧平缓衰减。"
        right["光源方向"] = "从画面左侧向右侧柔和衰减。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_unchanged_main_and_fill_paths_are_not_a_reversal(self):
        left, right = self.shots()
        left["光源方向"] = "主光从左侧至右侧衰减；补光从右侧至左侧柔和衰减。"
        right["光源方向"] = left["光源方向"]
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_reversed_path_requires_real_camera_change_not_reverse_label(self):
        left, right = self.shots()
        left["光源方向"] = "画面左侧至右侧"
        right["光源方向"] = "画面右侧至左侧"
        right["机位"] += "合理反打。"
        anchor = {"SC1": {"lighting_state": {"source_id": "north_window", "world_position": "north_wall"}}}
        self.assertTrue(any("反打机位依据" in row for row in scene_lighting_continuity_faults([left, right], anchor)))
        right["机位"] = "摄影机位于场景东侧，向原位置反打。"
        self.assertEqual(scene_lighting_continuity_faults([left, right], anchor), [])

    def test_golden_light_continuity_mutation_reaches_main_validator(self):
        from run_golden_tests import apply_mutation, inject_pg1_fixture_evidence, upgrade_fixture_to_compact
        from validate_storyboard import validate_project
        suite = json.loads((Path(__file__).resolve().parent.parent / "references" / "golden_tests.json").read_text(encoding="utf-8"))
        test = next(row for row in suite["tests"] if row["test_id"] == "G-R10-LIGHT-CONTINUITY")
        for mutated in (False, True):
            with self.subTest(mutated=mutated):
                data = copy.deepcopy(suite["fixtures"][test["fixture_id"]])
                if suite.get("inject_pg1_fixture_evidence"):
                    inject_pg1_fixture_evidence(data)
                if mutated:
                    for mutation in test["mutations"]:
                        apply_mutation(data, mutation)
                upgrade_fixture_to_compact(data)
                codes = {issue["error_code"] for issue in validate_project(data, mode=test["mode"])["issues"]}
                self.assertEqual("E-LIGHT-002" in codes, mutated)

    def test_equivalent_candle_names_do_not_warn(self):
        left, right = self.shots()
        left["光源来源"] = "烛火为主光，浅墙反射弱补。"
        right["光源来源"] = "红烛形成主光，浅墙反射柔和补光。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_actual_source_change_warns(self):
        left, right = self.shots()
        right["光源来源"] = "烛火为主光，浅墙反射弱补。"
        self.assertTrue(any("光源类型" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_actual_world_position_change_warns(self):
        left, right = self.shots()
        left["光源来源"] = "东窗窗纸透入天光。"
        right["光源来源"] = "西窗窗纸透入天光。"
        self.assertTrue(any("场景位置" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_equivalent_east_window_location_does_not_warn(self):
        left, right = self.shots()
        left["光源来源"] = "东窗窗纸透入天光。"
        right["光源来源"] = "东侧窗窗纸透入天光。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_side_word_normalization_preserves_direction(self):
        left, right = self.shots()
        left["光源来源"] = "东侧窗窗纸透入天光。"
        right["光源来源"] = "西侧窗窗纸透入天光。"
        self.assertTrue(any("场景位置" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_side_word_normalization_preserves_opening_identity(self):
        left, right = self.shots()
        left["光源来源"] = "东侧窗透入天光。"
        right["光源来源"] = "东侧门透入天光。"
        self.assertTrue(any("场景位置" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_common_anchor_cannot_hide_authored_position_change(self):
        left, right = self.shots()
        left["光源来源"] = "东窗窗纸透入天光。"
        right["光源来源"] = "西窗窗纸透入天光。"
        anchors = {"SC1": {"lighting_state": {"source_id": "east_window", "world_position": "east_wall"}}}
        self.assertTrue(any("明示光源场景位置" in row for row in scene_lighting_continuity_faults([left, right], anchors)))

    def test_reasoned_reverse_shot_passes(self):
        left, right = self.shots()
        right["光源方向"] = "右前侧略亮，向背景柔和衰减。"
        right["机位"] = "摄影机位于场景东侧。"
        anchor = {"SC1": {"space_type": "indoor", "lighting_state": {"source_id": "north_window", "world_position": "north_wall"}}}
        self.assertEqual(scene_lighting_continuity_faults([left, right], anchor), [])

    def test_reverse_shot_word_alone_not_proof(self):
        left, right = self.shots()
        right["光源方向"] = "右前侧略亮，向背景柔和衰减。"
        right["机位"] = "合理反打。"
        self.assertTrue(any("反打机位依据" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_door_state_change_needs_its_own_event(self):
        left, right = self.shots()
        right["空间站位描述"] = "清晨卧房，左窗打开，窗纸完整。"
        right["画面内容"] = "女子打开右窗。"
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_matching_door_event_passes(self):
        left, right = self.shots()
        right["空间站位描述"] = "清晨卧房，左窗打开，窗纸完整。"
        right["画面内容"] = "女子打开左窗。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_left_shot_opens_window_and_keeps_it_open_at_cut(self):
        left, right = self.shots()
        left["画面内容"] = "【0.0—1.0秒】女子走近左窗。【1.0—3.0秒】女子打开左窗。【3.0—4.0秒】女子放开窗闩，左窗保持敞开。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_ordered_operations_keep_two_window_identities_distinct(self):
        left, right = self.shots()
        left["空间站位描述"] += "右窗打开。"
        left["画面内容"] = "女子打开左窗后又关闭右窗；镜尾左窗保持敞开，右窗保持关闭。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开") + "右窗关闭。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_left_window_half_open_event_keeps_exact_destination(self):
        left, right = self.shots()
        left["画面内容"] = "女子把左窗半开，镜尾左窗保持半开。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗半开")
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])
        right["空间站位描述"] = right["空间站位描述"].replace("左窗半开", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_opening_window_latch_does_not_open_window_itself(self):
        left, right = self.shots()
        left["画面内容"] = "女子打开左窗闩，镜尾左窗保持敞开。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_right_shot_cannot_omit_left_terminal_window_state(self):
        left, right = self.shots()
        left["画面内容"] = "女子打开右窗。"
        self.assertTrue(any("右窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_left_shot_open_then_close_does_not_authorize_open_start(self):
        left, right = self.shots()
        left["画面内容"] = "女子打开左窗；镜尾女子关闭左窗，左窗保持紧闭。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_left_shot_open_event_must_match_next_start_even_if_old_starts_match(self):
        left, right = self.shots()
        left["画面内容"] = "女子打开左窗，镜尾左窗保持敞开。"
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_left_shot_cannot_borrow_another_window_event(self):
        left, right = self.shots()
        left["画面内容"] = "女子打开右窗，看向左窗；镜尾左窗保持敞开。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_negated_window_events_do_not_authorize_transition(self):
        for field in ("left", "right"):
            for description in ("女子并未打开左窗。", "女子让左窗保持关闭，左窗并未打开。", "禁止打开左窗。"):
                with self.subTest(field=field, description=description):
                    left, right = self.shots()
                    (left if field == "left" else right)["画面内容"] = description
                    right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
                    self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_window_operation_in_dialogue_is_not_a_visible_event(self):
        left, right = self.shots()
        left["画面内容"] = "女子说道：“打开左窗。”"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_terminal_open_state_without_operation_does_not_authorize_transition(self):
        left, right = self.shots()
        left["画面内容"] = "女子站定，镜尾左窗保持打开。"
        right["空间站位描述"] = right["空间站位描述"].replace("左窗关闭", "左窗打开")
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_door_transition_must_match_destination_state(self):
        left, right = self.shots()
        right["空间站位描述"] = "清晨卧房，左窗打开，窗纸完整。"
        right["画面内容"] = "女子关闭左窗。"
        self.assertTrue(any("左窗" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_unexplained_time_jump_warns(self):
        left, right = self.shots()
        right["空间站位描述"] = right["空间站位描述"].replace("清晨", "夜晚")
        self.assertTrue(any("时段变化" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_common_anchor_cannot_hide_authored_time_jump(self):
        left, right = self.shots()
        right["空间站位描述"] = right["空间站位描述"].replace("清晨", "夜晚")
        anchors = {"SC1": {"lighting_state": {"time_of_day": "清晨"}}}
        self.assertTrue(any("时段变化" in row for row in scene_lighting_continuity_faults([left, right], anchors)))

    def test_unrelated_candle_event_cannot_authorize_sun_to_moon(self):
        left, right = self.shots()
        left["光源来源"] = "太阳日光。"
        right["光源来源"] = "月光。"
        right["画面内容"] = "女子点燃红烛。"
        self.assertTrue(any("光源类型" in row for row in scene_lighting_continuity_faults([left, right])))

    def test_camera_turn_cannot_change_source_identity(self):
        left, right = self.shots()
        right["机位"] = "摄影机位于场景东侧。"
        right["光源方向"] = "右前侧略亮。"
        anchor = {"SC1": {"lighting_states": {"EP01-001": {"source_id": "east_window", "world_position": "east_wall"}, "EP01-002": {"source_id": "west_window", "world_position": "west_wall"}}}}
        self.assertTrue(any("光源身份" in row for row in scene_lighting_continuity_faults([left, right], anchor)))

    def test_scene_cut_does_not_compare_unrelated_sources(self):
        left, right = self.shots()
        right["场景连续性ID"] = "SC2"
        right["光源来源"] = "烛火主光。"
        self.assertEqual(scene_lighting_continuity_faults([left, right]), [])

    def test_grouped_nonadjacent_rows_are_not_invented_neighbors(self):
        left, right = self.shots()
        right["光源来源"] = "烛火。"
        self.assertEqual(scene_lighting_continuity_faults([(0, left), (3, right)]), [])

    def test_warm_to_cool_is_physical_change(self):
        left, right = self.shots()
        left["色温倾向"] = "背景微暖，肤色自然。"
        right["色温倾向"] = "背景偏冷，肤色自然。"
        self.assertTrue(any("暖冷关系" in row for row in scene_lighting_continuity_faults([left, right])))


if __name__ == "__main__":
    unittest.main()
