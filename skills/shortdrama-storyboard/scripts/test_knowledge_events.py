"""Certificate-packaged knowledge gate controls, not prose/reading certification."""
import copy
import unittest
from types import SimpleNamespace

from production_plan import ProductionPlanGuard, ProductionPlanError, model_production_contract


def fact(identity='F1', actor='甲', channel='action', text='甲推不动乙。'):
    return {'id':identity,'event_id':'E-'+identity,'subject':actor,'channel':channel,
            'polarity':'affirmative','modality':'asserted','occurrence':'occurred',
            'expression_occurrence':'occurred' if channel=='oral' else 'not_applicable',
            'expression_status':'asserted' if channel=='oral' else 'not_applicable',
            'content_availability':'given' if channel=='oral' else 'not_applicable',
            'conditions':[],'depends_on':[],
            'supports':[{'source_id':'DOC','start':0,'end':len(text),'text':text}]}


def setup(kind='push_actor_no_displacement', text='甲推不动乙。'):
    oral=kind=='speak'; f=fact(channel='oral' if oral else 'action',text=text)
    use={'id':'U1','actor':'甲','mode':'spoken' if oral else 'physical_action',
         'fact_ids':['F1'],'source_constraints':[f],'text':text}
    action={'id':'event','kind':kind,'actor':'甲','source_use_id':'U1','start':0,'end':4}
    guard=ProductionPlanGuard.__new__(ProductionPlanGuard)
    guard.source={'uses_by_id':{'U1':use}}
    guard.plan={'knowledge':{'acquisitions':[]}}
    guard.uses={'event':{'action_id':'event','actor':'甲','fact_ids':[],
                       'event_fact_ids':['F1'],'evidence_fact_ids':[],'stance':'not_applicable'}}
    guard.known=set();guard.known_at={};guard.reported=set();guard.reported_at={}
    guard.events={};guard.granted=set();guard.source_action_ids={'U1':{'event'}}
    guard.narrative=SimpleNamespace(clock={'S1':0,'EARLIER':0,'LATER':20})
    world={'actors':{name:{'scene_id':'ROOM','eye_state':'natural_open',
                          'gaze':{'ref':{'kind':'prop','id':'清单'},'position_xyz':[0,1,1]}}
                     for name in ['甲','乙']},
           'scenes':{'ROOM':{'barriers':[]}},
           'props':{'清单':{'anchor':{'point':'纸张位置'},'physical':{'material':'paper'}}},
           'points':{'纸张位置':{'scene_id':'ROOM','position_xyz':[0,1,1]}}}
    samples=[{'tick':n,'world':copy.deepcopy(world)} for n in range(13)]
    row={'shot_id':'S1','ticks':[0,4,8,12]}
    return guard,row,samples,action,use


def acquire(guard, mode='self_event', **changes):
    item={'id':'receipt','actor':'甲','fact_ids':['F1'],'evidence_fact_ids':['F1'],
          'action_id':'event','mode':mode}
    if mode=='source_reading':item['prop_id']='清单'
    item.update(changes);guard.plan['knowledge']['acquisitions']=[item]


def reaction(guard, start=4, actor='甲', stance='known'):
    action={'id':'reaction','kind':'expression','actor':actor,'source_use_id':None,'start':start,'end':start+1}
    guard.uses['reaction']={'action_id':'reaction','actor':actor,'fact_ids':['F1'],
                            'stance':stance,'evidence_fact_ids':['F1']}
    return action


