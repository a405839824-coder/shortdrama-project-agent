"""Synthetic software regressions, never acceptance of user storyboards."""
import copy
import hashlib
import io
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from shortdrama_mcp.audit_coverage import build_obligations, coverage_context
from shortdrama_mcp.file_io import extract_xlsx
from shortdrama_mcp.service import StoryboardService, canonical_storyboard_sha256
from semantic_audit_helpers import semantic_audit_for_artifact
from test_v0556_audit_intake_coverage import workbook_bytes
import test_efficiency as efficiency_support
import validate_storyboard


def external_bytes():
    book = load_workbook(io.BytesIO(workbook_bytes()))
    row = [cell.value for cell in book['正式分镜母版'][2]]
    row[0], row[2] = 'EP10-002', 8
    book['正式分镜母版'].append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def declare_unknown(service, settings, project, artifact):
    value = semantic_audit_for_artifact(service, settings, project, artifact)
    for check in value['coverage']['checks']:
        check['status'] = 'unknown'
        check['judgement'] = '合成软件回归：核对实际字段绑定，内容语义无法确认，不宣称用户作品通过。'
    for layer in value['layers']:
        layer['status'] = 'fail_current'
    value['conclusion']['status'] = 'fail_current'
    return value


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.candidate = extract_xlsx(external_bytes(), audit_only=True)['canonical_storyboard']
        self.service = StoryboardService.__new__(StoryboardService)
        for name in ('ruleset', 'validator', 'runtime', 'schema'):
            setattr(self.service, '_' + name + '_fingerprint', 'e' * 64)

    def submission(self):
        with patch.object(self.service, '_storyboard_from_artifact', return_value=(self.candidate, {})), \
             patch('semantic_audit_helpers.request_namespace', return_value='test'):
            return declare_unknown(self.service, None, 'P', 'A')

    def attach(self, submission):
        return self.service._attach_independent_evidence_matrix(
            validate_storyboard.validate_project(self.candidate, mode='audit'), self.candidate, submission)

    def test_all_actual_voice_fields_required_independent_of_dialogue_syntax(self):
        for picture in ('甲：快走。', '甲问道：“去哪？”', '客观旁白：天亮了。', '甲低哼一声。', '无对白。'):
            with self.subTest(picture=picture):
                self.candidate['shots'][0]['画面内容'] = picture
                value = self.submission()
                result = self.attach(value)
                proof = result['independent_evidence_matrix']['voice_field_coverage']
                self.assertEqual(proof['expected_voice_field_count'], 2)
                self.assertEqual(proof['reviewed_voice_field_count'], 2)
                layer = next(row for row in value['layers'] if row['layer'] == '禁项与声音')
                layer['evidence_ids'].remove('field:EP10-001.音色')
                with self.assertRaisesRegex(ValueError, 'voice_field_coverage_incomplete'):
                    self.attach(value)

    def test_current_workbook_cell_can_explain_issue_but_arbitrary_cell_cannot(self):
        manifest = build_obligations(self.candidate, coverage_context(self.service))
        ref = next(ref for ref in manifest['_values'] if ref.startswith('workbook:') and 'F2' in ref)
        value = self.submission()
        layer = next(row for row in value['layers'] if row['layer'] == '机位可见性')
        layer['evidence_ids'].append(ref)
        value['issues'].append({'issue_id': 'fixture-keyframe', 'error_code': 'E-T2I-001',
            'layer': layer['layer'], 'severity': 'fail', 'blocking': True,
            'shot_ids': ['EP10-001'], 'batch_ids': [], 'field': 'GPT关键帧生成指令',
            'evidence_ids': [ref], 'evidence': '合成软件案例：当前F格内容与测试设定不一致。',
            'repair_action': '合成软件案例：只修正该静态首帧字段，保留其他字段。'})
        self.assertEqual(self.attach(value)['status'], 'fail_current')
        for invalid in ('workbook:正式分镜母版!ZZ999', 'workbook:10集_逐镜阅读!F2'):
            bad = copy.deepcopy(value)
            target = next(row for row in bad['layers'] if row['layer'] == layer['layer'])
            target['evidence_ids'][-1] = invalid
            bad['issues'][0]['evidence_ids'] = [invalid]
            with self.assertRaisesRegex(ValueError, 'workbook_binding_mismatch'):
                self.attach(bad)

    def test_unknown_no_issue_reports_gap_but_does_not_complete_or_pass(self):
        value = self.submission()
        before = copy.deepcopy(self.candidate)
        result = self.attach(value)
        self.assertEqual(result['status'], 'fail_current')
        self.assertFalse(result['independent_evidence_matrix']['semantic_obligation_coverage']['complete'])
        self.assertTrue(any(row['error_code'] == 'E-AUDIT-COVERAGE-UNKNOWN' for row in result['issues']))
        self.assertEqual(before, self.candidate)
        value['coverage']['checks'].pop()
        with self.assertRaisesRegex(ValueError, 'audit_coverage_incomplete'):
            self.attach(value)


class CompleteExternalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()

    async def asyncTearDown(self):
        await self.flow.asyncTearDown()

    async def test_original_no_sidecar_reaches_signed_report_and_keeps_bytes(self):
        h = self.flow
        project = await h.create_project('合成软件外部原件完整审计流程')
        raw = external_bytes()
        record = h.service.store.save_artifact_bytes(h.namespace, project, raw,
            file_name='synthetic-external.xlsx', artifact_type='source:storyboard', version='external')
        order = await h.begin_storyboard_task(project, operation='storyboard_audit', mode='audit_only')
        submission = declare_unknown(h.service, h.settings, project, record['artifact_id'])
        audited = await h.call('audit_storyboard', {'project_id': project, 'artifact_id': record['artifact_id'],
            'work_order_id': order, 'semantic_audit': submission})
        self.assertEqual(audited['status'], 'fail_current')
        cert = h.service.store.get_project_record(h.namespace, project, kind='certificates',
            record_id=audited['data']['certificate']['certificate_id'])
        self.assertTrue(h.service.store.project_record_is_signed(cert))
        self.assertEqual(cert['fingerprints']['artifact_raw'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(cert['canonical_sha256'], canonical_storyboard_sha256(extract_xlsx(raw, audit_only=True)['canonical_storyboard']))
        self.assertFalse(cert['semantic_obligation_coverage']['complete'])
        self.assertEqual(cert['report']['quality_metrics']['adjacent_continuity']['reviewed_pair_count'], 1)
        self.assertFalse(cert['report']['quality_metrics']['adjacent_continuity']['proof_verified'])
        _, original = h.service.store.get_artifact(h.namespace, project, record['artifact_id'])
        self.assertEqual(original.read_bytes(), raw)
        self.assertTrue(audited['data']['audited_workbook']['artifact_id'])
        self.assertIsNone(audited['data']['repair_package'])
        self.assertTrue(any(not row.get('shot_ids') for row in cert['report']['issues']))


if __name__ == '__main__':
    unittest.main()
