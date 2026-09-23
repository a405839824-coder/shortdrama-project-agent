"""Runtime-packaged failed-push controls; not a source-semantic certificate."""
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


KIND = 'push_actor_no_displacement'
OWNER, TARGET = '甲', '乙'


def fixture(posture='坐姿'):
    """Declared seated shaft landmarks, with a neutral test-room light."""
    actor = {'scene_id':'ROOM','position_xyz':[0,0,.65], 'body_yaw':0,'head_yaw':0,
             'eye_height':1.1,'eye_state':'natural_open','posture':posture,
             'hands':{'left':[-.2,.3,.6],'right':[.2,.3,.6]},'gaze':None,'reach_m':.9,
             'reach_origin_xyz':[0,0,1.45]}
    target = copy.deepcopy(actor)
    target.update(position_xyz=[.2,.45,.65], reach_origin_xyz=[.2,.45,1.45])
    light = {'period':'day','kind':'paper_window','name':'纸窗','state':'closed',
             'position_xyz':[3,0,2],'main_tone':'neutral','shadow_tone':'neutral','materials':['纸面']}
    world = {'scenes':{'ROOM':{'label':'动作试验场','door_window_state':'纸窗关闭。',
              'opening_labels':{'纸窗':'纸窗'},'light':light,'barriers':[]}},
             'actors':{OWNER:actor,TARGET:target},'props':{},
             'points':{'取景中心':{'scene_id':'ROOM','position_xyz':[.1,.2,1.2]},
                       '旁侧空位':{'scene_id':'ROOM','position_xyz':[.35,.65,.65]}}}
    shot = {'shot_id':'EP90-001','shot_uid':'FAILED-PUSH-001','episode_id':'EP90','scene_id':'ROOM',
            'continuity_id':'ROOM-C1','ticks':[0,10,30,40],
            'camera':{'position_xyz':[.25,4,1.6],'target':'取景中心','hfov_deg':60,'vfov_deg':75,'frame':'wide'},
            'actions':[], 'editorial':{'功能类型':'保留推不动的动作结果','环境音效':{'mode':'none'},
              '环境动态':{'mode':'shared_world'},'参考图锚定':'无外部图像锚点。',
              '原文依据类型':'动作叙述','原文支撑句':'甲推不动乙。'},
            'source_unit_ids':['TEST-FAILED-PUSH'], 'transition':{'relation':'initial','basis':'两人已坐在车辕上。'}}
    data = {'contract':'production-decisions-v1',
            'project':{'source_lock':{'output_contract':'R10-SPC-1','target_model':'Seedance 2.5','aspect_ratio':'9:16'},
              'prevention_evidence':{
                'scope_lock':{'authoritative_source':'公开动作回归用片段，不是正式源文审计'},
                'source_ledger':[{'source_id':'TEST-FAILED-PUSH','source_text':'甲推不动乙。'}],
                'change_lock':{'must_include':[],'must_exclude':[]},
                'cognition_matrix':[{'character':OWNER,'knows':['推不动对方'],'does_not_know':[]}],
                'shot_plan':[{'shot_id':'EP90-001','core_function':'推不动的可见结果'}],
                'performance_chains':[],'audit_layers':['源文对白','机位可见性','空间连续','表演链',
                    '镜头节奏','正式母版单镜执行完整性','禁项与声音','统计闭合'],'repair_history':[],
                'audio_workflow':{'timing':{'contract':'shot-timing-v1','utterances':[]}}}},
            'initial_world':world,'shots':[shot]}
    return data, {'hand':'right','target_actor':TARGET,'target_part':'torso'}


def spec(kind=KIND, **fields):
    return {'id':'push1','kind':kind,'actor':OWNER,'start':10,'end':30,'after':[],
            'source_use_id':'U1', **fields}


def bind(data, parameters, *, kind=KIND, end=30):
    text = '甲推不动乙。' if kind == KIND else '甲推开乙。'
    fact = {'id':'F1','subject':OWNER,'event_id':'E1','polarity':'affirmative','conditions':[],
            'modality':'asserted','occurrence':'occurred','expression_occurrence':'not_applicable',
            'expression_status':'not_applicable','channel':'action','content_availability':'not_applicable',
            'content_policy':'not_applicable','content_text':'','depends_on':[],'required':True,
            'supports':[{'source_id':'DOC','start':0,'end':len(text),'text':text}]}
    graph = {'contract':'source-facts-v1','sources':{'DOC':hashlib.sha256(text.encode()).hexdigest()},'facts':[fact]}
    uses = {'contract':'source-decisions-v1','uses':[{'id':'U1','fact_ids':['F1'],'mode':'physical_action',
            'wording':'authored','actor':OWNER,'text':text}],'written_objects':[]}
    events = [{'source_use_id':'U1','steps':[{'step_id':'push','kind':kind,'parameters':parameters,'after':[]}]}]
    graph['action_plans'] = construct_source_action_plans(initial_world=data['initial_world'],events=events)
    data['shots'][0]['actions'] = [source_step(action_id='push1',source_use_id='U1',step_id='push',start=10,end=end)]
    raw = json.dumps(graph,ensure_ascii=False).encode()
    return compile_source_decisions(raw,uses,source_texts={'DOC':text},expected_graph_sha256=hashlib.sha256(raw).hexdigest())


