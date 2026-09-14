from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import shortdrama_mcp.service as service_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings

from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    semantic_audit_for_artifact,
)
from test_efficiency import valid_storyboard


CONTINUATION_CONFIRMATION = "ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION"


class V0511WindowRoleContinuationTests(unittest.TestCase):
    """Security and lifecycle contract for rotating a lost window authority.

    The service calls are direct so every case can provide a deterministic
    server-observed session identity.  Public MCP wiring remains covered by
    the stdio and HTTP smoke suites.
    """

    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="shortdrama-v0511-window-continuation-"
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
        _server, self.service = create_mcp(self.settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(self.service)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _project(self, label: str) -> str:
        project_id = str(
            self.service.create_project(self.namespace, label).project_id
        )
        status = self.service.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(
            status.data.get("runtime_test_certificate_valid"),
            status.data,
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

    def _begin(
        self,
        project_id: str,
        *,
        operation: str,
        mode: str,
        authorization: str,
        session_id: str,
        targets: list[str],
        label: str,
    ) -> str:
        response = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            request_text=f"v0.5.11窗口权限接续测试：{label}",
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
        self, project_id: str, session_id: str, shot_id: str, label: str
    ) -> str:
        return self._begin(
            project_id,
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            session_id=session_id,
            targets=[shot_id],
            label=label,
        )

    def _continuation_request(
        self,
        project_id: str,
        *,
        resume_work_order_id: str,
        new_session_id: str,
        action: str,
        expected_plan_sha256: str | None = None,
        confirmation: str | None = None,
        overrides: dict[str, Any] | None = None,
        contract_project_id: str | None = None,
    ) -> Any:
        hub = self.service.store.load_hub(
            self.namespace, contract_project_id or project_id
        )
        root = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == resume_work_order_id
        )
        source_lock = root.get("source_lock") or {}
        request: dict[str, Any] = {
            "project_id": project_id,
            "operation": root["operation"],
            "mode": root["mode"],
            "authorization": root["authorization"],
            "request_text": "用户明确授权撤销旧窗口并由当前新窗口接续同一角色权限",
            "targets": copy.deepcopy(root["targets"]),
            "authoritative_source": source_lock.get(
                "authoritative_source", "已锁定项目证据链"
            ),
            "latest_version": source_lock.get("latest_version", "current"),
            "target_boundary": source_lock.get("target_boundary", "第一集"),
            "must_keep": copy.deepcopy(source_lock.get("must_keep") or []),
            "must_remove": copy.deepcopy(source_lock.get("must_remove") or []),
            "protected_scope": copy.deepcopy(root.get("protected_scope") or []),
            "expected_outputs": copy.deepcopy(root.get("expected_outputs") or []),
            "window_key": f"continued-{new_session_id}",
            "request_session_id": new_session_id,
            "action": action,
            "resume_work_order_id": resume_work_order_id,
            "expected_plan_sha256": expected_plan_sha256,
            "confirmation": confirmation,
        }
        request.update(overrides or {})
        return self.service.begin_task(self.namespace, **request)

    def _plan_continuation(
        self,
        project_id: str,
        work_order_id: str,
        new_session_id: str,
    ) -> Any:
        return self._continuation_request(
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
        return self._continuation_request(
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

    def _commit(
        self,
        *,
        project_id: str,
        work_order_id: str,
        session_id: str,
        storyboard: dict[str, Any],
        label: str,
    ) -> Any:
        preflight = self._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=session_id,
            storyboard=storyboard,
        )
        self.assertEqual(preflight.status, service_module.DRAFT_PREFLIGHT_PASS)
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

    def _passed_audit_fixture(self, label: str) -> dict[str, Any]:
        project_id = self._project(label)
        production_session = f"openai:{label}-production"
        audit_session = f"openai:{label}-audit"
        production_order = self._begin_production(
            project_id, production_session, f"{label}-production"
        )
        storyboard = valid_storyboard()
        committed = self._commit(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            storyboard=storyboard,
            label=f"{label}-storyboard",
        )
        artifact_id = str(committed.artifact_id)
        root_handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            audit_session,
            f"{label}-audit",
        )
        passed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                artifact_id,
            ),
            request_session_id=audit_session,
        )
        self.assertEqual(passed.status, "pass")
        audit_work_order = next(
            row
            for row in self.service.store.load_hub(
                self.namespace, project_id
            )["work_orders"]
            if row.get("work_order_id") == audit_order
        )
        self.assertEqual(audit_work_order["status"], "passed")
        return {
            "project_id": project_id,
            "storyboard": storyboard,
            "artifact_id": artifact_id,
            "root_handoff_id": root_handoff_id,
            "audit_order": audit_order,
            "audit_session": audit_session,
        }

    def _leave_prepared_reaudit_successor(
        self, fixture: dict[str, Any]
    ) -> dict[str, Any]:
        semantic_audit = self._continued_reaudit_semantic(fixture)
        with patch.object(
            self.service.store,
            "claim_reaudit_successor",
            side_effect=RuntimeError("simulated loss before re-audit claim"),
        ), patch.object(
            self.service.store,
            "rollback_reaudit_successor",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "before re-audit claim"
            ):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=fixture["project_id"],
                    artifact_id=fixture["artifact_id"],
                    audit_cycle_id=None,
                    work_order_id=fixture["audit_order"],
                    handoff_artifact_id=fixture["root_handoff_id"],
                    force_machine_recheck=True,
                    semantic_audit=semantic_audit,
                    request_session_id=fixture["audit_session"],
                )
        pending = [
            row
            for row in self.service.store.list_project_records_strict(
                self.namespace,
                fixture["project_id"],
                kind="handoffs",
            )
            if row.get("reaudit_kind") == "independent_audit_successor"
            and row.get("claim_authority_work_order_id")
            == fixture["audit_order"]
            and row.get("status") == "prepared"
        ]
        self.assertEqual(len(pending), 1)
        successor = pending[0]
        self.assertTrue(self.service.store.project_record_is_signed(successor))
        self.service._validate_schema_record(
            self.service._project_schema, "handoff_manifest", successor
        )
        audit_work_order = next(
            row
            for row in self.service.store.load_hub(
                self.namespace, fixture["project_id"]
            )["work_orders"]
            if row.get("work_order_id") == fixture["audit_order"]
        )
        self.assertEqual(audit_work_order["status"], "passed")
        return successor

    def _continued_reaudit_semantic(
        self, fixture: dict[str, Any]
    ) -> dict[str, Any]:
        storyboard = fixture["storyboard"]
        return failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            fixture["project_id"],
            fixture["artifact_id"],
            shot_id=str(storyboard["shots"][0]["镜头编号"]),
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].continuation_reaudit_recovery",
            evidence="独立复审已形成新结论，但窗口在领取复审路由前丢失。",
            repair_action="由同一独立审计权限的新窗口接续并完成该复审路由。",
        )

    @staticmethod
    def _plan_generations(data: dict[str, Any]) -> tuple[int, int]:
        previous = data.get("current_generation", data.get("previous_generation"))
        upcoming = data.get("next_generation", data.get("new_generation"))
        return int(previous), int(upcoming)

    def test_plan_is_zero_write_and_binds_exact_authority(self) -> None:
        project_id = self._project("production-window-continuation-plan")
        old_session = "openai:production-plan-old-window"
        new_session = "openai:production-plan-new-window"
        work_order_id = self._begin_production(
            project_id, old_session, "production-plan-root"
        )

        before_plan = self._project_tree_bytes(project_id)
        planned = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        self.assertEqual(planned.status, "planned")
        self.assertEqual(planned.work_order_id, work_order_id)
        self.assertEqual(planned.data["action"], "plan_role_continuation")
        self.assertEqual(planned.data["resume_work_order_id"], work_order_id)
        self.assertEqual(planned.data["role"], "production")
        self.assertTrue(planned.data["authority_root_id"])
        self.assertTrue(planned.data["authority_contract_sha256"])
        self.assertEqual(
            planned.data["confirmation_required"], CONTINUATION_CONFIRMATION
        )
        self.assertEqual(self._plan_generations(planned.data), (0, 1))
        self.assertEqual(
            self._project_tree_bytes(project_id),
            before_plan,
            "planning a continuation must be a zero-write operation",
        )

    def test_passed_audit_with_one_pending_reaudit_route_can_continue(
        self,
    ) -> None:
        fixture = self._passed_audit_fixture(
            "passed-audit-pending-reaudit-continuation"
        )
        pending = self._leave_prepared_reaudit_successor(fixture)
        project_id = str(fixture["project_id"])
        audit_order = str(fixture["audit_order"])
        new_session = "openai:passed-audit-pending-reaudit-new-window"
        pending_before = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=str(pending["handoff_id"]),
        )

        before_plan = self._project_tree_bytes(project_id)
        planned = self._plan_continuation(
            project_id, audit_order, new_session
        )
        self.assertEqual(planned.status, "planned")
        self.assertEqual(planned.data["role"], "independent_audit")
        self.assertEqual(self._plan_generations(planned.data), (0, 1))
        self.assertEqual(self._project_tree_bytes(project_id), before_plan)

        continued = self._execute_continuation(
            project_id,
            audit_order,
            new_session,
            str(planned.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        self.assertEqual(continued.data["generation"], 1)
        self.assertEqual(
            self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="handoffs",
                record_id=str(pending["handoff_id"]),
            ),
            pending_before,
            "continuing a passed audit authority must not rewrite its pending route",
        )

    def test_continued_audit_completes_legacy_pending_route_and_replays_lineage(
        self,
    ) -> None:
        fixture = self._passed_audit_fixture(
            "continued-audit-certificate-lineage"
        )
        pending = self._leave_prepared_reaudit_successor(fixture)
        project_id = str(fixture["project_id"])
        audit_order = str(fixture["audit_order"])
        root_handoff_id = str(fixture["root_handoff_id"])
        artifact_id = str(fixture["artifact_id"])
        new_session = "openai:continued-audit-certificate-new-window"
        plan = self._plan_continuation(
            project_id, audit_order, new_session
        )
        continued = self._execute_continuation(
            project_id,
            audit_order,
            new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.data["generation"], 1)
        active = self.service._active_authority_binding(
            self.namespace,
            project_id,
            next(
                row
                for row in self.service.store.load_hub(
                    self.namespace, project_id
                )["work_orders"]
                if row.get("work_order_id") == audit_order
            ),
        )

        failed_semantic = self._continued_reaudit_semantic(fixture)
        completed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=str(pending["handoff_id"]),
            force_machine_recheck=True,
            semantic_audit=failed_semantic,
            request_session_id=new_session,
        )
        self.assertEqual(completed.status, "fail_current")
        first_certificate_id = str(
            completed.data["certificate"]["certificate_id"]
        )
        first_certificate = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=first_certificate_id,
        )
        self.assertEqual(first_certificate["audit_authority_generation"], 1)
        self.assertEqual(
            first_certificate["audit_authority_root_id"],
            active["authority_root_id"],
        )
        self.assertEqual(
            first_certificate["audit_authority_epoch_id"],
            active["authority_epoch_id"],
        )
        self.assertEqual(
            first_certificate["audit_authority_continuation_id"],
            active["continuation_id"],
        )
        self.assertEqual(
            first_certificate["audit_actor_binding_id"],
            active["actor_binding_id"],
        )
        self.assertEqual(
            first_certificate["audit_session_fingerprint"],
            active["session_fingerprint"],
        )

        before_replays = self._project_tree_bytes(project_id)
        ordinary_replay = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            request_session_id=new_session,
        )
        self.assertEqual(ordinary_replay.status, "fail_current")
        force_replay = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            force_machine_recheck=True,
            semantic_audit=failed_semantic,
            request_session_id=new_session,
        )
        self.assertEqual(force_replay.status, "fail_current")
        self.assertEqual(
            force_replay.data["certificate"]["certificate_id"],
            first_certificate_id,
        )
        self.assertEqual(
            self._project_tree_bytes(project_id),
            before_replays,
            "ordinary and identical force retries must replay without writes",
        )

        next_semantic = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id=str(fixture["storyboard"]["shots"][0]["镜头编号"]),
            field="光源来源",
            error_code="E-TEMPLATE-009",
            issue_id="shots[1].continued_window_next_lineage",
            evidence="新窗口下一轮独立复审确认同一镜头仍有另一项光源连续性问题。",
            repair_action="保持同一审计权限血缘并签发下一轮修复证据。",
        )
        next_round = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            force_machine_recheck=True,
            semantic_audit=next_semantic,
            request_session_id=new_session,
        )
        self.assertEqual(next_round.status, "fail_current")
        successors = sorted(
            (
                row
                for row in self.service.store.list_project_records_strict(
                    self.namespace, project_id, kind="handoffs"
                )
                if row.get("reaudit_kind")
                == "independent_audit_successor"
            ),
            key=lambda row: int(row["reaudit_sequence"]),
        )
        self.assertEqual([row["reaudit_sequence"] for row in successors], [1, 2])
        latest_route = successors[-1]
        self.assertEqual(
            latest_route["reaudit_audit_authority_generation"], 1
        )
        self.assertEqual(
            latest_route["reaudit_audit_authority_continuation_id"],
            active["continuation_id"],
        )
        latest_certificate = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=str(latest_route["audit_certificate_id"]),
        )
        self.assertEqual(latest_certificate["audit_authority_generation"], 1)
        self.assertEqual(
            latest_certificate["audit_authority_continuation_id"],
            active["continuation_id"],
        )
        original_manifest_validator = (
            self.service._validate_reaudit_lineage_certificate_authority
        )

        def poison_manifest(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            manifest = original_manifest_validator(*args, **kwargs)
            poisoned = copy.deepcopy(manifest)
            poisoned[-1]["authority_contract_sha256"] = "0" * 64
            return poisoned

        before_poisoned_replay = self._project_tree_bytes(project_id)
        with patch.object(
            self.service,
            "_validate_reaudit_lineage_certificate_authority",
            side_effect=poison_manifest,
        ):
            with self.assertRaisesRegex(
                ValueError, "completed lineage authority changed"
            ):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=project_id,
                    artifact_id=artifact_id,
                    audit_cycle_id=None,
                    work_order_id=audit_order,
                    handoff_artifact_id=root_handoff_id,
                    request_session_id=new_session,
                )
        self.assertEqual(
            self._project_tree_bytes(project_id),
            before_poisoned_replay,
        )
        final_replay = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            request_session_id=new_session,
        )
        self.assertEqual(final_replay.status, "fail_current")
        self.assertEqual(
            final_replay.data["certificate"]["certificate_id"],
            latest_certificate["certificate_id"],
        )

    def test_certificate_authority_partial_and_bool_snapshots_fail_zero_write(
        self,
    ) -> None:
        fixture = self._passed_audit_fixture(
            "certificate-authority-snapshot-negative"
        )
        project_id = str(fixture["project_id"])
        root = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=str(fixture["root_handoff_id"]),
        )
        certificate_id = str(root["audit_certificate_id"])
        certificate_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "certificates"
            / f"{certificate_id}.json"
        )
        original = self.service.store.read_json(certificate_path)

        mutations = []
        partial = copy.deepcopy(original)
        partial.pop("audit_authority_epoch_id")
        mutations.append(partial)
        bool_generation = copy.deepcopy(original)
        bool_generation["audit_authority_generation"] = True
        mutations.append(bool_generation)
        for tampered in mutations:
            tampered.pop("integrity_hmac", None)
            tampered["integrity_hmac"] = (
                self.service.store._record_integrity_hmac(tampered)
            )
            self.service.store.atomic_write_json(
                certificate_path, tampered
            )
            before = self._project_tree_bytes(project_id)
            with self.assertRaises(ValueError):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=project_id,
                    artifact_id=str(fixture["artifact_id"]),
                    audit_cycle_id=None,
                    work_order_id=str(fixture["audit_order"]),
                    handoff_artifact_id=str(fixture["root_handoff_id"]),
                    request_session_id=str(fixture["audit_session"]),
                )
            self.assertEqual(self._project_tree_bytes(project_id), before)
            self.service.store.atomic_write_json(certificate_path, original)

    def test_non_head_continuation_corrupt_json_fails_session_fence_zero_write(
        self,
    ) -> None:
        project_id = self._project("non-head-continuation-corrupt-json")
        work_order_id = self._begin_production(
            project_id,
            "openai:corrupt-chain-old",
            "corrupt-chain-root",
        )
        plan_one = self._plan_continuation(
            project_id, work_order_id, "openai:corrupt-chain-one"
        )
        generation_one = self._execute_continuation(
            project_id,
            work_order_id,
            "openai:corrupt-chain-one",
            str(plan_one.data["plan_sha256"]),
        )
        plan_two = self._plan_continuation(
            project_id, work_order_id, "openai:corrupt-chain-two"
        )
        self._execute_continuation(
            project_id,
            work_order_id,
            "openai:corrupt-chain-two",
            str(plan_two.data["plan_sha256"]),
        )
        non_head_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "authority_continuations"
            / f"{generation_one.data['continuation_id']}.json"
        )
        non_head_path.write_text("{", encoding="utf-8")
        before = self._project_tree_bytes(project_id)
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            self._plan_continuation(
                project_id,
                work_order_id,
                "openai:corrupt-chain-three",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_passed_audit_without_pending_reaudit_route_is_not_resumable(
        self,
    ) -> None:
        fixture = self._passed_audit_fixture(
            "passed-audit-without-pending-reaudit"
        )
        project_id = str(fixture["project_id"])
        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation_state_invalid: passed audit requires exactly one",
        ):
            self._plan_continuation(
                project_id,
                str(fixture["audit_order"]),
                "openai:passed-audit-without-route-new-window",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_passed_audit_with_multiple_pending_reaudit_routes_is_not_resumable(
        self,
    ) -> None:
        fixture = self._passed_audit_fixture(
            "passed-audit-multiple-pending-reaudit"
        )
        pending = self._leave_prepared_reaudit_successor(fixture)
        project_id = str(fixture["project_id"])
        duplicate = copy.deepcopy(pending)
        for key in ("record_id", "project_id", "integrity_hmac"):
            duplicate.pop(key, None)
        duplicate_id = "HND-REAUDIT-CONTINUATION-DUPLICATE"
        duplicate["handoff_id"] = duplicate_id
        duplicate = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=duplicate_id,
            value=duplicate,
        )
        self.assertTrue(self.service.store.project_record_is_signed(duplicate))
        self.service._validate_schema_record(
            self.service._project_schema, "handoff_manifest", duplicate
        )

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation_state_invalid: passed audit requires exactly one",
        ):
            self._plan_continuation(
                project_id,
                str(fixture["audit_order"]),
                "openai:passed-audit-multiple-routes-new-window",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_execute_switches_session_and_replay_is_idempotent(
        self,
    ) -> None:
        project_id = self._project("production-window-continuation")
        old_session = "openai:production-old-window"
        new_session = "openai:production-new-window"
        work_order_id = self._begin_production(
            project_id, old_session, "production-root"
        )

        before_plan = self._project_tree_bytes(project_id)
        planned = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        self.assertEqual(planned.status, "planned")
        self.assertEqual(planned.work_order_id, work_order_id)
        self.assertEqual(planned.data["action"], "plan_role_continuation")
        self.assertEqual(planned.data["resume_work_order_id"], work_order_id)
        self.assertEqual(planned.data["role"], "production")
        self.assertTrue(planned.data["authority_root_id"])
        self.assertTrue(planned.data["authority_contract_sha256"])
        self.assertEqual(
            planned.data["confirmation_required"], CONTINUATION_CONFIRMATION
        )
        self.assertEqual(self._plan_generations(planned.data), (0, 1))
        self.assertEqual(
            self._project_tree_bytes(project_id),
            before_plan,
            "planning a continuation must be a zero-write operation",
        )

        executed = self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(planned.data["plan_sha256"]),
        )
        self.assertEqual(executed.status, "continued")
        self.assertEqual(executed.work_order_id, work_order_id)
        self.assertEqual(
            executed.data["authority_root_id"],
            planned.data["authority_root_id"],
        )
        self.assertEqual(executed.data["generation"], 1)
        self.assertEqual(executed.data["previous_generation"], 0)
        self.assertEqual(
            executed.data.get(
                "previous_status",
                executed.data.get("previous_epoch_status"),
            ),
            "revoked",
        )
        self.assertFalse(executed.data["idempotent_replay"])
        self.assertEqual(executed.data["session_binding_status"], "bound")

        draft = valid_storyboard()
        with self.assertRaisesRegex(
            ValueError,
            "window_session_revoked|window_authority_generation|window_session_mismatch",
        ):
            self.service.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=draft,
                scope_kind="scene",
                scope_id="EP01-SC01",
                request_session_id=old_session,
            )

        committed = self._commit(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=new_session,
            storyboard=draft,
            label="continued-production",
        )
        self.assertEqual(
            committed.status, service_module.PRODUCTION_PREFLIGHT_PASS
        )

        before_replay = self._project_tree_bytes(project_id)
        replayed = self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(planned.data["plan_sha256"]),
        )
        self.assertEqual(replayed.status, "continued")
        self.assertTrue(replayed.data["idempotent_replay"])
        self.assertEqual(
            replayed.data["continuation_id"],
            executed.data["continuation_id"],
        )
        self.assertEqual(
            replayed.data["authority_epoch_id"],
            executed.data["authority_epoch_id"],
        )
        self.assertEqual(self._project_tree_bytes(project_id), before_replay)

    def test_two_new_sessions_have_one_continuation_winner(self) -> None:
        project_id = self._project("concurrent-window-continuation")
        old_session = "openai:concurrent-old-window"
        work_order_id = self._begin_production(
            project_id, old_session, "concurrent-root"
        )
        contenders = (
            "openai:concurrent-new-window-a",
            "openai:concurrent-new-window-b",
        )
        plans = {
            session: self._plan_continuation(
                project_id, work_order_id, session
            )
            for session in contenders
        }
        barrier = threading.Barrier(len(contenders))

        def compete(session_id: str) -> tuple[str, str, Any]:
            barrier.wait(timeout=10)
            try:
                response = self._execute_continuation(
                    project_id,
                    work_order_id,
                    session_id,
                    str(plans[session_id].data["plan_sha256"]),
                )
                return "success", session_id, response
            except ValueError as exc:
                return "error", session_id, str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(compete, contenders))

        successes = [row for row in outcomes if row[0] == "success"]
        failures = [row for row in outcomes if row[0] == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(failures), 1, outcomes)
        self.assertRegex(
            str(failures[0][2]),
            "(?:role|authority)_continuation.*(?:stale|conflict)|authority.*generation",
        )
        winning_session = successes[0][1]
        losing_session = failures[0][1]
        self.assertEqual(successes[0][2].data["generation"], 1)

        draft = valid_storyboard()
        scene_id = str(
            next(iter(service_module._scene_scope_groups(draft)))[
                "stable_scene_id"
            ]
        )
        accepted = self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_kind="scene",
            scope_id=scene_id,
            request_session_id=winning_session,
        )
        self.assertEqual(accepted.status, service_module.DRAFT_PREFLIGHT_PASS)
        before_loser = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "window_session_revoked|window_authority_generation|window_session_mismatch",
        ):
            self.service.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=draft,
                scope_kind="scene",
                scope_id=scene_id,
                request_session_id=losing_session,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before_loser)

    def test_tamper_cross_role_cross_project_and_same_session_fail_zero_write(
        self,
    ) -> None:
        project_id = self._project("continuation-negative-matrix")
        other_project_id = self._project("continuation-negative-other-project")
        old_session = "openai:negative-old-window"
        new_session = "openai:negative-new-window"
        work_order_id = self._begin_production(
            project_id, old_session, "negative-root"
        )
        planned = self._plan_continuation(
            project_id, work_order_id, new_session
        )

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation.*(?:plan|hash|stale)|expected_plan_sha256",
        ):
            self._execute_continuation(
                project_id,
                work_order_id,
                new_session,
                "0" * 64,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation.*confirmation|confirmation.*invalid",
        ):
            self._continuation_request(
                project_id,
                resume_work_order_id=work_order_id,
                new_session_id=new_session,
                action="execute_role_continuation",
                expected_plan_sha256=str(planned.data["plan_sha256"]),
                confirmation="TAMPERED_CONFIRMATION",
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation.*(?:same|new).*session|previous.*session",
        ):
            self._plan_continuation(project_id, work_order_id, old_session)
        self.assertEqual(self._project_tree_bytes(project_id), before)

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation.*(?:contract|role|operation)|window_role_matrix_mismatch",
        ):
            self._continuation_request(
                project_id,
                resume_work_order_id=work_order_id,
                new_session_id=new_session,
                action="plan_role_continuation",
                overrides={
                    "operation": "storyboard_audit",
                    "mode": "audit_only",
                    "authorization": "read_only",
                },
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

        before_source = self._project_tree_bytes(project_id)
        before_other = self._project_tree_bytes(other_project_id)
        with self.assertRaisesRegex(
            ValueError,
            "unknown|not found|work_order|project.*mismatch",
        ):
            self._continuation_request(
                other_project_id,
                resume_work_order_id=work_order_id,
                new_session_id="openai:cross-project-window",
                action="plan_role_continuation",
                contract_project_id=project_id,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before_source)
        self.assertEqual(
            self._project_tree_bytes(other_project_id), before_other
        )

        executed = self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(planned.data["plan_sha256"]),
        )
        self.assertEqual(executed.status, "continued")

        audit_order = self._begin(
            project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            session_id="openai:separate-audit-window",
            targets=["ART-not-yet-created"],
            label="separate-audit-role",
        )
        with self.assertRaisesRegex(
            ValueError,
            "role_continuation.*(?:same|separation)|independent_audit_separation_required",
        ):
            self._plan_continuation(project_id, audit_order, new_session)

    def test_status_projection_does_not_disclose_session_or_token_material(
        self,
    ) -> None:
        project_id = self._project("continuation-status-redaction")
        old_session = "openai:status-secret-old-window"
        new_session = "openai:status-secret-new-window"
        work_order_id = self._begin_production(
            project_id, old_session, "status-root"
        )
        plan = self._plan_continuation(
            project_id, work_order_id, new_session
        )
        self._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(plan.data["plan_sha256"]),
        )

        status = self.service.agent_status(
            self.namespace,
            project_id,
            full_self_test=False,
            request_session_id=new_session,
        )
        projection = {
            "supported": status.data["role_continuation_supported"],
            "resumable_authorities": status.data["resumable_authorities"],
            "current_window_status": status.data["current_window_status"],
            "current_window_roles": status.data["current_window_roles"],
            "required_next_role": status.data["required_next_role"],
        }
        self.assertTrue(projection["supported"])
        serialized = json.dumps(
            projection, ensure_ascii=False, sort_keys=True
        )
        self.assertNotIn(old_session, serialized)
        self.assertNotIn(new_session, serialized)

        forbidden_keys = {
            "transport_session_fingerprint",
            "from_session_fingerprint",
            "new_session_fingerprint",
            "previous_session_fingerprint",
            "claim_token",
            "continuation_token",
            "integrity_hmac",
        }

        def keys(value: Any) -> set[str]:
            if isinstance(value, dict):
                found = set(value)
                for nested in value.values():
                    found.update(keys(nested))
                return found
            if isinstance(value, list):
                found: set[str] = set()
                for nested in value:
                    found.update(keys(nested))
                return found
            return set()

        self.assertTrue(forbidden_keys.isdisjoint(keys(projection)))
        current = projection["resumable_authorities"]
        authority = next(
            row
            for row in current
            if row.get("work_order_id") == work_order_id
        )
        self.assertEqual(authority["role"], "production")
        self.assertEqual(authority["active_generation"], 1)
        self.assertEqual(authority["current_window_status"], "active")
        self.assertFalse(authority["resumable"])

    def test_repair_returns_to_current_generation_of_original_audit_root(
        self,
    ) -> None:
        project_id = self._project("repair-return-audit-continuation")
        production_session = "openai:repair-chain-production"
        old_audit_session = "openai:repair-chain-audit-old"
        new_audit_session = "openai:repair-chain-audit-new"
        repair_session = "openai:repair-chain-repair"

        production_order = self._begin_production(
            project_id, production_session, "repair-chain-production"
        )
        broken = valid_storyboard()
        broken["source_lock"]["上集终点"] += "【repair-continuation】"
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        committed = self._commit(
            project_id=project_id,
            work_order_id=production_order,
            session_id=production_session,
            storyboard=broken,
            label="repair-chain-broken",
        )
        artifact_id = str(committed.artifact_id)
        root_handoff_id = str(committed.data["handoff"]["handoff_id"])
        audit_order = self._begin_audit(
            project_id,
            artifact_id,
            old_audit_session,
            "repair-chain-audit-root",
        )
        failed_semantic = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
            shot_id=str(broken["shots"][0]["镜头编号"]),
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认右侧窗光与锁定的左侧自然光事实冲突。",
            repair_action="仅修正该镜光源来源后返回原独立审计权限复核。",
        )
        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=root_handoff_id,
            semantic_audit=failed_semantic,
            request_session_id=old_audit_session,
        )
        self.assertEqual(failed.status, "fail_current")
        package_id = str(failed.data["repair_package"]["repair_package_id"])
        package_before_continuation = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )

        plan = self._plan_continuation(
            project_id, audit_order, new_audit_session
        )
        continued = self._execute_continuation(
            project_id,
            audit_order,
            new_audit_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(continued.status, "continued")
        self.assertEqual(continued.data["generation"], 1)
        self.assertEqual(
            self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="repair_packages",
                record_id=package_id,
            ),
            package_before_continuation,
            "rotating the audit window must not rewrite its historical repair package",
        )

        shot_id = str(broken["shots"][0]["镜头编号"])
        repair_order = self._begin_repair(
            project_id, repair_session, shot_id, "repair-chain-repair"
        )
        repaired = self.service.commit_storyboard_patch(
            self.namespace,
            project_id=project_id,
            work_order_id=repair_order,
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
            file_name="repair-chain-fixed.xlsx",
            request_session_id=repair_session,
        )
        repaired_artifact_id = str(repaired.artifact_id)
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
        self.assertEqual(
            repair_handoff["return_to_audit_work_order_id"], audit_order
        )

        unrelated_audit_session = "openai:repair-chain-unrelated-audit"
        unrelated_audit_order = self._begin_audit(
            project_id,
            repaired_artifact_id,
            unrelated_audit_session,
            "repair-chain-unrelated-audit",
        )
        before_unrelated = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "repair_return_(?:authority|audit_mismatch)|audit_return_binding|"
            "handoff.*authority|original audit",
        ):
            self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=repaired_artifact_id,
                audit_cycle_id=None,
                work_order_id=unrelated_audit_order,
                handoff_artifact_id=repair_handoff_id,
                semantic_audit=semantic_audit_for_artifact(
                    self.service,
                    self.settings,
                    project_id,
                    repaired_artifact_id,
                ),
                request_session_id=unrelated_audit_session,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before_unrelated)

        passed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=repaired_artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_order,
            handoff_artifact_id=repair_handoff_id,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                repaired_artifact_id,
            ),
            request_session_id=new_audit_session,
        )
        self.assertEqual(passed.status, "pass")
        self.assertEqual(
            passed.data["certificate"]["work_order_id"], audit_order
        )

        with self.assertRaisesRegex(
            ValueError,
            "window_session_revoked|window_authority_generation|window_session_mismatch",
        ):
            self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=repaired_artifact_id,
                audit_cycle_id=None,
                work_order_id=audit_order,
                handoff_artifact_id=repair_handoff_id,
                semantic_audit=semantic_audit_for_artifact(
                    self.service,
                    self.settings,
                    project_id,
                    repaired_artifact_id,
                ),
                request_session_id=old_audit_session,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
