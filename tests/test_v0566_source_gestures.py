"""Source-preserving small gestures; geometry is a finite landmark model."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT/'skills/shortdrama-storyboard/scripts')]
import production_decisions as p
from author_storyboard import _render_decisions
from test_v0527_production_decisions import production_fixture, action
from test_v0537_source_actions import sourced, next_shot


class SourceGestureTests(unittest.TestCase):
    def test_sigh_keeps_the_source_word_sound_and_excludes_overlapping_speech(self):
        data = production_fixture()
        source = sourced(data, 'breath', {'phase':'sigh'}, text='甲轻轻叹气。')
        data['shots'][0]['editorial']['环境音效'] = {'mode':'action_bound','cues':[
            {'action_id':'event1','sound':'sigh','volume':'soft'}]}
        result = p.lower_production_decisions(data, source_compilation=source)
        shot = _render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('甲轻轻叹气', shot['画面内容'])
        self.assertIn('1.0—3.0秒：甲轻轻叹气的声音', shot['环境音效'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['performance_state']['breath'], 'resting')
        self.assertIn(('甲','voice'), p._channels(action('breath', phase='sigh')))
        self.assertIn(('audio_bus','primary'), p._channels(action('breath', phase='sigh')))
        source = sourced(data, 'breath', {'phase':'exhale'}, text='甲呼气。')
        with self.assertRaisesRegex(p.ProductionDecisionError, 'sigh sound requires'):
            p.lower_production_decisions(data, source_compilation=source)

    def test_hidden_hand_contact_remains_in_state_but_not_first_frame_picture(self):
        import production_actions as actions
        actor = production_fixture()['initial_world']['actors']['甲']
        actions.defaults({'actors':{'甲':actor},'props':{}})
        actor['action_state']['hand_shapes']['left'] = 'fist'
        actor['action_state']['contacts']['right'] = {'actor':'乙','part':'left_upper_arm','mode':'support'}
        state = copy.deepcopy(actor)
        hidden = actions.static_actor(actor, '甲', view={'actors':{'甲':{'hands':[]}}})
        self.assertNotIn('握拳', hidden)
        self.assertNotIn('扶住', hidden)
        shown = actions.static_actor(actor, '甲', view={'actors':{'甲':{'hands':['left','right']}}})
        self.assertIn('左手握拳', shown)
        self.assertIn('右手仍扶住乙左上臂', shown)
        self.assertEqual(actor, state)

    def test_tremor_keeps_free_hand_and_returns_to_its_actual_position(self):
        data = production_fixture()
        source = sourced(data, 'hand_tremor', {'hand': 'right'}, text='甲右手指尖微颤。')
        next_shot(data)
        result = p.lower_production_decisions(data, source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['hands'], data['initial_world']['actors']['甲']['hands'])
        self.assertIn('右手指尖微颤', _render_decisions(result['typed_decisions'])['shots'][0]['画面内容'])
        self.assertNotIn('微颤', _render_decisions(result['typed_decisions'])['shots'][1]['起始状态'])

    def test_tremor_cannot_be_added_without_source_or_with_an_occupied_hand(self):
        data = production_fixture()
        data['shots'][0]['actions'] = [action('hand_tremor', hand='right')]
        with self.assertRaisesRegex(p.ProductionDecisionError, 'frozen physical source'):
            p.lower_production_decisions(data)
        data['initial_world']['props']['信纸']['anchor'] = {'actor': '甲', 'hand': 'right'}
        source = sourced(data, 'hand_tremor', {'hand': 'right'})
        with self.assertRaisesRegex(p.ProductionDecisionError, 'occupied'):
            p.lower_production_decisions(data, source_compilation=source)

    def test_stroke_preserves_prop_anchor_and_requires_actual_surface_contact(self):
        data = production_fixture()
        world = data['initial_world']
        world['props']['信纸']['surface'] = {'pattern':'still', 'glow':'faint', 'mist':'none', 'extent_m':.3}
        world['props']['信纸']['physical'] = {'material':'metal', 'contents':'none', 'integrity':'intact'}
        world['points']['拂镜终点'] = {'scene_id':'ROOM', 'position_xyz':[.28,.3,1]}
        source = sourced(data, 'stroke_prop', {'prop':'信纸','hand':'right','to':'拂镜终点'}, text='甲指尖轻拂镜面。')
        result = p.lower_production_decisions(data, source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['props']['信纸']['anchor'], {'point':'案左'})
        self.assertIn('指尖贴着', _render_decisions(result['typed_decisions'])['shots'][0]['画面内容'])
        world['points']['拂镜终点']['position_xyz'] = [1,.3,1]
        source = sourced(data, 'stroke_prop', {'prop':'信纸','hand':'right','to':'拂镜终点'})
        with self.assertRaisesRegex(p.ProductionDecisionError, 'bounded surface landmarks'):
            p.lower_production_decisions(data, source_compilation=source)

    def test_lap_cloth_stays_worn_after_fingers_grasp_and_carries_into_next_frame(self):
        data = production_fixture()
        world = data['initial_world']
        world['actors']['甲'].update(posture='坐姿', eye_height=1.1)
        world['props']['素衣'] = {'anchor':{'actor':'甲','worn':'torso'}, 'written_ref':None,
                                 'physical':{'material':'cloth','contents':'none','integrity':'intact'}}
        parameters = {'hand':'right','target_actor':'甲','target_part':'lap_cloth','mode':'grasp_cloth'}
        source = sourced(data, 'contact_actor', parameters, text='甲轻轻攥紧膝上衣角。')
        next_shot(data)
        result = p.lower_production_decisions(data, source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['props']['素衣']['anchor'], {'actor':'甲','worn':'torso'})
        self.assertIn('攥住甲膝上衣角', _render_decisions(result['typed_decisions'])['shots'][1]['起始状态'])
        data['shots'] = data['shots'][:1]
        world['props'].pop('素衣')
        source = sourced(data, 'contact_actor', parameters)
        with self.assertRaisesRegex(p.ProductionDecisionError, 'actual intact worn garment'):
            p.lower_production_decisions(data, source_compilation=source)

    def test_upper_arm_support_uses_that_side_and_does_not_relabel_wrist(self):
        data = production_fixture()
        world = data['initial_world']
        world['actors']['乙'] = copy.deepcopy(world['actors']['甲'])
        world['actors']['乙'].update(position_xyz=[.5,0,0], reach_origin_xyz=[.5,0,1.2])
        source = sourced(data, 'contact_actor', {'hand':'right','target_actor':'乙','target_part':'left_upper_arm','mode':'support'}, text='甲轻扶乙左上臂。')
        result = p.lower_production_decisions(data, source_compilation=source)
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['action_state']['contacts']['right']['part'], 'left_upper_arm')
        self.assertIn('扶住乙的左上臂', _render_decisions(result['typed_decisions'])['shots'][0]['画面内容'])


if __name__ == '__main__': unittest.main()
