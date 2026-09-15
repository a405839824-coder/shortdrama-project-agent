"""Real compiler/replay acceptance across a JSON number roundtrip."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'tests'), str(ROOT/'skills/shortdrama-storyboard/scripts')]
from production_derivation import digest, inspect_derivation
from production_plan import digest as plan_digest
from shortdrama_mcp.production_route import require_current_production
from test_v0535_production_contract import complete_episode, generate
from validate_storyboard import validate_project
from episode_rules import review_obligations


def transport(value):
    # Parse decimal JSON numbers into ints when JS would serialize them that way.
    def number(raw):
        parsed = float(raw)
        return int(parsed) if parsed.is_integer() else parsed
    return json.loads(json.dumps(value, ensure_ascii=False), parse_float=number)


class JsonNumberBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, graph = complete_episode()
        # Plan fingerprints must also tolerate the same JSON number values.
        data['initial_world']['actors']['甲']['body_yaw'] = float(data['initial_world']['actors']['甲']['body_yaw'])
        with tempfile.TemporaryDirectory() as folder:
            cls.original = generate(folder, data, graph)
        cls.roundtripped = transport(cls.original)

    def test_numeric_equivalence_preserves_only_number_values(self):
        for hash_value in (digest, plan_digest):
            self.assertEqual(hash_value({'a':[0.0,9.0,-0.0,1.25]}), hash_value({'a':[0,9,0,1.25]}))
            for left, right in [(True,1), ('0.0','0'), ('e\u0301','é'), ('a\r\nb','a\nb'), (1.25,1.250000001), ([1,2],[2,1])]:
                self.assertNotEqual(hash_value(left),hash_value(right))
            for value in (float('nan'),float('inf'),float('-inf')):
                with self.assertRaises(ValueError): hash_value(value)

    def test_real_complete_episode_replays_after_transport(self):
        proof = inspect_derivation(self.roundtripped)
        self.assertTrue(proof.get('production_prevention_verified'), proof)
        self.assertEqual(proof['source_obligations'],inspect_derivation(self.original)['source_obligations'])
        route = require_current_production(self.roundtripped,work_order={'operation':'storyboard_create'},scope_kind='full')
        self.assertEqual(route,'replayed_current_production')
        self.assertEqual(validate_project(self.roundtripped)['issues'],[])
        self.assertEqual(review_obligations(self.original),review_obligations(self.roundtripped))

    def test_source_text_and_rendered_dialogue_changes_still_fail(self):
        for target in ('picture','source'):
            changed=copy.deepcopy(self.roundtripped)
            if target=='picture': changed['shots'][0]['画面内容'] += '甲说道：“改写台词。”'
            else: changed['prevention_evidence']['production_derivation']['source_graph_json'] += '\n'
            with self.subTest(target=target):
                self.assertFalse(inspect_derivation(changed)['valid'])
                with self.assertRaisesRegex(ValueError,'production_plan_required'):
                    require_current_production(changed,work_order={'operation':'storyboard_create'},scope_kind='full')

    def test_changed_action_numbers_still_fail(self):
        changed=copy.deepcopy(self.roundtripped)
        changed['prevention_evidence']['production_derivation']['input']['initial_world']['actors']['甲']['body_yaw'] += .000001
        self.assertFalse(inspect_derivation(changed)['valid'])
        with self.assertRaisesRegex(ValueError,'production_plan_required'):
            require_current_production(changed,work_order={'operation':'storyboard_create'},scope_kind='scene')


if __name__=='__main__': unittest.main()
