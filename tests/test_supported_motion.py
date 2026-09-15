"""Public synthetic controls; no private storyboard or audit certificate."""
import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'skills/shortdrama-storyboard/scripts'),str(ROOT/'tests')]
import production_decisions as p
from source_action_plans import source_step
from test_v0527_production_decisions import production_fixture,action
from test_v0527_source_action_plans import compile_plan,physical_source
from author_storyboard import _render_decisions

def lm(h):
    return {'eye_height':h,'reach_origin_offset_xyz':[0,0,max(.3,h-.3)],
            'hands':{'left':[-.2,.3,max(.3,h-.5)],'right':[.2,.3,max(.3,h-.5)]}}

def assistance():
    d=production_fixture();w=d['initial_world'];w['props']={}
    a=w['actors']['甲'];a.update(position_xyz=[.5,0,0],posture='蹲姿',eye_height=.8,
                                reach_origin_xyz=[.5,0,.5],hands=lm(.8)['hands'])
    other=copy.deepcopy(a);other.update(position_xyz=[0,0,0],posture='躺卧',eye_height=.3,
                                       reach_origin_xyz=[0,0,.3],hands=lm(.3)['hands'])
    w['actors']['乙']=other
    w['points']['原位']={'scene_id':'ROOM','position_xyz':[0,0,0]}
    w['points']['施扶位']={'scene_id':'ROOM','position_xyz':[.5,0,0]}
    d['shots'][0]['camera'].update(position_xyz=[.3,4,1.5],hfov_deg=60,vfov_deg=75)
    contact={'hand':'right','target_actor':'乙','target_part':'right_wrist','mode':'support'}
    stand={'hand':'right','target_actor':'乙','target_part':'right_wrist','point':'原位',
           'landmarks':lm(1.5),'helper_point':'施扶位','helper_landmarks':lm(1.5)}
    steps=[{'step_id':'support','kind':'contact_actor','parameters':contact,'after':[]},
           {'step_id':'stand','kind':'assist_stand','parameters':stand,'after':['support']}]
    return d,steps

def bound(d,steps):
    times={'support':(2,6),'stand':(6,26)}
    d['shots'][0]['actions']=[source_step(action_id=x['step_id'],source_use_id='U1',
        step_id=x['step_id'],start=times[x['step_id']][0],end=times[x['step_id']][1],
        after=x['after']) for x in steps]
    return compile_plan(d,[{'source_use_id':'U1','steps':steps}],
                        source=physical_source('甲扶住乙的右手腕，帮助乙起身站稳。'))

