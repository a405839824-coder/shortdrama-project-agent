"""Real admission integration; synthetic fixtures do not certify story quality."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import shortdrama_mcp.service as service_module
from shortdrama_mcp.canonical import canonical_sha256
from test_efficiency import valid_storyboard
import test_v055_quality_migration as legacy_fixtures
import test_v0527_mcp_production_authoring as authoring_fixtures
import test_workbook_canonical_migration as migration_fixtures


class ProductionBindingServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy_fixtures.V055QualityAndMigrationTests()
        self.fixture.setUp()
        self.service, self.namespace = self.fixture.service, self.fixture.namespace
        self.project = self.fixture._project("0.5.64 production admission synthetic fixture")
        self.order, self.session = self.fixture._begin(
            self.project, operation="storyboard_create", label="context-binding")

    def tearDown(self):
        self.fixture.tearDown()

    def preflight(self, candidate):
        return self.service.preflight_storyboard_draft(self.namespace, project_id=self.project,
            work_order_id=self.order, storyboard=candidate, scope_kind="full", scope_id="EP01",
            request_session_id=self.session)

    def observation(self, result):
        return self.service.store.get_project_record(self.namespace, self.project,
            kind="draft_observations", record_id=result.data["candidate_observation"]["observation_id"])

    def test_missing_versions_and_equivalent_literal_preserve_raw_and_return_exact_committable_candidate(self):
        raw = valid_storyboard()
        del raw["project_version"]
        del raw["workflow_profile"]
        raw["shots"][0]["生成限制"] = raw["shots"][0]["生成限制"].replace("禁止额外配乐", "不加额外配乐")
        frozen = copy.deepcopy(raw)
        result = self.fixture._preflight_full_with_scene_coverage(project_id=self.project,
            work_order_id=self.order, storyboard=raw, scope_id="EP01", request_session_id=self.session)
        self.assertEqual(result.status, service_module.DRAFT_PREFLIGHT_PASS, result.data["report"])
        self.assertEqual(raw, frozen)
        prepared = result.data["prepared_storyboard"]
        self.assertEqual(prepared["project_version"], service_module.validate_storyboard.VERSION)
        self.assertEqual(canonical_sha256(prepared), result.data["draft_preflight"]["candidate_sha256"])
        self.assertIn("禁止额外配乐", prepared["shots"][0]["生成限制"])
        observation = self.observation(result)
        self.assertEqual(observation["candidate_snapshot"], frozen)
        self.assertGreaterEqual(len(observation["preparation_changes"]), 3)
        self.assertFalse(observation["model_first_draft_verified"])
        self.assertIn("selected_source.bytes_sha256", observation["production_binding"]["missing_evidence"])
        committed = self.service.commit_storyboard(self.namespace, project_id=self.project,
            work_order_id=self.order, storyboard=prepared, file_name="prepared-binding.xlsx",
            source_artifact_id=None, request_session_id=self.session, draft_preflight_id=result.trace_id)
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)

    def test_stale_version_and_missing_source_lock_fail_and_both_originals_are_recorded(self):
        for field, value in (("project_version", "old-runtime"), ("source_lock", None)):
            raw = valid_storyboard()
            if value is None:
                raw.pop(field)
            else:
                raw[field] = value
            result = self.preflight(raw)
            self.assertEqual(result.status, service_module.DRAFT_PREFLIGHT_FAIL)
            self.assertIsNone(result.data["prepared_storyboard"])
            self.assertEqual(self.observation(result)["candidate_snapshot"], raw)
            self.assertFalse(result.data["commit_ready"])
        self.assertIn("source_lock", result.data["production_binding"]["missing_evidence"])
        self.assertFalse(result.data["production_binding"]["source_lock_created"])

    def test_bound_canonical_is_not_rewritten_or_resigned_to_fix_format(self):
        raw = valid_storyboard()
        raw["prevention_evidence"]["production_derivation"] = {"binding_sha256": "old-bound-hash"}
        raw["shots"][0]["生成限制"] = "不加额外配乐"
        frozen = copy.deepcopy(raw)
        result = self.preflight(raw)
        self.assertEqual(result.status, service_module.DRAFT_PREFLIGHT_FAIL)
        self.assertIn("production_preparation_requires_recompile", str(result.data["report"]["issues"]))
        self.assertTrue(result.data["rejected_preparation_changes"])
        self.assertEqual(result.data["preparation_changes"], [])
        self.assertEqual(self.observation(result)["candidate_snapshot"], frozen)
        self.assertEqual(raw, frozen)

    def test_actual_stored_source_bytes_and_conflicting_claim_are_checked(self):
        payload = "甲说道：不能虚构来源。".encode()
        record = self.service.store.save_artifact_bytes(self.namespace, self.project, payload,
            file_name="source.txt", artifact_type="source:novel", version="v1")
        selected = {"artifact_id": record["artifact_id"], "fingerprint": record["fingerprint"],
                    "source_role": "source:novel"}
        hub = {"project_state": {"latest_artifacts": {"source:novel": selected}}}
        binding = self.service._production_candidate_binding(self.namespace, self.project,
            valid_storyboard(), hub=hub, scope_key="full:EP01")
        self.assertEqual(binding["source"]["bytes_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(binding["source"]["artifact_id"], record["artifact_id"])
        raw = valid_storyboard()
        raw["source_lock"]["source_artifact_id"] = "ART-different"
        with self.assertRaisesRegex(ValueError, "source_claim_conflict"):
            self.service._production_candidate_binding(self.namespace, self.project, raw, hub=hub)
        _record, path = self.service.store.get_artifact(self.namespace, self.project, record["artifact_id"])
        path.write_bytes(payload + b"tampered")
        with self.assertRaisesRegex(ValueError, "source_identity_conflict|artifact_raw_fingerprint_mismatch"):
            self.service._production_candidate_binding(self.namespace, self.project, valid_storyboard(), hub=hub)

    def test_same_raw_candidate_new_runtime_gets_new_observation_without_claiming_new_first_draft(self):
        raw = valid_storyboard()
        first = self.preflight(raw)
        before = self.observation(first)
        with patch.object(self.service, "_runtime_fingerprint", "9" * 64):
            second = self.service._observe_draft_candidate(self.namespace, self.project,
                work_order_id=self.order, scope_kind="full", scope_id="EP01", storyboard=raw,
                quality=first.data["quality"], report=first.data["report"])
        self.assertNotEqual(second["observation_id"], before["observation_id"])
        self.assertEqual(second["first_observation_id"], before["observation_id"])
        self.assertFalse(second["model_first_draft_verified"])
        self.assertEqual(self.observation(first), before)

    def test_status_exposes_reporting_and_interpretation_policy(self):
        result = self.service.agent_status(self.namespace)
        runtime = result.data["runtime"]
        for key in ("audit_reporting_policy", "input_interpretation_policy"):
            self.assertEqual(runtime[key], service_module.validate_storyboard.load_runtime_rules()[key])


class AuthoringBindingServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = authoring_fixtures.McpProductionAuthoringTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_original_request_frozen_before_server_versions_and_compiler_binding(self):
        original = copy.deepcopy(self.fixture.request)
        result = self.fixture.direct()
        self.assertEqual(result.data["authoring_attempt"]["status"], "rendered_pending_preflight",
                         result.data["authoring_attempt"])
        folder, = self.fixture.records()
        self.assertEqual(json.loads((folder / "request.original.json").read_text()), original)
        self.assertEqual(json.loads((folder / "decisions.original.json").read_text()), original["decisions"])
        prepared = json.loads((folder / "decisions.prepared.json").read_text())
        self.assertNotIn("project_version", prepared["project"])
        self.assertEqual(result.data["compiled_storyboard"]["project_version"], service_module.validate_storyboard.VERSION)
        attempt, = (folder / "compiler_attempts").iterdir()
        self.assertEqual(json.loads((attempt / "input.original.json").read_text()), prepared)
        changes = json.loads((folder / "preparation_changes.json").read_text())
        self.assertEqual(changes, [])  # This typed fixture already uses fixed literals.
        binding = json.loads((folder / "production_binding.json").read_text())
        self.assertEqual(binding["server"]["project_version"], service_module.validate_storyboard.VERSION)
        self.assertFalse(binding["independent_content_coverage_verified"])
        self.assertFalse(binding["model_first_draft_verified"])
        self.assertEqual(self.fixture.request, original)

    def test_conflicting_authoring_version_is_rejected_with_full_raw_request_retained(self):
        raw = copy.deepcopy(self.fixture.request)
        raw["decisions"]["project"]["project_version"] = "old-runtime"
        result = self.fixture.direct(raw)
        self.assertEqual(result.status, "authoring_rejected")
        self.assertIn("production_context_conflict", str(result.data["authoring_attempt"]["findings"]))
        folder, = self.fixture.records()
        self.assertEqual(json.loads((folder / "request.original.json").read_text()), raw)
        self.assertFalse((folder / "compiler_attempts").exists())
        self.assertFalse(result.data["commit_ready"])


class MigrationBindingCompatibilityTests(unittest.TestCase):
    def test_existing_migration_preserves_old_declaration_without_new_production_binding(self):
        fixture = migration_fixtures.WorkbookMigrationLifecycleTests()
        fixture.setUp()
        try:
            plan = fixture.migrate()
            started = fixture.migrate("execute", plan)
            candidate = fixture.candidate()
            candidate["source_lock"]["ruleset_version"] = "historical-source-rules-v1"
            original = copy.deepcopy(candidate)
            with patch.object(fixture.service, "_production_candidate_binding",
                              wraps=fixture.service._production_candidate_binding) as new_binding:
                preflight_id = fixture.clean_preflight(started.work_order_id, candidate)
            new_binding.assert_not_called()
            record = fixture.service.store.get_project_record(fixture.namespace, fixture.project,
                kind="draft_preflights", record_id=preflight_id)
            observation = fixture.service.store.get_project_record(fixture.namespace, fixture.project,
                kind="draft_observations", record_id=record["candidate_observation"]["observation_id"])
            self.assertEqual(observation["candidate_snapshot"], original)
            self.assertEqual(observation["preparation_changes"], [])
            self.assertEqual(observation["production_binding"]["status"], "not_applied")
            self.assertFalse(observation["production_binding"]["source"]["bytes_verified"])
            self.assertTrue(record["commit_ready"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
