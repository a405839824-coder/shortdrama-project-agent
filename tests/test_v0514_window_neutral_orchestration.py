from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import shortdrama_mcp.service as service_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.models import ToolResponse

from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    semantic_audit_for_artifact,
)
from test_efficiency import valid_storyboard


class V0514WindowNeutralOrchestrationTests(unittest.TestCase):
    """Authenticated windows orchestrate roles; server identities stay split."""

    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="shortdrama-v0514-window-neutral-"
        )
        self.settings = Settings(
            data_dir=Path(self.temp.name),
            public_base_url="https://example.invalid",
            host="127.0.0.1",
            port=8765,
            deployment_mode="cloud",
            auth_mode="none",
            oauth_issuer=None,
            oauth_audience=None,
            oauth_jwks_url=None,
            oauth_scopes=("storyboard:read", "storyboard:write"),
            allow_local_file_urls=False,
            max_file_bytes=50 * 1024 * 1024,
        )
        self.server, self.service = create_mcp(self.settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(self.service)
        self.project_id = str(
            self.service.create_project(
                self.namespace, "0.5.14窗口无角色限制"
            ).project_id
        )
        status = self.service.agent_status(
            self.namespace, self.project_id, full_self_test=True
        )
        self.assertTrue(status.data["runtime_test_certificate_valid"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _begin(
        self,
        *,
        operation: str,
        mode: str,
        authorization: str,
        session_id: str,
        targets: list[str],
    ) -> str:
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            request_text=f"窗口无角色限制：{operation}",
            targets=targets,
            authoritative_source="已锁定项目证据链",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["剧情、对白、历史证据和独立审计"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜", "独立审计证据链"],
            window_key="chat",
            request_session_id=session_id,
            action="create_window_neutral",
        )
        self.assertEqual(
            response.data["window_access_contract"],
            service_module.WINDOW_NEUTRAL_ACCESS_CONTRACT,
        )
        self.assertEqual(response.data["session_binding_status"], "window_neutral")
        return str(response.work_order_id)

    def _preflight(
        self,
        work_order_id: str,
        storyboard: dict[str, Any],
        sessions: list[str],
    ) -> Any:
        for index, group in enumerate(service_module._scene_scope_groups(storyboard)):
            aliases = list(group.get("scene_aliases") or [])
            scope_id = str(aliases[0] if aliases else group["stable_scene_id"])
            response = self.service.preflight_storyboard_draft(
                self.namespace,
                project_id=self.project_id,
                work_order_id=work_order_id,
                storyboard=storyboard,
                scope_kind="scene",
                scope_id=scope_id,
                request_session_id=sessions[index % len(sessions)],
            )
            self.assertEqual(response.status, service_module.DRAFT_PREFLIGHT_PASS)
        return self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            scope_kind="full",
            scope_id="EP01",
            request_session_id=sessions[-1],
        )

    def test_one_window_can_create_production_audit_and_repair_identities(self) -> None:
        session = "openai:one-window-all-actions"
        production_id = self._begin(
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id=session,
            targets=["第一集"],
        )
        audit_id = self._begin(
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id=session,
            targets=["ART-pending"],
        )
        repair_id = self._begin(
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            session_id=session,
            targets=["EP01-001"],
        )

        hub = self.service.store.load_hub(self.namespace, self.project_id)
        orders = {
            row["work_order_id"]: row
            for row in hub["work_orders"]
            if row["work_order_id"] in {production_id, audit_id, repair_id}
        }
        self.assertEqual(
            {orders[value]["server_actor_role"] for value in orders},
            {"production", "independent_audit", "repair"},
        )
        self.assertEqual(
            len({orders[value]["server_actor_binding_id"] for value in orders}),
            3,
        )
        self.assertEqual(
            len({orders[value]["server_window_binding_id"] for value in orders}),
            3,
        )
        for order in orders.values():
            binding = self.service._assert_work_order_session(
                self.namespace, order, "openai:a-different-window"
            )
            self.assertEqual(binding["work_order_id"], order["work_order_id"])

    def test_public_create_action_routes_to_window_neutral_service_contract(self) -> None:
        response = ToolResponse(
            status="created",
            summary="test",
            project_id=self.project_id,
            work_order_id="WO-window-neutral-routing",
        )
        arguments = {
            "project_id": self.project_id,
            "operation": "storyboard_create",
            "mode": "execute",
            "authorization": "explicit_execute",
            "request_text": "公开工具默认窗口无角色限制",
            "targets": ["第一集"],
            "authoritative_source": "已锁定项目证据链",
            "latest_version": "current",
            "target_boundary": "第一集",
            "must_keep": ["剧情"],
            "must_remove": [],
            "protected_scope": [],
            "expected_outputs": ["正式分镜"],
        }
        # The HTTP OAuth gate is covered separately. This routing-only fixture
        # uses a local principal rather than unauthenticated cloud access.
        with patch.object(self.server.access, "settings", replace(self.settings, deployment_mode="local")), patch.object(
            self.service, "begin_task", return_value=response
        ) as mocked:
            asyncio.run(
                self.server.call_tool("begin_project_task", arguments)
            )
        self.assertEqual(
            mocked.call_args.kwargs["action"], "create_window_neutral"
        )

    def test_different_windows_can_preflight_commit_and_audit_same_batch(self) -> None:
        production_id = self._begin(
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id="openai:production-request-window",
            targets=["第一集"],
        )
        storyboard = valid_storyboard()
        preflight = self._preflight(
            production_id,
            storyboard,
            [
                "openai:scene-preflight-window",
                "openai:full-preflight-window",
            ],
        )
        self.assertTrue(preflight.data["commit_ready"])
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=self.project_id,
            work_order_id=production_id,
            storyboard=storyboard,
            file_name="v0514-window-neutral.xlsx",
            source_artifact_id=None,
            request_session_id="openai:commit-window",
            draft_preflight_id=str(preflight.trace_id),
        )
        artifact_id = str(committed.artifact_id)
        handoff_id = str(committed.data["handoff"]["handoff_id"])

        pending = self.service.agent_status(
            self.namespace,
            self.project_id,
            request_session_id="openai:any-status-window",
        )
        self.assertTrue(pending.data["window_neutral_orchestration"])
        self.assertFalse(pending.data["window_role_restrictions"])
        self.assertTrue(pending.data["execution_available"])
        self.assertEqual(pending.data["execution_mode"], "audit_resolution")
        self.assertEqual(
            pending.data["allowed_storyboard_operations"],
            ["storyboard_audit"],
        )
        with self.assertRaisesRegex(ValueError, "independent_audit_pending"):
            self._begin(
                operation="storyboard_create",
                mode="execute",
                authorization="explicit_execute",
                session_id="openai:any-production-window",
                targets=["第二集"],
            )

        audit_id = self._begin(
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id="openai:production-request-window",
            targets=[artifact_id],
        )
        audited = self.service.audit_storyboard(
            self.namespace,
            project_id=self.project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_id,
            handoff_artifact_id=handoff_id,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                self.project_id,
                artifact_id,
            ),
            request_session_id="openai:audit-execution-window",
        )
        self.assertEqual(audited.status, "pass")
        certificate = audited.data["certificate"]
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        self.assertNotEqual(
            certificate["audit_actor_binding_id"],
            handoff["from_actor_binding_id"],
        )
        self.assertNotEqual(
            certificate["audit_authority_root_id"],
            handoff["from_authority_root_id"],
        )
        self.assertEqual(certificate["audit_class"], "independent_audit")
        self.assertNotEqual(
            certificate["certificate_id"],
            committed.data["certificate"]["certificate_id"],
        )

    def test_window_neutral_cloud_call_still_requires_authenticated_session(self) -> None:
        work_order_id = self._begin(
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id="openai:authenticated-window",
            targets=["第一集"],
        )
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == work_order_id
        )
        with self.assertRaisesRegex(ValueError, "authenticated_window_required"):
            self.service._assert_work_order_session(
                self.namespace, work_order, None
            )

    def test_repair_and_return_to_original_audit_can_cross_windows(self) -> None:
        production_id = self._begin(
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id="openai:repair-production-request",
            targets=["第一集"],
        )
        broken = valid_storyboard()
        broken["source_lock"]["上集终点"] += "【window-neutral-repair】"
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        preflight = self._preflight(
            production_id,
            broken,
            ["openai:repair-preflight-a", "openai:repair-preflight-b"],
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=self.project_id,
            work_order_id=production_id,
            storyboard=broken,
            file_name="v0514-repair-base.xlsx",
            source_artifact_id=None,
            request_session_id="openai:repair-commit-window",
            draft_preflight_id=str(preflight.trace_id),
        )
        artifact_id = str(committed.artifact_id)
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_id = self._begin(
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id="openai:repair-audit-request",
            targets=[artifact_id],
        )
        shot_id = str(broken["shots"][0]["镜头编号"])
        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=self.project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_id,
            handoff_artifact_id=handoff_id,
            semantic_audit=failing_semantic_audit_for_artifact(
                self.service,
                self.settings,
                self.project_id,
                artifact_id,
                shot_id=shot_id,
                field="光源来源",
                error_code="E-TEMPLATE-008",
                issue_id="shots[1].window_neutral_light_source",
                evidence="独立审计确认右侧窗光与锁定的左侧自然光冲突。",
                repair_action="仅修正该镜光源来源并返回原独立审计身份复核。",
            ),
            request_session_id="openai:repair-audit-execution",
        )
        self.assertEqual(failed.status, "fail_current")

        repair_id = self._begin(
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            session_id="openai:repair-request-window",
            targets=[shot_id],
        )
        repaired = self.service.commit_storyboard_patch(
            self.namespace,
            project_id=self.project_id,
            work_order_id=repair_id,
            base_artifact_id=artifact_id,
            expected_canonical_hash=str(committed.data["canonical_sha256"]),
            expected_raw_artifact_fingerprint=None,
            audit_certificate_id=str(
                failed.data["certificate"]["certificate_id"]
            ),
            operations=[
                {
                    "op": "replace_field",
                    "shot_id": shot_id,
                    "field": "光源来源",
                    "value": valid_storyboard()["shots"][0]["光源来源"],
                    "issue_ids": ["E-TEMPLATE-008"],
                    "reason": "按独立审计证据恢复锁定的左侧自然光来源",
                }
            ],
            impact_scope=[shot_id],
            file_name="v0514-repaired.xlsx",
            request_session_id="openai:repair-execution-window",
        )
        repair_handoff_id = str(
            repaired.data.get("handoff_artifact_id")
            or repaired.data["handoff"]["handoff_id"]
        )
        passed = self.service.audit_storyboard(
            self.namespace,
            project_id=self.project_id,
            artifact_id=str(repaired.artifact_id),
            audit_cycle_id=None,
            work_order_id=audit_id,
            handoff_artifact_id=repair_handoff_id,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                self.project_id,
                str(repaired.artifact_id),
            ),
            request_session_id="openai:reaudit-third-window",
        )
        self.assertEqual(passed.status, "pass")
        self.assertEqual(
            passed.data["certificate"]["work_order_id"], audit_id
        )

    def test_legacy_service_create_keeps_0513_window_binding_compatibility(self) -> None:
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="0.5.13兼容工单",
            targets=["第一集"],
            authoritative_source="已锁定项目证据链",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["历史证据"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key="legacy",
            request_session_id="openai:legacy-window",
            action="create",
        )
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == response.work_order_id
        )
        self.assertNotIn("window_access_contract", work_order)
        with self.assertRaisesRegex(ValueError, "window_session_mismatch"):
            self.service._assert_work_order_session(
                self.namespace, work_order, "openai:other-window"
            )
