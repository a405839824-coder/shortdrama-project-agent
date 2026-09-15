"""Scene checks retain real derivation authority without accepting edited rows.

Public synthetic stories only. No source story or user artifact is a test fixture.
"""
import copy
import tempfile
import unittest

from test_v0535_production_contract import complete_episode, generate
import test_v0535_production_contract as production_tests
from shortdrama_mcp.service import _scene_scoped_report


class SceneProductionContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, graph = complete_episode()
        data['project']['prevention_evidence']['change_lock']['must_include'] = ['编号']
        for index, shot in enumerate(data['shots']):
            if index >= 10:
                shot['continuity_id'] = 'ROOM-C2'
                if index == 10:
                    shot['transition'] = {'relation':'later_same_place', 'basis':'稍后在同一书房继续核对下一组编号。'}
        with tempfile.TemporaryDirectory() as folder:
            cls.candidate = generate(folder, data, graph)

    def test_both_real_scenes_use_complete_verified_derivation(self):
        before = copy.deepcopy(self.candidate)
        for selected in (self.candidate['shots'][:10], self.candidate['shots'][10:]):
            with self.subTest(first=selected[0]['镜头编号']):
                report, scoped = _scene_scoped_report(self.candidate, selected)
                self.assertEqual(report['issues'], [], report['issues'])
                self.assertEqual(report['scene_validation_basis'], 'verified_complete_production_context')
                self.assertTrue(report['quality_metrics']['production_derivation']['valid'])
                self.assertEqual(report['shot_count'], 10)
                self.assertEqual(scoped['shots'], selected)
        self.assertEqual(self.candidate, before)

    def test_modified_selected_picture_cannot_borrow_complete_proof(self):
        obj = copy.deepcopy(self.candidate)
        obj['shots'][10]['画面内容'] += '甲额外说了一句未经来源登记的话。'
        report, _ = _scene_scoped_report(obj, obj['shots'][10:])
        self.assertEqual(report['status'], 'fail_current')
        self.assertTrue(any(x['location']=='scene:production_derivation' for x in report['issues']))

    def test_modified_world_context_is_not_hidden_by_selecting_another_scene(self):
        obj = copy.deepcopy(self.candidate)
        obj['prevention_evidence']['production_derivation']['input']['initial_world']['actors']['甲']['position_xyz'][0] += 1
        report, _ = _scene_scoped_report(obj, obj['shots'][10:])
        self.assertEqual(report['status'], 'fail_current')
        self.assertTrue(any(x['location']=='scene:production_derivation' for x in report['issues']))

    def test_detached_edited_and_duplicate_rows_are_not_exact_subsets(self):
        altered = copy.deepcopy(self.candidate['shots'][10:])
        altered[0]['时长'] += 1
        for rows in (altered, [self.candidate['shots'][0], self.candidate['shots'][0]]):
            with self.subTest(count=len(rows)):
                report, _ = _scene_scoped_report(self.candidate, rows)
                self.assertEqual(report['status'], 'fail_current')
                self.assertTrue(any(x['location']=='scene:production_derivation' for x in report['issues']))

    def test_current_mcp_multiscene_production_reaches_full_preflight(self):
        harness = production_tests.CurrentMcpContractTests()
        harness.setUp()
        try:
            data, graph = complete_episode()
            data['project']['prevention_evidence']['change_lock']['must_include'] = ['编号']
            for index, shot in enumerate(data['shots']):
                if index >= 10:
                    shot['continuity_id'] = 'ROOM-C2'
                    if index == 10:
                        shot['transition'] = {'relation':'later_same_place', 'basis':'稍后在同一书房核对下一组编号。'}
            request = harness.request(data, graph)
            f = harness.f
            args = {'project_id':f.project, 'work_order_id':f.order, 'storyboard':request,
                    'scope_kind':'scene', 'scope_id':'ROOM-C1'}
            for scene in ('ROOM-C1','ROOM-C2'):
                args['scope_id'] = scene
                result = f.mcp('preflight_storyboard_draft', args)
                self.assertEqual(result['status'], 'draft_preflight_pass', result['data']['report']['issues'])
                self.assertFalse(result['data']['artifact_created'])
            args.update(scope_kind='full', scope_id='EP90')
            full = f.mcp('preflight_storyboard_draft', args)
            self.assertTrue(full['data']['commit_ready'], full['data']['report']['issues'])
        finally:
            harness.tearDown()


if __name__ == '__main__':
    unittest.main()
