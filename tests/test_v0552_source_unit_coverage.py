"""Source-unit review transport; synthetic verdicts never prove semantics."""
import copy
import hashlib
import json
import unittest
from types import SimpleNamespace

from shortdrama_mcp.audit_coverage import build_obligations, read_audit_obligations, validate_coverage
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES, SemanticAuditCoverageInput
from semantic_audit_helpers import synthetic_coverage


SOURCE = '孟怀澈推开妹妹，带辛三出门。'
CONTEXT = {key: 'f' * 64 for key in ('rules', 'schema', 'validator', 'runtime')}


def compound_source():
    shots = [{'镜头编号': f'EP90-{index:03d}', 'shot_uid': f'UID-{index}',
              '画面内容': text, '原文支撑句': '孟怀澈推开妹妹。',
              '心理音轨': '无。', '起始状态': '人物站在门内。', '环境音效': '无。',
              '时长': 3, '累计时长': 3 * index}
             for index, text in enumerate(('孟怀澈推开孟佳宜。', '孟佳宜在门旁站定。',
                                           '孟怀澈带辛三走出房门。'), 1)]
    graph = {'sources': {'P937': hashlib.sha256(SOURCE.encode()).hexdigest()},
             'facts': [{'id': 'F937', 'supports': [{'source_id': 'P937', 'start': 0,
                                                 'end': len(SOURCE), 'text': SOURCE}]}]}
    return {'shots': shots, 'prevention_evidence': {
        'source_ledger': [{'source_id': 'P937', 'source_text': SOURCE,
                           'target_shots': ['UID-1'], 'treatment': '保留', 'authorization': '原文保留'}],
        'source_decisions': {'uses': [{'id': 'U937', 'fact_ids': ['F937']}], 'written_objects': []},
        'production_derivation': {'source_graph_json': json.dumps(graph, ensure_ascii=False),
            'action_trace': {'shots': [{'shot_id': sid, 'actions': [{'source_use_id': 'U937'}]}
                                      for sid in ('EP90-001', 'EP90-003')]}}}}


