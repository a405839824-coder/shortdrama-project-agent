"""Existing physical actions through real compiler and shared event timing."""
import copy
import hashlib
import unittest

from test_v0556_author_pauses import setup, compile, timing
from test_v0556_timing_profiles import RULES
from test_v0527_production_decisions import action, voice_spec, compile_source, source_fixture
from production_decisions import ProductionDecisionError
from timing_model import validate_timing, chapter_budget


class TimingActionTests(unittest.TestCase):
    def test_actual_standing_and_speech_share_the_same_window_and_effect_once(self):
        data, source, speech = setup([], fast=False)
        actor = data['initial_world']['actors']['甲']
        actor.update(posture='坐姿', eye_height=1.0, gaze=None)
        data['initial_world']['points']['原地'] = {'scene_id': 'ROOM', 'position_xyz': [0, 0, 0]}
        rise = action('posture', id='rise', start=10, end=30, to='站立', point='原地',
                      landmarks={'eye_height': 1.5, 'reach_origin_offset_xyz': [0, 0, 1.2],
                                 'hands': {'left': [-.2, .3, 1], 'right': [.2, .3, 1]}})
        data['shots'][0]['actions'] = [speech, rise]
        lowered, canonical = compile(data, source)
        self.assertEqual(lowered['derivation']['final_world']['actors']['甲']['posture'], '站立')
        world = {e['id']: e for e in lowered['derivation']['world_events']}
        self.assertEqual(world['rise']['effect_occurrences'], 1)
        self.assertEqual((world['rise']['start'], world['rise']['end']), (1, 3))
        self.assertEqual((world['speech']['start'], world['speech']['end']), (1, 3))
        checked = validate_timing(canonical, canonical['shots'], RULES)
        self.assertEqual(checked['faults'], [], checked)
        result = chapter_budget([{'speaker': '甲', 'text': '明日再议。'}], RULES, canonical,
                                budget={'dialogue_seconds': 2, 'action_seconds': 2, 'transition_seconds': 0})
        self.assertEqual(result['faults'], [], result)
        self.assertEqual(result['timeline_coverage'], 'validated_project_timeline')
        self.assertEqual(result['required_total_seconds'], 3)
        rise['end'] = 15
        with self.assertRaisesRegex(ProductionDecisionError, 'too short'):
            compile(data, source)

    def test_drinking_actual_canonical_event_cannot_overlap_compiled_speaker(self):
        data, source, speech = setup([], fast=False)
        data['shots'][0]['actions'] = [speech]
        _, canonical = compile(data, source)
        row = canonical['shots'][0]
        row.pop('picture_timing', None)
        row['画面内容'] = '【0.0—1.0秒】甲站定。【1.0—3.0秒】甲说道：“明日再议。”甲喝水。【3.0—4.0秒】甲站定。'
        timing(canonical)['events'] = [{'id': 'drink', 'kind': 'action', 'shot_id': row['镜头编号'],
            'actor': '甲', 'action': '喝水', 'basis': '甲喝水', 'start': 1.5, 'end': 2, 'after': []}]
        self.assertTrue(any('喝水/吞咽' in r['message'] for r in validate_timing(canonical, canonical['shots'], RULES)['faults']))
        self.assertTrue(chapter_budget([{'speaker': '甲', 'text': '明日再议。'}], RULES, canonical)['faults'])
        # Separate physical time is legal; no new typed drink action is claimed.
        row['画面内容'] = '【0.0—1.0秒】甲站定。【1.0—3.0秒】甲说道：“明日再议。”【3.0—4.0秒】甲喝水。'
        timing(canonical)['events'][0].update(start=3, end=3.5, after=['speech'])
        self.assertEqual(validate_timing(canonical, canonical['shots'], RULES)['faults'], [])

    def test_wait_for_actual_reply_after_blocks_early_or_missing_response(self):
        data, _, question = setup([], text='何时开始？', fast=False)
        graph, uses, texts = source_fixture()
        text = '甲问：“何时开始？”乙答：“明日。”'
        texts['DOC'] = text
        graph['sources']['DOC'] = hashlib.sha256(text.encode()).hexdigest()
        first = graph['facts'][0]
        first.update(content_text='何时开始？', supports=[{'source_id': 'DOC', 'start': 0, 'end': 10, 'text': text[:10]}])
        second = copy.deepcopy(first)
        second.update(id='F2', subject='乙', event_id='E2', content_text='明日。',
                      supports=[{'source_id': 'DOC', 'start': 10, 'end': len(text), 'text': text[10:]}])
        graph['facts'].append(second)
        uses['uses'][0]['text'] = '何时开始？'
        uses['uses'].append({'id': 'U2', 'fact_ids': ['F2'], 'mode': 'spoken', 'wording': 'verbatim', 'actor': '乙', 'text': '明日。'})
        data['project']['prevention_evidence']['source_decisions'] = uses
        data['project']['prevention_evidence']['source_ledger'] = [{'source_id': 'DOC', 'source_text': text}]
        other = copy.deepcopy(data['initial_world']['actors']['甲'])
        other.update(position_xyz=[.5, 0, 0], reach_origin_xyz=[.5, 0, 1.2], gaze=None)
        data['initial_world']['actors']['乙'] = other
        question.update(start=5, end=15)
        answer_voice = voice_spec(); answer_voice['emphasis'] = '“明日”。'
        reply = action('speak', id='reply', start=17, end=27, after=['speech'], source_use_id='U2', voice=answer_voice)
        acknowledgement = action('blink', id='ack', start=30, end=32, after=['reply'])
        data['shots'][0]['actions'] = [question, reply, acknowledgement]
        source = compile_source(graph, uses, texts)
        lowered, canonical = compile(data, source)
        self.assertIn('乙本句发言结束后', canonical['shots'][0]['画面内容'])
        self.assertEqual(validate_timing(canonical, canonical['shots'], RULES)['faults'], [])
        self.assertEqual(chapter_budget([{'speaker': '甲', 'text': '何时开始？'}, {'speaker': '乙', 'text': '明日。'}], RULES, canonical)['faults'], [])
        for start, after in ((25, ['reply']), (30, ['missing-reply'])):
            acknowledgement.update(start=start, end=start + 2, after=after)
            with self.subTest(start=start, after=after), self.assertRaisesRegex(ProductionDecisionError, 'prerequisite'):
                compile(data, source)


if __name__ == '__main__':
    unittest.main()
