"""The current eye contract cannot be silently restored to action quotas."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/shortdrama-storyboard/scripts'))
from rule_registry import check_registry_definition, load_registry, validate_registry


class EyePolicyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_current_event_policy_and_all_generated_views_agree(self):
        self.assertEqual(validate_registry(), [])
        policy = self.registry['runtime']['blink_policy']
        self.assertEqual(policy['default_check_basis'], 'declared_event_consistency')
        self.assertIsNone(policy['mandatory_when_face_visible_seconds_at_least'])
        self.assertEqual(policy['required_timed_chain'], [])
        self.assertEqual(policy['generation_limit_required'], [])
        self.assertFalse(policy['legacy_replay']['applies_to_current_generation'])
        self.assertIn('0-2次', policy['over_five_to_ten_seconds'])

    def test_original_baseline_keeps_old_contract_and_explicit_migration_is_required(self):
        baseline = json.loads((ROOT/'skills/shortdrama-storyboard/references/historical_rule_baseline.json').read_text(encoding='utf-8'))
        self.assertIn('必须写视线起点', baseline['atomic_semantic_definitions']['AR-R10-EXPR-003']['rule_full_text'])
        del self.registry['historical_rule_migrations']
        faults = check_registry_definition(self.registry)
        self.assertTrue(any('AR-R10-EXPR-003' in f for f in faults))
        self.assertTrue(any('E-EXPRESSION-003' in f for f in faults))

    def test_changed_current_or_baseline_hash_cannot_use_migration(self):
        for change in ('rule', 'error', 'baseline_hash', 'current_hash'):
            registry = copy.deepcopy(self.registry)
            if change == 'rule':
                next(r for r in registry['atomic_rules'] if r['rule_id']=='AR-R10-EXPR-003')['rule_full_text'] += ' changed'
            elif change == 'error':
                next(r for r in registry['errors'] if r['error_code']=='E-EXPRESSION-003')['repair_action'] += ' changed'
            else:
                migration = registry['historical_rule_migrations']['eye-event-applicability-v1']
                key = 'baseline_sha256' if change=='baseline_hash' else 'current_sha256'
                migration['atomic_semantic_definitions']['AR-R10-EXPR-003'][key] = '0'*64
            with self.subTest(change=change):
                self.assertTrue(any('historical baseline:' in f for f in check_registry_definition(registry)))

    def test_other_historical_rules_cannot_borrow_this_migration(self):
        baseline = json.loads((ROOT/'skills/shortdrama-storyboard/references/historical_rule_baseline.json').read_text(encoding='utf-8'))
        identity = next(key for key in baseline['atomic_semantic_definitions'] if key != 'AR-R10-EXPR-003')
        row = next(r for r in self.registry['atomic_rules'] if r['rule_id'] == identity)
        row['rule_full_text'] += ' changed'
        self.assertTrue(any(identity in f for f in check_registry_definition(self.registry)))


if __name__ == '__main__':
    unittest.main()
