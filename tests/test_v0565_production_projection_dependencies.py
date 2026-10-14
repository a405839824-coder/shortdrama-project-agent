"""Original frozen failures plus independent controls; no audit PASS fixtures."""
import copy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / 'skills/shortdrama-storyboard/scripts'), str(HERE)]
import production_decisions as p
from test_v0527_production_decisions import production_fixture, action


def frozen():
    return json.loads((HERE / 'fixtures/v0565_projection_failures.json').read_text())


def c_snapshot():
    case = frozen()['C']
    world = p._world(case['initial_world'], None, check_source_refs=False)
    row = case['shots'][1]
    cam = p._camera(world, row['camera'], row['scene_id'], 'camera')
    return world, row, cam


def look_control(*, moving=False):
    data = production_fixture(); world = data['initial_world']
    target = copy.deepcopy(world['actors']['甲'])
    target.update(position_xyz=[0, 2, 0], reach_origin_xyz=[0, 2, 1.2])
    world['actors']['乙'] = target
    world['points']['原落点'] = {'scene_id': 'ROOM', 'position_xyz': [0, 2, 1.5]}
    world['points']['乙终点'] = {'scene_id': 'ROOM', 'position_xyz': [1, 2, 0]}
    world['actors']['甲']['gaze'] = {'kind': 'point', 'id': '原落点'}
    data['shots'][0]['actions'] = [action('look', id='LOOK', start=0, end=10,
        target={'kind': 'actor', 'id': '乙'}, tracking=moving, head_mode='follow')]
    if moving:
        data['shots'][0]['actions'].append(action('move_actor', actor='乙', id='MOVE', start=0, end=10, to='乙终点'))
    return data


