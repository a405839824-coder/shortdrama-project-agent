"""Causal contact/displacement controls, not a source-semantic certificate."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'skills/shortdrama-storyboard/scripts'), str(ROOT/'tests')]
import production_decisions as p
from author_storyboard import _render_decisions
from source_action_plans import source_step, SourceActionPlanError
from test_v0527_production_decisions import production_fixture, action
from test_v0527_source_action_plans import compile_plan, physical_source
from test_v0537_source_actions import next_shot


def fixture():
    data = production_fixture(); world = data['initial_world']; world['props'] = {}
    world['scenes']['ROOM']['opening_labels'] = {'东纸窗':'纸窗'}
    target = copy.deepcopy(world['actors']['甲'])
    target.update(position_xyz=[.2,.45,0], reach_origin_xyz=[.2,.45,1.2])
    world['actors']['乙'] = target
    world['points']['旁侧空位'] = {'scene_id':'ROOM','position_xyz':[.35,.65,0]}
    data['shots'][0]['camera'].update(position_xyz=[.25,4,1.5], hfov_deg=60,vfov_deg=75)
    parameters = {'hand':'right','target_actor':'乙','target_part':'torso','point':'旁侧空位'}
    return data, parameters


def bind(data, parameters, *, start=10, end=30):
    steps = [{'step_id':'push','kind':'push_actor','parameters':parameters,'after':[]}]
    data['shots'][0]['actions'] = [source_step(action_id='push1',source_use_id='U1',step_id='push',start=start,end=end)]
    return compile_plan(data,[{'source_use_id':'U1','steps':steps}],source=physical_source('甲推开乙。'))


class PushActorTests(unittest.TestCase):
    def test_contact_precedes_passive_motion_and_release_retains_final_state(self):
        data, parameters = fixture(); source = bind(data,parameters); before = copy.deepcopy(data)
        snapshots = {}; real = p._apply
        def record(world, action, info, tick, path):
            real(world,action,info,tick,path)
            if action['id'] == 'push1': snapshots[tick] = copy.deepcopy(world)
        with patch.object(p,'_apply',record):
            result = p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(data,before)
        self.assertEqual(snapshots[14]['actors']['乙']['position_xyz'],[.2,.45,0])
        self.assertEqual(snapshots[14]['actors']['甲']['action_state']['contacts'],{})
        contact = snapshots[15]['actors']['甲']['action_state']['contacts']['right']
        self.assertEqual(contact,{'actor':'乙','part':'torso','mode':'push'})
        self.assertEqual(snapshots[15]['actors']['乙']['position_xyz'],[.2,.45,0])
        for actual,expected in zip(snapshots[20]['actors']['乙']['position_xyz'],[.275,.55,0]):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(p._hand(snapshots[20]['actors']['甲'],'right'),[.275,.55,1.2])
        final = result['derivation']['final_world']
        self.assertEqual(final['actors']['乙']['position_xyz'],[.35,.65,0])
        self.assertEqual(final['actors']['甲']['position_xyz'],[0,0,0])
        self.assertEqual(final['actors']['甲']['action_state']['contacts'],{})
        self.assertEqual(p._hand(final['actors']['甲'],'right'),[.2,.3,1])
        self.assertEqual(final['actors']['乙']['posture'],'站立')
        self.assertEqual(final['completed'].count('push1'),1)
        text = _render_decisions(result['typed_decisions'])['shots'][0]['画面内容']
        self.assertIn('乙被推至',text)
        self.assertNotIn('跌倒',text); self.assertNotIn('扇向',text); self.assertNotIn('让开',text)

    def test_next_shot_inherits_result_and_owner_can_then_walk(self):
        data, parameters = fixture(); source = bind(data,parameters)
        data['shots'][0]['actions'].append(action('move_actor',id='leave',start=30,end=40,to='落脚处',after=['push1']))
        next_shot(data)
        result = p.lower_production_decisions(data,source_compilation=source)
        rows = result['typed_decisions']['shots']
        self.assertEqual(rows[0]['end_state'],rows[1]['start_state'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['position_xyz'],[1,0,0])
        self.assertEqual(result['derivation']['final_world']['actors']['乙']['position_xyz'],[.35,.65,0])

    def test_sideways_push_is_not_forced_into_a_backward_shove(self):
        data,parameters=fixture()
        data['initial_world']['points']['旁侧空位']['position_xyz']=[.425,.35,0]
        source=bind(data,parameters)
        result=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['actors']['乙']['position_xyz'],[.425,.35,0])

    def test_bad_endpoints_and_missing_contact_cannot_claim_push(self):
        for name,xyz in [('no_change',[.2,.45,0]),('pull',[.1,.3,0]),('long',[1.5,.7,0]),
                         ('height',[.35,.65,.1]),('out_of_reach',[.2,1.,0])]:
            with self.subTest(name=name):
                data,parameters = fixture();data['initial_world']['points']['旁侧空位']['position_xyz'] = xyz
                source=bind(data,parameters)
                with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data,source_compilation=source)
        for change in ('wrong_scene','missing_point','self','face','sitting','fast','occupied','bound','closed_hand'):
            with self.subTest(change=change):
                data,parameters = fixture();world=data['initial_world']
                if change=='wrong_scene':world['points']['旁侧空位']['scene_id']='OTHER'
                if change=='missing_point':parameters['point']='不存在'
                if change=='self':parameters['target_actor']='甲'
                if change=='face':parameters['target_part']='face'
                if change=='sitting':world['actors']['乙']['posture']='坐姿'
                if change=='occupied':world['props']['杯']={'anchor':{'actor':'甲','hand':'right'},'written_ref':None}
                if change=='bound':world['props']['绳']={'anchor':{'actor':'乙','binding':'wrists'},'written_ref':None}
                if change=='closed_hand':
                    import production_actions
                    production_actions.defaults(world);world['actors']['甲']['action_state']['hand_shapes']['right']='fist'
                source=bind(data,parameters,end=15 if change=='fast' else 30)
                with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data,source_compilation=source)

    def test_body_and_hand_constraints_cannot_overlap_or_replace_contact(self):
        for who,kind,kwargs in [('甲','move_actor',{'to':'落脚处'}),('乙','move_actor',{'to':'落脚处'}),
                                ('甲','hand_move',{'hand':'right','to':'案左'}),('乙','hand_move',{'hand':'left','to':'案左'})]:
            with self.subTest(who=who,kind=kind):
                data,parameters=fixture();source=bind(data,parameters)
                data['shots'][0]['actions'].append(action(kind,id='conflict',actor=who,**kwargs))
                with self.assertRaisesRegex(p.ProductionDecisionError,'overlapping writes'):
                    p.lower_production_decisions(data,source_compilation=source)

    def test_actual_constraint_and_barrier_are_checked_before_push(self):
        data,parameters=fixture();source=bind(data,parameters)
        # An earlier real support contact must be released, even though no
        # scheduled interval overlaps the push.
        import production_actions as actions
        world=p._world(data['initial_world'],p._source(source))
        world['actors']['甲']['action_state']['contacts']['left']={'actor':'乙','part':'left_wrist','mode':'support'}
        spec=action('push_actor',source_use_id='U1',**parameters)
        with self.assertRaisesRegex(p.ProductionDecisionError,'release existing body contacts'):
            actions.prepare(world,spec,{},'push')
        for y in (.2,.55):
            with self.subTest(barrier_y=y):
                world=p._world(data['initial_world'],p._source(source))
                world['scenes']['ROOM']['barriers']=[{'id':'隔挡','a':[-1,y,0],'b':[1,y,0],'height_m':2}]
                with self.assertRaisesRegex(p.ProductionDecisionError,'barrier blocks'):
                    actions.prepare(world,spec,{},'push')

    def test_source_step_is_once_and_cannot_be_replaced_by_voluntary_movement(self):
        for change in ('duplicate','replace','unbound','bridge'):
            with self.subTest(change=change):
                data,parameters=fixture();source=bind(data,parameters)
                if change=='duplicate':
                    other=copy.deepcopy(data['shots'][0]['actions'][0]);other.update(id='push2',start=30,end=40)
                    data['shots'][0]['actions'].append(other)
                if change=='replace':data['shots'][0]['actions']=[action('move_actor',actor='乙',to='旁侧空位')]
                if change=='unbound':data['shots'][0]['actions']=[action('push_actor',**parameters)]
                if change=='bridge':
                    row=next_shot(data,relation='later_same_place')
                    row['transition']['bridge']={'ticks':20,'actions':[action('push_actor',id='hidden',start=0,end=20,**parameters)]}
                with self.assertRaises((p.ProductionDecisionError,SourceActionPlanError)):
                    p.lower_production_decisions(data,source_compilation=source)

    def test_corrupt_target_result_or_extra_fall_is_rejected(self):
        for change in ('position','posture'):
            with self.subTest(change=change):
                data,parameters=fixture();source=bind(data,parameters);real=p._apply
                def faulty(world,action,info,tick,path):
                    real(world,action,info,tick,path)
                    if tick==action['end']:
                        if change=='position':world['actors']['乙']['position_xyz']=[5,0,0]
                        else:world['actors']['乙']['posture']='躺卧'
                with patch.object(p,'_apply',faulty),self.assertRaisesRegex(p.ProductionDecisionError,'effect|state'):
                    p.lower_production_decisions(data,source_compilation=source)

    def test_tight_face_frame_cannot_deliver_whole_passive_displacement(self):
        data,parameters=fixture();source=bind(data,parameters)
        data['shots'][0]['camera']['vfov_deg']=20
        with self.assertRaisesRegex(p.ProductionDecisionError,'landmarks in frame'):
            p.lower_production_decisions(data,source_compilation=source)


if __name__=='__main__':unittest.main()
