"""Actual authored pauses and existing action clocks, not audio calibration."""
import copy
import unittest

from test_v0556_timing_profiles import compiler_input, FAST, RULES
from test_v0527_production_decisions import action
from author_storyboard import _render_decisions
from production_decisions import lower_production_decisions, ProductionDecisionError
from timing_model import estimate_utterance, validate_timing, chapter_budget, segment_pauses, source_uses


def setup(pauses, *, text='明日再议。', fast=True):
    data, source = compiler_input(text, fast=fast)
    speech = data['shots'][0]['actions'][-1]
    speech['pauses'] = pauses
    speech['voice']['pauses'] = '按语义自然短停。'
    return data, source, speech


def compile(data, source):
    lowered = lower_production_decisions(data, source_compilation=source)
    return lowered, _render_decisions(lowered['typed_decisions'])


def timing(canonical):
    return canonical['prevention_evidence']['audio_workflow']['timing']


class AuthorPauseTests(unittest.TestCase):
    def test_actual_compiler_normal_pause_not_added_extra_once_for_both_profiles(self):
        extra = {'id': 'wait', 'offset': 2, 'seconds': .4, 'extra': True}
        for fast, rate in ((True, 6.3), (False, 5)):
            data, source, _ = setup([{'id': 'normal', 'offset': 1, 'seconds': .7}, extra, extra], fast=fast)
            before = copy.deepcopy(data)
            lowered, canonical = compile(data, source)
            self.assertEqual(data, before)
            item = timing(canonical)['utterances'][0]
            self.assertEqual(len(item['pauses']), 2)
            checked = validate_timing(canonical, canonical['shots'], RULES)
            self.assertEqual(checked['faults'], [], checked)
            self.assertAlmostEqual(sum(s['planned_seconds'] for s in checked['metrics']['utterances']), 4 / rate + .4)
            budget = chapter_budget([{'speaker': '甲', 'text': '明日再议。'}], RULES, canonical)
            self.assertEqual(budget['faults'], [], budget)
            self.assertAlmostEqual(budget['required_dialogue_seconds'], 4 / rate + .4)
            alignment = lowered['derivation']['shots'][0]['audio_alignments'][0]
            self.assertEqual(alignment['source_use_id'], 'U1')
            self.assertEqual(alignment['pauses'], item['pauses'])
            self.assertIn('说至本句“明日”后额外停顿0.4秒', canonical['shots'][0]['音色'])
            self.assertFalse(any(e['kind'] == 'hold' for e in timing(canonical)['events']))

    def test_offset_zero_terminal_and_restraint_intervals_share_actual_pause_clock(self):
        pauses = [{'id': 'lead', 'offset': 0, 'seconds': .4, 'extra': True},
                  {'id': 'middle', 'offset': 2, 'seconds': .3, 'extra': True},
                  {'id': 'end', 'offset': 5, 'seconds': .2, 'extra': True}]
        data, source, speech = setup(pauses)
        speech['voice']['emphasis'] = '“再议”。'
        data['shots'][0]['actions'].append(action('blink_restraint', id='emphasis', start=22, end=28,
            reason='emphasis', utterance_id='speech', char_range=[2, 4]))
        lowered, canonical = compile(data, source)
        bindings = {r['id']: r['performance_binding'] for r in lowered['derivation']['shots'][0]['actions']}
        for actual, value in zip(bindings['emphasis']['predicted_interval'], [2.25, 2.8]):
            self.assertAlmostEqual(actual, value)
        voice = canonical['shots'][0]['音色']
        self.assertIn('开口前额外停顿0.4秒', voice)
        self.assertIn('本句结束后额外停顿0.2秒', voice)
        # Old uniform timing would wrongly accept 2.0-2.8 for the emphasis;
        # starting after 2.25 now misses the actual predicted first syllable.
        data['shots'][0]['actions'][-1]['start'] = 23
        with self.assertRaisesRegex(ProductionDecisionError, 'predicted character span'):
            compile(data, source)
        data['shots'][0]['actions'][-1].update(id='tail', start=25, reason='decisive_tail', char_range=[3, 5])
        lowered, _ = compile(data, source)
        binding = next(r['performance_binding'] for r in lowered['derivation']['shots'][0]['actions'] if r['id'] == 'tail')
        for actual, value in zip(binding['predicted_interval'], [2.525, 2.8]):
            self.assertAlmostEqual(actual, value)

    def test_pause_capacity_is_paid_in_actual_macro_segment_not_other_free_time(self):
        data, source, speech = setup([{'id': 'extra', 'offset': 0, 'seconds': 2, 'extra': True}])
        with self.assertRaisesRegex(ProductionDecisionError, 'audio alignment'):
            compile(data, source)
        data, source, speech = setup([{'id': 'extra', 'offset': 2, 'seconds': .3, 'extra': True}], text='甲乙丙丁。')
        data['shots'][0]['actions'] = [speech]
        data['shots'][0]['ticks'] = [0, 10, 20, 40]
        speech.update(start=0, end=40)
        _, canonical = compile(data, source)
        item = timing(canonical)['utterances'][0]
        self.assertEqual(len(item['segments']), 3)
        costs = [estimate_utterance('甲乙丙丁。'[a:b], RULES['timing_policy']['profiles'][0],
                 segment_pauses('甲乙丙丁。', item['pauses'], a, b))['extra_pause_seconds']
                 for a, b in (s['char_range'] for s in item['segments'])]
        self.assertAlmostEqual(sum(costs), .3)
        checked = validate_timing(canonical, canonical['shots'], RULES)
        self.assertEqual(checked['faults'], [], checked)

    def test_invalid_offsets_duplicate_conflicts_and_fake_binding_reject_at_author_entry(self):
        good = {'id': 'p', 'offset': 2, 'seconds': .2, 'extra': True}
        cases = [[{**good, 'offset': -1}], [{**good, 'offset': 6}],
                 [good, {**good, 'seconds': .3}], [good, {**good, 'offset': 3}],
                 [good, {**good, 'id': 'other', 'seconds': .3}],
                 [{**good, 'extra': 'true'}], [{**good, 'source_use_id': 'not-this-use'}], None]
        for pauses in cases:
            data, source, _ = setup(pauses)
            with self.subTest(pauses=pauses), self.assertRaises(ProductionDecisionError):
                compile(data, source)
        data, source, speech = setup([good])
        speech['voice']['pauses'] = '句中不停顿。'
        with self.assertRaisesRegex(ProductionDecisionError, 'conflicts'):
            compile(data, source)

    def test_cut_point_and_terminal_pause_each_paid_once_across_actual_compiled_audio(self):
        pauses = [{'id': 'cut', 'offset': 2, 'seconds': .3, 'extra': True},
                  {'id': 'end', 'offset': 5, 'seconds': .2, 'extra': True}]
        data, source, _ = setup(pauses, fast=False)
        _, canonical = compile(data, source)
        first = canonical['shots'][0]
        first.pop('picture_timing', None)
        first.update(时长=2, 累计时长=2, 画面内容='甲说道：“明日再议。”')
        second = copy.deepcopy(first)
        second.update(镜头编号='EP90-002', 时长=2, 累计时长=4, 画面内容='甲承接上一句，口型与承接对白一致。')
        canonical['shots'].append(second)
        timing(canonical)['events'] = []
        timing(canonical)['utterances'][0]['segments'] = [
            {'shot_id': 'EP90-001', 'start': 1, 'end': 2, 'char_range': [0, 2]},
            {'shot_id': 'EP90-002', 'start': 0, 'end': 2, 'char_range': [2, 5]}]
        checked = validate_timing(canonical, canonical['shots'], RULES)
        self.assertEqual(checked['faults'], [], checked)
        self.assertEqual([round(s['planned_seconds'], 6) for s in checked['metrics']['utterances']], [.4, .9])
        budget = chapter_budget([{'speaker': '甲', 'text': '明日再议。'}], RULES, canonical)
        self.assertEqual(budget['faults'], [], budget)
        self.assertAlmostEqual(budget['required_dialogue_seconds'], 1.3)

    def test_malformed_source_binding_returns_none_without_fast_profile_authority(self):
        for data in (None, {'prevention_evidence': 'invalid'},
                     {'prevention_evidence': {'production_derivation': []}},
                     {'prevention_evidence': {'source_decisions': {'uses': 'invalid'}}}):
            self.assertEqual(source_uses(data), {})

    def test_actual_compiled_pause_delays_character_trigger_without_delaying_heard_fact_again(self):
        data, source, speech = setup([{'id': 'lead', 'offset': 0, 'seconds': .6, 'extra': True}], fast=False)
        data['shots'][0]['actions'] = [speech]
        _, canonical = compile(data, source)
        row = canonical['shots'][0]
        row.pop('picture_timing', None)
        row['画面内容'] = '【0.0—1.0秒】甲站定。【1.0—3.0秒】甲说道：“明日再议。”；乙点头。【3.0—4.0秒】甲站定。'
        event = {'id': 'response', 'kind': 'reaction', 'shot_id': row['镜头编号'],
                 'actor': '乙', 'action': '点头', 'basis': '乙点头', 'start': 2.2, 'end': 2.4,
                 'trigger': {'utterance_id': 'speech', 'offset': 2}, 'after': []}
        timing(canonical)['events'] = [event]
        checked = validate_timing(canonical, canonical['shots'], RULES)
        self.assertTrue(any('早于' in r['message'] for r in checked['faults']), checked)
        self.assertAlmostEqual(checked['metrics']['utterances'][0]['character_reached_at'][2], 2.3)
        event.update(start=2.3, end=2.5)
        self.assertEqual(validate_timing(canonical, canonical['shots'], RULES)['faults'], [])
        self.assertEqual(chapter_budget([{'speaker': '甲', 'text': '明日再议。'}], RULES, canonical)['faults'], [])
        timing(canonical)['utterances'][0]['pauses'].append({'id': 'end', 'offset': 5, 'seconds': .2, 'extra': True})
        checked = validate_timing(canonical, canonical['shots'], RULES)
        self.assertEqual(checked['faults'], [])
        self.assertEqual(checked['metrics']['utterances'][0]['character_reached_at'][-1], 3)

    def test_numeric_voice_pause_cannot_be_silently_replaced_or_guessed(self):
        data, source, speech = setup([{'id': 'p', 'offset': 2, 'seconds': .4, 'extra': True}])
        for voice in ('“明日”后停顿1秒，再继续说。', '句中停顿0.4秒。',
                      '“再议”后停顿0.4秒。', '“明日”后停顿半秒。'):
            speech['voice']['pauses'] = voice
            with self.subTest(voice=voice), self.assertRaisesRegex(ProductionDecisionError, 'voice pause'):
                compile(data, source)
        speech['voice']['pauses'] = '“明日”后停顿0.4秒，再继续说。'
        _, canonical = compile(data, source)
        self.assertIn('“明日”后额外停顿0.4秒', canonical['shots'][0]['音色'])


if __name__ == '__main__':
    unittest.main()