class ProjectionDependencyTests(unittest.TestCase):
    def test_original_A_same_coordinate_reference_rebind_has_no_head_or_eye_motion(self):
        case = frozen()['A']; world = p._world(case['initial_world'], None, check_source_refs=False)
        sources = {'uses_by_id': {u['id']: u for u in case['source_uses']}}
        plans = {event['source_use_id']: event for event in case['action_plans']['events']}
        def expand(raw):
            step = next(s for s in plans[raw['source_use_id']]['steps'] if s['step_id'] == raw['step_id'])
            return {**{k: v for k, v in raw.items() if k not in {'kind', 'step_id'}},
                    'kind': step['kind'], 'actor': sources['uses_by_id'][raw['source_use_id']]['actor'], **step['parameters']}
        prior = expand(case['prior_gaze_action'])
        info = p._prepare(world, prior, sources, 'original.prior')
        for tick in range(prior['start'], prior['end'] + 1): p._apply(world, prior, info, tick, 'original.prior')
        raw = next(a for a in case['shots'][1]['actions'] if a['id'] == 'A_LOOK_Z')
        current = expand(raw); info = p._prepare(world, current, sources, 'original.look')
        samples = []
        for tick in range(current['start'], current['end'] + 1):
            p._apply(world, current, info, tick, 'original.look')
            samples.append({'world': copy.deepcopy(world)})
        info['motion_projection'] = p._look_motion(current, info, samples)
        cam = p._camera(world, case['shots'][1]['camera'], case['shots'][1]['scene_id'], 'camera')
        verb, sentence = p._action_sentence(current, info, samples[0]['world'], cam)
        self.assertEqual(info['before']['head_yaw'], 0)
        self.assertEqual(world['actors']['苏清柳']['head_yaw'], 0)
        self.assertFalse(info['motion_projection']['head_changed'])
        self.assertFalse(info['motion_projection']['gaze_direction_changed'])
        self.assertEqual(verb, '停在')
        self.assertIn('苏清柳视线持续停在邹氏。', sentence)
        self.assertIn('头部保持不动', sentence)
        self.assertNotIn('移向', sentence)
        self.assertNotIn('头部随目标方向转动', sentence)

    def test_noop_and_real_tracking_use_actual_tick_path_in_full_lowering(self):
        for moving in (False, True):
            data = look_control(moving=moving); before = copy.deepcopy(data)
            lowered = p.lower_production_decisions(data)
            record = next(a for a in lowered['derivation']['shots'][0]['actions'] if a['id'] == 'LOOK')
            self.assertEqual(record['motion_projection']['head_changed'], moving)
            self.assertEqual(record['motion_projection']['gaze_direction_changed'], moving)
            self.assertEqual('头部随目标方向转动' in record['world_action_description'], moving)
            self.assertEqual(data, before)
            self.assertFalse(lowered['derivation']['commit_eligible'])

    def test_returning_to_same_endpoint_still_records_intermediate_motion(self):
        data = look_control(); world = p._world(data['initial_world'], None, check_source_refs=False)
        current = data['shots'][0]['actions'][0]; info = p._prepare(world, current, p._source(None), 'look')
        states = [copy.deepcopy(world) for _ in range(11)]
        states[5]['actors']['甲']['head_yaw'] = 5
        states[5]['actors']['甲']['gaze']['position_xyz'][0] = .2
        motion = p._look_motion(current, info, [{'world': w} for w in states])
        self.assertTrue(motion['head_changed'])
        self.assertTrue(motion['gaze_direction_changed'])

    def test_concurrent_turn_is_not_misattributed_to_still_head_look(self):
        data = look_control()
        data['shots'][0]['actions'][0]['head_mode'] = 'still'
        data['shots'][0]['actions'].append(action('turn', id='TURN', start=0, end=10, body_yaw=30, head_yaw=30))
        result = p.lower_production_decisions(data)
        look = next(a for a in result['derivation']['shots'][0]['actions'] if a['id'] == 'LOOK')
        self.assertTrue(look['motion_projection']['head_changed'])
        self.assertNotIn('头部保持不动', look['world_action_description'])
        self.assertNotIn('头部随目标方向转动', look['world_action_description'])

    def test_original_A_cut_projects_actual_speaker_and_incoming_subject(self):
        case = frozen()['A']; world = p._world(case['initial_world'], None, check_source_refs=False)
        previous, row = case['shots']
        sources = {'production_plan': {'shots': [case['source_plan_shot']]},
                   'uses_by_id': {u['id']: u for u in case['source_uses']}}
        fields = p._cut_fields(world, row, previous, sources, {'actors': {'苏清柳': {}}, 'props': []})
        self.assertEqual(fields['切镜/转场提示词'], '邹氏本句话音收尾时，硬切至苏清柳。')
        self.assertNotIn(case['source_plan_shot']['design']['cut_reason'], fields['切镜/转场提示词'])
        bad = copy.deepcopy(sources)
        bad['production_plan']['shots'][0]['design']['cut_anchor']['action_id'] = 'MISSING'
        with self.assertRaisesRegex(p.ProductionDecisionError, 'cut event does not exist'):
            p._cut_fields(world, row, previous, bad, {'actors': {'苏清柳': {}}, 'props': []})

    def test_cut_allows_multiple_real_subjects_without_combining_their_identity(self):
        case = frozen()['A']; world = p._world(case['initial_world'], None, check_source_refs=False)
        sources = {'production_plan': {'shots': [case['source_plan_shot']]},
                   'uses_by_id': {u['id']: u for u in case['source_uses']}}
        fields = p._cut_fields(world, case['shots'][1], case['shots'][0], sources,
                              {'actors': {'苏清柳': {}, '邹氏': {}}, 'props': []})
        self.assertIn('硬切至苏清柳、邹氏。', fields['切镜/转场提示词'])

    def test_original_C_ground_only_actor_cannot_disappear_when_eye_is_out(self):
        world, row, cam = c_snapshot()
        result = p._camera_observation(world, cam, row, snapshot_basis='initial_world_reference_only', path='shots[1].camera')
        servant = result['actors']['仆从']
        self.assertEqual(servant['visible_landmarks'], ['ground'])
        self.assertAlmostEqual(servant['landmarks']['ground']['horizontal_degrees'], -4.778214583, places=7)
        self.assertAlmostEqual(servant['landmarks']['ground']['vertical_degrees'], -5.785039074, places=7)
        self.assertEqual(result['frame_status'], 'conflict')
        self.assertEqual(result['mesh_occlusion'], 'not_modelled')
        self.assertEqual(len(result['actors']), 4)

    def test_narrower_control_and_reversed_head_are_separate_from_original(self):
        world, row, cam = c_snapshot(); row['camera']['vfov_deg'] = 10
        cam = p._camera(world, row['camera'], row['scene_id'], 'camera')
        control = p._camera_observation(world, cam, row, snapshot_basis='synthetic_control', path='camera')
        self.assertFalse(control['frame_findings'])
        self.assertEqual(control['actors']['仆从']['status'], 'no_landmarks_in_frame')
        self.assertEqual(control['mesh_occlusion'], 'not_modelled')
        world['actors']['子平']['head_yaw'] = 270
        bad = p._camera_observation(world, cam, row, snapshot_basis='synthetic_control', path='camera')
        self.assertTrue(bad['actors']['子平']['landmarks']['eye']['in_frustum'])
        self.assertFalse(bad['actors']['子平']['eye_visible'])
        self.assertTrue(bad['frame_findings'])

    def test_known_wall_occlusion_is_distinct_from_outside_frustum(self):
        data = production_fixture(); world = p._world(data['initial_world'], None, check_source_refs=False)
        row = data['shots'][0]; cam = p._camera(world, row['camera'], 'ROOM', 'camera')
        world['scenes']['ROOM']['barriers'] = [{'id': '墙', 'a': [-2, 2, 0], 'b': [2, 2, 0], 'height_m': 3}]
        result = p.inspect_camera_landmarks(world, cam, 'ROOM', snapshot_basis='synthetic_control')
        eye = result['actors']['甲']['landmarks']['eye']
        self.assertTrue(eye['in_frustum'])
        self.assertEqual(eye['known_blocker_id'], '墙')
        self.assertFalse(eye['model_visible'])
        self.assertEqual(eye['mesh_occlusion'], 'not_modelled')

    def test_original_C_later_reference_conflict_survives_unavailable_effects_without_claiming_replay(self):
        data = production_fixture(); case = frozen()['C']
        data.update(initial_world=case['initial_world'], shots=case['shots'])
        before = copy.deepcopy(data)
        report = p.inspect_production_decisions(data, source_unavailable_reason='original source dependency unavailable')
        observation = report['geometry_observations'][1]
        self.assertEqual(observation['snapshot_basis'], 'initial_world_reference_only')
        self.assertTrue(observation['frame_findings'])
        self.assertTrue(any(x['stage'] == 'shots[1].camera.actual_start_geometry' for x in report['not_checked']))
        self.assertNotIn('lowered', report)
        self.assertEqual(data, before)

    def test_same_ground_conflict_at_real_frame_zero_is_hard_failure_not_pass(self):
        data = production_fixture(); case = frozen()['C']
        data.update(initial_world=case['initial_world'], shots=[case['shots'][1]])
        report = p.inspect_production_decisions(data, source_unavailable_reason='source unavailable')
        self.assertTrue(any(x['stage'] == 'shots[0].camera.landmarks' for x in report['findings']))
        self.assertNotIn('shots[0].camera.landmarks', report['checked'])


if __name__ == '__main__':
    unittest.main()
