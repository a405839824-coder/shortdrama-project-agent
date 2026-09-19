"""Coverage enforcement only; all reviewer conclusions here are synthetic."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shortdrama_mcp.audit_coverage import (
    CONTRACT, LEGACY_PREFIX, PAGE_PREFIX, build_obligations, coverage_context,
    normalize_coverage_transport, validate_coverage, verify_certificate_coverage,
)
from shortdrama_mcp.formal_delivery import inspect_formal_delivery
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES, SemanticAuditInput
from shortdrama_mcp.external_storage import ExternalProjectStorage
from semantic_audit_helpers import synthetic_coverage
import test_v0535_production_contract as current_contract_tests


def legacy_envelope(submission, manifest):
    result = copy.deepcopy(submission)
    coverage = result.pop('coverage')
    layers = {r['obligation_id']: r['layer'] for r in manifest['obligations']}
    for layer in result['layers']:
        data = {**coverage, 'checks': [row for row in coverage['checks']
                                     if layers[row['obligation_id']] == layer['layer']]}
        text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        layer['evidence_ids'].extend(
            f'{LEGACY_PREFIX}{i:05d}:' + text[start:start+220]
            for i, start in enumerate(range(0, len(text), 220)))
    return result


class CoverageUnitTests(unittest.TestCase):
    def setUp(self):
        self.context = {k: 'f'*64 for k in ('rules', 'schema', 'validator', 'runtime')}
        self.storyboard = {'schema_version': 'R10.1', 'shots': [
            {'镜头编号': f'EP90-{i:03d}', '画面内容': f'甲看向桌上的第{i}只杯子。',
             '原文支撑句': f'甲面前有第{i}只杯子。', '时长': 4, '累计时长': i*4}
            for i in range(1, 4)]}
        self.manifest = build_obligations(self.storyboard, self.context)
        self.submission = {'coverage': synthetic_coverage(self.manifest), 'issues': [],
                           'layers': [{'layer': layer, 'status': 'pass',
                                       'evidence_ids': ['shot:EP90-001']}
                                      for layer in SEMANTIC_AUDIT_LAYER_NAMES]}

    def validate(self, value=None):
        return validate_coverage(self.storyboard, value or self.submission, self.context)

    def test_all_current_checks_are_bound_and_covered(self):
        receipt = self.validate()
        self.assertEqual(receipt['expected_count'], 39)
        self.assertEqual(receipt['reviewed_count'], 39)
        ownership = next(r for r in self.manifest['obligations'] if r['rule_id'] == 'field_ownership')
        self.assertEqual(len(ownership['required_observation_refs']), 27)

    def test_missing_one_shot_one_rule_and_duplicates_rejected(self):
        for kind in ('shot', 'rule', 'duplicate', 'extra'):
            data = copy.deepcopy(self.submission)
            rows = data['coverage']['checks']
            if kind == 'shot': data['coverage']['checks'] = [r for r in rows if not r['obligation_id'].startswith('EP90-002/')]
            elif kind == 'rule': rows.pop()
            elif kind == 'duplicate': rows.append(copy.deepcopy(rows[0]))
            else: rows[0]['obligation_id'] = 'EP90-999/field_ownership'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'audit_coverage_(incomplete|duplicate)'):
                self.validate(data)

    def test_stale_unknown_empty_and_wrong_content_rejected(self):
        mutations = [
            ('dependency_sha256', '0'*64, 'dependency_stale'),
            ('status', 'unknown', 'unresolved'),
            ('status', 'not_applicable', 'unresolved'),
            ('judgement', '已通过', 'judgement_required'),
            ('observations', [], 'observation_missing'),
            ('observations', [{'reference': 'field:EP90-001.画面内容', 'quote': 'NOT CURRENT'}], 'observation_missing'),
        ]
        for key, value, error in mutations:
            data = copy.deepcopy(self.submission)
            data['coverage']['checks'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, error):
                self.validate(data)
        for quote in ('', '甲看向桌上的第2只杯子。'):
            data = copy.deepcopy(self.submission)
            data['coverage']['checks'][0]['observations'][0]['quote'] = quote
            with self.assertRaisesRegex(ValueError, 'observation_not_in_current_field'): self.validate(data)

    def test_changed_field_source_rules_and_neighbor_invalidate_manifest(self):
        for change in ('neighbor', 'source', 'rules'):
            data, context = copy.deepcopy(self.storyboard), dict(self.context)
            if change == 'neighbor': data['shots'][1]['画面内容'] += '甲收回手。'
            elif change == 'source': data['source_mapping'] = {'changed': True}
            else: context['rules'] = '0'*64
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'manifest_stale'):
                validate_coverage(data, self.submission, context)

    def test_fail_requires_actual_corresponding_issue(self):
        value = copy.deepcopy(self.submission)
        check = value['coverage']['checks'][0]
        check['status'] = 'fail_current'
        with self.assertRaisesRegex(ValueError, 'failed_check_requires_issue'): self.validate(value)
        value['layers'][0]['status'] = 'fail_current'
        check['issue_ids'] = ['semantic-1']
        value['issues'] = [{'issue_id': 'semantic-1', 'layer': '源文对白', 'shot_ids': ['EP90-001']}]
        self.assertEqual(self.validate(value)['failed_count'], 1)

    def test_legacy_chunks_equal_typed_and_malformed_chunks_never_bypass(self):
        legacy = legacy_envelope(self.submission, self.manifest)
        normalized = normalize_coverage_transport(legacy)
        # Transport order is irrelevant to semantics, but stable hashes bind
        # the exact accepted order instead of reusing a different submission.
        self.assertEqual(self.validate(normalized)['reviewed_count'], 39)
        for mutation in ('conflict', 'duplicate', 'reorder', 'missing', 'wrong_layer', 'duplicate_json'):
            value = copy.deepcopy(legacy)
            layer = value['layers'][0]
            if mutation == 'conflict': value['coverage'] = self.submission['coverage']
            elif mutation == 'duplicate': layer['evidence_ids'].append(layer['evidence_ids'][-1])
            elif mutation == 'reorder': layer['evidence_ids'][1], layer['evidence_ids'][2] = layer['evidence_ids'][2], layer['evidence_ids'][1]
            elif mutation == 'missing': layer['evidence_ids'].pop()
            elif mutation == 'wrong_layer': layer['layer'] = '表演链'
            else:
                text = '{"contract":"x","contract":"y"}'
                layer['evidence_ids'] = ['shot:EP90-001', f'{LEGACY_PREFIX}00000:{text}']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate(normalize_coverage_transport(value))

    def test_pending_declared_finding_cannot_be_dropped_or_reset_with_new_key(self):
        from shortdrama_mcp.storage import ProjectStore
        from shortdrama_mcp.audit_coverage_pages import process_coverage_page
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(Path(folder), 'https://test.invalid')
            project = 'SBP-coverage-fixture'
            store.create_project('fixture', {'project_id': project, 'project_name': 'synthetic', 'work_orders': []},
                                 {'project_id': project, 'events': []})
            service = SimpleNamespace(store=store, _ruleset_fingerprint=self.context['rules'],
                _validator_fingerprint=self.context['validator'], _schema_fingerprint=self.context['schema'],
                _runtime_fingerprint=self.context['runtime'],
                _active_authority_binding=lambda *args: {'authority_root_id': 'ROOT-1', 'generation': 0})
            value = copy.deepcopy(self.submission)
            value['canonical_sha256'] = self.manifest['canonical_sha256']
            value['issues'] = [{'issue_id': 'ISS-1', 'layer': '源文对白', 'shot_ids': ['EP90-001'], 'evidence': '合成测试发现'}]
            value['layers'][0]['status'] = 'fail_current'
            page = {'contract': 'semantic-obligation-pages-v1', 'action': 'append',
                    'collection_key': 'first', 'manifest_sha256': self.manifest['manifest_sha256'],
                    'offset': 0, 'checks': value.pop('coverage')['checks']}
            def call(payload, submission=value):
                return process_coverage_page(service, 'fixture', project, storyboard=self.storyboard,
                    artifact={'artifact_id': 'ART-1', 'fingerprint': 'f'*64}, audit_work_order={'work_order_id': 'WO-1'},
                    handoff={'handoff_id': 'HND-1'}, fingerprints={'canonical': self.manifest['canonical_sha256']},
                    submission={**submission, 'coverage_page': payload})
            staged = call(page)
            saved = store.get_project_record('fixture', project, kind='audit_coverage_pages', record_id=staged.data['page_id'])
            self.assertEqual(saved['issues'], value['issues'])
            clean = {**value, 'issues': []}
            with self.assertRaisesRegex(ValueError, 'collection_already_pending'):
                call({**page, 'collection_key': 'hide-finding'}, clean)
            with self.assertRaisesRegex(ValueError, 'finalize_issue_mismatch'):
                call({'contract': page['contract'], 'action': 'finalize', 'manifest_sha256': page['manifest_sha256'],
                      'collection_id': staged.data['collection_id'], 'page_ids': [staged.data['page_id']]}, clean)


class CoverageCurrentPipelineTests(unittest.TestCase):
    def test_paged_current_flow_is_pending_until_explicit_complete_finalization(self):
        from shortdrama_mcp.config import Settings
        from mcp.server.auth.provider import AccessToken
        authenticated = patch('shortdrama_mcp.auth.get_access_token', return_value=AccessToken(
            token='synthetic-test-token', client_id='test', subject='coverage-test-auditor',
            scopes=['storyboard:read', 'storyboard:write'], claims={'iss': 'https://test.invalid'}))
        authenticated.start(); self.addCleanup(authenticated.stop)
        transport = patch('shortdrama_mcp.app._window_session_id', return_value='synthetic-production-session')
        transport.start(); self.addCleanup(transport.stop)
        flow = current_contract_tests.CurrentMcpContractTests()
        namespace = 'https://test.invalid|coverage-test-auditor'
        from test_v055_quality_migration import V055QualityAndMigrationTests
        from test_v0527_mcp_production_authoring import McpProductionAuthoringTests
        with patch('test_v055_quality_migration.Settings', side_effect=lambda **kw: Settings(**{
                **kw, 'deployment_mode': 'cloud', 'auth_mode': 'oauth',
                'public_base_url': 'https://storyboard.test.invalid', 'oauth_issuer': 'https://test.invalid',
                'oauth_audience': 'https://storyboard.test.invalid', 'oauth_jwks_url': 'https://test.invalid/keys'})), \
             patch.object(V055QualityAndMigrationTests, 'namespace', namespace), \
             patch.object(McpProductionAuthoringTests, 'namespace', namespace):
            flow.setUp()
        flow.f.namespace = flow.f.fixture.namespace = namespace
        self.addCleanup(flow.tearDown)
        f, captured = flow.f, {}
        original = f.service.audit_storyboard

        def encode_page(submission, page, legacy=False):
            value = copy.deepcopy(submission); value.pop('coverage', None)
            if legacy:
                encoded = json.dumps(page, ensure_ascii=False, separators=(',', ':'))
                value['layers'][0]['evidence_ids'].extend(
                    f'{PAGE_PREFIX}{i:05d}:' + encoded[start:start+215]
                    for i, start in enumerate(range(0, len(encoded), 215)))
            else: value['coverage_page'] = page
            return value

        def audit(*args, **kwargs):
            captured.update(kwargs)
            submission = kwargs['semantic_audit']
            coverage = submission['coverage']
            rows, pages = coverage['checks'], []
            base = {'contract': 'semantic-obligation-pages-v1', 'manifest_sha256': coverage['manifest_sha256']}
            def call(page, **overrides):
                return original(*args, **{**kwargs, 'semantic_audit': encode_page(submission, page, legacy=True), **overrides})
            first = {**base, 'action': 'append', 'collection_key': 'synthetic-test-review', 'offset': 0, 'checks': rows[:64]}
            staged = call(first)
            self.assertEqual(staged.status, 'coverage_pending')
            self.assertFalse(staged.data['audit_pass'])
            self.assertFalse(staged.data['formal_delivery_ready'])
            from shortdrama_mcp.audit_coverage_pages import coverage_progress
            self.assertEqual(coverage_progress(f.service, f.namespace, f.project, kwargs['artifact_id'])['pending_count'], 1)
            collection = staged.data['collection_id']; pages.append(staged.data['page_id'])
            repeat = call(first)
            self.assertTrue(repeat.data['idempotent_replay'])
            self.assertEqual(repeat.data['page_id'], pages[0])
            with self.assertRaisesRegex(ValueError, 'collection_already_pending'):
                call({**first, 'collection_key': 'trying-to-reset-review'})
            with self.assertRaisesRegex(ValueError, 'audit_coverage_pending'):
                original(*args, **kwargs)
            with self.assertRaisesRegex(ValueError, 'audit_coverage_pending'):
                original(*args, **{**kwargs, 'semantic_audit': None})
            changed = copy.deepcopy(first); changed['checks'][0]['judgement'] += ' altered'
            with self.assertRaisesRegex(ValueError, 'immutable_conflict'): call(changed)
            with self.assertRaises(ValueError): call(first, request_session_id='wrong-audit-session')
            overlap = {**base, 'action': 'append', 'collection_id': collection, 'offset': 1, 'checks': rows[1:3]}
            with self.assertRaisesRegex(ValueError, 'overlap'): call(overlap)
            incomplete = {**base, 'action': 'finalize', 'collection_id': collection, 'page_ids': list(pages)}
            with self.assertRaisesRegex(ValueError, 'incomplete'): call(incomplete)
            before = inspect_formal_delivery(f.service, f.namespace, f.project, kwargs['artifact_id'])
            self.assertFalse(before.data['formal_delivery']['formal_delivery_ready'])
            other_order, other_session = f.fixture._begin_audit(f.project, kwargs['artifact_id'], label='other-page-auditor')
            second = {**base, 'action': 'append', 'collection_id': collection, 'offset': 64, 'checks': rows[64:128]}
            with self.assertRaisesRegex(ValueError, 'binding_mismatch'):
                call(second, work_order_id=other_order, request_session_id=other_session)
            # Real archive round-trip: record kind directories have no Library
            # whitelist and signatures survive export/mount byte-for-byte.
            with tempfile.TemporaryDirectory() as runtime:
                store = f.service.store
                previous_projects = store.projects_dir
                existing_project = store.project_dir(f.namespace, f.project)
                manager = ExternalProjectStorage(f.service, f.fixture.settings, mode='library', runtime_root=Path(runtime))
                try:
                    mounted = manager._mounted_project_dir(f.namespace, f.project)
                    shutil.copytree(existing_project, mounted)
                    exported = manager.export_snapshot(f.namespace, f.project)
                    manager._extract_project(Path(exported['path']).read_bytes(), namespace=f.namespace,
                        project_id=f.project, expected_tree_sha256=exported['tree_sha256'], install=True)
                    saved_page = store.get_project_record(f.namespace, f.project, kind='audit_coverage_pages', record_id=pages[0])
                    self.assertTrue(store.project_record_is_signed(saved_page))
                    self.assertEqual(saved_page['checks'], rows[:64])
                finally: store.projects_dir = previous_projects
            from test_v0511_window_role_continuation import V0511WindowRoleContinuationTests
            continuation = V0511WindowRoleContinuationTests()
            continuation.service, continuation.namespace = f.service, f.namespace
            new_session = 'coverage-authorized-continuation'
            plan = continuation._plan_continuation(f.project, kwargs['work_order_id'], new_session)
            continuation._execute_continuation(f.project, kwargs['work_order_id'], new_session, plan.data['plan_sha256'])
            kwargs['request_session_id'] = new_session
            # Even correctly signed local state cannot invent an ancestor
            # binding; historical authority is resolved from the signed chain.
            collection_path = store.project_dir(f.namespace, f.project) / 'audit_coverage_collections' / (collection+'.json')
            collection_raw = collection_path.read_bytes()
            try:
                forged = json.loads(collection_raw)
                forged['binding']['authority']['authority_epoch_id'] = 'EPOCH-not-an-ancestor'
                forged['integrity_hmac'] = store._record_integrity_hmac(forged)
                collection_path.write_text(json.dumps(forged, ensure_ascii=False), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'binding_mismatch'): call(second)
            finally: collection_path.write_bytes(collection_raw)
            for offset in range(64, len(rows), 64):
                result = call({**base, 'action': 'append', 'collection_id': collection,
                               'offset': offset, 'checks': rows[offset:offset+64]})
                self.assertEqual(result.status, 'coverage_pending')
                pages.append(result.data['page_id'])
            final = {**base, 'action': 'finalize', 'collection_id': collection, 'page_ids': pages}
            for ids in (pages[:-1], list(reversed(pages)), pages+[pages[0]], pages[:-1]+['ACP-other-collection']):
                with self.assertRaises(ValueError): call({**final, 'page_ids': ids})
            record_path = f.service.store.project_dir(f.namespace, f.project) / 'audit_coverage_pages' / (pages[0]+'.json')
            raw = record_path.read_bytes()
            try:
                tampered = json.loads(raw); tampered.pop('integrity_hmac')
                record_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'signature|integrity'): call(final)
            finally: record_path.write_bytes(raw)
            audited = call(final)
            # An actual signed completed audit releases the unfinished-group
            # fence; a declared PASS or a completed staging upload does not.
            next_review = call({**first, 'collection_key': 'legitimate-next-independent-review'}, force_machine_recheck=True)
            self.assertEqual(next_review.status, 'coverage_pending')
            self.assertNotEqual(next_review.data['collection_id'], collection)
            pending_receipt = inspect_formal_delivery(f.service, f.namespace, f.project, kwargs['artifact_id'])
            self.assertFalse(pending_receipt.data['formal_delivery']['formal_delivery_ready'])
            self.assertIn('audit_coverage_pending', ' '.join(pending_receipt.data['formal_delivery']['issues']))
            next_pages = [next_review.data['page_id']]
            for offset in range(64, len(rows), 64):
                result = call({**base, 'action': 'append', 'collection_id': next_review.data['collection_id'],
                               'offset': offset, 'checks': rows[offset:offset+64]}, force_machine_recheck=True)
                next_pages.append(result.data['page_id'])
            second_audit = call({**base, 'action': 'finalize', 'collection_id': next_review.data['collection_id'],
                                'page_ids': next_pages}, force_machine_recheck=True)
            self.assertNotEqual(second_audit.data['certificate']['certificate_id'], audited.data['certificate']['certificate_id'])
            third = call({**first, 'collection_key': 'third-review-same-declarations'}, force_machine_recheck=True)
            third_pages = [third.data['page_id']]
            for offset in range(64, len(rows), 64):
                item = call({**base, 'action': 'append', 'collection_id': third.data['collection_id'],
                             'offset': offset, 'checks': rows[offset:offset+64]}, force_machine_recheck=True)
                third_pages.append(item.data['page_id'])
            third_final = {**base, 'action': 'finalize', 'collection_id': third.data['collection_id'], 'page_ids': third_pages}
            third_audit = call(third_final, force_machine_recheck=True)
            self.assertNotEqual(third_audit.data['certificate']['certificate_id'], second_audit.data['certificate']['certificate_id'])
            self.assertEqual(call(third_final, force_machine_recheck=True).data['certificate']['certificate_id'], third_audit.data['certificate']['certificate_id'])
            self.assertEqual(coverage_progress(f.service, f.namespace, f.project, kwargs['artifact_id'])['pending_count'], 0)
            with patch.object(f.service, '_ruleset_fingerprint', '0'*64):
                historical = coverage_progress(f.service, f.namespace, f.project, kwargs['artifact_id'])
            self.assertEqual(historical['pending_count'], 0)
            self.assertEqual(historical['stale_count'], 3)
            return third_audit

        with patch.object(f.service, 'audit_storyboard', side_effect=audit):
            flow.test_full_current_episode_scene_full_commit_and_signed_review()
        receipt = inspect_formal_delivery(f.service, f.namespace, f.project, captured['artifact_id'])
        self.assertTrue(receipt.data['formal_delivery']['formal_delivery_ready'], receipt.data)

    def test_current_full_flow_rejects_old_declaration_and_accepts_old_schema_envelope(self):
        flow = current_contract_tests.CurrentMcpContractTests(); flow.setUp()
        self.addCleanup(flow.tearDown)
        f = flow.f
        original = f.service.audit_storyboard
        captured = {}
        def audit(*args, **kwargs):
            captured.update(kwargs)
            submission = kwargs['semantic_audit']
            storyboard, _ = f.service._storyboard_from_artifact(f.namespace, f.project, kwargs['artifact_id'])
            manifest = build_obligations(storyboard, coverage_context(f.service))
            old = copy.deepcopy(submission); old.pop('coverage')
            with self.assertRaisesRegex(ValueError, 'audit_coverage_required'):
                original(*args, **{**kwargs, 'semantic_audit': old})
            before = inspect_formal_delivery(f.service, f.namespace, f.project, kwargs['artifact_id'])
            self.assertFalse(before.data['formal_delivery']['formal_delivery_ready'])
            page = f.mcp('read_project_artifact', {'project_id': f.project, 'artifact_id': kwargs['artifact_id'],
                'section': 'audit_obligations', 'offset': 0, 'limit': 2, 'max_chars': 24000})
            self.assertEqual(len(page['data']['obligations']), 2)
            self.assertEqual(page['data']['next_offset'], 2)
            self.assertNotIn('_values', page['data'])
            legacy = legacy_envelope(submission, manifest)
            # Exact old nested schema fields only; the model accepts bounded refs.
            SemanticAuditInput.model_validate(legacy)
            return original(*args, **{**kwargs, 'semantic_audit': legacy})
        with patch.object(f.service, 'audit_storyboard', side_effect=audit):
            flow.test_full_current_episode_scene_full_commit_and_signed_review()
        result = inspect_formal_delivery(f.service, f.namespace, f.project, captured['artifact_id'])
        receipt = result.data['formal_delivery']
        self.assertTrue(receipt['formal_delivery_ready'], result.data)
        self.assertTrue(receipt['semantic_obligation_coverage_verified'])
        certificate = f.service.store.get_project_record(f.namespace, f.project, kind='certificates',
                            record_id=receipt['independent_audit_certificate_id'])
        storyboard, _ = f.service._storyboard_from_artifact(f.namespace, f.project, captured['artifact_id'])
        old = copy.deepcopy(certificate); old.pop('semantic_obligation_coverage')
        with self.assertRaisesRegex(ValueError, 'certificate_binding_missing_or_stale'):
            verify_certificate_coverage(f.service, storyboard, old)
        stale = copy.deepcopy(certificate)
        stale['report']['semantic_audit']['coverage']['checks'][0]['dependency_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'dependency_stale'):
            verify_certificate_coverage(f.service, storyboard, stale)
        # Even when a pre-existing authority check has returned a formerly
        # signed old certificate, the delivery boundary independently refuses
        # to manufacture current coverage from it.
        with patch.object(f.service, '_clean_independent_certificate_for_work_order', return_value=old):
            blocked = inspect_formal_delivery(f.service, f.namespace, f.project, captured['artifact_id'])
        self.assertFalse(blocked.data['formal_delivery']['formal_delivery_ready'])
        self.assertIsNone(blocked.download_url)


if __name__ == '__main__': unittest.main()
