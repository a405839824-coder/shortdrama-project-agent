"""Real preflight closure plus negative checks for reference/episode scope."""
import copy
import unittest
from unittest.mock import patch

import test_v055_quality_migration as helper_module
from test_v040_architecture import three_shot_storyboard
from shortdrama_mcp import service as sm


class DraftScopeResolutionTests(unittest.TestCase):
    namespace = helper_module.V055QualityAndMigrationTests.namespace
    setUp = helper_module.V055QualityAndMigrationTests.setUp
    tearDown = helper_module.V055QualityAndMigrationTests.tearDown
    _project = helper_module.V055QualityAndMigrationTests._project
    _begin = helper_module.V055QualityAndMigrationTests._begin

    def candidate(self):
        value = three_shot_storyboard()
        for i, shot in enumerate(value['shots']):
            shot['scene_uid'] = 'SCENE-' + str(i + 1)
            shot['场景连续性ID'] = '共享正厅'
        for row in value['prevention_evidence']['scene_state_ledger']:
            row['scene_id'] = '共享正厅'
        from run_golden_tests import build_adjacent_continuity_audit
        value['prevention_evidence']['adjacent_continuity_audit'] = build_adjacent_continuity_audit(
            value['shots'], value['prevention_evidence']['scene_state_ledger'])
        return value

    def start_alias_failure(self):
        project = self._project('same alias across stable scenes')
        work, session = self._begin(project, operation='storyboard_create', label='alias-closure')
        candidate = self.candidate()
        alias = candidate['shots'][0]['场景连续性ID']
        failed = self.service.preflight_storyboard_draft(self.namespace,
            project_id=project, work_order_id=work, request_session_id=session,
            storyboard=candidate, scope_kind='scene', scope_id=alias)
        self.assertEqual(failed.status, sm.DRAFT_PREFLIGHT_FAIL)
        self.assertEqual(len(failed.data['report']['issues']), 1, failed.data['report']['issues'])
        return project, work, session, candidate, failed

    def full_with_scenes(self, project, work, session, candidate):
        for group in sm._scene_scope_groups(candidate):
            scene = self.service.preflight_storyboard_draft(self.namespace,
                project_id=project, work_order_id=work, request_session_id=session,
                storyboard=candidate, scope_kind='scene', scope_id=group['stable_scene_id'])
            self.assertEqual(scene.status, sm.DRAFT_PREFLIGHT_PASS, scene.data['report']['issues'])
        return self.service.preflight_storyboard_draft(self.namespace,
            project_id=project, work_order_id=work, request_session_id=session,
            storyboard=candidate, scope_kind='full', scope_id='EP01')

    def test_complete_coverage_closes_alias_and_commits_without_erasing_failure(self):
        project, work, session, candidate, failed = self.start_alias_failure()
        original = copy.deepcopy(failed.data['draft_preflight'])
        passed = self.full_with_scenes(project, work, session, candidate)
        self.assertTrue(passed.data['commit_ready'], passed.data['report']['issues'])
        self.assertEqual(len(passed.data['draft_preflight']['resolved_scene_alias_events']), 1)
        ledger = self.service.store.load_ledger(self.namespace, project)
        self.assertFalse(self.service._draft_preflight_failure_state(ledger)['blocked'])
        retained = self.service.store.get_project_record(self.namespace, project,
            kind='draft_preflights', record_id=failed.trace_id)
        self.assertEqual(retained, original)
        self.assertEqual(retained['status'], sm.DRAFT_PREFLIGHT_FAIL)
        self.assertTrue(any(e.get('full_scope_resolution') for e in ledger['events']))
        committed = self.service.commit_storyboard(self.namespace, project_id=project,
            work_order_id=work, request_session_id=session, storyboard=candidate,
            file_name='scope-resolution.xlsx', source_artifact_id=None,
            draft_preflight_id=passed.trace_id)
        self.assertEqual(committed.status, sm.PRODUCTION_PREFLIGHT_PASS)

    def test_partial_scene_coverage_keeps_alias_blocked(self):
        project, work, session, candidate, failed = self.start_alias_failure()
        group = sm._scene_scope_groups(candidate)[0]
        self.service.preflight_storyboard_draft(self.namespace, project_id=project,
            work_order_id=work, request_session_id=session, storyboard=candidate,
            scope_kind='scene', scope_id=group['stable_scene_id'])
        result = self.service.preflight_storyboard_draft(self.namespace, project_id=project,
            work_order_id=work, request_session_id=session, storyboard=candidate,
            scope_kind='full', scope_id='EP01')
        self.assertFalse(result.data['commit_ready'])
        ledger = self.service.store.load_ledger(self.namespace, project)
        self.assertTrue(self.service._draft_preflight_failure_state(ledger)['blocked'])

    def test_changed_or_unsigned_failed_candidate_cannot_be_closed(self):
        project, work, session, candidate, failed = self.start_alias_failure()
        with patch.object(self.service.store, 'project_record_is_signed', return_value=False):
            manifest = {'complete': True, 'scene_coverage_manifest':[
                {**g, 'scene_preflight_id':'DPF-test'} for g in sm._scene_scope_groups(candidate)]}
            ledger = self.service.store.load_ledger(self.namespace, project)
            with self.assertRaisesRegex(ValueError, 'project_record_signature_required'):
                self.service._close_covered_scene_alias_failures(self.namespace, project,
                    ledger=ledger, work_order_id=work, candidate=candidate,
                    scene_coverage=manifest, full_preflight_id='DPF-current')
        candidate['shots'][0]['生成限制'] += '；保持门框位置'
        ledger = self.service.store.load_ledger(self.namespace, project)
        manifest = {'complete': True, 'scene_coverage_manifest':[
            {**g, 'scene_preflight_id':'DPF-test'} for g in sm._scene_scope_groups(candidate)]}
        self.assertEqual(self.service._close_covered_scene_alias_failures(self.namespace, project,
            ledger=ledger, work_order_id=work, candidate=candidate,
            scene_coverage=manifest, full_preflight_id='DPF-current'), [])

    def test_source_reference_is_not_a_storyboard_base(self):
        work = {'targets':['ART-reference','EP04','EP05']}
        for kind in ('source:reference','source:script'):
            with self.subTest(kind=kind), patch.object(self.service.store, 'get_artifact', return_value=({'artifact_type':kind},None)):
                self.service._assert_full_commit_episode_scope_integrity(self.namespace,'project',
                    work_order=work,source_artifact_id='ART-source',candidate_storyboard={})

    def test_existing_storyboard_still_rejects_ambiguous_episode_targets(self):
        for kind in ('storyboard','storyboard_audit_overlay','storyboard_json'):
            with self.subTest(kind=kind), patch.object(self.service.store, 'get_artifact', return_value=({'artifact_type':kind},None)):
                with self.assertRaisesRegex(ValueError, 'full_commit_episode_scope_ambiguous'):
                    self.service._assert_full_commit_episode_scope_integrity(self.namespace,'project',
                        work_order={'targets':['EP04','unparseable']},source_artifact_id='ART-base',candidate_storyboard={})


if __name__ == '__main__':
    unittest.main()
