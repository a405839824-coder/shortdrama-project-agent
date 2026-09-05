"""Regression tests for person- and utterance-bound performance checks."""
import copy
import unittest

from performance_checks import emphasis_binding_faults, emphasis_bindings, eye_contract_faults


LIMITS = "眼神自然聚焦；眼球换焦或位移；眼睑松紧变化；禁止无依据全程不眨眼；禁止僵直瞪视；禁止高频机械眨眼；禁止多人同步眨眼。"


def eye_shot(names=("甲",), *, duration=4.0):
    first = []
    final = []
    for index, name in enumerate(names):
        first.append(f"{name}视线从袖口移向茶盏，眼球先轻微换焦，头部随后轻跟，上眼睑略压，{0.2 + index * 0.2:.1f}秒在换焦后自然眨眼一次")
        final.append(f"{name}视线最终落在茶盏")
    return {
        "镜头类型": "人物中近景，腰部以上", "可见主体": "、".join(names),
        "画面内容": f"【0.0—0.8秒】{'；'.join(first)}。【0.8—3.2秒】甲保持自然呼吸。【3.2—{duration:.1f}秒】{'；'.join(final)}。",
        "起始状态": "；".join(f"{name}双眼自然睁开，不处于半眨眼瞬间" for name in names),
        "生成限制": LIMITS,
    }


class EmphasisBindingTests(unittest.TestCase):
    def test_single_speaker_all_real_words(self):
        self.assertEqual([], emphasis_binding_faults("落在“回来”和“好吗”", [("甲", "回来，好吗？")]))

    def test_one_real_word_does_not_cover_fake_word(self):
        faults = emphasis_binding_faults("“回来”和“不存在”", [("甲", "回来，好吗？")])
        self.assertTrue(any("不存在" in fault for fault in faults))

    def test_speaker_cannot_borrow_another_speakers_word(self):
        faults = emphasis_binding_faults("甲：“别走”；乙：“回来”", [("甲", "回来！"), ("乙", "别走！")])
        self.assertEqual(2, len(faults))

    def test_multiple_speakers_explicit_binding(self):
        self.assertEqual([], emphasis_binding_faults("甲：“回来”；乙：“别走”", [("甲", "回来！"), ("乙", "别走！")]))

    def test_possessive_speaker_label(self):
        self.assertEqual([], emphasis_binding_faults("甲的重音：“回来”；乙的重音：“别走”", [("甲", "回来！"), ("乙", "别走！")]))

    def test_multiple_speakers_require_names(self):
        self.assertTrue(emphasis_binding_faults("“回来”", [("甲", "回来！"), ("乙", "别走！")]))

    def test_words_cannot_bridge_utterances(self):
        self.assertTrue(emphasis_binding_faults("“你走”", [("甲", "是你"), ("甲", "走吧")]))

    def test_explicit_utterance_binds_exact_occurrence(self):
        bindings, faults = emphasis_bindings("甲第2句：“走”", [("甲", "你走。"), ("甲", "走吧。")])
        self.assertEqual([], faults)
        self.assertEqual(1, bindings[0]["utterance_index"])
        self.assertEqual([[0, 1]], bindings[0]["character_ranges"])

    def test_repeated_utterance_requires_disambiguation(self):
        self.assertTrue(emphasis_binding_faults("“走”", [("甲", "你走。"), ("甲", "走吧。")]))

    def test_punctuation_exact(self):
        self.assertTrue(emphasis_binding_faults("“回来！”", [("甲", "回来？")]))

    def test_broken_quotes_do_not_drop_one_word(self):
        self.assertTrue(emphasis_binding_faults('“回来”和“假词', [("甲", "回来！")]))

    def test_unknown_explicit_speaker_does_not_fall_back_to_single_speaker(self):
        self.assertTrue(emphasis_binding_faults("乙：“回来”", [("甲", "回来！")]))

    def test_repeated_word_preserves_every_actual_range(self):
        bindings, faults = emphasis_bindings("“走”", [("甲", "走，走吧。")])
        self.assertEqual([], faults)
        self.assertEqual([[0, 1], [2, 3]], bindings[0]["character_ranges"])


