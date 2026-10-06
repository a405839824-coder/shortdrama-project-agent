"""Compact read transport equivalence, not an independent story review."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from shortdrama_mcp.audit_coverage import (
    build_obligations, coverage_context, read_audit_obligations, validate_coverage,
)
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shortdrama-storyboard/scripts'))
from test_strict_prompt_contract import strict_fixture


def expand_refs(row, rule):
    dependencies = [f"field:{row['shot_id']}.{field}" for field in rule['dependency_fields']]
    dependencies.extend(f'field:{sid}.{field}' for sid in row.get('neighbor_shot_ids', [])
                        for field in rule['neighbor_dependency_fields'])
    observations = [f"field:{row['shot_id']}.{field}" for field in rule['required_observation_fields']]
    for key in ('planning_evidence', 'rhythm_evidence'):
        if key in row:
            dependencies.append(row[key]['reference'])
            observations.append(row[key]['reference'])
    dependencies.extend(row.get('additional_dependency_refs', []))
    observations.extend(row.get('additional_observation_refs', []))
    return list(dict.fromkeys(dependencies)), list(dict.fromkeys(observations))


class ObligationReadTests(unittest.TestCase):
    def setUp(self):
        self.storyboard = strict_fixture()
        first = self.storyboard['shots'][0]
        self.storyboard['shots'] = []
        for index in range(181):
            shot = copy.deepcopy(first)
            shot['镜头编号'] = f'EP90-{index + 1:03d}'
            self.storyboard['shots'].append(shot)
        # Each cloned synthetic occurrence retains its own complete source unit.
        self.storyboard['prevention_evidence']['source_ledger'] = [
            {'source_id': f'fixture-{index}', 'source_text': shot['原文支撑句'],
             'target_shots': [shot['镜头编号']], 'treatment': '保留', 'authorization': '合成分页验收'}
            for index, shot in enumerate(self.storyboard['shots'])]
        self.service = SimpleNamespace(
            _ruleset_fingerprint='r' * 64, _validator_fingerprint='v' * 64,
            _runtime_fingerprint='t' * 64, _schema_fingerprint='s' * 64,
            _storyboard_from_artifact=lambda *a, **k: (self.storyboard, None),
        )

    def read(self, **kwargs):
        return read_audit_obligations(self.service, 'test-namespace', 'project', 'artifact', **kwargs)

    def test_181_shots_default_budget_complete_view_matches_full_manifest(self):
        manifest = build_obligations(self.storyboard, coverage_context(self.service))
        first = self.read(limit=50, max_chars=16_000)
        definitions = first.data['rule_definitions']
        definition_hash = first.data['rule_definitions_sha256']
        rows = list(first.data['obligations'])
        pages = 1
        current = first
        while current.data['next_offset'] is not None:
            request = current.data['next_call']
            self.assertEqual(request['offset'], current.data['next_offset'])
            current = self.read(offset=request['offset'], limit=request['limit'], max_chars=request['max_chars'])
            self.assertNotIn('rule_definitions', current.data)
            self.assertNotIn('submission_schema', current.data)
            self.assertNotIn('page_submission_schema', current.data)
            self.assertEqual(current.data['definition_reference']['offset'], 0)
            self.assertEqual(current.data['manifest_sha256'], manifest['manifest_sha256'])
            self.assertEqual(current.data['rule_definitions_sha256'], definition_hash)
            self.assertLessEqual(len(current.model_dump_json()), 16_000)
            rows.extend(current.data['obligations'])
            pages += 1
        self.assertTrue(current.data['complete'])
        self.assertIsNone(current.data['next_call'])
        self.assertEqual(len(rows), 181 * 14)
        self.assertIn('scene_causality', definitions)
        # Keep the 16k page budget and every literal reference. The mandatory
        # six-field review on both sides of state/sound boundaries adds 3 pages.
        self.assertLessEqual(pages, 56)
        self.assertEqual(len({r['obligation_id'] for r in rows}), len(rows))
        checks = []
        for row, expected in zip(rows, manifest['obligations']):
            rule = definitions[row['rule_id']]
            self.assertEqual(row['obligation_id'], expected['obligation_id'])
            self.assertEqual(row['dependency_sha256'], expected['dependency_sha256'])
            refs, observations = expand_refs(row, rule)
            self.assertEqual(refs, list(expected['dependency_fields']))
            self.assertEqual(observations, expected['required_observation_refs'])
            # Synthetic evidence only: proves compact transport loses no refs.
            checks.append({'obligation_id': row['obligation_id'], 'dependency_sha256': row['dependency_sha256'],
                'status': 'pass', 'judgement': 'Synthetic transport equivalence check; not actual content acceptance.',
                'observations': [{'reference': ref, 'quote': manifest['_values'][ref] if ref.startswith('source-unit:')
                                  else manifest['_values'][ref][:24]} for ref in observations],
                'issue_ids': []})
        receipt = validate_coverage(self.storyboard, {
            'coverage': {'contract': manifest['contract'], 'manifest_sha256': manifest['manifest_sha256'], 'checks': checks},
            'layers': [{'layer': name, 'status': 'pass'} for name in SEMANTIC_AUDIT_LAYER_NAMES],
            'issues': [],
        }, coverage_context(self.service))
        self.assertEqual(receipt['reviewed_count'], 2534)

    def test_nonadjacent_causal_dependencies_and_observations_survive_read_view(self):
        self.storyboard['shots'] = self.storyboard['shots'][:3]
        ids = [s['镜头编号'] for s in self.storyboard['shots']]
        graph = {'production_plan': {'shots': [
            {'shot_id': sid, 'design': {}, 'purpose_fact_ids': [fact], 'source_use_ids': []}
            for sid, fact in zip(ids, ('cause', 'unrelated', 'effect'))],
            'narrative': {'segments': [{'shot_ids': [sid]} for sid in ids],
                'prior_deliveries': [], 'causal_links': [{'fact_id': 'effect',
                    'requires_fact_id': 'cause', 'establishment_shot_id': ids[0],
                    'reveal_shot_id': ids[2], 'prior_delivery_id': None}]}}}
        ledger = self.storyboard['prevention_evidence']['source_ledger'][:3]
        graph['sources'] = {row['source_id']: hashlib.sha256(row['source_text'].encode()).hexdigest() for row in ledger}
        self.storyboard['prevention_evidence'] = {'source_ledger': ledger,
            'production_derivation': {'source_graph_json': json.dumps(graph)}}
        manifest = build_obligations(self.storyboard, coverage_context(self.service))
        page = self.read(limit=100, max_chars=64_000)
        self.assertTrue(page.data['complete'])
        for row, full in zip(page.data['obligations'], manifest['obligations']):
            dependencies, observations = expand_refs(row, page.data['rule_definitions'][row['rule_id']])
            self.assertEqual(set(dependencies), set(full['dependency_fields']))
            self.assertEqual(set(observations), set(full['required_observation_refs']))
        causal = next(row for row in page.data['obligations']
                      if row['shot_id'] == ids[0] and row['rule_id'] == 'scene_causality')
        ref = f'field:{ids[2]}.画面内容'
        self.assertIn(ref, causal['additional_dependency_refs'])
        self.assertIn(ref, causal['additional_observation_refs'])

    def test_small_budget_fails_explicitly_and_offsets_never_skip(self):
        with self.assertRaisesRegex(ValueError, 'max_chars_too_small'):
            self.read(max_chars=300)
        with self.assertRaisesRegex(ValueError, 'offset_invalid'):
            self.read(offset=-1)
        with self.assertRaisesRegex(ValueError, 'offset_invalid'):
            self.read(offset=2535)
        end = self.read(offset=2534, max_chars=16_000)
        self.assertTrue(end.data['complete'])
        self.assertEqual(end.data['obligations'], [])

    def test_mutation_changes_manifest_even_when_read_definitions_stay_same(self):
        before = self.read(offset=50)
        self.storyboard['shots'][100]['画面内容'] += '江氏望向门口。'
        after = self.read(offset=50)
        self.assertEqual(before.data['rule_definitions_sha256'], after.data['rule_definitions_sha256'])
        self.assertNotEqual(before.data['manifest_sha256'], after.data['manifest_sha256'])
        self.assertNotEqual(before.data['definition_reference'], after.data['definition_reference'])


if __name__ == '__main__':
    unittest.main()