def next_shot(data, relation='continuous'):
    shot = copy.deepcopy(data['shots'][0])
    shot.update(shot_id='EP90-002',shot_uid='FAILED-PUSH-002',actions=[],
                transition={'relation':relation,'basis':'推不动后两人仍留在车辕上。'})
    data['shots'].append(shot)
    return shot


class FailedPushTests(unittest.TestCase):
    def lower(self, data, source):
        return production.lower_production_decisions(data,source_compilation=source)

    def test_seated_attempt_has_real_push_contact_zero_motion_and_release(self):
        data, params = fixture(); source = bind(data,params); before=copy.deepcopy(data)
        snapshots={}; real=production._apply
        def record(world, action, info, tick, path):
            real(world,action,info,tick,path)
            if action['id']=='push1':snapshots[tick]=copy.deepcopy(world)
        with patch.object(production,'_apply',record):result=self.lower(data,source)
        self.assertEqual(data,before)
        for world in snapshots.values():
            self.assertEqual(world['actors'][TARGET]['position_xyz'],[.2,.45,.65])
            self.assertEqual(world['actors'][TARGET]['reach_origin_xyz'],[.2,.45,1.45])
            self.assertEqual(world['actors'][TARGET]['posture'],'坐姿')
            self.assertEqual(world['actors'][OWNER]['position_xyz'],[0,0,.65])
        self.assertEqual(snapshots[14]['actors'][OWNER]['action_state']['contacts'],{})
        for tick in range(15,25):
            self.assertEqual(snapshots[tick]['actors'][OWNER]['action_state']['contacts']['right'],
                             {'actor':TARGET,'part':'torso','mode':'push'})
            self.assertEqual(production._hand(snapshots[tick]['actors'][OWNER],'right'),[.2,.45,1.45])
        for tick in (25,30):self.assertEqual(snapshots[tick]['actors'][OWNER]['action_state']['contacts'],{})
        final=result['derivation']['final_world']
        self.assertEqual(production._hand(final['actors'][OWNER],'right'),[.2,.3,1.25])
        self.assertEqual(final['completed'].count('push1'),1)
        text=_render_decisions(result['typed_decisions'])['shots'][0]['画面内容']
        self.assertIn('推不动',text);self.assertIn('保持原位',text)
        for wrong in ('被推至','扶住','抓住','站在','跌倒'):self.assertNotIn(wrong,text)
        self.assertFalse(result['derivation']['source_action_execution']['source_entailment_proved'])

    def test_result_and_seated_posture_are_inherited_without_second_push(self):
        data,params=fixture();source=bind(data,params);next_shot(data)
        result=self.lower(data,source); rows=result['typed_decisions']['shots']
        self.assertEqual(rows[0]['end_state'],rows[1]['start_state'])
        self.assertEqual(result['derivation']['final_world']['actors'][TARGET]['posture'],'坐姿')
        self.assertEqual(result['derivation']['final_world']['completed'].count('push1'),1)

    def test_standing_failure_is_distinct_from_success_and_success_stays_standing_only(self):
        data,params=fixture('站立');source=bind(data,params)
        self.assertEqual(self.lower(data,source)['derivation']['final_world']['actors'][TARGET]['position_xyz'],[.2,.45,.65])
        for posture,point in [('坐姿',[.35,.65,.65]),('站立',[.2,.45,.65])]:
            with self.subTest(posture=posture):
                data,params=fixture(posture);params['point']='旁侧空位'
                data['initial_world']['points']['旁侧空位']['position_xyz']=point
                source=bind(data,params,kind='push_actor')
                with self.assertRaises(production.ProductionDecisionError):self.lower(data,source)

    def test_endpoint_cannot_be_smuggled_into_stationary_action(self):
        data,params=fixture();params['point']='旁侧空位'
        with self.assertRaises(SourceActionPlanError):bind(data,params)

    def test_invalid_posture_contact_hand_reach_and_duration_are_rejected(self):
        for change in ('mixed','lying','self','face','fist','occupied','bound','far','too_fast'):
            with self.subTest(change=change):
                data,params=fixture();world=data['initial_world']
                if change=='mixed':world['actors'][TARGET]['posture']='站立'
                if change=='lying':
                    for a in world['actors'].values():a['posture']='躺卧'
                if change=='self':params['target_actor']=OWNER
                if change=='face':params['target_part']='face'
                if change=='fist':
                    actions.defaults(world);world['actors'][OWNER]['action_state']['hand_shapes']['right']='fist'
                if change=='occupied':world['props']['杯']={'anchor':{'actor':OWNER,'hand':'right'},'written_ref':None}
                if change=='bound':world['props']['绳']={'anchor':{'actor':TARGET,'binding':'wrists'},'written_ref':None}
                if change=='far':
                    world['actors'][TARGET].update(position_xyz=[.2,2,.65],reach_origin_xyz=[.2,2,1.45])
                source=bind(data,params,end=15 if change=='too_fast' else 30)
                with self.assertRaises(production.ProductionDecisionError):self.lower(data,source)

    def test_barrier_and_prior_support_or_grasp_cannot_be_called_a_push(self):
        data,params=fixture();source=bind(data,params)
        for mode in ('support','grasp'):
            world=production._world(data['initial_world'],production._source(source))
            world['actors'][OWNER]['action_state']['contacts']['left']={'actor':TARGET,'part':'left_wrist','mode':mode}
            with self.subTest(mode=mode),self.assertRaisesRegex(production.ProductionDecisionError,'release existing body contacts'):
                actions.prepare(world,spec(**params),{},'push')
        world=production._world(data['initial_world'],production._source(source))
        world['scenes']['ROOM']['barriers']=[{'id':'隔挡','a':[-1,.2,0],'b':[1,.2,0],'height_m':2}]
        with self.assertRaisesRegex(production.ProductionDecisionError,'barrier blocks'):
            actions.prepare(world,spec(**params),{},'push')

    def test_duplicate_unbound_substitute_and_hidden_bridge_are_rejected(self):
        for change in ('duplicate','unbound','substitute','bridge'):
            with self.subTest(change=change):
                data,params=fixture();source=bind(data,params)
                if change=='duplicate':
                    step=copy.deepcopy(data['shots'][0]['actions'][0]);step.update(id='push2',start=30,end=40)
                    data['shots'][0]['actions'].append(step)
                if change=='unbound':data['shots'][0]['actions']=[spec(source_use_id=None,**params)]
                if change=='substitute':data['shots'][0]['actions']=[spec(kind='contact_actor',mode='support',**params)]
                if change=='bridge':
                    row=next_shot(data,'later_same_place')
                    row['transition']['bridge']={'ticks':20,'actions':[spec(id='hidden',start=0,end=20,source_use_id=None,**params)]}
                with self.assertRaises((production.ProductionDecisionError,SourceActionPlanError)):self.lower(data,source)

    def test_target_or_hand_simultaneous_movement_is_rejected(self):
        for who in (OWNER,TARGET):
            data,params=fixture();source=bind(data,params)
            data['shots'][0]['actions'].append(spec(kind='hand_move',id='conflict',actor=who,source_use_id=None,hand='right',to='取景中心'))
            with self.subTest(who=who),self.assertRaisesRegex(production.ProductionDecisionError,'overlapping writes'):
                self.lower(data,source)

    def test_observed_state_cannot_invent_displacement_posture_or_replace_contact(self):
        for change in ('position','posture','support','missing_contact','hand'):
            with self.subTest(change=change):
                data,params=fixture();source=bind(data,params);real=production._apply
                def corrupt(world,action,info,tick,path):
                    real(world,action,info,tick,path)
                    if tick==20:
                        if change=='position':world['actors'][TARGET]['position_xyz'][0]+=.1
                        if change=='posture':world['actors'][TARGET]['posture']='站立'
                        if change=='support':world['actors'][OWNER]['action_state']['contacts']['right']['mode']='support'
                        if change=='missing_contact':world['actors'][OWNER]['action_state']['contacts']={}
                        if change=='hand':world['actors'][OWNER]['hands']['right'][0]+=.1
                with patch.object(production,'_apply',corrupt),self.assertRaises(production.ProductionDecisionError):
                    self.lower(data,source)

    def test_contact_and_stationary_target_must_both_be_visible(self):
        data,params=fixture();source=bind(data,params)
        data['shots'][0]['camera']['vfov_deg']=12
        with self.assertRaisesRegex(production.ProductionDecisionError,'landmarks in frame'):self.lower(data,source)


if __name__=='__main__':unittest.main()
