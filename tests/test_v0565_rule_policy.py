"""New reviewed policies cannot waive unreviewed registry changes."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/shortdrama-storyboard/scripts'
sys.path.insert(0, str(SCRIPTS))
import rule_registry


class ProductionGroundingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((SCRIPTS.parent / 'references/rule_registry.json').read_text())

    def test_reviewed_contract_is_valid_and_does_not_grant_semantic_pass(self):
        self.assertEqual(rule_registry.check_registry_definition(self.registry), [])
        p = self.registry['runtime']['production_prevention_policy']['performance_evidence_policy']
        self.assertEqual(p['semantic_status'], 'unknown')
        self.assertTrue(p['independent_semantic_audit_required'])
        self.assertNotEqual(p['field'], p['forbidden_author_input_path'])

    def test_wrong_migration_identity_or_policy_hash_blocks_publication(self):
        bad = copy.deepcopy(self.registry)
        bad['historical_rule_migrations']['production-grounding-0565']['basis'] = 'unreviewed'
        self.assertTrue(any('critical runtime policy changed without migration' in x
                            for x in rule_registry.check_registry_definition(bad)))
        bad = copy.deepcopy(self.registry)
        bad['runtime']['field_content_policy']['voice_subfields']['基础声线']['requires'] = 'anything'
        self.assertTrue(any('critical runtime policy changed without migration' in x
                            for x in rule_registry.check_registry_definition(bad)))
