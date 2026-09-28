"""Synthetic reviewer declarations test protocol integrity, not story quality."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shortdrama_mcp.audit_coverage import (audit_submission_contract, build_obligations,
    read_audit_coverage, read_audit_contract, validate_coverage)
from shortdrama_mcp.audit_coverage_pages import (PAGE_CONTRACT, collection_snapshot,
    collection_report_state, collection_has_signed_audit, coverage_progress,
    process_coverage_page, validate_rebind_snapshot)
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES, SemanticAuditCoveragePageInput, SemanticAuditIssueInput
from shortdrama_mcp.storage import ProjectStore
from shortdrama_mcp.canonical import stable_json_hash
from semantic_audit_helpers import synthetic_coverage


class CoverageRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name), 'https://test.invalid')
        self.ns, self.project = 'fixture', 'SBP-revisions'
        self.store.create_project(self.ns, {'project_id': self.project, 'project_name': 'synthetic', 'work_orders': []},
                                  {'project_id': self.project, 'events': []})
        self.context = {k: 'f' * 64 for k in ('rules', 'schema', 'validator', 'runtime')}
        self.storyboard = {'schema_version': 'R10.1', 'shots': [
            {'镜头编号': f'EP90-{i:03d}', '画面内容': f'甲看向桌上的第{i}只杯子。',
             '原文支撑句': f'甲面前有第{i}只杯子。', '时长': 4, '累计时长': i * 4}
            for i in range(1, 4)]}
        self.manifest = build_obligations(self.storyboard, self.context)
        self.checks = synthetic_coverage(self.manifest)['checks']
        self.authority = {'authority_root_id': 'ROOT-1', 'generation': 0,
                          'actor_binding_id': 'ACT-1', 'session_fingerprint': 'SESSION-1'}
        self.service = SimpleNamespace(store=self.store, _ruleset_fingerprint=self.context['rules'],
            _validator_fingerprint=self.context['validator'], _schema_fingerprint=self.context['schema'],
            _runtime_fingerprint=self.context['runtime'], _active_authority_binding=lambda *args: self.authority,
            _verified_authority_binding_at_generation=lambda *args: self.authority)
        self.artifact = {'artifact_id': 'ART-1', 'fingerprint': 'a' * 64}
        self.fp = {'canonical': self.manifest['canonical_sha256'], 'artifact_raw': 'a' * 64,
                   'source_bytes': 'b' * 64, 'episode_boundary': 'c' * 64, 'project_anchors': 'd' * 64,
                   **self.context}
        self.submission = {'canonical_sha256': self.manifest['canonical_sha256'], 'issues': [],
            'layers': [{'layer': layer, 'status': 'pass', 'evidence_ids': ['shot:EP90-001']}
                       for layer in SEMANTIC_AUDIT_LAYER_NAMES]}
        self.service._storyboard_from_artifact = lambda *a, **kw: (self.storyboard, self.artifact)
        self.service._fingerprint_bundle = lambda *a, **kw: self.fp
        self.service._source_fingerprint = lambda *a, **kw: 'b' * 64
        self.service._boundary_hash = lambda *a, **kw: 'c' * 64

    def call(self, page, submission=None, **kwargs):
        return process_coverage_page(self.service, self.ns, self.project, storyboard=self.storyboard,
            artifact=kwargs.get('artifact', self.artifact), audit_work_order={'work_order_id': 'WO-1'},
            handoff={'handoff_id': kwargs.get('handoff_id', 'HND-1')}, fingerprints=kwargs.get('fingerprints', self.fp),
            submission={**(submission or self.submission), 'coverage_page': page})

    def append(self, start=0, end=42, collection=None, submission=None):
        return self.call({'contract': PAGE_CONTRACT, 'action': 'append',
            'manifest_sha256': self.manifest['manifest_sha256'], 'offset': start,
            **({'collection_id': collection} if collection else {'collection_key': 'first'}),
            'checks': self.checks[start:end]}, submission)

    def snapshot(self, collection):
        return collection_snapshot(self.service, self.ns, self.project, collection)

    def revision(self, collection, **kwargs):
        snapshot = self.snapshot(collection)
        return {'contract': PAGE_CONTRACT, 'action': 'revise', 'collection_id': collection,
            'manifest_sha256': self.manifest['manifest_sha256'], 'expected_snapshot_sha256': snapshot['snapshot_sha256'],
            'page_ids': [r['page_id'] for r in snapshot['pages']], 'reason': '复核实际字段后撤回先前不成立的判断，保留原始记录。', **kwargs}

    def issue(self, identity='ISS-1'):
        return SemanticAuditIssueInput(issue_id=identity, layer='源文对白', error_code='E-SYNTHETIC',
            shot_ids=['EP90-001'], evidence_ids=['field:EP90-001.画面内容'], evidence='合成测试问题，不代表真实内容错误。',
            repair_action='重新核查同一真实字段。').model_dump()

    def stage_error(self, end=42):
        value = copy.deepcopy(self.submission)
        value['issues'] = [self.issue()]
        value['layers'][0]['status'] = 'fail_current'
        self.checks[0].update(status='fail_current', issue_ids=['ISS-1'])
        staged = self.append(end=end, submission=value)
        return staged.data['collection_id'], value

    def test_signed_retraction_preserves_old_pages_and_finalizes_effective_checks(self):
        collection, old_submission = self.stage_error()
        old = self.snapshot(collection)
        replacement = {**self.checks[0], 'status': 'pass', 'issue_ids': []}
        page = self.revision(collection, checks=[replacement], retract_issue_ids=['ISS-1'])
        revised = self.call(page)
        current = self.snapshot(collection)
        self.assertEqual(current['pages'], old['pages'])
        self.assertEqual(current['pages'][0]['checks'][0]['status'], 'fail_current')
        self.assertEqual(current['checks'][0]['status'], 'pass')
        self.assertEqual(current['issues'], [])
        self.assertTrue(self.store.project_record_is_signed(current['revisions'][0]))
        self.assertNotEqual(old['snapshot_sha256'], current['snapshot_sha256'])
        self.assertTrue(self.call(page).data['idempotent_replay'])
        final = self.call({'contract': PAGE_CONTRACT, 'action': 'finalize', 'collection_id': collection,
            'manifest_sha256': self.manifest['manifest_sha256'], 'page_ids': page['page_ids']})
        self.assertEqual(final['coverage']['checks'], current['checks'])
        self.assertEqual(final['_server_coverage_page_receipt']['revision_ids'], [revised.data['revision_id']])
        with self.assertRaisesRegex(ValueError, 'finalize_issue_mismatch'):
            self.call({**page, 'action': 'finalize', 'checks': [], 'retract_issue_ids': [],
                       'reason': None, 'expected_snapshot_sha256': None}, old_submission)

    def test_cas_race_and_retraction_of_still_failing_check_are_rejected(self):
        collection, value = self.stage_error(end=14)
        bad = self.revision(collection, retract_issue_ids=['ISS-1'])
        with self.assertRaisesRegex(ValueError, 'failed_check_requires_issue|failed_check_issue_mismatch'):
            self.call(bad)
        page = self.revision(collection, checks=[{**self.checks[0], 'status': 'pass', 'issue_ids': []}], retract_issue_ids=['ISS-1'])
        self.append(14, 28, collection, value)
        with self.assertRaisesRegex(ValueError, 'snapshot_conflict'):
            self.call(page)
        self.assertEqual(self.snapshot(collection)['revisions'], [])

    def test_continue_after_correction_and_prevent_resurrection(self):
        collection, old = self.stage_error(end=14)
        self.call(self.revision(collection, checks=[{**self.checks[0], 'status': 'pass', 'issue_ids': []}], retract_issue_ids=['ISS-1']))
        before = self.snapshot(collection)
        with self.assertRaisesRegex(ValueError, 'page_issue_conflict'):
            self.append(14, 28, collection, old)
        self.assertEqual(self.snapshot(collection), before)
        self.append(14, 42, collection)
        snapshot = self.snapshot(collection)
        self.assertEqual(len(snapshot['checks']), 42)
        self.assertEqual(snapshot['pages'][1]['revision_head'], snapshot['revision_ids'][0])
        # A missing-evidence layer may add a separately signed issue without
        # rewriting the old unknown judgement or pretending it has passed.
        unknown = {**snapshot['checks'][0], 'status': 'unknown', 'issue_ids': []}
        value = copy.deepcopy(self.submission)
        value['issues'] = [self.issue('MISSING-EVIDENCE')]
        value['layers'][0]['status'] = 'fail_current'
        self.call(self.revision(collection, checks=[unknown], issue_upserts=value['issues']), value)
        self.assertEqual(self.snapshot(collection)['checks'][0]['status'], 'unknown')
        self.assertEqual(len(self.snapshot(collection)['revisions']), 2)

    def test_read_all_old_and_effective_evidence_with_large_issue_fragment(self):
        collection, value = self.stage_error()
        issue = self.issue()
        issue['evidence'] = '合成证据' * 1700
        value['issues'] = [issue]
        self.call(self.revision(collection, issue_upserts=[issue]), value)
        offset, records, hashes = 0, [], set()
        while True:
            response = read_audit_coverage(self.service, self.ns, self.project, 'ART-1', collection_id=collection,
                                          offset=offset, limit=7, max_chars=24000).data
            hashes.add(response['snapshot_sha256'])
            records.extend(response['records'])
            if response['complete']: break
            offset = response['next_offset']
        self.assertEqual(len(hashes), 1)
        fragments = {}
        for record in records:
            if record['kind'] == 'record_fragment': fragments.setdefault(record['record_index'], []).append(record)
        decoded = [r for r in records if r['kind'] != 'record_fragment']
        for parts in fragments.values():
            parts.sort(key=lambda r: r['part_index'])
            text = ''.join(r['text'] for r in parts)
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), parts[0]['json_utf8_sha256'])
            decoded.append(json.loads(text))
        self.assertEqual(sum(r['kind'] == 'original_check' for r in decoded), 42)
        self.assertEqual(sum(r['kind'] == 'effective_check' for r in decoded), 42)
        self.assertTrue(any(r['kind'] == 'original_issue' and r['value']['evidence'] != issue['evidence'] for r in decoded))
        self.assertTrue(any(r['kind'] == 'effective_issue' and r['value'] == issue for r in decoded))
        with self.assertRaisesRegex(ValueError, 'read_artifact_mismatch'):
            read_audit_coverage(self.service, self.ns, self.project, 'ART-other', collection_id=collection)

    def test_rebind_is_explicit_empty_and_rejects_changed_content_or_stale_cas(self):
        collection, _ = self.stage_error()
        old = self.snapshot(collection)
        self.service._ruleset_fingerprint = '0' * 64
        self.context['rules'] = '0' * 64
        self.manifest = build_obligations(self.storyboard, self.context)
        current_fp = {**self.fp, 'rules': '0' * 64}
        request = {**self.revision(collection), 'action': 'rebind'}
        for fp in ({**current_fp, 'source_bytes': 'e' * 64}, {**current_fp, 'project_anchors': 'e' * 64}):
            with self.assertRaisesRegex(ValueError, 'content_or_authority_changed'):
                self.call(request, fingerprints=fp, handoff_id='HND-current')
        with self.assertRaisesRegex(ValueError, 'snapshot_conflict'):
            self.call({**request, 'expected_snapshot_sha256': '0' * 64}, fingerprints=current_fp)
        result = self.call(request, fingerprints=current_fp, handoff_id='HND-current')
        self.assertEqual(result.data['accepted_count'], 0)
        self.assertEqual(result.data['parent']['snapshot_sha256'], old['snapshot_sha256'])
        self.assertEqual(self.snapshot(collection), old)
        child = self.snapshot(result.data['collection_id'])
        self.assertEqual(child['checks'], [])
        self.assertEqual(child['issues'], [])
        self.assertTrue(self.call(request, fingerprints=current_fp, handoff_id='HND-current').data['idempotent_replay'])
        self.assertFalse(collection_has_signed_audit(self.service, self.ns, self.project, self.storyboard, child['collection']))

    def test_bad_revision_signature_and_page_tamper_fail_closed(self):
        collection, _ = self.stage_error()
        self.call(self.revision(collection, checks=[{**self.checks[0], 'status': 'pass', 'issue_ids': []}], retract_issue_ids=['ISS-1']))
        snapshot = self.snapshot(collection)
        path = self.store.project_dir(self.ns, self.project) / 'audit_coverage_revisions' / (snapshot['revision_ids'][0] + '.json')
        value = json.loads(path.read_text(encoding='utf-8'))
        value['reason'] = '篡改签名修订内容必须被拒绝。'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'integrity_mismatch'):
            self.snapshot(collection)

    def test_current_report_unknown_is_reported_but_not_completed_or_pass(self):
        collection = self.append().data['collection_id']
        snapshot = self.snapshot(collection)
        receipt = {'complete': False, 'unresolved_obligation_ids': ['EP90-001/source_fidelity'],
                   'checks_sha256': snapshot['pages'][0]['checks_sha256']}
        proof = {'collection_id': collection, 'binding': snapshot['collection']['binding'],
            'manifest_sha256': self.manifest['manifest_sha256'], 'page_ids': [r['page_id'] for r in snapshot['pages']],
            'snapshot_sha256': snapshot['snapshot_sha256'], 'revision_ids': [], 'checks_sha256': receipt['checks_sha256']}
        certificate = {'certificate_id': 'CERT-test', 'certificate_type': 'independent_audit', 'work_order_id': 'WO-1',
            'artifact_id': 'ART-1', 'result': 'fail_current', 'audit_coverage_page_receipt': proof,
            'semantic_obligation_coverage': receipt,
            'report': {'audit_coverage_page_receipt': proof, 'quality_metrics': {'semantic_checks': [{'status': 'unknown'}]}}}
        self.store.save_project_record(self.ns, self.project, kind='certificates', record_id='CERT-test', value=certificate)
        with patch('shortdrama_mcp.audit_coverage_pages.verify_certificate_coverage', return_value=receipt):
            state = collection_report_state(self.service, self.ns, self.project, self.storyboard, snapshot['collection'])
            self.assertEqual(state['status'], 'reported_unknown')
            self.assertFalse(state['completed_current_review'])
            self.assertFalse(state['audit_pass'])
            progress = coverage_progress(self.service, self.ns, self.project)
            self.assertEqual(progress['reported_unknown_count'], 1)
            self.assertEqual(progress['pending_count'], 0)
            self.assertEqual(progress['completed_current_count'], 0)
        certificate['report']['quality_metrics']['semantic_checks'] = []
        receipt.update(complete=True, unresolved_obligation_ids=[])
        self.store.save_project_record(self.ns, self.project, kind='certificates', record_id='CERT-test', value=certificate)
        with patch('shortdrama_mcp.audit_coverage_pages.verify_certificate_coverage', return_value=receipt):
            state = collection_report_state(self.service, self.ns, self.project, self.storyboard, snapshot['collection'])
            self.assertEqual(state['status'], 'reported_fail')
            self.assertTrue(state['completed_current_review'])
            self.assertFalse(state['audit_pass'])
            # A later signed amendment invalidates only current use of this
            # certificate; the historical certificate remains unchanged.
            amended = {**self.checks[0], 'judgement': '再次对照相同字段完成复核，保留这次独立说明和旧记录。'}
            self.call(self.revision(collection, checks=[amended]))
            self.assertFalse(collection_has_signed_audit(self.service, self.ns, self.project, self.storyboard, snapshot['collection']))

    def test_conflicting_signed_legacy_pages_are_recoverable_and_need_explicit_resolution(self):
        collection, value = self.stage_error(end=14)
        snapshot = self.snapshot(collection)
        changed = {**self.issue(), 'evidence': '旧客户端在第二页提交了同ID的不同说明。'}
        rows = self.checks[14:42]
        # Reproduce a record that the previous append protocol legally stored.
        self.store.save_project_record(self.ns, self.project, kind='audit_coverage_pages', record_id='ACP-legacy',
            value={'page_id': 'ACP-legacy', 'collection_id': collection, 'binding': snapshot['collection']['binding'],
                   'offset': 14, 'count': len(rows), 'checks': rows, 'checks_sha256': stable_json_hash(rows),
                   'issues': [changed], 'status': 'coverage_pending'})
        snapshot = self.snapshot(collection)
        self.assertEqual(len(snapshot['issue_conflicts']['ISS-1']), 2)
        final = {'contract': PAGE_CONTRACT, 'action': 'finalize', 'collection_id': collection,
            'manifest_sha256': self.manifest['manifest_sha256'], 'page_ids': [r['page_id'] for r in snapshot['pages']]}
        with self.assertRaisesRegex(ValueError, 'page_issue_conflict'):
            self.call(final, value)
        self.call(self.revision(collection, issue_upserts=[self.issue()]), value)
        self.assertEqual(self.snapshot(collection)['issue_conflicts'], {})
        self.assertEqual(self.call(final, value)['coverage']['checks'], self.checks)

    def test_submission_contract_voice_pair_order_and_read_pagination(self):
        self.storyboard['shots'][0]['音色'] = '旁白自然声线。'
        self.storyboard['shots'][1]['音色'] = '无现场声音。'
        for row in self.storyboard['shots']:
            row.update({key: '合成光影字段' for key in ('光影', '光源来源', '光源方向', '色温倾向')})
        contract = audit_submission_contract(self.storyboard)
        self.assertEqual(contract['voice_field_evidence_ids'], ['field:EP90-001.音色', 'field:EP90-002.音色'])
        self.assertEqual(contract['ordered_pair_ids'], ['EP90-001->EP90-002', 'EP90-002->EP90-003'])
        self.assertEqual(contract['adjacent_pair_order_sha256'], hashlib.sha256(
            json.dumps(contract['ordered_pair_ids'], separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
        response = read_audit_contract(self.service, self.ns, self.project, 'ART-1', limit=2)
        self.assertEqual(len(response.data['records']), 2)
        self.assertEqual(response.data['fingerprints'], self.fp)
        self.assertEqual(response.data['next_call']['offset'], 2)

    def test_model_forbids_implicit_rewrite_and_rebinding_with_checks(self):
        collection = self.append().data['collection_id']
        for page in (self.revision(collection), {**self.revision(collection, checks=[self.checks[0]]), 'action': 'rebind'},
                     self.revision(collection, issue_upserts=[self.issue()], retract_issue_ids=['ISS-1'])):
            with self.subTest(page=page), self.assertRaises(ValueError):
                SemanticAuditCoveragePageInput.model_validate(page)


if __name__ == '__main__':
    unittest.main()
