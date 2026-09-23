"""Actual source compilation and simulated action replay for the new knowledge roles."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'skills/shortdrama-storyboard/scripts')]
import test_v0533_production_plan as prior
import test_failed_push as push
from production_plan_test_support import frozen_plan
from production_decisions import lower_production_decisions, ProductionDecisionError
from source_action_plans import construct_source_action_plans, source_step
from source_decisions import compile_source_decisions, SourceDecisionError


def compile_and_lower(data,graph,texts):
    raw=json.dumps(graph,ensure_ascii=False).encode()
    source=compile_source_decisions(raw,data['project']['prevention_evidence']['source_decisions'],
        source_texts=texts,expected_graph_sha256=hashlib.sha256(raw).hexdigest())
    return lower_production_decisions(data,source_compilation=source)


def physical(reading=False,reaction_start=30):
    data,params=push.fixture();source=push.bind(data,params)
    text='甲看清内容' if reading else '甲推不动乙。'
    fact=json.loads(source.uses[0].constraints_json)[0]
    fact['supports']=[{'source_id':'DOC','start':0,'end':len(text),'text':text}]
    use={'id':'U1','fact_ids':['F1'],'mode':'physical_action','wording':'authored','actor':'甲','text':text}
    graph={'contract':'source-facts-v1','sources':{'DOC':hashlib.sha256(text.encode()).hexdigest()},'facts':[fact],
           'action_plans':json.loads(source.action_plans_json)}
    evidence=data['project']['prevention_evidence'];evidence['source_decisions']={'contract':'source-decisions-v1','uses':[use],'written_objects':[]}
    evidence['source_ledger']=[{'source_id':'DOC','source_text':text}]
    shot=data['shots'][0];shot['source_unit_ids']=['DOC']
    if reading:
        data['initial_world']['points']['纸张位置']={'scene_id':'ROOM','position_xyz':[0,.3,1.3]}
        data['initial_world']['props']['清单']={'anchor':{'point':'纸张位置'},'written_ref':None,
            'physical':{'material':'paper','contents':'none','integrity':'intact'}}
        graph['action_plans']=construct_source_action_plans(initial_world=data['initial_world'],events=[{
            'source_use_id':'U1','steps':[
                {'step_id':'look','kind':'look','parameters':{'target':{'kind':'prop','id':'清单'},'tracking':False,'head_mode':'follow'},'after':[]},
                {'step_id':'read','kind':'hold','parameters':{},'after':['look']}]}])
        shot['actions']=[source_step(action_id='look',source_use_id='U1',step_id='look',start=0,end=10),
                         source_step(action_id='read',source_use_id='U1',step_id='read',start=10,end=30)]
    shot['actions'].append({'id':'reaction','kind':'expression','actor':'甲','source_use_id':None,
                           'start':reaction_start,'end':reaction_start+5,'after':[],
                           'to':'双眼微睁，嘴唇微张'})
    graph['production_plan']=frozen_plan(data,graph,{'DOC':text});k=graph['production_plan']['knowledge'];k['initial']=[]
    for entry in k['uses']:
        entry['actor']='甲'
        if entry['action_id']=='reaction':entry.update(fact_ids=['F1'],stance='known',evidence_fact_ids=['F1'])
        else:entry.update(fact_ids=[],stance='not_applicable',event_fact_ids=['F1'])
    acq={'id':'completed','actor':'甲','fact_ids':['F1'],'evidence_fact_ids':['F1'],
         'action_id':'read' if reading else 'push1','mode':'source_reading' if reading else 'self_event'}
    if reading:acq['prop_id']='清单'
    k['acquisitions']=[acq]
    return data,graph,{'DOC':text}


class KnowledgeEventIntegrationTests(unittest.TestCase):
    def speech(self):
        case=prior.ProductionPlanTests();case.setUp();case.speech()
        k=case.plan['knowledge'];k['initial']=[]
        k['uses'][0].update(fact_ids=[],event_fact_ids=['F1'],stance='not_applicable')
        return case

    def test_actual_future_utterance_compiles_without_fake_initial(self):
        c=self.speech();result=c.lower()
        self.assertEqual(result['derivation']['production_prevention']['status'],'pass')
        self.assertEqual(result['derivation']['production_prevention']['semantic_status'],'pending_independent_review')
        self.assertEqual(c.plan['knowledge']['initial'],[])

    def test_overlapping_provenance_and_assertion_or_unknown_field_rejected(self):
        for change in ['overlap','unknown']:
            c=self.speech();u=c.plan['knowledge']['uses'][0]
            if change=='overlap':u.update(fact_ids=['F1'],stance='known')
            else:u['source_asserted_known']=True
            with self.assertRaises(SourceDecisionError):c.lower()

    def test_actual_stationary_push_then_reaction_passes_and_early_reaction_fails(self):
        result=compile_and_lower(*physical())
        self.assertEqual(result['derivation']['production_prevention']['status'],'pass')
        with self.assertRaisesRegex(ProductionDecisionError,'before its acquisition'):
            compile_and_lower(*physical(reaction_start=25))

    def test_actual_look_dwell_and_reading_reaction_keep_unknown_paper_uninvented(self):
        data,g,texts=physical(reading=True);result=compile_and_lower(data,g,texts)
        self.assertEqual(result['derivation']['production_prevention']['status'],'pass')
        self.assertIsNone(result['derivation']['final_world']['props']['清单']['written_ref'])
        self.assertEqual(g['production_plan']['knowledge']['initial'],[])

    def test_reading_cannot_complete_at_look_before_hold(self):
        data,g,texts=physical(reading=True)
        g['production_plan']['knowledge']['acquisitions'][0]['action_id']='look'
        with self.assertRaisesRegex(ProductionDecisionError,'before all actual source steps'):
            compile_and_lower(data,g,texts)

    def test_completed_event_cannot_be_acquired_by_a_different_actor(self):
        data,g,texts=physical()
        g['production_plan']['knowledge']['acquisitions'][0]['actor']='乙'
        with self.assertRaisesRegex(ProductionDecisionError,'same actual physical-event actor'):
            compile_and_lower(data,g,texts)

    def test_conditions_private_reports_and_initial_contract_keep_existing_guards(self):
        for method in ['test_listener_cannot_use_a_disclosure_before_it_finishes',
                       'test_hearing_a_claim_does_not_certify_its_truth',
                       'test_no_acquisition_and_self_hearing_do_not_grant_knowledge',
                       'test_actual_actor_cannot_be_changed_by_knowledge_declaration']:
            case=prior.ProductionPlanTests();case.setUp();getattr(case,method)()


if __name__=='__main__':unittest.main()
