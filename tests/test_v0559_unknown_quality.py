"""Synthetic quality-state contracts; never audits a user's storyboard."""
import copy
import hashlib
import json
import unittest

from jsonschema import Draft202012Validator

import project_agent
import test_efficiency as efficiency_support
import test_v0527_quality_schema as quality_support
from semantic_audit_helpers import semantic_audit_for_artifact
from shortdrama_mcp.file_io import storyboard_to_xlsx
from test_v0557_audit_submission import declare_unknown


def hub_validator():
    path = quality_support.ROOT / 'skills/shortdrama-storyboard/references/project_agent_schema.json'
    return Draft202012Validator(json.loads(path.read_text(encoding='utf-8')))


def unknown_hub(*, legacy=False):
    hub = project_agent.new_hub('Synthetic unknown quality', project_id='SBP-unknown-quality')
    hub['project_state']['latest_first_draft_quality'] = quality_support.persisted_quality(
        quality_support.public_draft(), [])
    hub['project_state']['production_quality_block'] = {
        'active': True, 'grade': 'unavailable', 'artifact_id': 'A1', 'audit_cycle_id': 'C1',
        'reason': ('first_draft_quality_below_threshold' if legacy
                   else 'first_draft_quality_evidence_incomplete'),
        'required_action': ('repair_root_cause_and_revalidate_prevention_gate' if legacy
                            else 'complete_independent_audit_evidence'),
        'created_at': '2026-09-28T00:00:00Z',
    }
    return hub


