from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from openpyxl import load_workbook

import shortdrama_mcp.service as service_module

import test_v056_handoff_recovery as recovery_tests


class V057PreflightEvidenceReissueTests(
    recovery_tests.V056HandoffRecoveryTests
):
    test_batch_recovers_each_handoff_with_one_shared_trace = None
    test_execute_requires_exact_confirmation_before_mutation = None
    test_execute_supersedes_preserves_history_and_replays_idempotently = None
    test_interrupted_commit_fails_closed_and_retry_recovers = None
    test_tool_contract_and_plan_are_controlled_and_read_only = None

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        original = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        self.clean_peer_handoff_id = "HND-v057-clean-peer"
        clean_peer = self.service._handoff_record_projection(original)
        clean_peer["handoff_id"] = self.clean_peer_handoff_id
        clean_peer_record = self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.clean_peer_handoff_id,
            value=clean_peer,
        )
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            hub["handoffs"].append(
                self.service._handoff_record_projection(clean_peer_record)
            )

        self.original_trace_id = str(original["stage_trace_id"])
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        ) as incomplete:
            for field in (
                "production_preflight_status",
                "production_preflight_issue_count",
                "independent_audit_status",
            ):
                incomplete.pop(field, None)
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            row = next(
                item
                for item in hub["handoffs"]
                if item["handoff_id"] == self.old_handoff_id
            )
            for field in (
                "production_preflight_status",
                "production_preflight_issue_count",
                "independent_audit_status",
            ):
                row.pop(field, None)
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="authorized controlled preflight evidence reissue",
            targets=[
                self.gap_id,
                self.old_handoff_id,
                "controlled_phase:preflight_evidence_backfill",
            ],
            authoritative_source="signed project records",
            latest_version="0.5.7",
            target_boundary="one exact legacy handoff",
            must_keep=["history", "content", "independent audit separation"],
            must_remove=[],
            protected_scope=["storyboard", "canonical", "XLSX", "audit history"],
            expected_outputs=["one append-only production preflight evidence successor"],
            window_key=self.session_id,
            request_session_id=self.session_id,
        )
        self.backfill_work_order_id = str(response.work_order_id)

    def _backfill_plan(self) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.old_handoff_id],
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )

    def _backfill_execute(self, plan_sha256: str, confirmation: str | None = None) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.old_handoff_id],
            action="execute",
            expected_plan_sha256=plan_sha256,
            confirmation=(
                service_module.PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION
                if confirmation is None
                else confirmation
            ),
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )

    def _artifact_bytes(self) -> bytes:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        _record, path = self.service.store.get_artifact(
            self.namespace,
            self.project_id,
            str(handoff["storyboard_artifact_id"]),
        )
        return Path(path).read_bytes()

    def _remove_handoff_preflight_summary(self, handoff_id: str) -> None:
        fields = (
            "production_preflight_status",
            "production_preflight_issue_count",
            "independent_audit_status",
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        ) as handoff:
            for field in fields:
                handoff.pop(field, None)
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            row = next(
                item
                for item in hub["handoffs"]
                if item["handoff_id"] == handoff_id
            )
            for field in fields:
                row.pop(field, None)

    def _controlled_work_order(
        self, *, phase: str, handoff_ids: list[str], operation: str = "rule_improvement"
    ) -> str:
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation=operation,
            mode="execute",
            authorization="explicit_execute",
            request_text=f"authorized controlled {phase}",
            targets=[
                self.gap_id,
                *handoff_ids,
                f"controlled_phase:{phase}",
            ],
            authoritative_source="signed project records",
            latest_version="0.5.7",
            target_boundary="exact authorized handoff batch",
            must_keep=["history", "content", "independent audit separation"],
            must_remove=[],
            protected_scope=["storyboard", "canonical", "XLSX", "audit history"],
            expected_outputs=[phase],
            window_key=self.session_id,
            request_session_id=self.session_id,
        )
        return str(response.work_order_id)

    async def test_tool_count_stays_nineteen_and_phase_is_advertised(self) -> None:
        tools = await self.server.list_tools()
        self.assertEqual(len(tools), 19)
        recovery = next(
            tool.model_dump(mode="json", by_alias=True)
            for tool in tools
            if tool.name == "recover_storyboard_handoff"
        )
        phase = recovery["inputSchema"]["properties"]["phase"]
        self.assertEqual(
            set(phase["enum"]),
            {"handoff_recovery", "preflight_evidence_backfill"},
        )

    async def test_backfill_plan_is_zero_write_and_binds_exact_evidence(self) -> None:
        old_before = self.service.store.project_state_sha256(
            self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=self.old_handoff_id,
            )
        )
        hub_before = self.service.store.project_state_sha256(
            self.service.store.load_hub(self.namespace, self.project_id)
        )
        certificate_count = len(
            self.service.store.list_project_records(
                self.namespace, self.project_id, kind="certificates"
            )
        )
        artifact_before = self._artifact_bytes()
        response = self._backfill_plan()
        self.assertEqual(response.status, "preflight_evidence_reissue_plan_ready")
        plan = response.data["preflight_evidence_plan"]
        item = plan["items"][0]
        self.assertTrue(item["preflight_evidence_id"].startswith("PFE-"))
        self.assertEqual(item["production_preflight_issue_count"], 0)
        self.assertFalse(item["first_draft_grade_reconstructed"])
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace,
                self.project_id,
                kind="preflight_evidence_reissues",
            ),
            [],
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, self.project_id, kind="certificates"
                )
            ),
            certificate_count,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind="handoffs",
                    record_id=self.old_handoff_id,
                )
            ),
            old_before,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, self.project_id)
            ),
            hub_before,
        )
        self.assertEqual(self._artifact_bytes(), artifact_before)

    async def test_backfill_is_append_only_and_idempotent(self) -> None:
        old_before = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        old_sha = self.service.store.project_state_sha256(old_before)
        old_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(old_before["certificate_id"]),
        )
        old_certificate_sha = self.service.store.project_state_sha256(
            old_certificate
        )
        artifact_before = self._artifact_bytes()
        plan = self._backfill_plan().data["preflight_evidence_plan"]
        result = self._backfill_execute(plan["plan_sha256"])
        self.assertEqual(result.status, "preflight_evidence_reissued")
        mapping = result.data["mappings"][0]
        evidence = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=mapping["preflight_evidence_id"],
        )
        certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=mapping["certificate_id"],
        )
        self.assertEqual(evidence["status"], "committed")
        self.assertTrue(self.service.store.project_record_is_signed(evidence))
        self.assertEqual(
            certificate["evidence_origin"],
            "controlled_current_revalidation",
        )
        self.assertEqual(certificate["preflight_issue_count"], 0)
        self.assertNotIn("result", certificate)
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind="handoffs",
                    record_id=self.old_handoff_id,
                )
            ),
            old_sha,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.get_project_record(
                    self.namespace,
                    self.project_id,
                    kind="certificates",
                    record_id=str(old_before["certificate_id"]),
                )
            ),
            old_certificate_sha,
        )
        self.assertEqual(self._artifact_bytes(), artifact_before)
        replay = self._backfill_execute(plan["plan_sha256"])
        self.assertEqual(
            replay.data["mappings"][0]["preflight_evidence_id"],
            mapping["preflight_evidence_id"],
        )
        self.assertTrue(replay.data["mappings"][0]["idempotent_replay"])
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace,
                    self.project_id,
                    kind="preflight_evidence_reissues",
                )
            ),
            1,
        )

    async def test_wrong_confirmation_is_zero_write(self) -> None:
        plan = self._backfill_plan().data["preflight_evidence_plan"]
        with self.assertRaisesRegex(
            ValueError, "preflight_evidence_reissue_confirmation_required"
        ):
            self._backfill_execute(plan["plan_sha256"], confirmation="wrong")
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace,
                self.project_id,
                kind="preflight_evidence_reissues",
            ),
            [],
        )

    async def test_interrupted_certificate_write_recovers_same_identity(self) -> None:
        plan = self._backfill_plan().data["preflight_evidence_plan"]
        certificate_id = plan["items"][0]["new_certificate_id"]
        original = self.service.store.atomic_write_json
        tripped = False

        def fail_once(path: Path, value: dict[str, Any]) -> None:
            nonlocal tripped
            if (
                not tripped
                and path.parent.name == "certificates"
                and path.name == f"{certificate_id}.json"
            ):
                tripped = True
                raise OSError("simulated preflight certificate interruption")
            original(path, value)

        with patch.object(
            self.service.store, "atomic_write_json", side_effect=fail_once
        ):
            with self.assertRaisesRegex(
                OSError, "simulated preflight certificate interruption"
            ):
                self._backfill_execute(plan["plan_sha256"])
        evidence_id = plan["items"][0]["preflight_evidence_id"]
        prepared = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=evidence_id,
        )
        self.assertEqual(prepared["status"], "prepared")
        retry = self._backfill_execute(plan["plan_sha256"])
        self.assertEqual(
            retry.data["mappings"][0]["preflight_evidence_id"], evidence_id
        )
        committed = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=evidence_id,
        )
        self.assertEqual(committed["status"], "committed")

    async def test_explicit_failure_and_content_drift_are_rejected(self) -> None:
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        ) as failed:
            failed["production_preflight_status"] = (
                service_module.PRODUCTION_PREFLIGHT_FAIL
            )
            failed["production_preflight_issue_count"] = 1
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            row = next(
                item
                for item in hub["handoffs"]
                if item["handoff_id"] == self.old_handoff_id
            )
            row["production_preflight_status"] = (
                service_module.PRODUCTION_PREFLIGHT_FAIL
            )
            row["production_preflight_issue_count"] = 1
        with self.assertRaisesRegex(
            ValueError, "preflight_evidence_reissue_forbidden"
        ):
            self._backfill_plan()
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        ) as drifted:
            drifted.pop("production_preflight_status", None)
            drifted.pop("production_preflight_issue_count", None)
            drifted["fingerprints"]["artifact_raw"] = "1" * 64
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            row = next(
                item
                for item in hub["handoffs"]
                if item["handoff_id"] == self.old_handoff_id
            )
            row.pop("production_preflight_status", None)
            row.pop("production_preflight_issue_count", None)
            row["fingerprints"]["artifact_raw"] = "1" * 64
        with self.assertRaisesRegex(
            ValueError, "handoff_recovery_content_changed"
        ):
            self._backfill_plan()

    async def test_signed_legacy_certificate_report_failure_is_rejected(self) -> None:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(handoff["certificate_id"]),
        ) as certificate:
            for field in (
                "preflight_status",
                "preflight_issue_count",
                "independent_audit_status",
            ):
                certificate.pop(field, None)
            report = copy.deepcopy(certificate["report"])
            report["issues"] = [
                {
                    "error_code": "E-EXPLICIT-LEGACY-FAILURE",
                    "message": "signed legacy report contains a failure",
                }
            ]
            certificate["report"] = report
            certificate["report_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            ValueError, "handoff_recovery_certificate_invalid"
        ):
            self._backfill_plan()

    async def test_visible_workbook_projection_drift_is_rejected(self) -> None:
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        artifact_id = str(handoff["storyboard_artifact_id"])
        record, path = self.service.store.get_artifact(
            self.namespace, self.project_id, artifact_id
        )
        workbook = load_workbook(path)
        workbook["正式生产母版"].cell(row=2, column=5).value = (
            "tampered visible projection"
        )
        workbook.save(path)
        workbook.close()
        payload = path.read_bytes()
        meta_path = (
            self.service.store.project_dir(
                self.namespace, self.project_id
            )
            / "artifacts"
            / f"{artifact_id}.meta.json"
        )
        record["fingerprint"] = service_module.sha256_bytes(payload)
        record["size_bytes"] = len(payload)
        self.service.store.atomic_write_json(meta_path, record)
        with self.assertRaisesRegex(
            ValueError, "canonical sidecar invalid"
        ):
            self._backfill_plan()

    async def test_phase_bound_work_orders_cannot_be_reused(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "handoff_recovery_scope_mismatch"
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.recovery_work_order_id,
                gap_id=self.gap_id,
                handoff_ids=[self.old_handoff_id],
                action="plan",
                expected_plan_sha256=None,
                confirmation=None,
                request_session_id=self.session_id,
                phase="preflight_evidence_backfill",
            )
        backfill_plan = self._backfill_plan().data[
            "preflight_evidence_plan"
        ]
        self._backfill_execute(backfill_plan["plan_sha256"])
        with self.assertRaisesRegex(
            ValueError, "handoff_recovery_scope_mismatch"
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.backfill_work_order_id,
                gap_id=self.gap_id,
                handoff_ids=[self.old_handoff_id],
                action="plan",
                expected_plan_sha256=None,
                confirmation=None,
                request_session_id=self.session_id,
                phase="handoff_recovery",
            )
        old = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        self.assertEqual(old["status"], "prepared")

    async def test_signed_but_semantically_misbound_reissue_is_rejected(self) -> None:
        plan = self._backfill_plan().data["preflight_evidence_plan"]
        result = self._backfill_execute(plan["plan_sha256"])
        mapping = result.data["mappings"][0]
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(mapping["certificate_id"]),
        ) as certificate:
            certificate["source_fingerprint"] = "0" * 64
            certificate["report"]["issues"] = [
                {"error_code": "E-MISBOUND", "message": "tampered"}
            ]
        tampered_certificate = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=str(mapping["certificate_id"]),
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=str(mapping["preflight_evidence_id"]),
        ) as evidence:
            evidence["certificate_after"] = copy.deepcopy(
                tampered_certificate
            )
            evidence["certificate_after_sha256"] = (
                self.service.store.project_state_sha256(
                    tampered_certificate
                )
            )
        with self.assertRaisesRegex(
            ValueError,
            "(handoff_preflight_evidence_reissue_invalid|lifecycle_schema_mismatch)",
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.recovery_work_order_id,
                gap_id=self.gap_id,
                handoff_ids=[self.old_handoff_id],
                action="plan",
                expected_plan_sha256=None,
                confirmation=None,
                request_session_id=self.session_id,
            )

    async def test_batch_backfill_authority_is_consumable_by_batch_recovery(self) -> None:
        second_id = self.clean_peer_handoff_id
        self._remove_handoff_preflight_summary(second_id)
        handoff_ids = [self.old_handoff_id, second_id]
        backfill_work_order_id = self._controlled_work_order(
            phase="preflight_evidence_backfill",
            handoff_ids=handoff_ids,
        )
        backfill_plan_response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )
        backfill_plan = backfill_plan_response.data[
            "preflight_evidence_plan"
        ]
        self.assertEqual(
            backfill_plan["authorized_handoff_ids"], handoff_ids
        )
        backfill_result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=backfill_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="execute",
            expected_plan_sha256=backfill_plan["plan_sha256"],
            confirmation=service_module.PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION,
            request_session_id=self.session_id,
            phase="preflight_evidence_backfill",
        )
        self.assertEqual(len(backfill_result.data["mappings"]), 2)
        for mapping in backfill_result.data["mappings"]:
            evidence = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="preflight_evidence_reissues",
                record_id=mapping["preflight_evidence_id"],
            )
            self.assertEqual(evidence["authorized_handoff_ids"], handoff_ids)

        recovery_work_order_id = self._controlled_work_order(
            phase="handoff_recovery", handoff_ids=handoff_ids
        )
        recovery_plan_response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
        )
        recovery_plan = recovery_plan_response.data["recovery_plan"]
        recovery_result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="execute",
            expected_plan_sha256=recovery_plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=self.session_id,
        )
        self.assertEqual(
            [row["old_handoff_id"] for row in recovery_result.data["mappings"]],
            handoff_ids,
        )

    async def test_version_management_cannot_authorize_either_phase(self) -> None:
        evidence_count_before = len(
            self.service.store.list_project_records(
                self.namespace,
                self.project_id,
                kind="preflight_evidence_reissues",
            )
        )
        for phase in (
            "preflight_evidence_backfill",
            "handoff_recovery",
        ):
            work_order_id = self._controlled_work_order(
                phase=phase,
                handoff_ids=[self.old_handoff_id],
                operation="version_management",
            )
            with self.assertRaisesRegex(
                ValueError, "handoff_recovery_authority_required"
            ):
                self.service.recover_storyboard_handoff(
                    self.namespace,
                    project_id=self.project_id,
                    work_order_id=work_order_id,
                    gap_id=self.gap_id,
                    handoff_ids=[self.old_handoff_id],
                    action="plan",
                    expected_plan_sha256=None,
                    confirmation=None,
                    request_session_id=self.session_id,
                    phase=phase,
                )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace,
                    self.project_id,
                    kind="preflight_evidence_reissues",
                )
            ),
            evidence_count_before,
        )

    async def test_controlled_certificate_provenance_poison_is_rejected(self) -> None:
        plan = self._backfill_plan().data["preflight_evidence_plan"]
        result = self._backfill_execute(plan["plan_sha256"])
        mapping = result.data["mappings"][0]
        certificate_id = str(mapping["certificate_id"])
        evidence_id = str(mapping["preflight_evidence_id"])
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=certificate_id,
        ) as certificate:
            certificate["evidence_reissue_gap_id"] = "GAP-semantic-poison"
            certificate["original_certificate_id"] = "CERT-semantic-poison"
            certificate["authored_artifacts_modified"] = True
        poisoned = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="certificates",
            record_id=certificate_id,
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="preflight_evidence_reissues",
            record_id=evidence_id,
        ) as evidence:
            evidence["certificate_after"] = copy.deepcopy(poisoned)
            evidence["certificate_after_sha256"] = (
                self.service.store.project_state_sha256(poisoned)
            )
        with self.assertRaisesRegex(
            ValueError,
            "(handoff_preflight_evidence_reissue_invalid|lifecycle_schema_mismatch)",
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.recovery_work_order_id,
                gap_id=self.gap_id,
                handoff_ids=[self.old_handoff_id],
                action="plan",
                expected_plan_sha256=None,
                confirmation=None,
                request_session_id=self.session_id,
            )

    async def test_revalidation_traces_are_measured_and_claim_lifecycle_stays_published(self) -> None:
        backfill_plan = self._backfill_plan().data[
            "preflight_evidence_plan"
        ]
        backfill_result = self._backfill_execute(
            backfill_plan["plan_sha256"]
        )
        evidence_trace = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=backfill_result.data["mappings"][0]["trace_id"],
        )
        self.assertGreater(evidence_trace["wall_duration_ms"], 0)
        self.assertGreater(evidence_trace["stages"][0]["duration_ms"], 0)

        recovery_plan = self._plan().data["recovery_plan"]
        recovery_result = self._execute(recovery_plan["plan_sha256"])
        mapping = recovery_result.data["mappings"][0]
        recovery_trace = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=mapping["trace_id"],
        )
        self.assertGreater(recovery_trace["wall_duration_ms"], 0)
        self.assertGreater(recovery_trace["stages"][0]["duration_ms"], 0)
        claimed = self.service.store.claim_handoff(
            self.namespace,
            self.project_id,
            mapping["new_handoff_id"],
            consumer_work_order_id="WO-v057-auditor",
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
        )
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace, self.project_id, claimed
            )
        )
        with self.assertRaisesRegex(
            ValueError, "handoff_state_conflict: handoff is being consumed"
        ):
            self.service.store.claim_handoff(
                self.namespace,
                self.project_id,
                mapping["new_handoff_id"],
                consumer_work_order_id="WO-v057-auditor",
                expected_to_role="independent_audit",
                expected_types={"production_to_audit"},
            )
        released = self.service.store.release_handoff_claim(
            self.namespace,
            self.project_id,
            mapping["new_handoff_id"],
            consumer_work_order_id="WO-v057-auditor",
            claim_token=claimed["claim_token"],
            reason="retry fixture",
        )
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace, self.project_id, released
            )
        )
        reclaimed = self.service.store.claim_handoff(
            self.namespace,
            self.project_id,
            mapping["new_handoff_id"],
            consumer_work_order_id="WO-v057-auditor",
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
        )
        self.assertEqual(reclaimed["claim_generation"], 2)
        audit_certificate_id = "AUD-v057-finished"
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            hub_handoff = next(
                row
                for row in hub["handoffs"]
                if row["handoff_id"] == mapping["new_handoff_id"]
            )
            hub_handoff["status"] = "consumed"
            hub_handoff["audit_certificate_id"] = audit_certificate_id
        finished = self.service.store.finish_handoff(
            self.namespace,
            self.project_id,
            mapping["new_handoff_id"],
            consumer_work_order_id="WO-v057-auditor",
            audit_certificate_id=audit_certificate_id,
            claim_token=reclaimed["claim_token"],
        )
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace, self.project_id, finished
            )
        )
        consumed_replay = self.service.store.claim_handoff(
            self.namespace,
            self.project_id,
            mapping["new_handoff_id"],
            consumer_work_order_id="WO-v057-auditor",
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
        )
        self.assertTrue(consumed_replay["idempotent_replay"])

    async def test_mixed_original_and_reissued_evidence_recovers_atomically(self) -> None:
        backfill_plan = self._backfill_plan().data[
            "preflight_evidence_plan"
        ]
        self._backfill_execute(backfill_plan["plan_sha256"])
        mixed_session = "v057-mixed-recovery"
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="authorized mixed evidence recovery",
            targets=[
                self.gap_id,
                self.clean_peer_handoff_id,
                self.old_handoff_id,
                "controlled_phase:handoff_recovery",
            ],
            authoritative_source="signed project records",
            latest_version="0.5.7",
            target_boundary="two exact handoffs",
            must_keep=["history", "content", "independent audit separation"],
            must_remove=[],
            protected_scope=["storyboard", "canonical", "XLSX", "audit history"],
            expected_outputs=["two prepared PENDING_AUDIT successors"],
            window_key=mixed_session,
            request_session_id=mixed_session,
        )
        work_order_id = str(response.work_order_id)
        ids = [self.clean_peer_handoff_id, self.old_handoff_id]
        plan_response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            gap_id=self.gap_id,
            handoff_ids=ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=mixed_session,
        )
        plan = plan_response.data["recovery_plan"]
        self.assertEqual(
            [row["preflight_evidence_source"] for row in plan["items"]],
            ["original_handoff", "controlled_current_revalidation"],
        )
        result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            gap_id=self.gap_id,
            handoff_ids=ids,
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=mixed_session,
        )
        self.assertEqual(result.status, "handoffs_recovered")
        self.assertEqual(len(result.data["mappings"]), 2)
        handoff_count = len(
            self.service.store.list_project_records(
                self.namespace, self.project_id, kind="handoffs"
            )
        )
        for mapping in result.data["mappings"]:
            old = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=mapping["old_handoff_id"],
            )
            new = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=mapping["new_handoff_id"],
            )
            self.assertEqual(old["status"], "superseded")
            self.assertEqual(new["status"], "prepared")
            self.assertEqual(new["independent_audit_status"], "PENDING_AUDIT")
        independent = [
            row
            for row in self.service.store.list_project_records(
                self.namespace, self.project_id, kind="certificates"
            )
            if row.get("certificate_type") == "independent_audit"
        ]
        self.assertEqual(independent, [])
        replay = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            gap_id=self.gap_id,
            handoff_ids=ids,
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=mixed_session,
        )
        self.assertTrue(
            all(row["idempotent_replay"] for row in replay.data["mappings"])
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, self.project_id, kind="handoffs"
                )
            ),
            handoff_count,
        )

    async def test_recovery_successor_is_unclaimable_until_journal_commits(self) -> None:
        backfill_plan = self._backfill_plan().data[
            "preflight_evidence_plan"
        ]
        self._backfill_execute(backfill_plan["plan_sha256"])
        recovery_plan = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.old_handoff_id],
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
        ).data["recovery_plan"]
        new_handoff_id = recovery_plan["items"][0]["new_handoff_id"]
        original = self.service.store.atomic_write_json
        tripped = False

        def fail_after_hub_write(path: Path, value: dict[str, Any]) -> None:
            nonlocal tripped
            original(path, value)
            if not tripped and path.name == "hub.json":
                tripped = True
                raise OSError("simulated recovery journal finalization interruption")

        with patch.object(
            self.service.store,
            "atomic_write_json",
            side_effect=fail_after_hub_write,
        ):
            with self.assertRaisesRegex(
                OSError, "simulated recovery journal finalization"
            ):
                self.service.recover_storyboard_handoff(
                    self.namespace,
                    project_id=self.project_id,
                    work_order_id=self.recovery_work_order_id,
                    gap_id=self.gap_id,
                    handoff_ids=[self.old_handoff_id],
                    action="execute",
                    expected_plan_sha256=recovery_plan["plan_sha256"],
                    confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
                    request_session_id=self.session_id,
                )
        partial = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=new_handoff_id,
        )
        self.assertFalse(
            self.service.store.handoff_is_published(
                self.namespace, self.project_id, partial
            )
        )
        with self.assertRaisesRegex(ValueError, "uncommitted successor"):
            self.service.store.claim_handoff(
                self.namespace,
                self.project_id,
                new_handoff_id,
                consumer_work_order_id="WO-v057-auditor",
                expected_to_role="independent_audit",
                expected_types={"production_to_audit"},
            )
        retry = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.old_handoff_id],
            action="execute",
            expected_plan_sha256=recovery_plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=self.session_id,
        )
        self.assertEqual(
            retry.data["mappings"][0]["new_handoff_id"], new_handoff_id
        )
        committed = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=new_handoff_id,
        )
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace, self.project_id, committed
            )
        )


if __name__ == "__main__":
    unittest.main()
