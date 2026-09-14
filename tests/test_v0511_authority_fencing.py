from __future__ import annotations

import copy
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import shortdrama_mcp.service as service_module
import shortdrama_mcp.storage as storage_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings

from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    semantic_audit_for_artifact,
)
from test_efficiency import valid_storyboard


CONTINUATION_CONFIRMATION = "ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION"


class V0511AuthorityFencingTests(unittest.TestCase):
    """P0 fences for rotating a lost production/audit/repair window.

    These tests deliberately synchronize at the persistence boundary and
    exercise the signed storage journal directly.  A continuation must never
    admit two physical sessions that can both complete the same logical work.
    """

    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="shortdrama-v0511-authority-fencing-"
        )
        self.settings = self._settings(
            Path(self.temp.name) / "cloud", deployment_mode="cloud"
        )
        _server, self.service = create_mcp(self.settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(self.service)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _settings(data_dir: Path, *, deployment_mode: str) -> Settings:
        return Settings(
            data_dir=data_dir,
            public_base_url="https://example.invalid",
            host="127.0.0.1",
            port=8765,
            deployment_mode=deployment_mode,
            auth_mode="none",
            oauth_issuer=None,
            oauth_audience=None,
            oauth_jwks_url=None,
            oauth_scopes=("storyboard:read", "storyboard:write"),
            allow_local_file_urls=False,
            max_file_bytes=50 * 1024 * 1024,
        )

    def _project(self, label: str, *, service: Any | None = None) -> str:
        target = service or self.service
        project_id = str(
            target.create_project(self.namespace, label).project_id
        )
        status = target.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(
            status.data.get("runtime_test_certificate_valid"), status.data
        )
        return project_id

    def _project_tree_bytes(self, project_id: str) -> dict[str, bytes]:
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        return {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }

    def _authority_lease_tree_bytes(self) -> dict[str, bytes]:
        lease_root = (
            self.service.store.root / ".authority-operation-leases"
        )
        if not lease_root.is_dir():
            return {}
        return {
            str(path.relative_to(lease_root)): path.read_bytes()
            for path in sorted(lease_root.rglob("*"))
            if path.is_file()
        }

    def _replace_with_unsigned_record(self, path: Path) -> bytes:
        record = self.service.store.read_json(path)
        self.assertIsInstance(record.get("integrity_hmac"), str, record)
        record.pop("integrity_hmac")
        self.service.store.atomic_write_json(path, record)
        unsigned_bytes = path.read_bytes()
        self.assertNotIn(
            "integrity_hmac", self.service.store.read_json(path)
        )
        return unsigned_bytes

    def _assert_unsigned_record_rejected_without_laundering(
        self,
        *,
        project_id: str,
        record_path: Path,
        expected_error: str,
        protected_write: Any,
    ) -> None:
        unsigned_bytes = record_path.read_bytes()
        before_project = self._project_tree_bytes(project_id)
        before_leases = self._authority_lease_tree_bytes()
        for attempt in range(2):
            with self.subTest(attempt=attempt + 1):
                with self.assertRaisesRegex(
                    ValueError, f"^{expected_error}$"
                ):
                    protected_write()
                self.assertEqual(
                    self._project_tree_bytes(project_id), before_project
                )
                self.assertEqual(
                    self._authority_lease_tree_bytes(), before_leases
                )
                self.assertEqual(record_path.read_bytes(), unsigned_bytes)
                self.assertNotIn(
                    "integrity_hmac",
                    self.service.store.read_json(record_path),
                )

    def _begin_on(
        self,
        service: Any,
        project_id: str,
        *,
        operation: str,
        mode: str,
        authorization: str,
        session_id: str | None,
        targets: list[str],
        label: str,
    ) -> str:
        response = service.begin_task(
            self.namespace,
            project_id=project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            request_text=f"v0.5.11权限栅栏测试：{label}",
            targets=targets,
            authoritative_source="已锁定测试剧本与项目证据链",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["剧情、对白、历史证据与角色隔离"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜", "独立审计证据链"],
            window_key=label,
            request_session_id=session_id,
            action="create",
            resume_work_order_id=None,
            expected_plan_sha256=None,
            confirmation=None,
        )
        return str(response.work_order_id)

    def _begin(
        self,
        project_id: str,
        *,
        operation: str,
        mode: str,
        authorization: str,
        session_id: str | None,
        targets: list[str],
        label: str,
    ) -> str:
        return self._begin_on(
            self.service,
            project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            session_id=session_id,
            targets=targets,
            label=label,
        )

    def _begin_production(
        self, project_id: str, session_id: str, label: str
    ) -> str:
        return self._begin(
            project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id=session_id,
            targets=["第一集"],
            label=label,
        )

    def _begin_audit(
        self,
        project_id: str,
        artifact_id: str,
        session_id: str,
        label: str,
    ) -> str:
        return self._begin(
            project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id=session_id,
            targets=[artifact_id],
            label=label,
        )

    def _begin_repair(
        self, project_id: str, session_id: str, target: str, label: str
    ) -> str:
        return self._begin(
            project_id,
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            session_id=session_id,
            targets=[target],
            label=label,
        )

    def _continuation_request_on(
        self,
        service: Any,
        project_id: str,
        *,
        resume_work_order_id: str,
        new_session_id: str | None,
        action: str,
        expected_plan_sha256: str | None = None,
        confirmation: str | None = None,
    ) -> Any:
        hub = service.store.load_hub(self.namespace, project_id)
        root = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == resume_work_order_id
        )
        source_lock = root.get("source_lock") or {}
        return service.begin_task(
            self.namespace,
            project_id=project_id,
            operation=root["operation"],
            mode=root["mode"],
            authorization=root["authorization"],
            request_text=(
                "用户明确授权撤销旧窗口并由当前新窗口接续同一角色权限"
            ),
            targets=copy.deepcopy(root["targets"]),
            authoritative_source=source_lock.get(
                "authoritative_source", "已锁定项目证据链"
            ),
            latest_version=source_lock.get("latest_version", "current"),
            target_boundary=source_lock.get("target_boundary", "第一集"),
            must_keep=copy.deepcopy(source_lock.get("must_keep") or []),
            must_remove=copy.deepcopy(source_lock.get("must_remove") or []),
            protected_scope=copy.deepcopy(root.get("protected_scope") or []),
            expected_outputs=copy.deepcopy(root.get("expected_outputs") or []),
            window_key=f"continued-{new_session_id or 'missing'}",
            request_session_id=new_session_id,
            action=action,
            resume_work_order_id=resume_work_order_id,
            expected_plan_sha256=expected_plan_sha256,
            confirmation=confirmation,
        )

    def _plan_continuation(
        self,
        project_id: str,
        work_order_id: str,
        new_session_id: str | None,
    ) -> Any:
        return self._continuation_request_on(
            self.service,
            project_id,
            resume_work_order_id=work_order_id,
            new_session_id=new_session_id,
            action="plan_role_continuation",
        )

    def _execute_continuation(
        self,
        project_id: str,
        work_order_id: str,
        new_session_id: str,
        plan_sha256: str,
    ) -> Any:
        return self._continuation_request_on(
            self.service,
            project_id,
            resume_work_order_id=work_order_id,
            new_session_id=new_session_id,
            action="execute_role_continuation",
            expected_plan_sha256=plan_sha256,
            confirmation=CONTINUATION_CONFIRMATION,
        )

    def _preflight_full(
        self,
        *,
        project_id: str,
        work_order_id: str,
        session_id: str,
        storyboard: dict[str, Any],
    ) -> Any:
        for group in service_module._scene_scope_groups(storyboard):
            aliases = list(group.get("scene_aliases") or [])
            scope_id = str(
                aliases[0] if aliases else group["stable_scene_id"]
            )
            scene = self.service.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=storyboard,
                scope_kind="scene",
                scope_id=scope_id,
                request_session_id=session_id,
            )
            self.assertEqual(scene.status, service_module.DRAFT_PREFLIGHT_PASS)
        return self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            scope_kind="full",
            scope_id="EP01",
            request_session_id=session_id,
        )

    def _commit_production(
        self,
        *,
        project_id: str,
        work_order_id: str,
        session_id: str,
        label: str,
    ) -> Any:
        storyboard = valid_storyboard()
        preflight = self._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=session_id,
            storyboard=storyboard,
        )
        self.assertTrue(preflight.data["commit_ready"])
        return self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            file_name=f"{label}.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=str(preflight.trace_id),
        )

    def _failed_light_repair_case(self, label: str) -> dict[str, Any]:
        """Create one real production→failed audit→repair authority chain."""

        project_id = self._project(label)
        production_session = f"openai:{label}-production"
        audit_session = f"openai:{label}-audit"
        repair_session = f"openai:{label}-repair-old"
        production_order = self._begin_production(
            project_id, production_session, f"{label}-production"
        )
        broken = valid_storyboard()
        broken["source_lock"]["上集终点"] += f"【{label}】"
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        preflight = self._preflight_full(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            storyboard=broken,
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=production_order,
            storyboard=broken,
            file_name=f"{label}-base.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        base_artifact_id = str(committed.artifact_id)
        audit_order = self._begin_audit(
            project_id,
            base_artifact_id,
            audit_session,
            f"{label}-audit",
        )
        shot_id = str(broken["shots"][0]["镜头编号"])
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            base_artifact_id,
            shot_id=shot_id,
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认右侧窗光与锁定的左侧自然光事实冲突。",
            repair_action="仅修正首镜光源来源后返回原独立审计窗口。",
        )
        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=base_artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=str(
                committed.data["handoff"]["handoff_id"]
            ),
            semantic_audit=semantic_failure,
            request_session_id=audit_session,
        )
        repair_order = self._begin_repair(
            project_id, repair_session, shot_id, f"{label}-repair"
        )
        repaired = copy.deepcopy(broken)
        repaired["shots"][0]["光源来源"] = valid_storyboard()["shots"][0][
            "光源来源"
        ]
        return {
            "project_id": project_id,
            "repair_order": repair_order,
            "repair_session": repair_session,
            "base_artifact_id": base_artifact_id,
            "base_canonical_sha256": str(
                committed.data["canonical_sha256"]
            ),
            "base_raw_fingerprint": str(
                committed.data["artifact"]["fingerprint"]
            ),
            "audit_certificate_id": str(
                failed.data["certificate"]["certificate_id"]
            ),
            "repair_package_id": str(
                failed.data["repair_package"]["repair_package_id"]
            ),
            "shot_id": shot_id,
            "repaired_storyboard": repaired,
        }

    def _run_light_patch(
        self, case: dict[str, Any], *, session_id: str
    ) -> Any:
        return self.service.commit_storyboard_patch(
            self.namespace,
            project_id=str(case["project_id"]),
            work_order_id=str(case["repair_order"]),
            base_artifact_id=str(case["base_artifact_id"]),
            expected_canonical_hash=str(case["base_canonical_sha256"]),
            expected_raw_artifact_fingerprint=str(
                case["base_raw_fingerprint"]
            ),
            audit_certificate_id=str(case["audit_certificate_id"]),
            operations=[
                {
                    "op": "replace_field",
                    "shot_id": str(case["shot_id"]),
                    "field": "光源来源",
                    "value": case["repaired_storyboard"]["shots"][0][
                        "光源来源"
                    ],
                    "issue_ids": ["E-TEMPLATE-008"],
                    "reason": "按独立审计证据恢复锁定的左侧自然光来源",
                }
            ],
            impact_scope=[str(case["shot_id"])],
            file_name="continued-hard-crash-patch.xlsx",
            request_session_id=session_id,
        )

    def _run_full_light_repair(
        self, case: dict[str, Any], *, session_id: str
    ) -> Any:
        return self.service.commit_storyboard(
            self.namespace,
            project_id=str(case["project_id"]),
            work_order_id=str(case["repair_order"]),
            storyboard=copy.deepcopy(case["repaired_storyboard"]),
            file_name="continued-hard-crash-full.xlsx",
            source_artifact_id=str(case["base_artifact_id"]),
            request_session_id=session_id,
            repair_package_id=str(case["repair_package_id"]),
        )

    def _save_issued_repair_package(
        self,
        project_id: str,
        *,
        package_id: str,
    ) -> dict[str, Any]:
        return self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
            value={
                "repair_package_id": package_id,
                "project_id": project_id,
                "status": "issued",
                "expires_at": "2099-01-01T00:00:00Z",
                "audit_certificate_id": "CERT-v0511-failed-audit",
                "failed_artifact_id": "ART-v0511-failed",
            },
        )

    def _leave_consuming_reaudit_successor(
        self, label: str
    ) -> dict[str, Any]:
        project_id = self._project(f"reaudit-claim-{label}")
        production_session = f"openai:reaudit-claim-production-{label}"
        audit_session = f"openai:reaudit-claim-audit-{label}"
        production_order = self._begin_production(
            project_id,
            production_session,
            f"reaudit-claim-production-root-{label}",
        )
        committed = self._commit_production(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            label=f"reaudit-claim-source-{label}",
        )
        artifact_id = str(committed.artifact_id)
        root_handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            audit_session,
            f"reaudit-claim-audit-root-{label}",
        )
        clean_semantic = semantic_audit_for_artifact(
            self.service, self.settings, project_id, artifact_id
        )
        first = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            semantic_audit=clean_semantic,
            request_session_id=audit_session,
        )
        self.assertEqual(first.status, "pass")
        shot_id = str(valid_storyboard()["shots"][0]["镜头编号"])
        failed_semantic = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id=shot_id,
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].unsigned_reaudit_claim",
            evidence="复审失败后模拟进程在完成 successor claim 前丢失。",
            repair_action="保持同一独立审计权限并验证签名栅栏。",
        )
        with (
            patch.object(
                self.service.store,
                "finish_reaudit_successor",
                side_effect=RuntimeError(
                    "simulated process loss before reaudit finish"
                ),
            ),
            patch.object(
                self.service.store,
                "release_reaudit_successor_claim",
                return_value=None,
            ),
            patch.object(
                self.service.store,
                "rollback_reaudit_successor",
                return_value=None,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "before reaudit finish"
            ):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=project_id,
                    artifact_id=artifact_id,
                    audit_cycle_id=None,
                    work_order_id=audit_order,
                    handoff_artifact_id=root_handoff_id,
                    force_machine_recheck=True,
                    semantic_audit=failed_semantic,
                    request_session_id=audit_session,
                )
        successor = next(
            row
            for row in self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="handoffs"
            )
            if row.get("reaudit_root_handoff_id") == root_handoff_id
        )
        self.assertEqual(
            successor.get("reaudit_kind"),
            "independent_audit_successor",
        )
        self.assertEqual(successor.get("status"), "consuming")
        self.assertTrue(successor.get("claim_token"), successor)
        return {
            "project_id": project_id,
            "audit_work_order_id": audit_order,
            "successor": successor,
        }

    @staticmethod
    def _scene_scope_id(storyboard: dict[str, Any]) -> str:
        group = service_module._scene_scope_groups(storyboard)[0]
        aliases = list(group.get("scene_aliases") or [])
        return str(aliases[0] if aliases else group["stable_scene_id"])

    def _expire_existing_authority_operation_lease(
        self, project_id: str, authority_root_id: str
    ) -> Path:
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        lease_path = self.service.store._authority_operation_lease_path(
            project_dir, authority_root_id
        )
        with self.service.store.project_write_lock(
            self.namespace, project_id
        ):
            lease = self.service.store.read_json(lease_path)
            lease["claim_lease_expires_epoch_seconds"] = time.time() - 1
            lease["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"
            lease["integrity_hmac"] = (
                self.service.store._record_integrity_hmac(lease)
            )
            self.service.store.atomic_write_json(lease_path, lease)
        return lease_path

    def _claim_expired_authority_operation(
        self,
        project_id: str,
        work_order_id: str,
        session_id: str,
        authority_root_id: str,
    ) -> Path:
        hub = self.service.store.load_hub(self.namespace, project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == work_order_id
        )
        binding = self.service._active_authority_binding(
            self.namespace, project_id, work_order, hub=hub
        )
        self.assertEqual(binding["authority_root_id"], authority_root_id)
        self.service.store.claim_authority_operation(
            self.namespace,
            project_id,
            work_order_id=work_order_id,
            expected_authority_root_id=authority_root_id,
            expected_authority_generation=int(binding.get("generation") or 0),
            expected_session_fingerprint=self.service._session_fingerprint(
                self.namespace, session_id
            ),
            lease_seconds=30,
        )
        return self._expire_existing_authority_operation_lease(
            project_id, authority_root_id
        )

    def _repair_package_rollback_fixture(
        self,
        *,
        label: str,
        transaction_state: str,
        mismatch_field: str | None = None,
        create_audit_consumer: bool = False,
    ) -> dict[str, Any]:
        self.assertIn(transaction_state, {"prepared", "aborted", "committed"})
        project_id = self._project(f"repair-rollback-{label}")
        repair_session = f"openai:repair-rollback-{label}"
        work_order_id = self._begin_repair(
            project_id,
            repair_session,
            "EP01-SC01-SH01",
            f"repair-rollback-{label}-root",
        )
        base_hash = "c" * 64
        successor_hash = "d" * 64
        transaction_id = f"REPAIR-v0511-{label}"
        scope_hash = f"repair-scope-{label}"
        package_id = f"RPK-v0511-{label}"
        handoff_id = f"HND-v0511-{label}"
        base = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            f"base-{label}".encode("utf-8"),
            file_name=f"base-{label}.xlsx",
            artifact_type="storyboard",
            version="v1",
            work_order_id=work_order_id,
            metadata={"canonical_sha256": base_hash},
        )
        self.service.store.prepare_storyboard_successor(
            self.namespace,
            project_id,
            str(base["artifact_id"]),
            expected_canonical_hash=base_hash,
            expected_raw_fingerprint=str(base["fingerprint"]),
            transaction_id=transaction_id,
            successor_kind="repair",
            scope_hash=scope_hash,
            lease_seconds=30,
        )
        successor_metadata = {
            "canonical_sha256": successor_hash,
            "publication_state": "pending",
            "successor_transaction_id": transaction_id,
            "successor_base_artifact_id": str(base["artifact_id"]),
            "successor_kind": "repair",
            "successor_scope_hash": scope_hash,
            "repair_transaction_id": transaction_id,
            "repair_package_id": package_id,
            "repair_base_artifact_id": str(base["artifact_id"]),
            "repair_scope_hash": scope_hash,
        }
        if mismatch_field == "transaction_id":
            mismatched_transaction_id = f"REPAIR-v0511-{label}-other"
            successor_metadata["successor_transaction_id"] = (
                mismatched_transaction_id
            )
            successor_metadata["repair_transaction_id"] = (
                mismatched_transaction_id
            )
        elif mismatch_field == "package_id":
            successor_metadata["repair_package_id"] = (
                f"RPK-v0511-{label}-other"
            )
        elif mismatch_field is not None:
            raise AssertionError(f"unsupported mismatch field: {mismatch_field}")
        successor = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            f"successor-{label}".encode("utf-8"),
            file_name=f"successor-{label}.xlsx",
            artifact_type="storyboard",
            version="v2",
            work_order_id=work_order_id,
            source_artifact_id=str(base["artifact_id"]),
            metadata=successor_metadata,
        )
        audit_work_order_id: str | None = None
        if create_audit_consumer:
            audit_work_order_id = self._begin_audit(
                project_id,
                str(successor["artifact_id"]),
                f"openai:{label}-audit-consumer",
                f"{label}-audit-consumer",
            )
        handoff = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
            value={
                "handoff_id": handoff_id,
                "handoff_type": "repair_to_audit",
                "from_window_role": "repair",
                "to_window_role": "independent_audit",
                "status": "prepared",
                "publication_state": "pending",
                "work_order_id": work_order_id,
                "storyboard_artifact_id": str(successor["artifact_id"]),
                "canonical_artifact_id": str(successor["artifact_id"]),
                "canonical_sha256": successor_hash,
                "immutable_repair_package_id": package_id,
                "successor_transaction_id": transaction_id,
                "successor_base_artifact_id": str(base["artifact_id"]),
                "successor_kind": "repair",
                "successor_scope_hash": scope_hash,
            },
        )
        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (hub, _ledger):
            hub.setdefault("handoffs", []).append(
                self.service._handoff_record_projection(handoff)
            )
        self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
            value={
                "repair_package_id": package_id,
                "project_id": project_id,
                "status": "issued",
                "expires_at": "2099-01-01T00:00:00Z",
                "base_artifact_id": str(base["artifact_id"]),
                "base_canonical_sha256": base_hash,
                "audit_certificate_id": f"CERT-v0511-{label}",
            },
        )
        claimed = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=work_order_id,
            lease_seconds=30,
        )
        claim_token = str(claimed["claim_token"])
        claim_generation = int(claimed["claim_generation"])
        consumed = self.service.store.finish_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=work_order_id,
            claim_token=claim_token,
            successor_artifact_id=str(successor["artifact_id"]),
            successor_canonical_sha256=successor_hash,
            repair_to_audit_handoff_id=handoff_id,
        )
        self.assertEqual(consumed["status"], "consumed")
        if transaction_state == "aborted":
            self.service.store.abort_storyboard_successor(
                self.namespace,
                project_id,
                str(base["artifact_id"]),
                transaction_id=transaction_id,
                reason="fixture abort before publication",
            )
            self.service._set_successor_candidate_publication(
                self.namespace,
                project_id,
                work_order_id=work_order_id,
                artifact_id=str(successor["artifact_id"]),
                canonical_artifact_id=str(successor["artifact_id"]),
                handoff_id=handoff_id,
                transaction_id=transaction_id,
                state="aborted",
                reason="fixture abort before package rollback",
            )
        elif transaction_state == "committed":
            self.service.store.commit_storyboard_successor(
                self.namespace,
                project_id,
                str(base["artifact_id"]),
                transaction_id=transaction_id,
                successor_artifact_id=str(successor["artifact_id"]),
                successor_canonical_hash=successor_hash,
            )
        return {
            "project_id": project_id,
            "work_order_id": work_order_id,
            "package_id": package_id,
            "claim_token": claim_token,
            "claim_generation": claim_generation,
            "base_artifact_id": str(base["artifact_id"]),
            "successor_artifact_id": str(successor["artifact_id"]),
            "successor_hash": successor_hash,
            "transaction_id": transaction_id,
            "transaction_state": transaction_state,
            "handoff_id": handoff_id,
            "audit_work_order_id": audit_work_order_id,
        }

    def test_committed_repair_successor_keeps_package_consumed_on_rollback(
        self,
    ) -> None:
        fixture = self._repair_package_rollback_fixture(
            label="committed-terminal",
            transaction_state="committed",
        )
        project_id = str(fixture["project_id"])
        before_package = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=str(fixture["package_id"]),
        )
        before_base, _path = self.service.store.get_artifact(
            self.namespace,
            project_id,
            str(fixture["base_artifact_id"]),
        )
        try:
            result = self.service.store.rollback_consumed_repair_package(
                self.namespace,
                project_id,
                str(fixture["package_id"]),
                work_order_id=str(fixture["work_order_id"]),
                claim_token=str(fixture["claim_token"]),
                claim_generation=int(fixture["claim_generation"]),
                base_artifact_id=str(fixture["base_artifact_id"]),
                transaction_id=str(fixture["transaction_id"]),
                expected_successor_artifact_id=str(
                    fixture["successor_artifact_id"]
                ),
                expected_successor_canonical_sha256=str(
                    fixture["successor_hash"]
                ),
                repair_to_audit_handoff_id=str(fixture["handoff_id"]),
            )
        except ValueError as exc:
            self.assertRegex(
                str(exc),
                "repair_package_.*(?:committed|rollback_forbidden)|"
                "successor_transaction_committed",
            )
            result = None
        after_package = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=str(fixture["package_id"]),
        )
        after_base, _path = self.service.store.get_artifact(
            self.namespace,
            project_id,
            str(fixture["base_artifact_id"]),
        )
        self.assertEqual(after_package["status"], "consumed")
        self.assertEqual(
            after_package["successor_artifact_id"],
            fixture["successor_artifact_id"],
        )
        self.assertEqual(
            after_package["successor_canonical_sha256"],
            fixture["successor_hash"],
        )
        self.assertEqual(
            after_package["consumption_claim"],
            before_package["consumption_claim"],
        )
        self.assertEqual(
            after_base["metadata"]["successor_transaction"]["state"],
            "committed",
        )
        self.assertEqual(
            after_base["metadata"]["successor_artifact_id"],
            fixture["successor_artifact_id"],
        )
        self.assertEqual(after_base, before_base)
        if result is not None:
            self.assertEqual(result["status"], "consumed")

    def test_delayed_successor_publication_preserves_claimed_handoff_bytes(
        self,
    ) -> None:
        for lifecycle in ("consuming", "consumed"):
            with self.subTest(lifecycle=lifecycle):
                fixture = self._repair_package_rollback_fixture(
                    label=f"delayed-publication-{lifecycle}",
                    transaction_state="committed",
                    create_audit_consumer=True,
                )
                project_id = str(fixture["project_id"])
                handoff_id = str(fixture["handoff_id"])
                audit_order = str(fixture["audit_work_order_id"])
                claimed = self.service.store.claim_handoff(
                    self.namespace,
                    project_id,
                    handoff_id,
                    consumer_work_order_id=audit_order,
                    expected_to_role="independent_audit",
                    expected_types={"repair_to_audit"},
                    lease_seconds=30,
                )
                if lifecycle == "consumed":
                    self.service.store.save_project_record(
                        self.namespace,
                        project_id,
                        kind="certificates",
                        record_id="CERT-delayed-publication-consumed",
                        value={
                            "certificate_id": (
                                "CERT-delayed-publication-consumed"
                            ),
                            "audit_class": "independent_audit",
                            "handoff_id": handoff_id,
                            "work_order_id": audit_order,
                            "artifact_id": str(
                                fixture["successor_artifact_id"]
                            ),
                        },
                    )
                    claimed = self.service.store.finish_handoff(
                        self.namespace,
                        project_id,
                        handoff_id,
                        consumer_work_order_id=audit_order,
                        audit_certificate_id=(
                            "CERT-delayed-publication-consumed"
                        ),
                        claim_token=str(claimed["claim_token"]),
                    )
                handoff_path = (
                    self.service.store.project_dir(
                        self.namespace, project_id
                    )
                    / "handoffs"
                    / f"{handoff_id}.json"
                )
                before_bytes = handoff_path.read_bytes()
                before_record = self.service.store.read_json(handoff_path)

                finalized = (
                    self.service._set_successor_candidate_publication(
                        self.namespace,
                        project_id,
                        work_order_id=str(fixture["work_order_id"]),
                        artifact_id=str(
                            fixture["successor_artifact_id"]
                        ),
                        canonical_artifact_id=str(
                            fixture["successor_artifact_id"]
                        ),
                        handoff_id=handoff_id,
                        transaction_id=str(fixture["transaction_id"]),
                        state="committed",
                    )
                )

                self.assertEqual(handoff_path.read_bytes(), before_bytes)
                after_record = self.service.store.read_json(handoff_path)
                self.assertEqual(after_record, before_record)
                self.assertEqual(
                    after_record["integrity_hmac"],
                    before_record["integrity_hmac"],
                )
                if lifecycle == "consuming":
                    self.assertEqual(
                        after_record["claim_token"],
                        before_record["claim_token"],
                    )
                    self.assertEqual(
                        after_record["claimed_by_work_order_id"],
                        audit_order,
                    )
                else:
                    self.assertEqual(after_record["status"], "consumed")
                    self.assertEqual(
                        after_record["audit_certificate_id"],
                        "CERT-delayed-publication-consumed",
                    )
                self.assertEqual(finalized["handoff"], before_record)
                hub = self.service.store.load_hub(
                    self.namespace, project_id
                )
                hub_handoff = next(
                    row
                    for row in hub["handoffs"]
                    if row.get("handoff_id") == handoff_id
                )
                self.assertEqual(
                    hub_handoff,
                    self.service._handoff_record_projection(before_record),
                )
                candidate, _path = self.service.store.get_artifact(
                    self.namespace,
                    project_id,
                    str(fixture["successor_artifact_id"]),
                )
                self.assertEqual(
                    candidate["metadata"]["publication_state"],
                    "committed",
                )

    def test_repair_package_rollback_requires_exact_precommit_identity(
        self,
    ) -> None:
        aborted = self._repair_package_rollback_fixture(
            label="aborted-exact",
            transaction_state="aborted",
        )
        restored = self.service.store.rollback_consumed_repair_package(
            self.namespace,
            str(aborted["project_id"]),
            str(aborted["package_id"]),
            work_order_id=str(aborted["work_order_id"]),
            claim_token=str(aborted["claim_token"]),
            claim_generation=int(aborted["claim_generation"]),
            base_artifact_id=str(aborted["base_artifact_id"]),
            transaction_id=str(aborted["transaction_id"]),
            expected_successor_artifact_id=str(
                aborted["successor_artifact_id"]
            ),
            expected_successor_canonical_sha256=str(
                aborted["successor_hash"]
            ),
            repair_to_audit_handoff_id=str(aborted["handoff_id"]),
        )
        self.assertEqual(restored["status"], "issued")
        self.assertNotIn("successor_artifact_id", restored)
        base, _path = self.service.store.get_artifact(
            self.namespace,
            str(aborted["project_id"]),
            str(aborted["base_artifact_id"]),
        )
        self.assertEqual(
            base["metadata"]["successor_transaction"]["state"],
            "aborted",
        )

        prepared = self._repair_package_rollback_fixture(
            label="prepared-exact",
            transaction_state="prepared",
        )
        prepared_project_id = str(prepared["project_id"])
        prepared_before = self._project_tree_bytes(prepared_project_id)
        with self.assertRaisesRegex(
            ValueError,
            "repair_package_rollback_forbidden.*(?:aborted|rollback-safe)",
        ):
            self.service.store.rollback_consumed_repair_package(
                self.namespace,
                prepared_project_id,
                str(prepared["package_id"]),
                work_order_id=str(prepared["work_order_id"]),
                claim_token=str(prepared["claim_token"]),
                claim_generation=int(prepared["claim_generation"]),
                base_artifact_id=str(prepared["base_artifact_id"]),
                transaction_id=str(prepared["transaction_id"]),
                expected_successor_artifact_id=str(
                    prepared["successor_artifact_id"]
                ),
                expected_successor_canonical_sha256=str(
                    prepared["successor_hash"]
                ),
                repair_to_audit_handoff_id=str(prepared["handoff_id"]),
            )
        prepared_after = self.service.store.get_project_record(
            self.namespace,
            prepared_project_id,
            kind="repair_packages",
            record_id=str(prepared["package_id"]),
        )
        self.assertEqual(prepared_after["status"], "consumed")
        self.assertEqual(
            prepared_after["successor_artifact_id"],
            prepared["successor_artifact_id"],
        )
        self.assertEqual(
            self._project_tree_bytes(prepared_project_id), prepared_before
        )

        for state, mismatch_field in (
            ("prepared", "transaction_id"),
            ("aborted", "package_id"),
        ):
            with self.subTest(mismatch_state=state, field=mismatch_field):
                fixture = self._repair_package_rollback_fixture(
                    label=f"{state}-mismatch",
                    transaction_state=state,
                    mismatch_field=mismatch_field,
                )
                project_id = str(fixture["project_id"])
                before = self._project_tree_bytes(project_id)
                try:
                    result = self.service.store.rollback_consumed_repair_package(
                        self.namespace,
                        project_id,
                        str(fixture["package_id"]),
                        work_order_id=str(fixture["work_order_id"]),
                        claim_token=str(fixture["claim_token"]),
                        claim_generation=int(fixture["claim_generation"]),
                        base_artifact_id=str(fixture["base_artifact_id"]),
                        transaction_id=str(fixture["transaction_id"]),
                        expected_successor_artifact_id=str(
                            fixture["successor_artifact_id"]
                        ),
                        expected_successor_canonical_sha256=str(
                            fixture["successor_hash"]
                        ),
                        repair_to_audit_handoff_id=str(
                            fixture["handoff_id"]
                        ),
                    )
                except ValueError as exc:
                    self.assertRegex(
                        str(exc),
                        "repair_package_.*(?:identity|binding|rollback)|"
                        "successor_transaction_.*(?:identity|conflict)",
                    )
                    result = None
                after = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=str(fixture["package_id"]),
                )
                self.assertEqual(after["status"], "consumed")
                self.assertEqual(
                    after["successor_artifact_id"],
                    fixture["successor_artifact_id"],
                )
                self.assertEqual(self._project_tree_bytes(project_id), before)
                if result is not None:
                    self.assertEqual(result["status"], "consumed")

    def test_repair_package_rollback_rejects_missing_or_claimed_handoff_zero_write(
        self,
    ) -> None:
        for variant in ("missing", "claimed"):
            with self.subTest(variant=variant):
                fixture = self._repair_package_rollback_fixture(
                    label=f"handoff-{variant}",
                    transaction_state="aborted",
                )
                project_id = str(fixture["project_id"])
                handoff_path = (
                    self.service.store.project_dir(
                        self.namespace, project_id
                    )
                    / "handoffs"
                    / f"{fixture['handoff_id']}.json"
                )
                if variant == "missing":
                    handoff_path.unlink()
                else:
                    handoff = self.service.store.get_project_record(
                        self.namespace,
                        project_id,
                        kind="handoffs",
                        record_id=str(fixture["handoff_id"]),
                    )
                    handoff["claimed_by_work_order_id"] = "WO-live-auditor"
                    handoff["claim_token"] = "live-audit-claim-token"
                    handoff = self.service.store.save_project_record(
                        self.namespace,
                        project_id,
                        kind="handoffs",
                        record_id=str(fixture["handoff_id"]),
                        value=handoff,
                    )
                    with self.service.store.mutate_project(
                        self.namespace, project_id
                    ) as (hub, _ledger):
                        for index, row in enumerate(
                            hub.setdefault("handoffs", [])
                        ):
                            if row.get("handoff_id") == fixture["handoff_id"]:
                                hub["handoffs"][index] = (
                                    self.service._handoff_record_projection(
                                        handoff
                                    )
                                )
                                break

                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError,
                    "repair_package_rollback_(?:identity_mismatch|forbidden)",
                ):
                    self.service.store.rollback_consumed_repair_package(
                        self.namespace,
                        project_id,
                        str(fixture["package_id"]),
                        work_order_id=str(fixture["work_order_id"]),
                        claim_token=str(fixture["claim_token"]),
                        claim_generation=int(fixture["claim_generation"]),
                        base_artifact_id=str(fixture["base_artifact_id"]),
                        transaction_id=str(fixture["transaction_id"]),
                        expected_successor_artifact_id=str(
                            fixture["successor_artifact_id"]
                        ),
                        expected_successor_canonical_sha256=str(
                            fixture["successor_hash"]
                        ),
                        repair_to_audit_handoff_id=str(
                            fixture["handoff_id"]
                        ),
                    )
                package = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=str(fixture["package_id"]),
                )
                self.assertEqual(package["status"], "consumed")
                self.assertEqual(
                    package["successor_artifact_id"],
                    fixture["successor_artifact_id"],
                )
                self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_patch_rolls_forward_after_committed_successor_response_is_lost(
        self,
    ) -> None:
        project_id = self._project("patch-lost-committed-response")
        production_session = "openai:patch-lost-response-production"
        audit_session = "openai:patch-lost-response-audit"
        repair_session = "openai:patch-lost-response-repair"
        production_order = self._begin_production(
            project_id,
            production_session,
            "patch-lost-response-production",
        )
        broken = valid_storyboard()
        broken["source_lock"]["上集终点"] += "【lost-response】"
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        preflight = self._preflight_full(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            storyboard=broken,
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=production_order,
            storyboard=broken,
            file_name="patch-lost-response-base.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        self.assertEqual(
            committed.status, service_module.PRODUCTION_PREFLIGHT_PASS
        )
        base_artifact_id = str(committed.artifact_id)
        production_handoff_id = str(
            committed.data["handoff"]["handoff_id"]
        )
        audit_order = self._begin_audit(
            project_id,
            base_artifact_id,
            audit_session,
            "patch-lost-response-audit",
        )
        shot_id = str(broken["shots"][0]["镜头编号"])
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            base_artifact_id,
            shot_id=shot_id,
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认右侧窗光与锁定的左侧自然光事实冲突。",
            repair_action="仅修正首镜光源来源后返回原独立审计窗口。",
        )
        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=base_artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=production_handoff_id,
            semantic_audit=semantic_failure,
            request_session_id=audit_session,
        )
        self.assertEqual(failed.status, "fail_current")
        package_id = str(
            failed.data["repair_package"]["repair_package_id"]
        )
        repair_order = self._begin_repair(
            project_id,
            repair_session,
            shot_id,
            "patch-lost-response-repair",
        )

        original_commit = self.service.store.commit_storyboard_successor
        original_abort = self.service.store.abort_storyboard_successor
        original_publication = self.service._set_successor_candidate_publication
        durable_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        aborted_publication_calls: list[dict[str, Any]] = []

        def commit_then_lose_first_response(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            result = original_commit(*args, **kwargs)
            durable_calls.append((args, copy.deepcopy(kwargs)))
            if len(durable_calls) == 1:
                raise RuntimeError(
                    "simulated lost committed successor response"
                )
            return result

        def record_publication(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            if kwargs.get("state") == "aborted":
                aborted_publication_calls.append(copy.deepcopy(kwargs))
            return original_publication(*args, **kwargs)

        with (
            patch.object(
                self.service.store,
                "commit_storyboard_successor",
                side_effect=commit_then_lose_first_response,
            ) as commit_mock,
            patch.object(
                self.service.store,
                "abort_storyboard_successor",
                wraps=original_abort,
            ) as abort_mock,
            patch.object(
                self.service,
                "_set_successor_candidate_publication",
                side_effect=record_publication,
            ),
        ):
            repaired = self.service.commit_storyboard_patch(
                self.namespace,
                project_id=project_id,
                work_order_id=repair_order,
                base_artifact_id=base_artifact_id,
                expected_canonical_hash=str(
                    committed.data["canonical_sha256"]
                ),
                expected_raw_artifact_fingerprint=str(
                    committed.data["artifact"]["fingerprint"]
                ),
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
                file_name="patch-lost-response-repaired.xlsx",
                request_session_id=repair_session,
            )

        self.assertEqual(
            repaired.status, service_module.PRODUCTION_PREFLIGHT_PASS
        )
        self.assertEqual(commit_mock.call_count, 2)
        self.assertEqual(durable_calls[0], durable_calls[1])
        abort_mock.assert_not_called()
        self.assertEqual(aborted_publication_calls, [])
        package = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )
        self.assertEqual(package["status"], "consumed")
        self.assertEqual(
            package["successor_artifact_id"], str(repaired.artifact_id)
        )
        base, _path = self.service.store.get_artifact(
            self.namespace, project_id, base_artifact_id
        )
        self.assertEqual(
            base["metadata"]["successor_transaction"]["state"],
            "committed",
        )
        self.assertEqual(
            base["metadata"]["successor_artifact_id"],
            str(repaired.artifact_id),
        )
        repair_handoff_id = str(
            repaired.data.get("handoff_artifact_id")
            or repaired.data["handoff"]["handoff_id"]
        )
        repair_handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=repair_handoff_id,
        )
        self.assertEqual(repair_handoff["status"], "prepared")
        self.assertEqual(repair_handoff["publication_state"], "committed")

    def test_patch_and_full_repair_continue_consumed_prepared_successor(
        self,
    ) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        for repair_kind in ("patch", "full"):
            with self.subTest(repair_kind=repair_kind):
                case = self._failed_light_repair_case(
                    f"consumed-prepared-{repair_kind}"
                )
                old_session = str(case["repair_session"])
                new_session = f"openai:consumed-prepared-{repair_kind}-new"
                runner = (
                    self._run_light_patch
                    if repair_kind == "patch"
                    else self._run_full_light_repair
                )
                with patch.object(
                    self.service.store,
                    "commit_storyboard_successor",
                    side_effect=SimulatedHardCrash(
                        "crash after package consume before successor CAS"
                    ),
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        runner(case, session_id=old_session)

                package = self.service.store.get_project_record(
                    self.namespace,
                    str(case["project_id"]),
                    kind="repair_packages",
                    record_id=str(case["repair_package_id"]),
                )
                self.assertEqual(package["status"], "consumed")
                base, _base_path = self.service.store.get_artifact(
                    self.namespace,
                    str(case["project_id"]),
                    str(case["base_artifact_id"]),
                )
                self.assertEqual(
                    base["metadata"]["successor_transaction"]["state"],
                    "prepared",
                )

                plan = self._plan_continuation(
                    str(case["project_id"]),
                    str(case["repair_order"]),
                    new_session,
                )
                continued = self._execute_continuation(
                    str(case["project_id"]),
                    str(case["repair_order"]),
                    new_session,
                    str(plan.data["plan_sha256"]),
                )
                self.assertEqual(continued.status, "continued")

                mismatched_case = copy.deepcopy(case)
                if repair_kind == "patch":
                    mismatched_case["repaired_storyboard"]["shots"][0][
                        "光源来源"
                    ] += "，但请求摘要不同"
                else:
                    mismatched_case["repaired_storyboard"]["source_lock"][
                        "下一集起点"
                    ] += "【非同一完整修复请求】"
                before_mismatch = self._project_tree_bytes(
                    str(case["project_id"])
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "repair_successor_recovery_invalid: base transaction differs",
                ):
                    runner(mismatched_case, session_id=new_session)
                self.assertEqual(
                    self._project_tree_bytes(str(case["project_id"])),
                    before_mismatch,
                )

                if repair_kind == "patch":
                    project_dir = self.service.store.project_dir(
                        self.namespace, str(case["project_id"])
                    )
                    hub_path = project_dir / "hub.json"
                    original_hub = self.service.store.read_json(hub_path)
                    tampered_hub = copy.deepcopy(original_hub)
                    package_now = self.service.store.get_project_record(
                        self.namespace,
                        str(case["project_id"]),
                        kind="repair_packages",
                        record_id=str(case["repair_package_id"]),
                    )
                    handoff_id = str(
                        package_now["repair_to_audit_handoff_id"]
                    )
                    hub_handoff = next(
                        row
                        for row in tampered_hub["handoffs"]
                        if row.get("handoff_id") == handoff_id
                    )
                    hub_handoff["successor_scope_hash"] = "f" * 64
                    self.service.store.atomic_write_json(
                        hub_path, tampered_hub
                    )
                    before_tampered = self._project_tree_bytes(
                        str(case["project_id"])
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "repair_successor_recovery_invalid: Hub candidate projection differs",
                    ):
                        runner(case, session_id=new_session)
                    self.assertEqual(
                        self._project_tree_bytes(str(case["project_id"])),
                        before_tampered,
                    )
                    self.service.store.atomic_write_json(
                        hub_path, original_hub
                    )
                else:
                    package_now = self.service.store.get_project_record(
                        self.namespace,
                        str(case["project_id"]),
                        kind="repair_packages",
                        record_id=str(case["repair_package_id"]),
                    )
                    handoff = self.service.store.get_project_record(
                        self.namespace,
                        str(case["project_id"]),
                        kind="handoffs",
                        record_id=str(
                            package_now["repair_to_audit_handoff_id"]
                        ),
                    )
                    certificate_id = str(handoff["certificate_id"])
                    certificate_path = (
                        self.service.store.project_dir(
                            self.namespace, str(case["project_id"])
                        )
                        / "certificates"
                        / f"{certificate_id}.json"
                    )
                    original_certificate = self.service.store.read_json(
                        certificate_path
                    )
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        str(case["project_id"]),
                        kind="certificates",
                        record_id=certificate_id,
                    ) as mutable_certificate:
                        mutable_certificate["preflight_issue_count"] = None
                    before_tampered = self._project_tree_bytes(
                        str(case["project_id"])
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "repair_successor_recovery_invalid: successor production certificate differs",
                    ):
                        runner(case, session_id=new_session)
                    self.assertEqual(
                        self._project_tree_bytes(str(case["project_id"])),
                        before_tampered,
                    )
                    self.service.store.atomic_write_json(
                        certificate_path, original_certificate
                    )

                before_old = self._project_tree_bytes(
                    str(case["project_id"])
                )
                with self.assertRaisesRegex(
                    ValueError, "window_session_mismatch"
                ):
                    runner(case, session_id=old_session)
                self.assertEqual(
                    self._project_tree_bytes(str(case["project_id"])),
                    before_old,
                )

                recovered = runner(case, session_id=new_session)
                self.assertEqual(
                    recovered.status,
                    service_module.PRODUCTION_PREFLIGHT_PASS,
                )
                self.assertTrue(
                    recovered.data["recovered_consumed_successor"]
                )
                recovered_artifact_id = str(recovered.artifact_id)
                package_after = self.service.store.get_project_record(
                    self.namespace,
                    str(case["project_id"]),
                    kind="repair_packages",
                    record_id=str(case["repair_package_id"]),
                )
                self.assertEqual(package_after["status"], "consumed")
                base_after, _base_path = self.service.store.get_artifact(
                    self.namespace,
                    str(case["project_id"]),
                    str(case["base_artifact_id"]),
                )
                self.assertEqual(
                    base_after["metadata"]["successor_transaction"][
                        "state"
                    ],
                    "committed",
                )
                self.assertEqual(
                    base_after["metadata"]["successor_artifact_id"],
                    recovered_artifact_id,
                )

                replay = runner(case, session_id=new_session)
                self.assertEqual(str(replay.artifact_id), recovered_artifact_id)
                self.assertTrue(replay.data["idempotent_replay"])

    def test_patch_and_full_repair_continue_expired_consuming_claim(
        self,
    ) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        for repair_kind in ("patch", "full"):
            with self.subTest(repair_kind=repair_kind):
                case = self._failed_light_repair_case(
                    f"expired-consuming-{repair_kind}"
                )
                old_session = str(case["repair_session"])
                new_session = f"openai:expired-consuming-{repair_kind}-new"
                runner = (
                    self._run_light_patch
                    if repair_kind == "patch"
                    else self._run_full_light_repair
                )
                with patch.object(
                    self.service.store,
                    "prepare_storyboard_successor",
                    side_effect=SimulatedHardCrash(
                        "crash after repair claim before successor prepare"
                    ),
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        runner(case, session_id=old_session)

                package_id = str(case["repair_package_id"])
                package = self.service.store.get_project_record(
                    self.namespace,
                    str(case["project_id"]),
                    kind="repair_packages",
                    record_id=package_id,
                )
                self.assertEqual(package["status"], "consuming")
                old_token = str(package["claim_token"])
                old_generation = int(package["claim_generation"])
                with self.service.store.mutate_project_record(
                    self.namespace,
                    str(case["project_id"]),
                    kind="repair_packages",
                    record_id=package_id,
                ) as mutable_package:
                    mutable_package[
                        "claim_lease_expires_epoch_seconds"
                    ] = time.time() - 1
                    mutable_package["claim_lease_expires_at"] = (
                        "2000-01-01T00:00:00Z"
                    )

                plan = self._plan_continuation(
                    str(case["project_id"]),
                    str(case["repair_order"]),
                    new_session,
                )
                continued = self._execute_continuation(
                    str(case["project_id"]),
                    str(case["repair_order"]),
                    new_session,
                    str(plan.data["plan_sha256"]),
                )
                self.assertEqual(continued.status, "continued")

                repaired = runner(case, session_id=new_session)
                self.assertEqual(
                    repaired.status,
                    service_module.PRODUCTION_PREFLIGHT_PASS,
                )
                package_after = self.service.store.get_project_record(
                    self.namespace,
                    str(case["project_id"]),
                    kind="repair_packages",
                    record_id=package_id,
                )
                self.assertEqual(package_after["status"], "consumed")
                self.assertGreater(
                    int(package_after["claim_generation"]), old_generation
                )
                self.assertEqual(
                    package_after["claim_history"][-1]["claim_token"],
                    old_token,
                )

    def test_repair_service_rejects_unrecoverable_consuming_claims(
        self,
    ) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        for variant, repair_kind in (
            ("live", "patch"),
            ("other-work-order", "full"),
            ("forged-lineage", "patch"),
        ):
            with self.subTest(variant=variant, repair_kind=repair_kind):
                case = self._failed_light_repair_case(
                    f"unrecoverable-consuming-{variant}"
                )
                old_session = str(case["repair_session"])
                new_session = f"openai:unrecoverable-{variant}-new"
                runner = (
                    self._run_light_patch
                    if repair_kind == "patch"
                    else self._run_full_light_repair
                )
                with patch.object(
                    self.service.store,
                    "prepare_storyboard_successor",
                    side_effect=SimulatedHardCrash(
                        "crash after repair claim before successor prepare"
                    ),
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        runner(case, session_id=old_session)

                project_id = str(case["project_id"])
                package_id = str(case["repair_package_id"])
                if variant == "live":
                    before = self._project_tree_bytes(project_id)
                    with self.assertRaisesRegex(
                        ValueError,
                        "repair_package_unavailable: package has no recoverable expired claim",
                    ):
                        runner(case, session_id=old_session)
                    self.assertEqual(
                        self._project_tree_bytes(project_id), before
                    )
                    continue

                with self.service.store.mutate_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=package_id,
                ) as mutable_package:
                    mutable_package[
                        "claim_lease_expires_epoch_seconds"
                    ] = time.time() - 1
                    mutable_package["claim_lease_expires_at"] = (
                        "2000-01-01T00:00:00Z"
                    )
                plan = self._plan_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                )
                continued = self._execute_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                    str(plan.data["plan_sha256"]),
                )
                self.assertEqual(continued.status, "continued")

                with self.service.store.mutate_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=package_id,
                ) as mutable_package:
                    if variant == "other-work-order":
                        mutable_package["consumed_by_work_order_id"] = (
                            "WO-unrelated-repair-authority"
                        )
                    else:
                        mutable_package[
                            "claim_authority_session_fingerprint"
                        ] = "f" * 64

                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError,
                    "repair_package_unavailable: package has no recoverable expired claim",
                ):
                    runner(case, session_id=new_session)
                self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_patch_and_full_repair_compensate_consumed_aborted_successor(
        self,
    ) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        for repair_kind in ("patch", "full"):
            with self.subTest(repair_kind=repair_kind):
                case = self._failed_light_repair_case(
                    f"consumed-aborted-{repair_kind}"
                )
                old_session = str(case["repair_session"])
                new_session = f"openai:consumed-aborted-{repair_kind}-new"
                runner = (
                    self._run_light_patch
                    if repair_kind == "patch"
                    else self._run_full_light_repair
                )
                with patch.object(
                    self.service.store,
                    "commit_storyboard_successor",
                    side_effect=SimulatedHardCrash(
                        "crash after consume before successor CAS"
                    ),
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        runner(case, session_id=old_session)

                project_id = str(case["project_id"])
                package = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=str(case["repair_package_id"]),
                )
                original_handoff_id = str(
                    package["repair_to_audit_handoff_id"]
                )
                base, _base_path = self.service.store.get_artifact(
                    self.namespace,
                    project_id,
                    str(case["base_artifact_id"]),
                )
                transaction_id = str(
                    base["metadata"]["successor_transaction"][
                        "transaction_id"
                    ]
                )
                self.service.store.abort_storyboard_successor(
                    self.namespace,
                    project_id,
                    str(case["base_artifact_id"]),
                    transaction_id=transaction_id,
                    reason="simulated crash after durable base abort",
                )

                plan = self._plan_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                )
                continued = self._execute_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                    str(plan.data["plan_sha256"]),
                )
                self.assertEqual(continued.status, "continued")
                with self.assertRaisesRegex(
                    ValueError, "repair_successor_recovery_compensated"
                ):
                    runner(case, session_id=new_session)
                original_handoff = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="handoffs",
                    record_id=original_handoff_id,
                )
                self.assertEqual(original_handoff["status"], "rejected")
                self.assertEqual(
                    original_handoff["publication_state"], "aborted"
                )
                final_package = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=str(case["repair_package_id"]),
                )
                self.assertEqual(final_package["status"], "issued")
                self.assertEqual(
                    final_package["claim_history"][-1]["state"],
                    "rolled_back_after_successor_cas_failure",
                )
                self.assertNotIn("successor_artifact_id", final_package)
                before_retry = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError, "repair_successor_recovery_compensated"
                ):
                    runner(case, session_id=new_session)
                self.assertEqual(
                    self._project_tree_bytes(project_id), before_retry
                )

    def test_patch_and_full_repair_terminal_candidate_compensates_once(
        self,
    ) -> None:
        class SimulatedHardCrash(BaseException):
            pass

        for repair_kind in ("patch", "full"):
            with self.subTest(repair_kind=repair_kind):
                case = self._failed_light_repair_case(
                    f"terminal-consumed-{repair_kind}"
                )
                old_session = str(case["repair_session"])
                new_session = f"openai:terminal-consumed-{repair_kind}-new"
                runner = (
                    self._run_light_patch
                    if repair_kind == "patch"
                    else self._run_full_light_repair
                )
                with patch.object(
                    self.service.store,
                    "commit_storyboard_successor",
                    side_effect=SimulatedHardCrash(
                        "crash after package consume before successor CAS"
                    ),
                ):
                    with self.assertRaises(SimulatedHardCrash):
                        runner(case, session_id=old_session)

                project_id = str(case["project_id"])
                package_id = str(case["repair_package_id"])
                package = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=package_id,
                )
                successor_id = str(package["successor_artifact_id"])
                handoff_id = str(package["repair_to_audit_handoff_id"])
                handoff = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="handoffs",
                    record_id=handoff_id,
                )
                canonical_id = str(handoff["canonical_artifact_id"])
                base, _base_path = self.service.store.get_artifact(
                    self.namespace,
                    project_id,
                    str(case["base_artifact_id"]),
                )
                transaction_id = str(
                    base["metadata"]["successor_transaction"][
                        "transaction_id"
                    ]
                )
                self.service.store.abort_storyboard_successor(
                    self.namespace,
                    project_id,
                    str(case["base_artifact_id"]),
                    transaction_id=transaction_id,
                    reason="simulated durable cleanup before hard crash",
                )
                self.service._set_successor_candidate_publication(
                    self.namespace,
                    project_id,
                    work_order_id=str(case["repair_order"]),
                    artifact_id=successor_id,
                    canonical_artifact_id=canonical_id,
                    handoff_id=handoff_id,
                    transaction_id=transaction_id,
                    state="aborted",
                    reason="simulated durable cleanup before hard crash",
                )

                plan = self._plan_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                )
                continued = self._execute_continuation(
                    project_id,
                    str(case["repair_order"]),
                    new_session,
                    str(plan.data["plan_sha256"]),
                )
                self.assertEqual(continued.status, "continued")

                with self.assertRaisesRegex(
                    ValueError, "repair_successor_recovery_compensated"
                ):
                    runner(case, session_id=new_session)
                restored = self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="repair_packages",
                    record_id=package_id,
                )
                self.assertEqual(restored["status"], "issued")
                self.assertEqual(
                    restored["claim_history"][-1]["state"],
                    "rolled_back_after_successor_cas_failure",
                )
                self.assertEqual(
                    restored["claim_history"][-1][
                        "consumed_by_work_order_id"
                    ],
                    str(case["repair_order"]),
                )

                before_retry = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError, "repair_successor_recovery_compensated"
                ):
                    runner(case, session_id=new_session)
                self.assertEqual(
                    self._project_tree_bytes(project_id), before_retry
                )

    def test_live_draft_commit_claim_blocks_plan_and_execute_zero_write(
        self,
    ) -> None:
        project_id = self._project("live-draft-commit-claim")
        old_session = "openai:dpf-live-old"
        new_session = "openai:dpf-live-new"
        work_order_id = self._begin_production(
            project_id, old_session, "dpf-live-production-root"
        )
        storyboard = valid_storyboard()
        full = self._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=old_session,
            storyboard=storyboard,
        )
        self.assertTrue(full.data["commit_ready"])
        preclaim_plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        claim = self.service.store.claim_draft_preflight_commit(
            self.namespace,
            project_id,
            str(full.trace_id),
            request_sha256="a" * 64,
            lease_seconds=30,
        )
        self.assertEqual(claim["claim_status"], "claimed")
        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "role_continuation_claim_in_progress"
        ):
            self._plan_continuation(
                project_id, work_order_id, new_session
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)
        with self.assertRaisesRegex(
            ValueError, "role_continuation_claim_in_progress"
        ):
            self._execute_continuation(
                project_id,
                work_order_id,
                new_session,
                str(preclaim_plan.data["plan_sha256"]),
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_expired_draft_claim_requires_new_full_preflight_after_rotation(
        self,
    ) -> None:
        project_id = self._project("expired-draft-commit-claim")
        old_session = "openai:dpf-expired-old"
        new_session = "openai:dpf-expired-new"
        work_order_id = self._begin_production(
            project_id, old_session, "dpf-expired-production-root"
        )
        storyboard = valid_storyboard()
        old_full = self._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=old_session,
            storyboard=storyboard,
        )
        old_preflight_id = str(old_full.trace_id)
        request_sha256 = "b" * 64
        old_claim = self.service.store.claim_draft_preflight_commit(
            self.namespace,
            project_id,
            old_preflight_id,
            request_sha256=request_sha256,
            lease_seconds=1,
        )
        old_token = str(old_claim["claim_token"])
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=old_preflight_id,
        ) as record:
            record["commit_claim"]["lease_expires_epoch_seconds"] = (
                time.time() - 1
            )
            record["commit_claim"]["lease_expires_at"] = (
                "2000-01-01T00:00:00Z"
            )

        plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        continued = self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        before_stale_token = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "draft_preflight_commit_(?:authority_revoked|"
            "claim_(?:owner_mismatch|revoked|stale))",
        ):
            self.service.store.complete_draft_preflight_commit(
                self.namespace,
                project_id,
                old_preflight_id,
                claim_token=old_token,
                consumed_by_commit={
                    "artifact_id": "ART-stale-dpf-claim",
                    "canonical_json_artifact_id": "ART-stale-dpf-json",
                    "request_sha256": request_sha256,
                },
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_stale_token
        )
        with self.assertRaisesRegex(
            ValueError,
            "draft_preflight_commit_(?:authority_revoked|"
            "claim_(?:owner_mismatch|revoked|stale))",
        ):
            self.service.store.release_draft_preflight_commit(
                self.namespace,
                project_id,
                old_preflight_id,
                claim_token=old_token,
                reason="stale old window must not release",
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_stale_token
        )
        with self.assertRaisesRegex(
            ValueError,
            "draft_preflight_commit_(?:authority_revoked|"
            "claim_(?:revoked|stale))|"
            "draft_preflight_session_(?:mismatch|revoked)",
        ):
            self.service.store.claim_draft_preflight_commit(
                self.namespace,
                project_id,
                old_preflight_id,
                request_sha256=request_sha256,
                lease_seconds=30,
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_stale_token
        )

        before_artifacts = self.service.store.list_artifacts(
            self.namespace, project_id
        )
        with self.assertRaisesRegex(
            ValueError,
            "draft_preflight_(?:binding_mismatch|authority_revoked).*session|"
            "draft_preflight_session_(?:mismatch|revoked)",
        ):
            self.service.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=storyboard,
                file_name="stale-old-window-preflight.xlsx",
                source_artifact_id=None,
                request_session_id=new_session,
                draft_preflight_id=old_preflight_id,
            )
        self.assertEqual(
            self.service.store.list_artifacts(self.namespace, project_id),
            before_artifacts,
        )

        new_full = self._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=new_session,
            storyboard=storyboard,
        )
        self.assertTrue(new_full.data["commit_ready"])
        self.assertNotEqual(str(new_full.trace_id), old_preflight_id)
        new_record = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=str(new_full.trace_id),
        )
        self.assertEqual(
            new_record["session_fingerprint"],
            self.service._session_fingerprint(self.namespace, new_session),
        )

    def test_live_operation_lease_blocks_rotation_then_allows_rotation(
        self,
    ) -> None:
        project_id = self._project("live-operation-generation-fence")
        old_session = "openai:live-operation-old"
        new_session = "openai:live-operation-new"
        work_order_id = self._begin_production(
            project_id, old_session, "live-operation-production-root"
        )
        storyboard = valid_storyboard()
        scope_id = self._scene_scope_id(storyboard)
        plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )

        reached_boundary = threading.Event()
        release_old_request = threading.Event()
        original_save = self.service.store.save_project_record
        paused = False

        def pause_before_first_record(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            nonlocal paused
            if not paused and kwargs.get("kind") == "draft_preflights":
                paused = True
                reached_boundary.set()
                if not release_old_request.wait(timeout=20):
                    raise AssertionError(
                        "timed out waiting for continuation refusal"
                    )
            return original_save(*args, **kwargs)

        with patch.object(
            self.service.store,
            "save_project_record",
            side_effect=pause_before_first_record,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.service.preflight_storyboard_draft,
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    storyboard=storyboard,
                    scope_kind="scene",
                    scope_id=scope_id,
                    request_session_id=old_session,
                )
                self.assertTrue(
                    reached_boundary.wait(timeout=20),
                    "old generation never reached the persistence boundary",
                )
                before_execute = self._project_tree_bytes(project_id)
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "role_continuation_operation_in_progress|"
                        "authority_operation_in_progress",
                    ):
                        self._execute_continuation(
                            project_id,
                            work_order_id,
                            new_session,
                            str(plan.data["plan_sha256"]),
                        )
                    self.assertEqual(
                        self._project_tree_bytes(project_id), before_execute
                    )
                finally:
                    release_old_request.set()
                old_response = future.result(timeout=30)

        self.assertEqual(
            old_response.status, service_module.DRAFT_PREFLIGHT_PASS
        )
        next_plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        continued = self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(next_plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        with self.assertRaisesRegex(
            ValueError,
            "window_session_(?:revoked|mismatch)|authority_operation_fence_stale",
        ):
            self.service.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=storyboard,
                scope_kind="scene",
                scope_id=scope_id,
                request_session_id=old_session,
            )

    def test_expired_operation_lease_rotates_and_fences_old_context(
        self,
    ) -> None:
        project_id = self._project("expired-operation-generation-fence")
        old_session = "openai:expired-operation-old"
        new_session = "openai:expired-operation-new"
        work_order_id = self._begin_production(
            project_id, old_session, "expired-operation-production-root"
        )
        storyboard = valid_storyboard()
        scope_id = self._scene_scope_id(storyboard)
        plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        before_preflights = self.service.store.list_project_records(
            self.namespace, project_id, kind="draft_preflights"
        )

        reached_boundary = threading.Event()
        release_old_request = threading.Event()
        original_save = self.service.store.save_project_record
        paused = False

        def pause_before_first_record(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            nonlocal paused
            if not paused and kwargs.get("kind") == "draft_preflights":
                paused = True
                reached_boundary.set()
                if not release_old_request.wait(timeout=20):
                    raise AssertionError("timed out waiting for lease expiry")
            return original_save(*args, **kwargs)

        old_error: BaseException | None = None
        with patch.object(
            self.service.store,
            "save_project_record",
            side_effect=pause_before_first_record,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.service.preflight_storyboard_draft,
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    storyboard=storyboard,
                    scope_kind="scene",
                    scope_id=scope_id,
                    request_session_id=old_session,
                )
                self.assertTrue(
                    reached_boundary.wait(timeout=20),
                    "old generation never reached the persistence boundary",
                )
                project_dir = self.service.store.project_dir(
                    self.namespace, project_id
                )
                lease_path = self.service.store._authority_operation_lease_path(
                    project_dir, str(plan.data["authority_root_id"])
                )
                with self.service.store.project_write_lock(
                    self.namespace, project_id
                ):
                    lease = self.service.store.read_json(lease_path)
                    lease["claim_lease_expires_epoch_seconds"] = time.time() - 1
                    lease["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"
                    lease["integrity_hmac"] = (
                        self.service.store._record_integrity_hmac(lease)
                    )
                    self.service.store.atomic_write_json(lease_path, lease)
                try:
                    continued = self._execute_continuation(
                        project_id,
                        work_order_id,
                        new_session,
                        str(plan.data["plan_sha256"]),
                    )
                    self.assertEqual(continued.status, "continued")
                finally:
                    release_old_request.set()
                try:
                    future.result(timeout=30)
                except BaseException as exc:  # noqa: BLE001 - asserted below
                    old_error = exc

        self.assertIsNotNone(old_error)
        self.assertRegex(
            str(old_error),
            "authority_operation_fence_(?:revoked|stale)|"
            "window_session_(?:revoked|mismatch)",
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="draft_preflights"
            ),
            before_preflights,
            "an expired old context must not persist its pending record",
        )

    def test_live_handoff_claim_blocks_rotation_zero_write(
        self,
    ) -> None:
        handoff_project = self._project("live-handoff-blocks-rotation")
        production_session = "openai:claim-production"
        production_order = self._begin_production(
            handoff_project, production_session, "claim-production-root"
        )
        committed = self._commit_production(
            project_id=handoff_project,
            work_order_id=production_order,
            session_id=production_session,
            label="claim-source",
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_session = "openai:claim-audit-old"
        audit_order = self._begin_audit(
            handoff_project,
            str(committed.artifact_id),
            audit_session,
            "claim-audit-root",
        )
        handoff_claim = self.service.store.claim_handoff(
            self.namespace,
            handoff_project,
            handoff_id,
            consumer_work_order_id=audit_order,
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
            lease_seconds=30,
        )
        before_handoff_execute = self._project_tree_bytes(handoff_project)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation_claim_in_progress",
        ):
            self._plan_continuation(
                handoff_project,
                audit_order,
                "openai:claim-audit-new",
            )
        self.assertEqual(
            self._project_tree_bytes(handoff_project),
            before_handoff_execute,
        )
        self.assertEqual(
            self.service.store.get_project_record(
                self.namespace,
                handoff_project,
                kind="handoffs",
                record_id=handoff_id,
            )["claim_token"],
            handoff_claim["claim_token"],
        )

    def test_expired_handoff_token_cannot_finish_or_release_after_rotation(
        self,
    ) -> None:
        project_id = self._project("expired-handoff-token-after-rotation")
        production_session = "openai:expired-handoff-production"
        audit_old_session = "openai:expired-handoff-audit-old"
        audit_new_session = "openai:expired-handoff-audit-new"
        production_order = self._begin_production(
            project_id,
            production_session,
            "expired-handoff-production-root",
        )
        committed = self._commit_production(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            label="expired-handoff-source",
        )
        artifact_id = str(committed.artifact_id)
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            audit_old_session,
            "expired-handoff-audit-root",
        )
        old_claim = self.service.store.claim_handoff(
            self.namespace,
            project_id,
            handoff_id,
            consumer_work_order_id=audit_order,
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
            lease_seconds=1,
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        ) as handoff:
            handoff["claim_lease_expires_epoch_seconds"] = time.time() - 1
            handoff["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"
        expired = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (hub, _ledger):
            for index, row in enumerate(hub["handoffs"]):
                if row.get("handoff_id") == handoff_id:
                    hub["handoffs"][index] = (
                        self.service._handoff_record_projection(expired)
                    )
                    break

        plan = self._plan_continuation(
            project_id, audit_order, audit_new_session
        )
        continued = self._execute_continuation(
            project_id,
            audit_order,
            audit_new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        certificate_id = "CERT-expired-handoff-old-token"
        self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
            value={
                "certificate_id": certificate_id,
                "audit_class": "independent_audit",
                "handoff_id": handoff_id,
                "work_order_id": audit_order,
                "artifact_id": artifact_id,
            },
        )

        for action in ("finish", "release"):
            with self.subTest(action=action):
                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError,
                    "^handoff_claim_revoked(?::|$)",
                ):
                    if action == "finish":
                        self.service.store.finish_handoff(
                            self.namespace,
                            project_id,
                            handoff_id,
                            consumer_work_order_id=audit_order,
                            audit_certificate_id=certificate_id,
                            claim_token=str(old_claim["claim_token"]),
                        )
                    else:
                        self.service.store.release_handoff_claim(
                            self.namespace,
                            project_id,
                            handoff_id,
                            consumer_work_order_id=audit_order,
                            claim_token=str(old_claim["claim_token"]),
                            reason="stale pre-continuation claim",
                        )
                self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_live_repair_claim_blocks_rotation_zero_write(self) -> None:
        repair_project = self._project("live-repair-blocks-rotation")
        repair_session = "openai:claim-repair-old"
        repair_order = self._begin_repair(
            repair_project,
            repair_session,
            "EP01-SC01-SH01",
            "claim-repair-root",
        )
        package_id = "RPK-v0511-live-claim"
        self._save_issued_repair_package(
            repair_project, package_id=package_id
        )
        package_claim = self.service.store.claim_repair_package(
            self.namespace,
            repair_project,
            package_id,
            work_order_id=repair_order,
            lease_seconds=30,
        )
        before_repair_execute = self._project_tree_bytes(repair_project)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation_claim_in_progress",
        ):
            self._plan_continuation(
                repair_project,
                repair_order,
                "openai:claim-repair-new",
            )
        self.assertEqual(
            self._project_tree_bytes(repair_project), before_repair_execute
        )
        self.assertEqual(
            self.service.store.get_project_record(
                self.namespace,
                repair_project,
                kind="repair_packages",
                record_id=package_id,
            )["claim_token"],
            package_claim["claim_token"],
        )

    def test_expired_repair_token_cannot_finish_or_release_after_rotation(
        self,
    ) -> None:
        project_id = self._project("expired-repair-token-after-rotation")
        repair_old_session = "openai:expired-repair-old"
        repair_new_session = "openai:expired-repair-new"
        repair_order = self._begin_repair(
            project_id,
            repair_old_session,
            "EP01-SC01-SH01",
            "expired-repair-root",
        )
        package_id = "RPK-v0511-expired-token-no-reclaim"
        package = self._save_issued_repair_package(
            project_id, package_id=package_id
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, package
        )
        old_claim = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            lease_seconds=1,
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, old_claim
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        ) as current:
            current["claim_lease_expires_epoch_seconds"] = time.time() - 1
            current["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"
        expired = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, expired
        )

        plan = self._plan_continuation(
            project_id, repair_order, repair_new_session
        )
        continued = self._execute_continuation(
            project_id,
            repair_order,
            repair_new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        current = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )
        self.assertEqual(current["status"], "consuming")
        self.assertEqual(current["claim_token"], old_claim["claim_token"])
        self.assertEqual(
            current["claim_generation"], old_claim["claim_generation"]
        )

        for action in ("finish", "release"):
            with self.subTest(action=action):
                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError,
                    "^repair_package_claim_revoked(?::|$)",
                ):
                    self.service.store.finish_repair_package(
                        self.namespace,
                        project_id,
                        package_id,
                        work_order_id=repair_order,
                        claim_token=str(old_claim["claim_token"]),
                        successor_artifact_id=(
                            "ART-stale-repair-successor"
                            if action == "finish"
                            else None
                        ),
                        successor_canonical_sha256=(
                            "e" * 64 if action == "finish" else None
                        ),
                        repair_to_audit_handoff_id=(
                            "HND-stale-repair-to-audit"
                            if action == "finish"
                            else None
                        ),
                    )
                self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_expired_reaudit_token_cannot_finish_or_release_after_rotation(
        self,
    ) -> None:
        project_id = self._project("expired-reaudit-token-after-rotation")
        production_session = "openai:expired-reaudit-production"
        audit_old_session = "openai:expired-reaudit-audit-old"
        audit_new_session = "openai:expired-reaudit-audit-new"
        production_order = self._begin_production(
            project_id,
            production_session,
            "expired-reaudit-production-root",
        )
        committed = self._commit_production(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            label="expired-reaudit-source",
        )
        artifact_id = str(committed.artifact_id)
        root_handoff_id = str(
            committed.data["handoff"]["handoff_id"]
        )
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            audit_old_session,
            "expired-reaudit-audit-root",
        )
        clean_semantic = semantic_audit_for_artifact(
            self.service, self.settings, project_id, artifact_id
        )
        first = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            semantic_audit=clean_semantic,
            request_session_id=audit_old_session,
        )
        self.assertEqual(first.status, "pass")
        shot_id = str(valid_storyboard()["shots"][0]["镜头编号"])
        failed_semantic = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id=shot_id,
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].reaudit_light_source_mismatch",
            evidence="强制复审确认光源语义仍需修复。",
            repair_action="按复审证据修复后返回同一独立审计权限。",
        )
        with (
            patch.object(
                self.service.store,
                "finish_reaudit_successor",
                side_effect=RuntimeError(
                    "simulated process loss before reaudit finish"
                ),
            ),
            patch.object(
                self.service.store,
                "release_reaudit_successor_claim",
                return_value=None,
            ),
            patch.object(
                self.service.store,
                "rollback_reaudit_successor",
                return_value=None,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "before reaudit finish"
            ):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=project_id,
                    artifact_id=artifact_id,
                    audit_cycle_id=None,
                    work_order_id=audit_order,
                    handoff_artifact_id=root_handoff_id,
                    force_machine_recheck=True,
                    semantic_audit=failed_semantic,
                    request_session_id=audit_old_session,
                )
        successor = next(
            row
            for row in self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="handoffs"
            )
            if row.get("reaudit_root_handoff_id") == root_handoff_id
        )
        successor_id = str(successor["handoff_id"])
        self.assertEqual(successor["status"], "consuming")
        self.assertEqual(
            successor["claim_authority_contract"],
            "window-role-authority-claim-v1",
        )
        self.assertEqual(successor["claim_authority_generation"], 0)
        certificates = [
            row
            for row in self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="certificates"
            )
            if row.get("handoff_id") == successor_id
        ]
        self.assertEqual(len(certificates), 1)
        certificate_id = str(certificates[0]["certificate_id"])
        successor_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "handoffs"
            / f"{successor_id}.json"
        )
        successor_bytes = successor_path.read_bytes()
        after_expiry = (
            float(successor["claim_lease_expires_epoch_seconds"]) + 1.0
        )

        with patch.object(
            storage_module.time, "time", return_value=after_expiry
        ):
            plan = self._plan_continuation(
                project_id, audit_order, audit_new_session
            )
            continued = self._execute_continuation(
                project_id,
                audit_order,
                audit_new_session,
                str(plan.data["plan_sha256"]),
            )
            self.assertEqual(continued.status, "continued")
            current = self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="handoffs",
                record_id=successor_id,
            )
            self.assertEqual(current["status"], "consuming")
            self.assertEqual(
                current["claim_token"], successor["claim_token"]
            )
            self.assertEqual(
                current["claim_generation"],
                successor["claim_generation"],
            )
            self.assertEqual(successor_path.read_bytes(), successor_bytes)

            for action in ("finish", "release"):
                with self.subTest(action=action):
                    before = self._project_tree_bytes(project_id)
                    with self.assertRaisesRegex(
                        ValueError,
                        "^reaudit_successor_claim_revoked(?::|$)",
                    ):
                        if action == "finish":
                            self.service.store.finish_reaudit_successor(
                                self.namespace,
                                project_id,
                                successor_id,
                                consumer_work_order_id=audit_order,
                                audit_certificate_id=certificate_id,
                                claim_token=str(successor["claim_token"]),
                            )
                        else:
                            self.service.store.release_reaudit_successor_claim(
                                self.namespace,
                                project_id,
                                successor_id,
                                consumer_work_order_id=audit_order,
                                claim_token=str(successor["claim_token"]),
                                reason="stale audit window cleanup",
                            )
                    self.assertEqual(
                        self._project_tree_bytes(project_id), before
                    )

    def test_expired_operation_lease_plus_live_claim_rejects_byte_exact(
        self,
    ) -> None:
        handoff_project = self._project("expired-operation-live-handoff")
        production_session = "openai:combo-production"
        production_order = self._begin_production(
            handoff_project, production_session, "combo-production-root"
        )
        committed = self._commit_production(
            project_id=handoff_project,
            work_order_id=production_order,
            session_id=production_session,
            label="combo-handoff-source",
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_session = "openai:combo-audit-old"
        audit_order = self._begin_audit(
            handoff_project,
            str(committed.artifact_id),
            audit_session,
            "combo-audit-root",
        )
        audit_plan = self._plan_continuation(
            handoff_project, audit_order, "openai:combo-audit-new"
        )
        self.service.store.claim_handoff(
            self.namespace,
            handoff_project,
            handoff_id,
            consumer_work_order_id=audit_order,
            expected_to_role="independent_audit",
            expected_types={"production_to_audit"},
            lease_seconds=30,
        )
        audit_lease_path = self._claim_expired_authority_operation(
            handoff_project,
            audit_order,
            audit_session,
            str(audit_plan.data["authority_root_id"]),
        )
        before_handoff = self._project_tree_bytes(handoff_project)
        before_audit_lease = audit_lease_path.read_bytes()
        with self.assertRaisesRegex(
            ValueError, "role_continuation_claim_in_progress"
        ):
            self._execute_continuation(
                handoff_project,
                audit_order,
                "openai:combo-audit-new",
                str(audit_plan.data["plan_sha256"]),
            )
        self.assertEqual(
            self._project_tree_bytes(handoff_project), before_handoff
        )
        self.assertEqual(audit_lease_path.read_bytes(), before_audit_lease)

        repair_project = self._project("expired-operation-live-repair")
        repair_session = "openai:combo-repair-old"
        repair_order = self._begin_repair(
            repair_project,
            repair_session,
            "EP01-SC01-SH01",
            "combo-repair-root",
        )
        package_id = "RPK-v0511-combo-live-claim"
        self._save_issued_repair_package(
            repair_project, package_id=package_id
        )
        repair_plan = self._plan_continuation(
            repair_project, repair_order, "openai:combo-repair-new"
        )
        self.service.store.claim_repair_package(
            self.namespace,
            repair_project,
            package_id,
            work_order_id=repair_order,
            lease_seconds=30,
        )
        repair_lease_path = self._claim_expired_authority_operation(
            repair_project,
            repair_order,
            repair_session,
            str(repair_plan.data["authority_root_id"]),
        )
        before_repair = self._project_tree_bytes(repair_project)
        before_repair_lease = repair_lease_path.read_bytes()
        with self.assertRaisesRegex(
            ValueError, "role_continuation_claim_in_progress"
        ):
            self._execute_continuation(
                repair_project,
                repair_order,
                "openai:combo-repair-new",
                str(repair_plan.data["plan_sha256"]),
            )
        self.assertEqual(
            self._project_tree_bytes(repair_project), before_repair
        )
        self.assertEqual(repair_lease_path.read_bytes(), before_repair_lease)

    def test_performance_stage_live_and_expired_authority_fences(
        self,
    ) -> None:
        live_project = self._project("performance-live-operation")
        live_old_session = "openai:performance-live-old"
        live_new_session = "openai:performance-live-new"
        live_order = self._begin_production(
            live_project,
            live_old_session,
            "performance-live-production-root",
        )
        live_plan = self._plan_continuation(
            live_project, live_order, live_new_session
        )
        reached_live = threading.Event()
        release_live = threading.Event()
        original_save = self.service.store.save_project_record
        live_paused = False

        def pause_live_diagnostic(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            nonlocal live_paused
            if not live_paused and kwargs.get("kind") == "diagnostics":
                live_paused = True
                reached_live.set()
                if not release_live.wait(timeout=20):
                    raise AssertionError(
                        "timed out waiting for live diagnostic fence"
                    )
            return original_save(*args, **kwargs)

        with patch.object(
            self.service.store,
            "save_project_record",
            side_effect=pause_live_diagnostic,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.service.mark_performance_stage,
                    self.namespace,
                    project_id=live_project,
                    work_order_id=live_order,
                    action="start",
                    stage="task_started",
                    reasoning_mode="medium",
                    window_type="fresh",
                    route="full_rebuild",
                    request_session_id=live_old_session,
                )
                self.assertTrue(reached_live.wait(timeout=20))
                before_live = self._project_tree_bytes(live_project)
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "role_continuation_operation_in_progress",
                    ):
                        self._execute_continuation(
                            live_project,
                            live_order,
                            live_new_session,
                            str(live_plan.data["plan_sha256"]),
                        )
                    self.assertEqual(
                        self._project_tree_bytes(live_project), before_live
                    )
                finally:
                    release_live.set()
                live_result = future.result(timeout=30)
        self.assertEqual(live_result.status, "diagnostic_started")

        expired_project = self._project("performance-expired-operation")
        expired_old_session = "openai:performance-expired-old"
        expired_new_session = "openai:performance-expired-new"
        expired_order = self._begin_production(
            expired_project,
            expired_old_session,
            "performance-expired-production-root",
        )
        expired_plan = self._plan_continuation(
            expired_project, expired_order, expired_new_session
        )
        before_diagnostics = self.service.store.list_project_records(
            self.namespace, expired_project, kind="diagnostics"
        )
        reached_expired = threading.Event()
        release_expired = threading.Event()
        expired_paused = False

        def pause_expired_diagnostic(
            *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            nonlocal expired_paused
            if not expired_paused and kwargs.get("kind") == "diagnostics":
                expired_paused = True
                reached_expired.set()
                if not release_expired.wait(timeout=20):
                    raise AssertionError(
                        "timed out waiting for expired diagnostic fence"
                    )
            return original_save(*args, **kwargs)

        expired_error: BaseException | None = None
        with patch.object(
            self.service.store,
            "save_project_record",
            side_effect=pause_expired_diagnostic,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.service.mark_performance_stage,
                    self.namespace,
                    project_id=expired_project,
                    work_order_id=expired_order,
                    action="start",
                    stage="task_started",
                    reasoning_mode="medium",
                    window_type="fresh",
                    route="full_rebuild",
                    request_session_id=expired_old_session,
                )
                self.assertTrue(reached_expired.wait(timeout=20))
                self._expire_existing_authority_operation_lease(
                    expired_project,
                    str(expired_plan.data["authority_root_id"]),
                )
                try:
                    continued = self._execute_continuation(
                        expired_project,
                        expired_order,
                        expired_new_session,
                        str(expired_plan.data["plan_sha256"]),
                    )
                    self.assertEqual(continued.status, "continued")
                finally:
                    release_expired.set()
                try:
                    future.result(timeout=30)
                except BaseException as exc:  # noqa: BLE001 - asserted below
                    expired_error = exc
        self.assertIsNotNone(expired_error)
        self.assertRegex(
            str(expired_error), "authority_operation_fence_(?:revoked|stale)"
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, expired_project, kind="diagnostics"
            ),
            before_diagnostics,
        )

    def test_expired_repair_claim_rotates_then_reclaims_with_new_token(
        self,
    ) -> None:
        project_id = self._project("repair-claim-lease-fence")
        old_session = "openai:repair-lease-old"
        new_session = "openai:repair-lease-new"
        repair_order = self._begin_repair(
            project_id,
            old_session,
            "EP01-SC01-SH01",
            "repair-lease-root",
        )
        package_id = "RPK-v0511-expiring-claim"
        self._save_issued_repair_package(project_id, package_id=package_id)
        first = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            lease_seconds=1,
        )
        required_claim_fields = {
            "claim_token",
            "claim_generation",
            "claim_epoch_seconds",
            "claim_lease_seconds",
            "claim_lease_expires_at",
            "claim_lease_expires_epoch_seconds",
        }
        self.assertTrue(required_claim_fields.issubset(first), first)
        self.assertTrue(str(first["claim_token"]))
        self.assertEqual(first["claim_generation"], 1)
        self.assertEqual(first["claim_lease_seconds"], 1)
        self.assertGreater(
            float(first["claim_lease_expires_epoch_seconds"]),
            float(first["claim_epoch_seconds"]),
        )

        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        ) as package:
            package["claim_lease_expires_epoch_seconds"] = time.time() - 1
            package["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"

        plan = self._plan_continuation(
            project_id, repair_order, new_session
        )
        continued = self._execute_continuation(
            project_id,
            repair_order,
            new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")

        second = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            lease_seconds=30,
        )
        self.assertEqual(second["claim_generation"], 2)
        self.assertNotEqual(second["claim_token"], first["claim_token"])
        self.assertEqual(
            second["claim_history"][-1]["state"], "lease_expired"
        )

        with self.assertRaisesRegex(
            ValueError, "repair_package_claim_conflict"
        ):
            self.service.store.finish_repair_package(
                self.namespace,
                project_id,
                package_id,
                work_order_id=repair_order,
                successor_artifact_id="ART-stale-claim",
                successor_canonical_sha256="a" * 64,
                repair_to_audit_handoff_id="HND-stale-claim",
                claim_token=str(first["claim_token"]),
            )
        released = self.service.store.finish_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            successor_artifact_id=None,
            successor_canonical_sha256=None,
            claim_token=str(second["claim_token"]),
        )
        self.assertEqual(released["status"], "issued")

    def test_stale_repair_cleanup_cannot_release_new_generation_claim(
        self,
    ) -> None:
        project_id = self._project("stale-repair-cleanup-generation")
        repair_session = "openai:stale-repair-cleanup"
        repair_order = self._begin_repair(
            project_id,
            repair_session,
            "EP01-SC01-SH01",
            "stale-repair-cleanup-root",
        )
        package_id = "RPK-v0511-stale-cleanup-generation"
        original_package = self._save_issued_repair_package(
            project_id, package_id=package_id
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, original_package
        )
        first = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            lease_seconds=1,
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, first
        )
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        ) as package:
            package["claim_lease_expires_epoch_seconds"] = time.time() - 1
            package["claim_lease_expires_at"] = "2000-01-01T00:00:00Z"
        second = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            work_order_id=repair_order,
            lease_seconds=30,
        )
        self.service._sync_repair_package_in_hub(
            self.namespace, project_id, second
        )
        self.assertEqual(
            int(second["claim_generation"]),
            int(first["claim_generation"]) + 1,
        )
        self.assertNotEqual(second["claim_token"], first["claim_token"])

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "repair_package_claim_conflict"
        ):
            self.service._restore_repair_package_after_failed_commit(
                self.namespace,
                project_id,
                original_package=original_package,
                work_order_id=repair_order,
                expected_claim_token=str(first["claim_token"]),
                expected_claim_generation=int(first["claim_generation"]),
                base_artifact_id="ART-fixed-stale-cleanup-base",
                transaction_id="REPAIR-fixed-stale-cleanup",
            )
        current = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )
        self.assertEqual(current["status"], "consuming")
        self.assertEqual(current["claim_token"], second["claim_token"])
        self.assertEqual(
            current["claim_generation"], second["claim_generation"]
        )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_same_authority_cannot_hold_two_live_operation_leases(
        self,
    ) -> None:
        project_id = self._project("exclusive-authority-operation")
        session_id = "openai:exclusive-authority-operation"
        work_order_id = self._begin_production(
            project_id, session_id, "exclusive-authority-operation-root"
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == work_order_id
        )
        binding = self.service._active_authority_binding(
            self.namespace, project_id, work_order, hub=hub
        )
        claim_arguments = {
            "work_order_id": work_order_id,
            "expected_authority_root_id": str(
                binding["authority_root_id"]
            ),
            "expected_authority_generation": int(
                binding.get("generation") or 0
            ),
            "expected_session_fingerprint": binding.get(
                "session_fingerprint"
            ),
            "lease_seconds": 30,
        }
        first = self.service.store.claim_authority_operation(
            self.namespace, project_id, **claim_arguments
        )
        before = self._project_tree_bytes(project_id)
        try:
            with self.assertRaisesRegex(
                ValueError, "authority_operation_in_progress"
            ):
                self.service.store.claim_authority_operation(
                    self.namespace, project_id, **claim_arguments
                )
            self.assertEqual(self._project_tree_bytes(project_id), before)
        finally:
            self.service.store.release_authority_operation(
                self.namespace,
                project_id,
                authority_root_id=str(first["authority_root_id"]),
                claim_token=str(first["claim_token"]),
            )

    def test_unsigned_authority_operation_lease_cannot_be_reclaimed_or_resigned(
        self,
    ) -> None:
        project_id = self._project("unsigned-authority-operation-lease")
        session_id = "openai:unsigned-authority-operation-lease"
        work_order_id = self._begin_production(
            project_id,
            session_id,
            "unsigned-authority-operation-root",
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == work_order_id
        )
        binding = self.service._active_authority_binding(
            self.namespace, project_id, work_order, hub=hub
        )
        claim_arguments = {
            "work_order_id": work_order_id,
            "expected_authority_root_id": str(
                binding["authority_root_id"]
            ),
            "expected_authority_generation": int(
                binding.get("generation") or 0
            ),
            "expected_session_fingerprint": binding.get(
                "session_fingerprint"
            ),
            "lease_seconds": 30,
        }
        claim = self.service.store.claim_authority_operation(
            self.namespace, project_id, **claim_arguments
        )
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        lease_path = self.service.store._authority_operation_lease_path(
            project_dir, str(claim["authority_root_id"])
        )
        self._replace_with_unsigned_record(lease_path)
        after_expiry = (
            float(claim["claim_lease_expires_epoch_seconds"]) + 1.0
        )
        with patch.object(
            storage_module.time, "time", return_value=after_expiry
        ):
            self._assert_unsigned_record_rejected_without_laundering(
                project_id=project_id,
                record_path=lease_path,
                expected_error="authority_operation_lease_tampered",
                protected_write=lambda: (
                    self.service.store.claim_authority_operation(
                        self.namespace, project_id, **claim_arguments
                    )
                ),
            )

    def test_unsigned_handoff_claim_cannot_be_reclaimed_or_resigned(
        self,
    ) -> None:
        project_id = self._project("unsigned-handoff-claim")
        production_session = "openai:unsigned-handoff-production"
        production_order = self._begin_production(
            project_id,
            production_session,
            "unsigned-handoff-production-root",
        )
        committed = self._commit_production(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            label="unsigned-handoff-source",
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_order = self._begin_audit(
            project_id,
            str(committed.artifact_id),
            "openai:unsigned-handoff-audit",
            "unsigned-handoff-audit-root",
        )
        claim_arguments = {
            "consumer_work_order_id": audit_order,
            "expected_to_role": "independent_audit",
            "expected_types": {"production_to_audit"},
            "lease_seconds": 30,
        }
        claim = self.service.store.claim_handoff(
            self.namespace,
            project_id,
            handoff_id,
            **claim_arguments,
        )
        handoff_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "handoffs"
            / f"{handoff_id}.json"
        )
        self._replace_with_unsigned_record(handoff_path)
        after_expiry = (
            float(claim["claim_lease_expires_epoch_seconds"]) + 1.0
        )
        with patch.object(
            storage_module.time, "time", return_value=after_expiry
        ):
            self._assert_unsigned_record_rejected_without_laundering(
                project_id=project_id,
                record_path=handoff_path,
                expected_error="handoff_signature_required",
                protected_write=lambda: self.service.store.claim_handoff(
                    self.namespace,
                    project_id,
                    handoff_id,
                    **claim_arguments,
                ),
            )

    def test_unsigned_repair_claim_cannot_be_reclaimed_or_resigned(
        self,
    ) -> None:
        project_id = self._project("unsigned-repair-package-claim")
        repair_order = self._begin_repair(
            project_id,
            "openai:unsigned-repair-package",
            "EP01-SC01-SH01",
            "unsigned-repair-package-root",
        )
        package_id = "RPK-v0511-unsigned-repair-claim"
        self._save_issued_repair_package(
            project_id, package_id=package_id
        )
        claim_arguments = {
            "work_order_id": repair_order,
            "lease_seconds": 30,
        }
        claim = self.service.store.claim_repair_package(
            self.namespace,
            project_id,
            package_id,
            **claim_arguments,
        )
        package_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "repair_packages"
            / f"{package_id}.json"
        )
        self._replace_with_unsigned_record(package_path)
        after_expiry = (
            float(claim["claim_lease_expires_epoch_seconds"]) + 1.0
        )
        with patch.object(
            storage_module.time, "time", return_value=after_expiry
        ):
            self._assert_unsigned_record_rejected_without_laundering(
                project_id=project_id,
                record_path=package_path,
                expected_error="repair_package_signature_required",
                protected_write=lambda: (
                    self.service.store.claim_repair_package(
                        self.namespace,
                        project_id,
                        package_id,
                        **claim_arguments,
                    )
                ),
            )

    def test_unsigned_reaudit_claim_cannot_be_reclaimed_or_resigned(
        self,
    ) -> None:
        fixture = self._leave_consuming_reaudit_successor(
            "unsigned-replacement"
        )
        project_id = str(fixture["project_id"])
        audit_work_order_id = str(fixture["audit_work_order_id"])
        successor = fixture["successor"]
        successor_id = str(successor["handoff_id"])
        successor_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "handoffs"
            / f"{successor_id}.json"
        )
        self._replace_with_unsigned_record(successor_path)
        after_expiry = (
            float(successor["claim_lease_expires_epoch_seconds"]) + 1.0
        )
        with patch.object(
            storage_module.time, "time", return_value=after_expiry
        ):
            self._assert_unsigned_record_rejected_without_laundering(
                project_id=project_id,
                record_path=successor_path,
                expected_error="handoff_signature_required",
                protected_write=lambda: (
                    self.service.store.claim_reaudit_successor(
                        self.namespace,
                        project_id,
                        successor_id,
                        consumer_work_order_id=audit_work_order_id,
                        lease_seconds=30,
                    )
                ),
            )

    def test_signed_record_hub_half_write_recovers_only_exact_plan(
        self,
    ) -> None:
        project_id = self._project("continuation-half-write")
        work_order_id = self._begin_production(
            project_id,
            "openai:half-write-old",
            "half-write-production-root",
        )
        session_a = "openai:half-write-new-a"
        session_b = "openai:half-write-new-b"
        plan_a = self._plan_continuation(
            project_id, work_order_id, session_a
        )
        plan_b = self._plan_continuation(
            project_id, work_order_id, session_b
        )
        hub_path = self.service.store.project_dir(
            self.namespace, project_id
        ) / "hub.json"
        original_write = self.service.store.atomic_write_json
        failed = False

        def fail_head_once(path: Path, value: Any) -> None:
            nonlocal failed
            if (
                not failed
                and Path(path) == hub_path
                and isinstance(value, dict)
                and value.get("active_authority_bindings")
            ):
                failed = True
                raise OSError("injected continuation Hub-head half-write")
            original_write(path, value)

        with patch.object(
            self.service.store,
            "atomic_write_json",
            side_effect=fail_head_once,
        ):
            with self.assertRaisesRegex(
                OSError, "injected continuation Hub-head half-write"
            ):
                self._execute_continuation(
                    project_id,
                    work_order_id,
                    session_a,
                    str(plan_a.data["plan_sha256"]),
                )

        records = self.service.store.list_project_records_strict(
            self.namespace,
            project_id,
            kind="authority_continuations",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["plan_sha256"], plan_a.data["plan_sha256"])
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id).get(
                "active_authority_bindings"
            ),
            [],
        )

        with self.assertRaisesRegex(
            ValueError,
            "authority_continuation_plan_conflict|"
            "role_continuation_(?:plan_mismatch|replay_conflict)|"
            "authority_head_incomplete",
        ):
            self._execute_continuation(
                project_id,
                work_order_id,
                session_b,
                str(plan_b.data["plan_sha256"]),
            )
        self.assertEqual(
            len(
                self.service.store.list_project_records_strict(
                    self.namespace,
                    project_id,
                    kind="authority_continuations",
                )
            ),
            1,
        )

        recovered = self._execute_continuation(
            project_id,
            work_order_id,
            session_a,
            str(plan_a.data["plan_sha256"]),
        )
        self.assertEqual(recovered.status, "continued")
        head = self.service.store.load_hub(
            self.namespace, project_id
        )["active_authority_bindings"]
        self.assertEqual(len(head), 1)
        self.assertEqual(head[0]["continuation_id"], records[0]["continuation_id"])
        replay = self._execute_continuation(
            project_id,
            work_order_id,
            session_a,
            str(plan_a.data["plan_sha256"]),
        )
        self.assertTrue(replay.data["idempotent_replay"])

    def test_signed_hmac_and_active_head_tamper_fail_closed(self) -> None:
        project_id = self._project("continuation-tamper-fence")
        work_order_id = self._begin_production(
            project_id,
            "openai:tamper-old",
            "tamper-production-root",
        )
        plan = self._plan_continuation(
            project_id, work_order_id, "openai:tamper-new"
        )
        executed = self._execute_continuation(
            project_id,
            work_order_id,
            "openai:tamper-new",
            str(plan.data["plan_sha256"]),
        )
        continuation_id = str(executed.data["continuation_id"])
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        continuation_path = (
            project_dir
            / "authority_continuations"
            / f"{continuation_id}.json"
        )
        original_record = self.service.store.read_json(continuation_path)
        tampered_record = copy.deepcopy(original_record)
        tampered_record["integrity_hmac"] = "0" * 64
        self.service.store.atomic_write_json(
            continuation_path, tampered_record
        )
        with self.assertRaisesRegex(
            ValueError,
            "integrity|hmac|signature|authority_head_invalid",
        ):
            self._plan_continuation(
                project_id, work_order_id, "openai:tamper-next"
            )
        self.service.store.atomic_write_json(
            continuation_path, original_record
        )

        hub_path = project_dir / "hub.json"
        original_hub = self.service.store.read_json(hub_path)
        tampered_hub = copy.deepcopy(original_hub)
        tampered_hub["active_authority_bindings"][0]["generation"] = 0
        self.service.store.atomic_write_json(hub_path, tampered_hub)
        with self.assertRaisesRegex(
            ValueError,
            "authority_head_(?:invalid|rollback)|continuation.*(?:differs|generation)",
        ):
            self._plan_continuation(
                project_id, work_order_id, "openai:tamper-next"
            )
        self.service.store.atomic_write_json(hub_path, original_hub)

        rolled_back_hub = copy.deepcopy(original_hub)
        rolled_back_hub["active_authority_bindings"] = []
        self.service.store.atomic_write_json(hub_path, rolled_back_hub)
        with self.assertRaisesRegex(
            ValueError,
            "authority_head_(?:missing|rollback|invalid|incomplete)|"
            "authority_continuation_(?:head|rollback)",
        ):
            self._plan_continuation(
                project_id, work_order_id, "openai:tamper-next"
            )

    def test_continuation_plan_requires_trusted_cloud_session(self) -> None:
        project_id = self._project("missing-cloud-session")
        work_order_id = self._begin_production(
            project_id,
            "openai:trusted-original-session",
            "missing-session-production-root",
        )
        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(ValueError, "window_session_required"):
            self._plan_continuation(project_id, work_order_id, None)
        self.assertEqual(self._project_tree_bytes(project_id), before)

        local_settings = self._settings(
            Path(self.temp.name) / "local", deployment_mode="local"
        )
        _server, local_service = create_mcp(local_settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(local_service)
        local_project = self._project(
            "untrusted-local-session", service=local_service
        )
        local_order = self._begin_on(
            local_service,
            local_project,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            session_id=None,
            targets=["第一集"],
            label="local-production-root",
        )
        local_dir = local_service.store.project_dir(
            self.namespace, local_project
        )
        local_before = {
            str(path.relative_to(local_dir)): path.read_bytes()
            for path in sorted(local_dir.rglob("*"))
            if path.is_file()
        }
        with self.assertRaisesRegex(
            ValueError, "role_continuation_cloud_only"
        ):
            self._continuation_request_on(
                local_service,
                local_project,
                resume_work_order_id=local_order,
                new_session_id="client-supplied-but-untrusted",
                action="plan_role_continuation",
            )
        self.assertEqual(
            {
                str(path.relative_to(local_dir)): path.read_bytes()
                for path in sorted(local_dir.rglob("*"))
                if path.is_file()
            },
            local_before,
        )

    def test_continued_production_session_cannot_create_audit_work_order(
        self,
    ) -> None:
        project_id = self._project("continued-production-cannot-audit")
        old_session = "openai:continued-production-old"
        continued_session = "openai:continued-production-role-x"
        production_order = self._begin_production(
            project_id,
            old_session,
            "continued-production-root",
        )
        committed = self._commit_production(
            project_id=project_id,
            work_order_id=production_order,
            session_id=old_session,
            label="continued-production-source",
        )
        plan = self._plan_continuation(
            project_id, production_order, continued_session
        )
        continued = self._execute_continuation(
            project_id,
            production_order,
            continued_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "(?:session_role_separation_required|"
            "role_continuation_separation_required)",
        ):
            self._begin_audit(
                project_id,
                str(committed.artifact_id),
                continued_session,
                "forbidden-audit-from-continued-production-session",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_continued_audit_session_cannot_create_repair_work_order(
        self,
    ) -> None:
        project_id = self._project("continued-audit-cannot-repair")
        production_session = "openai:continued-audit-production"
        old_audit_session = "openai:continued-audit-old"
        continued_session = "openai:continued-audit-role-x"
        production_order = self._begin_production(
            project_id,
            production_session,
            "continued-audit-production-root",
        )
        broken = valid_storyboard()
        broken["source_lock"]["上集终点"] += "【continued-audit】"
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        preflight = self._preflight_full(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            storyboard=broken,
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=production_order,
            storyboard=broken,
            file_name="continued-audit-failed-source.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        self.assertEqual(
            committed.status, service_module.PRODUCTION_PREFLIGHT_PASS
        )
        artifact_id = str(committed.artifact_id)
        production_handoff_id = str(
            committed.data["handoff"]["handoff_id"]
        )
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            old_audit_session,
            "continued-audit-root",
        )
        shot_id = str(broken["shots"][0]["镜头编号"])
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id=shot_id,
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认右侧窗光与锁定的左侧自然光事实冲突。",
            repair_action="仅修正首镜光源来源后返回原独立审计窗口。",
        )
        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=production_handoff_id,
            semantic_audit=semantic_failure,
            request_session_id=old_audit_session,
        )
        self.assertEqual(failed.status, "fail_current")
        self.assertEqual(
            failed.data["repair_package"]["status"], "issued"
        )
        plan = self._plan_continuation(
            project_id, audit_order, continued_session
        )
        continued = self._execute_continuation(
            project_id,
            audit_order,
            continued_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "(?:session_role_separation_required|"
            "role_continuation_separation_required)",
        ):
            self._begin_repair(
                project_id,
                continued_session,
                shot_id,
                "forbidden-repair-from-continued-audit-session",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)


if __name__ == "__main__":
    unittest.main()
