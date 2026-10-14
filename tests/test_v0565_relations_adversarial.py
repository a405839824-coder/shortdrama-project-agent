"""Independent adversarial controls for new two-person/opaque relations.

These are synthetic software controls, never a replacement for the frozen C
draft or medical/source-semantic review. All tests exercise the actual lowerer.
"""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/shortdrama-storyboard/scripts'), str(ROOT / 'tests')]

import production_decisions as p
from source_action_plans import SourceActionPlanError, source_step
from source_decisions import compile_source_decisions
from test_v0527_production_decisions import action
from test_v0527_source_action_plans import compile_plan, physical_source
from test_v0527_source_decision_independent_audit import source_input
from test_v0565_actor_carry import carry_fixture, lower
from test_v0565_container import container_fixture


def append_shot(data, actions, *, transition=None):
    row = copy.deepcopy(data['shots'][-1])
    n = len(data['shots']) + 1
    row.update(shot_id=f'EP90-{n:03d}', shot_uid=f'ADVERSARIAL-{n}', actions=actions)
    row['transition'] = transition or {'relation': 'continuous', 'basis': '实际状态连续，不重置身体或道具。'}
    data['shots'].append(row)


def hidden_paper_fixture():
    data = container_fixture()
    data['initial_world']['props']['纸片'] = {
        'anchor': {'prop': '戒尺', 'offset_xyz': [0, 0, .01]}, 'written_ref': None}
    data['initial_world']['actors']['甲']['hands']['right'] = [.25, 0, .91]
    data['shots'][0]['actions'] = [action('pick_up', prop='纸片', hand='right', start=0, end=40)]
    return data


