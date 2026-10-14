"""Synthetic dependency controls; frozen manuscript replay lives in evidence.

No control grants semantic approval or fills an absent source/author decision.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT / 'tests'),
               str(ROOT / 'skills/shortdrama-storyboard/scripts')]

from authoring_preflight import CheckCollection, inspect_production_editorial
from production_input import inspect_production_input, _Collection
from production_plan_test_support import with_plan
from test_v0527_source_decision_independent_audit import complete_source_input
from test_v0527_production_decisions import voice_spec


class InputDiagnosticTests(unittest.TestCase):
    def inspect(self, raw, graph, **kwargs):
        return inspect_production_input(raw, graph_bytes=graph,
            expected_graph_sha256=hashlib.sha256(graph).hexdigest() if graph is not None else None,
            require_production_plan=True, **kwargs)

    def test_compilation_failure_does_not_invent_a_missing_frozen_plan(self):
        raw, graph, texts = complete_source_input()
        graph = with_plan(raw, graph, texts)
        original = copy.deepcopy(raw)
        # An exact frozen plan exists, but a source-use wording error blocks its
        # verified compilation. Preserve that cause and defer its dependents.
        raw['project']['prevention_evidence']['source_decisions']['uses'][0]['wording'] = 'invalid-mode'
        report = self.inspect(raw, graph)
        plan = next(c for c in report['checks'] if c['check'] == 'frozen_production_plan')
        self.assertEqual(plan['status'], 'not_checked')
        self.assertEqual(plan['blocked_by'], ['source_compilation'])
        self.assertTrue(plan['upstream_findings'])
        self.assertTrue(any(f['stage'] == 'source_compilation' for f in report['findings']))
        self.assertFalse(any(f['stage'] == 'frozen_production_plan' for f in report['findings']))
        self.assertFalse(report['ready_for_canonical'])
        self.assertEqual(raw['shots'], original['shots'])

    def test_an_actually_absent_plan_remains_a_failure(self):
        raw, graph, _ = complete_source_input()
        report = self.inspect(raw, graph)
        checks = {c['check']: c for c in report['checks']}
        self.assertEqual(checks['source_compilation']['status'], 'pass')
        self.assertEqual(checks['frozen_production_plan']['status'], 'fail')
        self.assertEqual(checks['frozen_production_plan']['findings'][0]['code'], 'E-PRODUCTION-PLAN-001')

    def test_valid_plan_is_checked_but_not_independently_approved(self):
        raw, graph, texts = complete_source_input()
        graph = with_plan(raw, graph, texts)
        report = self.inspect(raw, graph)
        self.assertTrue(report['ready_for_canonical'], report['issues'])
        self.assertEqual(next(c for c in report['checks'] if c['check'] == 'frozen_production_plan')['status'], 'pass')
        self.assertFalse(report['commit_eligible'])
        self.assertIsNone(report['semantic_accuracy_percent'])

    def test_independent_boundary_schema_and_voice_failures_survive_missing_source(self):
        raw, _, _ = complete_source_input()
        raw['shots'][0]['transition'] = {'relation': 'continuous', 'basis': '', 'bridge': {}}
        raw['project']['prevention_evidence']['cognition_matrix'] = []
        raw['shots'][0]['actions'] = [
            {'kind': 'speak', 'source_use_id': 'missing-a'},
            {'kind': 'speak', 'source_use_id': 'missing-b'}]
        before = copy.deepcopy(raw)
        report = self.inspect(raw, None)
        checks = {c['check']: c for c in report['checks']}
        for name in ('project_schema', 'shots[0].transition.initial',
                     'shots[0].transition.basis', 'shots[0].transition.bridge',
                     'shots[0].actions[0].voice.settings', 'shots[0].actions[1].voice.settings'):
            self.assertEqual(checks[name]['status'], 'fail', name)
        for name in ('frozen_production_plan', 'shots[0].actions[0].voice', 'shots[0].actions[1].voice'):
            self.assertEqual(checks[name]['status'], 'not_checked', name)
            self.assertTrue(checks[name]['reason'])
        self.assertEqual(raw, before)

    def test_valid_initial_boundary_remains_checkable_without_source(self):
        raw, _, _ = complete_source_input()
        report = self.inspect(raw, None)
        checks = {c['check']: c for c in report['checks']}
        self.assertEqual(checks['shots[0].transition.initial']['status'], 'pass')
        self.assertEqual(checks['frozen_production_plan']['status'], 'not_checked')

    def test_independent_voice_failures_are_collected_for_every_speaker(self):
        raw, _, _ = complete_source_input()
        raw['project']['prevention_evidence']['source_decisions']['uses'] = [
            {'id': 'a', 'actor': '甲', 'text': '明日再议。'},
            {'id': 'b', 'actor': '乙', 'text': '明日再议。'}]
        voice = voice_spec()
        raw['shots'][0]['actions'] = [
            {'kind': 'speak', 'source_use_id': 'a', 'voice': {**voice, 'baseline': ''}},
            {'kind': 'speak', 'source_use_id': 'b', 'voice': {**voice, 'baseline': ''}}]
        report = inspect_production_editorial(raw)
        failed = {c['check'] for c in report['checks'] if c['status'] == 'fail'}
        self.assertIn('shots[0].actions[0].voice', failed)
        self.assertIn('shots[0].actions[1].voice', failed)
        for action in raw['shots'][0]['actions']:
            action['voice'] = copy.deepcopy(voice)
        self.assertEqual(inspect_production_editorial(raw)['findings'], [])

    def test_empty_declaration_is_seen_even_when_plan_context_is_stale(self):
        raw, graph, texts = complete_source_input()
        graph = json.loads(with_plan(raw, graph, texts))
        graph['production_plan']['context']['rule_registry_sha256'] = '0' * 64
        graph['production_plan']['shots'][0]['design']['performance'][0]['stimulus'] = ''
        report = self.inspect(raw, json.dumps(graph).encode())
        checks = {c['check']: c for c in report['checks']}
        self.assertEqual(checks['source_compilation']['status'], 'fail')
        performance = checks['performance_input_declarations']
        self.assertEqual(performance['status'], 'fail')
        self.assertEqual(performance['basis'], 'unverified_v3_plan_shape')
        self.assertIn('stimulus', str(performance['findings']))
        self.assertEqual(checks['frozen_production_plan']['status'], 'not_checked')

    def test_blocking_reason_cannot_hide_a_real_failure(self):
        collection = _Collection()
        collection.add('a', [{'path': 'x', 'message': 'real error'}], reason='other dependency unavailable')
        self.assertEqual(collection.checks[0]['status'], 'fail')
        self.assertEqual(len(collection.issues), 1)

    def test_dependent_checks_name_missing_prerequisites(self):
        collection = CheckCollection()
        collection.run('source-dependent', lambda: self.fail('must not run'), requires=('source',))
        self.assertEqual(collection.checks[0]['status'], 'not_checked')
        self.assertEqual(collection.checks[0]['blocked_by'], ['source'])
        self.assertIn('source', collection.checks[0]['reason'])


if __name__ == '__main__':
    unittest.main()
