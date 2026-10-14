"""Opaque storage is a real anchor and visibility relation, not surface support."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills/shortdrama-storyboard/scripts'), str(ROOT / 'tests')]
import production_decisions as p
from author_storyboard import _render_decisions
from test_v0527_production_decisions import production_fixture, action


def container_fixture():
    data = production_fixture()
    data['initial_world']['props'] = {
        '腰侧布套': {'anchor': {'actor': '甲', 'mount': 'right_waist', 'offset_xyz': [.25, 0, .9]},
                     'container': {'kind': 'opaque_sleeve', 'capacity': 1}, 'written_ref': None,
                     'physical': {'material': 'cloth', 'contents': 'none', 'integrity': 'intact'}},
        '戒尺': {'anchor': {'container': '腰侧布套'}, 'written_ref': None,
                 'physical': {'material': 'wood', 'contents': 'none', 'integrity': 'intact'}}}
    return data


class ContainerTests(unittest.TestCase):
    def test_containment_survives_state_without_becoming_visible_or_held(self):
        data = container_fixture(); result = p.lower_production_decisions(data)
        row = result['typed_decisions']['shots'][0]; state = row['start_state']
        self.assertEqual(state['动作进度']['物件位置与状态']['戒尺']['anchor'], {'container': '腰侧布套'})
        self.assertEqual(state['手持道具'], [])
        canonical = _render_decisions(result['typed_decisions'])['shots'][0]
        self.assertIn('右腰侧', canonical['起始状态'])
        self.assertNotIn('戒尺', canonical['起始状态'])
        self.assertNotIn('承放在', canonical['起始状态'])

    def test_sleeve_and_contents_follow_actual_body_translation_and_rotation(self):
        data = container_fixture()
        data['shots'][0]['actions'] = [action('move_actor', id='walk', start=0, end=20, to='落脚处'),
                                       action('turn', id='turn', start=20, end=40, body_yaw=90, head_yaw=90, after=['walk'])]
        result = p.lower_production_decisions(data); w = result['derivation']['final_world']
        sleeve = p._anchor(w, w['props']['腰侧布套']); contents = p._anchor(w, w['props']['戒尺'])
        self.assertEqual(sleeve, contents)
        self.assertAlmostEqual(sleeve[1][0], 1)
        self.assertAlmostEqual(sleeve[1][1], -.25)
        self.assertAlmostEqual(sleeve[1][2], .9)
        self.assertEqual(w['props']['戒尺']['anchor'], {'container': '腰侧布套'})

    def test_unknown_container_multiple_occupancy_false_material_and_bad_mount_rejected(self):
        for problem in ('unknown', 'full', 'material', 'wrong_side', 'mount_height', 'self_cycle'):
            with self.subTest(problem=problem):
                data = container_fixture(); props = data['initial_world']['props']
                if problem == 'unknown': props['戒尺']['anchor']['container'] = '不存在的袋子'
                if problem == 'full': props['第二把戒尺'] = copy.deepcopy(props['戒尺'])
                if problem == 'material': props['腰侧布套']['physical']['material'] = 'metal'
                if problem == 'wrong_side': props['腰侧布套']['anchor']['offset_xyz'][0] = -.25
                if problem == 'mount_height': props['腰侧布套']['anchor']['offset_xyz'][2] = 1.8
                if problem == 'self_cycle': props['腰侧布套']['anchor'] = {'container': '腰侧布套'}
                with self.assertRaises(p.ProductionDecisionError): p.lower_production_decisions(data)

    def test_hidden_content_cannot_be_visual_target_or_surface_descendant(self):
        data = container_fixture(); data['shots'][0]['actions'] = [action('look', target={'kind': 'prop', 'id': '戒尺'}, head_mode='follow', tracking=False)]
        with self.assertRaisesRegex(p.ProductionDecisionError, 'opaque container'): p.lower_production_decisions(data)
        data = container_fixture(); data['initial_world']['props']['纸片'] = {'anchor': {'prop': '戒尺', 'offset_xyz': [0, 0, .01]}, 'written_ref': None}
        result = p.lower_production_decisions(data)
        self.assertNotIn('纸片', _render_decisions(result['typed_decisions'])['shots'][0]['起始状态'])

    def test_initial_gaze_cannot_claim_hidden_paper_is_seen(self):
        data = container_fixture(); w = data['initial_world']
        w['props']['戒尺']['physical']['material'] = 'paper'
        w['actors']['甲']['gaze'] = {'kind': 'prop', 'id': '戒尺'}
        with self.assertRaisesRegex(p.ProductionDecisionError, 'opaque container'): p.lower_production_decisions(data)

    def test_posture_change_does_not_leave_floating_waist_mount(self):
        data = container_fixture(); w = data['initial_world']; w['points']['坐点'] = {'scene_id': 'ROOM', 'position_xyz': [0, 0, 0]}
        data['shots'][0]['actions'] = [action('posture', to='坐姿', point='坐点', landmarks={'eye_height': .8,
            'reach_origin_offset_xyz': [0, 0, .6], 'hands': {'left': [-.2, .3, .5], 'right': [.2, .3, .5]}})]
        with self.assertRaisesRegex(p.ProductionDecisionError, 'waist-mount'): p.lower_production_decisions(data)


if __name__ == '__main__': unittest.main()
