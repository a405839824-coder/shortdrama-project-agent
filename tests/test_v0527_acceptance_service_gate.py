"""Actual service/MCP entry gates; assertions cannot certify story accuracy."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mcp.server.mcpserver.exceptions import ToolError

import shortdrama_mcp.service as service_module
from shortdrama_mcp.acceptance_control import AcceptanceController
from shortdrama_mcp.acceptance_service import operation_request_sha256
from shortdrama_mcp.config import Settings
import test_v0527_mcp_production_authoring as fixtures


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AcceptanceServiceGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.McpProductionAuthoringTests()
        self.fixture.setUp()
        self.service, self.server = self.fixture.service, self.fixture.server
        self.namespace, self.project = self.fixture.namespace, self.fixture.project
        self.temp = tempfile.TemporaryDirectory(prefix="finite-service-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "candidate.py").write_text("candidate = 1\n")
        (self.workspace / "criteria.txt").write_text("exact service request only\n")
        self.registry = self.root / "registry"
        self.controller = AcceptanceController(self.registry)

    def tearDown(self):
        self.temp.cleanup()
        self.fixture.tearDown()

    def snapshot(self):
        root = self.service.settings.data_dir
        return {str(p.relative_to(root)): sha(p) for p in root.rglob("*") if p.is_file()}

    def enable(self, *, operation="preflight_storyboard_draft", arguments=None, lease=True):
        args = arguments if arguments is not None else self.fixture.arguments()
        digest = operation_request_sha256(getattr(self.service, operation), self.namespace,
                                          **args, request_session_id=None)
        manifest = {
            "contract_version": "finite-acceptance-v1", "contract_id": "service-gate",
            "scope_id": "service-gate-scope", "lineage_id": "service-gate-lineage",
            "namespace": self.namespace, "project_id": self.project,
            "frozen_files": {"criteria.txt": sha(self.workspace / "criteria.txt")},
            "candidate_files": {"candidate.py": sha(self.workspace / "candidate.py")},
            "repair_allowlist": ["candidate.py"], "tracked_roots": ["."], "exclude_globs": [],
            "steps": [{"step_id": "service", "recipe": {"kind": "python", "argv": ["-m", "unittest"], "timeout_seconds": 600, "expected_exit_code": 0},
                       "inputs": ["criteria.txt"], "depends_on": [],
                       "expected": {"stage": "service", "outcome": "accepted", "reason_codes": [],
                                    "prohibited_states": ["unexpected_publication"]},
                       "operation_limits": [{"operation": operation, "namespace": self.namespace,
                                             "project_id": self.project, "request_sha256": digest,
                                             "limit": 1}]}],
        }
        self.controller.freeze(manifest, self.workspace)
        hashes = manifest["candidate_files"]
        self.controller.begin_round("service-gate", hashes)
        reserved = self.controller.reserve_step("service-gate", 1, "service", hashes)
        self.service.settings = replace(self.service.settings, acceptance_control_enabled=True,
                                        acceptance_registry_dir=self.registry,
                                        acceptance_lease_id=reserved["lease_id"] if lease else None)
        return reserved

    def test_missing_and_forged_lease_before_any_service_writes(self):
        self.enable(lease=False)
        before = self.snapshot()
        for value in (None, "LEASE-forged"):
            with self.subTest(lease=value):
                self.service.settings = replace(self.service.settings, acceptance_lease_id=value)
                with self.assertRaises(ValueError):
                    self.fixture.direct()
                self.assertEqual(self.snapshot(), before)

    def test_real_mcp_valid_once_then_replay_rejected_before_write(self):
        self.enable()
        with patch.object(service_module.validate_storyboard, "validate_project",
                          wraps=service_module.validate_storyboard.validate_project) as validator:
            response = self.fixture.mcp("preflight_storyboard_draft", self.fixture.arguments())
        self.assertTrue(validator.called)
        self.assertEqual(response["data"]["authoring_attempt"]["status"], "rendered_pending_preflight")
        self.assertEqual(len(response["data"]["compiled_storyboard"]["shots"]), 1)
        self.assertEqual(len(self.fixture.records()), 1)
        self.assertTrue(response["data"]["authoring_attempt"]["canonical_created"])
        self.assertEqual(len(list((self.fixture.records()[0] / "compiler_attempts").rglob("canonical.json"))), 1)
        self.assertFalse(response["data"]["commit_ready"])
        self.assertIsNone(response["data"]["authoring_attempt"]["semantic_accuracy_percent"])
        before = self.snapshot()
        with self.assertRaises(ToolError):
            asyncio.run(self.server.call_tool("preflight_storyboard_draft", self.fixture.arguments()))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(asyncio.run(self.server.list_tools())), 24)

    def test_changed_body_cannot_borrow_lease(self):
        self.enable()
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            self.fixture.direct(request={**self.fixture.request, "source_graph_sha256": "0" * 64})
        self.assertEqual(caught.exception.code, "operation_not_authorized")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controller.status("service-gate")["state"], "stopped")

    def test_legacy_compact_cannot_borrow_production_envelope_lease(self):
        self.enable()
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            self.fixture.direct(request={"schema_version": "R10.1", "shots": []})
        self.assertEqual(caught.exception.code, "operation_not_authorized")
        self.assertEqual(self.snapshot(), before)

    def test_wrong_operation_cannot_borrow_lease(self):
        self.enable()
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            self.service.diagnose_storyboard_artifact(self.namespace, self.project, self.fixture.graph_id)
        self.assertEqual(caught.exception.code, "operation_not_authorized")
        self.assertEqual(self.snapshot(), before)

    def test_wrong_project_cannot_borrow_lease(self):
        self.enable()
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            self.service.preflight_storyboard_draft(self.namespace,
                **{**self.fixture.arguments(), "project_id": "wrong-project"}, request_session_id=None)
        self.assertEqual(caught.exception.code, "operation_not_authorized")
        self.assertEqual(self.snapshot(), before)

    def test_terminal_lease_is_rejected_after_controller_reconstruction(self):
        self.enable()
        self.controller.stop("service-gate", "bounded test stop")
        self.controller = AcceptanceController(self.registry)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.fixture.direct()
        self.assertEqual(self.snapshot(), before)

    def test_code_drift_rejected_before_artifact_creation(self):
        self.enable()
        (self.workspace / "candidate.py").write_text("candidate = 2\n")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.fixture.direct()
        self.assertEqual(self.snapshot(), before)

    def test_read_only_status_exempt_full_selftest_requires_exact_quota(self):
        self.enable(lease=False)
        status = self.service.agent_status(self.namespace, self.project, full_self_test=False)
        self.assertTrue(status.data["finite_acceptance_control"]["enabled"])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.service.agent_status(self.namespace, self.project, full_self_test=True)
        self.assertEqual(self.snapshot(), before)

    def test_full_selftest_consumes_one_host_bound_call(self):
        self.enable(operation="agent_status", arguments={"project_id": self.project, "full_self_test": True})
        self.service.agent_status(self.namespace, self.project, full_self_test=True)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.service.agent_status(self.namespace, self.project, full_self_test=True)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_profile_is_explicitly_uncontrolled(self):
        result = self.service.agent_status(self.namespace, self.project, full_self_test=False)
        control = result.data["finite_acceptance_control"]
        self.assertEqual(control["profile"], "legacy_uncontrolled")
        self.assertFalse(control["enabled"])
        self.assertFalse(control["manuscript_accuracy_proven"])

    def test_no_service_method_accepts_caller_control_tokens(self):
        for method in (self.service.preflight_storyboard_draft, self.service.commit_storyboard,
                       self.service.commit_storyboard_delta, self.service.commit_storyboard_patch,
                       self.service.audit_storyboard, self.service.begin_task):
            params = inspect.signature(method).parameters
            self.assertTrue({"lease_id", "registry_dir", "contract_id", "acceptance_control_enabled"}.isdisjoint(params))

    def test_explicit_environment_control_is_not_silently_disabled(self):
        with patch.dict(os.environ, {"SHORTDRAMA_ACCEPTANCE_CONTROL": "true",
                                    "SHORTDRAMA_ACCEPTANCE_REGISTRY": str(self.registry),
                                    "SHORTDRAMA_ACCEPTANCE_LEASE": ""}):
            self.assertTrue(Settings.from_env().acceptance_control_enabled)
        with patch.dict(os.environ, {"SHORTDRAMA_ACCEPTANCE_CONTROL": "typo"}):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_direct_settings_cannot_silently_disable_configured_control(self):
        settings = replace(self.service.settings, acceptance_control_enabled=False,
                           acceptance_registry_dir=self.registry,
                           data_dir=self.root / "must-not-create")
        with self.assertRaises(ValueError):
            service_module.StoryboardService(settings)
        self.assertFalse(settings.data_dir.exists())

    def test_external_storage_route_cannot_bypass_frozen_service(self):
        self.enable()
        before = self.snapshot()
        for action in ("register_snapshot", "mount_snapshot", "export_snapshot", "commit_snapshot", "unmount_snapshot"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ToolError, "acceptance_external_storage_mutation_not_supported"):
                    asyncio.run(self.server.call_tool("migrate_project_state", {
                        "project_id": self.project, "storage_action": action,
                    }))
                self.assertEqual(self.snapshot(), before)
        for action in ("register_snapshot", "mount_snapshot", "commit_snapshot"):
            with self.subTest(import_compatibility=action):
                with self.assertRaisesRegex(ToolError, "acceptance_external_storage_mutation_not_supported"):
                    asyncio.run(self.server.call_tool("import_source_file", {
                        "project_id": self.project, "work_order_id": self.fixture.order,
                        "source_role": "asset", "library_storage_action": action,
                        "file": {"file_id": "frozen-fixture", "download_url": "https://invalid.example/not-fetched"},
                    }))
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
