"""Actual per-phase tracking must not inherit motion from another phase."""
import unittest
import test_v0565_production_projection_dependencies as controls
import test_v0566_optical_ending as optical_controls


class PhaseGazeTests(unittest.TestCase):
    def test_final_gaze_is_explicitly_before_phantom_disappears(self):
        data=optical_controls.mirror()
        actor=data['initial_world']['actors']['甲']
        actor['optical']={'opacity':.4,'extent_m':.4,'glimmer':True}
        actor['gaze']={'kind':'prop','id':'古镜'}
        source=optical_controls.sourced(data,'phantom_fade',{'to':0,'glimmer':True},
            text='甲逐渐消散。',start=0,end=40)
        result=controls.p.lower_production_decisions(data,source_compilation=source)
        phases=result['typed_decisions']['shots'][0]['phases']
        self.assertIn('消散前，甲最终视线停在',phases[-1]['text'])
        self.assertEqual(result['derivation']['final_world']['actors']['甲']['optical']['opacity'],0)

    def test_initial_head_alignment_is_not_also_declared_still(self):
        data=controls.look_control()
        data['initial_world']['actors']['甲']['head_yaw']=20
        result=controls.p.lower_production_decisions(data)
        phases=result['typed_decisions']['shots'][0]['phases']
        text=phases[0]['text']
        self.assertIn('甲头部随目标方向转动',text)
        self.assertNotIn('甲头部保持不动',text)
        self.assertIn('甲头部保持不动',phases[-1]['text'])

    def test_tracking_only_moves_in_the_phase_with_actual_target_motion(self):
        data=controls.look_control(moving=True)
        data['shots'][0]['actions'][0]['end']=40
        data['shots'][0]['actions'][1].update(start=10,end=30)
        result=controls.p.lower_production_decisions(data)
        phases=result['typed_decisions']['shots'][0]['phases']
        self.assertIn('甲头部保持不动',phases[0]['text'])
        self.assertNotIn('甲头部随目标方向转动',phases[0]['text'])
        self.assertIn('头部随目标方向转动',phases[1]['text'])
        self.assertNotIn('头部保持不动',phases[1]['text'])
        self.assertIn('甲头部保持不动',phases[2]['text'])
        self.assertNotIn('甲头部随目标方向转动',phases[2]['text'])
