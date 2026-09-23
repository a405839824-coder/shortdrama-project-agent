"""Synthetic regression controls, not real-source approval or release evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'skills/shortdrama-storyboard/scripts'), str(ROOT/'tests')]
from authoring_contract_blocks import scene_lighting_blocks, interior_lighting_blocks, ContractBlockError
from author_storyboard import _render_decisions, record_attempt
from production_decisions import lower_production_decisions, ProductionDecisionError
from production_derivation import inspect_derivation
from lighting_checks import lighting_context_faults, classify_scene
from validate_storyboard import _lighting_context_faults
from test_v0527_production_decisions import production_fixture, action
from test_v0537_source_actions import next_shot
from test_v0527_source_decision_independent_audit import complete_source_input
from production_plan_test_support import frozen_plan


def outdoor(data):
    scene = data['initial_world']['scenes']['ROOM']
    scene.update(label='室外庭院', door_window_state={}, opening_labels={})
    scene['light'] = dict(period='day', kind='outdoor_daylight', name='庭院天光', state='present',
                          direction_xyz=[1, 0, 1], main_tone='neutral', shadow_tone='neutral', materials=['衣料', '纸面'])
    return data


class OutdoorDaylightTests(unittest.TestCase):
    def render(self, data):
        lowered = lower_production_decisions(data)
        return lowered, _render_decisions(lowered['typed_decisions'])['shots']

    def test_outdoor_compiles_without_inventing_window_lamp_or_source_height(self):
        data = outdoor(production_fixture()); original = copy.deepcopy(data)
        lowered, shots = self.render(data); shot = shots[0]
        self.assertEqual(data, original)
        self.assertEqual(classify_scene(shot)['space_type'], 'outdoor')
        self.assertEqual(lighting_context_faults(shot, legacy_checker=_lighting_context_faults), [])
        light = ''.join(str(shot[k]) for k in ('光影', '光源来源', '光源方向', '色温倾向', '生成限制'))
        for invented in ('纸窗', '灯笼', '油灯', '蜡烛', '室内', '离地', '米'):
            self.assertNotIn(invented, light)
        self.assertIn('天光', shot['光源来源'])
        self.assertIn('45度', shot['光源来源'])
        self.assertNotIn('衰减', shot['光影'])
        self.assertFalse(lowered['derivation']['commit_eligible'])
        self.assertIsNone(lowered['derivation']['semantic_accuracy_percent'])

    def test_camera_reversal_changes_screen_side_but_translation_does_not_move_daylight(self):
        data = outdoor(production_fixture())
        _, rows = self.render(data)
        self.assertIn('场景东侧主光位于画面左侧', rows[0]['光源方向'])
        translated = copy.deepcopy(data)
        world = translated['initial_world']
        for entity in [*world['actors'].values(), *world['points'].values()]:
            entity['position_xyz'][0] += 100
        for actor in world['actors'].values(): actor['reach_origin_xyz'][0] += 100
        translated['shots'][0]['camera']['position_xyz'][0] += 100
        _, moved = self.render(translated)
        self.assertEqual(rows[0]['光源方向'], moved[0]['光源方向'])
        data['shots'][0]['camera']['position_xyz'] = [0, -4, 1.5]
        _, reverse = self.render(data)
        self.assertIn('场景东侧主光位于画面右侧', reverse[0]['光源方向'])

    def test_bad_daylight_shape_period_state_geometry_and_indoor_location_reject(self):
        updates = [dict(period='night'), dict(state='unlit'), dict(state='closed'),
                   dict(direction_xyz=[0,0,1]), dict(direction_xyz=[1,0,0]),
                   dict(direction_xyz=[1,0,-1]), dict(direction_xyz=[True,0,1]),
                   dict(direction_xyz=[float('nan'),0,1]), dict(position_xyz=[3,0,2]),
                   dict(direction_xyz='东侧'), dict(kind='magic_daylight')]
        for update in updates:
            with self.subTest(update=update):
                data = outdoor(production_fixture()); data['initial_world']['scenes']['ROOM']['light'].update(update)
                with self.assertRaises(ProductionDecisionError): self.render(data)
        data = outdoor(production_fixture()); data['initial_world']['scenes']['ROOM']['label'] = '书房室内'
        with self.assertRaisesRegex(ProductionDecisionError, 'indoor scene'): self.render(data)

    def test_registered_opening_needs_its_own_geometry_not_the_distant_daylight(self):
        data = outdoor(production_fixture()); scene = data['initial_world']['scenes']['ROOM']
        scene.update(door_window_state={'东门':'open'}, opening_labels={'东门':'院门'})
        scene['light']['name'] = '东门'
        with self.assertRaisesRegex(ProductionDecisionError, 'explicit point/prop'): self.render(data)
        data['initial_world']['points']['东门'] = {'scene_id':'ROOM', 'position_xyz':[2,0,1], 'display_name':'院门'}
        _, rows = self.render(data)
        self.assertIn('院门', rows[0]['空间站位描述'])

    def test_frozen_light_bridge_uses_shared_validation_and_keeps_outdoor_scene(self):
        data = outdoor(production_fixture()); row = next_shot(data, relation='later_same_place')
        light = copy.deepcopy(data['initial_world']['scenes']['ROOM']['light']); light['direction_xyz'] = [-1,0,1]
        row['transition']['bridge'] = {'ticks':10, 'actions':[action('scene_light',id='laterlight',start=0,end=10,scene_id='ROOM',light=light)]}
        lowered, rows = self.render(data)
        self.assertIn('场景西侧', rows[1]['光源方向'])
        self.assertEqual(lowered['derivation']['final_world']['scenes']['ROOM']['light'], light)
        row['transition']['bridge']['actions'][0]['light']['state'] = 'lit'
        with self.assertRaises(ProductionDecisionError): self.render(data)
        row['transition']['bridge']['actions'][0]['light'] = production_fixture()['initial_world']['scenes']['ROOM']['light']
        with self.assertRaisesRegex(ProductionDecisionError, 'cannot relocate'): self.render(data)

    def test_outdoor_still_requires_frozen_plan_and_replay_rejects_field_tampering(self):
        data, raw, texts = complete_source_input(); outdoor(data)
        graph = json.loads(raw); graph['production_plan'] = frozen_plan(data, graph, texts)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); (path/'input.json').write_text(json.dumps(data), encoding='utf-8')
            (path/'graph.json').write_text(json.dumps(graph), encoding='utf-8')
            result = record_attempt(path/'input.json', path/'attempts', source_graph_path=path/'graph.json', require_production_plan=True)
            self.assertEqual(result['status'], 'rendered_pending_preflight', result.get('findings'))
            canonical = json.loads(Path(result['canonical_path']).read_text(encoding='utf-8'))
            self.assertTrue(inspect_derivation(canonical)['production_prevention_verified'])
            self.assertIsNone(result['semantic_accuracy_percent'])
            canonical['shots'][0]['光源来源'] = '室内纸窗透入天光。'
            self.assertFalse(inspect_derivation(canonical)['valid'])

    def test_standalone_outdoor_fragments_are_explicit_and_old_indoor_api_stays_indoor(self):
        kwargs = dict(source=dict(period='day',kind='outdoor_daylight',name='院中天光',state='present',world_side='东',elevation_deg=45),
                      screen_side='左',receiving_surfaces=['甲面部'],weak_reflector=None,main_tone='neutral',shadow_tone='neutral',materials=['衣料'],air='clear',exposure='normal')
        self.assertIn('室外日间', scene_lighting_blocks(**kwargs)['spatial_state_append'])
        with self.assertRaises(ContractBlockError): interior_lighting_blocks(**kwargs)
        for update in ({'height_m':2}, {'elevation_deg':90}, {'elevation_deg':True}, {'period':'night'}, {'state':'lit'}):
            bad = copy.deepcopy(kwargs); bad['source'].update(update)
            with self.subTest(update=update), self.assertRaises(ContractBlockError): scene_lighting_blocks(**bad)


if __name__ == '__main__': unittest.main()
