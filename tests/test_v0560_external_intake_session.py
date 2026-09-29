"""Synthetic cloud intake/repair binding regressions; no content acceptance claims."""
import copy
from dataclasses import replace
import unittest

import test_efficiency as efficiency_support
from shortdrama_mcp.service import _scene_scope_groups
from semantic_audit_helpers import failing_semantic_audit_for_artifact
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

    def begin(self, operation, mode, authorization, role, *, action="create", session=None):
        return self.service.begin_task(self.namespace, project_id=self.project,
            operation=operation, mode=mode, authorization=authorization,
            request_text="synthetic exact external intake repair binding", targets=[self.artifact["artifact_id"]],
            authoritative_source="synthetic workbook bytes", latest_version="synthetic",
            target_boundary="one original synthetic workbook", must_keep=["original bytes", "signed audit chain"],
            must_remove=[], protected_scope=[], expected_outputs=["verified repair binding"],
            window_key="synthetic-" + role, request_session_id=session or "openai:synthetic-" + role,
            action=action)

    def authorize(self):
        return self.service._full_repair_authorization(self.namespace, self.project,
            work_order=self.work_order, source_artifact_id=self.artifact["artifact_id"],
            repair_package_id=self.package["repair_package_id"])

    def save_record(self, kind, record, key):
        return self.store.save_project_record(self.namespace, self.project, kind=kind,
            record_id=record[key], value=record)

    async def test_window_neutral_same_audit_transport_authorizes_exact_full_repair(self):
        repair = self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "neutral-repair",
            action="create_window_neutral", session="openai:synthetic-audit")
        self.work_order = next(row for row in self.store.load_hub(self.namespace, self.project)["work_orders"]
            if row["work_order_id"] == repair.work_order_id)
        self.assertEqual(self.work_order["transport_session_fingerprint"], self.package["audit_session_fingerprint"])
        self.assertNotEqual(self.work_order["work_order_id"], self.package["audit_work_order_id"])
        self.assertNotEqual(self.work_order["server_window_binding_id"], self.package["audit_window_binding_id"])
        self.assertNotEqual(self.work_order["server_actor_binding_id"], self.package["audit_actor_binding_id"])
        before = {kind: self.store.list_project_records(self.namespace, self.project, kind=kind)
            for kind in ("handoffs", "repair_packages", "certificates")}
        authorized = self.authorize()
        self.assertEqual(authorized["package"]["repair_package_id"], self.package["repair_package_id"])
        self.assertEqual(before, {kind: self.store.list_project_records(self.namespace, self.project, kind=kind)
            for kind in before})
        self.assertEqual(self.store.get_artifact(self.namespace, self.project, self.artifact["artifact_id"])[1].read_bytes(), self.raw)

    def create_neutral_production(self, candidate):
        self.project = self.service.create_project(self.namespace, "synthetic same-transport production repair").project_id
        self.service._persist_runtime_certificate(self.namespace, self.project, {
            "status": "pass", "version": self.service.bootstrap.get("version"),
            "workflow_revision": self.service.bootstrap.get("workflow_revision"),
        })
        session = "openai:synthetic-produced-shared"
        self.artifact = {"artifact_id": "EP01"}
        producer = self.begin("storyboard_create", "execute", "explicit_execute", "producer",
            action="create_window_neutral", session=session)
        for group in _scene_scope_groups(candidate):
            preflight = self.service.preflight_storyboard_draft(self.namespace, project_id=self.project,
                work_order_id=producer.work_order_id, storyboard=candidate,
                scope_kind="scene", scope_id=group["stable_scene_id"], request_session_id=session)
            self.assertEqual(preflight.status, "draft_preflight_pass", preflight.data.get("report"))
        preflight = self.service.preflight_storyboard_draft(self.namespace, project_id=self.project,
            work_order_id=producer.work_order_id, storyboard=candidate,
            scope_kind="full", scope_id="EP01", request_session_id=session)
        self.assertTrue(preflight.data["commit_ready"], preflight.data.get("report"))
        committed = self.service.commit_storyboard(self.namespace, project_id=self.project,
            work_order_id=producer.work_order_id, storyboard=candidate,
            file_name="synthetic-produced.xlsx", source_artifact_id=None, request_session_id=session,
            draft_preflight_id=preflight.trace_id)
        self.artifact, path = self.store.get_artifact(self.namespace, self.project, committed.artifact_id)
        return producer, committed, session, path

    async def test_window_neutral_original_producer_transport_authorizes_exact_full_repair(self):
        producer, committed, session, path = self.create_neutral_production(efficiency_support.valid_storyboard())
        before_raw = path.read_bytes()
        auditor = self.begin("storyboard_audit", "audit_only", "read_only", "produced-audit",
            action="create_window_neutral", session=session)
        audited = self.service.audit_storyboard(self.namespace, project_id=self.project,
            artifact_id=committed.artifact_id, audit_cycle_id=None, work_order_id=auditor.work_order_id,
            semantic_audit=declare_unknown(self.service, self.flow.settings, self.project, committed.artifact_id),
            request_session_id=session)
        self.assertEqual(audited.status, "fail_current")
        self.package = audited.data["repair_package"]
        repair = self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "produced-repair",
            action="create_window_neutral", session=session)
        orders = {row["work_order_id"]: row for row in self.store.load_hub(self.namespace, self.project)["work_orders"]}
        self.work_order = orders[repair.work_order_id]
        participants = [orders[result.work_order_id] for result in (producer, auditor, repair)]
        self.assertEqual(len({row["transport_session_fingerprint"] for row in participants}), 1)
        for field in ("work_order_id", "server_window_binding_id", "server_actor_binding_id"):
            self.assertEqual(len({row[field] for row in participants}), 3)
        self.assertEqual(self.package["production_session_fingerprint"], self.work_order["transport_session_fingerprint"])
        before = {kind: self.store.list_project_records(self.namespace, self.project, kind=kind)
            for kind in ("handoffs", "repair_packages", "certificates")}
        authorized = self.authorize()
        self.assertEqual(authorized["production_handoff"]["handoff_type"], "production_to_audit")
        self.assertEqual(authorized["package"]["repair_package_id"], self.package["repair_package_id"])
        self.assertEqual(before, {kind: self.store.list_project_records(self.namespace, self.project, kind=kind)
            for kind in before})
        self.assertEqual(path.read_bytes(), before_raw)

    async def test_window_neutral_same_transport_patch_keeps_independent_identities(self):
        candidate = efficiency_support.valid_storyboard()
        correct = candidate["shots"][0]["光源来源"]
        candidate["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        producer, committed, session, path = self.create_neutral_production(candidate)
        before_raw = path.read_bytes()
        shot_id = candidate["shots"][0]["镜头编号"]
        auditor = self.begin("storyboard_audit", "audit_only", "read_only", "patch-audit",
            action="create_window_neutral", session=session)
        audited = self.service.audit_storyboard(self.namespace, project_id=self.project,
            artifact_id=committed.artifact_id, audit_cycle_id=None, work_order_id=auditor.work_order_id,
            handoff_artifact_id=committed.data["handoff"]["handoff_id"],
            semantic_audit=failing_semantic_audit_for_artifact(
                self.service, self.flow.settings, self.project, committed.artifact_id,
                shot_id=shot_id, field="光源来源", error_code="E-TEMPLATE-008",
                issue_id="shots[1].synthetic_light_source",
                evidence="合成软件案例：右侧窗光与样本锁定的左侧自然光冲突。",
                repair_action="合成软件案例：仅恢复该镜原定左侧自然光。"),
            request_session_id=session)
        self.assertEqual(audited.status, "fail_current")
        repair = self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "patch-repair",
            action="create_window_neutral", session=session)
        original_certificate = self.store.get_project_record(self.namespace, self.project, kind="certificates",
            record_id=audited.data["certificate"]["certificate_id"])
        original_handoff = self.store.get_project_record(self.namespace, self.project, kind="handoffs",
            record_id=committed.data["handoff"]["handoff_id"])
        repaired = self.service.commit_storyboard_patch(self.namespace, project_id=self.project,
            work_order_id=repair.work_order_id, base_artifact_id=committed.artifact_id,
            expected_canonical_hash=committed.data["canonical_sha256"],
            expected_raw_artifact_fingerprint=None,
            audit_certificate_id=original_certificate["certificate_id"],
            operations=[{"op": "replace_field", "shot_id": shot_id, "field": "光源来源",
                "value": correct, "issue_ids": ["E-TEMPLATE-008"],
                "reason": "合成软件案例：依据独审问题恢复左侧自然光"}],
            impact_scope=[shot_id], file_name="synthetic-same-transport-patch.xlsx",
            request_session_id=session)
        self.assertTrue(repaired.artifact_id)
        self.assertNotEqual(repaired.artifact_id, committed.artifact_id)
        orders = {row["work_order_id"]: row for row in self.store.load_hub(self.namespace, self.project)["work_orders"]}
        participants = [orders[result.work_order_id] for result in (producer, auditor, repair)]
        self.assertEqual(len({row["transport_session_fingerprint"] for row in participants}), 1)
        for field in ("work_order_id", "server_window_binding_id", "server_actor_binding_id"):
            self.assertEqual(len({row[field] for row in participants}), 3)
        self.assertEqual(self.store.get_project_record(self.namespace, self.project, kind="certificates",
            record_id=original_certificate["certificate_id"]), original_certificate)
        self.assertEqual(self.store.get_project_record(self.namespace, self.project, kind="handoffs",
            record_id=original_handoff["handoff_id"]), original_handoff)
        self.assertEqual(path.read_bytes(), before_raw)
        actual, _ = self.service._storyboard_from_artifact(self.namespace, self.project, repaired.artifact_id)
        self.assertEqual(actual["shots"][0]["光源来源"], correct)

    async def test_legacy_same_transport_remains_rejected(self):
        with self.assertRaisesRegex(ValueError, "session_role_separation_required"):
            self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "legacy-repair",
                session="openai:synthetic-audit")
        # Exercise both legacy guard branches with isolated input copies, without
        # modifying signed history or the independently created repair authority.
        for label in ("production", "audit"):
            with self.subTest(label=label):
                order = {**self.work_order,
                    "transport_session_fingerprint": self.package[label + "_session_fingerprint"]}
                with self.assertRaisesRegex(ValueError, f"repair reuses {label} session"):
                    self.service._assert_repair_separation(self.namespace, self.project,
                        order, self.package, self.handoff)

    async def test_window_neutral_still_rejects_reused_work_order_window_and_actor(self):
        repair = self.begin("storyboard_repair", "audit_and_repair", "explicit_execute", "neutral-guard",
            action="create_window_neutral", session="openai:synthetic-audit")
        order = next(row for row in self.store.load_hub(self.namespace, self.project)["work_orders"]
            if row["work_order_id"] == repair.work_order_id)
        for label in ("production", "audit"):
            for field, package_field, expected in (
                ("server_window_binding_id", label + "_window_binding_id", f"repair reuses {label} window"),
                ("server_actor_binding_id", label + "_actor_binding_id", f"repair reuses {label} actor"),
            ):
                with self.subTest(label=label, field=field), self.assertRaisesRegex(ValueError, expected):
                    self.service._assert_repair_separation(self.namespace, self.project,
                        {**order, field: self.package[package_field]}, self.package, self.handoff)
        with self.assertRaisesRegex(ValueError, "repair and audit work orders must differ"):
            self.service._assert_repair_separation(self.namespace, self.project,
                {**order, "work_order_id": self.package["audit_work_order_id"]}, self.package, self.handoff)

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
