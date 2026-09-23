"""Public synthetic hand-contact controls, bundled into the runtime certificate."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import production_actions as actions
import production_decisions as production
from author_storyboard import _render_decisions
from source_action_plans import construct_source_action_plans, source_step, SourceActionPlanError
from source_decisions import compile_source_decisions
from test_failed_push import fixture as public_world


def fixture(hand='right', target_part='left_hand', *, release=False, release_override=None, posture='坐姿'):
    data, _ = public_world(posture)
    text = '甲握住乙的手，随后松开。'
    shot = data['shots'][0]
    shot.update(shot_uid='HAND-GRASP-001', source_unit_ids=['TEST-HAND-GRASP'])
    shot['editorial'].update(功能类型='握手接触与释放', 原文支撑句=text)
    shot['transition']['basis'] = '两人在同一动作试验场。'
    evidence = data['project']['prevention_evidence']
    evidence['source_ledger'] = [{'source_id':'TEST-HAND-GRASP','source_text':text}]
    evidence['cognition_matrix'] = [{'character':'甲','knows':['握手'],'does_not_know':[]}]
    evidence['shot_plan'] = [{'shot_id':'EP90-001','core_function':'手部接触'}]
    world = data['initial_world']
    world['points']['手旁'] = {'scene_id':'ROOM','position_xyz':[.1,.4,1.3]}
    for who in ('甲', '乙'):
        origin = world['actors'][who]['position_xyz']
        world['points'][who+'近位'] = {'scene_id':'ROOM','position_xyz':[origin[0]+.01,*origin[1:]]}
    params = {'hand':hand,'target_actor':'乙','target_part':target_part,'mode':'grasp'}
    steps = [{'step_id':'grasp','kind':'contact_actor','parameters':params,'after':[]}]
    shot['actions'] = [source_step(action_id='grasp1',source_use_id='U1',step_id='grasp',start=10,end=30)]
    if release:
        second = copy.deepcopy(shot)
        second.update(shot_id='EP90-002',shot_uid='HAND-GRASP-002',
                      transition={'relation':'continuous','basis':'握手持续至实际松手。'})
        release_params = {**params,'mode':'release_grasp',**(release_override or {})}
        steps.append({'step_id':'release','kind':'contact_actor','parameters':release_params,'after':['grasp']})
        second['actions'] = [source_step(action_id='release1',source_use_id='U1',step_id='release',start=10,end=30,after=['grasp1'])]
        data['shots'].append(second)
    return data, steps


def bind(data, steps):
    text = '甲握住乙的手，随后松开。'
    fact = {'id':'F1','subject':'甲','event_id':'E1','polarity':'affirmative','conditions':[],
            'modality':'asserted','occurrence':'occurred','expression_occurrence':'not_applicable',
            'expression_status':'not_applicable','channel':'action','content_availability':'not_applicable',
            'content_policy':'not_applicable','content_text':'','depends_on':[],'required':True,
            'supports':[{'source_id':'DOC','start':0,'end':len(text),'text':text}]}
    graph = {'contract':'source-facts-v1','sources':{'DOC':hashlib.sha256(text.encode()).hexdigest()},'facts':[fact]}
    uses = {'contract':'source-decisions-v1','uses':[{'id':'U1','fact_ids':['F1'],'mode':'physical_action',
            'wording':'authored','actor':'甲','text':text}],'written_objects':[]}
    graph['action_plans'] = construct_source_action_plans(initial_world=data['initial_world'],
        events=[{'source_use_id':'U1','steps':steps}])
    raw = json.dumps(graph,ensure_ascii=False).encode()
    return compile_source_decisions(raw,uses,source_texts={'DOC':text},expected_graph_sha256=hashlib.sha256(raw).hexdigest())


def lower(data, steps):
    return production.lower_production_decisions(data,source_compilation=bind(data,steps))


def staging(kind, actor='甲', **fields):
    return {'id':'staging','kind':kind,'actor':actor,'start':31,'end':39,
            'after':['grasp1'],'source_use_id':None,**fields}


class HandGraspTests(unittest.TestCase):
    def test_both_hands_and_seated_or_standing_have_real_contact_and_fingers(self):
        for hand in ('left','right'):
            for target in ('left_hand','right_hand'):
                for posture in ('坐姿','站立'):
                    with self.subTest(hand=hand,target=target,posture=posture):
                        data,steps=fixture(hand,target,posture=posture); before=copy.deepcopy(data)
                        result=lower(data,steps); world=result['derivation']['final_world']
                        self.assertEqual(data,before)
                        self.assertEqual(world['actors']['甲']['action_state']['contacts'][hand],
                                         {'actor':'乙','part':target,'mode':'grasp'})
                        self.assertLess(production._distance(production._hand(world['actors']['甲'],hand),
                                         production._hand(world['actors']['乙'],target.split('_')[0])),1e-9)
                        self.assertEqual(world['actors']['甲']['action_state']['hand_shapes'][hand],'grip')
                        self.assertEqual(world['actors']['乙']['posture'],posture)
                        text=_render_decisions(result['typed_decisions'])['shots'][0]['画面内容']
                        self.assertIn('手指收拢',text); self.assertIn('握住乙的'+actions.PART_TEXT[target],text)
                        for wrong in ('扶住','手腕','握拳'): self.assertNotIn(wrong,text)
                        self.assertFalse(result['derivation']['source_action_execution']['source_entailment_proved'])
                        self.assertFalse(result['derivation']['commit_eligible'])

    def test_contact_begins_at_reach_and_matching_release_opens_fingers(self):
        data,steps=fixture(release=True); source=bind(data,steps)
        seen={}; real=production._apply
        def record(world,action,info,tick,path):
            real(world,action,info,tick,path)
            seen[action['id'],tick]=copy.deepcopy(world)
        with patch.object(production,'_apply',record):
            result=production.lower_production_decisions(data,source_compilation=source)
        for tick in (10,20,29):
            state=seen['grasp1',tick]['actors']['甲']['action_state']
            self.assertEqual(state['contacts'],{}); self.assertEqual(state['hand_shapes']['right'],'open')
        for event,tick in [('grasp1',30),('release1',10),('release1',29)]:
            state=seen[event,tick]['actors']['甲']['action_state']
            self.assertEqual(state['contacts']['right']['part'],'left_hand')
            self.assertEqual(state['hand_shapes']['right'],'grip')
        state=seen['release1',30]['actors']['甲']['action_state']
        self.assertEqual(state['contacts'],{}); self.assertEqual(state['hand_shapes']['right'],'open')
        rows=result['typed_decisions']['shots']
        self.assertEqual(rows[0]['end_state'],rows[1]['start_state'])
        rendered=_render_decisions(result['typed_decisions'])['shots']
        self.assertIn('仍手指收拢握住乙左手',rendered[1]['起始状态'])
        self.assertIn('手指展开，松开乙的左手',rendered[1]['画面内容'])
        self.assertNotIn('握住乙',actions.static_actor(result['derivation']['final_world']['actors']['甲'],'甲'))
        self.assertEqual(result['derivation']['final_world']['completed'].count('release1'),1)

    def test_empty_open_unbound_hands_and_same_scene_are_required(self):
        for change in ('owner_prop','target_prop','owner_fist','target_fist','owner_bound','target_bound','self','torso','far','other_scene','initial_grip','initial_contact'):
            with self.subTest(change=change):
                data,steps=fixture(); world=data['initial_world']; actions.defaults(world)
                who='甲' if change.startswith('owner') else '乙'; hand='right' if who=='甲' else 'left'
                if change.endswith('prop'): world['props']['物件']={'anchor':{'actor':who,'hand':hand},'written_ref':None}
                if change.endswith('fist'): world['actors'][who]['action_state']['hand_shapes'][hand]='fist'
                if change.endswith('bound'): world['props']['绳']={'anchor':{'actor':who,'binding':'wrists'},'written_ref':None}
                if change=='self': steps[0]['parameters']['target_actor']='甲'
                if change=='torso': steps[0]['parameters']['target_part']='torso'
                if change=='far': world['actors']['乙'].update(position_xyz=[0,3,.65],reach_origin_xyz=[0,3,1.45])
                if change=='other_scene':
                    world['scenes']['OTHER']=copy.deepcopy(world['scenes']['ROOM'])
                    world['actors']['乙']['scene_id']='OTHER'
                if change=='initial_grip': world['actors']['甲']['action_state']['hand_shapes']['right']='grip'
                if change=='initial_contact': world['actors']['甲']['action_state']['contacts']['right']={'actor':'乙','part':'left_hand','mode':'grasp'}
                with self.assertRaises(production.ProductionDecisionError): lower(data,steps)

    def test_wrong_release_part_hand_target_or_mode_cannot_unlock(self):
        for changed in ({'target_part':'right_hand'},{'target_part':'left_wrist'},{'hand':'left'},
                        {'target_actor':'甲'},{'mode':'release_support'}):
            with self.subTest(changed=changed):
                data,steps=fixture(release=True,release_override=changed)
                with self.assertRaises(production.ProductionDecisionError): lower(data,steps)

    def test_contact_locks_both_hands_and_bodies_until_release(self):
        for who,hand in [('甲','right'),('乙','left')]:
            variants=[staging('hand_move',who,hand=hand,to='手旁'),
                      staging('hand_shape',who,hand=hand,to='fist'),
                      staging('point',who,hand=hand,target={'kind':'point','id':'手旁'},finger_to='手旁'),
                      staging('move_actor',who,to=who+'近位'),
                      staging('turn',who,body_yaw=1,head_yaw=1)]
            for action in variants:
                with self.subTest(who=who,kind=action['kind']):
                    data,steps=fixture(); data['shots'][0]['actions'].append(action)
                    with self.assertRaisesRegex(production.ProductionDecisionError,'actual .*contact|actual hand grasp'): lower(data,steps)
        data,steps=fixture(release=True)
        data['shots'][1]['actions'].append({**staging('hand_move','乙',hand='left',to='手旁'),'after':['release1']})
        result=lower(data,steps)
        self.assertEqual(production._hand(result['derivation']['final_world']['actors']['乙'],'left'),[.1,.4,1.3])

    def test_contact_blocks_prop_handover_and_core_free_hand_operations(self):
        data,steps=fixture(); source=bind(data,steps)
        result=production.lower_production_decisions(data,source_compilation=source)
        world=result['derivation']['final_world']
        for who,hand in [('甲','right'),('乙','left')]:
            with self.subTest(who=who), self.assertRaisesRegex(production.ProductionDecisionError,'actual grasp/support'):
                production._free_hand(world,who,hand,'transfer-target')
        for who,hand in [('甲','left'),('乙','right')]: production._free_hand(world,who,hand,'other-hand')
        data,steps=fixture()
        data['initial_world']['actors']['丙']=copy.deepcopy(data['initial_world']['actors']['乙'])
        data['initial_world']['props']['物件']={'anchor':{'actor':'丙','hand':'right'},'written_ref':None}
        data['shots'][0]['actions'].append(staging('transfer','丙',prop='物件',to_actor='乙',to_hand='left'))
        with self.assertRaisesRegex(production.ProductionDecisionError,'actual grasp/support'): lower(data,steps)
        # A third person's contact cannot claim the already held target hand.
        world['actors']['丙']=copy.deepcopy(world['actors']['甲'])
        world['actors']['丙']['action_state']['contacts']={}
        world['actors']['丙']['action_state']['hand_shapes']['right']='open'
        action={'id':'other','kind':'contact_actor','actor':'丙','hand':'right','target_actor':'乙',
                'target_part':'left_hand','mode':'grasp','start':0,'end':10,'source_use_id':'U1'}
        with self.assertRaisesRegex(production.ProductionDecisionError,'actual grasp/support'):
            actions.prepare(world,action,{},'other-contact')

    def test_approach_and_release_lock_both_body_and_hand_channels(self):
        for release in (False,True):
            for who,hand in [('甲','right'),('乙','left')]:
                for kind,params in [('hand_move',{'hand':hand,'to':'手旁'}),('turn',{'body_yaw':1,'head_yaw':1}),
                                    ('move_actor',{'to':who+'近位'})]:
                    with self.subTest(release=release,who=who,kind=kind):
                        data,steps=fixture(release=release)
                        data['shots'][-1]['actions'].append({**staging(kind,who,**params),'start':15,'end':25,'after':[]})
                        with self.assertRaisesRegex(production.ProductionDecisionError,'overlapping writes'): lower(data,steps)

    def test_collision_and_hidden_contact_are_rejected(self):
        for change in ('barrier','hidden'):
            data,steps=fixture()
            if change=='barrier': data['initial_world']['scenes']['ROOM']['barriers']=[{'id':'隔挡','a':[-1,.6,0],'b':[1,.6,0],'height_m':2}]
            else:
                data['initial_world']['points']['取景中心']['position_xyz'][2]=1.75
                data['shots'][0]['camera']['vfov_deg']=5
            with self.subTest(change=change),self.assertRaises(production.ProductionDecisionError): lower(data,steps)

    def test_wrong_observed_contact_fingers_or_landmark_fail_replay(self):
        for change in ('missing_contact','support','wrist','fist','open','endpoint','target_fist'):
            with self.subTest(change=change):
                data,steps=fixture(); source=bind(data,steps); real=production._apply
                def corrupt(world,action,info,tick,path):
                    real(world,action,info,tick,path)
                    if action['id']=='grasp1' and tick==30:
                        state=world['actors']['甲']['action_state']
                        if change=='missing_contact': state['contacts']={}
                        if change=='support': state['contacts']['right']['mode']='support'
                        if change=='wrist': state['contacts']['right']['part']='left_wrist'
                        if change in ('fist','open'): state['hand_shapes']['right']=change
                        if change=='endpoint': world['actors']['甲']['hands']['right'][0]+=.1
                        if change=='target_fist': world['actors']['乙']['action_state']['hand_shapes']['left']='fist'
                with patch.object(production,'_apply',corrupt),self.assertRaises(production.ProductionDecisionError):
                    production.lower_production_decisions(data,source_compilation=source)

    def test_wrist_grasp_and_release_keep_existing_state_and_wording(self):
        for part in ('left_wrist','right_wrist'):
            data,steps=fixture(target_part=part,release=True); result=lower(data,steps)
            rows=_render_decisions(result['typed_decisions'])['shots']
            self.assertIn('抓住乙的'+actions.PART_TEXT[part],rows[0]['画面内容'])
            self.assertNotIn('手指收拢',rows[0]['画面内容'])
            self.assertEqual(result['derivation']['final_world']['actors']['甲']['action_state']['contacts'],{})

    def test_null_source_or_hidden_bridge_cannot_invent_a_hand_grasp(self):
        data,steps=fixture()
        action={**staging('contact_actor',**steps[0]['parameters']),'start':10,'end':30,'after':[]}
        data['shots'][0]['actions']=[action]
        with self.assertRaisesRegex(production.ProductionDecisionError,'frozen physical source use'):
            production.lower_production_decisions(data)
        data,steps=fixture(release=True); source=bind(data,steps)
        data['shots'][1]['transition']['bridge']={'ticks':20,'actions':[{**action,'start':0,'end':20}]}
        with self.assertRaises((production.ProductionDecisionError,SourceActionPlanError)):
            production.lower_production_decisions(data,source_compilation=source)


if __name__=='__main__': unittest.main()
