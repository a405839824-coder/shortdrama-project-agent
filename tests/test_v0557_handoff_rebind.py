"""Synthetic protocol tests: no user files or claims of content acceptance."""
import copy
import unittest
from unittest.mock import patch

from shortdrama_mcp.audit_coverage_pages import collection_snapshot
from test_v0557_audit_submission import declare_unknown, external_bytes
import test_efficiency as efficiency_support


class IntakeHandoffRebindTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()
        f = self.flow
        # This suite tests routing and signed storage, not runtime certification.
        self.certificate = patch.object(f.service, '_require_runtime_test_certificate',
                                        return_value={'certificate_id': 'CERT-synthetic-rebind'})
        self.certificate.start()
        self.project = (await f.call('create_project', {'project_name': '合成外部审计续审'}))['project_id']
        self.raw = external_bytes()
        self.artifact = f.service.store.save_artifact_bytes(f.namespace, self.project, self.raw,
            file_name='synthetic-rebind.xlsx', artifact_type='source:storyboard', version='external')
        self.order = await f.begin_storyboard_task(self.project, operation='storyboard_audit', mode='audit_only')
        self.submission = declare_unknown(f.service, f.settings, self.project, self.artifact['artifact_id'])
        coverage = self.submission.pop('coverage')
        self.append = copy.deepcopy(self.submission)
        self.append['coverage_page'] = {'contract': 'semantic-obligation-pages-v1', 'action': 'append',
            'manifest_sha256': coverage['manifest_sha256'], 'collection_key': 'old-runtime-synthetic',
            'offset': 0, 'checks': coverage['checks'][:2]}
        self.first = await self.call(self.append)
        self.old = f.service.store.list_project_records_strict(f.namespace, self.project, kind='handoffs')[0]
        self.snapshot = collection_snapshot(f.service, f.namespace, self.project, self.first['data']['collection_id'])
        self.original_records = self.records()
        f.service._runtime_fingerprint = '1' * 64
        f.service._ruleset_fingerprint = '2' * 64
        self.current = declare_unknown(f.service, f.settings, self.project, self.artifact['artifact_id'])
        self.current_coverage = self.current.pop('coverage')
        self.rebind = copy.deepcopy(self.current)
        self.rebind['coverage_page'] = {'contract': 'semantic-obligation-pages-v1', 'action': 'rebind',
            'manifest_sha256': self.current_coverage['manifest_sha256'],
            'collection_id': self.first['data']['collection_id'],
            'expected_snapshot_sha256': self.snapshot['snapshot_sha256'],
            'page_ids': [r['page_id'] for r in self.snapshot['pages']],
            'reason': '合成软件回归：规则升级后重新绑定，历史检查不复制到当前。'}

    async def asyncTearDown(self):
        self.certificate.stop()
        await self.flow.asyncTearDown()

    async def call(self, submission, *, handoff=None):
        return await self.flow.call('audit_storyboard', {'project_id': self.project,
            'artifact_id': self.artifact['artifact_id'], 'work_order_id': self.order,
            'handoff_artifact_id': handoff, 'semantic_audit': submission})

    def records(self):
        store = self.flow.service.store
        return {kind: store.list_project_records_strict(self.flow.namespace, self.project, kind=kind)
                for kind in ('handoffs', 'audit_coverage_collections', 'audit_coverage_pages')}

    def assert_no_mint(self):
        self.assertEqual(self.records(), self.original_records)

    async def reject(self, submission, expected, *, handoff=True):
        text = await self.flow.rejection_text('audit_storyboard', {'project_id': self.project,
            'artifact_id': self.artifact['artifact_id'], 'work_order_id': self.order,
            'handoff_artifact_id': self.old['handoff_id'] if handoff else None,
            'semantic_audit': submission})
        self.assertIn(expected, text)
        self.assert_no_mint()

    async def test_actual_route_preserves_old_signed_records_and_starts_empty_child(self):
        f = self.flow
        reply = await self.call(self.rebind, handoff=self.old['handoff_id'])
        self.assertEqual(reply['status'], 'coverage_pending')
        data = reply['data']
        self.assertEqual(data['accepted_count'], 0)
        self.assertEqual(data['accepted_page_ids'], [])
        self.assertFalse(data['audit_pass'])
        self.assertFalse(data['formal_delivery_ready'])
        self.assertEqual(data['previous_handoff_id'], self.old['handoff_id'])
        self.assertEqual(data['audit_cycle_id'], self.old['audit_cycle_id'])
        current = data['handoff_manifest']
        self.assertNotEqual(current['handoff_id'], self.old['handoff_id'])
        self.assertEqual(current['fingerprints']['runtime'], '1' * 64)
        store = f.service.store
        saved = store.get_project_record(f.namespace, self.project, kind='handoffs', record_id=current['handoff_id'])
        self.assertTrue(store.project_record_is_signed(saved))
        self.assertEqual(saved['materialization_passes'], 0)
        self.assertFalse(saved['objective_evidence']['historical_production_verified'])
        self.assertEqual(collection_snapshot(f.service, f.namespace, self.project,
                         self.first['data']['collection_id']), self.snapshot)
        self.assertEqual(store.get_project_record(f.namespace, self.project, kind='handoffs',
                         record_id=self.old['handoff_id']), self.old)
        repeated = await self.call(self.rebind, handoff=self.old['handoff_id'])
        self.assertEqual(repeated['data']['collection_id'], data['collection_id'])
        self.assertEqual(repeated['data']['handoff_manifest']['handoff_id'], current['handoff_id'])
        self.assertTrue(repeated['data']['idempotent_replay'])
        self.assertEqual(len(self.records()['handoffs']), 2)
        # A fresh unknown check can be appended only to the new binding.
        next_page = copy.deepcopy(self.current)
        next_page['coverage_page'] = {'contract': 'semantic-obligation-pages-v1', 'action': 'append',
            'manifest_sha256': self.current_coverage['manifest_sha256'], 'collection_id': data['collection_id'],
            'offset': 0, 'checks': self.current_coverage['checks'][:1]}
        staged = await self.call(next_page, handoff=current['handoff_id'])
        self.assertEqual(staged['data']['accepted_count'], 1)
        child = collection_snapshot(f.service, f.namespace, self.project, data['collection_id'])
        self.assertEqual(child['checks'][0]['status'], 'unknown')
        self.assertEqual(child['collection']['parent']['snapshot_sha256'], self.snapshot['snapshot_sha256'])
        _, path = store.get_artifact(f.namespace, self.project, self.artifact['artifact_id'])
        self.assertEqual(path.read_bytes(), self.raw)

    async def test_upgrade_never_silently_rebinds_append_or_ordinary_audit(self):
        await self.reject(self.append, 'handoff_fingerprint_mismatch')
        await self.reject(self.current, 'handoff_fingerprint_mismatch')
        await self.reject(self.rebind, 'explicit_handoff_required', handoff=False)

    async def test_cas_page_sequence_and_no_copy_are_validated_before_mint(self):
        for updates, message in (({'expected_snapshot_sha256': 'f' * 64}, 'snapshot_conflict'),
                                 ({'page_ids': []}, 'snapshot_conflict'),
                                 ({'checks': self.current_coverage['checks'][:1]}, 'cannot_copy_review')):
            with self.subTest(message=message):
                request = copy.deepcopy(self.rebind)
                request['coverage_page'].update(updates)
                await self.reject(request, message)

    async def test_source_boundary_anchors_artifact_or_work_order_change_cannot_mint(self):
        f = self.flow
        for field in ('source_bytes', 'normalized_source', 'episode_boundary', 'artifact_raw',
                      'artifact_semantic', 'canonical', 'project_anchors'):
            with self.subTest(field=field):
                original = f.service._fingerprint_bundle
                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    result[field] = 'f' * 64
                    return result
                with patch.object(f.service, '_fingerprint_bundle', side_effect=changed):
                    await self.reject(self.rebind, 'content_or_authority_changed')
        with patch.object(f.service, '_source_fingerprint', return_value='different-current-source'):
            await self.reject(self.rebind, 'identity_changed')
        with patch.object(f.service, '_boundary_hash', return_value='different-current-boundary'):
            await self.reject(self.rebind, 'identity_changed')
        foreign = {**self.snapshot['collection']['binding']['authority'],
                   'authority_root_id': 'OTHER-AUDITOR-ROOT'}
        with patch.object(f.service, '_active_authority_binding', return_value=foreign):
            await self.reject(self.rebind, 'authority_operation_fence_stale')
        other_order = await f.begin_storyboard_task(self.project, operation='storyboard_audit', mode='audit_only',
                                                   window_key='other-audit-window')
        text = await f.rejection_text('audit_storyboard', {'project_id': self.project,
            'artifact_id': self.artifact['artifact_id'], 'work_order_id': other_order,
            'handoff_artifact_id': self.old['handoff_id'], 'semantic_audit': self.rebind})
        self.assertIn('identity_changed', text)
        self.assert_no_mint()

    async def test_unsigned_parent_and_production_identity_cannot_mint(self):
        f = self.flow
        path = f.service.store.project_dir(f.namespace, self.project) / 'audit_coverage_collections' / (
            self.first['data']['collection_id'] + '.json')
        raw = path.read_bytes()
        try:
            import json
            value = json.loads(raw)
            value.pop('integrity_hmac')
            path.write_text(json.dumps(value), encoding='utf-8')
            text = await f.rejection_text('audit_storyboard', {'project_id': self.project,
                'artifact_id': self.artifact['artifact_id'], 'work_order_id': self.order,
                'handoff_artifact_id': self.old['handoff_id'], 'semantic_audit': self.rebind})
            self.assertRegex(text, 'signature|integrity')
        finally:
            path.write_bytes(raw)
        self.assert_no_mint()
        storyboard, record = f.service._storyboard_from_artifact(f.namespace, self.project, self.artifact['artifact_id'])
        order = {'work_order_id': self.order}
        production = {**self.old, 'handoff_type': 'production_to_audit'}
        with patch.object(f.service.store, 'get_project_record', return_value=production), \
             patch.object(f.service.store, 'project_record_is_signed', return_value=True):
            with self.assertRaisesRegex(ValueError, 'identity_changed'):
                f.service._rebind_audit_intake(f.namespace, self.project, storyboard=storyboard,
                    record=record, audit_work_order=order, handoff=production,
                    fingerprints={}, source_fingerprint=self.old['source_fingerprint'],
                    boundary_hash=self.old['boundary_hash'], submission=self.rebind, stage_trace_id='TRC-synthetic')
        self.assert_no_mint()

    async def test_replay_revalidates_existing_successor_identity_and_signature(self):
        import json
        f = self.flow
        reply = await self.call(self.rebind, handoff=self.old['handoff_id'])
        identity = reply['data']['handoff_manifest']['handoff_id']
        path = f.service.store.project_dir(f.namespace, self.project) / 'handoffs' / (identity + '.json')
        original = path.read_bytes()
        expected_records = self.records()
        for key, value in (('canonical_sha256', 'f' * 64), ('source_fingerprint', 'changed'),
                           ('boundary_hash', 'changed'), ('audit_class', 'production_preflight'),
                           ('from_window_role', 'production'), ('to_window_role', 'production'),
                           ('status', 'rejected'), ('handoff_type', 'production_to_audit')):
            with self.subTest(key=key):
                try:
                    changed = json.loads(original)
                    changed[key] = value
                    changed['integrity_hmac'] = f.service.store._record_integrity_hmac(changed)
                    path.write_text(json.dumps(changed), encoding='utf-8')
                    text = await f.rejection_text('audit_storyboard', {'project_id': self.project,
                        'artifact_id': self.artifact['artifact_id'], 'work_order_id': self.order,
                        'handoff_artifact_id': self.old['handoff_id'], 'semantic_audit': self.rebind})
                    self.assertIn('successor_conflict', text)
                finally:
                    path.write_bytes(original)
                self.assertEqual(self.records(), expected_records)
        try:
            changed = json.loads(original)
            changed.pop('integrity_hmac')
            path.write_text(json.dumps(changed), encoding='utf-8')
            text = await f.rejection_text('audit_storyboard', {'project_id': self.project,
                'artifact_id': self.artifact['artifact_id'], 'work_order_id': self.order,
                'handoff_artifact_id': self.old['handoff_id'], 'semantic_audit': self.rebind})
            self.assertIn('successor_conflict', text)
        finally:
            path.write_bytes(original)
        self.assertEqual(self.records(), expected_records)


if __name__ == '__main__':
    unittest.main()