class RelationsAdversarialTests(unittest.TestCase):
    def test_static_hold_preserves_both_real_actors_and_support_without_new_actions(self):
        data, events = carry_fixture()
        append_shot(data, [action('hold', id='quiet_hold', start=0, end=40)])
        result = lower(data, events)
        left, right = result['typed_decisions']['shots'][-2:]
        self.assertEqual(left['end_state'], right['start_state'])
        # A hold legitimately appends its completed event ID. Its actual body,
        # support, prop and eye state must remain unchanged.
        for field in ('人物位置', '身体朝向', '视线落点', '手持道具'):
            self.assertEqual(right['start_state'][field], right['end_state'][field])
        self.assertEqual(right['start_state']['动作进度']['动作状态'],
                         right['end_state']['动作进度']['动作状态'])
        self.assertEqual(set(result['derivation']['final_world']['actors']), {'甲', '乙'})
        self.assertEqual(result['derivation']['final_world']['props'], {})

    def test_sequential_turn_move_and_hand_motion_cannot_detach_either_actor(self):
        for actor in ('甲', '乙'):
            for kind, params in (
                ('turn', {'body_yaw': 30, 'head_yaw': 30}),
                ('move_actor', {'to': '怀前'}),
                ('hand_move', {'hand': 'right', 'to': '案右'})):
                with self.subTest(actor=actor, kind=kind):
                    data, events = carry_fixture()
                    append_shot(data, [action(kind, id='detach', actor=actor, **params)])
                    with self.assertRaisesRegex(p.ProductionDecisionError, 'two-person action'):
                        lower(data, events)

    def test_omitted_bridge_cannot_hide_carrier_or_target_detachment(self):
        for actor in ('甲', '乙'):
            with self.subTest(actor=actor):
                data, events = carry_fixture()
                append_shot(data, [action('hold', id='after_bridge')], transition={
                    'relation': 'later_same_place', 'basis': '时间略过但仍须保持真实抱持。',
                    'bridge': {'ticks': 10, 'actions': [action('move_actor', id='hidden_detach',
                        actor=actor, start=0, end=10, to='怀前')]}})
                data['shots'][-1]['continuity_id'] = 'ROOM-C2'
                with self.assertRaisesRegex(p.ProductionDecisionError, 'two-person action'):
                    lower(data, events)

    def test_real_grounded_release_then_independent_move_is_legal(self):
        data, events = carry_fixture()
        data['shots'] = data['shots'][:1]
        events[0]['steps'] = events[0]['steps'][:1] + [{
            'step_id': 'release', 'kind': 'release_embrace',
            'parameters': {'target_actor': '乙'}, 'after': ['embrace']}]
        append_shot(data, [source_step(action_id='release', source_use_id='U1', step_id='release', start=0, end=40)])
        append_shot(data, [action('move_actor', id='leave_after_release', to='门内', start=0, end=40)])
        source = compile_plan(data, events, source=physical_source('甲搂住乙，随后松开双臂。'))
        result = p.lower_production_decisions(data, source_compilation=source)
        self.assertIsNone(result['derivation']['final_world']['actors']['甲']['action_state']['body_support'])
        self.assertEqual(result['derivation']['final_world']['actors']['乙']['position_xyz'], [0, .35, 0])

    def test_fresh_source_id_cannot_repeat_an_already_scheduled_support_step(self):
        data, events = carry_fixture()
        append_shot(data, [source_step(action_id='second_lift_identity', source_use_id='U1',
                                      step_id='lift', start=0, end=40)])
        with self.assertRaisesRegex(p.ProductionDecisionError, 'already scheduled'):
            lower(data, events)

    def test_destination_scene_wall_blocks_the_entire_crossing_sweep(self):
        data, events = carry_fixture(crossing=True)
        data['initial_world']['scenes']['ROOM2']['barriers'] = [{
            'id': '门外实体墙', 'a': [-1, .85, 0], 'b': [1, .85, 0], 'height_m': 2}]
        # Entry is y=.9 and exit y=.8. Checking ROOM2 only at the last
        # simulation tick misses the wall crossed around the middle tick.
        with self.assertRaisesRegex(p.ProductionDecisionError, 'barrier'):
            lower(data, events)
        data['initial_world']['scenes']['ROOM2']['barriers'] = []
        result = lower(data, events)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['scene_id'], 'ROOM2')
        self.assertEqual(result['derivation']['final_world']['actors']['乙']['scene_id'], 'ROOM2')

    def test_hidden_descendant_cannot_be_extracted_by_null_pickup(self):
        data = hidden_paper_fixture()
        with self.assertRaisesRegex(p.ProductionDecisionError, 'opaque|extract|container|hidden'):
            p.lower_production_decisions(data)
        # Same surface-supported paper outside the opaque sleeve remains a
        # legitimate pickup. Do not solve concealment by banning all pickup.
        data['initial_world']['props']['戒尺']['anchor'] = {'point': '案左'}
        data['initial_world']['actors']['甲']['hands']['right'] = [.2, .3, 1.01]
        result = p.lower_production_decisions(data)
        self.assertEqual(result['derivation']['final_world']['props']['纸片']['anchor'], {'actor': '甲', 'hand': 'right'})

    def test_waist_attachment_is_not_an_actual_hand_for_transfer(self):
        data = container_fixture()
        other = copy.deepcopy(data['initial_world']['actors']['甲'])
        other.update(position_xyz=[.4, 0, 0], reach_origin_xyz=[.4, 0, 1.2])
        data['initial_world']['actors']['乙'] = other
        data['shots'][0]['actions'] = [action('transfer', prop='腰侧布套', to_actor='乙', to_hand='left')]
        # A domain rejection, not a KeyError escaping the authoring route.
        with self.assertRaisesRegex(p.ProductionDecisionError, 'hand|held|holder|手持'):
            p.lower_production_decisions(data)

    def test_initial_hidden_visual_target_is_rejected_without_a_look_action(self):
        data = container_fixture()
        data['initial_world']['actors']['甲'].update(head_yaw=90, gaze={'kind': 'prop', 'id': '戒尺'})
        with self.assertRaisesRegex(p.ProductionDecisionError, 'opaque'):
            p.lower_production_decisions(data)

    def test_contained_source_paper_cannot_receive_visible_delivery_credit(self):
        data, graph, texts = source_input()
        sleeve = container_fixture()['initial_world']['props']['腰侧布套']
        data['initial_world']['props']['腰侧布套'] = sleeve
        data['initial_world']['props']['信纸']['anchor'] = {'container': '腰侧布套'}
        source = compile_source_decisions(graph, data['project']['prevention_evidence']['source_decisions'],
            source_texts=texts, expected_graph_sha256=hashlib.sha256(graph).hexdigest())
        with self.assertRaises(p.ProductionDecisionError) as error:
            p.lower_production_decisions(data, source_compilation=source)
        self.assertIn('source_consumption', str(error.exception))

    def test_support_cannot_be_renamed_as_unspecified_medical_treatment(self):
        for kind in ('resuscitate', 'continue_rescue', 'spit_water', 'set_down_actor', 'extract_from_container'):
            with self.subTest(kind=kind):
                data, events = carry_fixture()
                events[0]['steps'][0].update(kind=kind, parameters={})
                with self.assertRaisesRegex(SourceActionPlanError, 'not supported'):
                    compile_plan(data, events)


if __name__ == '__main__':
    unittest.main()
