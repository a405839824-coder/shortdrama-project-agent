"""Environment renewal on real synthetic audit records; no user content or PASS claims."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_v0558_audit_finalization as finalization_support
from test_v0557_audit_submission import declare_unknown
import shortdrama_mcp.service as service_module


class AuditEnvironmentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = finalization_support.AuditFinalizationFlowTests()
        await self.case.asyncSetUp()
        self.addAsyncCleanup(self.case.asyncTearDown)
        self.flow = self.case.flow
        self.service = self.flow.service
        self.store = self.service.store
        self.namespace = self.flow.namespace
        self.project = (await self.flow.call('create_project', {
            'project_name': 'synthetic environment renewal'}))['project_id']
        self.runtime = self.renew_certificate()
        self.order = await self.flow.begin_storyboard_task(
            self.project, operation='storyboard_audit', mode='audit_only')
        self.artifact, self.audit_args, self.audit = await self.case.audit_external(
            self.project, self.order, 'synthetic-historical')
        self.cert = self.audit['data']['certificate']
        self.root = self.store.get_project_record(self.namespace, self.project,
            kind='handoffs', record_id=self.cert['handoff_id'])
        self.directory = self.store.project_dir(self.namespace, self.project)
        self.history = {p: p.read_bytes() for category in (
            'artifacts', 'handoffs', 'certificates', 'repair_packages', 'telemetry')
            for p in (self.directory / category).rglob('*') if p.is_file()}

    def renew_certificate(self):
        # Synthetic routing fixture only, never counted as software acceptance.
        return self.service._persist_runtime_certificate(self.namespace, self.project, {
            'status': 'pass', 'version': self.service.bootstrap.get('version'),
            'workflow_revision': self.service.bootstrap.get('workflow_revision')})

    def drift(self):
        self.service._runtime_fingerprint = 'd' * 64
        self.service._service_code_fingerprint = 'e' * 64
        return self.renew_certificate()

    def reuse(self):
        return self.service._project_reuse_summary(self.namespace, self.project,
            self.store.load_hub(self.namespace, self.project))

    def authority(self):
        gap = 'GAP-synthetic-environment-renewal'
        with self.store.mutate_project(self.namespace, self.project) as (hub, _ledger):
            service_module.project_agent.record_capability_gap(hub, {
                'gap_id': gap, 'timestamp': service_module.utc_stamp(),
                'request': 'Reaudit unchanged synthetic content after environment renewal',
                'missing_dependency': 'current audit intake', 'risk': 'stale evidence',
                'safe_fallback': 'retain pending status',
                'proposed_implementation': 'signed current intake preserving history',
                'required_tests': ['content identity', 'history preservation']})
        order = self.service.begin_task(self.namespace, project_id=self.project,
            operation='rule_improvement', mode='execute', authorization='explicit_execute',
            request_text='authorize exact synthetic environment recovery',
            targets=[gap, self.root['handoff_id'], 'controlled_phase:audit_environment_recovery'],
            authoritative_source='signed synthetic original and audit', latest_version='synthetic',
            target_boundary='one external audit root', must_keep=['original', 'history'],
            must_remove=[], protected_scope=['source', 'dialogue', 'old conclusion'],
            expected_outputs=['new pending audit intake'], window_key='synthetic-recovery',
            request_session_id=None)
        return dict(project_id=self.project, work_order_id=order.work_order_id,
                    gap_id=gap, handoff_ids=[self.root['handoff_id']])

    async def plan(self, args):
        return (await self.flow.call('recover_storyboard_handoff', {
            **args, 'action': 'plan'}))['data']['recovery_plan']

    async def execute(self, args, plan):
        return await self.flow.call('recover_storyboard_handoff', {
            **args, 'action': 'execute', 'expected_plan_sha256': plan['plan_sha256'],
            'confirmation': 'REVALIDATE_STALE_ENVIRONMENT_PRESERVE_HISTORY'})

    async def test_expired_profile_allows_new_native_audit_but_not_false_pass(self):
        self.drift()
        summary = self.reuse()
        self.assertEqual(summary['reuse_state_errors'], [])
        latest = summary['latest_storyboard']
        self.assertEqual(latest['audit_evidence_state'], 'stale_environment')
        self.assertEqual(latest['audit_status'], 'PENDING_AUDIT')
        self.assertIsNone(latest['independent_audit_certificate_id'])
        self.assertEqual(latest['historical_independent_audit_certificate_id'], self.cert['certificate_id'])
        with self.assertRaisesRegex(Exception, 'independent_audit_pending'):
            await self.flow.begin_storyboard_task(self.project, operation='storyboard_create',
                                                 window_key='must-not-start-production')
        order = await self.flow.begin_storyboard_task(
            self.project, operation='storyboard_audit', mode='audit_only', window_key='next-native-audit')
        _artifact, _args, result = await self.case.audit_external(self.project, order, 'synthetic-new')
        self.assertEqual(result['status'], 'fail_current')
        self.assertNotEqual(result['data']['certificate']['certificate_id'], self.cert['certificate_id'])
        # Timestamp ties must not select the old overlay by random artifact ID.
        artifacts = self.store.list_artifacts(self.namespace, self.project)
        old_overlay = self.audit['data']['audited_workbook']['artifact_id']
        artifacts.sort(key=lambda row: row.get('artifact_id') != old_overlay)
        with patch.object(self.store, 'list_artifacts', return_value=artifacts):
            latest = self.reuse()['latest_storyboard']
        self.assertEqual(latest['audit_evidence_state'], 'verified_independent_certificate')
        self.assertEqual(latest['independent_audit_certificate_id'], result['data']['certificate']['certificate_id'])
        for path, raw in self.history.items():
            self.assertEqual(path.read_bytes(), raw, str(path))

    async def test_legacy_nonpaged_recovery_replay_and_current_reaudit_preserve_history(self):
        self.drift()
        self.assertEqual(self.store.list_project_records_strict(
            self.namespace, self.project, kind='audit_coverage_collections'), [])
        args = self.authority()
        plan = await self.plan(args)
        self.assertEqual(plan['phase'], 'audit_environment_recovery')
        self.assertEqual(plan['items'][0]['old_trace_id'], self.root['stage_trace_id'])
        result = await self.execute(args, plan)
        mapping = result['data']['mappings'][0]
        repeated = await self.execute(args, plan)
        self.assertEqual(repeated['data']['mappings'], result['data']['mappings'])
        self.assertFalse(result['data']['audit_pass'])
        self.assertFalse(result['data']['formal_delivery_ready'])
        for path, raw in self.history.items():
            self.assertEqual(path.read_bytes(), raw, str(path))
        submission = declare_unknown(self.service, self.flow.settings,
                                     self.project, self.artifact['artifact_id'])
        current = await self.flow.call('audit_storyboard', {
            **self.audit_args, 'handoff_artifact_id': mapping['new_handoff_id'],
            'semantic_audit': submission})
        self.assertEqual(current['status'], 'fail_current')
        self.assertEqual(current['data']['certificate']['handoff_id'], mapping['new_handoff_id'])
        self.assertNotEqual(current['data']['certificate']['certificate_id'], self.cert['certificate_id'])

    async def test_certificate_renewal_without_content_or_profile_change_is_recoverable(self):
        current = self.renew_certificate()
        self.assertNotEqual(current['certificate_id'], self.runtime['certificate_id'])
        self.assertEqual(self.reuse()['latest_storyboard']['audit_evidence_state'], 'stale_environment')
        args = self.authority()
        result = await self.execute(args, await self.plan(args))
        self.assertEqual(result['data']['mappings'][0]['status'], 'PENDING_AUDIT')

    async def test_unchanged_current_profile_requires_no_recovery(self):
        args = self.authority()
        error = await self.flow.rejection_text('recover_storyboard_handoff', {**args, 'action': 'plan'})
        self.assertIn('profile_unchanged', error)

    async def test_invalid_signature_content_or_trace_still_blocks_even_after_drift(self):
        self.drift()
        certificate_path = self.directory / 'certificates' / (self.cert['certificate_id'] + '.json')
        trace_path = self.directory / 'telemetry' / (self.root['stage_trace_id'] + '.json')
        _record, artifact_path = self.store.get_artifact(self.namespace, self.project, self.artifact['artifact_id'])
        _overlay, overlay_path = self.store.get_artifact(self.namespace, self.project,
            self.audit['data']['audited_workbook']['artifact_id'])
        for path in (certificate_path, trace_path, artifact_path, overlay_path):
            with self.subTest(path=str(path)):
                raw = path.read_bytes()
                try:
                    if path in (artifact_path, overlay_path):
                        path.write_bytes(raw + b'tampered')
                    else:
                        value = self.store.read_json(path)
                        value['artifact_id'] = 'ART-forged'
                        self.store.atomic_write_json(path, value)
                    self.assertTrue(self.reuse()['reuse_state_errors'])
                    self.assertEqual(self.reuse()['latest_storyboard']['audit_evidence_state'],
                                     'invalid_certificate_or_binding')
                finally:
                    path.write_bytes(raw)
        self.assertEqual(self.reuse()['reuse_state_errors'], [])

    async def test_environment_recovery_checks_authority_stale_plan_and_interrupted_publication(self):
        self.drift()
        args = self.authority()
        wrong = {**args, 'phase': 'audit_intake_recovery'}
        error = await self.flow.rejection_text('recover_storyboard_handoff', {**wrong, 'action': 'plan'})
        self.assertIn('controlled_phase', error)
        plan = await self.plan(args)
        error = await self.flow.rejection_text('recover_storyboard_handoff', {
            **args, 'action': 'execute', 'expected_plan_sha256': '0' * 64,
            'confirmation': 'REVALIDATE_STALE_ENVIRONMENT_PRESERVE_HISTORY'})
        self.assertIn('plan_stale', error)
        original = self.store.atomic_write_json

        def interrupt(path, value):
            if Path(path).parent.name == 'handoff_recoveries' and value.get('status') == 'committed':
                raise OSError('synthetic interruption before publication')
            return original(path, value)

        with patch.object(self.store, 'atomic_write_json', side_effect=interrupt):
            with self.assertRaisesRegex(Exception, 'synthetic interruption'):
                await self.execute(args, plan)
        child = next(row for row in self.store.list_project_records_strict(
            self.namespace, self.project, kind='handoffs') if row.get('audit_intake_recovery_id'))
        error = await self.flow.rejection_text('audit_storyboard', {
            **self.audit_args, 'handoff_artifact_id': child['handoff_id'],
            'semantic_audit': declare_unknown(self.service, self.flow.settings, self.project,
                                             self.artifact['artifact_id'])})
        self.assertIn('recovery_not_committed', error)
        result = await self.execute(args, plan)
        self.assertEqual(result['data']['mappings'][0]['new_handoff_id'], child['handoff_id'])
        for path, raw in self.history.items():
            self.assertEqual(path.read_bytes(), raw, str(path))


if __name__ == '__main__':
    unittest.main()
