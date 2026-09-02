from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings

from test_efficiency import valid_storyboard
from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    semantic_audit_for_artifact,
)


class V040ConditionalRepairSecurityTests(unittest.TestCase):
    """Security contract for two resident authorities plus conditional repair.

    These tests call the service directly so every role-sensitive operation can
    receive an explicit, deterministic MCP transport-session identifier.  The
    public MCP wiring is covered by the existing HTTP and stdio smoke suites.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v040-security-")
        os.environ["SHORTDRAMA_DATA_DIR"] = cls.temp.name
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8765"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        os.environ.pop("ALLOW_LOCAL_FILE_URLS", None)
        cls.settings = Settings.from_env()
        cls.server, cls.service = create_mcp(cls.settings)
        cls.namespace = request_namespace(cls.settings)

    def setUp(self) -> None:
        # Each security scenario owns a project. Several negative cases
        # intentionally leave a pending handoff or validating work order; in
        # v0.5.5 those states correctly block a later storyboard batch and
        # therefore must not leak into the next test.
        created = self.service.create_project(
            self.namespace, f"v0.4.0双常驻安全测试-{self._testMethodName}"
        )
        self.project_id = str(created.project_id)
        checked = self.service.agent_status(
            self.namespace, self.project_id, full_self_test=True
        )
        if not checked.data.get("runtime_test_certificate_valid"):
            raise AssertionError("v0.4.0 security tests require a valid runtime certificate")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _begin(
        self,
        *,
        operation: str,
        mode: str,
        authorization: str,
        session_id: str | None,
        targets: list[str],
        label: str,
    ) -> str:
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            request_text=f"v0.4.0双常驻加条件修复安全测试：{label}",
            targets=targets,
            authoritative_source="测试剧本v1",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["主线、对白、认知与连续性"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜", "独立审计证据链"],
            window_key=label,
            request_session_id=session_id,
        )
        return str(response.work_order_id)

    def _begin_production(self, session_id: str | None, label: str) -> str:
        return self._begin(
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id=session_id,
            targets=["第一集"],
            label=label,
        )

    def _begin_audit(
        self, artifact_id: str, session_id: str | None, label: str
    ) -> str:
        return self._begin(
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id=session_id,
            targets=[artifact_id],
            label=label,
        )

    def _begin_repair(self, session_id: str | None, label: str) -> str:
        return self._begin(
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            session_id=session_id,
            targets=["EP01-001"],
            label=label,
        )

    def _project_tree_bytes(self) -> dict[str, bytes]:
        project_dir = self.service.store.project_dir(
            self.namespace, self.project_id
        )
        return {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }

    def _commit(
        self,
        work_order_id: str,
        storyboard: dict[str, Any],
        session_id: str | None,
        label: str,
    ) -> Any:
        return self.service.commit_storyboard(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            file_name=f"{label}.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
        )

    @staticmethod
    def _fixture(label: str) -> dict[str, Any]:
        """Return a valid but cache-distinct storyboard for one security case."""

        storyboard = valid_storyboard()
        storyboard["source_lock"]["上集终点"] += f"【{label}】"
        return storyboard

    def _audit(
        self,
        *,
        artifact_id: str,
        audit_work_order_id: str,
        handoff_id: str,
        session_id: str | None,
        audit_cycle_id: str | None = None,
        semantic_audit: dict[str, Any] | None = None,
    ) -> Any:
        return self.service.audit_storyboard(
            self.namespace,
            project_id=self.project_id,
            artifact_id=artifact_id,
            audit_cycle_id=audit_cycle_id,
            work_order_id=audit_work_order_id,
            handoff_artifact_id=handoff_id,
            semantic_audit=semantic_audit
            or semantic_audit_for_artifact(
                self.service, self.settings, self.project_id, artifact_id
            ),
            request_session_id=session_id,
        )

    def _prepare_failed_audit_chain(
        self, *, production_session: str, audit_session: str, label: str
    ) -> tuple[str, Any, str, Any]:
        production_work_order = self._begin_production(
            production_session, f"{label}-production"
        )
        broken = self._fixture(f"{label}-broken")
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        committed = self._commit(
            production_work_order, broken, production_session, f"{label}-broken"
        )
        self.assertEqual(committed.status, "production_preflight_pass")
        audit_work_order = self._begin_audit(
            str(committed.artifact_id), audit_session, f"{label}-audit"
        )
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            self.project_id,
            str(committed.artifact_id),
            shot_id="S001",
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认场景锁要求左侧窗光，当前右侧来源与其冲突。",
            repair_action="仅修正光源来源字段后执行完整回归。",
        )
        audited = self._audit(
            artifact_id=str(committed.artifact_id),
            audit_work_order_id=audit_work_order,
            handoff_id=str(committed.data["handoff"]["handoff_id"]),
            session_id=audit_session,
            semantic_audit=semantic_failure,
        )
        self.assertEqual(audited.status, "fail_current")
        self.assertIsNotNone(audited.data.get("repair_package"))
        return production_work_order, committed, audit_work_order, audited

    @staticmethod
    def _patch_operations() -> list[dict[str, Any]]:
        return [
            {
                "op": "replace_field",
                "shot_id": "EP01-001",
                "field": "光源来源",
                "value": "左侧木格窗透入的日光",
                "issue_ids": ["E-TEMPLATE-008"],
                "reason": "只修复独立审计确认的光源语义错位，不改变剧情或结构",
            }
        ]

    def _attempt_patch(
        self,
        *,
        repair_work_order_id: str,
        committed: Any,
        audited: Any,
        repair_session: str,
        label: str,
    ) -> Any:
        return self.service.commit_storyboard_patch(
            self.namespace,
            project_id=self.project_id,
            work_order_id=repair_work_order_id,
            base_artifact_id=str(committed.artifact_id),
            expected_canonical_hash=str(committed.data["canonical_sha256"]),
            expected_raw_artifact_fingerprint=None,
            audit_certificate_id=str(audited.data["certificate"]["certificate_id"]),
            operations=self._patch_operations(),
            impact_scope=["EP01-001"],
            file_name=f"{label}.xlsx",
            request_session_id=repair_session,
        )

    def test_same_transport_session_cannot_cross_any_role_boundary(self) -> None:
        shared = "transport-shared-production-audit"
        production_work_order = self._begin_production(shared, "same-session-production")
        committed = self._commit(
            production_work_order,
            self._fixture("same-session-valid"),
            shared,
            "same-session-valid",
        )
        before_forbidden_audit = self._project_tree_bytes()
        with self.assertRaises(ValueError) as forbidden_audit:
            self._begin_audit(
                str(committed.artifact_id), shared, "same-session-audit"
            )
        self.assertEqual(
            str(forbidden_audit.exception).split(":", 1)[0],
            "session_role_separation_required",
        )
        self.assertEqual(self._project_tree_bytes(), before_forbidden_audit)

        # Close the first production-to-audit handoff through a genuinely
        # independent transport before starting the next production chain.
        independent_session = "transport-independent-cleanup-audit"
        clean_audit_work_order = self._begin_audit(
            str(committed.artifact_id),
            independent_session,
            "same-session-cleanup-audit",
        )
        clean = self._audit(
            artifact_id=str(committed.artifact_id),
            audit_work_order_id=clean_audit_work_order,
            handoff_id=str(committed.data["handoff"]["handoff_id"]),
            session_id=independent_session,
        )
        self.assertEqual(clean.status, "pass")

        production_session = "transport-production-for-repair-separation"
        audit_session = "transport-audit-for-repair-separation"
        _production, broken, _audit, audited = self._prepare_failed_audit_chain(
            production_session=production_session,
            audit_session=audit_session,
            label="repair-separation",
        )

        for reused_session, label in (
            (production_session, "repair-reuses-production"),
            (audit_session, "repair-reuses-audit"),
        ):
            with self.subTest(reused_session=reused_session):
                before_forbidden_repair = self._project_tree_bytes()
                with self.assertRaises(ValueError) as forbidden_repair:
                    self._begin_repair(reused_session, label)
                self.assertEqual(
                    str(forbidden_repair.exception).split(":", 1)[0],
                    "session_role_separation_required",
                )
                self.assertEqual(
                    self._project_tree_bytes(), before_forbidden_repair
                )

    def test_cloud_role_claim_without_transport_session_fails_closed(self) -> None:
        cloud_settings = replace(self.settings, deployment_mode="cloud")
        profiles = (
            ("storyboard_create", "execute", "explicit_execute", "cloud-production"),
            ("storyboard_audit", "audit_only", "read_only", "cloud-audit"),
            (
                "storyboard_repair",
                "audit_and_repair",
                "explicit_execute",
                "cloud-repair",
            ),
        )
        with patch.object(self.service, "settings", cloud_settings):
            for operation, mode, authorization, label in profiles:
                with self.subTest(role=label), self.assertRaisesRegex(
                    ValueError, "window_session_required"
                ):
                    self._begin(
                        operation=operation,
                        mode=mode,
                        authorization=authorization,
                        session_id=None,
                        targets=["第一集"],
                        label=label,
                    )

    def test_forged_audit_cycle_id_is_rejected_without_consuming_handoff(self) -> None:
        production_session = "cycle-production"
        audit_session = "cycle-audit"
        production_work_order = self._begin_production(
            production_session, "cycle-production"
        )
        committed = self._commit(
            production_work_order,
            self._fixture("cycle-valid"),
            production_session,
            "cycle-valid",
        )
        handoff = committed.data["handoff"]
        audit_work_order = self._begin_audit(
            str(committed.artifact_id), audit_session, "cycle-audit"
        )

        forged_cycle = f"{handoff['audit_cycle_id']}-forged"
        with self.assertRaisesRegex(ValueError, "audit_cycle_mismatch"):
            self._audit(
                artifact_id=str(committed.artifact_id),
                audit_work_order_id=audit_work_order,
                handoff_id=str(handoff["handoff_id"]),
                session_id=audit_session,
                audit_cycle_id=forged_cycle,
            )

        audited = self._audit(
            artifact_id=str(committed.artifact_id),
            audit_work_order_id=audit_work_order,
            handoff_id=str(handoff["handoff_id"]),
            session_id=audit_session,
        )
        self.assertEqual(audited.status, "pass")
        self.assertEqual(
            audited.data["audit"]["event"]["audit_cycle_id"],
            handoff["audit_cycle_id"],
        )

    def test_consumed_handoff_rejects_replay_by_different_audit_work_order(self) -> None:
        production_session = "replay-production"
        audit_session = "replay-audit"
        production_work_order = self._begin_production(
            production_session, "replay-production"
        )
        committed = self._commit(
            production_work_order,
            self._fixture("replay-valid"),
            production_session,
            "replay-valid",
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        first_audit_work_order = self._begin_audit(
            str(committed.artifact_id), audit_session, "replay-audit-1"
        )
        first = self._audit(
            artifact_id=str(committed.artifact_id),
            audit_work_order_id=first_audit_work_order,
            handoff_id=handoff_id,
            session_id=audit_session,
        )
        self.assertEqual(first.status, "pass")

        second_audit_work_order = self._begin_audit(
            str(committed.artifact_id), audit_session, "replay-audit-2"
        )
        with self.assertRaisesRegex(ValueError, "handoff_state_conflict"):
            self._audit(
                artifact_id=str(committed.artifact_id),
                audit_work_order_id=second_audit_work_order,
                handoff_id=handoff_id,
                session_id=audit_session,
            )

    def test_role_boundaries_block_cross_authority_tools_and_final_verdict(self) -> None:
        production_session = "role-production"
        audit_session = "role-audit"
        repair_session = "role-repair"
        production_work_order = self._begin_production(
            production_session, "role-production"
        )
        committed = self._commit(
            production_work_order,
            self._fixture("role-valid"),
            production_session,
            "role-valid",
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_work_order = self._begin_audit(
            str(committed.artifact_id), audit_session, "role-audit"
        )
        repair_work_order = self._begin_repair(repair_session, "role-repair")

        with self.assertRaisesRegex(
            ValueError, "production_authority_required|window_role_matrix_mismatch"
        ):
            self._commit(
                audit_work_order,
                self._fixture("audit-cannot-commit"),
                audit_session,
                "audit-cannot-commit",
            )

        with self.assertRaisesRegex(
            ValueError,
            "independent_audit_authority_required|window_role_matrix_mismatch",
        ):
            self._audit(
                artifact_id=str(committed.artifact_id),
                audit_work_order_id=repair_work_order,
                handoff_id=handoff_id,
                session_id=repair_session,
            )

        with self.assertRaisesRegex(
            ValueError,
            "independent_audit_authority_required|window_role_matrix_mismatch",
        ):
            self._audit(
                artifact_id=str(committed.artifact_id),
                audit_work_order_id=production_work_order,
                handoff_id=handoff_id,
                session_id=production_session,
            )

        hub = self.service.store.load_hub(self.namespace, self.project_id)
        production_state = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == production_work_order
        )
        self.assertEqual(production_state["status"], "validating")
        self.assertFalse(committed.data["validation"]["final_verdict"])
        with self.assertRaisesRegex(ValueError, "independent_audit_required"):
            self.service.signoff_task(
                self.namespace,
                project_id=self.project_id,
                work_order_id=production_work_order,
                decision="approve",
                signer="安全测试签核人",
            )


if __name__ == "__main__":
    unittest.main()
