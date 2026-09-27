"""A09: real immutable export -> one-shot patch/rename -> independent reaudit."""
import copy
import hashlib
import unittest

import test_efficiency as support
from test_v040_architecture import three_shot_storyboard
from semantic_audit_helpers import failing_semantic_audit_for_artifact
from shortdrama_mcp.file_io import extract_xlsx


class FrozenDraftDeltaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = support.EfficiencyContractTests()
        await self.h.asyncSetUp()

    async def asyncTearDown(self):
        await self.h.asyncTearDown()

    async def test_single_shot_renamed_export_retains_frozen_first_delivery_and_receipts(self):
        h = self.h
        project = await h.create_project('冻结首交付与单镜改名导出')

        async def begin(operation, mode, window):
            response = await h.call('begin_project_task', {
                'project_id': project, 'operation': operation, 'mode': mode,
                'authorization': 'read_only' if mode == 'audit_only' else 'explicit_execute',
                'request_text': 'A09软件事务回归；合成独审声明不作真实首稿验收',
                'targets': ['EP01-EP03'], 'authoritative_source': '合成测试源文与独审证书',
                'latest_version': 'current', 'target_boundary': 'EP01-EP03',
                'must_keep': ['未修改镜和冻结原件'], 'must_remove': [], 'protected_scope': [],
                'expected_outputs': ['只修首镜光源并改名导出'], 'window_key': window})
            return response['work_order_id']

        order = await begin('storyboard_create', 'execute', 'a09-create')
        source = three_shot_storyboard()
        source['shots'][0]['光源来源'] = '右侧木格窗透入的日光'
        first = await h.commit_fixture(project, order, source, file_name='首次冻结交付.xlsx')
        self.assertEqual(first['status'], 'production_preflight_pass')
        original, original_path = h.service.store.get_artifact(h.namespace, project, first['artifact_id'])
        frozen_raw = original_path.read_bytes()
        frozen = extract_xlsx(frozen_raw)['canonical_storyboard']
        audit_order = await begin('storyboard_audit', 'audit_only', 'a09-audit')
        evidence = failing_semantic_audit_for_artifact(h.service, h.settings, project, first['artifact_id'],
            shot_id='EP01-001', field='光源来源', error_code='E-TEMPLATE-008', issue_id='shots[1].a09_first_light',
            evidence='源场景左侧窗光，首镜误写右侧；其余镜经合成测试声明完成适用审阅。',
            repair_action='只把首镜光源恢复为左侧木格窗透入的日光。')
        failed = await h.call('audit_storyboard', {'project_id': project, 'artifact_id': first['artifact_id'],
            'work_order_id': audit_order, 'semantic_audit': evidence})
        self.assertEqual(failed['status'], 'fail_current')
        failed_cert = h.service.store.get_project_record(h.namespace, project, kind='certificates',
            record_id=failed['data']['certificate']['certificate_id'])
        saved_failed_cert = copy.deepcopy(failed_cert)
        first_quality = failed_cert['audit_summary']['first_draft_quality']
        self.assertEqual(first_quality['completed_shot_count'], 3)
        self.assertEqual(first_quality['passed_shot_count'], 2)
        repair_order = await begin('storyboard_repair', 'audit_and_repair', 'a09-repair')
        args = h._patch_arguments(project, repair_order, first, failed)
        args['file_name'] = '单镜修复后的改名交付.xlsx'
        patched = await h.call('commit_storyboard_patch', args)
        self.assertEqual(patched['status'], 'production_preflight_pass')
        self.assertEqual(patched['data']['impact_scope'], ['EP01-001'])
        self.assertEqual(len(patched['data']['applied_operations']), 1)
        updated, updated_path = h.service.store.get_artifact(h.namespace, project, patched['artifact_id'])
        self.assertNotEqual(updated['artifact_id'], original['artifact_id'])
        self.assertEqual(updated['file_name'], '单镜修复后的改名交付.xlsx')
        actual = extract_xlsx(updated_path.read_bytes())['canonical_storyboard']
        self.assertEqual(actual['shots'][1:], frozen['shots'][1:])
        self.assertEqual(actual['shots'][0]['光源来源'], '左侧木格窗透入的日光')
        final = await h.call('audit_storyboard', {'project_id': project, 'artifact_id': patched['artifact_id'],
            'work_order_id': audit_order, 'handoff_artifact_id': patched['data']['handoff']['handoff_id']})
        self.assertEqual(final['status'], 'pass')
        final_cert = h.service.store.get_project_record(h.namespace, project, kind='certificates',
            record_id=final['data']['certificate']['certificate_id'])
        self.assertEqual(final_cert['audit_summary']['first_draft_quality'], first_quality)
        self.assertEqual(final_cert['report']['quality_metrics']['review_completion']['passed_shot_count'], 3)
        self.assertEqual(final_cert['semantic_obligation_coverage']['expected_count'], 42)
        self.assertTrue(final_cert['semantic_obligation_coverage']['complete'])
        self.assertEqual(final_cert['report']['quality_metrics']['review_completion']['unknown_shot_count'], 0)
        self.assertEqual(original_path.read_bytes(), frozen_raw)
        self.assertEqual(hashlib.sha256(frozen_raw).hexdigest(), original['fingerprint'])
        self.assertEqual(h.service.store.get_project_record(h.namespace, project, kind='certificates',
            record_id=failed_cert['certificate_id']), saved_failed_cert)
        self.assertNotEqual(final_cert['certificate_id'], failed_cert['certificate_id'])
        self.assertNotEqual(final_cert['fingerprints']['artifact_raw'], failed_cert['fingerprints']['artifact_raw'])
        events = h.service.store.load_ledger(h.namespace, project)['events']
        independent = [row for row in events if row.get('audit_class') == 'independent_audit']
        self.assertTrue(independent)
        for row in independent:
            self.assertEqual(row['first_delivery_quality'], first_quality)
            self.assertIsNone(row['raw_generation_quality'])


if __name__ == '__main__':
    unittest.main()
