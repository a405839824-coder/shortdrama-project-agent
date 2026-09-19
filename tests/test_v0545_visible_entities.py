"""Regression for typed visible entities and event-based prose inspection.

These cases check mapping and actual authored events, not semantic accuracy.
"""
from pathlib import Path
import sys
import copy
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'tests'), str(ROOT/'skills/shortdrama-storyboard/scripts')]
from visible_entities import resolve_visible_subjects, project_entity_registry
from performance_checks import _people, eye_contract_faults, listener_reaction_faults
from validate_storyboard import validate_project
from test_v0527_actor_parser_followup import BY_ID


class VisibleEntityMappingTests(unittest.TestCase):
    def test_short_words_are_not_assumed_to_be_people(self):
        names, _ = _people('许氏、杯盖、蜜饯、佛珠', None)
        self.assertEqual(names, [])
        resolved = resolve_visible_subjects('许氏、杯盖、蜜饯、佛珠', people=['许氏'])
        self.assertEqual(resolved.people, ('许氏',))
        self.assertEqual(resolved.unknown, ('杯盖', '蜜饯', '佛珠'))
        self.assertEqual(len(resolved.faults), 3)

    def test_registered_mixed_entities_keep_silent_actor(self):
        r = resolve_visible_subjects('许氏、婢女、杯盖、蜜饯、佛珠',
            people=['许氏', '婢女'], non_people=['杯盖', '蜜饯', '佛珠'], speakers=['许氏'])
        self.assertEqual(r.people, ('许氏', '婢女'))
        self.assertEqual(r.non_people, ('杯盖', '蜜饯', '佛珠'))
        self.assertEqual(r.faults, [])

    def test_explicit_types_do_not_need_an_object_blacklist(self):
        r = resolve_visible_subjects('人物：许氏；道具：蜜饯；物件：杯盖；环境：远处竹林')
        self.assertEqual(r.people, ('许氏',))
        self.assertEqual(r.non_people, ('蜜饯', '杯盖', '远处竹林'))
        self.assertEqual(r.faults, [])

    def test_silent_visible_body_is_resolved_without_dialogue(self):
        r = resolve_visible_subjects('许氏（面部可见）；婢女（仅双手可见）')
        self.assertEqual(r.people, ('许氏', '婢女'))
        self.assertFalse(r.faults)

    def test_unknown_silent_name_stays_pending(self):
        r = resolve_visible_subjects('许氏、未登记者', speakers=['许氏'])
        self.assertEqual(r.unknown, ('未登记者',))
        self.assertEqual(r.people, ('许氏',))

    def test_anonymous_numbered_faces_need_identity_mapping(self):
        r = resolve_visible_subjects('两人面部可见')
        self.assertFalse(r.people)
        self.assertTrue(r.faults)
        counted = resolve_visible_subjects('许氏等三人', people=['许氏'])
        self.assertTrue(counted.faults)

    def test_background_people_do_not_become_props_or_named_leads(self):
        r = resolve_visible_subjects('许氏（面部可见）；古代市集前中后景普通人物')
        self.assertEqual(r.people, ('许氏',))
        self.assertFalse(r.non_people)
        self.assertEqual(r.background_people, ('古代市集前中后景普通人物',))
        self.assertFalse(r.faults)

    def test_offscreen_speaker_not_added_to_visible_cast(self):
        r = resolve_visible_subjects('婢女（仅双手可见）', people=['许氏', '婢女'], speakers=['许氏'])
        self.assertEqual(r.people, ('婢女',))

    def test_body_in_gaze_target_or_holder_annotation_does_not_type_prop(self):
        r = resolve_visible_subjects('许氏（面部可见）；杯盖（在许氏右手）；蜜饯',
            evidence_texts=['许氏视线落在蜜饯。许氏说道：“蜜饯双眼睁开。”'])
        self.assertEqual(r.people, ('许氏',))
        self.assertEqual(r.unknown, ('杯盖', '蜜饯'))

    def test_type_conflicts_cannot_be_silently_downgraded(self):
        r = resolve_visible_subjects('许氏；杯盖（面部可见）', people=['许氏'], non_people=['许氏', '杯盖'])
        self.assertEqual(r.conflicts, ('许氏', '杯盖'))
        self.assertFalse(r.people)

    def test_no_people_declaration_supports_unlisted_props_and_detects_conflict(self):
        self.assertEqual(resolve_visible_subjects('无人物、蜜饯、杯盖').non_people, ('蜜饯', '杯盖'))
        self.assertEqual(resolve_visible_subjects('无人物、许氏（面部可见）').conflicts, ('许氏',))

    def test_registry_reads_typed_containers_not_arbitrary_prose(self):
        project = {'character_anchors': [{'name':'许氏'}], 'prop_anchors':['蜜饯'],
            'prevention_evidence': {'cognition_matrix':[{'character':'婢女'}]},
            'notes':'杯盖说道：“我也是人物。”'}
        self.assertEqual(project_entity_registry(project), (['婢女', '许氏'], ['蜜饯']))

    def test_validator_mapping_is_blocking_and_has_no_eye_repair(self):
        obj = copy.deepcopy(BY_ID['P03-DIALOGUEBOUND']['input'])
        shot = obj['shots'][0]
        shot['可见主体'] = '甲；蜜饯；杯盖；佛珠'
        obj['character_anchors'] = ['甲']
        report = validate_project(obj, mode='audit')
        mapped = [i for i in report['issues'] if i.get('classification') == 'input_mapping']
        self.assertTrue(mapped)
        self.assertTrue(all(i['blocking'] for i in mapped))
        self.assertFalse(any(i['error_code'] == 'E-EXPRESSION-003' for i in report['issues']))
        self.assertFalse(any('当前=4' in i['evidence'] for i in report['issues']))
        self.assertEqual(report['quality_metrics']['entity_mapping']['status'], 'pending')


