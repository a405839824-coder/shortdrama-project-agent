"""Small certificate-bound checks for the generic 0.5.56 contracts."""
import copy
import unittest

from rule_registry import load_registry, check_registry_definition
from timing_model import resolve_profile, estimate_utterance
from projection_semantics import music_directives, explicit_state_conflicts, tail_binding_faults
from authoring_contract_blocks import scene_lighting_blocks, ContractBlockError


class AuditTimingContractTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_registry()['runtime']
        self.fast = 'provisional-zh-continuous-explanation-v1'
        self.text = '设备检查完成后我们再依次进入实验室。'
        self.uses = {'u': {'actor': '研究员', 'text': self.text, 'mode': 'spoken', 'fact_ids': ['f']}}
        self.selection = {'speaker_category': 'adult_supported', 'delivery_class': 'continuous_explanation',
                          'source_use_id': 'u', 'basis_fact_ids': ['f'], 'rationale': '有源成年身份与场内解释'}

    def select(self, **extra):
        values = dict(selection=self.selection, kind='dialogue', speaker='研究员', text=self.text, uses=self.uses)
        values.update(extra)
        return resolve_profile(self.rules, self.fast, **values)

    def test_source_bound_profile_is_provisional_and_central(self):
        profile = self.select()
        self.assertEqual(profile['units_per_second'], 6.3)
        self.assertEqual(profile['evidence_status'], 'provisional')
        self.assertEqual(profile['voice'], 'unmatched')

    def test_default_and_unknown_speaker_cannot_take_fast_profile(self):
        with self.assertRaises(ValueError):
            resolve_profile(self.rules, self.fast, default=True)
        self.selection['speaker_category'] = 'unknown'
        with self.assertRaises(ValueError):
            self.select()

    def test_forged_source_and_narration_cannot_take_fast_profile(self):
        with self.assertRaises(ValueError):
            self.select(text='另一句无关对白')
        with self.assertRaises(ValueError):
            self.select(kind='psychological')

    def test_normal_pause_once_extra_pause_preserved(self):
        result = estimate_utterance(self.text, self.select(),
            [{'id': 'normal', 'offset': 3, 'seconds': 0.4},
             {'id': 'wait', 'offset': 5, 'seconds': 0.7, 'extra': True}])
        self.assertAlmostEqual(result['extra_pause_seconds'], 0.7)
        self.assertGreater(result['reference_seconds'][1], result['seconds'])

    def test_asr_cannot_turn_into_measured(self):
        registry = load_registry()
        profile = next(p for p in registry['runtime']['timing_policy']['profiles'] if p['id'] == self.fast)
        profile['evidence_status'] = 'measured'
        self.assertTrue(check_registry_definition(registry))

    def test_unknown_music_is_not_an_affirmative_action(self):
        self.assertEqual(music_directives('是否加入背景音乐待定。')[0]['status'], 'unknown')
        self.assertEqual(music_directives('禁止雨幕、霓虹标牌和背景音乐。')[0]['status'], 'negative')
        self.assertEqual(music_directives('禁止雨幕；加入背景音乐。')[0]['status'], 'affirmative')

    def test_closed_eye_and_seated_initial_state_are_not_overridden(self):
        self.assertTrue(explicit_state_conflicts('医师闭眼，坐于椅上。', '医师睁眼，站在门边。', ['医师']))
        self.assertEqual(explicit_state_conflicts('医师闭眼，坐于椅上。', '医师闭眼，坐在椅上。', ['医师']), [])

    def test_tail_anchor_is_this_utterance_not_generic_or_previous(self):
        dialogue = [('研究员', '检查完成。')]
        self.assertTrue(tail_binding_faults('随句自然收住。', dialogue))
        self.assertTrue(tail_binding_faults('“回来”收稳。', dialogue))
        self.assertEqual(tail_binding_faults('“完成”收稳。', dialogue), [])

    def test_closed_glass_daylight_preserves_real_material_and_state(self):
        args = dict(source=dict(period='day', kind='glass_window', name='东玻璃窗',
                    state='closed', world_side='东', height_m=2), screen_side='右',
                    receiving_surfaces=['检修员的面颊'], weak_reflector=None,
                    main_tone='neutral', shadow_tone='neutral', materials=['衣料'],
                    air='clear', exposure='normal')
        result = scene_lighting_blocks(**args)
        self.assertIn('玻璃', result['lighting_fields']['光源来源'])
        self.assertIn('关闭', result['spatial_state_append'])
        self.assertNotIn('窗纸', str(result))
        args['source']['period'] = 'night'
        with self.assertRaises(ContractBlockError):
            scene_lighting_blocks(**args)


if __name__ == '__main__':
    unittest.main()
