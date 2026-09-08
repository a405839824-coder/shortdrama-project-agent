from __future__ import annotations

import copy
import os
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings

from test_v040_architecture import three_shot_storyboard
from run_golden_tests import build_adjacent_continuity_audit
from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    inject_semantic_audit,
)


class CompactMasterFullRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-compact-full-repair-")
        os.environ["SHORTDRAMA_DATA_DIR"] = self.temp.name
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8765"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        self.settings = Settings.from_env()
        self.server, self.service = create_mcp(self.settings)
        self.namespace = request_namespace(self.settings)
        await self._prepare_failed_audit_chain()

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = inject_semantic_audit(self.service, self.settings, name, arguments)
        result = await self.server.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.content)
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def rejection(self, name: str, arguments: dict[str, Any]) -> str:
        arguments = inject_semantic_audit(self.service, self.settings, name, arguments)
        try:
            result = await self.server.call_tool(name, arguments)
        except Exception as exc:
            return str(exc)
        return "\n".join(str(item) for item in result.content)

    async def begin(
        self,
        operation: str,
        *,
        mode: str,
        authorization: str,
        window_key: str,
        project_id: str | None = None,
    ) -> str:
        task = await self.call(
            "begin_project_task",
            {
                "project_id": project_id or self.project_id,
                "operation": operation,
                "mode": mode,
                "authorization": authorization,
                "request_text": "0.5.0紧凑母版完整结构修复事务测试",
                "targets": ["EP01-EP03"],
                "authoritative_source": "独立审计锁定基线",
                "latest_version": "current",
                "target_boundary": "EP01-EP03",
                "must_keep": ["原审计绑定与完整R10门禁"],
                "must_remove": [],
                "protected_scope": [],
                "expected_outputs": ["完整修复版", "repair_to_audit"],
                "window_key": window_key,
            },
        )
        return str(task["work_order_id"])

    @staticmethod
    def broken_storyboard() -> dict[str, Any]:
        broken = three_shot_storyboard()
        broken["shots"] = [broken["shots"][0]]
        broken["total_duration"] = 4
        evidence = broken["prevention_evidence"]
        for field in (
            "source_ledger",
            "scene_state_ledger",
            "shot_plan",
            "performance_chains",
        ):
            evidence[field] = evidence[field][:1]
        evidence["adjacent_continuity_audit"] = build_adjacent_continuity_audit(
            broken["shots"], evidence["scene_state_ledger"]
        )
        return broken

    async def _prepare_failed_audit_chain(self) -> None:
        created = await self.call(
            "create_project", {"project_name": "完整结构修复闭环专项"}
        )
        self.project_id = str(created["project_id"])
        await self.call(
            "get_agent_status",
            {"project_id": self.project_id, "full_self_test": True},
        )
        production_order = await self.begin(
            "storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            window_key="full-repair-production",
        )
        self.committed = await self.call(
            "commit_storyboard",
            {
                "project_id": self.project_id,
                "work_order_id": production_order,
                "storyboard": self.broken_storyboard(),
                "file_name": "结构性待修复基线.xlsx",
            },
        )
        self.assertEqual(self.committed["status"], "production_preflight_pass")
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            self.project_id,
            str(self.committed["artifact_id"]),
            shot_id="S001",
            field="画面内容",
            error_code="E-SHOTLOAD-001",
            issue_id="shots[1].episode_structure_incomplete",
            evidence=(
                "独立审计按锁定的EP01-EP03边界复核，确认当前单镜只覆盖第一剧情阶段，"
                "缺少后两阶段，必须完整重建受影响边界。"
            ),
            repair_action="按锁定边界完整重建三镜，并执行完整生产回归。",
        )
        self.audited = await self.call(
            "audit_storyboard",
            {
                "project_id": self.project_id,
                "artifact_id": self.committed["artifact_id"],
                "semantic_audit": semantic_failure,
            },
        )
        self.assertEqual(self.audited["status"], "fail_current")
        self.assertEqual(
            self.audited["data"]["repair_mode_recommendation"]["recommended_effort"],
            "xhigh",
        )
        self.package = self.audited["data"]["repair_package"]
        self.package_id = str(self.package["repair_package_id"])
        self.original_audit_order = str(self.audited["data"]["audit_work_order_id"])
        self.repair_order = await self.begin(
            "storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            window_key="full-repair-window",
        )

    def full_arguments(self, storyboard: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "work_order_id": self.repair_order,
            "storyboard": storyboard or three_shot_storyboard(),
            "file_name": "完整结构修复版.xlsx",
            "source_artifact_id": self.committed["artifact_id"],
            "repair_package_id": self.package_id,
        }

    async def test_success_consumes_package_commits_repair_successor_and_returns_auditor(
        self,
    ) -> None:
        repaired = await self.call("commit_storyboard", self.full_arguments())
        self.assertEqual(repaired["status"], "production_preflight_pass")
        self.assertTrue(repaired["data"]["structural_repair"])
        self.assertEqual(repaired["data"]["repair_transaction_state"], "committed")
        self.assertEqual(repaired["data"]["repair_package"]["status"], "consumed")
        canonical, _ = self.service._storyboard_from_artifact(
            self.namespace, self.project_id, repaired["artifact_id"])
        proof = canonical["prevention_evidence"]["repair_history"][-1]["repair_proof"]
        self.assertTrue(proof["content_changed"])
        self.assertEqual(proof["machine_regression"]["status"], "pass")
        self.assertEqual(proof["semantic_closure"], "requires_independent_audit")
        self.assertEqual(
            repaired["data"]["return_to_audit_work_order_id"],
            self.original_audit_order,
        )
        self.assertEqual(
            repaired["data"]["handoff"]["return_to_audit_work_order_id"],
            self.original_audit_order,
        )

        base_meta, _base_path = self.service.store.get_artifact(
            self.namespace, self.project_id, self.committed["artifact_id"]
        )
        metadata = base_meta["metadata"]
        self.assertEqual(metadata["successor_kind"], "repair")
        self.assertEqual(metadata["repair_successor_artifact_id"], repaired["artifact_id"])

        wrong_auditor = await self.rejection(
            "audit_storyboard",
            {
                "project_id": self.project_id,
                "artifact_id": repaired["artifact_id"],
                "handoff_artifact_id": repaired["data"]["handoff"]["handoff_id"],
            },
        )
        self.assertIn("repair_return_audit_mismatch", wrong_auditor)
        final = await self.call(
            "audit_storyboard",
            {
                "project_id": self.project_id,
                "artifact_id": repaired["artifact_id"],
                "work_order_id": self.original_audit_order,
                "handoff_artifact_id": repaired["data"]["handoff"]["handoff_id"],
            },
        )
        self.assertEqual(final["status"], "pass")
        self.assertEqual(
            final["data"]["repair_mode_recommendation"]["status"], "no_repair"
        )

    async def test_failed_repair_return_can_authorize_a_second_full_repair(
        self,
    ) -> None:
        """A failed repair successor remains an exact, package-bound source.

        The second repair must be authorized by the new failed-audit package,
        whose source handoff is the first ``repair_to_audit`` route.  It must
        not fall back to the original production handoff or reuse the first
        repair package.
        """

        first_storyboard = three_shot_storyboard()
        first_repair = await self.call(
            "commit_storyboard", self.full_arguments(first_storyboard)
        )
        first_handoff = first_repair["data"]["handoff"]
        self.assertEqual(first_handoff["handoff_type"], "repair_to_audit")

        second_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            self.project_id,
            str(first_repair["artifact_id"]),
            shot_id="EP01-001",
            field="画面内容",
            error_code="E-SHOTLOAD-001",
            issue_id="shots[1].repair_return_still_incomplete",
            evidence="原独审窗口复核修复返件，确认第一镜仍缺少必要的动作细化。",
            repair_action="仅在新修复包授权下补足第一镜动作，再返回同一独审窗口。",
        )
        second_audit = await self.call(
            "audit_storyboard",
            {
                "project_id": self.project_id,
                "artifact_id": first_repair["artifact_id"],
                "work_order_id": self.original_audit_order,
                "handoff_artifact_id": first_handoff["handoff_id"],
                "semantic_audit": second_failure,
            },
        )
        self.assertEqual(second_audit["status"], "fail_current")
        second_package = second_audit["data"]["repair_package"]
        self.assertNotEqual(
            second_package["repair_package_id"], self.package_id
        )
        self.assertEqual(
            second_package["production_handoff_id"],
            first_handoff["handoff_id"],
        )

        source_handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=str(second_package["production_handoff_id"]),
        )
        self.assertTrue(self.service.store.project_record_is_signed(source_handoff))
        self.assertEqual(source_handoff["handoff_type"], "repair_to_audit")
        self.assertEqual(
            source_handoff["audit_certificate_id"],
            second_package["audit_certificate_id"],
        )

        second_repair_order = await self.begin(
            "storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            window_key="full-repair-window-round-2",
        )
        second_storyboard = copy.deepcopy(first_storyboard)
        second_storyboard["shots"][0]["画面内容"] = second_storyboard[
            "shots"
        ][0]["画面内容"].replace(
            "女主转动视线", "女主缓慢转动视线"
        )
        second_repair = await self.call(
            "commit_storyboard",
            {
                "project_id": self.project_id,
                "work_order_id": second_repair_order,
                "storyboard": second_storyboard,
                "file_name": "第二轮完整结构修复版.xlsx",
                "source_artifact_id": first_repair["artifact_id"],
                "repair_package_id": second_package["repair_package_id"],
            },
        )
        self.assertEqual(second_repair["status"], "production_preflight_pass")
        self.assertEqual(
            second_repair["data"]["base_artifact_id"],
            first_repair["artifact_id"],
        )
        self.assertEqual(
            second_repair["data"]["repair_package"]["status"], "consumed"
        )
        self.assertEqual(
            second_repair["data"]["repair_package_id"],
            second_package["repair_package_id"],
        )
        self.assertEqual(
            second_repair["data"]["handoff"]["handoff_type"],
            "repair_to_audit",
        )
        self.assertEqual(
            second_repair["data"]["return_to_audit_work_order_id"],
            self.original_audit_order,
        )

    async def test_external_audit_intake_failure_authorizes_full_repair(
        self,
    ) -> None:
        """External intake has no producer trace; its audit trace stays exact."""

        created = await self.call(
            "create_project", {"project_name": "外部 intake 完整修复闭环"}
        )
        project_id = str(created["project_id"])
        await self.call(
            "get_agent_status",
            {"project_id": project_id, "full_self_test": True},
        )
        external = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            self.broken_storyboard(),
            file_name="外部待审结构基线.json",
            artifact_type="storyboard_json",
            version="external-v1",
        )
        artifact_id = str(external["artifact_id"])
        audit_order = await self.begin(
            "storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            window_key="external-intake-audit",
            project_id=project_id,
        )
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id="EP01-001",
            field="画面内容",
            error_code="E-SHOTLOAD-001",
            issue_id="shots[1].external_structure_incomplete",
            evidence="外部输入只覆盖第一阶段，独审要求在锁定边界内完整重建。",
            repair_action="使用服务器签发的修复包完整重建三镜后返回原独审窗口。",
        )
        audited = await self.call(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": artifact_id,
                "work_order_id": audit_order,
                "semantic_audit": semantic_failure,
            },
        )
        self.assertEqual(audited["status"], "fail_current")
        intake_handoff = audited["data"]["handoff"]
        certificate = audited["data"]["certificate"]
        package = audited["data"]["repair_package"]
        self.assertEqual(intake_handoff["handoff_type"], "audit_intake")
        self.assertEqual(intake_handoff["from_window_role"], "external_input")
        self.assertEqual(
            certificate["stage_trace_id"],
            certificate["production_stage_trace_id"],
        )
        self.assertEqual(
            package["production_handoff_id"], intake_handoff["handoff_id"]
        )
        audit_trace = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="telemetry",
            record_id=str(certificate["stage_trace_id"]),
        )
        self.assertEqual(audit_trace["operation"], "audit_storyboard")
        self.assertEqual(audit_trace["window_role"], "independent_audit")

        repair_order = await self.begin(
            "storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            window_key="external-intake-full-repair",
            project_id=project_id,
        )
        repair_arguments = {
            "project_id": project_id,
            "work_order_id": repair_order,
            "storyboard": three_shot_storyboard(),
            "file_name": "外部 intake 完整修复版.xlsx",
            "source_artifact_id": artifact_id,
            "repair_package_id": package["repair_package_id"],
        }

        # The intake handoff's source role is external_input, but the aliased
        # trace is still an independent-audit event.  Even a server-resigned
        # record using the source role must fail the exact trace-role binding
        # before the one-time package is claimed.
        package_before = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=str(package["repair_package_id"]),
        )
        original_trace = copy.deepcopy(audit_trace)
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="telemetry",
            record_id=str(certificate["stage_trace_id"]),
        ) as trace:
            trace["window_role"] = "external_input"
        rejected = await self.rejection("commit_storyboard", repair_arguments)
        self.assertIn(
            "production or audit trace changed identity", rejected
        )
        self.assertEqual(
            self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="repair_packages",
                record_id=str(package["repair_package_id"]),
            ),
            package_before,
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="telemetry",
            record_id=str(certificate["stage_trace_id"]),
        ) as trace:
            trace.clear()
            trace.update(copy.deepcopy(original_trace))

        repaired = await self.call("commit_storyboard", repair_arguments)
        self.assertEqual(repaired["status"], "production_preflight_pass")
        self.assertEqual(repaired["data"]["base_artifact_id"], artifact_id)
        self.assertEqual(repaired["data"]["repair_package"]["status"], "consumed")
        self.assertEqual(
            repaired["data"]["return_to_audit_work_order_id"], audit_order
        )

    async def test_external_audit_intake_failure_authorizes_patch_repair(
        self,
    ) -> None:
        """The same exact intake trace authority is shared by patch repair."""

        created = await self.call(
            "create_project", {"project_name": "外部 intake 局部修复闭环"}
        )
        project_id = str(created["project_id"])
        await self.call(
            "get_agent_status",
            {"project_id": project_id, "full_self_test": True},
        )
        base_storyboard = self.broken_storyboard()
        base_storyboard["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        external = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            base_storyboard,
            file_name="外部待审光源基线.json",
            artifact_type="storyboard_json",
            version="external-v1",
        )
        artifact_id = str(external["artifact_id"])
        audit_order = await self.begin(
            "storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            window_key="external-intake-patch-audit",
            project_id=project_id,
        )
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id="EP01-001",
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].external_light_source_mismatch",
            evidence="外部分镜的右侧窗光与锁定场景中的左侧窗光冲突。",
            repair_action="仅把该镜光源来源修正为左侧木格窗日光。",
        )
        audited = await self.call(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": artifact_id,
                "work_order_id": audit_order,
                "semantic_audit": semantic_failure,
            },
        )
        self.assertEqual(audited["status"], "fail_current")
        self.assertEqual(
            audited["data"]["handoff"]["handoff_type"], "audit_intake"
        )
        certificate = audited["data"]["certificate"]
        repair_order = await self.begin(
            "storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            window_key="external-intake-patch-repair",
            project_id=project_id,
        )
        patched = await self.call(
            "commit_storyboard_patch",
            {
                "project_id": project_id,
                "work_order_id": repair_order,
                "base_artifact_id": artifact_id,
                "expected_canonical_hash": certificate["canonical_sha256"],
                "expected_raw_artifact_fingerprint": external["fingerprint"],
                "audit_certificate_id": certificate["certificate_id"],
                "operations": [
                    {
                        "op": "replace_field",
                        "shot_id": "EP01-001",
                        "field": "光源来源",
                        "value": "左侧木格窗透入的日光",
                        "issue_ids": ["E-TEMPLATE-008"],
                        "reason": "仅修复独审证书确认的光源语义错位",
                    }
                ],
                "impact_scope": ["EP01-001"],
                "file_name": "外部 intake 局部修复版.xlsx",
            },
        )
        self.assertEqual(patched["status"], "production_preflight_pass")
        self.assertEqual(patched["data"]["base_artifact_id"], artifact_id)
        self.assertEqual(patched["data"]["repair_package"]["status"], "consumed")
        canonical, _ = self.service._storyboard_from_artifact(
            self.namespace, project_id, patched["artifact_id"])
        proof = canonical["prevention_evidence"]["repair_history"][-1]["repair_proof"]
        self.assertIn("shots[0].光源来源", proof["changed_paths"])
        self.assertEqual(proof["machine_regression"]["status"], "pass")
        self.assertEqual(
            patched["data"]["handoff"]["handoff_type"], "repair_to_audit"
        )
        self.assertEqual(
            patched["data"]["handoff"]["return_to_audit_work_order_id"],
            audit_order,
        )

    async def test_wrong_package_and_wrong_base_are_rejected_before_claim(self) -> None:
        wrong_package = self.full_arguments()
        wrong_package["repair_package_id"] = "RPK-does-not-exist"
        rejected = await self.rejection("commit_storyboard", wrong_package)
        self.assertIn("repair_package_required", rejected)

        wrong_base = self.full_arguments()
        wrong_base["source_artifact_id"] = self.committed["data"][
            "canonical_json_artifact_id"
        ]
        rejected = await self.rejection("commit_storyboard", wrong_base)
        self.assertIn("repair_package_binding_mismatch", rejected)
        package = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="repair_packages",
            record_id=self.package_id,
        )
        self.assertEqual(package["status"], "issued")

    async def test_damaged_authority_records_preserve_integrity_error_and_zero_mutation(
        self,
    ) -> None:
        project_dir = self.service.store.project_dir(
            self.namespace, self.project_id
        )
        authority_records = (
            ("repair_packages", self.package_id),
            ("certificates", str(self.package["audit_certificate_id"])),
            ("handoffs", str(self.package["audit_to_repair_handoff_id"])),
            ("handoffs", str(self.package["production_handoff_id"])),
        )
        for kind, record_id in authority_records:
            with self.subTest(kind=kind, record_id=record_id):
                record_path = project_dir / kind / f"{record_id}.json"
                original = self.service.store.read_json(record_path)
                damaged = copy.deepcopy(original)
                damaged["tamper_marker"] = f"damaged-{kind}-{record_id}"
                self.service.store.atomic_write_json(record_path, damaged)
                try:
                    damaged_bytes = record_path.read_bytes()
                    before = {
                        str(path.relative_to(project_dir)): path.read_bytes()
                        for path in sorted(project_dir.rglob("*"))
                        if path.is_file()
                    }

                    rejected = await self.rejection(
                        "commit_storyboard", self.full_arguments()
                    )
                    self.assertIn("project_record_integrity_mismatch", rejected)
                    self.assertIn(record_id, rejected)
                    self.assertEqual(record_path.read_bytes(), damaged_bytes)
                    self.assertEqual(
                        {
                            str(path.relative_to(project_dir)): path.read_bytes()
                            for path in sorted(project_dir.rglob("*"))
                            if path.is_file()
                        },
                        before,
                    )
                finally:
                    self.service.store.atomic_write_json(record_path, original)

    async def test_missing_production_handoff_is_domain_error_and_zero_mutation(
        self,
    ) -> None:
        project_dir = self.service.store.project_dir(
            self.namespace, self.project_id
        )
        handoff_id = str(self.package["production_handoff_id"])
        handoff_path = project_dir / "handoffs" / f"{handoff_id}.json"
        original = self.service.store.read_json(handoff_path)
        handoff_path.unlink()
        try:
            before = {
                str(path.relative_to(project_dir)): path.read_bytes()
                for path in sorted(project_dir.rglob("*"))
                if path.is_file()
            }
            rejected = await self.rejection(
                "commit_storyboard", self.full_arguments()
            )
            self.assertIn(
                "repair_package_binding_mismatch: original production "
                "handoff is missing",
                rejected,
            )
            self.assertNotIn(
                f"unknown handoffs record_id: {handoff_id}", rejected
            )
            self.assertEqual(
                {
                    str(path.relative_to(project_dir)): path.read_bytes()
                    for path in sorted(project_dir.rglob("*"))
                    if path.is_file()
                },
                before,
            )
            package = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="repair_packages",
                record_id=self.package_id,
            )
            self.assertEqual(package["status"], "issued")
        finally:
            self.service.store.atomic_write_json(handoff_path, original)

    async def test_repair_cannot_reuse_original_audit_window(self) -> None:
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            for row in hub["work_orders"]:
                if row["work_order_id"] == self.repair_order:
                    row["server_window_binding_id"] = self.package[
                        "audit_window_binding_id"
                    ]
                    break
        rejected = await self.rejection("commit_storyboard", self.full_arguments())
        self.assertIn("repair_window_separation_required", rejected)
        package = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="repair_packages",
            record_id=self.package_id,
        )
        self.assertEqual(package["status"], "issued")

    async def test_consumed_package_cannot_be_replayed(self) -> None:
        first = await self.call("commit_storyboard", self.full_arguments())
        self.assertEqual(first["status"], "production_preflight_pass")
        replay_order = await self.begin(
            "storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            window_key="full-repair-consumed-package-replay",
        )
        replay_arguments = self.full_arguments()
        replay_arguments["work_order_id"] = replay_order
        replay = await self.rejection("commit_storyboard", replay_arguments)
        self.assertIn("repair_package_unavailable", replay)
        base_meta, _base_path = self.service.store.get_artifact(
            self.namespace, self.project_id, self.committed["artifact_id"]
        )
        self.assertEqual(
            base_meta["metadata"]["successor_artifact_id"], first["artifact_id"]
        )

    async def test_failed_full_validation_releases_package_and_aborts_transaction(
        self,
    ) -> None:
        still_invalid = copy.deepcopy(three_shot_storyboard())
        still_invalid["shots"][0]["光源来源"] = ""
        failed = await self.call(
            "commit_storyboard", self.full_arguments(still_invalid)
        )
        self.assertEqual(failed["status"], "fail_current")
        self.assertEqual(failed["data"]["repair_transaction_state"], "aborted")
        package = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="repair_packages",
            record_id=self.package_id,
        )
        self.assertEqual(package["status"], "issued")
        base_meta, _base_path = self.service.store.get_artifact(
            self.namespace, self.project_id, self.committed["artifact_id"]
        )
        metadata = base_meta["metadata"]
        self.assertFalse(metadata.get("successor_artifact_id"))
        self.assertFalse(metadata.get("active_successor_claim"))
        self.assertEqual(metadata["successor_transaction"]["state"], "aborted")
        self.assertFalse(failed["data"]["artifact_created"])
        self.assertFalse(failed["data"]["handoff_created"])
        repair_handoffs = [
            row
            for row in self.service.store.list_project_records(
                self.namespace, self.project_id, kind="handoffs"
            )
            if row.get("handoff_type") == "repair_to_audit"
            and row.get("immutable_repair_package_id") == self.package_id
        ]
        self.assertEqual(
            repair_handoffs,
            [],
            "failed full validation must not export or create a repair handoff",
        )

    async def test_failed_repairs_accumulate_across_work_orders_and_allow_real_fix(self):
        invalid = copy.deepcopy(three_shot_storyboard())
        invalid["shots"][0]["光源来源"] = ""
        attempts = []
        for index in range(3):
            if index:
                self.repair_order = await self.begin("storyboard_repair", mode="audit_and_repair",
                    authorization="explicit_execute", window_key=f"retry-repair-{index}")
            failed = await self.call("commit_storyboard", self.full_arguments(invalid))
            self.assertEqual(failed["status"], "fail_current")
            attempts.append(failed["data"]["repair_attempt"])
            self.assertEqual(attempts[-1]["audit_round"], index + 1)
        self.assertEqual(len({event["audit_cycle_id"] for event in attempts}), 1)
        self.assertTrue(any(row["required_action"] == "rebuild_upstream"
                            for row in attempts[-1]["repair_convergence"]))
        self.repair_order = await self.begin("storyboard_repair", mode="audit_and_repair",
            authorization="explicit_execute", window_key="retry-repair-fixed")
        fixed = await self.call("commit_storyboard", self.full_arguments())
        self.assertEqual(fixed["status"], "production_preflight_pass")
        ledger = self.service.store.load_ledger(self.namespace, self.project_id)
        saved = [event for event in ledger["events"]
                 if event.get("repair_root_artifact_id") == attempts[0]["repair_root_artifact_id"]]
        self.assertEqual(len(saved), 4)
        self.assertEqual(saved[-1]["result"], "pass")
        self.assertEqual(saved[-1]["audit_round"], 4)
        self.assertEqual(saved[-1]["publication_status"], "not_yet_published")
        scope = next(row for row in ledger["scope_states"] if row["scope_key"] == saved[-1]["scope_key"])
        self.assertEqual(scope["closure_state"], "preflight_clean")
        from shortdrama_mcp.service import storyboard_agent
        ordinary = copy.deepcopy(saved[-1])
        ordinary.pop("repair_root_artifact_id")
        self.assertTrue(any("audit_round" in error for error in storyboard_agent.validate_event(ordinary)))
        self.assertEqual(ledger["policy"]["max_auto_repair_rounds"], 3)
        final = await self.call("audit_storyboard", {"project_id": self.project_id,
            "artifact_id": fixed["artifact_id"], "work_order_id": self.original_audit_order,
            "handoff_artifact_id": fixed["data"]["handoff"]["handoff_id"]})
        self.assertEqual(final["status"], "pass")

    async def test_final_cas_failure_compensates_package_and_rejects_handoff(
        self,
    ) -> None:
        with patch.object(
            self.service.store,
            "commit_storyboard_successor",
            side_effect=RuntimeError("simulated final repair CAS failure"),
        ):
            rejected = await self.rejection(
                "commit_storyboard", self.full_arguments()
            )
        self.assertIn("simulated final repair CAS failure", rejected)
        package = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="repair_packages",
            record_id=self.package_id,
        )
        self.assertEqual(package["status"], "issued")
        self.assertFalse(package.get("successor_artifact_id"))
        base_meta, _base_path = self.service.store.get_artifact(
            self.namespace, self.project_id, self.committed["artifact_id"]
        )
        metadata = base_meta["metadata"]
        self.assertFalse(metadata.get("successor_artifact_id"))
        self.assertEqual(metadata["successor_transaction"]["state"], "aborted")
        repair_handoffs = [
            row
            for row in self.service.store.list_project_records(
                self.namespace, self.project_id, kind="handoffs"
            )
            if row.get("handoff_type") == "repair_to_audit"
            and row.get("immutable_repair_package_id") == self.package_id
        ]
        self.assertEqual(len(repair_handoffs), 1)
        self.assertEqual(repair_handoffs[0]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
