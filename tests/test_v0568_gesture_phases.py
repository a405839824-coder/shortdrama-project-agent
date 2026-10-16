"""Counted events remain single events; pitch is an explicit state change."""
import copy
import unittest
from test_v0537_source_actions import sourced, next_shot
from test_v0527_production_decisions import production_fixture, action
import production_decisions as p
from author_storyboard import _render_decisions


class GesturePhaseTests(unittest.TestCase):
    def test_back_head_turn_is_retained_before_eyes_become_visible(self):
        data = production_fixture()
        data['initial_world']['actors']['甲'].update(body_yaw=90, head_yaw=170)
        data['initial_world']['points']['来人']={'scene_id':'ROOM','position_xyz':[0,2,1.5]}
        data['shots'][0]['actions']=[action('look', start=0, end=10,
            target={'kind':'point','id':'来人'}, tracking=False, head_mode='follow')]
        result=p.lower_production_decisions(data)
        row=result['typed_decisions']['shots'][0]
        self.assertIn('转头朝向',row['phases'][0]['text'])
        self.assertNotIn('视线从',row['phases'][0]['text'])
        self.assertIn('眼睛清楚可见',row['phases'][1]['text'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['head_yaw'],0)

    def test_shudder_does_not_repeat_the_impulse_in_its_recovery(self):
        data=production_fixture()
        source=sourced(data,'body_shudder',{},start=0,end=40,text='甲身躯一震。')
        result=p.lower_production_decisions(data,source_compilation=source)
        text=_render_decisions(result['typed_decisions'])['shots'][0]['画面内容']
        self.assertEqual(text.count('身躯短促一震'),1)
        self.assertIn('震后逐渐稳住',text)
        self.assertIn('震后重新稳住',text)

    def test_single_nod_across_three_phases_is_not_three_completed_nods(self):
        data = production_fixture()
        data['initial_world']['actors']['甲']['gaze'] = None
        source = sourced(data, 'head_gesture', {'direction':'nod','count':1}, start=0, end=40)
        result = p.lower_production_decisions(data, source_compilation=source)
        text = _render_decisions(result['typed_decisions'])['shots'][0]['画面内容']
        self.assertEqual(text.count('共1次'), 1)
        self.assertIn('开始点头', text)
        self.assertIn('延续同一点头过程', text)
        self.assertIn('完成这一次连续点头过程', text)
        self.assertEqual(result['derivation']['world_events'][0]['effect_occurrences'], 1)

    def test_head_tilt_changes_pitch_and_preserves_root_and_next_frame(self):
        data = production_fixture()
        source = sourced(data, 'head_tilt', {'to_degrees':-15}, start=0, end=10, text='甲低头。')
        next_shot(data)
        original = copy.deepcopy(data)
        result = p.lower_production_decisions(data, source_compilation=source)
        rows = _render_decisions(result['typed_decisions'])['shots']
        self.assertIn('低头至头部微低', rows[0]['画面内容'])
        self.assertIn('头部微低', rows[1]['起始状态'])
        actor = result['derivation']['final_world']['actors']['甲']
        self.assertEqual(actor['action_state']['head_pitch_degrees'], -15)
        self.assertEqual(actor['position_xyz'], original['initial_world']['actors']['甲']['position_xyz'])
        self.assertEqual(data, original)

    def test_pitch_has_finite_range_and_real_motion(self):
        for pitch in (True, float('inf'), -31, 31, 0):
            with self.subTest(pitch=pitch):
                data = production_fixture()
                data['shots'][0]['actions'] = [action('head_tilt', to_degrees=pitch)]
                with self.assertRaises(p.ProductionDecisionError):
                    p.lower_production_decisions(data)

    def test_head_tilt_and_horizontal_turn_cannot_both_own_head(self):
        data = production_fixture()
        data['shots'][0]['actions'] = [action('head_tilt', id='TILT', to_degrees=-15),
            action('turn', id='TURN', body_yaw=0, head_yaw=10)]
        with self.assertRaises(p.ProductionDecisionError):
            p.lower_production_decisions(data)

    def test_pitch_changes_camera_facing_without_claiming_mesh_occlusion(self):
        actor = {'head_yaw':0, 'action_state':{'head_pitch_degrees':-30}}
        self.assertAlmostEqual(p._head_forward(actor)[2], -.5)


if __name__ == '__main__': unittest.main()
