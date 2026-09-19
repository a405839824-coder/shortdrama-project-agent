"""Actual service/MCP entry gates; assertions cannot certify story accuracy."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime
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

    def snapshot(self, *, without_access_audit=False):
        root = self.service.settings.data_dir
        # Only MCP failures append account-level started/error events. Direct
        # service calls retain the original all-files byte comparison. SQLite
        # sidecars or any other new files are deliberately not excluded.
        audit_db = self.server.access.path
        return {str(p.relative_to(root)): sha(p) for p in root.rglob("*")
                if p.is_file() and not (without_access_audit and p == audit_db)}

    def access_snapshot(self):
        with closing(self.server.access.connect()) as db:
            return {
                "schema": [tuple(row) for row in db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")],
                "roles": [tuple(row) for row in db.execute(
                    "SELECT * FROM user_roles ORDER BY issuer,subject")],
                "events": [dict(row) for row in db.execute("SELECT * FROM access_audit ORDER BY id")],
            }

    def assert_failed_mcp_logged(self, before, tool):
        after = self.access_snapshot()
        self.assertEqual(after["schema"], before["schema"])
        self.assertEqual(after["roles"], before["roles"])
        old_events = before["events"]
        self.assertEqual(after["events"][:len(old_events)], old_events)
        appended = after["events"][len(old_events):]
        self.assertEqual(len(appended), 2)
        last_id = old_events[-1]["id"] if old_events else 0
        for offset, (event, outcome) in enumerate(zip(appended, ("started", "error")), 1):
            self.assertEqual(event["id"], last_id + offset)
            datetime.fromisoformat(event["occurred_at"])
            original_fields = {"issuer", "subject", "role", "tool", "project_id", "outcome"}
            self.assertEqual({k: event[k] for k in original_fields}, {
                "issuer": "local", "subject": "local-single-user", "role": "admin",
                "tool": tool, "project_id": self.project, "outcome": outcome,
            })
            self.assertRegex(event["trace_id"], r"^ACC-[0-9a-f]{32}$")
            self.assertEqual(event["stage"], "tool_dispatch" if outcome == "started" else "tool_execution")
            self.assertEqual(event["rules_fingerprint"], self.service._ruleset_fingerprint)
            self.assertEqual(event["runtime_fingerprint"], self.service._runtime_fingerprint)
            reason = json.loads(event["reason"])
            self.assertEqual(reason["tool_outcome"], outcome)
            self.assertEqual(reason["trace_id"], event["trace_id"])
            self.assertEqual(reason["stage"], event["stage"])
            if outcome == "error":
                self.assertIsNotNone(event["error_code"])
                self.assertEqual(reason["error_code"], event["error_code"])
        self.assertEqual(appended[0]["trace_id"], appended[1]["trace_id"])
        # Diagnostics are now content-free and correlated. Exact controller
        # errors and frozen counters remain independently asserted below.

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
        before = self.snapshot(without_access_audit=True)
        access_before = self.access_snapshot()
        control_before = self.controller.status("service-gate")
        with self.assertRaisesRegex(ToolError, "Frozen operation budget is exhausted"):
            asyncio.run(self.server.call_tool("preflight_storyboard_draft", self.fixture.arguments()))
        self.assertEqual(self.snapshot(without_access_audit=True), before)
        self.assert_failed_mcp_logged(access_before, "preflight_storyboard_draft")
        control_after = self.controller.status("service-gate")
        self.assertEqual(control_after["state"], "stopped")
        self.assertEqual(control_after["stop_reason"]["category"], "operation_budget_exhausted")
        self.assertEqual(control_after["rounds"][-1]["state"], "interrupted")
        expected_rounds = json.loads(json.dumps(control_before["rounds"]))
        expected_rounds[-1]["state"] = "interrupted"
        self.assertEqual(control_after["rounds"], expected_rounds)
        for key in control_before:
            if key not in {"state", "stop_reason", "rounds"}:
                self.assertEqual(control_after[key], control_before[key], key)
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
        before = self.snapshot(without_access_audit=True)
        control_before = self.controller.status("service-gate")
        for action in ("register_snapshot", "mount_snapshot", "export_snapshot", "commit_snapshot", "unmount_snapshot"):
            with self.subTest(action=action):
                access_before = self.access_snapshot()
                with self.assertRaisesRegex(ToolError, "acceptance_external_storage_mutation_not_supported"):
                    asyncio.run(self.server.call_tool("migrate_project_state", {
                        "project_id": self.project, "storage_action": action,
                    }))
                self.assertEqual(self.snapshot(without_access_audit=True), before)
                self.assert_failed_mcp_logged(access_before, "migrate_project_state")
                self.assertEqual(self.controller.status("service-gate"), control_before)
        for action in ("register_snapshot", "mount_snapshot", "commit_snapshot"):
            with self.subTest(import_compatibility=action):
                access_before = self.access_snapshot()
                with self.assertRaisesRegex(ToolError, "acceptance_external_storage_mutation_not_supported"):
                    asyncio.run(self.server.call_tool("import_source_file", {
                        "project_id": self.project, "work_order_id": self.fixture.order,
                        "source_role": "asset", "library_storage_action": action,
                        "file": {"file_id": "frozen-fixture", "download_url": "https://invalid.example/not-fetched"},
                    }))
                self.assertEqual(self.snapshot(without_access_audit=True), before)
                self.assert_failed_mcp_logged(access_before, "import_source_file")
                self.assertEqual(self.controller.status("service-gate"), control_before)


if __name__ == "__main__":
    unittest.main()
