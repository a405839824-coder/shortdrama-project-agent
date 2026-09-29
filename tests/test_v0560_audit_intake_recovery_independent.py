"""Independent synthetic recovery tests; never use a user's storyboard."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import test_v0557_handoff_rebind as rebind_support
import test_efficiency as efficiency_support
from test_v0557_audit_submission import declare_unknown, external_bytes
from shortdrama_mcp.audit_coverage_pages import collection_snapshot
import shortdrama_mcp.service as service_module


class IntakeTraceDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.started = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.case = rebind_support.IntakeHandoffRebindTests()
        await self.case.asyncSetUp()
        self.flow = self.case.flow
        self.store = self.flow.service.store

    async def asyncTearDown(self):
        await self.case.asyncTearDown()

    def trace_for(self, handoff):
        trace = self.store.get_project_record(
            self.flow.namespace, self.case.project, kind="telemetry",
            record_id=handoff["stage_trace_id"],
        )
        self.assertTrue(self.store.project_record_is_signed(trace))
        self.assertEqual(trace["project_id"], self.case.project)
        self.assertEqual(trace["artifact_id"], self.case.artifact["artifact_id"])
        self.assertEqual(trace["input_fingerprints"], handoff["fingerprints"])
        created = datetime.fromisoformat(trace["created_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(created, self.started)
        self.assertLessEqual(created, datetime.now(timezone.utc))
        return trace

    async def test_first_coverage_page_already_has_real_signed_intake_trace(self):
        self.assertEqual(self.case.first["status"], "coverage_pending")
        self.trace_for(self.case.old)
        snapshot = self.store.load_ledger(self.flow.namespace, self.case.project)
        self.assertEqual(snapshot.get("events", []), [])
        self.assertEqual(snapshot.get("audit_certificates", []), [])
        self.assertFalse(self.case.first["data"]["audit_pass"])

    async def test_rebind_and_replay_keep_root_trace_without_new_audit_claim(self):
        old_trace = self.trace_for(self.case.old)
        reply = await self.case.call(self.case.rebind, handoff=self.case.old["handoff_id"])
        handoff_id = reply["data"]["handoff_manifest"]["handoff_id"]
        handoff = self.store.get_project_record(
            self.flow.namespace, self.case.project, kind="handoffs", record_id=handoff_id)
        trace = self.trace_for(handoff)
        self.assertNotEqual(handoff["stage_trace_id"], self.case.old["stage_trace_id"])
        before = self.store.list_project_records_strict(
            self.flow.namespace, self.case.project, kind="telemetry")
        repeated = await self.case.call(self.case.rebind, handoff=self.case.old["handoff_id"])
        self.assertEqual(repeated["data"]["handoff_manifest"]["handoff_id"], handoff_id)
        self.assertEqual(self.store.list_project_records_strict(
            self.flow.namespace, self.case.project, kind="telemetry"), before)
        self.assertEqual(self.trace_for(handoff), trace)
        self.assertEqual(self.trace_for(self.case.old), old_trace)
        self.assertEqual(reply["data"]["accepted_count"], 0)
        self.assertFalse(reply["data"]["audit_pass"])
        self.assertFalse(reply["data"]["formal_delivery_ready"])
        _, original = self.store.get_artifact(
            self.flow.namespace, self.case.project, self.case.artifact["artifact_id"])
        self.assertEqual(original.read_bytes(), self.case.raw)

    async def test_trace_write_failure_cannot_publish_unbacked_rebound_handoff(self):
        before = self.case.records()
        original_write = self.store.atomic_write_json

        def interrupted(path, value):
            if Path(path).parent.name == "telemetry":
                raise OSError("synthetic interruption before intake trace persistence")
            return original_write(path, value)

        with patch.object(self.store, "atomic_write_json", side_effect=interrupted):
            error = await self.flow.rejection_text("audit_storyboard", {
                "project_id": self.case.project,
                "artifact_id": self.case.artifact["artifact_id"],
                "work_order_id": self.case.order,
                "handoff_artifact_id": self.case.old["handoff_id"],
                "semantic_audit": self.case.rebind,
            })
        self.assertIn("synthetic interruption", error)
        self.assertEqual(self.case.records(), before)
        result = await self.case.call(self.case.rebind, handoff=self.case.old["handoff_id"])
        self.trace_for(result["data"]["handoff_manifest"])


class IntakeRecoveryAuthorityTests(unittest.IsolatedAsyncioTestCase):
    """Real protocol/storage on tiny synthetic input; no real-content claims."""

    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()
        self.addAsyncCleanup(self.flow.asyncTearDown)
        f = self.flow
        self.store = f.service.store
        self.project = (await f.call("create_project", {
            "project_name": "synthetic missing intake trace recovery"}))["project_id"]
        # Only routing/record integrity is under test, not software self-test acceptance.
        self.runtime_certificate = f.service._persist_runtime_certificate(f.namespace, self.project, {
            "status": "pass", "version": f.service.bootstrap.get("version"),
            "workflow_revision": f.service.bootstrap.get("workflow_revision"),
        })
        self.raw = external_bytes()
        self.artifact = self.store.save_artifact_bytes(f.namespace, self.project, self.raw,
            file_name="synthetic-recovery.xlsx", artifact_type="source:storyboard", version="external")
        self.audit_order = await f.begin_storyboard_task(
            self.project, operation="storyboard_audit", mode="audit_only")
        submission = declare_unknown(f.service, f.settings, self.project, self.artifact["artifact_id"])
        coverage = submission.pop("coverage")
        args = {"project_id": self.project, "artifact_id": self.artifact["artifact_id"],
                "work_order_id": self.audit_order}
        append = {**submission, "coverage_page": {
            "contract": "semantic-obligation-pages-v1", "action": "append",
            "collection_key": "synthetic-missing-intake", "manifest_sha256": coverage["manifest_sha256"],
            "offset": 0, "checks": coverage["checks"],
        }}
        pending = await f.call("audit_storyboard", {**args, "semantic_audit": append})
        self.collection_id = pending["data"]["collection_id"]
        final = {**submission, "coverage_page": {
            "contract": "semantic-obligation-pages-v1", "action": "finalize",
            "manifest_sha256": coverage["manifest_sha256"], "collection_id": self.collection_id,
            "page_ids": [pending["data"]["page_id"]],
        }}
        self.audit = await f.call("audit_storyboard", {**args, "semantic_audit": final})
        self.assertEqual(self.audit["status"], "fail_current")
        self.cert = self.audit["data"]["certificate"]
        self.package = self.audit["data"]["repair_package"]
        self.old = next(row for row in self.store.list_project_records_strict(
            f.namespace, self.project, kind="handoffs")
            if row["handoff_type"] == "audit_intake" and not row.get("reaudit_kind"))
        self.assertEqual(self.old["status"], "consumed")
        self.directory = self.store.project_dir(f.namespace, self.project)
        self.missing_path = self.directory / "telemetry" / (self.old["stage_trace_id"] + ".json")
        self.original_root_trace_bytes = self.missing_path.read_bytes()
        self.missing_path.unlink(missing_ok=True)  # Only this isolated synthetic fixture.
        self.gap_id = "GAP-synthetic-intake-loss"
        with self.store.mutate_project(f.namespace, self.project) as (hub, _ledger):
            service_module.project_agent.record_capability_gap(hub, {
                "gap_id": self.gap_id, "timestamp": service_module.utc_stamp(),
                "request": "Recover a missing synthetic audit intake root without rewriting history",
                "missing_dependency": "missing historical intake trace", "risk": "repair route blocked",
                "safe_fallback": "preserve originals and fail/unknown", "proposed_implementation": "new signed recovery chain",
                "required_tests": ["exact authority binding", "no fabricated historical trace"],
            })
        order = f.service.begin_task(f.namespace, project_id=self.project,
            operation="rule_improvement", mode="execute", authorization="explicit_execute",
            request_text="authorize exactly one synthetic intake recovery", targets=[self.gap_id,
                self.old["handoff_id"], "controlled_phase:audit_intake_recovery"],
            authoritative_source="signed synthetic original, certificate, repair package and coverage",
            latest_version="synthetic", target_boundary="one exact external audit intake",
            must_keep=["original bytes", "signed history", "fail and unknown"], must_remove=[],
            protected_scope=["dialogue", "source", "old certificate", "old trace ID"],
            expected_outputs=["new pending audit intake"], window_key="independent-intake-recovery",
            request_session_id=None)
        self.recovery_order = order.work_order_id
        self.old_files = {p.relative_to(self.directory).as_posix(): p.read_bytes()
            for parent in ("artifacts", "handoffs", "certificates", "repair_packages",
                           "audit_coverage_collections", "audit_coverage_pages", "audit_coverage_revisions")
            for p in (self.directory / parent).glob("**/*") if p.is_file()}
        self.old_coverage = collection_snapshot(f.service, f.namespace, self.project, self.collection_id)
        self.base_args = {"project_id": self.project, "work_order_id": self.recovery_order,
            "gap_id": self.gap_id, "handoff_ids": [self.old["handoff_id"]], "phase": "audit_intake_recovery"}

    async def plan(self):
        reply = await self.flow.call("recover_storyboard_handoff", {**self.base_args, "action": "plan"})
        return reply["data"]["recovery_plan"]

    async def execute(self, plan):
        return await self.flow.call("recover_storyboard_handoff", {**self.base_args, "action": "execute",
            "expected_plan_sha256": plan["plan_sha256"],
            "confirmation": "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY"})

    async def current_reaudit_successor(self):
        """Create a genuine successor through audit, then lose only the root."""
        root_path = self.directory / "handoffs" / (self.old["handoff_id"] + ".json")
        root_bytes = root_path.read_bytes()
        # Restore the exact fixture bytes while running an ordinary real audit;
        # this helper never synthesizes a historical signed event.
        self.missing_path.write_bytes(self.original_root_trace_bytes)
        try:
            semantic = declare_unknown(self.flow.service, self.flow.settings,
                                       self.project, self.artifact["artifact_id"])
            semantic["layers"][0]["summary"] += " Actual second synthetic review; evidence remains unknown."
            coverage = semantic.pop("coverage")
            args = {
                "project_id": self.project, "artifact_id": self.artifact["artifact_id"],
                "work_order_id": self.audit_order, "handoff_artifact_id": self.old["handoff_id"],
                "force_machine_recheck": True}
            # UNKNOWN is reported but not complete; preserve the accepted
            # collection instead of attempting to discard it under a new key.
            original_pages = [p["page_id"] for p in self.old_coverage["pages"]]
            result = await self.flow.call("audit_storyboard", {**args, "semantic_audit": {
                **semantic, "coverage_page": {"contract": "semantic-obligation-pages-v1",
                    "action": "finalize", "manifest_sha256": coverage["manifest_sha256"],
                    "collection_id": self.collection_id, "page_ids": original_pages}}})
        finally:
            self.missing_path.unlink(missing_ok=True)
        self.assertEqual(result["status"], "fail_current")
        self.assertEqual(root_path.read_bytes(), root_bytes)
        current = result["data"]["certificate"]
        self.assertNotEqual(current["certificate_id"], self.cert["certificate_id"])
        route = self.store.get_project_record(self.flow.namespace, self.project,
            kind="handoffs", record_id=current["handoff_id"])
        self.assertEqual(route["status"], "consumed")
        self.assertEqual(route["reaudit_root_handoff_id"], self.old["handoff_id"])
        self.assertEqual(self.old["audit_certificate_id"], self.cert["certificate_id"])
        self.old_files = {p.relative_to(self.directory).as_posix(): p.read_bytes()
            for parent in ("artifacts", "handoffs", "certificates", "repair_packages",
                           "audit_coverage_collections", "audit_coverage_pages", "audit_coverage_revisions")
            for p in (self.directory / parent).glob("**/*") if p.is_file()}
        return result, route

    async def test_current_reaudit_authority_is_selected_without_rewriting_initial_root(self):
        current, route = await self.current_reaudit_successor()
        plan = await self.plan()
        item = plan["items"][0]
        self.assertEqual(item["certificate_id"], current["data"]["certificate"]["certificate_id"])
        self.assertEqual(item["repair_package_id"], current["data"]["repair_package"]["repair_package_id"])
        self.assertNotEqual(item["certificate_id"], self.cert["certificate_id"])
        result = await self.execute(plan)
        self.assertEqual(result["data"]["mappings"][0]["status"], "PENDING_AUDIT")
        self.assertFalse(result["data"].get("audit_pass", False))
        self.assertGreater(current["data"]["certificate"]["semantic_obligation_coverage"]["unknown_shot_count"], 0)
        self.assert_history_preserved()

    async def test_signed_successor_with_wrong_root_content_or_route_is_rejected(self):
        _current, route = await self.current_reaudit_successor()
        path = self.directory / "handoffs" / (route["handoff_id"] + ".json")
        original = path.read_bytes()
        cases = [
            ({"reaudit_root_handoff_id": "HND-unrelated"}, "route_root_mismatch"),
            ({"production_stage_trace_id": "TRC-unrelated"}, "current_route_mismatch"),
            ({"status": "prepared"}, "current_route_mismatch"),
            ({"fingerprints": {**route["fingerprints"], "canonical": "f" * 64}}, "route_content_mismatch"),
            ({"reaudit_route_trace_id": "TRC-unrelated"}, "current_route_trace_mismatch"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                value = {**route, **changes}
                value["integrity_hmac"] = self.store._record_integrity_hmac(value)
                self.store.atomic_write_json(path, value)
                baseline = {p.relative_to(self.directory).as_posix(): p.read_bytes()
                            for p in self.directory.rglob("*") if p.is_file()}
                try:
                    error = await self.flow.rejection_text("recover_storyboard_handoff", {
                        **self.base_args, "action": "plan"})
                    self.assertIn(expected, error)
                    self.assertEqual({p.relative_to(self.directory).as_posix(): p.read_bytes()
                                      for p in self.directory.rglob("*") if p.is_file()}, baseline)
                finally:
                    path.write_bytes(original)
        self.assert_history_preserved()

    async def test_signed_successor_trace_for_another_artifact_is_rejected(self):
        _current, route = await self.current_reaudit_successor()
        path = self.directory / "telemetry" / (route["stage_trace_id"] + ".json")
        original = path.read_bytes()
        value = self.store.read_json(path)
        value["artifact_id"] = "ART-unrelated"
        value["integrity_hmac"] = self.store._record_integrity_hmac(value)
        self.store.atomic_write_json(path, value)
        try:
            error = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "plan"})
            self.assertIn("current_route_trace_mismatch", error)
        finally:
            path.write_bytes(original)
        self.assert_history_preserved()

    async def test_metadata_cannot_roll_back_from_signed_successor_to_initial_certificate(self):
        await self.current_reaudit_successor()
        path = self.directory / "artifacts" / (self.artifact["artifact_id"] + ".meta.json")
        original = path.read_bytes()
        self.store.update_artifact_metadata(self.flow.namespace, self.project, self.artifact["artifact_id"], {
            "independent_audit_certificate_id": self.cert["certificate_id"]})
        baseline = {p.relative_to(self.directory).as_posix(): p.read_bytes()
                    for p in self.directory.rglob("*") if p.is_file()}
        try:
            error = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "plan"})
            self.assertRegex(error, "latest|superseded|lineage|current_route|repair_package")
            self.assertEqual({p.relative_to(self.directory).as_posix(): p.read_bytes()
                              for p in self.directory.rglob("*") if p.is_file()}, baseline)
        finally:
            path.write_bytes(original)
        self.assert_history_preserved()

    def assert_history_preserved(self):
        for relative, raw in self.old_files.items():
            self.assertEqual((self.directory / relative).read_bytes(), raw, relative)
        self.assertFalse(self.missing_path.exists(), "Recovery must not invent the old trace file")
        self.assertEqual(collection_snapshot(self.flow.service, self.flow.namespace, self.project,
                         self.collection_id), self.old_coverage)

    async def test_new_chain_keeps_old_failure_unknown_bytes_and_uses_present_time(self):
        plan = await self.plan()
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        result = await self.execute(plan)
        self.assert_history_preserved()
        new = [row for row in self.store.list_project_records_strict(
            self.flow.namespace, self.project, kind="handoffs")
            if row["handoff_type"] == "audit_intake" and row["handoff_id"] != self.old["handoff_id"]]
        self.assertEqual(len(new), 1)
        child = new[0]
        self.assertEqual(child["status"], "prepared")
        self.assertNotEqual(child["stage_trace_id"], self.old["stage_trace_id"])
        trace = self.store.get_project_record(self.flow.namespace, self.project, kind="telemetry",
                                             record_id=child["stage_trace_id"])
        self.assertTrue(self.store.project_record_is_signed(trace))
        self.assertGreaterEqual(datetime.fromisoformat(trace["created_at"].replace("Z", "+00:00")), before)
        self.assertEqual(trace["artifact_id"], self.artifact["artifact_id"])
        self.assertEqual(trace["input_fingerprints"], child["fingerprints"])
        self.assertFalse(result["data"].get("audit_pass", False))
        self.assertFalse(result["data"].get("formal_delivery_ready", False))
        for kind in ("certificates", "repair_packages", "audit_coverage_collections"):
            existing = {p.relative_to(self.directory).as_posix() for p in (self.directory / kind).glob("*.json")}
            self.assertEqual(existing, {p for p in self.old_files if p.startswith(kind + "/")})
        self.assertGreater(self.cert["semantic_obligation_coverage"]["unknown_shot_count"], 0)

    async def test_execute_requires_exact_plan_confirmation_and_scope_before_writes(self):
        plan = await self.plan()
        baseline = {str(p.relative_to(self.directory)): p.read_bytes()
                    for p in self.directory.rglob("*") if p.is_file()}
        for override in ({"confirmation": "wrong"}, {"expected_plan_sha256": "0" * 64},
                         {"handoff_ids": [self.old["handoff_id"], "HND-other"]}):
            with self.subTest(override=override):
                text = await self.flow.rejection_text("recover_storyboard_handoff", {
                    **self.base_args, "action": "execute", "expected_plan_sha256": plan["plan_sha256"],
                    "confirmation": "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY", **override})
                self.assertRegex(text, "required|mismatch|conflict|scope|plan_stale")
                self.assertEqual({str(p.relative_to(self.directory)): p.read_bytes()
                    for p in self.directory.rglob("*") if p.is_file()}, baseline)

    async def test_tampered_signature_and_changed_original_cannot_acquire_recovery(self):
        cert_path = self.directory / "certificates" / (self.cert["certificate_id"] + ".json")
        original = cert_path.read_bytes()
        changed = self.store.read_json(cert_path)
        changed["integrity_hmac"] = "0" * 64
        self.store.atomic_write_json(cert_path, changed)
        try:
            text = await self.flow.rejection_text("recover_storyboard_handoff", {**self.base_args, "action": "plan"})
            self.assertRegex(text, "integrity|signature")
        finally:
            cert_path.write_bytes(original)
        _, source = self.store.get_artifact(self.flow.namespace, self.project, self.artifact["artifact_id"])
        source.write_bytes(self.raw + b"changed")
        try:
            text = await self.flow.rejection_text("recover_storyboard_handoff", {**self.base_args, "action": "plan"})
            self.assertIn("fingerprint_mismatch", text)
        finally:
            source.write_bytes(self.raw)
        self.assert_history_preserved()

    async def test_another_authenticated_namespace_cannot_recover_this_project(self):
        with patch("shortdrama_mcp.app.request_namespace", return_value="other-authenticated-principal"):
            text = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "plan"})
        self.assertRegex(text, "unknown project|not found|authority|namespace")
        self.assert_history_preserved()

    async def test_signed_but_foreign_repair_package_binding_is_rejected(self):
        path = self.directory / "repair_packages" / (self.package["repair_package_id"] + ".json")
        before = path.read_bytes()
        try:
            value = self.store.read_json(path)
            value["audit_certificate_id"] = "CERT-another-authority"
            value["integrity_hmac"] = self.store._record_integrity_hmac(value)
            self.store.atomic_write_json(path, value)
            text = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "plan"})
            self.assertRegex(text, "binding|mismatch|conflict|invalid|signed_repair_package_required")
        finally:
            path.write_bytes(before)
        self.assert_history_preserved()

    async def test_consumed_historical_repair_authority_cannot_create_another_root(self):
        path = self.directory / "repair_packages" / (self.package["repair_package_id"] + ".json")
        before = path.read_bytes()
        try:
            value = self.store.read_json(path)
            value["status"] = "consumed"
            value["integrity_hmac"] = self.store._record_integrity_hmac(value)
            self.store.atomic_write_json(path, value)
            text = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "plan"})
            self.assertRegex(text, "consumed|issued|repair_package")
        finally:
            path.write_bytes(before)
        self.assert_history_preserved()

    async def test_second_exactly_authorized_order_cannot_fork_the_same_recovery(self):
        await self.execute(await self.plan())
        other = self.flow.service.begin_task(self.flow.namespace, project_id=self.project,
            operation="rule_improvement", mode="execute", authorization="explicit_execute",
            request_text="second exact synthetic recovery order", targets=[self.gap_id,
                self.old["handoff_id"], "controlled_phase:audit_intake_recovery"],
            authoritative_source="the same signed synthetic authority",
            latest_version="synthetic", target_boundary="the same original intake",
            must_keep=["originals", "signed history"], must_remove=[], protected_scope=[],
            expected_outputs=["pending audit intake"], window_key="second-exact-recovery",
            request_session_id=None)
        before = self.store.list_project_records_strict(self.flow.namespace, self.project, kind="handoffs")
        text = await self.flow.rejection_text("recover_storyboard_handoff", {
            **self.base_args, "work_order_id": other.work_order_id, "action": "plan"})
        self.assertIn("already_started", text)
        self.assertEqual(self.store.list_project_records_strict(
            self.flow.namespace, self.project, kind="handoffs"), before)
        self.assert_history_preserved()

    async def test_concurrent_exact_execute_reuses_one_recovery_chain(self):
        plan = await self.plan()
        args = {**self.base_args, "action": "execute", "expected_plan_sha256": plan["plan_sha256"],
                "confirmation": "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY", "request_session_id": None}
        results = await asyncio.gather(*(asyncio.to_thread(
            self.flow.service.recover_storyboard_handoff, self.flow.namespace, **args) for _ in range(2)))
        self.assertEqual(results[0].data["mappings"], results[1].data["mappings"])
        mappings = results[0].data["mappings"]
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["old_handoff_id"], self.old["handoff_id"])
        self.assertEqual(mappings[0]["status"], "PENDING_AUDIT")
        self.assertNotEqual(mappings[0]["new_stage_trace_id"], self.old["stage_trace_id"])
        self.assertEqual(len(self.store.list_project_records_strict(
            self.flow.namespace, self.project, kind="handoff_recoveries")), 1)
        self.assert_history_preserved()

    async def test_recovered_intake_requires_fresh_full_audit_and_issues_usable_new_authority(self):
        plan = await self.plan()
        recovered = await self.execute(plan)
        mapping = recovered["data"]["mappings"][0]
        self.assert_history_preserved()
        # A recovery does not copy the historical review or silently pass it.
        submission = declare_unknown(self.flow.service, self.flow.settings,
                                     self.project, self.artifact["artifact_id"])
        coverage = submission.pop("coverage")
        args = {"project_id": self.project, "artifact_id": self.artifact["artifact_id"],
                "work_order_id": self.audit_order, "handoff_artifact_id": mapping["new_handoff_id"]}
        page = {"contract": "semantic-obligation-pages-v1", "action": "append",
                "collection_key": "synthetic-new-full-audit", "manifest_sha256": coverage["manifest_sha256"],
                "offset": 0, "checks": coverage["checks"]}
        appended = await self.flow.call("audit_storyboard", {
            **args, "semantic_audit": {**submission, "coverage_page": page}})
        self.assertEqual(appended["status"], "coverage_pending")
        self.assertNotEqual(appended["data"]["collection_id"], self.collection_id)
        finalize = {**submission, "coverage_page": {
            "contract": "semantic-obligation-pages-v1", "action": "finalize",
            "manifest_sha256": coverage["manifest_sha256"],
            "collection_id": appended["data"]["collection_id"],
            "page_ids": [appended["data"]["page_id"]],
        }}
        result = await self.flow.call("audit_storyboard", {**args, "semantic_audit": finalize})
        if result["data"].get("requires_force_recheck"):
            result = await self.flow.call("audit_storyboard", {
                **args, "semantic_audit": finalize, "force_machine_recheck": True})
        self.assertEqual(result["status"], "fail_current")
        certificate, package = result["data"]["certificate"], result["data"]["repair_package"]
        self.assertNotEqual(certificate["certificate_id"], self.cert["certificate_id"])
        self.assertNotEqual(package["repair_package_id"], self.package["repair_package_id"])
        self.assertEqual(certificate["production_stage_trace_id"], mapping["new_stage_trace_id"])
        self.assertEqual(package["source_production_stage_trace_id"], mapping["new_stage_trace_id"])
        self.assertEqual(certificate["handoff_id"], mapping["new_handoff_id"])
        self.assertEqual(package["source_audit_handoff_id"], mapping["new_handoff_id"])
        self.assertGreater(certificate["semantic_obligation_coverage"]["unknown_shot_count"], 0)
        self.flow.service._assert_latest_repair_package_authority(
            self.flow.namespace, self.project, package=package, certificate=certificate,
            base_artifact_id=self.artifact["artifact_id"], current_fingerprints=package["fingerprints"])
        # New audit metadata may advance, but original XLSX and every old signed
        # authority/coverage record must remain byte-for-byte unchanged.
        for relative, raw in self.old_files.items():
            if not relative.startswith("artifacts/"):
                self.assertEqual((self.directory / relative).read_bytes(), raw, relative)
        _, original = self.store.get_artifact(self.flow.namespace, self.project, self.artifact["artifact_id"])
        self.assertEqual(original.read_bytes(), self.raw)
        self.assertFalse(self.missing_path.exists())
        self.assertEqual(collection_snapshot(self.flow.service, self.flow.namespace, self.project,
                         self.collection_id), self.old_coverage)

    async def test_old_signed_coverage_survives_new_runtime_without_becoming_current_pass(self):
        service = self.flow.service
        service._runtime_fingerprint = "d" * 64
        service._ruleset_fingerprint = "e" * 64
        service._persist_runtime_certificate(self.flow.namespace, self.project, {
            "status": "pass", "version": service.bootstrap.get("version"),
            "workflow_revision": service.bootstrap.get("workflow_revision"),
        })
        plan = await self.plan()
        result = await self.execute(plan)
        mapping = result["data"]["mappings"][0]
        new = self.store.get_project_record(self.flow.namespace, self.project, kind="handoffs",
                                            record_id=mapping["new_handoff_id"])
        self.assertEqual(new["fingerprints"]["runtime"], "d" * 64)
        self.assertEqual(new["fingerprints"]["rules"], "e" * 64)
        self.assertEqual(mapping["status"], "PENDING_AUDIT")
        self.assert_history_preserved()

    async def test_interruption_before_new_handoff_resumes_without_recreating_history(self):
        plan = await self.plan()
        before_ids = {p.stem for p in (self.directory / "handoffs").glob("*.json")}
        write = self.store.atomic_write_json
        faulted = False

        def interrupt_once(path, value):
            nonlocal faulted
            path = Path(path)
            if path.parent.name == "handoffs" and path.stem not in before_ids and not faulted:
                faulted = True
                raise OSError("synthetic crash before recovered handoff write")
            return write(path, value)

        with patch.object(self.store, "atomic_write_json", side_effect=interrupt_once):
            text = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "execute", "expected_plan_sha256": plan["plan_sha256"],
                "confirmation": "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY"})
        self.assertTrue(faulted)
        self.assertIn("synthetic crash", text)
        self.assert_history_preserved()
        result = await self.execute(plan)
        self.assertEqual(len(result["data"]["mappings"]), 1)
        self.assertEqual(result["data"]["mappings"][0]["status"], "PENDING_AUDIT")
        self.assert_history_preserved()

    async def test_uncommitted_recovery_cannot_audit_and_retry_publishes_same_root(self):
        plan = await self.plan()
        write = self.store.atomic_write_json

        def interrupt_commit(path, value):
            if Path(path).parent.name == "handoff_recoveries" and value.get("status") == "committed":
                raise OSError("synthetic crash before recovery publication")
            return write(path, value)

        with patch.object(self.store, "atomic_write_json", side_effect=interrupt_commit):
            text = await self.flow.rejection_text("recover_storyboard_handoff", {
                **self.base_args, "action": "execute", "expected_plan_sha256": plan["plan_sha256"],
                "confirmation": "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY"})
        self.assertIn("synthetic crash", text)
        child = next(row for row in self.store.list_project_records_strict(
            self.flow.namespace, self.project, kind="handoffs") if row.get("audit_intake_recovery_id"))
        before = self.store.list_project_records_strict(self.flow.namespace, self.project,
                                                       kind="audit_coverage_collections")
        submission = declare_unknown(self.flow.service, self.flow.settings,
                                     self.project, self.artifact["artifact_id"])
        text = await self.flow.rejection_text("audit_storyboard", {
            "project_id": self.project, "artifact_id": self.artifact["artifact_id"],
            "work_order_id": self.audit_order, "handoff_artifact_id": child["handoff_id"],
            "semantic_audit": submission})
        self.assertIn("recovery_not_committed", text)
        self.assertEqual(self.store.list_project_records_strict(self.flow.namespace, self.project,
                         kind="audit_coverage_collections"), before)
        repeated = await self.execute(plan)
        self.assertEqual(repeated["data"]["mappings"][0]["new_handoff_id"], child["handoff_id"])
        self.assertEqual(repeated["data"]["mappings"][0]["new_stage_trace_id"], child["stage_trace_id"])
        self.assert_history_preserved()


if __name__ == "__main__":
    unittest.main()
