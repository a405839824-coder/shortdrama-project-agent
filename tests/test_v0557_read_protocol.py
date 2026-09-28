"""Public MCP read transport against synthetic artifacts, never user content.

Read assertions parse content[].text as a client with no structuredContent
support would. Runtime certification is stubbed only while seeding audit pages;
the app routes, signed store, original XLSX parsing and read serializers are real.
"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from shortdrama_mcp.audit_coverage_pages import collection_snapshot
from shortdrama_mcp.models import SemanticAuditIssueInput
from test_v0557_audit_intake_pairs import unplanned_project
from test_v0557_audit_submission import declare_unknown, external_bytes
import test_efficiency as efficiency_support


class ReadProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()
        self.project = (await self.raw_call('create_project', {'project_name': '合成协议分页读取'}))['project_id']
        self.raw = external_bytes()
        self.artifact = self.flow.service.store.save_artifact_bytes(self.flow.namespace, self.project, self.raw,
            file_name='synthetic-read-protocol.xlsx', artifact_type='source:storyboard', version='external')
        self.base = {'project_id': self.project, 'artifact_id': self.artifact['artifact_id']}

    async def asyncTearDown(self):
        await self.flow.asyncTearDown()

    async def raw_call(self, name, args):
        result = await self.flow.server.call_tool(name, args)
        texts = [block.text for block in result.content if getattr(block, 'text', None)]
        self.assertTrue(texts, 'MCP content must contain the full recoverable response')
        self.assertFalse(result.is_error, '\n'.join(texts))
        documents = []
        for text in texts:
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(document, dict) and 'status' in document:
                documents.append(document)
        self.assertEqual(len(documents), 1, '\n'.join(texts))
        # Deliberately do not read result.structured_content.
        return documents[0]

    async def read_all(self, section, *, limit=3):
        args = {**self.base, 'section': section, 'limit': limit, 'max_chars': 16000}
        records, pages, identity, offsets = [], [], None, set()
        while args is not None:
            offset = args.get('offset', 0)
            self.assertNotIn(offset, offsets)
            offsets.add(offset)
            response = await self.raw_call('read_project_artifact', args)
            data = response['data']
            current = data.get('snapshot_sha256') or data.get('contract_sha256')
            if identity is not None: self.assertEqual(identity, current)
            identity = current
            self.assertEqual(data['offset'], offset)
            records.extend(data['records'])
            pages.append(data)
            args = data['next_call']
            self.assertEqual(data['complete'], args is None)
            if args:
                self.assertEqual(args['offset'], data['next_offset'])
                self.assertEqual(args['section'], section)
                self.assertEqual(args['artifact_id'], self.artifact['artifact_id'])
        self.assertEqual(len(records), pages[-1]['total_items'])
        return records, pages

    def assert_original_unchanged(self):
        record, path = self.flow.service.store.get_artifact(self.flow.namespace, self.project, self.artifact['artifact_id'])
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertEqual(record['fingerprint'], hashlib.sha256(self.raw).hexdigest())

    async def test_contract_next_call_covers_every_required_ref_and_bare_pair(self):
        records, pages = await self.read_all('audit_contract')
        self.assertGreater(len(pages), 1)
        expected_counts = pages[0]['required_layer_evidence_counts']
        for layer, count in expected_counts.items():
            refs = [r for r in records if r['kind'] == 'required_layer_evidence' and r['layer'] == layer]
            self.assertEqual(len(refs), count)
            self.assertEqual([r['index'] for r in refs], list(range(count)))
            self.assertEqual(len({r['evidence_id'] for r in refs}), count)
        voice = [r['evidence_id'] for r in records if r['kind'] == 'required_layer_evidence'
                 and r['layer'] == '禁项与声音' and r['evidence_id'].endswith('.音色')]
        self.assertEqual(voice, ['field:EP10-001.音色', 'field:EP10-002.音色'])
        pairs = [r['pair_id'] for r in records if r['kind'] == 'ordered_pair']
        self.assertEqual(pairs, ['EP10-001->EP10-002'])
        self.assertEqual(pages[0]['adjacent_pair_order_sha256'], hashlib.sha256(
            json.dumps(pairs, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest())
        self.assertEqual(pages[0]['fingerprints']['artifact_raw'], hashlib.sha256(self.raw).hexdigest())
        self.assertFalse(pages[0]['counts_as_review'])
        self.assert_original_unchanged()

    async def test_recovery_returns_original_and_effective_checks_and_lossless_fragments(self):
        flow = self.flow
        with patch.object(flow.service, '_require_runtime_test_certificate',
                          return_value={'certificate_id': 'CERT-synthetic-read-seeding'}):
            order = await flow.begin_storyboard_task(self.project, operation='storyboard_audit', mode='audit_only')
            submission = declare_unknown(flow.service, flow.settings, self.project, self.artifact['artifact_id'])
            coverage = submission.pop('coverage')
            issue = SemanticAuditIssueInput(issue_id='synthetic-original', layer='源文对白', error_code='E-SYNTHETIC',
                shot_ids=['EP10-001'], evidence_ids=['field:EP10-001.画面内容'],
                evidence='合成问题用于验证原页与追加更正均可恢复。', repair_action='合成协议检查，不修改用户内容。').model_dump()
            submission['issues'] = [issue]
            page = {'contract': 'semantic-obligation-pages-v1', 'action': 'append',
                'manifest_sha256': coverage['manifest_sha256'], 'collection_key': 'read-protocol',
                'offset': 0, 'checks': coverage['checks'][:14]}
            args = {**self.base, 'work_order_id': order}
            first = await self.raw_call('audit_storyboard', {**args, 'semantic_audit': {**submission, 'coverage_page': page}})
            collection = first['data']['collection_id']
            page.pop('collection_key')
            page.update(collection_id=collection, offset=14, checks=coverage['checks'][14:])
            await self.raw_call('audit_storyboard', {**args, 'semantic_audit': {**submission, 'coverage_page': page}})
            snapshot = collection_snapshot(flow.service, flow.namespace, self.project, collection)
            changed = {**coverage['checks'][0], 'judgement': '合成追加复核说明：原字段保留，来源仍未知，不能将协议测试当内容通过。'}
            long_issue = {**issue, 'evidence': '合成协议完整证据。' * 700}
            revised = {**submission, 'issues': [long_issue], 'coverage_page': {
                'contract': 'semantic-obligation-pages-v1', 'action': 'revise', 'collection_id': collection,
                'manifest_sha256': coverage['manifest_sha256'], 'expected_snapshot_sha256': snapshot['snapshot_sha256'],
                'page_ids': [r['page_id'] for r in snapshot['pages']], 'reason': '合成分页软件验收：追加明确修订，保存原页与原问题。',
                'checks': [changed], 'issue_upserts': [long_issue]}}
            await self.raw_call('audit_storyboard', {**args, 'semantic_audit': revised})
        before = collection_snapshot(flow.service, flow.namespace, self.project, collection)
        records, pages = await self.read_all('audit_coverage:' + collection, limit=7)
        self.assertGreater(len(pages), 1)
        decoded = [r for r in records if r['kind'] != 'record_fragment']
        fragments = {}
        for record in records:
            if record['kind'] == 'record_fragment': fragments.setdefault(record['record_index'], []).append(record)
        self.assertTrue(fragments)
        for parts in fragments.values():
            parts.sort(key=lambda r: r['part_index'])
            self.assertEqual([r['part_index'] for r in parts], list(range(parts[0]['part_count'])))
            text = ''.join(r['text'] for r in parts)
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), parts[0]['json_utf8_sha256'])
            decoded.append(json.loads(text))
        original = [r['value'] for r in decoded if r['kind'] == 'original_check']
        effective = [r['value'] for r in decoded if r['kind'] == 'effective_check']
        self.assertEqual(original, coverage['checks'])
        self.assertEqual(effective, [changed, *coverage['checks'][1:]])
        self.assertEqual(sum(r['kind'] == 'original_page' for r in decoded), 2)
        self.assertTrue(any(r['kind'] == 'original_issue' and r['value'] == issue for r in decoded))
        self.assertTrue(any(r['kind'] == 'effective_issue' and r['value'] == long_issue for r in decoded))
        self.assertEqual(collection_snapshot(flow.service, flow.namespace, self.project, collection), before)
        listing, _ = await self.read_all('audit_coverage')
        self.assertEqual([r['collection_id'] for r in listing], [collection])
        self.assertEqual(listing[0]['snapshot_sha256'], before['snapshot_sha256'])
        self.assert_original_unchanged()

    async def test_mixed_read_modes_and_malformed_sections_are_rejected(self):
        for section in ('audit_contract', 'audit_coverage', 'audit_coverage:ACOL-unread'):
            for extra in ({'diagnose_storyboard': True}, {'diagnostic_episode_ids': []},
                          {'bulk_ranges': []}, {'if_none_match': 'stale'}):
                with self.subTest(section=section, extra=extra):
                    text = await self.flow.rejection_text('read_project_artifact', {**self.base, 'section': section, **extra})
                    self.assertIn('cannot be combined', text)
        for section in ('audit_coverage:', 'audit_coverage:one:two'):
            text = await self.flow.rejection_text('read_project_artifact', {**self.base, 'section': section})
            self.assertIn('audit_coverage_section_invalid', text)
        self.assert_original_unchanged()

    async def test_malformed_evidence_shape_is_an_actionable_error_not_a_partial_contract(self):
        cases = ['invalid', ['invalid'], {'dialogue_adaptation_audit': 'invalid'},
                 {'dialogue_adaptation_audit': {'entries': None}},
                 {'dialogue_adaptation_audit': {'entries': ['invalid']}},
                 {'dialogue_adaptation_audit': {'entries': [{'treatment': '原文事实转化对白', 'target_shots': None}]}}]
        for evidence in cases:
            candidate = unplanned_project()
            candidate['prevention_evidence'] = evidence
            before = copy.deepcopy(candidate)
            # This injection isolates malformed already-parsed evidence from
            # workbook intake; the public app response/serializer is real.
            with self.subTest(evidence=evidence), patch.object(self.flow.service, '_storyboard_from_artifact',
                                                              return_value=(candidate, self.artifact)):
                text = await self.flow.rejection_text('read_project_artifact', {**self.base, 'section': 'audit_contract'})
                self.assertIn('audit_contract_malformed_evidence: prevention_evidence', text)
                self.assertNotIn('AttributeError', text)
            self.assertEqual(candidate, before)
        self.assert_original_unchanged()


if __name__ == '__main__':
    unittest.main()
