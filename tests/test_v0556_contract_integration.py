import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shortdrama-storyboard/scripts'))
from rule_registry import check_registry_definition, load_registry, validate_registry
from jsonschema import Draft202012Validator


class CurrentContractTests(unittest.TestCase):
    def test_current_registry_and_views_agree(self):
        self.assertEqual(validate_registry(), [])

    def test_asr_evidence_cannot_be_relabelled_or_become_global_default(self):
        for mutate in (
            lambda r, p: p.update(evidence_status='measured'),
            lambda r, p: p['evidence'].update(reviewed=True),
            lambda r, p: p.update(candidate_range=[6.9, 5.8]),
            lambda r, p: p.update(reference_range_kind='confidence_interval'),
            lambda r, p: p['applicability'].update(channels=['dialogue', 'psychological']),
            lambda r, p: r['runtime']['timing_policy'].update(default_profile_id=p['id']),
        ):
            registry = copy.deepcopy(load_registry())
            profile = next(p for p in registry['runtime']['timing_policy']['profiles']
                           if p['id'] == 'provisional-zh-continuous-explanation-v1')
            mutate(registry, profile)
            self.assertTrue(check_registry_definition(registry))

    def test_audit_cell_schema_keeps_real_addresses_and_digest(self):
        schema = json.loads((ROOT / 'skills/shortdrama-storyboard/references/storyboard_schema.json').read_text(encoding='utf-8'))
        validator = Draft202012Validator(schema['properties']['audit_workbook_evidence'])
        evidence = {'contract': 'external-workbook-audit-v1', 'raw_sha256': 'a'*64,
                    'master_sheet': 'EP35_正式分镜母版', 'column_contract': 10,
                    'rows': [{'shot_id': 'EP35-001', 'row': 2,
                              'cells': {'正式分镜母版': {'address': 'E2', 'value': '起始状态：甲坐稳。', 'sha256': 'b'*64}}}]}
        self.assertEqual(list(validator.iter_errors(evidence)), [])
        altered = copy.deepcopy(evidence)
        altered['rows'][0]['cells']['正式分镜母版']['address'] = 'invented'
        self.assertTrue(list(validator.iter_errors(altered)))
        altered = copy.deepcopy(evidence)
        altered['rows'][0]['cells']['正式分镜母版']['sha256'] = 'unknown'
        self.assertTrue(list(validator.iter_errors(altered)))

    def test_timing_selection_schema_disallows_self_reported_speed(self):
        schema = json.loads((ROOT / 'skills/shortdrama-storyboard/references/storyboard_schema.json').read_text(encoding='utf-8'))
        validator = Draft202012Validator(schema['$defs']['shot_timing']['properties']['utterances']['items']['properties']['selection_evidence'])
        evidence = {'speaker_category': 'adult_inferred', 'delivery_class': 'continuous_explanation',
                    'source_use_id': 'use-1', 'basis_fact_ids': ['fact-1'], 'rationale': '来源与话语目的待独审'}
        self.assertEqual(list(validator.iter_errors(evidence)), [])
        evidence['units_per_second'] = 9
        self.assertTrue(list(validator.iter_errors(evidence)))


if __name__ == '__main__':
    unittest.main()
