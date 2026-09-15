"""Independent expected outcomes for common drama action dependencies."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'skills/shortdrama-storyboard/scripts'),str(ROOT/'tests')]
import production_decisions as p
from author_storyboard import _render_decisions
from source_action_plans import source_step
from test_v0527_production_decisions import production_fixture,action,voice_spec
from test_v0527_source_action_plans import compile_plan,physical_source
from voice_sequence import build_voice_sequence
from validate_storyboard import _voice_template_errors,VOICE_TEMPLATE_LABELS,_parse_voice_template


def sourced(data,kind,parameters,*,text='甲完成所述动作。',start=10,end=30):
    events=[{'source_use_id':'U1','steps':[{'step_id':'event','kind':kind,'parameters':parameters,'after':[]}]}]
    data['shots'][0]['actions']=[source_step(action_id='event1',source_use_id='U1',step_id='event',start=start,end=end)]
    return compile_plan(data,events,source=physical_source(text))


def pose(to='跪姿',height=.9):
    return {'to':to,'point':'地面','landmarks':{'eye_height':height,'reach_origin_offset_xyz':[0,0,height-.2],
            'hands':{'left':[-.2,.3,.5],'right':[.2,.3,.5]}}}


def next_shot(data,scene='ROOM',relation='continuous'):
    row=copy.deepcopy(data['shots'][0]);row.update(shot_id='EP90-002',shot_uid='SECOND',actions=[],scene_id=scene)
    row['transition']={'relation':relation,'basis':'同一故事的后续明确时空。'}
    if relation!='continuous':row['continuity_id']='NEXT'
    data['shots'].append(row);return row


def drop_fixture():
    data=production_fixture();w=data['initial_world']
    w['props']={'茶盏':{'anchor':{'actor':'甲','hand':'right'},'written_ref':None,
                       'physical':{'material':'ceramic','contents':'hot_water','integrity':'intact'}}}
    other=copy.deepcopy(w['actors']['甲']);other.update(position_xyz=[.32,.3,0],reach_origin_xyz=[.32,.3,1.2])
    w['actors']['乙']=other;w['points']['脚边']={'scene_id':'ROOM','position_xyz':[.2,.3,.05]}
    params={'prop':'茶盏','hand':'right','to':'脚边','contact':{'actor':'乙','part':'left_foot'},'breaks':False}
    return data,params


class SourceActionsTests(unittest.TestCase):
    def test_kneeling_changes_landmarks_and_next_frame_without_overwriting_first(self):
        data=production_fixture();data['initial_world']['points']['地面']={'scene_id':'ROOM','position_xyz':[0,0,0]}
        source=sourced(data,'posture',pose(),text='甲跪在地上。',start=5,end=35)
        next_shot(data)
        result=p.lower_production_decisions(data,source_compilation=source);rows=result['typed_decisions']['shots']
        self.assertEqual(rows[0]['start_state']['人物位置']['甲']['posture'],'站立')
        self.assertEqual(rows[0]['end_state']['人物位置']['甲']['posture'],'跪姿')
        self.assertEqual(rows[0]['end_state'],rows[1]['start_state'])
        self.assertAlmostEqual(result['derivation']['final_world']['actors']['甲']['eye_height'],.9)
        canonical=_render_decisions(result['typed_decisions'])
        self.assertIn('跪姿',canonical['shots'][1]['起始状态'])
        self.assertNotIn('落在地面',rows[0]['phases'][0]['text'])

    def test_pose_cannot_teleport_or_use_standing_eye_height_for_kneel(self):
        for position,height in [([5,0,0],.9),([0,0,0],1.9)]:
            data=production_fixture();data['initial_world']['points']['地面']={'scene_id':'ROOM','position_xyz':position}
            data['shots'][0]['actions']=[action('posture',**pose(height=height))]
            with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data)

    def test_source_result_corruption_and_extra_mutation_are_rejected(self):
        for corrupt in ['wrong_pose','extra_actor_change']:
            data=production_fixture();data['initial_world']['points']['地面']={'scene_id':'ROOM','position_xyz':[0,0,0]}
            source=sourced(data,'posture',pose(),text='甲跪下。')
            real=p._apply
            def faulty(world,a,info,tick,path):
                real(world,a,info,tick,path)
                if tick==a['end']:
                    if corrupt=='wrong_pose':world['actors']['甲']['posture']='站立'
                    else:world['actors']['甲']['body_yaw']=90
            with patch.object(p,'_apply',faulty),self.assertRaisesRegex(p.ProductionDecisionError,'effect|state'):
                p.lower_production_decisions(data,source_compilation=source)

    def test_drop_has_real_ownership_landing_water_state_and_contact_time(self):
        data,params=drop_fixture();source=sourced(data,'drop_prop',params,text='甲松开茶盏，热水洒在乙的左脚背。',start=5,end=35)
        data['shots'][0]['editorial']['环境音效']={'mode':'action_bound','cues':[{'action_id':'event1','sound':'drop_impact','volume':'normal'}]}
        next_shot(data);data['shots'][1]['editorial']['环境音效']={'mode':'none'}
        result=p.lower_production_decisions(data,source_compilation=source);w=result['derivation']['final_world']
        self.assertEqual(w['props']['茶盏']['anchor'],{'point':'脚边'})
        self.assertEqual(w['props']['茶盏']['physical'],{'material':'ceramic','contents':'none','integrity':'intact'})
        self.assertEqual(w['actors']['乙']['action_state']['wet_parts'],['left_foot'])
        self.assertEqual(w['actors']['甲']['action_state']['wet_parts'],[])
        sound=result['derivation']['shots'][0]['environment_binding']['sound_bindings'][0]
        self.assertEqual(sound['contact_ticks'],[35]);self.assertNotIn('0.5秒',sound['rendered_text'])
        self.assertNotIn('热水洒在',result['typed_decisions']['shots'][0]['phases'][0]['text'])
        canonical=_render_decisions(result['typed_decisions'])
        self.assertIn('左脚背留有水渍',canonical['shots'][1]['起始状态'])

    def test_drop_refuses_wrong_holder_missed_contact_and_fake_break_material(self):
        for change in ['holder','contact','material']:
            data,params=drop_fixture()
            if change=='holder':params['hand']='left'
            if change=='contact':params['contact']['part']='face'
            if change=='material':data['initial_world']['props']['茶盏']['physical']['material']='cloth';params['breaks']=True
            source=sourced(data,'drop_prop',params)
            with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data,source_compilation=source)

    def test_source_actions_cannot_be_declared_as_null_staging(self):
        data,params=drop_fixture();data['shots'][0]['actions']=[action('drop_prop',**params)]
        with self.assertRaisesRegex(p.ProductionDecisionError,'frozen physical source use'):p.lower_production_decisions(data)

    def test_slap_requires_reached_face_and_sound_is_not_a_grasp_sound(self):
        for mode,part,sound,ok in [('slap','face','slap',True),('grasp','left_wrist','slap',False)]:
            data=production_fixture();w=data['initial_world'];other=copy.deepcopy(w['actors']['甲'])
            other.update(position_xyz=[.4,0,0],reach_origin_xyz=[.4,0,1.2]);w['actors']['乙']=other
            if part=='face':w['actors']['甲']['hands']['right']=[.2,0,1.38]
            source=sourced(data,'contact_actor',{'hand':'right','target_actor':'乙','target_part':part,'mode':mode})
            data['shots'][0]['editorial']['环境音效']={'mode':'action_bound','cues':[{'action_id':'event1','sound':sound,'volume':'normal'}]}
            if ok:
                result=p.lower_production_decisions(data,source_compilation=source)
                self.assertEqual(result['derivation']['shots'][0]['environment_binding']['sound_bindings'][0]['contact_ticks'],[30])
                self.assertIn('乙面颊',result['typed_decisions']['shots'][0]['fields']['环境音效'])
            else:
                with self.assertRaisesRegex(p.ProductionDecisionError,'slap sound'):p.lower_production_decisions(data,source_compilation=source)

    def test_grasp_must_be_released_before_the_gripped_hand_moves(self):
        data=production_fixture();w=data['initial_world'];other=copy.deepcopy(w['actors']['甲'])
        other.update(position_xyz=[.4,0,0],reach_origin_xyz=[.4,0,1.2]);w['actors']['乙']=other
        source=sourced(data,'contact_actor',{'hand':'right','target_actor':'乙','target_part':'left_wrist','mode':'grasp'})
        row=next_shot(data);row['actions']=[action('hand_move',id='escape',actor='乙',hand='left',to='案右')]
        with self.assertRaisesRegex(p.ProductionDecisionError,'grasp/support'):p.lower_production_decisions(data,source_compilation=source)

    def test_two_claps_preserve_exact_count_and_two_contact_instants(self):
        data=production_fixture();source=sourced(data,'clap',{'count':2},text='甲拍了两下手。',start=10,end=30)
        data['shots'][0]['editorial']['环境音效']={'mode':'action_bound','cues':[{'action_id':'event1','sound':'handclaps','volume':'normal'}]}
        result=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['action_state']['claps'],2)
        self.assertEqual(result['derivation']['shots'][0]['environment_binding']['sound_bindings'][0]['contact_ticks'],[15,25])

    def test_kowtow_requires_kneeling_and_finishes_upright_on_knees(self):
        for kneeling in [False,True]:
            data=production_fixture();a=data['initial_world']['actors']['甲']
            if kneeling:a.update(posture='跪姿',eye_height=.9)
            source=sourced(data,'kowtow',{'count':2},text='甲跪地磕头两下。',start=10,end=30)
            if not kneeling:
                with self.assertRaisesRegex(p.ProductionDecisionError,'ground kneel'):p.lower_production_decisions(data,source_compilation=source)
            else:
                result=p.lower_production_decisions(data,source_compilation=source)
                self.assertAlmostEqual(result['derivation']['final_world']['actors']['甲']['eye_height'],.9)
                self.assertEqual(result['derivation']['final_world']['actors']['甲']['action_state']['kowtows'],2)

    def test_worn_crown_is_removed_into_the_correct_hand(self):
        data=production_fixture();w=data['initial_world'];w['actors']['甲']['hands']['right']=[0,0,1.5]
        w['props']={'凤冠':{'anchor':{'actor':'甲','worn':'head'},'written_ref':None}}
        source=sourced(data,'remove_worn',{'prop':'凤冠','hand':'right'},text='甲取下头上凤冠。')
        result=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['props']['凤冠']['anchor'],{'actor':'甲','hand':'right'})
        self.assertEqual(result['typed_decisions']['shots'][0]['end_state']['手持道具'][0]['holder'],'甲')

    def test_declared_doorway_changes_room_without_replacing_actor(self):
        data=production_fixture();w=data['initial_world'];w['scenes']['ROOM2']=copy.deepcopy(w['scenes']['ROOM'])
        w['scenes']['ROOM']['door_window_state']+='门开启。'
        w['points']['门内']={'scene_id':'ROOM','position_xyz':[0,0,0]};w['points']['门外']={'scene_id':'ROOM2','position_xyz':[0,.1,0]}
        w['scenes']['ROOM']['passages']=[{'id':'过门','exit':'门内','entry':'门外','opening':'门'}]
        source=sourced(data,'cross_passage',{'passage':'过门'},text='甲由房内跨过门口。')
        result=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['scene_id'],'ROOM2')
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['position_xyz'],[0,.1,0])
        w['scenes']['ROOM']['door_window_state']='东纸窗关闭。门关闭。'
        source=sourced(data,'cross_passage',{'passage':'过门'})
        with self.assertRaisesRegex(p.ProductionDecisionError,'opening is closed'):p.lower_production_decisions(data,source_compilation=source)

    def test_bridge_moves_offscreen_but_has_zero_screen_time_and_no_source_credit(self):
        data=production_fixture();row=next_shot(data,relation='later_same_place')
        row['transition']['bridge']={'ticks':20,'actions':[action('move_actor',id='between',start=0,end=20,to='落脚处')]}
        result=p.lower_production_decisions(data);rows=result['typed_decisions']['shots']
        self.assertEqual([r['duration'] for r in rows],[4,4])
        self.assertEqual(rows[1]['start_state']['人物位置']['甲']['position_xyz'],[1,0,0])
        bridge=result['derivation']['shots'][1]['inter_shot_bridge']
        self.assertEqual(bridge['screen_seconds'],0);self.assertFalse(bridge['source_delivery'])
        self.assertNotIn('between',str(rows[1]['phases']))

    def test_bridge_refuses_impacts_and_continuous_cut_reset(self):
        for kind,relation in [('move_actor','continuous'),('clap','later_same_place')]:
            data=production_fixture();row=next_shot(data,relation=relation)
            a=action(kind,id='between',start=0,end=20,**({'to':'落脚处'} if kind=='move_actor' else {'count':2}))
            row['transition']['bridge']={'ticks':20,'actions':[a]}
            with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data)

    def test_voice_sequence_keeps_single_template_and_exact_speaker_order(self):
        v=voice_spec();other=copy.deepcopy(v);other.update(baseline='年龄感为成年，高音区，偏薄，明亮，常态清楚。')
        rows=[('甲','明日再议。'),('乙','明日？'),('甲','明日。')]
        block=build_voice_sequence([(a,t,other if a=='乙' else v) for a,t in rows])
        self.assertEqual(_parse_voice_template(block)[1],list(VOICE_TEMPLATE_LABELS))
        self.assertEqual(_voice_template_errors(block,''.join(t for _,t in rows),rows),[])
        bad=block.replace('〔2:乙〕','〔2:甲〕',1)
        self.assertTrue(_voice_template_errors(bad,''.join(t for _,t in rows),rows))

    def test_fall_keeps_involuntary_verb_and_actual_lying_landmarks(self):
        data=production_fixture();data['initial_world']['points']['地面']={'scene_id':'ROOM','position_xyz':[0,0,0]}
        spec=pose('躺卧',.3);spec.pop('to')
        source=sourced(data,'fall',spec,text='甲失去平衡跌倒。')
        result=p.lower_production_decisions(data,source_compilation=source)
        row=_render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('跌倒',row['画面内容']);self.assertNotIn('躺下',row['画面内容'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['posture'],'躺卧')

    def test_dressing_changes_the_recipient_not_the_helper(self):
        data=production_fixture();w=data['initial_world'];w['actors']['乙']=copy.deepcopy(w['actors']['甲'])
        w['actors']['乙'].update(position_xyz=[.2,.3,0],reach_origin_xyz=[.2,.3,1.0])
        w['props']={'外袍':{'anchor':{'actor':'甲','hand':'right'},'written_ref':None,'physical':{'material':'cloth','contents':'none','integrity':'intact'}}}
        source=sourced(data,'dress_actor',{'prop':'外袍','hand':'right','target_actor':'乙','part':'torso'},text='甲给乙穿上外袍。')
        r=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(r['derivation']['final_world']['props']['外袍']['anchor'],{'actor':'乙','worn':'torso'})
        self.assertIn('为乙穿戴外袍',_render_decisions(r['typed_decisions'])['shots'][0]['画面内容'])

    def test_opening_uses_a_real_reached_opening_and_updates_next_frame(self):
        data=production_fixture();w=data['initial_world'];w['scenes']['ROOM']['door_window_state']+='门帘关闭。'
        w['points']['门帘']={'scene_id':'ROOM','position_xyz':[.2,.3,1]}
        source=sourced(data,'set_opening',{'opening':'门帘','to':'open','hand':'right'},text='甲掀开门帘。')
        next_shot(data);r=p.lower_production_decisions(data,source_compilation=source)
        self.assertIn('门帘开启',r['derivation']['final_world']['scenes']['ROOM']['door_window_state'])
        self.assertIn('门帘开启',str(r['typed_decisions']['shots'][1]['start_state']))
        _render_decisions(r['typed_decisions'])

    def test_invisible_contact_cannot_claim_source_delivery(self):
        data,params=drop_fixture();data['shots'][0]['camera']['vfov_deg']=10
        source=sourced(data,'drop_prop',params)
        with self.assertRaisesRegex(p.ProductionDecisionError,'landmarks in frame'):p.lower_production_decisions(data,source_compilation=source)

    def test_contact_target_cannot_move_during_a_drop(self):
        data,params=drop_fixture();source=sourced(data,'drop_prop',params)
        data['shots'][0]['actions'].append(action('move_actor',id='evade',actor='乙',to='落脚处'))
        with self.assertRaisesRegex(p.ProductionDecisionError,'overlapping writes'):p.lower_production_decisions(data,source_compilation=source)

    def test_malformed_extended_inputs_fail_as_domain_errors(self):
        for spec in [action('hand_move',to=[],hand='right'),action('posture',**{**pose(),'to':{}}),action('contact_actor',hand='right',target_actor=[],target_part='face',mode='slap')]:
            data=production_fixture();data['shots'][0]['actions']=[spec]
            with self.assertRaises(p.ProductionDecisionError):p.lower_production_decisions(data)

    def test_footsteps_use_actual_standing_state_after_getting_up(self):
        data=production_fixture();w=data['initial_world'];w['actors']['甲']['posture']='躺卧';w['actors']['甲']['eye_height']=.3
        w['points']['地面']={'scene_id':'ROOM','position_xyz':[0,0,0]}
        data['shots'][0]['actions']=[action('posture',id='rise',start=0,end=10,**pose('站立',1.5)),
                                    action('move_actor',id='walk',start=10,end=30,to='落脚处',after=['rise'])]
        data['shots'][0]['editorial']['环境音效']={'mode':'action_bound','cues':[{'action_id':'walk','sound':'footsteps','volume':'soft'}]}
        r=p.lower_production_decisions(data)
        self.assertIn('脚步声',r['typed_decisions']['shots'][0]['fields']['环境音效'])

    def test_bridge_light_change_is_frozen_state_and_not_a_visible_plot_action(self):
        data=production_fixture();row=next_shot(data,relation='later_same_place')
        light=copy.deepcopy(data['initial_world']['scenes']['ROOM']['light']);light['main_tone']='warm'
        row['transition']['bridge']={'ticks':10,'actions':[action('scene_light',id='morning',start=0,end=10,scene_id='ROOM',light=light)]}
        r=p.lower_production_decisions(data)
        self.assertEqual(r['derivation']['final_world']['scenes']['ROOM']['light']['main_tone'],'warm')
        self.assertNotIn('morning',r['typed_decisions']['shots'][1]['phases'][0]['text'])

    def test_release_binding_preserves_restriction_until_actual_release(self):
        data=production_fixture();w=data['initial_world'];w['actors']['乙']=copy.deepcopy(w['actors']['甲'])
        w['props']={'绳索':{'anchor':{'actor':'乙','binding':'wrists'},'written_ref':None}}
        source=sourced(data,'release_binding',{'prop':'绳索','hand':'right','target_actor':'乙'},text='甲解开乙双腕上的绳索。')
        r=p.lower_production_decisions(data,source_compilation=source)
        self.assertEqual(r['derivation']['final_world']['props']['绳索']['anchor'],{'actor':'甲','hand':'right'})
        self.assertIn('捆住乙双腕',_render_decisions(r['typed_decisions'])['shots'][0]['起始状态'])
        data['shots'][0]['actions'].insert(0,action('hand_move',id='tooearly',actor='乙',hand='left',to='案右',start=0,end=5))
        with self.assertRaisesRegex(p.ProductionDecisionError,'wrist binding'):p.lower_production_decisions(data,source_compilation=source)

    def test_full_frozen_plan_replays_bridge_and_refuses_tampering(self):
        import json
        from test_v0527_source_decision_independent_audit import complete_source_input
        from production_plan_test_support import frozen_plan
        from test_v0527_source_decisions import build
        from source_decisions import SourceDecisionError
        data,raw,texts=complete_source_input();g=json.loads(raw);row=next_shot(data,relation='later_same_place')
        row['transition']['bridge']={'ticks':20,'actions':[action('move_actor',id='travel',start=0,end=20,to='落脚处')]}
        g['production_plan']=frozen_plan(data,g,texts)
        c=build(g,data['project']['prevention_evidence']['source_decisions'],texts)
        r=p.lower_production_decisions(data,source_compilation=c)
        self.assertEqual(r['derivation']['production_prevention']['status'],'pass')
        row['transition']['bridge']['actions'][0]['end']=10
        with self.assertRaisesRegex((p.ProductionDecisionError,SourceDecisionError),'frozen'):p.lower_production_decisions(data,source_compilation=c)


if __name__=='__main__':unittest.main()