class ActualEyeEventTests(unittest.TestCase):
    def shot(self, picture='许氏视线停在门框，头部保持不动。'):
        return {'镜头类型':'中近景', '可见主体':'许氏（面部可见）',
            '画面内容':picture, '起始状态':'许氏双眼自然睁开，视线停在门框。', '生成限制':'不加额外人物。'}

    def check(self, shot):
        return eye_contract_faults(shot, duration=4)

    def test_natural_hold_has_no_forced_eye_action_quota(self):
        shot = self.shot(); original = copy.deepcopy(shot)
        self.assertEqual(self.check(shot), [])
        self.assertEqual(shot, original)

    def test_explicit_same_endpoint_shift_is_still_rejected(self):
        faults = self.check(self.shot('许氏视线从门框移向门框。'))
        self.assertTrue(any('起点与目标相同' in f for f in faults))

    def test_unresolved_shift_is_rejected(self):
        self.assertTrue(any('目标未明确' in f for f in self.check(self.shot('许氏视线移向某处。'))))

    def test_continuous_hold_cannot_change_initial_target_without_event(self):
        self.assertTrue(any('起始视线目标冲突' in f for f in self.check(self.shot('许氏视线持续落在窗框。'))))

    def test_hidden_eyes_cannot_perform_visible_actions(self):
        shot = self.shot('许氏眼球从门框移向窗框，许氏在1.0秒眨眼一次。')
        shot['可见主体'] = '许氏（仅背影）'
        self.assertTrue(any('不可见' in f for f in self.check(shot)))

    def test_source_bound_closed_eyes_cannot_execute_a_gaze_shift(self):
        shot = copy.deepcopy(BY_ID['E14']['input'])
        shot['画面内容'] += '甲视线从门框移向窗框。'
        self.assertTrue(any('源文明确全程闭眼' in f for f in self.check(shot)))

    def test_blink_no_blink_conflict_and_mechanical_count_still_fail(self):
        faults = self.check(self.shot('许氏全程不眨眼。许氏在1.0秒、2.0秒、3.0秒自然眨眼三次。'))
        self.assertTrue(faults)
        self.assertTrue(any('累计3次' in f for f in faults))

    def test_unknown_listener_is_mapping_not_invented_reaction(self):
        shot = self.shot('许氏说道：“请坐。”')
        shot['可见主体'] = '许氏；匿名标签'
        faults = listener_reaction_faults(shot, duration=4, dialogue_rows=[('许氏','请坐。')])
        self.assertTrue(all(f.startswith('input_mapping') for f in faults))


if __name__ == '__main__':
    unittest.main()
