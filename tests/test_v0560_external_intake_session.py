"""Synthetic cloud intake/repair binding regressions; no content acceptance claims."""
import copy
from dataclasses import replace
import unittest

import test_efficiency as efficiency_support
from test_v0557_audit_submission import declare_unknown, external_bytes


class ExternalIntakeRepairSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = efficiency_support.EfficiencyContractTests()
        await self.flow.asyncSetUp()
        self.addAsyncCleanup(self.flow.asyncTearDown)
        self.service = self.flow.service
        self.service.settings = replace(self.service.settings, deployment_mode="cloud")
        self.namespace = self.flow.namespace
        self.store = self.service.store
        self.project = self.service.create_project(self.namespace, "synthetic cloud intake repair").project_id
        # Only identity routing is tested; this fixture certificate is not a
        # software/self-test or user-content acceptance result.
        self.service._persist_runtime_certificate(self.namespace, self.project, {
            "status": "pass", "version": self.service.bootstrap.get("version"),
            "workflow_revision": self.service.bootstrap.get("workflow_revision"),
        })
        self.raw = external_bytes()
        self.artifact = self.store.save_artifact_bytes(self.namespace, self.project, self.raw,
            file_name="synthetic-external.xlsx", artifact_type="source:storyboard", version="external")
        self.audit_order = self.begin("storyboard_audit", "audit_only", "read_only", "audit")
        semantic = declare_unknown(self.service, self.flow.settings, self.project, self.artifact["artifact_id"])
        self.audited = self.service.audit_storyboard(self.namespace, project_id=self.project,
            artifact_id=self.artifact["artifact_id"], audit_cycle_id=None,
            work_order_id=self.audit_order.work_order_id, semantic_audit=semantic,
            request_session_id="openai:synthetic-audit")
        self.assertEqual(self.audited.status, "fail_current")
        self.package = self.audited.data["repair_package"]
        self.handoff = self.store.get_project_record(self.namespace, self.project, kind="handoffs",
            record_id=self.package["production_handoff_id"])
        self.repair_order = self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "repair")
        self.work_order = next(row for row in self.store.load_hub(self.namespace, self.project)["work_orders"]
            if row["work_order_id"] == self.repair_order.work_order_id)

    def begin(self, operation, mode, authorization, role):
        return self.service.begin_task(self.namespace, project_id=self.project,
            operation=operation, mode=mode, authorization=authorization,
            request_text="synthetic exact external intake repair binding", targets=[self.artifact["artifact_id"]],
            authoritative_source="synthetic workbook bytes", latest_version="synthetic",
            target_boundary="one original synthetic workbook", must_keep=["original bytes", "signed audit chain"],
            must_remove=[], protected_scope=[], expected_outputs=["verified repair binding"],
            window_key="synthetic-" + role, request_session_id="openai:synthetic-" + role)

    def authorize(self):
        return self.service._full_repair_authorization(self.namespace, self.project,
            work_order=self.work_order, source_artifact_id=self.artifact["artifact_id"],
            repair_package_id=self.package["repair_package_id"])

    def save_record(self, kind, record, key):
        return self.store.save_project_record(self.namespace, self.project, kind=kind,
            record_id=record[key], value=record)

    async def test_cloud_external_workbook_audit_can_authorize_exact_repair_without_rewriting_history(self):
        self.assertIsNone(self.handoff["from_session_fingerprint"])
        self.assertEqual(self.handoff["handoff_type"], "audit_intake")
        self.assertTrue(self.store.project_record_is_signed(self.handoff))
        self.assertTrue(self.store.project_record_is_signed(self.package))
        before_handoff = copy.deepcopy(self.handoff)
        authorized = self.authorize()
        self.assertEqual(authorized["package"]["repair_package_id"], self.package["repair_package_id"])
        self.assertEqual(self.service._repair_source_session_fingerprint(self.handoff),
            self.package["production_session_fingerprint"])
        self.assertEqual(self.store.get_project_record(self.namespace, self.project, kind="handoffs",
            record_id=self.handoff["handoff_id"]), before_handoff)
        record, path = self.store.get_artifact(self.namespace, self.project, self.artifact["artifact_id"])
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertFalse(record.get("metadata", {}).get("repair_successor_artifact_id"))
        self.assertEqual(authorized["certificate"]["result"], "fail_current")

    async def test_changed_signed_session_and_unsigned_handoff_are_rejected(self):
        changed = copy.deepcopy(self.package)
        changed["production_session_fingerprint"] = "f" * 64
        self.save_record("repair_packages", changed, "repair_package_id")
        with self.assertRaisesRegex(ValueError, "production transport session differs"):
            self.authorize()
        self.save_record("repair_packages", self.package, "repair_package_id")
        changed_handoff = copy.deepcopy(self.handoff)
        changed_handoff["from_session_fingerprint"] = "f" * 64
        self.save_record("handoffs", changed_handoff, "handoff_id")
        with self.assertRaisesRegex(ValueError, "production transport session differs"):
            self.authorize()
        self.save_record("handoffs", self.handoff, "handoff_id")
        path = self.store.project_dir(self.namespace, self.project) / "handoffs" / (self.handoff["handoff_id"] + ".json")
        self.store.atomic_write_json(path, changed_handoff)  # Deliberately do not re-sign the isolated fixture.
        with self.assertRaises(ValueError):
            self.authorize()

    async def test_cloud_real_producer_keeps_session_and_missing_or_mislabelled_transport_fails(self):
        real = {**self.handoff, "handoff_type": "production_to_audit",
            "from_window_role": "production", "from_session_fingerprint": "e" * 64}
        self.assertEqual(self.service._repair_source_session_fingerprint(real), "e" * 64)
        for change in ({"handoff_type": "production_to_audit"},
                       {"from_window_role": "production"},
                       {"from_window_binding_id": "EXTWIN-wrong-artifact"},
                       {"from_actor_binding_id": "EXTACT-wrong-artifact"},
                       {"materializer_contract": "compact-master-v1"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "production transport session is missing"):
                self.service._repair_source_session_fingerprint({**self.handoff, **change})
        # Existing local no-transport behavior remains compatible.
        self.service.settings = replace(self.service.settings, deployment_mode="local")
        self.assertEqual(len(self.service._repair_source_session_fingerprint({**real,
            "from_session_fingerprint": None})), 64)


if __name__ == "__main__":
    unittest.main()
