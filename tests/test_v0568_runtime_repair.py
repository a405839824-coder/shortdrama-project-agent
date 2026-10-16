"""A signed failed audit may authorize full revalidation after software changes.

These synthetic fixtures prove authority routing, not user-content acceptance.
"""
import copy
import unittest
import test_v040_full_repair as repair_support


class RuntimeRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = repair_support.CompactMasterFullRepairTests()
        await self.flow.asyncSetUp()
        self.addAsyncCleanup(self.flow.asyncTearDown)

    def change_software(self, certify=True):
        h = self.flow
        h.service._runtime_fingerprint = 'e' * 64
        h.service._scripts_fingerprint = 'd' * 64
        h.service._service_code_fingerprint = 'c' * 64
        if certify:
            h.service._persist_runtime_certificate(h.namespace, h.project_id, {
                'status':'pass', 'version':h.service.bootstrap.get('version'),
                'workflow_revision':h.service.bootstrap.get('workflow_revision')})

    def authorize(self):
        h = self.flow
        order=next(x for x in h.service.store.load_hub(h.namespace,h.project_id)['work_orders']
                   if x['work_order_id']==h.repair_order)
        return h.service._full_repair_authorization(h.namespace,h.project_id,
            work_order=order, source_artifact_id=h.committed['artifact_id'],
            repair_package_id=h.package_id)

    async def test_new_runtime_requires_fresh_validation_and_original_auditor(self):
        h = self.flow
        old=copy.deepcopy(h.service.store.get_project_record(h.namespace,h.project_id,
            kind='certificates',record_id=h.audited['data']['certificate']['certificate_id']))
        self.change_software()
        repaired=await h.call('commit_storyboard',h.full_arguments())
        self.assertEqual(repaired['status'],'production_preflight_pass')
        self.assertEqual(repaired['data']['return_to_audit_work_order_id'],h.original_audit_order)
        self.assertEqual(repaired['data']['handoff']['fingerprints']['runtime'],'e'*64)
        persisted=h.service.store.get_project_record(h.namespace,h.project_id,
            kind='certificates',record_id=old['certificate_id'])
        self.assertEqual(persisted,old)
        final=await h.call('audit_storyboard',{
            'project_id':h.project_id,'artifact_id':repaired['artifact_id'],
            'work_order_id':h.original_audit_order,
            'handoff_artifact_id':repaired['data']['handoff']['handoff_id']})
        self.assertEqual(final['status'],'pass')
        self.assertNotEqual(final['data']['certificate']['certificate_id'],old['certificate_id'])

    async def test_changed_software_without_current_runtime_certificate_rejected(self):
        self.change_software(certify=False)
        with self.assertRaisesRegex(ValueError,'runtime_test_certificate_required'):
            self.authorize()

    async def test_source_contract_changes_still_rejected(self):
        self.change_software()
        h=self.flow
        old=h.service._schema_fingerprint
        h.service._schema_fingerprint='a'*64
        h.service._persist_runtime_certificate(h.namespace,h.project_id,{'status':'pass'})
        with self.assertRaisesRegex(ValueError,'immutable content or contract changed'):
            self.authorize()
        h.service._schema_fingerprint=old

    async def test_unsigned_historical_runtime_rejected(self):
        self.change_software()
        h=self.flow
        runtime_id=h.audited['data']['certificate']['cache_components']['runtime_test_certificate_id']
        original=h.service.store.get_project_record
        def tampered(*args,**kwargs):
            record=original(*args,**kwargs)
            if kwargs.get('record_id')==runtime_id:
                record=copy.deepcopy(record);record['result']='fail_current'
            return record
        h.service.store.get_project_record=tampered
        with self.assertRaisesRegex(ValueError,'historical runtime certificate differs'):
            self.authorize()

    async def test_normal_audit_cannot_reuse_old_route_after_software_change(self):
        self.change_software()
        h=self.flow
        error=await h.rejection('audit_storyboard',{'project_id':h.project_id,
            'artifact_id':h.committed['artifact_id'],'work_order_id':h.original_audit_order,
            'handoff_artifact_id':h.audited['data']['certificate']['handoff_id']})
        self.assertIn('fingerprint',error)


if __name__=='__main__':unittest.main()
