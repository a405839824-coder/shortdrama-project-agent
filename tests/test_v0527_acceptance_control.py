"""Independent finite-control oracles. Written before execution; public dummy data only.

These checks exercise scheduling/evidence identity, not storyboard semantics or
model reliability. The root runner must include this module in its frozen batch.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

SERVER = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER))
from shortdrama_mcp.acceptance_control import AcceptanceControlError, AcceptanceController


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AcceptanceControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='public-acceptance-control-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.registry = self.base / 'trusted-registry'
        self.workspace = self.base / 'workspace'
        (self.workspace / 'candidate').mkdir(parents=True)
        (self.workspace / 'receipts').mkdir()
        (self.workspace / 'criteria.json').write_text('{"target":"public-fixed-target"}\n', encoding='utf-8')
        (self.workspace / 'input.json').write_text('{"public_input":"first-input"}\n', encoding='utf-8')
        (self.workspace / 'candidate' / 'engine.py').write_text('PUBLIC_ENGINE = 1\n', encoding='utf-8')
        self.controller = AcceptanceController(self.registry)
        self.receipt_number = 0

    def manifest(self, steps=1, negative=False):
        rows = []
        for n in range(steps):
            step_id = f'step-{n + 1}'
            rows.append({
                'step_id': step_id,
                'recipe': {'kind': 'python', 'argv': ['-c', 'pass'],
                           'timeout_seconds': 5, 'expected_exit_code': 0},
                'inputs': ['input.json'],
                'depends_on': [] if n == 0 else [f'step-{n}'],
                'expected': {
                    'stage': 'public-validation',
                    'outcome': 'rejected' if negative else 'accepted',
                    'reason_codes': ['public_target_error'] if negative else [],
                    'prohibited_states': ['artifact_written', 'committed'] if negative else [],
                },
                'operation_limits': [],
            })
        return {
            'contract_version': 'finite-acceptance-v1',
            'contract_id': 'public-contract',
            'scope_id': 'public-scope',
            'lineage_id': 'public-lineage',
            'namespace': 'public-test',
            'project_id': 'public-project',
            'frozen_files': {name: file_sha(self.workspace / name) for name in ('criteria.json', 'input.json')},
            'candidate_files': self.hashes(),
            'repair_allowlist': ['candidate/engine.py'],
            'tracked_roots': ['candidate'],
            'exclude_globs': [],
            'steps': rows,
        }

    def hashes(self):
        return {'candidate/engine.py': file_sha(self.workspace / 'candidate' / 'engine.py')}

    def start(self, manifest=None):
        manifest = manifest or self.manifest()
        self.controller.freeze(manifest, self.workspace)
        self.controller.begin_round(manifest['contract_id'], self.hashes())
        return manifest

    def reserve(self, step_id='step-1', round_number=1, controller=None):
        return (controller or self.controller).reserve_step('public-contract', round_number, step_id, self.hashes())

    def result(self, classification='completed', outcome='accepted', stage='public-validation', reasons=None, states=None):
        self.receipt_number += 1
        relative = f'receipts/result-{self.receipt_number}.json'
        path = self.workspace / relative
        path.write_text(json.dumps({'public_observation': self.receipt_number,
                                    'classification': classification, 'outcome': outcome}), encoding='utf-8')
        return {'classification': classification, 'stage': stage, 'outcome': outcome,
                'reason_codes': list(reasons or []), 'reached_states': list(states or []),
                'evidence': {relative: file_sha(path)},
                'summary': 'Public control observation; not an independent storyboard audit certificate.'}

    def state(self):
        return self.controller.status('public-contract')

    def fail_first_round(self):
        self.start()
        reservation = self.reserve()
        self.controller.complete_step(reservation['reservation_id'], self.result('implementation_defect', 'error', reasons=['public_failure']))
        self.assertEqual(self.state()['state'], 'awaiting_repair')
        return reservation

    def repair_code(self):
        (self.workspace / 'candidate' / 'engine.py').write_text('PUBLIC_ENGINE = 2\n', encoding='utf-8')
        self.controller.authorize_repair('public-contract', observed_hashes=self.hashes(),
                                         changed_paths=['candidate/engine.py'], rationale='One scoped implementation correction.')
        self.assertEqual(self.state()['state'], 'repair_frozen')
        self.controller.begin_round('public-contract', self.hashes())

    def test_ac01_first_round_all_steps_pass_is_terminal(self):
        self.start(self.manifest(steps=2))
        first = self.reserve()
        self.controller.complete_step(first['reservation_id'], self.result())
        self.assertEqual(self.state()['state'], 'running')
        second = self.reserve('step-2')
        self.controller.complete_step(second['reservation_id'], self.result())
        self.assertEqual(self.state()['state'], 'passed')
        self.assertEqual(len(self.state()['rounds']), 1)
        for step in ('step-1', 'step-2', 'internal-preparation'):
            with self.subTest(step=step), self.assertRaises(AcceptanceControlError):
                self.reserve(step)
        with self.assertRaises(AcceptanceControlError):
            self.controller.begin_round('public-contract', self.hashes())
        with self.assertRaises(AcceptanceControlError):
            self.controller.authorize_repair('public-contract', observed_hashes=self.hashes(), changed_paths=[], rationale='No extra work allowed.')
        self.assertEqual(self.state()['state'], 'passed')

    def test_ac02_one_scoped_code_repair_retains_failed_round_and_can_pass(self):
        original_hashes = self.hashes()
        first = self.fail_first_round()
        original_result = copy.deepcopy(self.state()['reservations'][first['reservation_id']]['result'])
        self.repair_code()
        self.assertNotEqual(original_hashes, self.hashes())
        second = self.reserve(round_number=2)
        self.controller.complete_step(second['reservation_id'], self.result())
        status = self.state()
        self.assertEqual(status['state'], 'passed')
        self.assertEqual(len(status['rounds']), 2)
        self.assertTrue(status['repair_used'])
        self.assertEqual(status['reservations'][first['reservation_id']]['result'], original_result)
        with self.assertRaises(AcceptanceControlError):
            self.reserve(round_number=3)

    def test_ac03_second_round_residual_failure_stops(self):
        self.fail_first_round()
        self.repair_code()
        reservation = self.reserve(round_number=2)
        self.controller.complete_step(reservation['reservation_id'], self.result('implementation_defect', 'error', reasons=['public_residual']))
        self.assertEqual(self.state()['state'], 'stopped')
        with self.assertRaises(AcceptanceControlError):
            self.controller.authorize_repair('public-contract', observed_hashes=self.hashes(), changed_paths=[], rationale='A forbidden second repair.')
        with self.assertRaises(AcceptanceControlError):
            self.controller.begin_round('public-contract', self.hashes())

    def test_ac03_unknown_architecture_change_and_interrupted_cannot_pass(self):
        # Three fixed classifications, each uses a distinct authorized synthetic scope.
        for index, classification in enumerate(('unknown', 'architecture_change', 'interrupted')):
            with self.subTest(classification=classification):
                manifest = self.manifest()
                suffix = f'-classification-{index}'
                for key in ('contract_id', 'scope_id', 'lineage_id', 'project_id'):
                    manifest[key] += suffix
                cid = manifest['contract_id']
                self.controller.freeze(manifest, self.workspace)
                self.controller.begin_round(cid, self.hashes())
                reservation = self.controller.reserve_step(cid, 1, 'step-1', self.hashes())
                self.controller.complete_step(reservation['reservation_id'], self.result(classification, 'not_run'))
                self.assertEqual(self.controller.status(cid)['state'], 'stopped')
                with self.assertRaises(AcceptanceControlError):
                    self.controller.begin_round(cid, self.hashes())

    def test_ac04_same_registry_scope_or_lineage_alias_cannot_reset(self):
        original = self.start()
        first = self.reserve()
        self.controller.complete_step(first['reservation_id'], self.result())
        other_workspace = self.base / 'other-output-directory'
        (other_workspace / 'candidate').mkdir(parents=True)
        for name in ('criteria.json', 'input.json', 'candidate/engine.py'):
            (other_workspace / name).write_bytes((self.workspace / name).read_bytes())
        for key in ('scope_id', 'lineage_id'):
            with self.subTest(shared_identity=key):
                altered = copy.deepcopy(original)
                altered['contract_id'] = 'alias-' + key
                altered['scope_id'] = 'other-scope'
                altered['lineage_id'] = 'other-lineage'
                altered[key] = original[key]
                try:
                    returned = AcceptanceController(self.registry).freeze(altered, other_workspace)
                except AcceptanceControlError:
                    pass
                else:
                    self.assertEqual(returned['contract_id'], original['contract_id'])
                    self.assertEqual(returned['state'], 'passed')
                self.assertEqual(self.state()['state'], 'passed')
                self.assertEqual(len(self.state()['rounds']), 1)

    def test_ac05_freeze_does_not_keep_mutable_caller_criteria_reference(self):
        manifest = self.manifest()
        self.controller.freeze(manifest, self.workspace)
        manifest['steps'][0]['expected']['stage'] = 'caller-replaced-stage'
        manifest['steps'][0]['expected']['outcome'] = 'rejected'
        self.controller.begin_round('public-contract', self.hashes())
        reservation = self.reserve()
        self.controller.complete_step(reservation['reservation_id'], self.result())
        self.assertEqual(self.state()['state'], 'passed')

    def test_ac05_actual_frozen_input_change_is_rejected_before_reserve(self):
        self.start()
        (self.workspace / 'input.json').write_text('{"public_input":"silently-replaced"}\n', encoding='utf-8')
        with self.assertRaises(AcceptanceControlError):
            self.reserve()
        self.assertNotEqual(self.state()['state'], 'passed')

    def test_ac05_candidate_input_successor_preserves_original_bytes(self):
        manifest = self.manifest()
        original_bytes = (self.workspace / 'input.json').read_bytes()
        input_relative = 'candidate/input.json'
        (self.workspace / input_relative).write_bytes(original_bytes)
        manifest['candidate_files'][input_relative] = manifest['frozen_files'].pop('input.json')
        manifest['repair_allowlist'].append(input_relative)
        manifest['steps'][0]['inputs'] = [input_relative]
        def observed():
            return {name: file_sha(self.workspace / name) for name in manifest['candidate_files']}
        self.controller.freeze(manifest, self.workspace)
        original_hashes = observed()
        self.controller.begin_round('public-contract', original_hashes)
        first = self.controller.reserve_step('public-contract', 1, 'step-1', original_hashes)
        self.controller.complete_step(first['reservation_id'], self.result('input_error', 'error', reasons=['public_adapter_error']))
        first_result = copy.deepcopy(self.state()['reservations'][first['reservation_id']]['result'])
        successor_bytes = b'{"public_input":"explicit-second-input"}\n'
        (self.workspace / input_relative).write_bytes(successor_bytes)
        self.controller.authorize_repair('public-contract', observed_hashes=observed(),
                                         changed_paths=[input_relative], rationale='One declared input adapter correction; criteria unchanged.')
        self.assertEqual(self.state()['state'], 'repair_frozen')
        # The public contract promises actual immutable candidate blobs, not
        # just a hash that points to the now-overwritten working input.
        archived_payloads = [p.read_bytes() for p in self.registry.rglob('*') if p.is_file()]
        self.assertTrue(any(value == original_bytes for value in archived_payloads), 'Original input bytes were not retained in immutable storage.')
        self.assertTrue(any(value == successor_bytes for value in archived_payloads), 'Successor input bytes were not frozen in immutable storage.')
        self.assertEqual(self.state()['reservations'][first['reservation_id']]['result'], first_result)
        self.controller.begin_round('public-contract', observed())
        second = self.controller.reserve_step('public-contract', 2, 'step-1', observed())
        self.controller.complete_step(second['reservation_id'], self.result())
        self.assertEqual(self.state()['state'], 'passed')

    def test_ac06_undeclared_or_out_of_order_steps_cannot_execute(self):
        for index, step in enumerate(('not-listed', 'internal-preparation', 'step-2')):
            with self.subTest(step=step):
                manifest = self.manifest(steps=2)
                for key in ('contract_id', 'scope_id', 'lineage_id', 'project_id'):
                    manifest[key] += f'-ordering-{index}'
                cid = manifest['contract_id']
                self.controller.freeze(manifest, self.workspace)
                self.controller.begin_round(cid, self.hashes())
                with self.assertRaises(AcceptanceControlError):
                    self.controller.reserve_step(cid, 1, step, self.hashes())
                self.assertFalse(self.controller.status(cid)['reservations'])

    def test_ac07_negative_requires_stage_reason_and_forbidden_state_absence(self):
        variants = (
            ('matched', 'public-validation', ['public_target_error'], [], True),
            ('wrong-stage', 'different-stage', ['public_target_error'], [], False),
            ('wrong-reason', 'public-validation', ['unrelated_error'], [], False),
            ('downstream-leaked', 'public-validation', ['public_target_error'], ['artifact_written'], False),
        )
        for name, stage, reasons, states, should_match in variants:
            with self.subTest(name=name):
                manifest = self.manifest(negative=True)
                for key in ('contract_id', 'scope_id', 'lineage_id', 'project_id'):
                    manifest[key] += '-' + name
                cid = manifest['contract_id']
                self.controller.freeze(manifest, self.workspace)
                self.controller.begin_round(cid, self.hashes())
                reservation = self.controller.reserve_step(cid, 1, 'step-1', self.hashes())
                self.controller.complete_step(reservation['reservation_id'], self.result(outcome='rejected', stage=stage, reasons=reasons, states=states))
                status = self.controller.status(cid)
                self.assertEqual(status['reservations'][reservation['reservation_id']]['matched'], should_match)
                self.assertEqual(status['state'], 'passed' if should_match else 'awaiting_repair')

    def test_ac08_two_concurrent_reserves_get_at_most_one_execution(self):
        self.start()
        barrier = threading.Barrier(2, timeout=10)
        def worker():
            controller = AcceptanceController(self.registry)
            barrier.wait()
            try:
                return ('reserved', self.reserve(controller=controller))
            except AcceptanceControlError as exc:
                return ('rejected', exc.code)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker) for _ in range(2)]
            results = [job.result(timeout=15) for job in jobs]
        self.assertEqual([kind for kind, value in results].count('reserved'), 1)
        self.assertEqual([kind for kind, value in results].count('rejected'), 1)
        status = self.state()
        self.assertEqual(len(status['reservations']), 1)
        self.assertEqual(len(status['rounds'][0]['steps']), 1)

    def test_ac09_reserved_budget_survives_process_exit_without_refund(self):
        self.start()
        # Exactly one predeclared short child process. It exits after reserve,
        # without completing the step or closing controller state cleanly.
        child = '\n'.join((
            'import json, os, sys',
            'from shortdrama_mcp.acceptance_control import AcceptanceController',
            'controller = AcceptanceController(sys.argv[1])',
            'reservation = controller.reserve_step("public-contract", 1, "step-1", json.loads(sys.argv[2]))',
            'sys.stdout.write(json.dumps(reservation))',
            'sys.stdout.flush()',
            'os._exit(23)',
        ))
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(SERVER) + os.pathsep + environment.get('PYTHONPATH', '')
        completed = subprocess.run([sys.executable, '-c', child, str(self.registry), json.dumps(self.hashes())],
                                   capture_output=True, text=True, timeout=15, env=environment)
        self.assertEqual(completed.returncode, 23, completed.stderr)
        reservation = json.loads(completed.stdout)
        reopened = AcceptanceController(self.registry)
        self.assertIn(reservation['reservation_id'], reopened.status('public-contract')['reservations'])
        with self.assertRaises(AcceptanceControlError):
            self.reserve(controller=reopened)
        reopened.complete_step(reservation['reservation_id'], self.result('interrupted', 'not_run'))
        self.assertEqual(reopened.status('public-contract')['state'], 'stopped')
        with self.assertRaises(AcceptanceControlError):
            self.reserve(controller=reopened)

    def test_ac10_pending_steps_and_rewritten_results_never_become_success(self):
        self.start(self.manifest(steps=2))
        reservation = self.reserve()
        original = self.result()
        self.controller.complete_step(reservation['reservation_id'], original)
        self.assertEqual(self.state()['state'], 'running')
        with self.assertRaises(AcceptanceControlError):
            self.controller.complete_step(reservation['reservation_id'], self.result('implementation_defect', 'error'))
        self.assertEqual(self.state()['reservations'][reservation['reservation_id']]['result'], original)
        self.assertNotEqual(self.state()['state'], 'passed')

    def test_ac11_frozen_criteria_cannot_be_changed_as_code_repair(self):
        self.fail_first_round()
        (self.workspace / 'criteria.json').write_text('{"target":"easier-replacement"}\n', encoding='utf-8')
        with self.assertRaises(AcceptanceControlError):
            self.controller.authorize_repair('public-contract', observed_hashes=self.hashes(), changed_paths=['criteria.json'], rationale='Not an allowed code correction.')
        with self.assertRaises(AcceptanceControlError):
            self.controller.begin_round('public-contract', self.hashes())

    def test_ac11_unapproved_tracked_file_change_cannot_be_repaired(self):
        self.fail_first_round()
        (self.workspace / 'candidate' / 'extra.py').write_text('PUBLIC_EXTRA = True\n', encoding='utf-8')
        with self.assertRaises(AcceptanceControlError):
            self.controller.authorize_repair('public-contract', observed_hashes=self.hashes(), changed_paths=['candidate/extra.py'], rationale='Unapproved additional implementation.')
        self.assertNotEqual(self.state()['state'], 'repair_frozen')

    def test_ac12_code_drift_and_unlisted_tracked_addition_are_detected(self):
        self.start()
        observed_before = self.hashes()
        (self.workspace / 'candidate' / 'unlisted.py').write_text('PUBLIC_UNLISTED = True\n', encoding='utf-8')
        with self.assertRaises(AcceptanceControlError):
            self.controller.reserve_step('public-contract', 1, 'step-1', observed_before)
        status = self.state()
        self.assertEqual(status['state'], 'stopped')
        self.assertEqual(len(status['reservations']), 1)
        recorded = next(iter(status['reservations'].values()))
        self.assertEqual(recorded['state'], 'reserved')
        self.assertNotIn('lease_id', recorded)
        with self.assertRaises(AcceptanceControlError):
            self.reserve()

    def test_ac12_result_evidence_hash_is_checked_from_actual_file(self):
        self.start()
        reservation = self.reserve()
        result = self.result()
        evidence_path = next(iter(result['evidence']))
        (self.workspace / evidence_path).write_text('changed-after-result-was-built', encoding='utf-8')
        with self.assertRaises(AcceptanceControlError):
            self.controller.complete_step(reservation['reservation_id'], result)
        self.assertNotEqual(self.state()['state'], 'passed')


if __name__ == '__main__':
    unittest.main()
