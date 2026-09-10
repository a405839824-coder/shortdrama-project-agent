"""Actual entry, dependency invalidation, and complete-check regressions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_v0527_production_decisions as p
import test_v0527_source_decisions as s
from test_v0527_authoring_serializer import decisions
import author_storyboard as author
from authoring_preflight import inspect_typed_decisions
import production_derivation as d
from source_action_plans import construct_source_action_plans, source_step


def fixture():
    # Reuse the already explicit public eye/timing decision set. The old
    # hold-only core fixture is still tested as incomplete at the new entry.
    value = p.performance_fixture()
    text = '甲在书房站立等候。'
    fact = s.fact('F1', text, channel='action', content='', availability='not_applicable', occurrence='occurred')
    graph = {'contract': 'source-facts-v1', 'sources': {'DOC': hashlib.sha256(text.encode()).hexdigest()}, 'facts': [fact]}
    uses = {'contract': 'source-decisions-v1', 'uses': [{'id': 'U1', 'fact_ids': ['F1'], 'mode': 'physical_action',
            'wording': 'authored', 'actor': '甲', 'text': '甲站立等候。'}], 'written_objects': []}
    ev = value['project']['prevention_evidence']
    ev['source_ledger'] = [{'source_id': 'DOC', 'source_text': text}]
    ev['source_decisions'] = uses
    value['shots'][0]['source_unit_ids'] = ['DOC']
    hold = p.action('hold', id='hold1', source_use_id='U1')
    graph['action_plans'] = construct_source_action_plans(
        initial_world=value['initial_world'],
        events=[{'source_use_id': 'U1', 'steps': [
            {'step_id': 'hold1', 'kind': 'hold', 'parameters': {}, 'after': []}]}])
    value['shots'][0]['actions'].append(source_step(
        action_id=hold['id'], source_use_id=hold['source_use_id'], step_id='hold1',
        start=hold['start'], end=hold['end'], after=hold['after']))
    return value, graph


class AuthoringPreventionTests(unittest.TestCase):
    def record(self, value=None, graph=None):
        if value is None: value, graph = fixture()
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / 'input.json'; source.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        graph_path = root / 'graph.json'
        if graph is not None: graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding='utf-8')
        report = author.record_attempt(source, root / 'attempts', attempt_id='control',
                                       source_graph_path=graph_path if graph is not None else None)
        return report, root / 'attempts/control'

    def canonical(self):
        report, folder = self.record()
        self.assertEqual(report['status'], 'rendered_pending_preflight', report['findings'])
        return json.loads((folder / 'canonical.json').read_text(encoding='utf-8'))

    def test_actual_entry_freezes_inputs_and_reproducible_binding(self):
        report, folder = self.record()
        self.assertEqual(report['status'], 'rendered_pending_preflight', report['findings'])
        canonical = json.loads((folder / 'canonical.json').read_text(encoding='utf-8'))
        checked = d.inspect_derivation(canonical)
        self.assertTrue(checked['valid'], checked)
        self.assertEqual(checked['source_obligations'][0]['field_evidence_ids'], ['field:EP90-001.画面内容'])
        self.assertTrue((folder / 'source_graph.original.json').is_file())
        self.assertTrue((folder / 'check_coverage.json').is_file())
        self.assertFalse(report['commit_eligible']); self.assertIsNone(report['semantic_accuracy_percent'])

    def test_missing_graph_keeps_failed_attempt_and_has_no_canonical(self):
        value, _ = fixture(); report, folder = self.record(value, None)
        self.assertEqual(report['status'], 'rejected')
        self.assertTrue((folder / 'input.original.json').is_file())
        self.assertFalse((folder / 'canonical.json').exists())
        self.assertTrue(any(x['path'] == 'source_graph_path' for x in report['findings']))

    def test_service_preparation_preserves_exact_recorded_candidate_and_binding(self):
        from shortdrama_mcp.service import _prepare_compact_storyboard_for_commit
        from shortdrama_mcp.canonical import assert_compact_storyboard_contract
        canonical = self.canonical()
        assert_compact_storyboard_contract(canonical)
        prepared, _ = _prepare_compact_storyboard_for_commit(canonical)
        self.assertEqual(prepared, canonical)
        self.assertTrue(d.inspect_derivation(prepared)['valid'])
        self.assertTrue(prepared['shots'][0]['scene_uid'].startswith('SCENE-'))
        self.assertNotIn('derived', canonical['prevention_evidence'][d.FIELD])

    def test_actual_state_edit_invalidates_old_evidence_in_full_validator(self):
        from validate_storyboard import validate_project
        canonical = self.canonical()
        canonical['prevention_evidence']['scene_state_ledger'][0]['end_state']['人物位置']['甲']['position_xyz'][0] += 1
        inspected = d.inspect_derivation(canonical)
        self.assertFalse(inspected['valid'])
        report = validate_project(canonical, mode='audit')
        self.assertTrue(any(d.FIELD in x.get('location', '') for x in report['issues']), report['issues'])

    def test_refreshed_content_checksum_cannot_avoid_recomputation(self):
        canonical = self.canonical()
        canonical['shots'][0]['画面内容'] += '甲已走到门外。'
        binding = canonical['prevention_evidence'][d.FIELD]
        binding['content_sha256'] = d.digest(d.owned_content(canonical))
        binding['binding_sha256'] = d.digest({k: v for k, v in binding.items() if k != 'binding_sha256'})
        checked = d.inspect_derivation(canonical)
        self.assertFalse(checked['valid'])
        self.assertTrue(any('recomputed' in x['message'] for x in checked['faults']), checked)

    def test_compiler_change_requires_new_evidence(self):
        canonical = self.canonical(); actual = d.compiler_fingerprints()
        with mock.patch.object(d, 'compiler_fingerprints', return_value={**actual, 'source_decisions.py': '0' * 64}):
            checked = d.inspect_derivation(canonical)
        self.assertFalse(checked['valid'])
        self.assertTrue(any('stale' in x['message'] for x in checked['faults']))

    def test_review_attachment_does_not_rewrite_canonical_pending(self):
        canonical = self.canonical()
        canonical['prevention_evidence']['independent_review_attachment'] = {'reviewer_note': 'content review pending'}
        self.assertTrue(d.inspect_derivation(canonical)['valid'])
        canonical['prevention_evidence'][d.FIELD]['independent_audit_status'] = 'PASS'
        self.assertFalse(d.inspect_derivation(canonical)['valid'])

    def test_deleted_binding_marker_is_detected_and_legacy_is_unaffected(self):
        canonical = self.canonical(); del canonical['prevention_evidence'][d.FIELD]
        self.assertFalse(d.inspect_derivation(canonical)['valid'])
        self.assertTrue(d.inspect_derivation(canonical)['present'])
        del canonical['prevention_evidence']['authoring_evidence']['production_binding_required']
        self.assertTrue(d.inspect_derivation(canonical)['present'])
        self.assertFalse(d.inspect_derivation(canonical)['valid'])
        self.assertFalse(d.inspect_derivation(author._render_decisions(decisions()))['present'])

    def test_all_independent_shots_checked_when_first_duration_invalid(self):
        value = decisions(); row = copy.deepcopy(value['shots'][0]); row['shot_id'] = 'EP33-002'
        value['shots'][0]['duration'] = True; row['start_state'] = {}
        value['shots'].append(row)
        checked = inspect_typed_decisions(value)
        paths = {item['path'] for item in checked['findings']}
        self.assertIn('shots[0].duration', paths)
        self.assertIn('shots[1].start_state', paths)
        self.assertTrue(any(x['check'] == 'complete_timing' for x in checked['not_checked']))

    def test_paper_and_voice_errors_are_collected_in_one_actual_attempt(self):
        value, graph = fixture()
        graph['facts'][0].update(channel='written', expression_status='asserted', expression_occurrence='future',
                                 content_availability='given', content_policy='verbatim', content_text='明日再议。')
        ev = value['project']['prevention_evidence']
        ev['source_decisions']['uses'][0].update(mode='current_written', wording='verbatim', text='明日再议。')
        ev['source_decisions']['written_objects'] = [{'id': 'PAPER', 'use_id': 'U1', 'carrier_id': '信纸', 'at': 'frame0', 'state': 'current'}]
        value['initial_world']['props']['信纸']['written_ref'] = 'PAPER'
        value['shots'][0]['actions'] = [p.action('speak', source_use_id='U1', voice={})]
        report, folder = self.record(value, graph)
        self.assertEqual(report['status'], 'rejected')
        self.assertTrue(any(x.get('code') == 'written_scope' for x in report['findings']), report)
        self.assertTrue(any('voice' in x.get('check', '') for x in report['findings']), report)
        self.assertFalse(any('unknown compiled paper' in x['message'] for x in report['findings']), report)
        coverage = json.loads((folder / 'check_coverage.json').read_text(encoding='utf-8'))
        self.assertTrue(any(x['check'] == 'shots[0].camera' and x['status'] == 'pass' for x in coverage['checks']))
        self.assertTrue(any(x['check'] == 'source_references' for x in coverage['not_checked']))
        self.assertFalse((folder / 'canonical.json').exists())


if __name__ == '__main__': unittest.main()
