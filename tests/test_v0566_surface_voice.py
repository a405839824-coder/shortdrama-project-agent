"""Public synthetic controls; these never sign off a real manuscript."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'skills/shortdrama-storyboard/scripts')]
import production_surface
from production_decisions import lower_production_decisions, ProductionDecisionError
from source_decisions import compile_source_decisions
from source_action_plans import construct_source_action_plans, source_step
from author_storyboard import _render_decisions
from production_plan_test_support import frozen_plan
from test_v0527_production_decisions import production_fixture, action, voice_spec
from test_v0527_source_decisions import fact


def fixture(*, reaction_start=30, reaction_stance='reported', continuation=False):
    data = production_fixture()
    world = data['initial_world']
    world['props'] = {'古镜': {'anchor': {'point': '案左'}, 'written_ref': None,
        'physical': {'material': 'metal', 'contents': 'none', 'integrity': 'intact'},
        'surface': {'pattern': 'still', 'glow': 'faint', 'mist': 'none', 'extent_m': .3}}}
    changed = {'pattern': 'ripple', 'glow': 'flicker', 'mist': 'local_white', 'extent_m': .3}
    text = '古镜镜面泛起涟漪，白雾缭绕。古镜残音说道：明日再议。'
    graph = {'contract': 'source-facts-v1', 'sources': {'DOC': hashlib.sha256(text.encode()).hexdigest()},
        'facts': [fact('FX', text, subject='古镜', event='FX', channel='action', content='',
            availability='not_applicable', occurrence='occurred', expression='not_applicable'),
            fact('SAY', text, subject='古镜残音', event='SAY')]}
    uses = {'contract': 'source-decisions-v1', 'uses': [
        {'id': 'UFX', 'fact_ids': ['FX'], 'mode': 'physical_action', 'wording': 'authored', 'actor': '古镜', 'text': '古镜镜面泛起涟漪，白雾缭绕。'},
        {'id': 'USAY', 'fact_ids': ['SAY'], 'mode': 'spoken', 'wording': 'verbatim', 'actor': '古镜残音', 'text': '明日再议。'}], 'written_objects': []}
    row = data['shots'][0]
    row.update(ticks=[0, 10, 30, 40], source_unit_ids=['DOC'])
    row['actions'] = [source_step(action_id='fx', source_use_id='UFX', step_id='surface', start=0, end=10),
        action('speak', id='voice', start=10, end=30, source_use_id='USAY', voice=voice_spec(),
               emitter={'prop': '古镜', 'mode': 'disembodied'}, after=['fx']),
        action('expression', id='reaction', start=reaction_start, end=reaction_start+10, to='双眼微睁，嘴唇微张')]
    data['project']['prevention_evidence']['source_decisions'] = uses
    data['project']['prevention_evidence']['source_ledger'] = [{'source_id': 'DOC', 'source_text': text}]
    graph['action_plans'] = construct_source_action_plans(initial_world=world, events=[
        {'source_use_id': 'UFX', 'steps': [{'step_id': 'surface', 'kind': 'surface_effect', 'parameters': {'to': changed}, 'after': []}]}])
    if continuation:
        row2 = copy.deepcopy(row)
        row2.update(shot_id='EP90-002', shot_uid='SURFACE-CONTINUE', actions=[action('hold', id='hold2', start=0, end=40)])
        row2['transition'] = {'relation': 'continuous', 'basis': '镜面异动后连续观察，没有新增事件。'}
        data['shots'].append(row2)
    replan(data, graph, {'DOC': text}, reaction_stance)
    if continuation:
        # This second shot tests low-level state carry only, not a new
        # production-plan approval for an information-free repeat shot.
        graph.pop('production_plan')
    return data, graph, {'DOC': text}


def replan(data, graph, texts, reaction_stance='reported'):
    graph['production_plan'] = frozen_plan(data, graph, texts)
    for shot in graph['production_plan']['shots']:
        for item in shot['design']['performance']:
            if not item['fact_ids']: item['fact_ids'] = ['FX']
    knowledge = graph['production_plan']['knowledge']
    knowledge['initial'] = []
    for use in knowledge['uses']:
        if use['action_id'] == 'reaction':
            use.update(fact_ids=['SAY'], stance=reaction_stance, evidence_fact_ids=['SAY'])
        elif use['action_id'] in ('fx', 'voice'):
            use.update(actor='古镜' if use['action_id'] == 'fx' else '古镜残音',
                       fact_ids=[], event_fact_ids=['FX' if use['action_id'] == 'fx' else 'SAY'], stance='not_applicable')
    knowledge['acquisitions'] = [{'id': 'heard', 'actor': '甲', 'fact_ids': ['SAY'],
        'evidence_fact_ids': ['SAY'], 'action_id': 'voice', 'mode': 'heard'}]


def lower(data, graph, texts):
    raw = json.dumps(graph, ensure_ascii=False).encode()
    compiled = compile_source_decisions(raw, data['project']['prevention_evidence']['source_decisions'],
        source_texts=texts, expected_graph_sha256=hashlib.sha256(raw).hexdigest())
    return lower_production_decisions(data, source_compilation=compiled)


class SurfaceVoiceTests(unittest.TestCase):
    def test_scene_echo_is_bound_to_one_actual_prop_voice_and_clock(self):
        data, graph, texts = fixture()
        data['shots'][0]['actions'][1]['emitter']['effect'] = 'short_echo'
        data['shots'][0]['editorial']['环境音效'] = {'mode':'action_bound','cues':[
            {'action_id':'voice','sound':'scene_echo','volume':'soft'}]}
        replan(data, graph, texts)
        result = lower(data, graph, texts)
        shot = _render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('1.0—3.0秒：古镜残音原声的细微短回声', shot['环境音效'])
        self.assertIn('不添加字词', shot['环境音效'])
        self.assertIn('古镜残音说道：“明日再议。”', shot['画面内容'])
        data['shots'][0]['actions'][1]['emitter']['effect'] = 'dry'
        replan(data, graph, texts)
        with self.assertRaisesRegex(ProductionDecisionError,'actual scene_echo cue'):
            lower(data, graph, texts)
        data['shots'][0]['actions'][1]['emitter']['effect'] = 'short_echo'
        data['shots'][0]['editorial']['环境音效'] = {'mode':'none'}
        replan(data, graph, texts)
        with self.assertRaisesRegex(ProductionDecisionError,'actual scene_echo cue'):
            lower(data, graph, texts)

    def test_real_source_action_voice_receipt_and_static_continuation(self):
        planned = lower(*fixture())
        self.assertEqual(planned['derivation']['production_prevention']['status'], 'pass')
        self.assertEqual(planned['derivation']['production_prevention']['semantic_status'], 'pending_independent_review')
        args = fixture(continuation=True)
        before = copy.deepcopy(args)
        result = lower(*args)
        self.assertEqual(args, before)
        canonical = _render_decisions(result['typed_decisions'])
        first, second = canonical['shots']
        self.assertIn('镜前无白雾', first['起始状态'])
        self.assertIn('镜前有白雾', second['起始状态'])
        self.assertNotIn('缭绕', second['起始状态'])
        self.assertIn('镜面具有涟漪纹理', second['起始状态'])
        self.assertIn('古镜残音说道：“明日再议。”', first['画面内容'])
        self.assertIn('无人物形体', first['画面内容'])
        self.assertNotIn('古镜残音（', first['可见主体'])
        record = next(a for a in result['derivation']['shots'][0]['actions'] if a['id'] == 'voice')
        self.assertEqual(record['mouth_sync_intervals'], [])
        self.assertIsNone(record['speaking_gaze_check'])
        self.assertEqual(record['emitter'], {'prop': '古镜', 'mode': 'disembodied'})
        self.assertEqual(result['derivation']['final_world']['completed'].count('fx'), 1)
        self.assertEqual(result['derivation']['source_action_execution']['completed_steps'][0]['observed_effect']['surface']['mist'], 'local_white')
        self.assertFalse(result['derivation']['commit_eligible'])
        self.assertNotIn('不雾化', first['光影'])
        self.assertIn('禁止全景泛白', first['生成限制'])
        self.assertEqual(result['typed_decisions']['shots'][0]['end_state'], result['typed_decisions']['shots'][1]['start_state'])

    def test_hearing_is_after_actual_emission_and_remains_reported(self):
        for kwargs, message in [({'reaction_start': 20}, 'before actually hearing'),
                                ({'reaction_stance': 'known'}, 'before its acquisition')]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ProductionDecisionError, message):
                lower(*fixture(**kwargs))

    def test_missing_unknown_offscene_or_bodily_emitter_cannot_bypass_guards(self):
        for change, message in [('missing', 'unknown action actor'), ('unknown', 'unknown scene voice emitter'),
                                ('offscene', 'another physical scene'), ('bodily', 'bodily speaker')]:
            data, graph, texts = fixture()
            voice = data['shots'][0]['actions'][1]
            if change == 'missing': voice.pop('emitter')
            if change == 'unknown': voice['emitter']['prop'] = '不存在的镜'
            if change == 'offscene':
                data['initial_world']['scenes']['OTHER'] = copy.deepcopy(data['initial_world']['scenes']['ROOM'])
                data['initial_world']['points']['异室镜位'] = {'scene_id': 'OTHER', 'position_xyz': [0,0,1]}
                data['initial_world']['props']['异室镜'] = {'anchor': {'point': '异室镜位'}, 'written_ref': None}
                voice['emitter']['prop'] = '异室镜'
            if change == 'bodily': data['initial_world']['actors']['古镜残音'] = copy.deepcopy(data['initial_world']['actors']['甲'])
            replan(data, graph, texts)
            with self.subTest(change=change), self.assertRaisesRegex(ProductionDecisionError, message):
                lower(data, graph, texts)

    def test_source_effect_cannot_expand_be_hidden_or_mutate_other_state(self):
        data, graph, texts = fixture()
        graph['action_plans']['events'][0]['steps'][0]['parameters']['to']['extent_m'] = .7
        with self.assertRaisesRegex(ProductionDecisionError, 'cannot expand'):
            lower(data, graph, texts)
        data, graph, texts = fixture()
        data['shots'][0]['camera'].update(position_xyz=[0,1,1.5], hfov_deg=18, vfov_deg=28, frame='face')
        data['initial_world']['points']['取景中心']['position_xyz'] = [0,0,1.45]
        replan(data, graph, texts)
        with self.assertRaisesRegex(ProductionDecisionError, 'must remain in frame'):
            lower(data, graph, texts)
        data, graph, texts = fixture()
        actual = production_surface.apply
        def corrupt(world, action, info, tick):
            actual(world, action, info, tick)
            world['props']['古镜']['physical']['integrity'] = 'broken'
        with patch.object(production_surface, 'apply', corrupt), self.assertRaisesRegex(ProductionDecisionError, 'undeclared|extra|outside|unowned'):
            lower(data, graph, texts)


if __name__ == '__main__': unittest.main()
