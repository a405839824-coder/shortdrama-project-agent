"""Two real actor endpoints through the source-bound lower/render route.

Synthetic geometry controls do not certify the literary sample or body mechanics.
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/shortdrama-storyboard/scripts'), str(ROOT / 'tests')]
import production_decisions as p
import production_relations as relations
from author_storyboard import _render_decisions
from source_action_plans import SourceActionPlanError, source_step
from test_v0527_production_decisions import production_fixture, action
from test_v0527_source_action_plans import compile_plan, physical_source


def carry_fixture(*, crossing=False):
    data = production_fixture(); w = data['initial_world']; w['props'] = {}
    a = w['actors']['甲']
    a.update(posture='蹲姿', eye_height=.9, reach_origin_xyz=[0, 0, .8],
             hands={'left': [-.2, .3, .5], 'right': [.2, .3, .5]})
    child = copy.deepcopy(a)
    child.update(position_xyz=[0, .4, 0], eye_height=.3, posture='躺卧',
                 reach_origin_xyz=[0, .4, .2], reach_m=.5,
                 hands={'left': [-.12, .12, .15], 'right': [.12, .12, .15]})
    w['actors']['乙'] = child
    for name, xyz in [('怀前', [0, .35, 0]), ('抱持处', [0, .35, .8]),
                      ('起身处', [0, 0, 0]), ('门内', [0, .8, 0])]:
        w['points'][name] = {'scene_id': 'ROOM', 'position_xyz': xyz}
    lm = {'eye_height': .3, 'reach_origin_offset_xyz': [0, 0, .2],
          'hands': {'left': [-.12, .12, .15], 'right': [.12, .12, .15]}}
    steps = [
        {'step_id': 'embrace', 'kind': 'embrace', 'parameters': {'target_actor': '乙', 'point': '怀前', 'posture': '躺卧',
            'landmarks': lm, 'contacts': {'left': [-.15, 0, 0], 'right': [.15, 0, 0]}}, 'after': []},
        {'step_id': 'lift', 'kind': 'lift_actor', 'parameters': {'target_actor': '乙', 'point': '抱持处', 'landmarks': lm,
            'carrier_point': '起身处', 'carrier_landmarks': {'eye_height': 1.5, 'reach_origin_offset_xyz': [0, 0, 1.2],
                'hands': {'left': [-.15, .35, 1], 'right': [.15, .35, 1]}}}, 'after': ['embrace']},
        {'step_id': 'walk', 'kind': 'carry_actor', 'parameters': {'target_actor': '乙', 'to': '门内'}, 'after': ['lift']},
    ]
    if crossing:
        w['scenes']['ROOM2'] = copy.deepcopy(w['scenes']['ROOM']); w['scenes']['ROOM2']['label'] = '门外'
        w['scenes']['ROOM']['door_window_state'] += '门开启。'
        w['points']['门外'] = {'scene_id': 'ROOM2', 'position_xyz': [0, .9, 0]}
        w['scenes']['ROOM']['passages'] = [{'id': '过门', 'exit': '门内', 'entry': '门外', 'opening': '门'}]
        steps.append({'step_id': 'cross', 'kind': 'carry_actor_through_passage',
                      'parameters': {'target_actor': '乙', 'passage': '过门'}, 'after': ['walk']})
    template = copy.deepcopy(data['shots'][0]); data['shots'] = []
    for i, step in enumerate(steps):
        row = copy.deepcopy(template)
        row.update(shot_id=f'EP90-{i + 1:03d}', shot_uid=f'CARRY-{i + 1}',
                   actions=[source_step(action_id='event_' + step['step_id'], source_use_id='U1',
                                        step_id=step['step_id'], start=0, end=40)])
        if i: row['transition'] = {'relation': 'continuous', 'basis': '两人的接触与位置连续。'}
        data['shots'].append(row)
    return data, [{'source_use_id': 'U1', 'steps': steps}]


def lower(data, events):
    source = compile_plan(data, events, source=physical_source('甲将乙搂近，亲自抱起乙，抱着乙走到门边并过门。'))
    return p.lower_production_decisions(data, source_compilation=source)


class ActorCarryTests(unittest.TestCase):
    def test_real_actor_relation_persists_and_both_endpoints_move(self):
        data, events = carry_fixture(); before = copy.deepcopy(data)
        result = lower(data, events); w = result['derivation']['final_world']; rows = result['typed_decisions']['shots']
        self.assertEqual(data, before)
        self.assertEqual(set(w['actors']), {'甲', '乙'}); self.assertEqual(w['props'], {})
        self.assertEqual(w['actors']['甲']['position_xyz'], [0, .8, 0])
        self.assertEqual(w['actors']['乙']['position_xyz'], [0, 1.15, .8])
        rel = w['actors']['甲']['action_state']['body_support']
        self.assertEqual(rel['target_actor'], '乙'); self.assertEqual(rel['mode'], 'carry')
        for hand in ('left', 'right'):
            self.assertEqual(p._hand(w['actors']['甲'], hand), relations.contact_point(w['actors']['乙'], rel['contacts'][hand]))
        for left, right in zip(rows, rows[1:]): self.assertEqual(left['end_state'], right['start_state'])
        canonical = _render_decisions(result['typed_decisions'])
        self.assertIn('双臂仍抱着乙', canonical['shots'][2]['起始状态'])
        self.assertNotIn('走到门内', rows[2]['phases'][0]['text'])
        report = result['derivation']['source_action_execution']
        self.assertEqual([s['effect_occurrences'] for s in report['completed_steps']], [1, 1, 1])
        self.assertFalse(report['source_entailment_proved'])

    def test_crossing_changes_both_scenes_and_retains_relative_pose(self):
        data, events = carry_fixture(crossing=True); result = lower(data, events)
        a, b = (result['derivation']['final_world']['actors'][name] for name in ('甲', '乙'))
        self.assertEqual((a['scene_id'], b['scene_id']), ('ROOM2', 'ROOM2'))
        self.assertEqual(a['position_xyz'], [0, .9, 0])
        self.assertAlmostEqual(b['position_xyz'][1], 1.25)
        self.assertAlmostEqual(b['position_xyz'][2], .8)
        self.assertIn('一同进入ROOM2', result['typed_decisions']['shots'][-1]['phases'][-1]['text'])

    def test_release_airborne_child_and_bare_staging_are_rejected(self):
        data, events = carry_fixture()
        events[0]['steps'][-1].update(kind='release_embrace', parameters={'target_actor': '乙'})
        with self.assertRaisesRegex(p.ProductionDecisionError, 'airborne'): lower(data, events)
        data, events = carry_fixture(); step = events[0]['steps'][0]
        data['shots'] = data['shots'][:1]
        data['shots'][0]['actions'] = [action('embrace', **step['parameters'])]
        with self.assertRaisesRegex(p.ProductionDecisionError, 'frozen physical source'): p.lower_production_decisions(data)

    def test_target_cannot_be_a_prop_or_lift_without_embrace(self):
        data, events = carry_fixture(); events[0]['steps'][0]['parameters']['target_actor'] = '信纸'
        with self.assertRaisesRegex(p.ProductionDecisionError, 'actor'): lower(data, events)
        data, events = carry_fixture(); data['shots'] = data['shots'][1:]
        events[0]['steps'] = events[0]['steps'][1:]; events[0]['steps'][0]['after'] = []
        data['shots'][0]['transition'] = {'relation': 'initial', 'basis': '缺失真实搂抱。'}
        with self.assertRaisesRegex(p.ProductionDecisionError, 'preceding'): lower(data, events)

    def test_independent_move_and_overlapping_target_body_write_rejected(self):
        data, events = carry_fixture()
        data['shots'][2]['actions'].append(action('turn', actor='乙', id='escape', body_yaw=30, head_yaw=30))
        with self.assertRaisesRegex(p.ProductionDecisionError, 'overlapping writes'): lower(data, events)
        data, events = carry_fixture(); row = copy.deepcopy(data['shots'][-1]); row['shot_id'] = 'EP90-004'; row['shot_uid'] = 'ESCAPE'
        row['actions'] = [action('move_actor', actor='乙', id='escape', to='怀前')]; data['shots'].append(row)
        with self.assertRaisesRegex(p.ProductionDecisionError, 'two-person action'): lower(data, events)

    def test_two_hands_occupied_contacts_obstructions_and_speed_rejected(self):
        for problem in ('held_prop', 'same_contact', 'wall', 'speed'):
            with self.subTest(problem=problem):
                data, events = carry_fixture(); w = data['initial_world']
                if problem == 'held_prop': w['props']['道具'] = {'anchor': {'actor': '甲', 'hand': 'left'}, 'written_ref': None}
                if problem == 'same_contact': events[0]['steps'][0]['parameters']['contacts']['right'] = [-.15, 0, 0]
                if problem == 'wall': w['scenes']['ROOM']['barriers'] = [{'id': '墙', 'a': [-1, .7, 0], 'b': [1, .7, 0], 'height_m': 2}]
                if problem == 'speed': w['points']['门内']['position_xyz'] = [0, 8, 0]
                with self.assertRaises(p.ProductionDecisionError): lower(data, events)

    def test_closed_door_remote_portal_and_old_teleport_kind_rejected(self):
        for problem in ('closed', 'remote', 'legacy'):
            with self.subTest(problem=problem):
                data, events = carry_fixture(crossing=True); w = data['initial_world']
                if problem == 'closed': w['scenes']['ROOM']['door_window_state'] = '东纸窗关闭。门关闭。'
                if problem == 'remote': w['points']['门外']['position_xyz'] = [0, 20, 0]
                if problem == 'legacy': events[0]['steps'][-1].update(kind='carry_actor_to_scene', parameters={'target_actor': '乙', 'to': '门外', 'scene_id': 'ROOM2'})
                with self.assertRaises((p.ProductionDecisionError, SourceActionPlanError)): lower(data, events)

    def test_forged_initial_relation_and_corrupted_child_effect_rejected(self):
        data, events = carry_fixture(); a = data['initial_world']['actors']['甲']
        a['action_state'] = {'contacts': {}, 'wet_parts': [], 'clean_parts': [], 'hand_shapes': {'left': 'open', 'right': 'open'},
                             'expression': '平静', 'claps': 0, 'kowtows': 0,
                             'body_support': {'target_actor': '乙', 'mode': 'carry', 'contacts': {'left': [-.15, 0, 0], 'right': [.15, 0, 0]}}}
        with self.assertRaisesRegex(p.ProductionDecisionError, 'executed source action'): lower(data, events)
        data, events = carry_fixture(); real = p._apply
        def corrupt(world, act, info, tick, path):
            real(world, act, info, tick, path)
            if act['kind'] == 'carry_actor' and tick == act['end']: world['actors']['乙']['position_xyz'][1] += .1
        with patch.object(p, '_apply', corrupt), self.assertRaisesRegex(p.ProductionDecisionError, 'effect'): lower(data, events)

    def test_unsupported_rescue_method_and_spit_water_stay_unsupported(self):
        for kind in ('continue_rescue', 'spit_water'):
            data, events = carry_fixture(); events[0]['steps'][0].update(kind=kind, parameters={})
            with self.assertRaisesRegex(SourceActionPlanError, 'not supported'): compile_plan(data, events)


if __name__ == '__main__': unittest.main()
