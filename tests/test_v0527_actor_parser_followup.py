"""Frozen parser controls and bounded natural-language polarity regressions.

These test software evidence consumption, never score an authored manuscript.
The 26 expectations predate the repair. The earlier eight integration fixtures
that extracted no dialogue remain in the evaluation history, not this oracle.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

SKILL_SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/shortdrama-storyboard/scripts'
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
import validate_storyboard as validator
from performance_checks import _people, eye_contract_faults, listener_reaction_faults


CASES = json.loads((Path(__file__).parent / 'fixtures/v0527_actor_parser_followup_controls.json').read_text('utf-8'))['cases']
BY_ID = {case['id']: case for case in CASES}


def eye_target(faults, probe):
    if probe == 'all':
        return bool(faults)
    terms = {'path': ['缺视线从起点'], 'eyelid': ['缺上/下眼睑'],
             'hidden': ['眼部不可见'],
             'open_eye_chain': ['缺视线从起点', '缺眼球先动', '缺轻微眼球', '缺逐人眨眼']}[probe]
    return any(term in fault for term in terms for fault in faults)


def run_case(case):
    value = copy.deepcopy(case['input'])
    if case['kind'] == 'eye':
        faults = validator._eye_contract_faults(value, duration=case['duration'], strict=True)
        target = eye_target(faults, case['probe'])
    else:
        rows = validator.extract_dialogues(value['shots'][0]['画面内容'])
        assert rows == [('甲', '请坐。')], rows  # prove this consumer actually runs
        report = validator.validate_project(value, mode='audit')
        faults = [i for i in report['issues'] if i['error_code'] == 'E-PERFORMANCE-001'
                  and '多人对白镜未写听者错峰反应' in i['evidence']]
        target = bool(faults)
    assert value == case['input'], 'a validator must not rewrite the control'
    return target, faults


class FrozenActorParserControls(unittest.TestCase):
    pass


def _frozen_test(case):
    def test(self):
        target, faults = run_case(case)
        self.assertEqual(case['expected_target_fault_present'], target, faults)
    return test


for _case in CASES:
    setattr(FrozenActorParserControls, 'test_' + _case['id'].replace('-', '_'), _frozen_test(_case))


class NaturalActorParserBoundaries(unittest.TestCase):
    def eye(self, replacement, original='甲眼球先从案边移向门框'):
        shot = copy.deepcopy(BY_ID['E01']['input'])
        shot['画面内容'] = shot['画面内容'].replace(original, replacement)
        return eye_contract_faults(shot, duration=4.0)

    def reaction(self, response, visible='甲；乙'):
        shot = copy.deepcopy(BY_ID['P03-DIALOGUEBOUND']['input']['shots'][0])
        shot['可见主体'] = visible
        shot['画面内容'] = shot['画面内容'].replace('不看镜头。', '不看镜头。' + response)
        return listener_reaction_faults(shot, duration=4.0, dialogue_rows=[('甲', '请坐。')])

    def test_from_to_synonyms_are_positive_with_distinct_points(self):
        for path in ('甲视线由案边轻微移焦到门框', '甲眼球从案边缓慢换焦至门框',
                     '甲目光从门框左端移向门框右端', '甲视线从无字的纸边移向门框'):
            with self.subTest(path=path):
                self.assertFalse(eye_target(self.eye(path), 'path'))

    def test_negation_at_each_movement_predicate_is_not_path(self):
        for path in ('甲眼球未从案边移向门框', '甲眼球从案边不移向门框',
                     '甲眼球没有从案边轻微换焦至门框', '甲眼球禁止从案边移向门框',
                     '甲眼球从案边并未移向门框'):
            with self.subTest(path=path):
                self.assertTrue(eye_target(self.eye(path), 'path'))

    def test_same_endpoint_does_not_pass_with_motion_adverb(self):
        for path in ('甲眼球从门框同一木纹点轻微移向门框同一木纹点',
                     '甲眼球从案边换焦至案边', '甲视线从案边移向原处'):
            with self.subTest(path=path):
                self.assertTrue(eye_target(self.eye(path), 'path'))

    def test_negated_focus_does_not_count_but_later_affirmative_does(self):
        original = '甲眼球轻微换焦'
        for replacement in ('甲眼球不轻微换焦', '甲眼球并未换焦', '甲眼球禁止轻微换焦'):
            with self.subTest(replacement=replacement):
                self.assertTrue(any('缺轻微眼球' in f for f in self.eye(replacement, original)))
        self.assertFalse(any('缺轻微眼球' in f for f in self.eye('甲眼球不换焦；甲眼球随后轻微换焦', original)))

    def test_lid_change_cannot_be_borrowed_from_negation_or_static_state(self):
        original = '甲上眼睑放松；甲下眼睑收紧'
        for replacement in ('甲上眼睑不由紧转松；甲下眼睑不放平',
                            '甲上眼睑保持平稳；甲下眼睑柔和',
                            '甲禁止上眼睑微提；甲下眼睑并未轻收'):
            with self.subTest(replacement=replacement):
                self.assertTrue(eye_target(self.eye(replacement, original), 'eyelid'))
        self.assertFalse(eye_target(self.eye('甲上眼睑微提，下眼睑轻收', original), 'eyelid'))

    def test_zero_person_exact_forms_do_not_hide_real_names_or_later_people(self):
        self.assertEqual(_people('无人物入画；木盒（封口完整）', None)[0], [])
        self.assertEqual(_people('无忌（面部清晰可见）', None)[0], ['无忌'])
        self.assertEqual(_people('无人物入画；甲（面部清晰可见）', None)[0], ['甲'])

    def test_chest_above_is_not_a_hands_only_crop(self):
        shot = copy.deepcopy(BY_ID['E01']['input'])
        shot['可见主体'] = '甲（仅胸部以上，面部清晰可见）'
        shot['画面内容'] = '甲保持坐姿。'
        self.assertTrue(eye_target(eye_contract_faults(shot, duration=4.0), 'path'))

    def test_hidden_face_still_rejects_rendered_lid_changes(self):
        for descriptor in ('甲（仅胸腹衣襟与双手）', '甲（仅侧背，双手可见）'):
            with self.subTest(descriptor=descriptor):
                shot = copy.deepcopy(BY_ID['E11']['input']); shot['可见主体'] = descriptor
                shot['画面内容'] = '甲上眼睑微提。'
                self.assertTrue(eye_target(eye_contract_faults(shot, duration=4.0), 'hidden'))

    def test_whole_closed_exemption_needs_actual_source_and_no_reopening(self):
        for key, value in (('原文支撑句', '甲没有合眼睡着。'), ('原文支撑句', '乙合眼睡着。'),
                           ('画面内容', '甲全程闭眼休息，甲在2.0秒睁开双眼。'),
                           ('画面内容', '甲并非全程闭眼休息，呼吸自然。')):
            with self.subTest(key=key, value=value):
                shot = copy.deepcopy(BY_ID['E14']['input']); shot[key] = value
                self.assertTrue(eye_target(eye_contract_faults(shot, duration=4.0), 'open_eye_chain'))

    def test_closed_eyelid_motion_is_possible_but_open_eye_motion_is_not(self):
        shot = copy.deepcopy(BY_ID['E14']['input'])
        shot['画面内容'] += '甲闭合的上眼睑微微收紧。'
        self.assertEqual(eye_contract_faults(shot, duration=4.0), [])
        shot['画面内容'] += '甲眼球轻微换焦。'
        self.assertTrue(any('源文明确全程闭眼' in f for f in eye_contract_faults(shot, duration=4.0)))

    def test_numeric_time_cannot_transfer_other_person_eye_action(self):
        shot = copy.deepcopy(BY_ID['E18']['input'])
        shot['画面内容'] = shot['画面内容'].replace('1.8秒乙眼球先从书边移向窗框', '1.8秒乙观察甲眼球先从书边移向窗框')
        self.assertTrue(any(f.startswith('乙[') and '缺视线' in f for f in eye_contract_faults(shot, duration=4.0)))

    def test_later_named_reaction_accepts_numeric_or_heard_trigger(self):
        for response in ('在1.6秒乙轻点头一次。', '乙在1.6秒轻点头一次。',
                         '乙听完甲的话后，双手轻收。', '乙听清甲尾音后轻点头一次。'):
            with self.subTest(response=response):
                self.assertEqual(self.reaction(response), [])

    def test_environment_timing_or_negated_response_cannot_pass(self):
        for response in ('窗纸随后轻动。乙轻点头一次。', '乙随后不点头。',
                         '乙不随后点头。', '乙在1.6秒不轻点头。',
                         '乙不做延迟反应。', '乙说道：“随后轻点头。”'):
            with self.subTest(response=response):
                self.assertTrue(self.reaction(response))

    def test_reaction_before_speech_or_outside_stage_is_not_later_response(self):
        for response in ('0.1秒乙轻点头一次。', '4.8秒乙轻点头一次。'):
            with self.subTest(response=response):
                self.assertTrue(self.reaction(response))

    def test_one_listener_cannot_cover_second_visible_listener(self):
        faults = self.reaction('1.6秒乙轻点头一次。', '甲；乙；丙')
        self.assertTrue(any('丙' in f for f in faults))
        self.assertFalse(any('乙' in f for f in faults))

    def test_comma_parenthetical_never_creates_a_second_listener(self):
        self.assertEqual(self.reaction('', '甲（面部，双手，双眼可辨）'), [])

    def test_explicit_response_clock_is_stronger_than_causal_word(self):
        for moment in ('0.1', '0.5', '4.5'):
            with self.subTest(moment=moment):
                self.assertTrue(self.reaction(f'乙在{moment}秒随后轻点头。'))
        self.assertEqual(self.reaction('乙在1.7秒随后轻点头。'), [])
        self.assertEqual(self.reaction('乙听完甲的话后轻点头。'), [])

    def test_observed_actor_action_is_not_the_observers_listener_response(self):
        for response in ('乙看着甲在1.7秒轻点头。', '乙观察甲在1.7秒眼睑放松。',
                         '乙看见甲在1.7秒双手收紧。'):
            with self.subTest(response=response):
                self.assertTrue(self.reaction(response))
        # Complex wording is supported when the actual reaction belongs to
        # the listener; the observer's name does not erase that binding.
        self.assertEqual(self.reaction('甲看见乙在1.7秒轻点头。'), [])

    def test_source_closed_observation_and_quotation_are_not_actor_evidence(self):
        for source in ('甲看见乙闭眼睡着。', '甲说道：“乙合眼睡着了。”',
                       '乙说道：“甲合眼睡着了。”', '甲让乙闭眼睡着。'):
            with self.subTest(source=source):
                shot = copy.deepcopy(BY_ID['E14']['input']); shot['原文支撑句'] = source
                faults = eye_contract_faults(shot, duration=4.0)
                self.assertTrue(eye_target(faults, 'open_eye_chain'))
                self.assertTrue(any('待独立源文复核' in fault for fault in faults))

    def test_direct_closure_with_place_or_adverb_keeps_narrow_exemption(self):
        for source in ('甲已经合眼睡着。', '甲在榻上缓缓合眼睡着。',
                       '甲在乙身旁合眼睡着。', '乙走开了；甲合眼睡着。'):
            with self.subTest(source=source):
                shot = copy.deepcopy(BY_ID['E14']['input']); shot['原文支撑句'] = source
                self.assertEqual(eye_contract_faults(shot, duration=4.0), [])

    def test_explicit_actor_restarts_after_contrast_with_prior_negative(self):
        for connector in ('但', '但是', '然而'):
            with self.subTest(connector=connector):
                self.assertEqual(self.reaction(f'乙在1.7秒不再点头，{connector}乙在2.0秒轻点头。'), [])
                self.assertTrue(self.reaction(f'乙在1.7秒不再点头，{connector}甲在2.0秒轻点头。'))

    def test_quote_terminal_boundary_keeps_following_unquoted_actor_fact(self):
        shot = copy.deepcopy(BY_ID['E14']['input'])
        shot['原文支撑句'] = '乙轻声说道：“别叫醒他。”甲疲惫地合眼睡着。'
        self.assertEqual(eye_contract_faults(shot, duration=4.0), [])
        shot['原文支撑句'] = '乙轻声说道：“甲疲惫地合眼睡着。”'
        self.assertTrue(eye_target(eye_contract_faults(shot, duration=4.0), 'open_eye_chain'))

    def test_also_closing_eyes_still_requires_explicit_same_actor(self):
        shot = copy.deepcopy(BY_ID['E14']['input'])
        shot['原文支撑句'] = '甲看见乙闭眼，甲也合眼睡着。'
        self.assertEqual(eye_contract_faults(shot, duration=4.0), [])
        shot['原文支撑句'] = '甲看见乙也合眼睡着。'
        self.assertTrue(eye_target(eye_contract_faults(shot, duration=4.0), 'open_eye_chain'))


if __name__ == '__main__':
    unittest.main()