class SourceUnitCoverageTests(unittest.TestCase):
    def setUp(self):
        self.candidate = compound_source()
        self.manifest = build_obligations(self.candidate, CONTEXT)
        self.submission = {'coverage': synthetic_coverage(self.manifest), 'issues': [],
            'layers': [{'layer': layer, 'status': 'pass'} for layer in SEMANTIC_AUDIT_LAYER_NAMES]}

    def source_check(self, submission=None):
        return next(row for row in (submission or self.submission)['coverage']['checks']
                    if row['obligation_id'] == 'EP90-001/source_fidelity')

    def test_full_unit_is_independent_of_abbreviated_support_and_fact_granularity(self):
        row = next(r for r in self.manifest['obligations'] if r['obligation_id'] == 'EP90-001/source_fidelity')
        self.assertEqual(row['source_evidence'][0]['text'], SOURCE)
        self.assertEqual(row['source_evidence'][0]['related_shot_ids'], ['EP90-001', 'EP90-003'])
        self.assertIn('field:EP90-003.画面内容', row['required_observation_refs'])
        self.assertEqual(validate_coverage(self.candidate, self.submission, CONTEXT)['failed_count'], 0)

    def test_missing_or_partial_source_quote_cannot_pass(self):
        for mode in ('missing', 'prefix'):
            value = copy.deepcopy(self.submission)
            check = self.source_check(value)
            source = next(r for r in check['observations'] if r['reference'].startswith('source-unit:'))
            if mode == 'missing': check['observations'].remove(source)
            else: source['quote'] = SOURCE[:8]
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'observation_missing|source_unit_quote_incomplete'):
                validate_coverage(self.candidate, value, CONTEXT)

    def test_omitting_nonadjacent_second_action_observation_cannot_pass(self):
        value = copy.deepcopy(self.submission)
        check = self.source_check(value)
        check['observations'] = [r for r in check['observations'] if r['reference'] != 'field:EP90-003.画面内容']
        with self.assertRaisesRegex(ValueError, 'observation_missing'):
            validate_coverage(self.candidate, value, CONTEXT)

    def test_missing_raw_text_or_changed_frozen_unit_is_blocked(self):
        for text in (None, '', SOURCE[:8]):
            data = copy.deepcopy(self.candidate)
            data['prevention_evidence']['source_ledger'][0]['source_text'] = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'source_unit_(invalid|hash_mismatch)'):
                build_obligations(data, CONTEXT)

    def test_compact_pages_deliver_complete_source_and_cross_shot_references(self):
        service = SimpleNamespace(_storyboard_from_artifact=lambda *a, **k: (self.candidate, {}),
            _ruleset_fingerprint=CONTEXT['rules'], _validator_fingerprint=CONTEXT['validator'],
            _runtime_fingerprint=CONTEXT['runtime'], _schema_fingerprint=CONTEXT['schema'])
        response = read_audit_obligations(service, 'test', 'PROJECT', 'ART', limit=1, max_chars=64000)
        row = response.data['obligations'][0]
        self.assertEqual(row['source_evidence'][0]['text'], SOURCE)
        self.assertIn('source-unit:P937:00000', row['additional_observation_refs'])
        self.assertIn('field:EP90-003.画面内容', row['additional_observation_refs'])
        self.assertIn('field:EP90-003.画面内容', row['additional_dependency_refs'])
        self.assertFalse(response.data['semantic_truth_guaranteed'])

    def test_full_source_is_chunked_without_losing_suffix(self):
        data = copy.deepcopy(self.candidate)
        text = SOURCE * 160
        data['prevention_evidence']['source_ledger'][0]['source_text'] = text
        graph = json.loads(data['prevention_evidence']['production_derivation']['source_graph_json'])
        graph['sources']['P937'] = hashlib.sha256(text.encode()).hexdigest()
        data['prevention_evidence']['production_derivation']['source_graph_json'] = json.dumps(graph)
        manifest = build_obligations(data, CONTEXT)
        row = next(r for r in manifest['obligations'] if r['rule_id'] == 'source_fidelity')
        self.assertEqual(''.join(part['text'] for part in row['source_evidence']), text)
        self.assertTrue(all(len(part['text']) <= 2000 for part in row['source_evidence']))
        self.assertTrue(all(part['reference'] in row['required_observation_refs'] for part in row['source_evidence']))

    def test_persistent_paper_binds_actual_written_delivery_without_unrelated_fields(self):
        data = copy.deepcopy(self.candidate)
        data['shots'] = [{**copy.deepcopy(data['shots'][0]), '镜头编号': f'EP90-{i:03d}', 'shot_uid': f'UID-{i}'}
                         for i in range(1, 26)]
        evidence = data['prevention_evidence']
        evidence['source_decisions']['written_objects'] = [{'id': 'PAPER', 'use_id': 'U937'}]
        evidence['production_derivation']['action_trace']['shots'] = [
            {'shot_id': shot['镜头编号'], 'actions': [], 'visible_current_paper_refs': ['PAPER']}
            for shot in data['shots']]
        manifest = build_obligations(data, CONTEXT)
        row = next(r for r in manifest['obligations'] if r['rule_id'] == 'source_fidelity')
        self.assertIn('field:EP90-025.起始状态', row['required_observation_refs'])
        self.assertNotIn('field:EP90-025.环境音效', row['required_observation_refs'])
        self.assertNotIn('field:EP90-025.心理音轨', row['required_observation_refs'])
        SemanticAuditCoverageInput.model_validate(synthetic_coverage(manifest))

    def test_unfinished_second_action_is_an_independent_failure_not_automatic_pass(self):
        data = copy.deepcopy(self.candidate)
        data['shots'][2]['画面内容'] = '孟怀澈与辛三仍站在门内。'
        value = {**self.submission, 'coverage': synthetic_coverage(build_obligations(data, CONTEXT)),
                 'layers': copy.deepcopy(self.submission['layers'])}
        check = self.source_check(value)
        check.update(status='fail_current', issue_ids=['MISSING-EXIT'],
                     judgement='合成审计反例：只看到推开，尚无带辛三出门的实际交付，整句未完成。')
        value['layers'][0]['status'] = 'fail_current'
        value['issues'] = [{'issue_id': 'MISSING-EXIT', 'layer': '源文对白', 'shot_ids': ['EP90-001']}]
        self.assertEqual(validate_coverage(data, value, CONTEXT)['failed_count'], 1)

    def test_181_shot_unit_distributes_observations_without_losing_any_delivery(self):
        data = copy.deepcopy(self.candidate)
        data['shots'] = [{**copy.deepcopy(data['shots'][0]), '镜头编号': f'EP90-{i:03d}', 'shot_uid': f'UID-{i}'}
                         for i in range(1, 182)]
        data['prevention_evidence']['production_derivation']['action_trace']['shots'] = [
            {'shot_id': shot['镜头编号'], 'actions': [{'source_use_id': 'U937'}]} for shot in data['shots']]
        manifest = build_obligations(data, CONTEXT)
        checks = [r for r in manifest['obligations'] if r['rule_id'] == 'source_fidelity']
        self.assertEqual(checks[0]['source_evidence'][0]['review_scope'], 'distributed_across_related_shots')
        self.assertEqual(len(checks[0]['source_evidence'][0]['related_shot_ids']), 181)
        self.assertNotIn('related_shot_ids', checks[-1]['source_evidence'][0])
        observed = {ref for row in checks for ref in row['required_observation_refs']}
        for shot in data['shots']:
            self.assertIn(f"field:{shot['镜头编号']}.画面内容", observed)
        self.assertEqual(sum(len(row['required_observation_refs']) for row in checks), 181 * 3)
        SemanticAuditCoverageInput.model_validate(synthetic_coverage(manifest))

    def test_excessive_source_evidence_blocks_instead_of_truncating(self):
        for count, error in ((4200, 'item_exceeds_transport'), (10000, 'source_unit_transport_exceeded')):
            data = copy.deepcopy(self.candidate)
            text = SOURCE * count
            data['prevention_evidence']['source_ledger'][0]['source_text'] = text
            graph = json.loads(data['prevention_evidence']['production_derivation']['source_graph_json'])
            graph['sources']['P937'] = hashlib.sha256(text.encode()).hexdigest()
            data['prevention_evidence']['production_derivation']['source_graph_json'] = json.dumps(graph)
            service = SimpleNamespace(_storyboard_from_artifact=lambda *a, **k: (data, {}),
                _ruleset_fingerprint=CONTEXT['rules'], _validator_fingerprint=CONTEXT['validator'],
                _runtime_fingerprint=CONTEXT['runtime'], _schema_fingerprint=CONTEXT['schema'])
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, error):
                read_audit_obligations(service, 'test', 'PROJECT', 'ART', limit=1, max_chars=64000)


if __name__ == '__main__':
    unittest.main()
