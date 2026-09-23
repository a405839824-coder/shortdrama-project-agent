"""Computed anonymous actions must survive the unchanged author/timing gates."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import production_actions as actions
import production_decisions as production
from author_storyboard import record_attempt, _render_decisions
from picture_presentation import timed_picture
from timing_model import _phase_rows
from validate_storyboard import project_gpt_keyframe, validate_project, _invisible_face_terms
from visible_entities import resolve_visible_subjects
from test_outdoor_daylight import fixture as daylight_fixture, render as render_daylight
from source_action_plans import construct_source_action_plans, source_step
from source_decisions import compile_source_decisions
from test_failed_push import fixture as public_world
from test_hand_grasp import fixture as hand_fixture, bind as bind_hand


POSE = '眉梢微抬，嘴角上扬'
NATURAL = '眉眼舒展，唇线自然'


def expression_fixture(pose=POSE, *, phased=False):
    data,_=public_world()
    data['shots'][0]['actions']=[{'id':'face','kind':'expression','actor':'甲','to':pose,
                                 'start':5 if phased else 10,'end':35 if phased else 30,
                                 'after':[],'source_use_id':None}]
    return data


def dynamic_subject_fixture(*, turn=False):
    data,_=public_world()
    world=data['initial_world']; target=world['actors']['乙']
    if turn:
        target.update(body_yaw=180,head_yaw=180)
        action={'kind':'turn','body_yaw':0,'head_yaw':0}
    else:
        target.update(position_xyz=[3,.45,.65],reach_origin_xyz=[3,.45,1.45])
        world['points']['入画位']={'scene_id':'ROOM','position_xyz':[.2,.45,.65]}
        action={'kind':'move_actor','to':'入画位'}
    data['shots'][0]['actions']=[{'id':'arrival','actor':'乙','start':5,'end':30,
                                 'after':[],'source_use_id':None,**action}]
    return data


def bind_expression(data, *, kind='expression', parameters=None):
    """Source binding for a visible expression, without an audit/approval fixture."""
    parameters = {'to':POSE} if parameters is None else parameters
    text='甲'+POSE+'。' if kind == 'expression' else '甲看向观察点。'
    fact={'id':'F1','subject':'甲','event_id':'E1','polarity':'affirmative','conditions':[],
          'modality':'asserted','occurrence':'occurred','expression_occurrence':'not_applicable',
          'expression_status':'not_applicable','channel':'action','content_availability':'not_applicable',
          'content_policy':'not_applicable','content_text':'','depends_on':[],'required':True,
          'supports':[{'source_id':'DOC','start':0,'end':len(text),'text':text}]}
    graph={'contract':'source-facts-v1','sources':{'DOC':hashlib.sha256(text.encode()).hexdigest()},'facts':[fact]}
    uses={'contract':'source-decisions-v1','uses':[{'id':'U1','fact_ids':['F1'],'mode':'physical_action',
          'wording':'authored','actor':'甲','text':text}],'written_objects':[]}
    graph['action_plans']=construct_source_action_plans(initial_world=data['initial_world'],events=[{
        'source_use_id':'U1','steps':[{'step_id':'face','kind':kind,'parameters':parameters,'after':[]}]}])
    data['shots'][0]['actions']=[source_step(action_id='face',source_use_id='U1',step_id='face',start=5,end=35)]
    raw=json.dumps(graph,ensure_ascii=False).encode()
    return compile_source_decisions(raw,uses,source_texts={'DOC':text},expected_graph_sha256=hashlib.sha256(raw).hexdigest())


class GeneratedActionBindingTests(unittest.TestCase):
    def record(self, typed):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'input.json'
            path.write_text(json.dumps(typed,ensure_ascii=False),encoding='utf-8')
            report=record_attempt(path,root/'attempts',attempt_id='anonymous-actions')
            canonical=json.loads(Path(report['canonical_path']).read_text(encoding='utf-8')) if report['canonical_path'] else None
            return report,canonical

    def accepted(self, typed):
        report,canonical=self.record(typed)
        self.assertEqual(report['status'],'rendered_pending_preflight',report.get('findings'))
        self.assertEqual(report['findings'],[])
        self.assertFalse(report['commit_eligible'])
        self.assertIsNone(report['semantic_accuracy_percent'])
        return canonical

    def test_all_concrete_poses_and_macro_fragments_pass_full_author_recording(self):
        for pose in actions.EXPRESSION_POSES:
            for phased in (False,True):
                with self.subTest(pose=pose,phased=phased):
                    data=expression_fixture(pose,phased=phased)
                    typed=production.lower_production_decisions(data)['typed_decisions']
                    events=typed['shots'][0]['events']
                    self.assertEqual(len(events),3 if phased else 1)
                    for event in events:
                        self.assertEqual(event['action'],pose)
                        self.assertIn(pose,event['basis'])
                    canonical=self.accepted(typed)
                    self.assertIn(pose,canonical['shots'][0]['画面内容'])
                    self.assertNotIn('表情变化',canonical['shots'][0]['画面内容'])

    def test_return_to_natural_pose_keeps_actual_state_and_event_sequence(self):
        data=expression_fixture()
        data['shots'][0]['actions']=[
            {'id':'smile','kind':'expression','actor':'甲','to':POSE,'start':5,'end':15,'after':[],'source_use_id':None},
            {'id':'natural','kind':'expression','actor':'甲','to':NATURAL,'start':20,'end':35,'after':['smile'],'source_use_id':None}]
        result=production.lower_production_decisions(data)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['action_state']['expression'],NATURAL)
        canonical=self.accepted(result['typed_decisions'])
        self.assertIn(POSE,canonical['shots'][0]['画面内容']); self.assertIn(NATURAL,canonical['shots'][0]['画面内容'])

    def test_fictional_abstract_or_changed_actor_and_pose_labels_are_rejected(self):
        typed=production.lower_production_decisions(expression_fixture())['typed_decisions']
        for change in ('abstract','unknown','actor','pose'):
            altered=copy.deepcopy(typed); event=altered['shots'][0]['events'][0]
            if change=='abstract': event['action']='表情变化'
            if change=='unknown': event['action']='眼中发射光束'
            if change=='actor': event['actor']='乙'
            if change=='pose': event['action']=NATURAL
            with self.subTest(change=change):
                report,_=self.record(altered)
                self.assertEqual(report['status'],'rejected')
                self.assertTrue(any('动作依据' in x['message'] for x in report['findings']))
        data=expression_fixture('眼中发射光束')
        with self.assertRaises(production.ProductionDecisionError): production.lower_production_decisions(data)

    def test_negation_and_spoken_description_cannot_lend_a_pose_event(self):
        typed=production.lower_production_decisions(expression_fixture())['typed_decisions']
        for replacement in ('甲不'+POSE+'。','甲说道：“甲'+POSE+'。”'):
            altered=copy.deepcopy(typed); row=altered['shots'][0]; original=row['events'][0]['basis']
            for phase in row['phases']: phase['text']=phase['text'].replace(original,replacement)
            row['events'][0]['basis']=replacement
            with self.subTest(replacement=replacement):
                report,_=self.record(altered)
                self.assertEqual(report['status'],'rejected')
                self.assertTrue(any('动作依据' in x['message'] for x in report['findings']))

    def test_source_bound_hidden_expression_cannot_receive_visible_action_credit(self):
        data=expression_fixture(phased=True); source=bind_expression(data)
        result=production.lower_production_decisions(data,source_compilation=source)
        self.accepted(result['typed_decisions'])
        data['shots'][0]['camera']['position_xyz']=[0,-4,1.6]
        with self.assertRaisesRegex(production.ProductionDecisionError,'landmarks in frame'):
            production.lower_production_decisions(data,source_compilation=source)

    def test_hand_grasp_and_release_use_visible_matching_verbs_through_author(self):
        for hand in ('left','right'):
            for target in ('left_hand','right_hand'):
                with self.subTest(hand=hand,target=target):
                    data,steps=hand_fixture(hand,target,release=True)
                    result=production.lower_production_decisions(data,source_compilation=bind_hand(data,steps))
                    canonical=self.accepted(result['typed_decisions'])
                    self.assertIn('握住乙的'+actions.PART_TEXT[target],canonical['shots'][0]['画面内容'])
                    self.assertIn('松开乙的'+actions.PART_TEXT[target],canonical['shots'][1]['画面内容'])
                    altered=copy.deepcopy(result['typed_decisions'])
                    altered['shots'][0]['events'][0]['actor']='乙'
                    report,_=self.record(altered)
                    self.assertEqual(report['status'],'rejected')

    def test_late_actor_is_registered_but_not_in_the_initial_keyframe(self):
        data=dynamic_subject_fixture(); result=production.lower_production_decisions(data)
        typed=result['typed_decisions']; row=typed['shots'][0]
        self.assertIn('乙（',row['fields']['可见主体'])
        self.assertIn('首帧：不入画',row['fields']['可见主体'])
        self.assertNotIn('乙位于',row['fields']['起始状态'])
        self.assertGreater(result['derivation']['shots'][0]['eye_visibility_ticks']['乙'][0][0],0)
        canonical=self.accepted(typed); shot=canonical['shots'][0]
        restored=timed_picture(shot)
        self.assertRegex(restored,r'乙眼睛仅在\d+\.\d—\d+\.\d秒清楚可见')
        self.assertEqual(len(_phase_rows({**shot,'画面内容':restored})),3)
        keyframe=project_gpt_keyframe(shot)
        self.assertIn('甲（',keyframe); self.assertNotIn('乙（',keyframe)
        altered=copy.deepcopy(typed)
        altered['shots'][0]['fields']['可见主体']='甲（面部、左手、右手、足部）'
        report,_=self.record(altered)
        self.assertEqual(report['status'],'rejected')
        self.assertTrue(any('非标准时间段' in x['message'] for x in report['findings']))

    def test_later_face_does_not_replace_initial_back_of_head_in_keyframe(self):
        data=dynamic_subject_fixture(turn=True); result=production.lower_production_decisions(data)
        canonical=self.accepted(result['typed_decisions']); shot=canonical['shots'][0]
        self.assertIn('首帧：后侧头部',shot['可见主体'])
        keyframe=project_gpt_keyframe(shot)
        self.assertIn('乙（后侧头部',keyframe)
        self.assertNotIn('乙（面部',keyframe)
        self.assertNotIn('首帧：',keyframe)

    def test_unknown_visibility_and_extra_stage_cannot_use_registered_window_exemption(self):
        typed=production.lower_production_decisions(dynamic_subject_fixture())['typed_decisions']
        for changed in ('unknown','negated','outside','extra_stage'):
            altered=copy.deepcopy(typed)
            for phase in altered['shots'][0]['phases']:
                if '乙眼睛仅在' not in phase['text']: continue
                if changed=='unknown': phase['text']=phase['text'].replace('乙眼睛仅在','丙眼睛仅在')
                if changed=='negated': phase['text']=phase['text'].replace('乙眼睛仅在','乙眼睛并非仅在')
                if changed=='outside':
                    import re
                    phase['text']=re.sub(r'乙眼睛仅在[\d.]+—[\d.]+秒','乙眼睛仅在0.0—8.0秒',phase['text'])
                if changed=='extra_stage': phase['text']+='【1.0—1.2秒】乙抬手。'
            with self.subTest(changed=changed):
                report,_=self.record(altered)
                self.assertEqual(report['status'],'rejected')

    def test_never_visible_actor_and_unregistered_initial_parts_are_not_invented(self):
        data=dynamic_subject_fixture()
        data['initial_world']['actors']['丙']=copy.deepcopy(data['initial_world']['actors']['乙'])
        data['initial_world']['actors']['丙'].update(position_xyz=[30,0,.65],reach_origin_xyz=[30,0,1.45])
        canonical=self.accepted(production.lower_production_decisions(data)['typed_decisions'])
        self.assertNotIn('丙（',canonical['shots'][0]['可见主体'])
        invalid=copy.deepcopy(canonical['shots'][0])
        invalid['可见主体']='乙（左手；首帧：面部）'
        with self.assertRaisesRegex(ValueError,'frame-zero parts'): project_gpt_keyframe(invalid)

    def test_hand_frame_negative_eyes_and_mixed_faces_keep_person_ownership(self):
        hands = resolve_visible_subjects('甲（左手、右手）；乙（左手）')
        self.assertEqual(_invisible_face_terms('甲眼睛不入画。乙眼睛不入画。甲握住乙的左手。',hands),[])
        mixed = resolve_visible_subjects('甲（面部、右手）；乙（左手）')
        self.assertEqual(_invisible_face_terms('甲眉梢微抬，嘴角上扬。甲眼睛清楚可见。乙眼睛不入画。',mixed),[])
        for legitimate in ('甲自然眨眼。','甲眉梢微抬，自然眨眼。','甲眉梢微抬，嘴角微扬。',
                           '甲轻轻闭上双眼。','甲眉梢微抬，轻轻闭上双眼。','乙观察甲自然眨眼。'):
            with self.subTest(legitimate=legitimate):
                self.assertEqual(_invisible_face_terms(legitimate,mixed),[])
        both_faces=resolve_visible_subjects('甲（面部、右手）；乙（面部、左手）')
        self.assertEqual(_invisible_face_terms('甲观察乙自然眨眼。',both_faces),[])
        for wrong in ('乙眼睛清楚可见。','甲眼睛不入画，乙眼睛睁大。','丙眼睛不入画。',
                      '甲眉梢微抬，丙眼睛睁大。','甲观察乙眼睛转动。','甲观察丙眼睛转动。',
                      '甲丙眼睛睁大。','甲眼睛并非不入画。','甲眉梢微抬，丙自然眨眼。',
                      '甲眉梢微抬，丙轻轻闭上双眼。','甲眉梢微抬，乙自然眨眼。',
                      '甲眉梢微抬，丙缓慢睁开眼睛。','甲眉梢微抬，丙闭眼。',
                      '甲观察丙自然眨眼。','甲观察乙自然眨眼。','甲观察丙闭上双眼。'):
            with self.subTest(wrong=wrong):
                self.assertTrue(_invisible_face_terms(wrong,hands if wrong.startswith('甲眼睛并非') else mixed))

    def test_hidden_staging_look_updates_world_without_inventing_visible_eyes(self):
        data,_=public_world()
        data['initial_world']['points']['观察点']={'scene_id':'ROOM','position_xyz':[0,2,1.75]}
        params={'target':{'kind':'point','id':'观察点'},'tracking':False,'head_mode':'still'}
        data['shots'][0]['actions']=[{'id':'look','kind':'look','actor':'甲','start':5,'end':35,
                                     'after':[],'source_use_id':None,**params}]
        visible=production.lower_production_decisions(data)
        self.assertTrue(visible['typed_decisions']['shots'][0]['events'])
        data['initial_world']['points']['手部取景']={'scene_id':'ROOM','position_xyz':[.1,.3,1.25]}
        data['shots'][0]['camera'].update(position_xyz=[.1,2,1.25],target='手部取景',vfov_deg=20,frame='hands')
        hidden=production.lower_production_decisions(data)
        shot=self.accepted(hidden['typed_decisions'])['shots'][0]
        self.assertIn('眼睛不入画',shot['画面内容'])
        self.assertNotIn('视线',shot['画面内容']); self.assertNotIn('眼球',shot['画面内容'])
        self.assertEqual(hidden['derivation']['final_world']['actors']['甲']['gaze']['ref'],params['target'])
        self.assertEqual(hidden['derivation']['shots'][0]['actions'][0]['projection_status'],'hidden')
        self.assertEqual(hidden['typed_decisions']['shots'][0]['events'],[])
        source=bind_expression(data,kind='look',parameters=params)
        with self.assertRaisesRegex(production.ProductionDecisionError,'source look requires actual eye landmarks'):
            production.lower_production_decisions(data,source_compilation=source)

    def test_generated_frontal_daylight_survives_full_lighting_gate(self):
        world,row=daylight_fixture()
        world['scenes']['YARD']['light']['direction_xyz']=[0,1,1]
        _,fields=render_daylight(world,row)
        self.assertIn('画面正前侧',fields['光源方向'])
        project=_render_decisions(production.lower_production_decisions(expression_fixture())['typed_decisions'])
        shot=project['shots'][0]
        for key in ('光影','光源来源','光源方向','色温倾向','空间站位描述','生成限制'): shot[key]=fields[key]
        self.assertFalse(any(x['error_code']=='E-LIGHT-001' for x in validate_project(project)['issues']))
        for key,value in (('光源方向','主光落向甲面部。'),('光源方向','主光位于画面正前侧。'),
                          ('光源来源','无已建立光源。'),('空间站位描述','未分类地点。')):
            changed=copy.deepcopy(project);changed['shots'][0][key]=value
            with self.subTest(key=key,value=value):
                self.assertTrue(any(x['error_code']=='E-LIGHT-001' for x in validate_project(changed)['issues']))


if __name__=='__main__': unittest.main()
