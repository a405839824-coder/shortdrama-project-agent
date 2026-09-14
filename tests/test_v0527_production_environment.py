"""Real lowerer controls for environment prose/state and sound/event drift."""
import copy
import unittest

from test_v0527_production_decisions import production_fixture, action
from production_decisions import lower_production_decisions, inspect_production_decisions, ProductionDecisionError
from production_environment import environment_editorial, METADATA_FIELDS, EnvironmentDecisionError
from author_storyboard import _render_decisions


class ProductionEnvironmentTests(unittest.TestCase):
    def test_normal_static_scene_constructs_fields_from_shared_light(self):
        raw = production_fixture()
        metadata = {k: raw['shots'][0]['editorial'][k] for k in METADATA_FIELDS}
        raw['shots'][0]['editorial'] = environment_editorial(metadata=metadata, sound_cues=[])
        before = copy.deepcopy(raw)
        lower = lower_production_decisions(raw)
        shot = _render_decisions(lower['typed_decisions'])['shots'][0]
        self.assertEqual(shot['环境动态'], '无环境动态。')
        self.assertIn('东纸窗', shot['光源来源'])
        self.assertIn('关闭', shot['空间站位描述'])
        self.assertEqual(shot['环境音效'], '无额外环境音效。')
        self.assertEqual(raw, before)
        self.assertEqual(lower['derivation']['shots'][0]['environment_binding']['fields']['环境动态'], shot['环境动态'])
        raw['initial_world']['scenes']['ROOM']['light']['state'] = 'open'
        raw['initial_world']['scenes']['ROOM']['door_window_state'] = {'东纸窗': 'open'}
        changed = lower_production_decisions(raw)
        self.assertEqual(changed['typed_decisions']['shots'][0]['fields']['环境动态'], '无环境动态。')

    def test_free_prose_cannot_add_paper_motion_fog_or_explosion(self):
        for field, text in [('环境动态', '纸张自行翻动，室内浓雾弥漫。'),
                            ('环境音效', '窗外传来爆炸声。')]:
            with self.subTest(field=field):
                raw = production_fixture(); raw['shots'][0]['editorial'][field] = text
                with self.assertRaises(ProductionDecisionError):
                    lower_production_decisions(raw)
                inspected = inspect_production_decisions(raw)
                self.assertNotIn('lowered', inspected)
                self.assertTrue(any(field in x['path'] for x in inspected['findings']))

    def test_constructor_does_not_silently_discard_legacy_prose(self):
        raw = production_fixture()
        with self.assertRaises(EnvironmentDecisionError):
            environment_editorial(metadata=raw['shots'][0]['editorial'], sound_cues=[])

    def moving(self):
        raw = production_fixture()
        raw['shots'][0]['actions'] = [action('move_prop', id='slide', prop='信纸', hand='right', to='案右')]
        raw['shots'][0]['editorial']['环境音效'] = {
            'mode': 'action_bound', 'cues': [{'action_id': 'slide', 'sound': 'prop_contact', 'volume': 'soft'}]}
        return raw

    def test_sound_uses_executed_action_identity_object_and_time(self):
        raw = self.moving(); result = lower_production_decisions(raw)
        shot = _render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('1.0—3.0秒：甲移动信纸', shot['环境音效'])
        self.assertEqual(result['derivation']['final_world']['props']['信纸']['anchor'], {'point': '案右'})
        self.assertEqual(result['derivation']['shots'][0]['environment_binding']['sound_bindings'][0]['action_id'], 'slide')
        raw['shots'][0]['actions'][0].update(start=12, end=28)
        revised = lower_production_decisions(raw)
        self.assertIn('1.2—2.8秒', revised['typed_decisions']['shots'][0]['fields']['环境音效'])
        self.assertNotIn('1.0—3.0秒', revised['typed_decisions']['shots'][0]['fields']['环境音效'])

    def test_sound_cannot_claim_missing_or_different_kind_of_action(self):
        for update in [{'action_id': 'missing'}, {'sound': 'footsteps'}, {'sound': 'explosion'}]:
            with self.subTest(update=update):
                raw = self.moving(); raw['shots'][0]['editorial']['环境音效']['cues'][0].update(update)
                with self.assertRaises(ProductionDecisionError):
                    lower_production_decisions(raw)

    def test_failed_physical_action_cannot_produce_sound_or_candidate(self):
        raw = self.moving()
        raw['initial_world']['points']['案左']['position_xyz'] = [4, 4, 1]
        with self.assertRaises(ProductionDecisionError):
            lower_production_decisions(raw)

    def test_repeated_cue_does_not_duplicate_one_event(self):
        raw = self.moving(); cues = raw['shots'][0]['editorial']['环境音效']['cues']
        cues.append(copy.deepcopy(cues[0]))
        with self.assertRaises(ProductionDecisionError):
            lower_production_decisions(raw)

    def test_opening_snapshot_and_lighting_cannot_disagree(self):
        for wrong in ['东纸窗敞开。', {'东纸窗': 'open'}, '东纸窗关闭。信纸自行翻动。']:
            with self.subTest(wrong=wrong):
                raw = production_fixture()
                raw['initial_world']['scenes']['ROOM']['door_window_state'] = wrong
                with self.assertRaises(ProductionDecisionError):
                    lower_production_decisions(raw)

    def test_structured_openings_preserve_other_doors_in_one_snapshot(self):
        raw = production_fixture()
        raw['initial_world']['scenes']['ROOM']['door_window_state'] = {'东纸窗': 'closed', '房门': 'open'}
        result = lower_production_decisions(raw)
        snapshot = result['typed_decisions']['shots'][0]['start_state']
        self.assertIn('房门开启', str(snapshot))
        self.assertIn('东纸窗关闭', str(snapshot))


if __name__ == '__main__':
    unittest.main()
