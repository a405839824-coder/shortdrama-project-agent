"""Replay-backed applicability controls, without manuscript-specific waivers."""
import copy
import json
import unittest
from test_v0566_surface_voice import fixture
from test_v0565_performance_evidence import build
from production_derivation import inspect_derivation
from validate_storyboard import validate_project, _validate_adapted_dialogue_audit, detail_categories


class VoiceEvidenceTests(unittest.TestCase):
    def test_actual_breath_phases_are_recognized_as_breath_details(self):
        for phrase in ('甲自然吸气。', '甲自然呼气。', '甲轻轻叹气。'):
            self.assertIn('breath', detail_categories(phrase))

    def test_unbodied_voice_has_source_and_audio_but_no_invented_body(self):
        data, graph, texts = fixture()
        canonical, _, _ = build(data, json.dumps(graph, ensure_ascii=False).encode(), texts)
        binding = inspect_derivation(canonical)
        self.assertTrue(binding['valid'], binding['faults'])
        self.assertEqual(binding['disembodied_speakers'], {'EP90-001': ['古镜残音']})
        actor = next(a for a in binding['performance_evidence']['shots'][0]['actors'] if a['actor'] == '古镜残音')
        self.assertEqual(actor['fields']['stimulus']['evidence_status'], 'bound')
        self.assertEqual(actor['fields']['listen_or_observe_reaction']['evidence_status'], 'bound')
        for name in ('gaze_path', 'micro_expression', 'ecological_action', 'body_or_breath', 'end_state'):
            self.assertEqual(actor['fields'][name]['evidence_status'], 'not_applicable')
        issues = validate_project(canonical)['issues']
        self.assertFalse(any('可见对白未写' in i['evidence'] or ('重放表演证据仍未知' in i['evidence'] and '古镜残音.' in i['evidence']) for i in issues), issues)
        self.assertTrue(any('甲.gaze_path' in i['evidence'] for i in issues))
        changed = copy.deepcopy(canonical)
        changed['shots'][0]['画面内容'] += '伪造旁白。'
        self.assertFalse(inspect_derivation(changed)['valid'])

    def test_communication_mapping_requires_exact_support_target_and_receipt(self):
        data, graph, texts = fixture()
        data['project']['prevention_evidence']['source_ledger'][0]['target_shots'] = ['EP90-001']
        canonical, _, _ = build(data, json.dumps(graph, ensure_ascii=False).encode(), texts)
        binding = inspect_derivation(canonical)
        records = binding['source_communication']
        self.assertTrue(records and all(r['receipt_ids'] for r in records))
        def check(rows, verified=True):
            return _validate_adapted_dialogue_audit(canonical, canonical['shots'], {},
                production_verified=verified, communication_bindings=rows)
        self.assertEqual(check(records), [])
        self.assertTrue(check([], verified=False))
        for field in ('source_text', 'shot_id'):
            wrong = copy.deepcopy(records)
            for row in wrong: row[field] = 'unrelated'
            self.assertTrue(check(wrong))


if __name__ == '__main__': unittest.main()
