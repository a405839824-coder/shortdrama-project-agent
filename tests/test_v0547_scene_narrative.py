"""Small semantic-boundary regression retained, not run in this release."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/shortdrama-storyboard/scripts'))
from production_plan import ProductionPlanError
from scene_narrative import SceneNarrativeGuard, validate_narrative, verify_prior_deliveries


class SceneNarrativeRegression(unittest.TestCase):
    def test_source_delivery_parallel_time_and_history_are_separate(self):
        def segment(identity, shot, relation, reference, tick):
            return {'id': identity, 'shot_ids': [shot], 'time': {'relation': relation,
                'reference_segment_id': reference, 'start_tick': tick, 'evidence_fact_ids': ['cause']},
                'purpose': 'advance', 'purpose_fact_ids': ['cause' if shot == 'A' else 'effect'],
                'orientation_subject_ids': ['person'], 'entry_basis': 'source room and visible person',
                'cut_basis': 'source outcome', 'audio_bridge': 'none'}
        narrative = {'contract': 'scene-narrative-v1', 'segments': [segment('sa', 'A', 'initial', None, 0),
            segment('sb', 'B', 'simultaneous', 'sa', 0)], 'prior_deliveries': [], 'causal_links': [{
            'fact_id': 'effect', 'requires_fact_id': 'cause', 'treatment': 'shown',
            'establishment_shot_id': 'A', 'prior_delivery_id': None, 'reveal_shot_id': None,
            'future_source_span': None, 'reason': 'the audience first sees the source trigger'}]}
        facts = {'cause': {'depends_on': [], 'conditions': []},
                 'effect': {'depends_on': ['cause'], 'conditions': []}}
        rows = [{'shot_id': s, 'scene_id': place, 'continuity_id': place, 'episode_id': 'E1',
                 'ticks': [0, 10, 20, 40], 'transition': {'relation': relation}}
                for s, place, relation in [('A', 'room_a', 'initial'), ('B', 'room_b', 'simultaneous_different_place')]]
        plan = {'narrative': narrative, 'decisions': []}
        validate_narrative(narrative, rows, facts, {})
        guard = SceneNarrativeGuard({'shots': rows}, plan, {})
        self.assertEqual(guard.clock, {'A': 0, 'B': 0})
        guard.deliveries = [dict(shot_id=s, id=s, fact_ids=[fid], start=0, end=20, co_delivery=True)
                            for s, fid in [('A', 'cause'), ('B', 'effect')]]
        self.assertEqual(guard.finish()['status'], 'pass')
        guard.deliveries = [guard.deliveries[1]]
        with self.assertRaises(ProductionPlanError): guard.finish()
        broken = copy.deepcopy(narrative); broken['causal_links'] = []
        with self.assertRaises(ProductionPlanError): validate_narrative(broken, rows, facts, {})
        broken = copy.deepcopy(narrative); broken['segments'][1]['time']['start_tick'] = 40
        with self.assertRaises(ProductionPlanError):
            SceneNarrativeGuard({'shots': rows}, {**plan, 'narrative': broken}, {})
        guard = SceneNarrativeGuard({'shots': rows}, plan, {})
        guard.resource_ends = {'scene:room_b': 40}
        world = {'actors': {}, 'props': {}, 'points': {}}
        with self.assertRaises(ProductionPlanError):
            guard.check_shot(rows[1], [{'world': world}], [], [])
        raw = json.dumps({'shots': [{'镜头编号': 'OLD', '画面内容': '使女递来求救信。'}]}, ensure_ascii=False).encode()
        record = {'id': 'previous', 'fact_ids': ['cause'], 'artifact_id': 'actual',
            'artifact_sha256': hashlib.sha256(raw).hexdigest(), 'shot_id': 'OLD',
            'field': '画面内容', 'quote': '使女递来求救信。'}
        evidence = {'prior_deliveries': [record]}
        verify_prior_deliveries(evidence, lambda _: raw)
        with self.assertRaises(ProductionPlanError): verify_prior_deliveries(evidence, None)
        record['quote'] = '丫鬟凭空出现并报信。'
        with self.assertRaises(ProductionPlanError): verify_prior_deliveries(evidence, lambda _: raw)


if __name__ == '__main__':
    unittest.main()
