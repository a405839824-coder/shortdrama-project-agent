"""New physical effects use the existing source-bound knowledge gates."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/shortdrama-storyboard/scripts'), str(ROOT / 'tests')]
from production_plan_test_support import frozen_plan
from source_action_plans import construct_source_action_plans
from source_decisions import SourceDecisionError
from production_decisions import ProductionDecisionError
from test_v0527_production_decisions import action
from test_v0527_source_action_plans import physical_source
from test_v0555_knowledge_events import compile_and_lower
from test_v0565_actor_carry import carry_fixture


def knowledge_fixture(*, early=False, last_kind='carry_actor', dependency=False):
    data, events = carry_fixture(crossing=last_kind == 'carry_actor_through_passage')
    steps = events[0]['steps']
    if last_kind in {'embrace', 'lift_actor'}:
        count = 1 if last_kind == 'embrace' else 2
        events[0]['steps'] = steps[:count]; data['shots'] = data['shots'][:count]
    elif last_kind == 'release_embrace':
        events[0]['steps'] = [steps[0], {'step_id': 'lift', 'kind': 'release_embrace',
                                       'parameters': {'target_actor': '乙'}, 'after': ['embrace']}]
        data['shots'] = data['shots'][:2]
    data['shots'][-1]['actions'][0]['end'] = 30
    # Crossing has a completion acquisition but no fabricated same-room reaction.
    if last_kind != 'carry_actor_through_passage':
        data['shots'][-1]['actions'].append(action('expression', id='reaction',
            start=25 if early else 30, end=35, to='双眼微睁，嘴唇微张'))
    graph, uses, texts = physical_source('甲将乙搂近，亲自抱起乙，抱着乙走到门边并过门。')
    if dependency:
        f = copy.deepcopy(graph['facts'][0]); f.update(id='F2', event_id='PRIOR', required=False)
        graph['facts'].append(f); graph['facts'][0]['depends_on'] = ['F2']
        uses['uses'][0]['fact_ids'].append('F2')
    evidence = data['project']['prevention_evidence']
    evidence['source_decisions'] = uses
    evidence['source_ledger'] = [{'source_id': 'DOC', 'source_text': texts['DOC']}]
    for shot in data['shots']: shot['source_unit_ids'] = ['DOC']
    graph['action_plans'] = construct_source_action_plans(initial_world=data['initial_world'], events=events)
    graph['production_plan'] = frozen_plan(data, graph, texts)
    knowledge = graph['production_plan']['knowledge']; knowledge['initial'] = []
    for entry in knowledge['uses']:
        entry['actor'] = '甲'
        if entry['action_id'] == 'reaction':
            entry.update(fact_ids=['F1'], stance='known', evidence_fact_ids=['F1'])
        else:
            entry.update(fact_ids=[], stance='not_applicable', event_fact_ids=['F1'] + (['F2'] if dependency else []))
    knowledge['acquisitions'] = [{'id': 'completed', 'actor': '甲', 'fact_ids': ['F1'],
        'evidence_fact_ids': ['F1'], 'action_id': data['shots'][-1]['actions'][0]['id'], 'mode': 'self_event'}]
    return data, graph, texts


class CarryKnowledgeTests(unittest.TestCase):
    def test_supported_own_event_acquires_only_after_its_actual_last_step(self):
        for kind in ('embrace', 'lift_actor', 'carry_actor', 'carry_actor_through_passage', 'release_embrace'):
            with self.subTest(kind=kind):
                result = compile_and_lower(*knowledge_fixture(last_kind=kind))
                self.assertEqual(result['derivation']['production_prevention']['status'], 'pass')
                self.assertEqual(result['derivation']['production_prevention']['semantic_status'], 'pending_independent_review')

    def test_early_reaction_or_acquisition_at_embrace_before_travel_rejected(self):
        with self.assertRaisesRegex(ProductionDecisionError, 'before its acquisition'):
            compile_and_lower(*knowledge_fixture(early=True))
        data, graph, texts = knowledge_fixture()
        graph['production_plan']['knowledge']['acquisitions'][0]['action_id'] = 'event_embrace'
        with self.assertRaisesRegex(ProductionDecisionError, 'before all actual source steps'):
            compile_and_lower(data, graph, texts)

    def test_carried_target_cannot_acquire_carrier_event_as_its_own(self):
        data, graph, texts = knowledge_fixture()
        graph['production_plan']['knowledge']['acquisitions'][0]['actor'] = '乙'
        with self.assertRaisesRegex(ProductionDecisionError, 'same actual physical-event actor'):
            compile_and_lower(data, graph, texts)

    def test_dependency_cannot_be_absorbed_into_event_or_silently_become_known(self):
        data, graph, texts = knowledge_fixture(dependency=True)
        with self.assertRaisesRegex(ProductionDecisionError, 'dependency'):
            compile_and_lower(data, graph, texts)
        data, graph, texts = knowledge_fixture(dependency=True)
        for entry in graph['production_plan']['knowledge']['uses']:
            if entry['action_id'] != 'reaction':
                entry.update(event_fact_ids=['F1'], fact_ids=['F2'], stance='known', evidence_fact_ids=['F2'])
        with self.assertRaisesRegex(ProductionDecisionError, 'before its acquisition'):
            compile_and_lower(data, graph, texts)

    def test_future_or_other_person_thought_cannot_become_completed_carry_knowledge(self):
        for change in ('future', 'private_thought'):
            with self.subTest(change=change):
                data, graph, texts = knowledge_fixture()
                if change == 'future': graph['facts'][0]['occurrence'] = 'future'
                else: graph['facts'][0].update(subject='乙', channel='thought')
                with self.assertRaises((ProductionDecisionError, SourceDecisionError)):
                    compile_and_lower(data, graph, texts)


if __name__ == '__main__': unittest.main()
