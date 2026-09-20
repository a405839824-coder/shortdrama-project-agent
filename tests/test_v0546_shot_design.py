"""Regression cases retained for later authorization; not run for this release."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/shortdrama-storyboard/scripts'))
sys.path.insert(0, str(ROOT / 'server'))
from production_plan import ProductionPlanError, check_duration_design, validate_design
from shortdrama_mcp.audit_coverage import build_obligations
from shortdrama_mcp.materializer import (MOTHER_TEMPLATE_COLUMNS, LEGACY_MOTHER_TEMPLATE_COLUMNS,
    FORMAL_MERGED_ATOMIC_FIELDS, LEGACY_FORMAL_MERGED_ATOMIC_FIELDS, LIGHTING_FIELDS)


class ShotDesignRegression(unittest.TestCase):
    def test_duration_boundaries_and_false_dialogue_exception(self):
        design = {'framing_reason': 'read the source note', 'cut_reason': 'new evidence',
                  'spatial_anchors': {}, 'performance': [], 'duration_exception': None}
        row = {'ticks': [0, 10, 20, 40], 'camera': {'frame': 'object'}, 'actions': []}
        check_duration_design(row, design, 'shot')
        row['ticks'][-1] = 41
        with self.assertRaises(ProductionPlanError): check_duration_design(row, design, 'shot')
        design['duration_exception'] = {'kind': 'information_reading', 'fact_ids': ['F1'],
            'continuous_content': 'source note visible as a whole', 'required_seconds': 7,
            'timing_basis': 'reading estimate', 'measurement': 'estimate',
            'alternative_cuts_seconds': [3.5, 3.5], 'alternative_loss': 'the juxtaposed source words leave the frame',
            'after_four': 'finish reading the same note', 'complexity': 'fixed object shot'}
        row['ticks'][-1] = 70
        validate_design(design, {'F1'}, 'design')
        check_duration_design(row, design, 'shot')
        row['actions'] = [{'kind': 'speak', 'actor': 'A', 'start': 0, 'end': 70}]
        with self.assertRaises(ProductionPlanError): check_duration_design(row, design, 'shot')
        row['actions'] = []; row['ticks'][-1] = 81
        with self.assertRaises(ProductionPlanError): check_duration_design(row, design, 'shot')
        broken = copy.deepcopy(design); broken['duration_exception']['alternative_cuts_seconds'] = [3, 3]
        with self.assertRaises(ProductionPlanError): validate_design(broken, {'F1'}, 'design')

    def test_layout_and_required_design_evidence(self):
        self.assertEqual(MOTHER_TEMPLATE_COLUMNS[:-1], LEGACY_MOTHER_TEMPLATE_COLUMNS)
        self.assertEqual(MOTHER_TEMPLATE_COLUMNS[-1], '光影')
        self.assertFalse(set(LIGHTING_FIELDS) & set(FORMAL_MERGED_ATOMIC_FIELDS))
        self.assertTrue(set(LIGHTING_FIELDS) <= set(LEGACY_FORMAL_MERGED_ATOMIC_FIELDS))
        import json
        graph = {'production_plan': {'shots': [{'shot_id': 'EP01-001', 'design': {
            'framing_reason': 'visible face', 'cut_reason': 'new statement', 'spatial_anchors': {'A': 'table'},
            'performance': [], 'duration_exception': None}}]}}
        storyboard = {'shots': [{'镜头编号': 'EP01-001'}], 'prevention_evidence': {
            'production_derivation': {'source_graph_json': json.dumps(graph)}}}
        manifest = build_obligations(storyboard, {})
        timing = next(r for r in manifest['obligations'] if r['rule_id'] == 'timing_capacity')
        ref = timing['planning_evidence']['reference']
        self.assertIn(ref, timing['required_observation_refs'])
        self.assertIn(ref, timing['dependency_fields'])
        self.assertEqual(manifest['_values'][ref], timing['planning_evidence']['text'])


if __name__ == '__main__':
    unittest.main()
