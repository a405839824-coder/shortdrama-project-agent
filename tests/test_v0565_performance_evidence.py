"""Real compiler/replay controls for source-bound performance evidence.

These public synthetic controls are not first-draft or manuscript-quality data.
No renderer, binding inspector or validator is mocked.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'tests'), str(ROOT/'skills/shortdrama-storyboard/scripts')]
from author_storyboard import _render_decisions, record_attempt
from performance_evidence import (FIELD, CHAIN_FIELDS, derive_performance_evidence,
                                  inspect_performance_evidence, inspect_performance_input)
from production_decisions import lower_production_decisions
from production_derivation import attach_derivation, digest, inspect_derivation
from production_plan_test_support import with_plan
from source_decisions import compile_source_decisions
from test_v0527_source_decision_independent_audit import complete_source_input
from test_v0527_production_decisions import action
from validate_storyboard import validate_project


def case(*, plan=True, gaze=True):
    data, raw, texts = complete_source_input()
    data['project']['prevention_evidence']['performance_chains'] = []
    data['shots'][0]['editorial']['功能类型'] = '关系反应'
    # An explicit stable state with current source text. No performance event
    # is added to satisfy a seven-field or blink quota.
    data['shots'][0]['actions'] = []
    if not gaze:
        data['initial_world']['actors']['甲']['gaze'] = None
    if plan:
        raw = with_plan(data, raw, texts)
    return data, raw, texts


def build(data, raw, texts):
    source = compile_source_decisions(raw, data['project']['prevention_evidence']['source_decisions'],
        source_texts=texts, expected_graph_sha256=hashlib.sha256(raw).hexdigest())
    lowered = lower_production_decisions(data, source_compilation=source)
    canonical = attach_derivation(_render_decisions(lowered['typed_decisions']), data, raw,
                                  production=lowered, source_compilation=source)
    return canonical, lowered, source


def performance_issues(canonical):
    return [issue for issue in validate_project(canonical, mode='audit')['issues']
            if issue['error_code'] == 'E-PERFORMANCE-001']


class PerformanceEvidenceTests(unittest.TestCase):
    def test_malformed_unrelated_shapes_do_not_raise_cascading_source_errors(self):
        for project in (None, [], {'prevention_evidence': None}):
            result = inspect_performance_input({'project': project, 'shots': [None]})
            self.assertEqual(result['basis'], 'unknown')
            self.assertEqual(result['findings'], [])
        data, raw, _ = case(); plan = json.loads(raw)['production_plan']
        plan['shots'][0]['visible'] = [None, {'kind': []}]
        self.assertEqual(inspect_performance_input(data, raw_plan=plan)['findings'], [])

    def test_static_silent_plan_binds_actual_state_without_extra_actions(self):
        data, raw, texts = case(); before = copy.deepcopy(data)
        canonical, lowered, source = build(data, raw, texts)
        self.assertEqual(data, before)
        self.assertEqual(lowered['derivation']['world_events'], [])
        self.assertEqual(canonical['prevention_evidence']['performance_chains'], [])
        binding = inspect_derivation(canonical)
        self.assertTrue(binding['valid'], binding['faults'])
        observed = inspect_performance_evidence(canonical, binding)
        self.assertTrue(observed['verified'])
        self.assertEqual(observed['unknown_evidence'], [])
        self.assertEqual(observed['semantic_status'], 'unknown')
        actor = observed['shots'][0]['actors'][0]
        self.assertEqual(set(actor['fields']), set(CHAIN_FIELDS))
        self.assertEqual(actor['events'], [])
        self.assertTrue(all(value['semantic_status'] == 'unknown' for value in actor['fields'].values()))
        self.assertEqual(performance_issues(canonical), [])

    def test_missing_actual_gaze_is_unknown_and_still_blocks(self):
        canonical, _, _ = build(*case(gaze=False))
        binding = inspect_derivation(canonical)
        self.assertTrue(binding['valid'], binding['faults'])
        observed = inspect_performance_evidence(canonical, binding)
        self.assertIn({'shot_id': 'EP90-001', 'actor': '甲', 'field': 'gaze_path'}, observed['unknown_evidence'])
        self.assertTrue(any('gaze_path' in row['evidence'] for row in performance_issues(canonical)))

    def test_no_visible_eyes_keeps_unknown_gaze_without_fabricated_target(self):
        data, raw, texts = case(gaze=False)
        data['shots'][0]['camera']['position_xyz'] = [0, -4, 1.5]
        raw = with_plan(data, raw, texts)
        canonical, _, _ = build(data, raw, texts)
        gaze = canonical['prevention_evidence'][FIELD]['shots'][0]['actors'][0]['fields']['gaze_path']
        self.assertEqual(gaze['evidence_status'], 'not_applicable')
        self.assertEqual(gaze['modelled']['eye_visibility_ticks'], [])
        self.assertNotEqual(gaze['modelled']['start'].get('status'), 'resolved')
        self.assertEqual(performance_issues(canonical), [])

    def test_visible_eyes_closed_for_every_tick_need_no_gaze_target(self):
        data, raw, texts = case(gaze=False)
        data['initial_world']['actors']['甲']['eye_state'] = 'closed'
        raw = with_plan(data, raw, texts)
        canonical, lowered, _ = build(data, raw, texts)
        self.assertTrue(inspect_derivation(canonical)['valid'])
        field = canonical['prevention_evidence'][FIELD]['shots'][0]['actors'][0]['fields']['gaze_path']
        self.assertEqual(field['modelled']['eye_visibility_ticks'], [[0, 40]])
        self.assertEqual(field['evidence_status'], 'not_applicable')
        self.assertEqual(field['modelled']['tick_coverage']['closed_visible_ticks'], list(range(40)))
        self.assertEqual(field['semantic_status'], 'unknown')
        samples = lowered['derivation']['shots'][0]['performance_eye_timeline']['actors']['甲']
        self.assertEqual(len(samples), 41)
        self.assertTrue(all(s['eye_state'] == 'closed' and s['gaze'] is None for s in samples))
        self.assertEqual(data['initial_world']['actors']['甲']['gaze'], None)
        self.assertEqual(lowered['derivation']['world_events'], [])
        self.assertEqual(performance_issues(canonical), [])

    def test_closed_endpoints_do_not_hide_intermediate_open_unknown_gaze(self):
        data, raw, texts = case(gaze=False)
        data['initial_world']['actors']['甲']['eye_state'] = 'closed'
        data['shots'][0]['actions'] = [
            action('eye_state', id='open', start=10, end=12, to='natural_open'),
            action('eye_state', id='close', start=25, end=30, to='closed', after=['open'])]
        raw = with_plan(data, raw, texts)
        canonical, _, _ = build(data, raw, texts)
        self.assertTrue(inspect_derivation(canonical)['valid'])
        actor = canonical['prevention_evidence'][FIELD]['shots'][0]['actors'][0]
        self.assertEqual(actor['start_state']['eye_state'], actor['end_state']['eye_state'])
        self.assertEqual(actor['start_state']['eye_state'], 'closed')
        gaze = actor['fields']['gaze_path']
        self.assertEqual(gaze['evidence_status'], 'unknown')
        self.assertEqual(gaze['modelled']['tick_coverage']['unknown_ticks'], list(range(12, 30)))
        self.assertTrue(performance_issues(canonical))

    def test_resolved_endpoints_do_not_hide_intermediate_unknown_gaze(self):
        data, raw, texts = case()
        target = copy.deepcopy(data['initial_world']['actors']['甲']['gaze'])
        data['shots'][0]['actions'] = [
            action('look', id='clear', start=10, end=12, target=None, tracking=False, head_mode='still'),
            action('look', id='restore', start=25, end=30, target=target, tracking=True,
                   head_mode='still', after=['clear'])]
        raw = with_plan(data, raw, texts)
        canonical, _, _ = build(data, raw, texts)
        self.assertTrue(inspect_derivation(canonical)['valid'])
        gaze = canonical['prevention_evidence'][FIELD]['shots'][0]['actors'][0]['fields']['gaze_path']
        self.assertEqual(gaze['modelled']['start']['status'], 'resolved')
        self.assertEqual(gaze['modelled']['end']['status'], 'resolved')
        self.assertEqual(gaze['evidence_status'], 'unknown')
        self.assertEqual(gaze['modelled']['tick_coverage']['unknown_ticks'], list(range(10, 25)))
        self.assertTrue(performance_issues(canonical))

    def test_missing_or_unknown_tick_cannot_prove_continuously_closed_eyes(self):
        data, raw, texts = case(gaze=False)
        data['initial_world']['actors']['甲']['eye_state'] = 'closed'
        raw = with_plan(data, raw, texts)
        _, lowered, source = build(data, raw, texts)
        for mutation in ('missing_timeline', 'missing_tick', 'unknown_eye'):
            with self.subTest(mutation=mutation):
                incomplete = copy.deepcopy(lowered)
                trace = incomplete['derivation']['shots'][0]
                if mutation == 'missing_timeline':
                    del trace['performance_eye_timeline']
                elif mutation == 'missing_tick':
                    trace['performance_eye_timeline']['actors']['甲'].pop(20)
                else:
                    trace['performance_eye_timeline']['actors']['甲'][20]['eye_state'] = 'unknown'
                evidence = derive_performance_evidence(data, incomplete, source)
                gaze = evidence['shots'][0]['actors'][0]['fields']['gaze_path']
                self.assertEqual(gaze['evidence_status'], 'unknown')
                self.assertEqual(gaze['semantic_status'], 'unknown')

    def test_unknown_breath_is_preserved_when_body_has_actual_evidence(self):
        data, raw, texts = case()
        del data['initial_world']['actors']['甲']['performance_state']
        raw = with_plan(data, raw, texts)
        canonical, _, _ = build(data, raw, texts)
        field = canonical['prevention_evidence'][FIELD]['shots'][0]['actors'][0]['fields']['body_or_breath']
        self.assertEqual(field['modelled']['start_performance']['breath'], 'unknown')
        self.assertEqual(field['evidence_status'], 'bound')
        self.assertEqual(field['semantic_status'], 'unknown')

    def test_new_block_does_not_waive_the_legacy_seven_fields(self):
        canonical, _, _ = build(*case(plan=False))
        self.assertNotIn(FIELD, canonical['prevention_evidence'])
        self.assertTrue(performance_issues(canonical))
        canonical['prevention_evidence'][FIELD] = {'contract': 'performance-evidence-v1', 'shots': [], 'status': 'pass'}
        faults = performance_issues(canonical)
        self.assertTrue(any('未通过' in x['evidence'] for x in faults))
        self.assertTrue(any('缺字段' in x['evidence'] for x in faults))

    def test_old_valid_legacy_chain_remains_supported(self):
        canonical, _, _ = build(*case(plan=False))
        canonical['prevention_evidence']['performance_chains'] = [{
            'shot_id': 'EP90-001', **{name: '作者显式记录的旧版证据，语义仍须独立审查。' for name in CHAIN_FIELDS}}]
        self.assertEqual(performance_issues(canonical), [])

    def test_tampered_projection_even_with_resealed_outer_hash_fails_replay(self):
        canonical, _, _ = build(*case())
        block = canonical['prevention_evidence'][FIELD]
        block['shots'][0]['actors'][0]['fields']['stimulus']['declaration'] = '伪造新的情绪因果'
        from production_derivation import owned_content
        payload = canonical['prevention_evidence']['production_derivation']
        payload['content_sha256'] = digest(owned_content(canonical))
        payload['binding_sha256'] = digest({k: v for k, v in payload.items() if k != 'binding_sha256'})
        binding = inspect_derivation(canonical)
        self.assertFalse(binding['valid'])
        self.assertTrue(any('recomputed' in f['message'] for f in binding['faults']))
        self.assertTrue(performance_issues(canonical))

    def test_deleted_projection_cannot_downgrade_to_verified_plan(self):
        canonical, _, _ = build(*case())
        del canonical['prevention_evidence'][FIELD]
        self.assertFalse(inspect_derivation(canonical)['valid'])
        self.assertTrue(performance_issues(canonical))

    def test_stale_compiler_cannot_authorize_projection(self):
        canonical, _, _ = build(*case())
        binding = canonical['prevention_evidence']['production_derivation']
        binding['compiler_fingerprints']['performance_evidence.py'] = '0' * 64
        binding['binding_sha256'] = digest({k: v for k, v in binding.items() if k != 'binding_sha256'})
        self.assertFalse(inspect_derivation(canonical)['valid'])
        self.assertTrue(performance_issues(canonical))

    def test_missing_plan_choice_is_diagnosed_without_inventing_a_reaction(self):
        data, raw, texts = case()
        graph = json.loads(raw); graph['production_plan']['shots'][0]['design']['performance'][0]['stimulus'] = ''
        before = copy.deepcopy((data, graph))
        result = inspect_performance_input(data, raw_plan=graph['production_plan'])
        self.assertEqual(result['basis'], 'unverified_v3_plan_shape')
        self.assertTrue(any('stimulus' in x['message'] for x in result['findings']))
        self.assertEqual((data, graph), before)
        self.assertEqual(inspect_performance_input(data)['basis'], 'unknown')

    def test_unverified_source_still_diagnoses_explicit_speaker_without_choices(self):
        data, raw, _ = case(); plan = json.loads(raw)['production_plan']
        use = data['project']['prevention_evidence']['source_decisions']['uses'][0]
        use['actor'] = '甲'
        data['shots'][0]['actions'] = [{'kind': 'speak', 'source_use_id': use['id']}]
        plan['shots'][0]['visible'] = []
        plan['shots'][0]['design']['performance'] = []
        result = inspect_performance_input(data, raw_plan=plan)
        self.assertEqual(result['basis'], 'unverified_v3_plan_shape')
        self.assertTrue(any('speaker choices: 甲' in row['message'] for row in result['findings']))
        self.assertEqual(result['semantic_status'], 'unknown')

    def test_injected_compiler_evidence_is_rejected_at_input_and_derivation(self):
        data, raw, texts = case()
        canonical, lowered, source = build(data, raw, texts)
        data['project']['prevention_evidence'][FIELD] = canonical['prevention_evidence'][FIELD]
        self.assertTrue(inspect_performance_input(data, source)['findings'])
        with self.assertRaisesRegex(ValueError, 'compiler-owned'):
            derive_performance_evidence(data, lowered, source)

    def test_real_record_attempt_preserves_original_and_binds_derived_evidence(self):
        data, raw, _ = case()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); input_bytes = json.dumps(data, ensure_ascii=False).encode()
            (root/'input.json').write_bytes(input_bytes); (root/'graph.json').write_bytes(raw)
            result = record_attempt(root/'input.json', root/'attempts', attempt_id='first-and-only',
                                    source_graph_path=root/'graph.json', require_production_plan=True)
            self.assertEqual(result['status'], 'rendered_pending_preflight', result)
            self.assertEqual((root/'attempts/first-and-only/input.original.json').read_bytes(), input_bytes)
            self.assertNotIn(FIELD, json.loads(input_bytes)['project']['prevention_evidence'])
            canonical = json.loads(Path(result['canonical_path']).read_text())
            self.assertTrue(inspect_derivation(canonical)['valid'])
            self.assertEqual(performance_issues(canonical), [])


if __name__ == '__main__':
    unittest.main()