class EyeContractTests(unittest.TestCase):
    def test_single_person_complete_chain(self):
        self.assertEqual([], eye_contract_faults(eye_shot(), duration=4.0))

    def test_three_individual_chains_pass(self):
        self.assertEqual([], eye_contract_faults(eye_shot(("甲", "乙", "丙")), duration=4.0))

    def test_one_person_cannot_cover_three_people(self):
        shot = eye_shot()
        shot["可见主体"] = "甲、乙、丙"
        shot["起始状态"] = "三人双眼自然睁开，不处于半眨眼瞬间。"
        faults = eye_contract_faults(shot, duration=4.0)
        self.assertTrue(any(fault.startswith("乙[") for fault in faults))
        self.assertTrue(any(fault.startswith("丙[") for fault in faults))

    def test_cast_count_shorthand_does_not_become_one_fake_identity(self):
        shot = eye_shot()
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["可见主体"] = "甲等三人"
        self.assertTrue(any("人物身份" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_complete_names_with_matching_cast_count_remain_valid(self):
        shot = eye_shot(("甲", "乙", "丙"))
        shot["可见主体"] = "甲、乙、丙等三人"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_gaze_object_does_not_steal_subject(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("甲视线从袖口移向茶盏", "甲视线从乙袖口移向乙面部")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_single_person_final_hold_inherits_latest_same_shot_target(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲视线最终落在茶盏", "她保持视线、站位与稳定呼吸")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_final_hold_cannot_inherit_a_choice_of_targets(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("移向茶盏", "移向茶盏或窗外").replace("甲视线最终落在茶盏", "她保持视线、站位与稳定呼吸")
        self.assertTrue(any("最终视线落点" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_unresolved_gaze_change_invalidates_prior_target_before_hold(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲视线移开，甲保持自然呼吸").replace("甲视线最终落在茶盏", "她保持视线、站位与稳定呼吸")
        self.assertTrue(any("最终视线落点" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_unresolved_gaze_change_after_hold_invalidates_hold(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲视线最终落在茶盏", "她保持视线，随后视线游移")
        self.assertTrue(any("最终视线落点" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_multicast_pronoun_hold_cannot_borrow_a_target(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("甲视线最终落在茶盏；乙视线最终落在茶盏", "她保持视线、站位与稳定呼吸")
        faults = eye_contract_faults(shot, duration=4.0)
        self.assertTrue(any(fault.startswith("甲[") and "最终视线落点" in fault for fault in faults))
        self.assertTrue(any(fault.startswith("乙[") and "最终视线落点" in fault for fault in faults))

    def test_negated_final_hold_does_not_supply_end_gaze(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲视线最终落在茶盏", "她不再保持视线")
        self.assertTrue(any("最终视线落点" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_another_persons_named_gaze_cannot_supply_single_person_hold(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "乙视线移向窗外，甲保持自然呼吸").replace("甲视线最终落在茶盏", "她保持视线")
        self.assertTrue(any("最终视线落点" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_observer_cannot_borrow_observed_persons_eye_movement(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("眼球先轻微换焦", "甲观察乙眼球先轻微换焦", 1)
        self.assertTrue(any(fault.startswith("甲[") and "缺轻微眼球" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_eyelid_noun_alone_is_not_change(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("上眼睑略压", "上眼睑可见")
        self.assertTrue(any("眼睑松紧" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_eyelid_gentle_downward_press_is_a_real_change(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("上眼睑略压", "上眼睑略微下压")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_negated_focus_cannot_satisfy_performance(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("眼球先轻微换焦", "禁止眼球先轻微换焦")
        self.assertTrue(any("缺轻微眼球" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_negative_clause_cannot_hide_later_positive_blink(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("0.2秒在换焦后自然眨眼一次", "禁止机械眨眼，0.2秒在换焦后自然眨眼一次")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_numbers_check_actual_staggering(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("0.4秒", "0.2秒") + "两人眨眼错开。"
        self.assertTrue(any("时点相同" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_short_exposure_does_not_waive_actual_sync_with_long_exposure(self):
        shot = eye_shot(("甲", "乙"))
        shot["可见主体"] = "甲、乙（眼睛仅在0.0—1.5秒清楚可见）"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("0.4秒", "0.2秒")
        self.assertTrue(any("时点相同" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_all_short_exposures_still_forbid_actual_sync(self):
        shot = eye_shot(("甲", "乙"))
        shot["可见主体"] = "甲（眼睛仅在0.0—1.5秒清楚可见）、乙（眼睛仅在0.0—1.5秒清楚可见）"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("0.4秒", "0.2秒")
        self.assertTrue(any("时点相同" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_out_of_phase_blink_fails(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("0.2秒", "2.2秒")
        self.assertTrue(any("所属时间阶段" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_hidden_person_does_not_waive_visible_person(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（面部可见）、乙（背影）"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("上眼睑略压", "肩膀略沉")
        self.assertTrue(any(fault.startswith("甲[") for fault in eye_contract_faults(shot, duration=4.0)))

    def test_listener_shoulder_is_not_a_second_visible_face(self):
        shot = eye_shot()
        shot["可见主体"] = "甲面部可见、听者肩缘可辨"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_single_person_body_enumeration_keeps_face_visibility(self):
        shot = eye_shot(("女主",))
        shot["镜头类型"] = "中景"
        shot["可见主体"] = "女主上半身、面部与双手可见"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["可见主体"] = "女主面部、肩颈与右手可见"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_body_enumeration_cannot_lend_another_person_face_visibility(self):
        shot = eye_shot(("甲", "乙"))
        shot["镜头类型"] = "中景"
        shot["可见主体"] = "甲上半身、面部与双手可见；乙背影、双手可见"
        faults = eye_contract_faults(shot, duration=4.0)
        self.assertTrue(any("乙眼部不可见" in fault for fault in faults))
        self.assertFalse(any("甲眼部不可见" in fault for fault in faults))

    def test_hidden_person_cannot_have_eye_animation(self):
        shot = eye_shot(("甲", "乙"))
        shot["可见主体"] = "甲（面部可见）、乙（背影）"
        self.assertTrue(any("乙眼部不可见" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_exact_short_visibility_interval_exemption(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛仅在0.0—1.5秒清楚可见）"
        shot["画面内容"] = "【0.0—0.8秒】甲看向茶盏。【0.8—3.2秒】甲转身。【3.2—4.0秒】甲背向机位。"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_unbounded_visibility_claim_does_not_waive_gate(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛可见1秒）"
        shot["画面内容"] = "【0.0—0.8秒】甲看向茶盏。【0.8—3.2秒】甲坐着。【3.2—4.0秒】甲仍坐着。"
        self.assertTrue(eye_contract_faults(shot, duration=4.0))

    def test_negated_short_visibility_is_not_an_exemption(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛并非仅在0.0—1.5秒清楚可见）"
        shot["画面内容"] = "【0.0—0.8秒】甲看向茶盏。【0.8—3.2秒】甲坐着。【3.2—4.0秒】甲仍坐着。"
        self.assertTrue(eye_contract_faults(shot, duration=4.0))

    def test_short_visibility_does_not_allow_offscreen_blink(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛仅在0.0—1.5秒清楚可见）"
        shot["画面内容"] = shot["画面内容"].replace("0.2秒", "2.0秒")
        self.assertTrue(any("可见区间" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_visibility_interval_must_fit_shot(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛仅在0.0—7.5秒清楚可见）"
        self.assertTrue(any("越界" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_justified_no_blink_keeps_substitute_motion(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("0.2秒在换焦后自然眨眼一次", "甲因认出熟人本镜不眨眼")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_same_person_adjacent_causal_clause_can_justify_no_blink(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("0.2秒在换焦后自然眨眼一次", "因认出熟人而高度警觉，本镜不眨眼")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_full_shot_no_blink_conflicts_with_real_blink(self):
        shot = eye_shot()
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲因认出熟人本镜不眨眼，甲保持自然呼吸")
        self.assertTrue(any("决策相互矛盾" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_phase_no_blink_conflicts_only_with_same_phase_blink(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲因认出熟人本段不眨眼，甲保持自然呼吸")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲保持自然呼吸，1.2秒换气后眨眼一次")
        self.assertTrue(any("同段眨眼" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_emphasis_no_blink_does_not_forbid_pre_speech_blink(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲本镜重音期间不眨眼，甲保持自然呼吸")
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_other_person_reason_cannot_justify_no_blink(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("0.2秒在换焦后自然眨眼一次", "甲本镜不眨眼").replace("0.4秒在换焦后自然眨眼一次", "乙因认出熟人本镜不眨眼，乙自然呼吸")
        self.assertTrue(any("甲[" in fault and "剧情理由" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_static_collective_open_eye_state_allowed(self):
        shot = eye_shot(("甲", "乙", "丙"))
        shot["起始状态"] = "三人双眼自然睁开，不处于半眨眼瞬间。"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_anonymous_static_claim_is_not_collective_evidence(self):
        shot = eye_shot(("甲", "乙"))
        shot["起始状态"] = "双眼自然睁开，不处于半眨眼瞬间。"
        faults = eye_contract_faults(shot, duration=4.0)
        self.assertTrue(any(fault.startswith("甲[") and "起始状态" in fault for fault in faults))
        self.assertTrue(any(fault.startswith("乙[") and "起始状态" in fault for fault in faults))

    def test_non_strict_does_not_require_static_keyframe_phrase(self):
        shot = eye_shot()
        shot["起始状态"] = "甲坐在椅上。"
        self.assertEqual([], eye_contract_faults(shot, duration=4.0, strict=False))

    def test_every_speaker_needs_own_emphasis_no_blink(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲保持自然呼吸，甲重音期间不眨眼；乙保持自然呼吸")
        faults = eye_contract_faults(shot, duration=4.0, dialogue_rows=[("甲", "回来"), ("乙", "不走")])
        self.assertTrue(any(fault.startswith("乙[") and "重音" in fault for fault in faults))
        self.assertFalse(any(fault.startswith("甲[") for fault in faults))

    def test_dialogue_words_do_not_supply_eye_instructions(self):
        shot = eye_shot()
        shot["画面内容"] = shot["画面内容"].replace("眼球先轻微换焦", "甲说道：“眼球先轻微换焦。”")
        self.assertTrue(any("缺轻微眼球" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_count_each_person_not_total_cast_blinks(self):
        shot = eye_shot(("甲", "乙", "丙"))
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))
        shot["画面内容"] = shot["画面内容"].replace("甲保持自然呼吸", "甲保持自然呼吸，2.0秒在换气后眨眼一次")
        self.assertTrue(any("甲[" in fault and "累计2次" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_actual_synchronized_blink_is_not_negative_clause(self):
        shot = eye_shot(("甲", "乙"))
        shot["画面内容"] += "禁止机械眨眼，两人同时眨眼。"
        self.assertTrue(any("多人出现同步" in fault for fault in eye_contract_faults(shot, duration=4.0)))

    def test_prop_closeup_does_not_create_a_person(self):
        shot = {"镜头类型": "茶杯特写", "可见主体": "无人物、茶杯", "画面内容": "茶杯置于桌面。"}
        self.assertEqual([], eye_contract_faults(shot, duration=4.0))

    def test_short_face_exposure_still_rejects_mechanical_blinks(self):
        shot = eye_shot()
        shot["可见主体"] = "甲（眼睛仅在0.0—1.5秒清楚可见）"
        shot["画面内容"] = shot["画面内容"].replace("眨眼一次", "眨眼三次")
        self.assertTrue(any("机械安排" in fault for fault in eye_contract_faults(shot, duration=4.0)))


if __name__ == "__main__":
    unittest.main()
