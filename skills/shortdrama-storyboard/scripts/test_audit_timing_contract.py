"""Small certificate-bound checks for the generic 0.5.56 contracts."""
import copy
import hashlib
import json
import unittest

from rule_registry import load_registry, check_registry_definition
from timing_model import resolve_profile, estimate_utterance
from projection_semantics import music_directives, explicit_state_conflicts, tail_binding_faults
from authoring_contract_blocks import scene_lighting_blocks, ContractBlockError
from production_decisions import _audio_segments, ProductionDecisionError
from timing_model import segment_pauses, predicted_character_interval


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

    def test_author_pause_partition_and_character_clock_use_one_extra_event(self):
        profile = self.select()
        text = '甲乙丙丁。'
        pause = {'id': 'extra', 'offset': 2, 'seconds': .3, 'extra': True}
        segments = _audio_segments(text, [(0, 0, 10), (1, 10, 20), (2, 20, 40)],
                                   'S1', profile, 'test', [pause, pause])
        total = sum(estimate_utterance(text[a:b], profile,
                    segment_pauses(text, [pause, pause], a, b))['seconds']
                    for a, b in (s['char_range'] for s in segments))
        self.assertAlmostEqual(total, 4 / 6.3 + .3)
        span = predicted_character_interval(text, profile, [pause],
                    {'start': 1, 'end': 3, 'char_range': [0, 5]}, [2, 4])
        self.assertAlmostEqual(span[0], 2.15)
        with self.assertRaises(ProductionDecisionError):
            _audio_segments(text, [(0, 0, 5)], 'S1', profile, 'test', [pause])

    def test_actual_author_pause_compiler_canonical_and_chapter(self):
        # Reuse a packaged public structural fixture, not a story accuracy sample.
        from test_failed_push import fixture
        from source_decisions import compile_source_decisions
        from production_decisions import lower_production_decisions
        from author_storyboard import _render_decisions
        from timing_model import validate_timing, chapter_budget
        data, _ = fixture()
        del data['initial_world']['actors']['乙']
        text = '明日再议。'; source_text = '成年甲说道：“' + text + '”'
        fact = {'id': 'f', 'subject': '甲', 'event_id': 'e', 'polarity': 'affirmative',
            'conditions': [], 'modality': 'asserted', 'occurrence': 'future',
            'expression_occurrence': 'occurred', 'expression_status': 'asserted',
            'channel': 'oral', 'content_availability': 'given', 'content_policy': 'verbatim',
            'content_text': text, 'depends_on': [], 'required': True,
            'supports': [{'source_id': 'DOC', 'start': 0, 'end': len(source_text), 'text': source_text}]}
        graph = json.dumps({'contract': 'source-facts-v1', 'sources': {'DOC': hashlib.sha256(source_text.encode()).hexdigest()},
                            'facts': [fact]}, ensure_ascii=False).encode()
        decisions = {'contract': 'source-decisions-v1', 'uses': [{'id': 'u', 'fact_ids': ['f'],
            'mode': 'spoken', 'wording': 'verbatim', 'actor': '甲', 'text': text}], 'written_objects': []}
        source = compile_source_decisions(graph, decisions, source_texts={'DOC': source_text}, expected_graph_sha256=hashlib.sha256(graph).hexdigest())
        evidence = data['project']['prevention_evidence']
        evidence.update(source_decisions=decisions, source_ledger=[{'source_id': 'DOC', 'source_text': source_text}])
        row = data['shots'][0]; row['source_unit_ids'] = ['DOC']
        voice = {'baseline': '年龄感为成年，中音区，厚薄适中，明暗中性，常态清楚。',
            'speech_act': '陈述商议安排。', 'current_expression': '平静，意在告知。', 'volume': '正常交谈。',
            'rhythm': '中速，逐字清楚。', 'breath': '自然吸气。', 'pauses': '按语义短停。',
            'emphasis': '“明日”。', 'tail_direction': '落稳', 'tail_execution': '句末自然收住。',
            'prohibited_tones': ['喊叫', '哭腔']}
        speech = {'id': 'speech', 'kind': 'speak', 'source_use_id': 'u', 'start': 10, 'end': 30, 'after': [], 'voice': voice,
                  'timing': {'profile_id': self.fast, 'selection_evidence': self.selection}}
        row['actions'] = [speech]
        def render():
            return _render_decisions(lower_production_decisions(data, source_compilation=source)['typed_decisions'])
        ordinary = render()
        speech['pauses'] = []
        self.assertEqual(render(), ordinary)
        pause = {'id': 'wait', 'offset': 2, 'seconds': .4, 'extra': True}
        speech['pauses'] = [pause, pause]
        canonical = render()
        item = canonical['prevention_evidence']['audio_workflow']['timing']['utterances'][0]
        self.assertEqual(len(item['pauses']), 1)
        self.assertEqual(validate_timing(canonical, canonical['shots'])['faults'], [])
        budget = chapter_budget([{'speaker': '甲', 'text': text}], project=canonical)
        self.assertEqual(budget['faults'], [])
        self.assertAlmostEqual(budget['required_dialogue_seconds'], 4 / 6.3 + .4)
        speech['pauses'] = [pause, dict(pause, seconds=.5)]
        with self.assertRaises(ProductionDecisionError):
            render()
        speech['pauses'] = [pause]
        speech['voice']['pauses'] = '“明日”后停顿1秒。'
        with self.assertRaises(ProductionDecisionError):
            render()


if __name__ == '__main__':
    unittest.main()