class SupportedMotionTests(unittest.TestCase):
    def test_two_people_rise_with_actual_persistent_support_and_real_text(self):
        d,steps=assistance();source=bound(d,steps)
        r=p.lower_production_decisions(d,source_compilation=source);w=r['derivation']['final_world']
        for name in ('甲','乙'):
            self.assertEqual(w['actors'][name]['posture'],'站立')
            self.assertEqual(w['actors'][name]['eye_height'],1.5)
        self.assertEqual(w['actors']['甲']['action_state']['contacts']['right'],
                         {'actor':'乙','part':'right_wrist','mode':'support'})
        self.assertEqual(p._hand(w['actors']['甲'],'right'),p._hand(w['actors']['乙'],'right'))
        c=_render_decisions(r['typed_decisions'])
        self.assertIn('扶起乙',c['shots'][0]['画面内容'])
        self.assertFalse(r['derivation']['commit_eligible'])

    def test_missing_wrong_or_grasp_contact_cannot_be_borrowed(self):
        for change in ('missing','hand','grasp','binding','far'):
            with self.subTest(change=change):
                d,steps=assistance()
                if change=='missing':steps=steps[1:];steps[0]['after']=[]
                if change=='hand':steps[1]['parameters']['hand']='left'
                if change=='grasp':steps[0]['parameters']['mode']='grasp'
                if change=='binding':
                    d['initial_world']['props']['绳']={'anchor':{'actor':'乙','binding':'wrists'},'written_ref':None}
                if change=='far':d['initial_world']['points']['原位']['position_xyz']=[5,0,0]
                source=bound(d,steps)
                with self.assertRaises(p.ProductionDecisionError):
                    p.lower_production_decisions(d,source_compilation=source)

    def test_supported_cup_moves_with_tray_without_claiming_second_hand(self):
        d=production_fixture();w=d['initial_world'];w['props']={
          '托盘':{'anchor':{'actor':'甲','hand':'right'},'written_ref':None},
          '茶杯':{'anchor':{'prop':'托盘','offset_xyz':[0,0,.05]},'written_ref':None}}
        w['points']['抬高手']={'scene_id':'ROOM','position_xyz':[.3,.3,1.4]}
        d['shots'][0]['actions']=[action('hand_move',hand='right',to='抬高手')]
        r=p.lower_production_decisions(d);end=r['derivation']['final_world']
        self.assertEqual(p._anchor(end,end['props']['茶杯'])[1],[.3,.3,1.45])
        self.assertEqual(end['props']['茶杯']['anchor'],{'prop':'托盘','offset_xyz':[0,0,.05]})
        self.assertEqual(len(p._state(end)['手持道具']),1)

    def test_cup_pickup_detaches_from_carrier_and_keeps_original_holder(self):
        d=production_fixture();w=d['initial_world'];w['props']={
            '托盘':{'anchor':{'actor':'甲','hand':'left'},'written_ref':None},
            '茶杯':{'anchor':{'prop':'托盘','offset_xyz':[.1,0,0]},'written_ref':None}}
        w['actors']['甲']['hands']['right']=[-.1,.3,1]
        steps=[{'step_id':'cup','kind':'pick_up','parameters':{'prop':'茶杯','hand':'right'},'after':[]}]
        d['shots'][0]['actions']=[source_step(action_id='cup',source_use_id='U1',step_id='cup',start=10,end=30)]
        source=compile_plan(d,[{'source_use_id':'U1','steps':steps}],source=physical_source('甲从托盘中端起茶杯。'))
        result=p.lower_production_decisions(d,source_compilation=source);w=result['derivation']['final_world']
        self.assertEqual(w['props']['茶杯']['anchor'],{'actor':'甲','hand':'right'})
        self.assertEqual(w['props']['托盘']['anchor'],{'actor':'甲','hand':'left'})

    def test_bad_carriers_and_offsets_fail_closed(self):
        for mode in ('missing','self','cycle','offset','nonfinite','broken'):
            d=production_fixture();w=d['initial_world'];w['props']={
                '托盘':{'anchor':{'point':'案左'},'written_ref':None},
                '茶杯':{'anchor':{'prop':'托盘','offset_xyz':[0,0,.1]},'written_ref':None}}
            if mode=='missing':w['props']['茶杯']['anchor']['prop']='不存在'
            if mode=='self':w['props']['茶杯']['anchor']['prop']='茶杯'
            if mode=='cycle':w['props']['托盘']['anchor']={'prop':'茶杯','offset_xyz':[0,0,.1]}
            if mode=='offset':w['props']['茶杯']['anchor']['offset_xyz']=[0,0,3]
            if mode=='nonfinite':w['props']['茶杯']['anchor']['offset_xyz']=[0,0,float('nan')]
            if mode=='broken':w['props']['托盘']['physical']={'material':'wood','contents':'none','integrity':'broken'}
            with self.subTest(mode=mode),self.assertRaises(p.ProductionDecisionError):
                p.lower_production_decisions(d)

    def test_source_protected_cup_cannot_be_moved_through_unbound_tray(self):
        d=production_fixture();w=d['initial_world'];w['props']={
            '托盘':{'anchor':{'point':'案左'},'written_ref':None},
            '茶杯':{'anchor':{'prop':'托盘','offset_xyz':[0,0,.05]},'written_ref':None}}
        steps=[{'step_id':'cup','kind':'pick_up','parameters':{'prop':'茶杯','hand':'right'},'after':[]}]
        d['shots'][0]['actions']=[
            action('move_prop',prop='托盘',hand='right',to='案右',start=0,end=5),
            source_step(action_id='cup',source_use_id='U1',step_id='cup',start=10,end=30)]
        source=compile_plan(d,[{'source_use_id':'U1','steps':steps}],source=physical_source('甲端起茶杯。'))
        with self.assertRaisesRegex(p.ProductionDecisionError,'source-protected'):
            p.lower_production_decisions(d,source_compilation=source)

if __name__=='__main__':unittest.main()