class KnowledgeEventTests(unittest.TestCase):
    def test_public_contract_exposes_complete_limited_remote_authoring_rules(self):
        contract=model_production_contract();p=contract['knowledge_event_policy']
        self.assertEqual(p['contract'],'source-event-knowledge-v1')
        self.assertIn('看清内容',p['reading_receipt_phrases'])
        self.assertIn('push_actor_no_displacement',p['self_event_effect_kinds'])
        self.assertEqual(p,contract['plan_shape']['knowledge']['event_provenance'])

    def test_own_event_is_provenance_not_initial_or_acquired_knowledge(self):
        g,r,s,a,u=setup();g._check_knowledge(r,s,[a])
        self.assertFalse(g.known);self.assertFalse(g.reported)

    def test_actual_conditional_future_and_negative_words_do_not_grant_truth(self):
        for occurrence in ['conditional','future','occurred']:
            with self.subTest(occurrence=occurrence):
                g,r,s,a,u=setup('speak','若下雨，我明天不去。')
                u['source_constraints'][0].update(occurrence=occurrence,polarity='negative',modality='possible')
                g._check_knowledge(r,s,[a]);self.assertFalse(g.known)
                acquire(g)
                with self.assertRaisesRegex(ProductionPlanError,'physical-event'):g._check_knowledge(r,s,[a])

    def test_unperformed_or_unprovided_words_are_not_event_provenance(self):
        for key,value in [('expression_occurrence','future'),('expression_status','uncertain'),('content_availability','unstated')]:
            g,r,s,a,u=setup('speak');u['source_constraints'][0][key]=value
            with self.assertRaisesRegex(ProductionPlanError,'actual supplied words'):g._check_knowledge(r,s,[a])

    def test_physical_conditional_or_future_event_stays_rejected(self):
        for key,value in [('occurrence','future'),('modality','possible'),('polarity','negative'),('conditions',['F2'])]:
            g,r,s,a,u=setup();u['source_constraints'][0][key]=value
            with self.assertRaisesRegex(ProductionPlanError,'conditional, possible or future'):g._check_knowledge(r,s,[a])

    def test_private_dependency_cannot_be_dropped_or_absorbed_as_own_event(self):
        for absorb in [False,True]:
            g,r,s,a,u=setup();f=fact('F2',actor='乙',text='乙私下筹谋。')
            u['fact_ids'].append('F2');u['source_constraints'].append(f)
            u['source_constraints'][0]['depends_on']=['F2']
            if absorb:g.uses['event']['event_fact_ids'].append('F2')
            with self.assertRaises(ProductionPlanError):g._check_knowledge(r,s,[a])

    def test_wrong_actor_no_source_and_context_disguised_as_same_event_fail(self):
        for change in ['actor','no_source','same_event_dependency']:
            g,r,s,a,u=setup()
            if change=='actor':u['source_constraints'][0]['subject']='乙'
            elif change=='no_source':a['source_use_id']=None
            else:
                f=fact('F2');f['event_id']='E-F1';u['source_constraints'].append(f)
                u['fact_ids'].append('F2');u['source_constraints'][0]['depends_on']=['F2']
                g.uses['event']['event_fact_ids'].append('F2')
            with self.assertRaises(ProductionPlanError):g._check_knowledge(r,s,[a])

    def test_completed_own_result_is_available_at_end_and_never_before(self):
        g,r,s,a,u=setup();acquire(g);g._check_knowledge(r,s,[a,reaction(g)])
        self.assertEqual(g.known_at[('甲','F1')],4)
        g,r,s,a,u=setup();acquire(g)
        with self.assertRaisesRegex(ProductionPlanError,'before its acquisition'):
            g._check_knowledge(r,s,[a,reaction(g,start=3)])

    def test_incomplete_steps_wrong_actor_and_pure_gaze_cannot_grant_results(self):
        for change in ['unfinished','wrong_actor','gaze']:
            g,r,s,a,u=setup('look' if change=='gaze' else 'push_actor_no_displacement');acquire(g)
            if change=='unfinished':g.source_action_ids['U1'].add('later_step')
            if change=='wrong_actor':g.plan['knowledge']['acquisitions'][0]['actor']='乙'
            with self.assertRaises(ProductionPlanError):g._check_knowledge(r,s,[a])

    def test_unsourced_hold_cannot_grant_self_or_reading_knowledge(self):
        for mode in ['self_event','source_reading']:
            g,r,s,a,u=setup('hold','甲看清内容');a['source_use_id']=None
            g.uses['event']['event_fact_ids']=[];acquire(g,mode)
            with self.assertRaisesRegex(ProductionPlanError,'actual compiled source use'):
                g._check_knowledge(r,s,[a])

    def test_later_story_result_cannot_be_used_in_earlier_parallel_scene(self):
        g,r,s,a,u=setup();acquire(g);r['shot_id']='LATER';g._check_knowledge(r,s,[a])
        r['shot_id']='EARLIER';b=reaction(g)
        with self.assertRaisesRegex(ProductionPlanError,'later physical event'):g._check_knowledge(r,s,[b])

    def test_actual_source_reading_requires_exact_receipt_paper_and_sustained_gaze(self):
        g,r,s,a,u=setup('hold','甲看清内容');acquire(g,'source_reading')
        g._check_knowledge(r,s,[a,reaction(g)])
        self.assertEqual(g.known,{('甲','F1')});self.assertNotIn('text',s[0]['world']['props']['清单'])

    def test_looking_holding_denied_reading_or_later_reaction_are_not_receipts(self):
        for text in ['甲停住','甲望着纸','甲没有看清内容','乙看清内容','甲看清内容，顿时瞪眼。']:
            g,r,s,a,u=setup('hold',text);acquire(g,'source_reading')
            with self.assertRaisesRegex(ProductionPlanError,'explicit source reading clause'):g._check_knowledge(r,s,[a])

    def test_closed_eyes_wrong_target_other_material_barrier_and_look_only_fail(self):
        for change in ['closed','target','material','barrier','look_only']:
            g,r,s,a,u=setup('hold','甲看清内容');acquire(g,'source_reading')
            if change=='closed':s[2]['world']['actors']['甲']['eye_state']='closed'
            if change=='target':s[2]['world']['actors']['甲']['gaze']['ref']['id']='另一张纸'
            if change=='material':s[2]['world']['props']['清单']['physical']['material']='wood'
            if change=='barrier':s[2]['world']['scenes']['ROOM']['barriers']=['遮挡']
            if change=='look_only':a['kind']='look'
            with self.assertRaises(ProductionPlanError):g._check_knowledge(r,s,[a])

    def test_reading_cannot_supply_unstated_contents_or_acquire_before_dwell_ends(self):
        g,r,s,a,u=setup('hold','甲看清内容');acquire(g,'source_reading',fact_ids=['SECRET'],evidence_fact_ids=['SECRET'])
        with self.assertRaises(ProductionPlanError):g._check_knowledge(r,s,[a])
        g,r,s,a,u=setup('hold','甲看清内容');acquire(g,'source_reading')
        with self.assertRaisesRegex(ProductionPlanError,'before its acquisition'):g._check_knowledge(r,s,[a,reaction(g,3)])

    def test_heard_remains_report_only_and_self_hearing_is_rejected(self):
        g,r,s,a,u=setup('speak');acquire(g,'heard',actor='乙')
        g._check_knowledge(r,s,[a,reaction(g,actor='乙',stance='reported')])
        self.assertEqual(g.reported,{('乙','F1')});self.assertFalse(g.known)
        g,r,s,a,u=setup('speak');acquire(g,'heard')
        with self.assertRaisesRegex(ProductionPlanError,'same-scene disclosure'):g._check_knowledge(r,s,[a])


if __name__=='__main__':unittest.main()