class UnknownQualityStateTests(unittest.TestCase):
    def test_current_and_legacy_unknown_roundtrip_without_rewriting_history(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                hub = unknown_hub(legacy=legacy)
                frozen = copy.deepcopy(hub)
                self.assertEqual(project_agent.validate_hub(hub), [])
                self.assertEqual(list(hub_validator().iter_errors(hub)), [])
                migrated, _ = project_agent.migrate_hub_to_current(hub)
                for field in ('production_quality_block', 'latest_first_draft_quality'):
                    self.assertEqual(migrated['project_state'][field], frozen['project_state'][field])
                self.assertEqual(hub, frozen)
                quality = hub['project_state']['latest_first_draft_quality']
                self.assertEqual(quality['measurement_state'], 'unavailable')
                self.assertIsNone(quality['first_draft_correctness_percent'])
                self.assertIsNone(quality['direct_usability_percent'])
                self.assertFalse(quality['commit_eligible'])

    def test_unknown_block_cannot_be_deleted_or_cleared_without_evidence(self):
        for replacement in (None, {'active': False}, {'active': True, 'cleared_at': '2026-09-28T00:00:00Z'}):
            with self.subTest(replacement=replacement):
                hub = unknown_hub()
                if replacement is None:
                    hub['project_state']['production_quality_block'] = None
                else:
                    hub['project_state']['production_quality_block'].update(replacement)
                self.assertTrue(project_agent.validate_hub(hub))

    def test_reason_action_and_grade_are_not_interchangeable(self):
        for update in (
            {'grade': 'A'}, {'grade': 'B'}, {'grade': 'C'}, {'grade': 0},
            {'reason': 'first_draft_quality_below_threshold'},
            {'required_action': 'repair_root_cause_and_revalidate_prevention_gate'},
        ):
            with self.subTest(update=update):
                hub = unknown_hub()
                hub['project_state']['production_quality_block'].update(update)
                self.assertTrue(project_agent.validate_hub(hub))
                self.assertTrue(list(hub_validator().iter_errors(hub)))
        for grade in ('B', 'C'):
            hub = unknown_hub(legacy=True)
            hub['project_state']['production_quality_block']['grade'] = grade
            self.assertEqual(project_agent.validate_hub(hub), [])
            self.assertEqual(list(hub_validator().iter_errors(hub)), [])


class UnknownQualityAuditFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()

    async def asyncTearDown(self):
        await self.flow.asyncTearDown()

    async def test_real_unknown_writer_blocks_production_but_allows_bound_reaudit(self):
        h = self.flow
        project = await h.create_project('Synthetic unavailable first audit')
        order = await h.begin_storyboard_task(project, operation='storyboard_audit', mode='audit_only')
        raw = storyboard_to_xlsx(efficiency_support.valid_storyboard(), {'issues': []}, assume_compact=True)
        unrelated = h.service.store.save_artifact_bytes(h.namespace, project, raw,
            file_name='synthetic-B.xlsx', artifact_type='source:storyboard', version='current')
        clean_args = {'project_id': project, 'artifact_id': unrelated['artifact_id'], 'work_order_id': order,
            'semantic_audit': semantic_audit_for_artifact(h.service, h.settings, project, unrelated['artifact_id'])}
        clean = await h.call('audit_storyboard', clean_args)
        self.assertEqual(clean['status'], 'pass')
        original = h.service.store.save_artifact_bytes(h.namespace, project, raw,
            file_name='synthetic-A.xlsx', artifact_type='source:storyboard', version='current')
        args = {'project_id': project, 'artifact_id': original['artifact_id'], 'work_order_id': order}
        submission = declare_unknown(h.service, h.settings, project, original['artifact_id'])
        initial = await h.call('audit_storyboard', {**args, 'semantic_audit': submission})
        self.assertEqual(initial['status'], 'fail_current')
        state = h.service.store.load_hub(h.namespace, project)['project_state']
        block = copy.deepcopy(state['production_quality_block'])
        first = copy.deepcopy(state['latest_first_draft_quality'])
        self.assertEqual((block['grade'], block['reason'], block['required_action']),
            ('unavailable', 'first_draft_quality_evidence_incomplete', 'complete_independent_audit_evidence'))
        self.assertIsNone(first['first_draft_correctness_percent'])
        self.assertIsNone(first['direct_usability_percent'])
        self.assertFalse(first['commit_eligible'])
        hub = h.service.store.load_hub(h.namespace, project)
        self.assertEqual(project_agent.validate_hub(hub), [])
        self.assertEqual(list(hub_validator().iter_errors(hub)), [])
        status = await h.call('get_agent_status', {'project_id': project, 'full_self_test': False})
        self.assertTrue(status['data']['state_valid'], status['data']['state_errors'])
        self.assertEqual(status['data']['execution_mode'], 'audit_resolution')
        self.assertEqual(status['data']['required_next_role'], 'independent_audit')
        self.assertIn('storyboard_audit', status['data']['allowed_storyboard_operations'])
        # This advertises the existing signed-target repair route, not repair
        # authority derived from unknown checks or the quality grade itself.
        self.assertIn('storyboard_repair', status['data']['allowed_storyboard_operations'])
        for operation in ('storyboard_create', 'storyboard_regenerate', 'storyboard_change'):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(Exception, 'first_draft_quality_blocked'):
                    await h.begin_storyboard_task(project, operation=operation, window_key=operation)

        # A persisted old block remains readable and is never silently rewritten.
        with h.service.store.mutate_project(h.namespace, project) as (hub, _ledger):
            legacy = hub['project_state']['production_quality_block']
            legacy['reason'] = 'first_draft_quality_below_threshold'
            legacy['required_action'] = 'repair_root_cause_and_revalidate_prevention_gate'
        legacy = copy.deepcopy(h.service.store.load_hub(h.namespace, project)['project_state']['production_quality_block'])
        status = await h.call('get_agent_status', {'project_id': project, 'full_self_test': False})
        self.assertTrue(status['data']['state_valid'], status['data']['state_errors'])
        self.assertEqual(status['data']['execution_mode'], 'audit_resolution')
        self.assertEqual(h.service.store.load_hub(h.namespace, project)['project_state']['production_quality_block'], legacy)

        replayed = await h.call('audit_storyboard', clean_args)
        self.assertEqual(replayed['status'], 'pass')
        self.assertEqual(replayed['data']['certificate']['certificate_id'], clean['data']['certificate']['certificate_id'])
        self.assertEqual(h.service.store.load_hub(h.namespace, project)['project_state']['production_quality_block'], legacy)

        supplemental = copy.deepcopy(submission)
        supplemental['layers'][0]['summary'] += ' Synthetic supplemental read still lacks source evidence.'
        repeated = await h.call('audit_storyboard', {**args, 'force_machine_recheck': True,
            'semantic_audit': supplemental})
        self.assertEqual(repeated['status'], 'fail_current')
        self.assertNotEqual(repeated['data']['certificate']['certificate_id'], initial['data']['certificate']['certificate_id'])
        state = h.service.store.load_hub(h.namespace, project)['project_state']
        self.assertEqual(state['production_quality_block'], legacy)
        self.assertEqual(state['latest_first_draft_quality'], first)
        self.assertEqual(project_agent.validate_hub(h.service.store.load_hub(h.namespace, project)), [])
        _, source_path = h.service.store.get_artifact(h.namespace, project, original['artifact_id'])
        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
