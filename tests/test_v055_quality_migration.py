from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jsonschema import Draft202012Validator
from starlette.testclient import TestClient

import shortdrama_mcp.service as service_module
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.storage import ProjectStore

from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    semantic_audit_for_artifact,
)
from test_efficiency import valid_storyboard


class V055QualityAndMigrationTests(unittest.TestCase):
    """Executable contracts for the v0.5.5 prevention-first release."""

    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v055-quality-")
        self.settings = Settings(
            data_dir=Path(self.temp.name),
            public_base_url="http://127.0.0.1:8765",
            host="127.0.0.1",
            port=8765,
            deployment_mode="local",
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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _project(self, label: str) -> str:
        project_id = str(
            self.service.create_project(self.namespace, label).project_id
        )
        status = self.service.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(status.data.get("runtime_test_certificate_valid"))
        return project_id

    def _begin(
        self,
        project_id: str,
        *,
        operation: str,
        label: str,
    ) -> tuple[str, str]:
        session_id = f"v055-{label}"
        response = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation=operation,
            mode="execute",
            authorization="explicit_execute",
            request_text=f"v0.5.5 quality/migration contract: {label}",
            targets=["第一集"],
            authoritative_source="测试剧本",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["剧情、对白、历史产物与审计证据"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"] if operation == "storyboard_create" else ["规则候选"],
            window_key=session_id,
            request_session_id=session_id,
        )
        return str(response.work_order_id), session_id

    def _begin_audit(
        self, project_id: str, artifact_id: str, *, label: str
    ) -> tuple[str, str]:
        session_id = f"v055-audit-{label}"
        response = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            request_text=f"v0.5.5 independent audit: {label}",
            targets=[artifact_id],
            authoritative_source="已锁定正式分镜与生产handoff",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["只读独立审计"],
            must_remove=[],
            protected_scope=["全部源内容"],
            expected_outputs=["independent_audit_certificate"],
            window_key=session_id,
            request_session_id=session_id,
        )
        return str(response.work_order_id), session_id

    @staticmethod
    def _make_legacy_state(service: Any, namespace: str, project_id: str) -> None:
        hub = service.store.load_hub(namespace, project_id)
        ledger = service.store.load_ledger(namespace, project_id)
        hub["policy"]["compact_master_version"] = "0.5.4"
        for field in (
            "scene_draft_preflight_required",
            "failed_draft_learning_required",
            "first_draft_correctness_threshold_percent",
            "first_draft_direct_usability_threshold_percent",
            "max_new_local_issue_rate_percent",
            "minimum_episode_correctness_percent",
            "known_issue_recurrence_tolerance",
            "systemic_root_cause_tolerance",
        ):
            hub["policy"].pop(field, None)
        for field in (
            "production_quality_block",
            "latest_first_draft_quality",
            "active_prevention_profile_sha256",
        ):
            hub["project_state"].pop(field, None)
        for field in (
            "cross_batch_pattern_identity_required",
            "failed_draft_recording_required",
            "first_draft_correctness_threshold_percent",
            "first_draft_direct_usability_threshold_percent",
            "max_new_local_issue_rate_percent",
            "minimum_episode_correctness_percent",
            "hard_major_known_systemic_tolerance",
        ):
            ledger["policy"].pop(field, None)
        service.store.save_hub(namespace, project_id, hub)
        service.store.save_ledger(namespace, project_id, ledger)

    def _project_tree_bytes(self, project_id: str) -> dict[str, bytes]:
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        return {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }

    def _save_prepared_migration(
        self,
        *,
        project_id: str,
        work_order_id: str,
        migration_id: str,
        timestamp: str = "2026-08-31T12:00:00Z",
    ) -> dict[str, Any]:
        hub_before = self.service.store.load_hub(
            self.namespace, project_id
        )
        ledger_before = self.service.store.load_ledger(
            self.namespace, project_id
        )
        hub_after, hub_changes = (
            service_module.project_agent.migrate_hub_to_current(
                hub_before,
                timestamp=timestamp,
                migration_id=migration_id,
            )
        )
        ledger_after, ledger_changes = (
            service_module.storyboard_agent.migrate_ledger_to_current(
                ledger_before,
                timestamp=timestamp,
                migration_id=migration_id,
            )
        )
        migration_key = self.service._project_migration_key(
            project_id=project_id,
            work_order_id=work_order_id,
            from_version="0.5.4",
            target_version="0.5.5",
        )
        runtime_certificate = self.service._require_runtime_test_certificate(
            self.namespace, project_id
        )
        return self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
            value={
                "record_id": migration_id,
                "migration_id": migration_id,
                "migration_key": migration_key,
                "project_id": project_id,
                "work_order_id": work_order_id,
                "status": "prepared",
                "created_at": timestamp,
                "from_version": "0.5.4",
                "target_version": "0.5.5",
                "hub_before_sha256": (
                    self.service.store.project_state_sha256(hub_before)
                ),
                "ledger_before_sha256": (
                    self.service.store.project_state_sha256(ledger_before)
                ),
                "hub_after_sha256": (
                    self.service.store.project_state_sha256(hub_after)
                ),
                "ledger_after_sha256": (
                    self.service.store.project_state_sha256(ledger_after)
                ),
                "hub_before": hub_before,
                "ledger_before": ledger_before,
                "hub_after": hub_after,
                "ledger_after": ledger_after,
                "runtime_test_certificate_id": runtime_certificate[
                    "certificate_id"
                ],
                "ruleset_version": self.service._ruleset_version,
                "validator_version": self.service._validator_version(),
                "changed_paths": hub_changes + ledger_changes,
                "preserves_artifacts": True,
            },
        )

    def _assert_no_storyboard_publication(self, project_id: str) -> None:
        self.assertEqual(
            self.service.store.list_artifacts(self.namespace, project_id), []
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            ),
            [],
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertEqual(hub.get("deliverables", []), [])
        self.assertNotIn("storyboard", hub["project_state"]["latest_artifacts"])

    def _rewrite_signed_migration_fixture(
        self,
        project_id: str,
        migration_id: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """Test-only raw rewrite for an already fenced migration journal."""

        rewritten = copy.deepcopy(value)
        rewritten["record_id"] = migration_id
        rewritten["migration_id"] = migration_id
        rewritten["project_id"] = project_id
        rewritten["integrity_hmac"] = (
            self.service.store._record_integrity_hmac(rewritten)
        )
        path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "migrations"
            / f"{migration_id}.json"
        )
        self.service.store.atomic_write_json(path, rewritten)
        return self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
        )

    def _preflight_full_with_scene_coverage(
        self,
        *,
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        scope_id: str,
        request_session_id: str,
        service: Any | None = None,
    ) -> Any:
        active_service = service or self.service
        for group in service_module._scene_scope_groups(storyboard):
            scene_aliases = list(group.get("scene_aliases") or [])
            scene_scope_id = str(
                scene_aliases[0] if scene_aliases else group["stable_scene_id"]
            )
            scene = active_service.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=storyboard,
                scope_kind="scene",
                scope_id=scene_scope_id,
                request_session_id=request_session_id,
            )
            self.assertEqual(scene.status, service_module.DRAFT_PREFLIGHT_PASS)
            self.assertFalse(scene.data["commit_ready"])
        return active_service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            scope_kind="full",
            scope_id=scope_id,
            request_session_id=request_session_id,
        )

    def test_status_without_valid_runtime_certificate_is_self_test_only(self) -> None:
        project_id = str(
            self.service.create_project(
                self.namespace, "runtime-certificate-status-gate"
            ).project_id
        )

        missing = self.service.agent_status(
            self.namespace, project_id, full_self_test=False
        )
        self.assertEqual(missing.status, "action_required")
        self.assertFalse(missing.data["runtime_test_certificate_valid"])
        self.assertFalse(missing.data["execution_available"])
        self.assertEqual(missing.data["execution_mode"], "self_test_only")

        certified = self.service.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        certificate = certified.data["runtime_test_certificate"]
        self.assertTrue(certified.data["runtime_test_certificate_valid"])
        certificate_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "certificates"
            / f"{certificate['certificate_id']}.json"
        )
        tampered = json.loads(certificate_path.read_text(encoding="utf-8"))
        tampered["integrity_hmac"] = "0" * 64
        self.service.store.atomic_write_json(certificate_path, tampered)

        invalid = self.service.agent_status(
            self.namespace, project_id, full_self_test=False
        )
        self.assertEqual(invalid.status, "action_required")
        self.assertFalse(invalid.data["runtime_test_certificate_valid"])
        self.assertFalse(invalid.data["execution_available"])
        self.assertEqual(invalid.data["execution_mode"], "self_test_only")
        with self.assertRaisesRegex(ValueError, "runtime_test_certificate_required"):
            self.service._require_runtime_test_certificate(
                self.namespace, project_id
            )

    def test_runtime_certificate_sequence_orders_same_second_pass_and_fail(
        self,
    ) -> None:
        project_id = self._project("runtime-certificate-total-order")
        current = self.service._require_runtime_test_certificate(
            self.namespace, project_id
        )
        current_sequence = int(current["certificate_sequence"])
        report_base = {
            "version": "0.5.9",
            "workflow_revision": "R10-PG1",
        }
        with patch(
            "shortdrama_mcp.storage.utc_stamp",
            return_value="2026-08-31T15:00:00Z",
        ):
            newer_pass = self.service._persist_runtime_certificate(
                self.namespace,
                project_id,
                {**report_base, "status": "pass"},
            )
            newest_fail = self.service._persist_runtime_certificate(
                self.namespace,
                project_id,
                {**report_base, "status": "fail_current"},
            )
        self.assertEqual(
            newer_pass["certificate_sequence"], current_sequence + 1
        )
        self.assertEqual(
            newest_fail["certificate_sequence"], current_sequence + 2
        )
        self.assertEqual(newer_pass["created_at"], newest_fail["created_at"])
        self.assertEqual(
            self.service.store.latest_runtime_certificate(
                self.namespace,
                project_id,
                certificate_key=self.service._runtime_certificate_key(),
            )["certificate_id"],
            newest_fail["certificate_id"],
        )
        self.assertIsNone(
            self.service._matching_runtime_certificate(
                self.namespace, project_id
            )
        )

        with patch(
            "shortdrama_mcp.storage.utc_stamp",
            return_value="2026-08-31T15:00:00Z",
        ):
            recovered_pass = self.service._persist_runtime_certificate(
                self.namespace,
                project_id,
                {**report_base, "status": "pass"},
            )
        self.assertEqual(
            recovered_pass["certificate_sequence"], current_sequence + 3
        )
        self.assertEqual(
            self.service._require_runtime_test_certificate(
                self.namespace, project_id
            )["certificate_id"],
            recovered_pass["certificate_id"],
        )

    def test_ambiguous_legacy_runtime_head_is_superseded_by_signed_sequence(
        self,
    ) -> None:
        project_id = self._project("runtime-certificate-legacy-order")
        current = self.service._require_runtime_test_certificate(
            self.namespace, project_id
        )
        certificate_dir = (
            self.service.store.project_dir(self.namespace, project_id)
            / "certificates"
        )
        legacy_a = copy.deepcopy(current)
        legacy_a.pop("certificate_sequence", None)
        legacy_a["created_at"] = "2026-08-31T15:30:00Z"
        legacy_a["integrity_hmac"] = (
            self.service.store._record_integrity_hmac(legacy_a)
        )
        self.service.store.atomic_write_json(
            certificate_dir / f"{legacy_a['certificate_id']}.json",
            legacy_a,
        )
        legacy_b = copy.deepcopy(legacy_a)
        legacy_b_id = "CERT-legacy-same-second-b"
        legacy_b["record_id"] = legacy_b_id
        legacy_b["certificate_id"] = legacy_b_id
        legacy_b["integrity_hmac"] = (
            self.service.store._record_integrity_hmac(legacy_b)
        )
        self.service.store.atomic_write_json(
            certificate_dir / f"{legacy_b_id}.json",
            legacy_b,
        )
        with self.assertRaisesRegex(
            ValueError, "runtime_test_certificate_required"
        ):
            self.service._require_runtime_test_certificate(
                self.namespace, project_id
            )

        recovered = self.service.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(recovered.data["runtime_test_certificate_valid"])
        latest = recovered.data["runtime_test_certificate"]
        self.assertEqual(latest["certificate_sequence"], 1)
        self.assertNotIn(
            latest["certificate_id"],
            {legacy_a["certificate_id"], legacy_b_id},
        )

    def test_ruleset_digest_covers_markdown_and_jsonl_and_invalidates_old_authority(self) -> None:
        original_skill_root = service_module.SKILL_ROOT
        for index, relative_path in enumerate(
            (
                Path("references/strict_prompt_contract.md"),
                Path("references/atomic_rule_registry.jsonl"),
            ),
            start=1,
        ):
            with self.subTest(rule_resource=str(relative_path)):
                case_root = Path(self.temp.name) / f"ruleset-digest-{index}"
                skill_copy = case_root / "shortdrama-storyboard"
                shutil.copytree(original_skill_root, skill_copy)
                case_settings = Settings(
                    data_dir=case_root / "data",
                    public_base_url="http://127.0.0.1:8765",
                    host="127.0.0.1",
                    port=8765,
                    deployment_mode="local",
                    auth_mode="none",
                    oauth_issuer=None,
                    oauth_audience=None,
                    oauth_jwks_url=None,
                    oauth_scopes=("storyboard:read", "storyboard:write"),
                    allow_local_file_urls=False,
                    max_file_bytes=50 * 1024 * 1024,
                )
                with (
                    patch.object(service_module, "SKILL_ROOT", skill_copy),
                    patch.object(
                        service_module, "SCRIPTS_DIR", skill_copy / "scripts"
                    ),
                ):
                    _server, before_service = create_mcp(case_settings)
                    from historical_entry_fixture import freeze_legacy_entry
                    freeze_legacy_entry(before_service)
                    project_id = str(
                        before_service.create_project(
                            self.namespace, f"ruleset-digest-{index}"
                        ).project_id
                    )
                    certified = before_service.agent_status(
                        self.namespace, project_id, full_self_test=True
                    )
                    old_certificate_id = str(
                        certified.data["runtime_test_certificate"][
                            "certificate_id"
                        ]
                    )
                    old_ruleset_version = before_service._ruleset_version
                    session_id = f"ruleset-digest-session-{index}"
                    task = before_service.begin_task(
                        self.namespace,
                        project_id=project_id,
                        operation="storyboard_create",
                        mode="execute",
                        authorization="explicit_execute",
                        request_text="验证规则资源变更会使旧授权失效",
                        targets=["第一集"],
                        authoritative_source="测试剧本",
                        latest_version="v1",
                        target_boundary="第一集",
                        must_keep=["剧情与对白"],
                        must_remove=[],
                        protected_scope=[],
                        expected_outputs=["正式分镜"],
                        window_key=session_id,
                        request_session_id=session_id,
                    )
                    draft = valid_storyboard()
                    preflight = self._preflight_full_with_scene_coverage(
                        project_id=project_id,
                        work_order_id=str(task.work_order_id),
                        storyboard=draft,
                        scope_id="EP01",
                        request_session_id=session_id,
                        service=before_service,
                    )
                    self.assertEqual(
                        preflight.status, service_module.DRAFT_PREFLIGHT_PASS
                    )

                    target = skill_copy / relative_path
                    target.write_bytes(target.read_bytes() + b"\n")
                    _server, after_service = create_mcp(case_settings)
                    from historical_entry_fixture import freeze_legacy_entry
                    freeze_legacy_entry(after_service)
                    self.assertNotEqual(
                        after_service._ruleset_version, old_ruleset_version
                    )
                    self.assertIsNone(
                        after_service._matching_runtime_certificate(
                            self.namespace, project_id
                        )
                    )
                    invalidated = after_service.agent_status(
                        self.namespace, project_id, full_self_test=False
                    )
                    self.assertEqual(invalidated.status, "action_required")
                    self.assertFalse(
                        invalidated.data["runtime_test_certificate_valid"]
                    )
                    self.assertFalse(invalidated.data["execution_available"])
                    self.assertEqual(
                        invalidated.data["execution_mode"], "self_test_only"
                    )
                    with self.assertRaisesRegex(
                        ValueError, "runtime_test_certificate_required"
                    ):
                        after_service.commit_storyboard(
                            self.namespace,
                            project_id=project_id,
                            work_order_id=str(task.work_order_id),
                            storyboard=draft,
                            file_name="旧规则预检不得提交.xlsx",
                            source_artifact_id=None,
                            request_session_id=session_id,
                            draft_preflight_id=str(preflight.trace_id),
                        )

                    recertified = after_service.agent_status(
                        self.namespace, project_id, full_self_test=True
                    )
                    new_certificate_id = str(
                        recertified.data["runtime_test_certificate"][
                            "certificate_id"
                        ]
                    )
                    self.assertNotEqual(new_certificate_id, old_certificate_id)
                    with self.assertRaisesRegex(
                        ValueError,
                        "draft_preflight_binding_mismatch:.*(rules|runtime_certificate)",
                    ):
                        after_service.commit_storyboard(
                            self.namespace,
                            project_id=project_id,
                            work_order_id=str(task.work_order_id),
                            storyboard=draft,
                            file_name="旧规则预检不得提交.xlsx",
                            source_artifact_id=None,
                            request_session_id=session_id,
                            draft_preflight_id=str(preflight.trace_id),
                        )
                    self.assertEqual(
                        after_service.store.list_artifacts(
                            self.namespace, project_id
                        ),
                        [],
                    )

    def test_binary_store_secrets_are_restart_stable_at_whitespace_boundaries(
        self,
    ) -> None:
        root = Path(self.temp.name) / "binary-secret-restart"
        download_secret = b" " + (b"d" * 46) + b"\n"
        integrity_secret = b"\t" + (b"i" * 46) + b"\r"
        with patch(
            "shortdrama_mcp.storage.secrets.token_bytes",
            side_effect=(download_secret, integrity_secret, b"e" * 16),
        ):
            before = ProjectStore(root, "http://127.0.0.1:8765")

        signed_payload = {"record_id": "REC-restart-boundary", "value": 1}
        before_signature = before._record_integrity_hmac(signed_payload)
        artifact = before.save_artifact_json(
            self.namespace,
            "PRJ-secret-restart",
            {"restart": "stable"},
            artifact_id="ART-secret-restart",
            file_name="restart.json",
            artifact_type="test_fixture",
            version="v1",
        )
        download_url = before.signed_download_url(
            self.namespace,
            "PRJ-secret-restart",
            str(artifact["artifact_id"]),
            str(artifact["file_name"]),
        )
        download_token = download_url.split("/files/", 1)[1].split("/", 1)[0]
        restarted = ProjectStore(root, "http://127.0.0.1:8765")

        self.assertEqual(before._secret, download_secret)
        self.assertEqual(restarted._secret, download_secret)
        self.assertEqual(before._integrity_secret, integrity_secret)
        self.assertEqual(restarted._integrity_secret, integrity_secret)
        self.assertEqual(
            restarted._record_integrity_hmac(signed_payload), before_signature
        )
        restarted_record, restarted_path = restarted.resolve_download_token(
            download_token
        )
        self.assertEqual(restarted_record["artifact_id"], artifact["artifact_id"])
        self.assertEqual(
            json.loads(restarted_path.read_text(encoding="utf-8")),
            {"restart": "stable"},
        )

    def test_legacy_trimmed_secret_signatures_survive_055_upgrade(self) -> None:
        root = Path(self.temp.name) / "legacy-trimmed-secret-upgrade"
        root.mkdir(parents=True, exist_ok=True)
        download_secret = b" " + (b"d" * 46) + b"\n"
        integrity_secret = b"\t" + (b"i" * 46) + b"\r"
        (root / ".download_secret").write_bytes(download_secret)
        (root / ".record_integrity_secret").write_bytes(integrity_secret)
        store = ProjectStore(root, "http://127.0.0.1:8765")

        legacy_record = {"record_id": "REC-legacy-trimmed", "value": 1}
        legacy_record["integrity_hmac"] = (
            store._record_integrity_hmac_with_secret(
                legacy_record, integrity_secret.strip()
            )
        )
        store._verify_record_integrity(
            legacy_record, record_id="REC-legacy-trimmed"
        )
        self.assertTrue(store.project_record_is_signed(legacy_record))
        self.assertNotEqual(
            store._record_integrity_hmac(legacy_record),
            legacy_record["integrity_hmac"],
        )

        artifact = store.save_artifact_json(
            self.namespace,
            "PRJ-legacy-secret-upgrade",
            {"legacy": True},
            artifact_id="ART-legacy-secret-upgrade",
            file_name="legacy.json",
            artifact_type="test_fixture",
            version="v1",
        )
        legacy_value = {
            "namespace_key": store.namespace_key(self.namespace),
            "project_id": "PRJ-legacy-secret-upgrade",
            "artifact_id": str(artifact["artifact_id"]),
            "exp": int(time.time()) + 3_600,
        }
        payload = json.dumps(
            legacy_value,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        legacy_signature = hmac.new(
            download_secret.strip(), payload, hashlib.sha256
        ).digest()
        legacy_token = ".".join(
            base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
            for part in (payload, legacy_signature)
        )
        resolved_record, resolved_path = store.resolve_download_token(legacy_token)
        self.assertEqual(resolved_record["artifact_id"], artifact["artifact_id"])
        self.assertEqual(
            json.loads(resolved_path.read_text(encoding="utf-8")),
            {"legacy": True},
        )

        far_future_value = dict(legacy_value)
        far_future_value["exp"] = store._legacy_download_token_accept_until + 1
        far_future_payload = json.dumps(
            far_future_value, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        far_future_signature = hmac.new(
            download_secret.strip(), far_future_payload, hashlib.sha256
        ).digest()
        far_future_token = ".".join(
            base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
            for part in (far_future_payload, far_future_signature)
        )
        with self.assertRaisesRegex(ValueError, "invalid or expired download token"):
            store.resolve_download_token(far_future_token)

        now = int(time.time())
        forged_v2_value = {
            **legacy_value,
            "sig_v": 2,
            "iat": now,
            "exp": now + 3_600,
            "fingerprint": artifact["fingerprint"],
            "size_bytes": artifact["size_bytes"],
            "file_name": artifact["file_name"],
        }
        forged_v2_payload = json.dumps(
            forged_v2_value, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        forged_v2_signature = hmac.new(
            download_secret.strip(), forged_v2_payload, hashlib.sha256
        ).digest()
        forged_v2_token = ".".join(
            base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
            for part in (forged_v2_payload, forged_v2_signature)
        )
        with self.assertRaisesRegex(ValueError, "invalid or expired download token"):
            store.resolve_download_token(forged_v2_token)

        new_url = store.signed_download_url(
            self.namespace,
            "PRJ-legacy-secret-upgrade",
            str(artifact["artifact_id"]),
            str(artifact["file_name"]),
        )
        new_token = new_url.split("/files/", 1)[1].split("/", 1)[0]
        restarted = ProjectStore(root, "http://127.0.0.1:8765")
        self.assertEqual(
            restarted._legacy_download_token_accept_until,
            store._legacy_download_token_accept_until,
        )
        self.assertEqual(
            restarted.resolve_download_token(new_token)[0]["artifact_id"],
            artifact["artifact_id"],
        )

        window_path = root / ".legacy_download_token_window.json"
        sentinel_path = root / ".v055_security_epoch.json"
        anchor_path = root / "projects" / ".v055_security_epoch.anchor.json"
        expired_window = store.read_json(window_path)
        expired_sentinel = store.read_json(sentinel_path)
        expired_anchor = store.read_json(anchor_path)
        expired_window["accept_until_epoch"] = int(time.time()) - 1
        expired_sentinel["accept_until_epoch"] = expired_window[
            "accept_until_epoch"
        ]
        expired_anchor["accept_until_epoch"] = expired_window[
            "accept_until_epoch"
        ]
        expired_window["integrity_hmac"] = (
            store._record_integrity_hmac_with_secret(
                expired_window, integrity_secret
            )
        )
        expired_sentinel["integrity_hmac"] = (
            store._record_integrity_hmac_with_secret(
                expired_sentinel, integrity_secret
            )
        )
        expired_anchor["integrity_hmac"] = (
            store._record_integrity_hmac_with_secret(
                expired_anchor, integrity_secret
            )
        )
        store.atomic_write_json(window_path, expired_window)
        store.atomic_write_json(sentinel_path, expired_sentinel)
        store.atomic_write_json(anchor_path, expired_anchor)
        after_cutoff = ProjectStore(root, "http://127.0.0.1:8765")
        with self.assertRaisesRegex(ValueError, "invalid or expired download token"):
            after_cutoff.resolve_download_token(legacy_token)
        self.assertEqual(
            after_cutoff.resolve_download_token(new_token)[0]["artifact_id"],
            artifact["artifact_id"],
        )
        window_path.unlink()
        with self.assertRaisesRegex(
            RuntimeError, "legacy_download_token_window_missing"
        ):
            ProjectStore(root, "http://127.0.0.1:8765")

    def test_legacy_window_recovers_only_in_preparing_transaction(self) -> None:
        root = Path(self.temp.name) / "legacy-window-preparing-recovery"
        store = ProjectStore(root, "http://127.0.0.1:8765")
        cutoff = store._legacy_download_token_accept_until
        sentinel_path = root / ".v055_security_epoch.json"
        window_path = root / ".legacy_download_token_window.json"
        anchor_path = root / "projects" / ".v055_security_epoch.anchor.json"
        sentinel = store.read_json(sentinel_path)
        anchor = store.read_json(anchor_path)
        sentinel["status"] = "preparing"
        sentinel.pop("committed_at", None)
        sentinel["integrity_hmac"] = store._record_integrity_hmac_with_secret(
            sentinel, store._integrity_secret
        )
        anchor["status"] = "preparing"
        anchor.pop("committed_at", None)
        anchor["integrity_hmac"] = store._record_integrity_hmac_with_secret(
            anchor, store._integrity_secret
        )
        store.atomic_write_json(sentinel_path, sentinel)
        store.atomic_write_json(anchor_path, anchor)
        window_path.unlink()

        recovered = ProjectStore(root, "http://127.0.0.1:8765")
        self.assertEqual(recovered._legacy_download_token_accept_until, cutoff)
        self.assertTrue(window_path.is_file())
        recovered_sentinel = recovered.read_json(sentinel_path)
        recovered_window = recovered.read_json(window_path)
        recovered_anchor = recovered.read_json(anchor_path)
        self.assertEqual(recovered_sentinel["status"], "committed")
        self.assertEqual(recovered_anchor["status"], "committed")
        self.assertEqual(recovered_window["status"], "active")
        for record in (recovered_sentinel, recovered_window, recovered_anchor):
            self.assertEqual(record["accept_until_epoch"], cutoff)
            self.assertEqual(record["epoch_id"], recovered_sentinel["epoch_id"])

    def test_late_first_upgrade_gets_its_own_absolute_legacy_window(self) -> None:
        root = Path(self.temp.name) / "late-first-v055-upgrade"
        root.mkdir(parents=True, exist_ok=True)
        download_secret = b" " + (b"d" * 46) + b"\n"
        integrity_secret = b"\t" + (b"i" * 46) + b"\r"
        (root / ".download_secret").write_bytes(download_secret)
        (root / ".record_integrity_secret").write_bytes(integrity_secret)
        legacy_hub = root / "projects" / "legacy" / "PRJ-late" / "hub.json"
        legacy_hub.parent.mkdir(parents=True, exist_ok=True)
        legacy_hub.write_text(
            json.dumps({"policy": {"compact_master_version": "0.5.4"}}),
            encoding="utf-8",
        )
        late_start = 2_000_000_000
        with patch("shortdrama_mcp.storage.time.time", return_value=late_start):
            store = ProjectStore(root, "http://127.0.0.1:8765")
            self.assertEqual(
                store._legacy_download_token_accept_until,
                late_start + 86_400,
            )
            artifact = store.save_artifact_json(
                "legacy",
                "PRJ-late",
                {"late_upgrade": True},
                artifact_id="ART-late-upgrade",
                file_name="late.json",
                artifact_type="test_fixture",
                version="v1",
            )
            legacy_value = {
                "namespace_key": store.namespace_key("legacy"),
                "project_id": "PRJ-late",
                "artifact_id": artifact["artifact_id"],
                "exp": late_start + 3_600,
            }
            payload = json.dumps(
                legacy_value, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            signature = hmac.new(
                download_secret.strip(), payload, hashlib.sha256
            ).digest()
            token = ".".join(
                base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
                for part in (payload, signature)
            )
            self.assertEqual(
                store.resolve_download_token(token)[0]["artifact_id"],
                artifact["artifact_id"],
            )

    def test_unmigrated_v054_project_cannot_renew_deleted_security_epoch(
        self,
    ) -> None:
        root = Path(self.temp.name) / "unmigrated-v054-deleted-security-epoch"
        root.mkdir(parents=True, exist_ok=True)
        (root / ".download_secret").write_bytes(b"d" * 48)
        (root / ".record_integrity_secret").write_bytes(b"i" * 48)
        legacy_hub = root / "projects" / "legacy" / "PRJ-v054" / "hub.json"
        legacy_hub.parent.mkdir(parents=True, exist_ok=True)
        legacy_hub.write_text(
            json.dumps({"policy": {"compact_master_version": "0.5.4"}}),
            encoding="utf-8",
        )

        first_start = 2_100_000_000
        with patch("shortdrama_mcp.storage.time.time", return_value=first_start):
            first_store = ProjectStore(root, "http://127.0.0.1:8765")
        cutoff = first_store._legacy_download_token_accept_until
        self.assertEqual(cutoff, first_start + 86_400)

        sentinel_path = root / ".v055_security_epoch.json"
        window_path = root / ".legacy_download_token_window.json"
        anchor_path = root / "projects" / ".v055_security_epoch.anchor.json"
        anchor_before = first_store.read_json(anchor_path)
        self.assertEqual(anchor_before["status"], "committed")
        self.assertEqual(anchor_before["accept_until_epoch"], cutoff)
        sentinel_path.unlink()
        window_path.unlink()

        with patch(
            "shortdrama_mcp.storage.time.time", return_value=cutoff + 1
        ), self.assertRaisesRegex(
            RuntimeError, "v055_security_epoch_files_missing"
        ):
            ProjectStore(root, "http://127.0.0.1:8765")

        self.assertFalse(sentinel_path.exists())
        self.assertFalse(window_path.exists())
        anchor_after = first_store.read_json(anchor_path)
        self.assertEqual(anchor_after, anchor_before)
        self.assertEqual(anchor_after["accept_until_epoch"], cutoff)

    def test_v055_state_cannot_rebootstrap_deleted_security_epoch(self) -> None:
        root = Path(self.temp.name) / "deleted-v055-security-epoch"
        ProjectStore(root, "http://127.0.0.1:8765")
        hub_path = root / "projects" / "current" / "PRJ-v055" / "hub.json"
        hub_path.parent.mkdir(parents=True, exist_ok=True)
        hub_path.write_text(
            json.dumps({"policy": {"compact_master_version": "0.5.5"}}),
            encoding="utf-8",
        )
        (root / ".legacy_download_token_window.json").unlink()
        (root / ".v055_security_epoch.json").unlink()
        with self.assertRaisesRegex(
            RuntimeError, "v055_security_epoch_files_missing"
        ):
            ProjectStore(root, "http://127.0.0.1:8765")

    def test_legacy_window_rejects_trimmed_integrity_key_forgery(self) -> None:
        root = Path(self.temp.name) / "legacy-window-raw-signature"
        root.mkdir(parents=True, exist_ok=True)
        download_secret = b" " + (b"d" * 46) + b"\n"
        integrity_secret = b"\t" + (b"i" * 46) + b"\r"
        (root / ".download_secret").write_bytes(download_secret)
        (root / ".record_integrity_secret").write_bytes(integrity_secret)
        store = ProjectStore(root, "http://127.0.0.1:8765")
        window_path = root / ".legacy_download_token_window.json"
        forged = store.read_json(window_path)
        forged["accept_until_epoch"] += 86_400
        forged["integrity_hmac"] = store._record_integrity_hmac_with_secret(
            forged, integrity_secret.strip()
        )
        store.atomic_write_json(window_path, forged)
        with self.assertRaisesRegex(
            RuntimeError, "legacy_download_token_window_invalid"
        ):
            ProjectStore(root, "http://127.0.0.1:8765")

    def test_corrupt_existing_binary_secrets_fail_closed_without_rotation(
        self,
    ) -> None:
        for secret_name, expected_error, corrupt in (
            (".download_secret", "download_secret_invalid", b"x" * 31),
            (".download_secret", "download_secret_invalid", b" " * 48),
            (
                ".record_integrity_secret",
                "record_integrity_secret_invalid",
                b"x" * 31,
            ),
            (
                ".record_integrity_secret",
                "record_integrity_secret_invalid",
                b"\t" * 48,
            ),
        ):
            with self.subTest(secret_name=secret_name, corrupt=repr(corrupt[:1])):
                root = (
                    Path(self.temp.name)
                    / f"{secret_name.removeprefix('.')}-{len(corrupt)}-{corrupt[0]}"
                )
                root.mkdir(parents=True, exist_ok=True)
                if secret_name == ".record_integrity_secret":
                    (root / ".download_secret").write_bytes(b"d" * 48)
                (root / secret_name).write_bytes(corrupt)
                with self.assertRaisesRegex(RuntimeError, expected_error):
                    ProjectStore(root, "http://127.0.0.1:8765")
                self.assertEqual((root / secret_name).read_bytes(), corrupt)

    def test_missing_binary_secret_with_existing_state_fails_closed(self) -> None:
        for missing_name, expected_error in (
            (".download_secret", "download_secret_missing_for_existing_state"),
            (
                ".record_integrity_secret",
                "record_integrity_secret_missing_for_existing_state",
            ),
        ):
            with self.subTest(missing_name=missing_name):
                root = Path(self.temp.name) / missing_name.removeprefix(".")
                (root / "projects" / "existing").mkdir(parents=True, exist_ok=True)
                if missing_name == ".record_integrity_secret":
                    (root / ".download_secret").write_bytes(b"d" * 48)
                with self.assertRaisesRegex(RuntimeError, expected_error):
                    ProjectStore(root, "http://127.0.0.1:8765")
                self.assertFalse((root / missing_name).exists())

    def test_signed_download_rejects_payload_changed_after_token_issue(self) -> None:
        root = Path(self.temp.name) / "signed-download-fingerprint"
        store = ProjectStore(root, "http://127.0.0.1:8765")
        artifact = store.save_artifact_json(
            self.namespace,
            "PRJ-download-integrity",
            {"trusted": True},
            artifact_id="ART-download-integrity",
            file_name="trusted.json",
            artifact_type="test_fixture",
            version="v1",
        )
        url = store.signed_download_url(
            self.namespace,
            "PRJ-download-integrity",
            str(artifact["artifact_id"]),
            str(artifact["file_name"]),
        )
        token = url.split("/files/", 1)[1].split("/", 1)[0]
        record, stream, size = store.open_verified_download(token)
        try:
            payload = stream.read()
        finally:
            stream.close()
        self.assertEqual(record["artifact_id"], artifact["artifact_id"])
        self.assertEqual(size, len(payload))
        self.assertEqual(json.loads(payload), {"trusted": True})

        _record, payload_path = store.get_artifact(
            self.namespace,
            "PRJ-download-integrity",
            str(artifact["artifact_id"]),
        )
        payload_path.write_text('{"trusted":false}', encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "artifact_raw_(size|fingerprint)_mismatch"
        ):
            store.resolve_download_token(token)
        with self.assertRaisesRegex(
            ValueError, "artifact_raw_(size|fingerprint)_mismatch"
        ):
            store.open_verified_download(token)
        meta_path = (
            store.project_dir(self.namespace, "PRJ-download-integrity")
            / "artifacts"
            / f"{artifact['artifact_id']}.meta.json"
        )
        tampered = payload_path.read_bytes()
        tampered_meta = store.read_json(meta_path)
        tampered_meta["size_bytes"] = len(tampered)
        tampered_meta["fingerprint"] = hashlib.sha256(tampered).hexdigest()
        store.atomic_write_json(meta_path, tampered_meta)
        with self.assertRaisesRegex(
            ValueError, "download_token_artifact_binding_mismatch"
        ):
            store.open_verified_download(token)

        # The HTTP route must send the already-verified bytes rather than
        # reopening the mutable path through FileResponse.
        http_artifact = self.service.store.save_artifact_json(
            self.namespace,
            "PRJ-download-http-integrity",
            {"trusted": True},
            artifact_id="ART-download-http-integrity",
            file_name="http-trusted.json",
            artifact_type="test_fixture",
            version="v1",
        )
        http_url = self.service.store.signed_download_url(
            self.namespace,
            "PRJ-download-http-integrity",
            str(http_artifact["artifact_id"]),
            str(http_artifact["file_name"]),
        )
        http_token = http_url.split("/files/", 1)[1].split("/", 1)[0]
        _http_record, http_path = self.service.store.get_artifact(
            self.namespace,
            "PRJ-download-http-integrity",
            str(http_artifact["artifact_id"]),
        )
        app = self.server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            host="127.0.0.1",
        )
        with TestClient(app) as client:
            clean_response = client.get(
                f"/files/{http_token}/http-trusted.json"
            )
            http_path.write_text('{"trusted":false}', encoding="utf-8")
            response = client.get(f"/files/{http_token}/http-trusted.json")
            http_meta_path = (
                self.service.store.project_dir(
                    self.namespace, "PRJ-download-http-integrity"
                )
                / "artifacts"
                / f"{http_artifact['artifact_id']}.meta.json"
            )
            http_tampered = http_path.read_bytes()
            http_meta = self.service.store.read_json(http_meta_path)
            http_meta["size_bytes"] = len(http_tampered)
            http_meta["fingerprint"] = hashlib.sha256(http_tampered).hexdigest()
            self.service.store.atomic_write_json(http_meta_path, http_meta)
            rebound_response = client.get(
                f"/files/{http_token}/http-trusted.json"
            )
            http_meta["metadata"] = []
            self.service.store.atomic_write_json(http_meta_path, http_meta)
            malformed_publication_response = client.get(
                f"/files/{http_token}/http-trusted.json"
            )
            http_meta["metadata"] = {}
            http_meta.pop("relative_path")
            self.service.store.atomic_write_json(http_meta_path, http_meta)
            malformed_meta_response = client.get(
                f"/files/{http_token}/http-trusted.json"
            )
            http_meta_path.unlink()
            missing_meta_response = client.get(
                f"/files/{http_token}/http-trusted.json"
            )
        self.assertEqual(clean_response.status_code, 200)
        self.assertEqual(
            int(clean_response.headers["content-length"]),
            len(clean_response.content),
        )
        self.assertEqual(json.loads(clean_response.content), {"trusted": True})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'"trusted":false', response.content)
        self.assertEqual(rebound_response.status_code, 404)
        self.assertNotIn(b'"trusted":false', rebound_response.content)
        self.assertEqual(malformed_meta_response.status_code, 404)
        self.assertEqual(malformed_publication_response.status_code, 404)
        self.assertEqual(missing_meta_response.status_code, 404)

    def test_verified_download_enforces_configured_size_limit(self) -> None:
        root = Path(self.temp.name) / "signed-download-size-limit"
        store = ProjectStore(
            root,
            "http://127.0.0.1:8765",
            max_download_bytes=32,
        )
        artifact = store.save_artifact_bytes(
            self.namespace,
            "PRJ-download-size-limit",
            b"x" * 33,
            artifact_id="ART-download-size-limit",
            file_name="oversize.bin",
            artifact_type="test_fixture",
            version="v1",
        )
        with self.assertRaisesRegex(
            ValueError, "artifact_payload_exceeds_download_limit"
        ):
            store.signed_download_url(
                self.namespace,
                "PRJ-download-size-limit",
                str(artifact["artifact_id"]),
                str(artifact["file_name"]),
            )
        store.max_download_bytes = 64
        url = store.signed_download_url(
            self.namespace,
            "PRJ-download-size-limit",
            str(artifact["artifact_id"]),
            str(artifact["file_name"]),
        )
        token = url.split("/files/", 1)[1].split("/", 1)[0]
        store.max_download_bytes = 32
        with self.assertRaisesRegex(
            ValueError, "artifact_payload_exceeds_download_limit"
        ):
            store.open_verified_download(token)

    def test_failed_draft_preflight_learns_without_publication_side_effects(self) -> None:
        project_id = self._project("failed-draft-is-learning-only")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="failed-draft"
        )
        draft = valid_storyboard()
        draft["shots"][0]["环境音效"] += "；BGM渐入"

        result = self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_kind="scene",
            scope_id="EP01-SC01",
            request_session_id=session_id,
        )

        self.assertEqual(result.status, service_module.DRAFT_PREFLIGHT_FAIL)
        self.assertFalse(result.data["commit_ready"])
        self.assertFalse(result.data["artifact_created"])
        self.assertFalse(result.data["version_created"])
        self.assertFalse(result.data["xlsx_created"])
        self.assertFalse(result.data["handoff_created"])
        self.assertGreater(result.data["quality"]["checked_shot_count"], 0)
        self.assertFalse(
            any(
                str(row.get("location") or "").startswith("scope.scene")
                for row in result.data["report"]["issues"]
            )
        )
        self.assertTrue(
            any(
                row.get("error_code") == "E-AUDIO-STAGE-001"
                for row in result.data["report"]["issues"]
            )
        )
        self._assert_no_storyboard_publication(project_id)

        records = self.service.store.list_project_records(
            self.namespace, project_id, kind="draft_preflights"
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(self.service.store.project_record_is_signed(records[0]))
        observations = self.service.store.list_project_records_strict(
            self.namespace, project_id, kind="draft_observations")
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["candidate_snapshot"], draft)
        self.assertIsNone(observations[0]["raw_generation_quality"])
        self.assertNotIn("candidate_snapshot", result.data["candidate_observation"])
        self.assertTrue(self.service.store.project_record_is_signed(observations[0]))
        changed = copy.deepcopy(observations[0])
        changed["candidate_snapshot"]["shots"][0]["环境音效"] = "无"
        with self.assertRaisesRegex(ValueError, "draft_observation_immutable"):
            self.service.store.save_project_record(self.namespace, project_id,
                kind="draft_observations", record_id=changed["observation_id"], value=changed)
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        self.assertEqual(ledger["events"][-1]["task_type"], "production_draft_preflight")
        self.assertGreaterEqual(len(ledger["issues"]), 1)

    def test_readonly_diagnostic_preserves_unknown_history_and_ignores_workflow_block(self):
        project_id = self._project("readonly-scoped-diagnostic")
        storyboard = valid_storyboard()
        storyboard["shots"][0]["环境音效"] += "；BGM渐入"
        foreign = copy.deepcopy(storyboard["shots"][0])
        foreign.update({"episode_id": "EP36", "镜头编号": "EP36-001", "场景连续性ID": "EP36-SC01"})
        storyboard["shots"].append(foreign)
        storyboard["prevention_evidence"]["repair_history"] = [{"round": 1, "introduced_issue_count": None}]
        record = self.service.store.save_artifact_bytes(self.namespace, project_id,
            json.dumps(storyboard, ensure_ascii=False).encode("utf-8"), file_name="historical.json",
            artifact_type="storyboard_json", version="historical-v1")
        with self.service.store.mutate_project(self.namespace, project_id) as (hub, ledger):
            hub["project_state"]["current_stage"] = "failed_current"
        directory = self.service.store.project_dir(self.namespace, project_id)
        before = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in directory.rglob("*") if p.is_file()}
        result = self.service.diagnose_storyboard_artifact(self.namespace, project_id,
            record["artifact_id"], ["EP01"])
        self.assertEqual(result.status, "diagnostic_completed")
        self.assertEqual(result.data["formal_audit_status"], "not_performed")
        self.assertFalse(result.data["certificate_created"])
        self.assertTrue(result.data["outside_scope_issues"])
        self.assertTrue(result.data["unscoped_or_dependency_issues"])
        after = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in directory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_full_clean_preflight_is_signed_and_bound_to_exact_commit(self) -> None:
        project_id = self._project("full-draft-binding")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="full-binding"
        )
        draft = valid_storyboard()

        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=session_id,
        )
        preflight_id = str(preflight.trace_id)
        self.assertEqual(preflight.status, service_module.DRAFT_PREFLIGHT_PASS)
        self.assertTrue(preflight.data["commit_ready"])
        self.assertEqual(preflight.data["quality"]["grade"], "A")
        self._assert_no_storyboard_publication(project_id)

        changed = copy.deepcopy(draft)
        changed["shots"][0]["空间站位描述"] += "；门框边缘保持在画面左侧。"
        with self.assertRaisesRegex(ValueError, "draft_preflight_binding_mismatch: canonical"):
            self.service.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=changed,
                file_name="绑定不匹配.xlsx",
                source_artifact_id=None,
                request_session_id=session_id,
                draft_preflight_id=preflight_id,
            )
        self._assert_no_storyboard_publication(project_id)

        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="绑定成功.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=preflight_id,
        )
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)
        self.assertEqual(
            committed.data["draft_preflight"]["draft_preflight_id"], preflight_id
        )
        self.assertEqual(
            committed.data["artifact"]["metadata"]["draft_preflight_id"],
            preflight_id,
        )
        handoff_id = str(committed.data["handoff"]["handoff_id"])
        signed_handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        hub_handoff = next(
            row
            for row in self.service.store.load_hub(
                self.namespace, project_id
            )["handoffs"]
            if row["handoff_id"] == handoff_id
        )
        self.assertEqual(
            hub_handoff,
            self.service._handoff_record_projection(signed_handoff),
        )
        self.assertFalse(
            {"record_id", "project_id", "integrity_hmac"} & hub_handoff.keys()
        )
        artifact_count = len(
            self.service.store.list_artifacts(self.namespace, project_id)
        )
        replay = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="绑定成功.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=preflight_id,
        )
        self.assertEqual(replay.artifact_id, committed.artifact_id)
        self.assertTrue(replay.data["idempotent_replay"])
        self.assertEqual(
            len(self.service.store.list_artifacts(self.namespace, project_id)),
            artifact_count,
        )

    def test_prevention_profile_change_requires_new_preflight_revision(self) -> None:
        project_id = self._project("preflight-profile-revision")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="profile-revision"
        )
        draft = valid_storyboard()
        initial = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=session_id,
        )
        self.assertEqual(initial.status, service_module.DRAFT_PREFLIGHT_PASS)
        old_preflight_id = str(initial.trace_id)
        old_profile_sha = str(
            initial.data["draft_preflight"]["prevention_profile_sha256"]
        )

        artifact_record = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"independent_observation": "prevention profile changed"},
            file_name="independent-profile-observation.json",
            artifact_type="source_extraction",
            version="v2",
        )
        canonical_hash = service_module.canonical_storyboard_sha256(draft)
        fingerprints = self.service._fingerprint_bundle(
            draft,
            artifact_record,
            canonical_hash=canonical_hash,
            source_fingerprint=self.service._source_fingerprint(
                self.namespace, project_id, draft, None
            ),
            boundary_hash=self.service._boundary_hash(draft),
        )
        shot_id = str(draft["shots"][0]["镜头编号"])
        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (_hub, ledger):
            observed = self.service._record_audit(
                namespace=self.namespace,
                project_id=project_id,
                artifact_record=artifact_record,
                storyboard=draft,
                report={
                    "status": "fail_current",
                    "semantic_audit_sha256": hashlib.sha256(
                        b"independent-profile-change-semantic-audit"
                    ).hexdigest(),
                    "issues": [
                        {
                            "issue_id": "SEM-PROFILE-CHANGE",
                            "error_code": "E-STATE-ATOM-001",
                            "severity": "fail",
                            "blocking": True,
                            "shot_ids": [shot_id],
                            "field": "起始状态",
                            "location": "semantic.issue.SEM-PROFILE-CHANGE",
                            "issue_type": "起始状态提前包含结果",
                            "root_cause": "状态原子模板混入镜尾结果",
                            "evidence": "另一批独立审计发现已知模式",
                            "repair_action": "生成前加载更新后的预防画像",
                        }
                    ],
                },
                ledger=ledger,
                audit_cycle_id="AUD-INDEPENDENT-PROFILE-CHANGE",
                audit_class=service_module.AUDIT_CLASS_INDEPENDENT,
                audit_cache_key="CACHE-INDEPENDENT-PROFILE-CHANGE",
                event_id="EVT-INDEPENDENT-PROFILE-CHANGE",
                handoff_id="HND-INDEPENDENT-PROFILE-CHANGE",
                stage_trace_id="TRC-INDEPENDENT-PROFILE-CHANGE",
                fingerprints=fingerprints,
                runtime_test_certificate_id=str(
                    self.service._require_runtime_test_certificate(
                        self.namespace, project_id
                    )["certificate_id"]
                ),
                work_order_id="WO-INDEPENDENT-PROFILE-CHANGE",
                validation_role="independent_audit",
            )
        self.assertEqual(observed["status"], "recorded")
        current_profile = service_module.storyboard_agent.active_prevention_profile(
            self.service.store.load_ledger(self.namespace, project_id)
        )
        current_profile_sha = service_module.sha256_bytes(
            service_module._stable_json_bytes(current_profile)
        )
        self.assertNotEqual(current_profile_sha, old_profile_sha)
        artifact_count_before_rejected_commit = len(
            self.service.store.list_artifacts(self.namespace, project_id)
        )

        with self.assertRaisesRegex(
            ValueError, "draft_preflight_binding_mismatch:.*prevention_profile"
        ):
            self.service.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=draft,
                file_name="旧画像预检不得提交.xlsx",
                source_artifact_id=None,
                request_session_id=session_id,
                draft_preflight_id=old_preflight_id,
            )
        self.assertEqual(
            len(self.service.store.list_artifacts(self.namespace, project_id)),
            artifact_count_before_rejected_commit,
        )
        self.assertEqual(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            ),
            [],
        )
        hub_after_rejection = self.service.store.load_hub(
            self.namespace, project_id
        )
        self.assertEqual(hub_after_rejection.get("deliverables", []), [])
        self.assertNotIn(
            "storyboard",
            hub_after_rejection["project_state"]["latest_artifacts"],
        )

        revised = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=session_id,
        )
        self.assertEqual(revised.status, service_module.DRAFT_PREFLIGHT_PASS)
        self.assertNotEqual(revised.trace_id, old_preflight_id)
        self.assertEqual(
            revised.data["draft_preflight"]["preflight_revision"], 2
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="新画像预检可提交.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=str(revised.trace_id),
        )
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)
        self.assertEqual(
            committed.data["artifact"]["metadata"]["draft_preflight_id"],
            revised.trace_id,
        )

    def test_corrected_draft_closes_stable_scope_and_can_commit_immediately(self) -> None:
        project_id = self._project("failed-then-corrected-stable-scope")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="stable-draft-cycle"
        )
        broken = valid_storyboard()
        broken["shots"][0]["环境音效"] += "；BGM渐入"

        failed = self.service.preflight_storyboard_draft(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=broken,
            scope_kind="full",
            scope_id="EP01",
            request_session_id=session_id,
        )
        self.assertEqual(failed.status, service_module.DRAFT_PREFLIGHT_FAIL)
        failed_event = failed.data["draft_preflight"]["ledger_event"]
        self.assertEqual(failed_event["audit_round"], 1)

        corrected = valid_storyboard()
        passed = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=corrected,
            scope_id="EP01",
            request_session_id=session_id,
        )
        self.assertEqual(passed.status, service_module.DRAFT_PREFLIGHT_PASS)
        self.assertTrue(passed.data["commit_ready"])
        passed_event = passed.data["draft_preflight"]["ledger_event"]
        self.assertEqual(passed_event["audit_cycle_id"], failed_event["audit_cycle_id"])
        self.assertEqual(passed_event["audit_round"], 2)
        first_observation = failed.data["candidate_observation"]
        self.assertEqual(passed.data["candidate_observation"]["first_observation_id"],
                         first_observation["observation_id"])
        original = self.service.store.get_project_record(self.namespace, project_id,
            kind="draft_observations", record_id=first_observation["observation_id"])
        self.assertEqual(original["candidate_snapshot"], broken)
        reopened = self.service._observe_draft_candidate(self.namespace, project_id,
            work_order_id="new-work-order", scope_kind="full", scope_id="EP01", storyboard=corrected,
            quality=passed.data["quality"], report=passed.data["report"])
        self.assertEqual(reopened["first_observation_id"], first_observation["observation_id"])

        ledger = self.service.store.load_ledger(self.namespace, project_id)
        draft_events = [
            row
            for row in ledger["events"]
            if row.get("task_type") == "production_draft_preflight"
            and row.get("file_name") == "draft-full-EP01"
        ]
        self.assertEqual(len(draft_events), 2)
        self.assertEqual(len({row["scope_key"] for row in draft_events}), 1)
        self.assertTrue(ledger["issues"])
        self.assertTrue(
            all(row["status"] == "resolved" for row in ledger["issues"])
        )
        self.assertTrue(
            all(not row["open_scope_keys"] for row in ledger["issues"])
        )
        scope = next(
            row
            for row in ledger["scope_states"]
            if row["scope_key"] == draft_events[-1]["scope_key"]
        )
        self.assertEqual(scope["closure_state"], "preflight_clean")
        self.assertEqual(scope["open_issue_ids"], [])

        preflight_id = str(passed.trace_id)
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=corrected,
            file_name="修正后立即提交.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=preflight_id,
        )
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)
        self.assertEqual(
            committed.data["draft_preflight"]["draft_preflight_id"], preflight_id
        )

    def test_draft_commit_claim_releases_on_failure_and_exact_replays_are_single_publish(self) -> None:
        project_id = self._project("draft-commit-claim-recovery")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="claim-recovery"
        )
        draft = valid_storyboard()
        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=session_id,
        )
        preflight_id = str(preflight.trace_id)

        def commit() -> Any:
            return self.service.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=draft,
                file_name="claim精确重放.xlsx",
                source_artifact_id=None,
                request_session_id=session_id,
                draft_preflight_id=preflight_id,
            )

        with patch.object(
            self.service,
            "_source_fingerprint",
            side_effect=RuntimeError("source fingerprint exploded"),
        ):
            with self.assertRaisesRegex(RuntimeError, "source fingerprint exploded"):
                commit()
        self._assert_no_storyboard_publication(project_id)

        with patch.object(
            service_module,
            "_export_compact_storyboard",
            side_effect=RuntimeError("export exploded"),
        ):
            with self.assertRaisesRegex(RuntimeError, "export exploded"):
                commit()
        self._assert_no_storyboard_publication(project_id)

        original_save_json = self.service.store.save_artifact_json
        persistence_entered = threading.Event()
        allow_persistence = threading.Event()

        def delayed_save_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
            persistence_entered.set()
            if not allow_persistence.wait(timeout=10):
                raise TimeoutError("concurrent commit test did not release persistence")
            return original_save_json(*args, **kwargs)

        with patch.object(
            self.service.store,
            "save_artifact_json",
            side_effect=delayed_save_json,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(commit)
                self.assertTrue(persistence_entered.wait(timeout=5))
                try:
                    with self.assertRaisesRegex(
                        ValueError, "authority_operation_in_progress"
                    ):
                        commit()
                finally:
                    allow_persistence.set()
                first = future.result(timeout=20)

        self.assertEqual(first.status, service_module.PRODUCTION_PREFLIGHT_PASS)
        artifact_count = len(
            self.service.store.list_artifacts(self.namespace, project_id)
        )
        handoff_count = len(
            self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            )
        )
        replay = commit()
        self.assertTrue(replay.data["idempotent_replay"])
        self.assertEqual(replay.artifact_id, first.artifact_id)
        self.assertEqual(
            len(self.service.store.list_artifacts(self.namespace, project_id)),
            artifact_count,
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, project_id, kind="handoffs"
                )
            ),
            handoff_count,
        )
        consumed = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=preflight_id,
        )["consumed_by_commit"]
        self.assertEqual(consumed["artifact_id"], first.artifact_id)

    def test_tampered_deterministic_certificate_and_handoff_fail_closed_on_retry(self) -> None:
        for record_kind in ("certificates", "handoffs"):
            with self.subTest(tampered_record_kind=record_kind):
                project_id = self._project(f"tampered-deterministic-{record_kind}")
                work_order_id, session_id = self._begin(
                    project_id,
                    operation="storyboard_create",
                    label=f"tampered-{record_kind}",
                )
                draft = valid_storyboard()
                preflight = self._preflight_full_with_scene_coverage(
                    project_id=project_id,
                    work_order_id=work_order_id,
                    storyboard=draft,
                    scope_id="EP01",
                    request_session_id=session_id,
                )

                def commit() -> Any:
                    return self.service.commit_storyboard(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=work_order_id,
                        storyboard=draft,
                        file_name=f"deterministic-{record_kind}.xlsx",
                        source_artifact_id=None,
                        request_session_id=session_id,
                        draft_preflight_id=str(preflight.trace_id),
                    )

                original_update_metadata = (
                    self.service.store.update_artifact_metadata
                )
                injected = {"raised": False}

                def fail_after_certificate_and_handoff(
                    namespace: str,
                    bound_project_id: str,
                    artifact_id: str,
                    updates: dict[str, Any],
                ) -> dict[str, Any]:
                    if (
                        "production_certificate_id" in updates
                        and not injected["raised"]
                    ):
                        injected["raised"] = True
                        raise RuntimeError(
                            "injected failure after deterministic authority records"
                        )
                    return original_update_metadata(
                        namespace,
                        bound_project_id,
                        artifact_id,
                        updates,
                    )

                with patch.object(
                    self.service.store,
                    "update_artifact_metadata",
                    side_effect=fail_after_certificate_and_handoff,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "injected failure after deterministic authority records",
                    ):
                        commit()
                self.assertTrue(injected["raised"])
                if record_kind == "certificates":
                    candidates = [
                        row
                        for row in self.service.store.list_project_records(
                            self.namespace,
                            project_id,
                            kind="certificates",
                        )
                        if row.get("audit_class")
                        == service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT
                    ]
                    self.assertEqual(len(candidates), 1)
                    record_id = str(candidates[0]["certificate_id"])
                else:
                    candidates = self.service.store.list_project_records(
                        self.namespace, project_id, kind="handoffs"
                    )
                    self.assertEqual(len(candidates), 1)
                    record_id = str(candidates[0]["handoff_id"])

                record_path = (
                    self.service.store.project_dir(self.namespace, project_id)
                    / record_kind
                    / f"{record_id}.json"
                )
                tampered = json.loads(record_path.read_text(encoding="utf-8"))
                tampered["integrity_hmac"] = "0" * 64
                self.service.store.atomic_write_json(record_path, tampered)
                tampered_bytes = record_path.read_bytes()
                artifact_count = len(
                    self.service.store.list_artifacts(
                        self.namespace, project_id
                    )
                )

                with self.assertRaisesRegex(
                    ValueError, "project_record_integrity_mismatch"
                ):
                    commit()
                self.assertEqual(record_path.read_bytes(), tampered_bytes)
                self.assertEqual(
                    len(
                        self.service.store.list_artifacts(
                            self.namespace, project_id
                        )
                    ),
                    artifact_count,
                )

    def test_explicit_source_artifact_must_resolve_for_commit_and_audit(self) -> None:
        project_id = self._project("source-artifact-resolution")
        other_project_id = self._project("source-artifact-other-project")
        foreign_source = self.service.store.save_artifact_json(
            self.namespace,
            other_project_id,
            {"source": "belongs to another project"},
            file_name="foreign-source.json",
            artifact_type="source_extraction",
            version="v1",
        )
        session_id = "v055-explicit-source-resolution"
        task = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="显式来源产物必须属于当前项目",
            targets=["全项目"],
            authoritative_source="测试剧本",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["剧情与对白"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key=session_id,
            request_session_id=session_id,
        )
        work_order_id = str(task.work_order_id)
        draft = valid_storyboard()
        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=session_id,
        )
        self.assertEqual(preflight.status, service_module.DRAFT_PREFLIGHT_PASS)

        for source_id in (
            "ART-source-does-not-exist",
            str(foreign_source["artifact_id"]),
        ):
            with self.subTest(commit_source_artifact_id=source_id):
                with self.assertRaisesRegex(
                    ValueError, "source_artifact_missing_or_invalid"
                ):
                    self.service.commit_storyboard(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=work_order_id,
                        storyboard=draft,
                        file_name="非法显式来源不得生成.xlsx",
                        source_artifact_id=source_id,
                        source_link_mode="revision",
                        request_session_id=session_id,
                        draft_preflight_id=str(preflight.trace_id),
                    )
                self._assert_no_storyboard_publication(project_id)

        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="source-lock合法回退.xlsx",
            source_artifact_id=None,
            request_session_id=session_id,
            draft_preflight_id=str(preflight.trace_id),
        )
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)
        committed_record, _committed_path = self.service.store.get_artifact(
            self.namespace, project_id, str(committed.artifact_id)
        )
        expected_source_fingerprint = self.service._source_fingerprint(
            self.namespace, project_id, draft, None
        )
        self.assertEqual(
            committed_record["metadata"]["source_fingerprint"],
            expected_source_fingerprint,
        )

        audit_project_id = self._project("audit-source-artifact-resolution")
        for index, source_id in enumerate(
            (
                "ART-audit-source-does-not-exist",
                str(foreign_source["artifact_id"]),
            ),
            start=1,
        ):
            with self.subTest(audit_source_artifact_id=source_id):
                audit_artifact = self.service.store.save_artifact_json(
                    self.namespace,
                    audit_project_id,
                    draft,
                    file_name=f"external-audit-{index}.json",
                    artifact_type="storyboard_json",
                    version=f"v{index}",
                    source_artifact_id=source_id,
                )
                audit_work_order_id, audit_session_id = self._begin_audit(
                    audit_project_id,
                    str(audit_artifact["artifact_id"]),
                    label=f"invalid-source-{index}",
                )
                artifacts_before = len(
                    self.service.store.list_artifacts(
                        self.namespace, audit_project_id
                    )
                )
                handoffs_before = len(
                    self.service.store.list_project_records(
                        self.namespace, audit_project_id, kind="handoffs"
                    )
                )
                certificates_before = len(
                    self.service.store.list_project_records(
                        self.namespace, audit_project_id, kind="certificates"
                    )
                )
                with self.assertRaisesRegex(
                    ValueError, "source_artifact_missing_or_invalid"
                ):
                    self.service.audit_storyboard(
                        self.namespace,
                        project_id=audit_project_id,
                        artifact_id=str(audit_artifact["artifact_id"]),
                        audit_cycle_id=None,
                        work_order_id=audit_work_order_id,
                        handoff_artifact_id=None,
                        force_machine_recheck=False,
                        semantic_audit=semantic_audit_for_artifact(
                            self.service,
                            self.settings,
                            audit_project_id,
                            str(audit_artifact["artifact_id"]),
                        ),
                        request_session_id=audit_session_id,
                    )
                self.assertEqual(
                    len(
                        self.service.store.list_artifacts(
                            self.namespace, audit_project_id
                        )
                    ),
                    artifacts_before,
                )
                self.assertEqual(
                    len(
                        self.service.store.list_project_records(
                            self.namespace, audit_project_id, kind="handoffs"
                        )
                    ),
                    handoffs_before,
                )
                self.assertEqual(
                    len(
                        self.service.store.list_project_records(
                            self.namespace, audit_project_id, kind="certificates"
                        )
                    ),
                    certificates_before,
                )

    def test_nonexistent_scene_and_episode_scope_ids_are_blocked(self) -> None:
        project_id = self._project("draft-scope-id-must-exist")
        work_order_id, session_id = self._begin(
            project_id, operation="storyboard_create", label="missing-scopes"
        )
        draft = valid_storyboard()

        for scope_kind, scope_id in (
            ("episode", "EP99"),
            ("scene", "EP01-SC99"),
        ):
            with self.subTest(scope_kind=scope_kind):
                result = self.service.preflight_storyboard_draft(
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    storyboard=draft,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    request_session_id=session_id,
                )
                self.assertEqual(result.status, service_module.DRAFT_PREFLIGHT_FAIL)
                self.assertFalse(result.data["commit_ready"])
                self.assertEqual(result.data["quality"]["checked_shot_count"], 0)
                self.assertTrue(
                    any(
                        row.get("location") == f"scope.{scope_kind}.{scope_id}"
                        for row in result.data["report"]["issues"]
                    )
                )
        self._assert_no_storyboard_publication(project_id)

    def test_cloud_invalid_state_requires_migration_then_requires_full_preflight(self) -> None:
        cloud_settings = Settings(
            data_dir=Path(self.temp.name) / "cloud",
            public_base_url="https://shortdrama.example.test",
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
        _server, cloud = create_mcp(cloud_settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(cloud)
        project_id = str(
            cloud.create_project(self.namespace, "cloud-migration-and-preflight").project_id
        )
        certified = cloud.agent_status(
            self.namespace, project_id, full_self_test=True
        )
        self.assertTrue(certified.data["runtime_test_certificate_valid"])

        production_session = "cloud-production-session"
        production = cloud.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="cloud production must migrate and preflight",
            targets=["第一集"],
            authoritative_source="测试剧本",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["全部内容"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key=production_session,
            request_session_id=production_session,
        )
        migration_session = "cloud-migration-session"
        migration = cloud.begin_task(
            self.namespace,
            project_id=project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="migrate project control state",
            targets=["Hub", "Ledger"],
            authoritative_source="当前项目控制状态",
            latest_version="v0.5.4",
            target_boundary="控制状态",
            must_keep=["历史分镜与审计证据"],
            must_remove=[],
            protected_scope=["历史产物"],
            expected_outputs=["迁移记录"],
            window_key=migration_session,
            request_session_id=migration_session,
        )
        self._make_legacy_state(cloud, self.namespace, project_id)
        draft = valid_storyboard()

        with self.assertRaisesRegex(ValueError, "project_state_migration_required"):
            cloud.preflight_storyboard_draft(
                self.namespace,
                project_id=project_id,
                work_order_id=str(production.work_order_id),
                storyboard=draft,
                scope_kind="full",
                scope_id="EP01",
                request_session_id=production_session,
            )
        with self.assertRaisesRegex(ValueError, "project_state_migration_required"):
            cloud.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=str(production.work_order_id),
                storyboard=draft,
                file_name="不得绕过迁移.xlsx",
                source_artifact_id=None,
                request_session_id=production_session,
            )
        self.assertEqual(cloud.store.list_artifacts(self.namespace, project_id), [])

        migrated = cloud.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=str(migration.work_order_id),
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=migration_session,
        )
        self.assertEqual(migrated.status, "migrated")
        with self.assertRaisesRegex(ValueError, "draft_preflight_required"):
            cloud.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=str(production.work_order_id),
                storyboard=draft,
                file_name="不得绕过初稿预检.xlsx",
                source_artifact_id=None,
                request_session_id=production_session,
            )

        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=str(production.work_order_id),
            storyboard=draft,
            scope_id="EP01",
            request_session_id=production_session,
            service=cloud,
        )
        committed = cloud.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=str(production.work_order_id),
            storyboard=draft,
            file_name="已迁移且已预检.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        self.assertEqual(committed.status, service_module.PRODUCTION_PREFLIGHT_PASS)

    def test_quality_counts_unique_affected_shots_not_issue_points(self) -> None:
        shots = []
        for index in range(1, 101):
            shots.append(
                {
                    "镜头编号": f"EP{'01' if index <= 50 else '02'}-{index:03d}",
                    "episode_id": "EP01" if index <= 50 else "EP02",
                }
            )
        affected = shots[0]["镜头编号"]
        issues = [
            {
                "error_code": "E-TRANSITION-TEST",
                "location": "shots[1].切镜/转场提示词",
                "shot_ids": [affected],
                "severity": "warn",
                "blocking": False,
                "issue_type": "重复转场",
                "root_cause": "转场模板重复拼接",
            },
            {
                "error_code": "E-TRANSITION-TEST",
                "location": "shots[1].切镜/转场提示词",
                "shot_ids": [affected],
                "severity": "warn",
                "blocking": False,
                "issue_type": "重复转场",
                "root_cause": "转场模板重复拼接",
            },
        ]

        quality = service_module._draft_quality_metrics(
            {"shots": shots}, issues, project_id="SBP-QUALITY"
        )

        self.assertEqual(quality["issue_point_count"], 2)
        self.assertEqual(quality["unique_affected_shot_count"], 1)
        self.assertEqual(quality["first_draft_correctness_percent"], 99.0)
        self.assertEqual(quality["direct_usability_percent"], 99.0)
        self.assertEqual(quality["episode_correctness_percent"]["EP01"], 98.0)
        self.assertEqual(quality["episode_correctness_percent"]["EP02"], 100.0)
        self.assertEqual(len(quality["systemic_pattern_keys"]), 1)

    def test_ledger_status_metrics_use_independent_cycle_semantics(self) -> None:
        agent = service_module.storyboard_agent
        ledger = agent.new_ledger(
            "状态语义矩阵", "SBP-STATUS-MATRIX", "2026-08-31T00:00:00Z"
        )

        production_preflight = {
            "event_id": "EVT-PRODUCTION-PREFLIGHT",
            "timestamp": "2026-08-31T00:01:00Z",
            "episode_id": "EP01",
            "file_name": "draft-full-EP01",
            "file_fingerprint": "sha256:draft",
            "source_version": "draft-candidate",
            "audit_cycle_id": "DRAFT-CYCLE-EP01",
            "task_type": "production_draft_preflight",
            "audit_class": "production_draft_preflight",
            "audit_round": 1,
            "result": "pass",
            "checked_shot_count": 100,
            "issues": [],
        }
        agent.ingest_event(ledger, production_preflight)
        no_independent = agent.summarize(ledger)["metrics"]
        self.assertIsNone(
            no_independent["first_draft_shot_correctness_percent"]
        )
        self.assertIsNone(
            no_independent["independent_audit_cycle_pass_rate_percent"]
        )
        self.assertIsNone(
            no_independent["human_release_signoff_completion_percent"]
        )
        self.assertIsNone(no_independent["current_artifact_issue_point_count"])
        self.assertEqual(
            no_independent["historical_checked_shot_observations"], 100
        )

        issues = []
        for index in range(5):
            shot_id = "EP01-001" if index < 3 else "EP01-002"
            issues.append(
                {
                    "error_code": f"E-MATRIX-{index + 1:03d}",
                    "category": "正式母版单镜执行完整性",
                    "severity": "fail",
                    "blocking": True,
                    "shot_ids": [shot_id],
                    "field": f"shots[{1 if index < 3 else 2}].状态字段{index}",
                    "issue_type": "状态语义矩阵问题",
                    "root_cause": "状态语义矩阵测试",
                    "evidence": f"问题点{index + 1}",
                    "source_sentence": "独立审计证据",
                    "repair_action": "修复对应字段",
                    "status": "initial",
                }
            )
        first_audit = {
            "event_id": "EVT-INDEPENDENT-R1",
            "timestamp": "2026-08-31T00:02:00Z",
            "episode_id": "EP01",
            "file_name": "storyboard-EP01.xlsx",
            "file_fingerprint": hashlib.sha256(b"storyboard-r1").hexdigest(),
            "source_version": "v1",
            "audit_cycle_id": "AUD-INDEPENDENT-EP01",
            "task_type": "audit",
            "audit_class": "independent_audit",
            "validation_role": "independent_audit",
            "handoff_id": "HND-STATUS-MATRIX-R1",
            "artifact_semantic_fingerprint": hashlib.sha256(
                b"status-matrix-artifact-r1"
            ).hexdigest(),
            "canonical_fingerprint": hashlib.sha256(
                b"status-matrix-canonical-r1"
            ).hexdigest(),
            "source_fingerprint": hashlib.sha256(
                b"status-matrix-source"
            ).hexdigest(),
            "boundary_fingerprint": hashlib.sha256(
                b"status-matrix-boundary"
            ).hexdigest(),
            "runtime_fingerprint": hashlib.sha256(
                b"status-matrix-runtime"
            ).hexdigest(),
            "rules_fingerprint": hashlib.sha256(
                b"status-matrix-rules"
            ).hexdigest(),
            "validator_fingerprint": hashlib.sha256(
                b"status-matrix-validator"
            ).hexdigest(),
            "runtime_test_certificate_id": "RTC-STATUS-MATRIX",
            "stage_trace_id": "TRC-STATUS-MATRIX-R1",
            "semantic_audit_sha256": hashlib.sha256(
                b"status-matrix-semantic-audit-r1"
            ).hexdigest(),
            "semantic_report_input_sha256": hashlib.sha256(
                b"status-matrix-semantic-report-r1"
            ).hexdigest(),
            "cache_evidence": [],
            "independence_assertions": {
                "artifact_read_only": True,
                "production_verdict_not_reused": True,
                "quality_conclusions_recomputed": True,
                "eight_layers_completed": True,
            },
            "audit_round": 1,
            "result": "fail_current",
            "checked_shot_count": 100,
            "blockers": [],
            "issues": issues,
        }
        first_result = agent.ingest_event(ledger, first_audit)
        first_metrics = agent.summarize(ledger)["metrics"]
        self.assertEqual(
            first_metrics["first_draft_shot_correctness_percent"], 98.0
        )
        self.assertEqual(first_metrics["first_draft_problem_shot_rate_percent"], 2.0)
        self.assertEqual(first_metrics["current_artifact_issue_point_count"], 5)
        self.assertEqual(first_metrics["current_artifact_problem_shot_count"], 2)
        self.assertEqual(
            first_metrics["independent_audit_cycle_pass_rate_percent"], 0.0
        )

        repaired_pass = {
            **first_audit,
            "event_id": "EVT-INDEPENDENT-R2",
            "timestamp": "2026-08-31T00:03:00Z",
            "file_fingerprint": hashlib.sha256(b"storyboard-r2").hexdigest(),
            "source_version": "v2",
            "task_type": "audit",
            "handoff_id": "HND-STATUS-MATRIX-R2",
            "artifact_semantic_fingerprint": hashlib.sha256(
                b"status-matrix-artifact-r2"
            ).hexdigest(),
            "canonical_fingerprint": hashlib.sha256(
                b"status-matrix-canonical-r2"
            ).hexdigest(),
            "stage_trace_id": "TRC-STATUS-MATRIX-R2",
            "semantic_audit_sha256": hashlib.sha256(
                b"status-matrix-semantic-audit-r2"
            ).hexdigest(),
            "semantic_report_input_sha256": hashlib.sha256(
                b"status-matrix-semantic-report-r2"
            ).hexdigest(),
            "audit_round": 2,
            "result": "pass",
            "blockers": [],
            "issues": [],
        }
        repaired_result = agent.ingest_event(ledger, repaired_pass)
        repaired_metrics = agent.summarize(ledger)["metrics"]
        self.assertEqual(
            repaired_metrics["first_draft_shot_correctness_percent"], 98.0
        )
        self.assertEqual(repaired_metrics["current_artifact_issue_point_count"], 0)
        self.assertEqual(repaired_metrics["current_artifact_problem_shot_count"], 0)
        self.assertEqual(
            repaired_metrics["independent_audit_cycle_pass_rate_percent"],
            100.0,
        )
        self.assertEqual(
            repaired_metrics["human_release_signoff_completion_percent"],
            0.0,
        )
        self.assertEqual(
            repaired_metrics["historical_checked_shot_observations"], 300
        )
        self.assertEqual(first_result["scope_key"], repaired_result["scope_key"])

        agent.signoff_scope(ledger, repaired_result["scope_key"], "approve")
        signed_metrics = agent.summarize(ledger)["metrics"]
        self.assertEqual(
            signed_metrics["human_release_signoff_completion_percent"], 100.0
        )

    def test_signoff_synchronizes_hub_and_clean_independent_ledger_scope(self) -> None:
        project_id = self._project("signoff-hub-ledger-sync")
        work_order_id, production_session = self._begin(
            project_id, operation="storyboard_create", label="signoff-production"
        )
        draft = valid_storyboard()
        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=production_session,
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="签核同步.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        artifact_id = str(committed.artifact_id)
        audit_work_order_id, audit_session = self._begin_audit(
            project_id, artifact_id, label="signoff-audit"
        )
        audited = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_work_order_id,
            handoff_artifact_id=str(committed.data["handoff_artifact_id"]),
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                artifact_id,
            ),
            request_session_id=audit_session,
        )
        self.assertEqual(audited.status, "pass")
        audit_event_id = str(audited.data["certificate"]["event_id"])

        signed = self.service.signoff_task(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            decision="approve",
            signer="v0.5.5 regression",
        )
        self.assertEqual(signed.status, "archived")
        hub = self.service.store.load_hub(self.namespace, project_id)
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        production_work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == work_order_id
        )
        self.assertEqual(production_work_order["status"], "archived")
        self.assertEqual(hub["project_state"]["current_stage"], "archived")
        self.assertEqual(
            hub["human_signoffs"][-1]["independent_audit_certificate_id"],
            audited.data["certificate"]["certificate_id"],
        )
        audit_scope = next(
            row
            for row in ledger["scope_states"]
            if row.get("last_event_id") == audit_event_id
        )
        self.assertEqual(audit_scope["closure_state"], "human_signed_off")
        self.assertTrue(audit_scope["can_advance"])
        self.assertEqual(audit_scope["human_signoff"]["decision"], "approve")
        self.assertEqual(
            ledger["human_signoffs"][-1]["scope_key"], audit_scope["scope_key"]
        )
        self.assertEqual(
            service_module.storyboard_agent.summarize(ledger)["metrics"][
                "human_release_signoff_completion_percent"
            ],
            100.0,
        )

    def test_migration_plan_requires_authority_session_and_write_ready_state(self) -> None:
        project_id = str(
            self.service.create_project(
                self.namespace, "migration-plan-authority"
            ).project_id
        )
        work_order_id, session_id = self._begin(
            project_id,
            operation="version_management",
            label="plan-authority",
        )

        def plan(request_session_id: str | None) -> Any:
            return self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="plan",
                target_version="0.5.5",
                expected_current_version="0.5.5",
                migration_id=None,
                request_session_id=request_session_id,
            )

        for field, invalid_value in (
            ("operation", "rewrite_plan"),
            ("mode", "plan_only"),
            ("authorization", "plan_only"),
            ("server_actor_role", "independent_audit"),
        ):
            with self.subTest(authority_field=field):
                with self.service.store.mutate_project(
                    self.namespace, project_id
                ) as (hub, _ledger):
                    work_order = next(
                        row
                        for row in hub["work_orders"]
                        if row["work_order_id"] == work_order_id
                    )
                    original_value = work_order[field]
                    work_order[field] = invalid_value
                try:
                    with self.assertRaisesRegex(
                        ValueError, "project_migration_authority_required"
                    ):
                        plan(session_id)
                finally:
                    with self.service.store.mutate_project(
                        self.namespace, project_id
                    ) as (hub, _ledger):
                        work_order = next(
                            row
                            for row in hub["work_orders"]
                            if row["work_order_id"] == work_order_id
                        )
                        work_order[field] = original_value

        with self.assertRaisesRegex(ValueError, "window_session_mismatch"):
            plan("different-session")

        # Direct/local callers do not have a trusted transport envelope. Keep
        # their established None-session compatibility while still rejecting a
        # supplied session that does not match the server-minted binding above.
        self.assertEqual(plan(None).status, "already_current")

        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (hub, _ledger):
            work_order = next(
                row
                for row in hub["work_orders"]
                if row["work_order_id"] == work_order_id
            )
            work_order["status"] = "validating"
        with self.assertRaisesRegex(ValueError, "work_order_write_forbidden"):
            plan(session_id)

    def test_migration_plan_requires_current_cloud_session(self) -> None:
        cloud_settings = replace(
            self.settings,
            data_dir=Path(self.temp.name) / "migration-plan-cloud-session",
            public_base_url="https://shortdrama.example.test",
            deployment_mode="cloud",
        )
        _server, cloud = create_mcp(cloud_settings)
        from historical_entry_fixture import freeze_legacy_entry
        freeze_legacy_entry(cloud)
        project_id = str(
            cloud.create_project(
                self.namespace, "migration-plan-cloud-session"
            ).project_id
        )
        session_id = "migration-plan-current-session"
        work_order = cloud.begin_task(
            self.namespace,
            project_id=project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="authorize a cloud migration plan",
            targets=["Hub", "Ledger"],
            authoritative_source="current project control state",
            latest_version="0.5.5",
            target_boundary="control state",
            must_keep=["authored artifacts"],
            must_remove=[],
            protected_scope=["authored artifacts"],
            expected_outputs=["migration plan"],
            window_key=session_id,
            request_session_id=session_id,
        )

        def plan(request_session_id: str | None) -> Any:
            return cloud.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=str(work_order.work_order_id),
                action="plan",
                target_version="0.5.5",
                expected_current_version="0.5.5",
                migration_id=None,
                request_session_id=request_session_id,
            )

        with self.assertRaisesRegex(ValueError, "window_session_required"):
            plan(None)
        with self.assertRaisesRegex(ValueError, "window_session_mismatch"):
            plan("different-cloud-session")
        self.assertEqual(plan(session_id).status, "already_current")

    def test_current_hub_duplicate_runtime_certificate_projection_is_migrated_out(
        self,
    ) -> None:
        project_id = self._project("retired-runtime-certificate-projection")
        work_order_id, session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="retired-runtime-certificate-projection",
        )
        recovery_work_order_id, recovery_session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="retired-runtime-certificate-projection-recovery",
        )
        certified = self.service.agent_status(self.namespace, project_id)
        runtime_certificate = certified.data["runtime_test_certificate"]
        certificates_before = (
            self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="certificates"
            )
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        hub["runtime_test_certificates"] = [
            copy.deepcopy(runtime_certificate)
        ]
        self.service.store.save_hub(self.namespace, project_id, hub)

        invalid = self.service.agent_status(self.namespace, project_id)
        self.assertEqual(invalid.status, "migration_required")
        self.assertFalse(invalid.data["state_valid"])
        self.assertTrue(
            any(
                "runtime_test_certificates" in error
                for error in invalid.data["state_errors"]
            )
        )

        plan = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="plan",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=None,
            request_session_id=session_id,
        )
        self.assertEqual(plan.status, "migration_plan_ready")
        self.assertTrue(plan.data["migration_plan"]["candidate_valid"])
        self.assertIn(
            "runtime_test_certificates",
            plan.data["migration_plan"]["hub_changed_paths"],
        )

        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=None,
            request_session_id=session_id,
        )
        self.assertEqual(migrated.status, "migrated")
        self.assertIn(
            "runtime_test_certificates",
            migrated.data["migration"]["changed_paths"],
        )
        self.assertNotIn(
            "runtime_test_certificates",
            self.service.store.load_hub(self.namespace, project_id),
        )
        self.assertEqual(
            self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="certificates"
            ),
            certificates_before,
        )
        current = self.service.agent_status(self.namespace, project_id)
        self.assertTrue(current.data["state_valid"], current.data["state_errors"])
        self.assertEqual(
            current.data["runtime_test_certificate"]["certificate_id"],
            runtime_certificate["certificate_id"],
        )

        migration_id = str(migrated.data["migration"]["migration_id"])
        rolled_back = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(rolled_back.status, "rolled_back")
        self.assertIn(
            "runtime_test_certificates",
            self.service.store.load_hub(self.namespace, project_id),
        )

        # Model a legitimately signed legacy journal whose after-image still
        # carries the retired Hub projection. A fresh current candidate must
        # never be overwritten by this stale after-image.
        stale_journal = copy.deepcopy(rolled_back.data["migration"])
        stale_journal["hub_after"] = copy.deepcopy(
            stale_journal["hub_before"]
        )
        stale_journal["hub_after_sha256"] = (
            self.service.store.project_state_sha256(
                stale_journal["hub_after"]
            )
        )
        stale_journal = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
            value=stale_journal,
        )
        self.assertTrue(
            self.service.store.project_record_is_signed(stale_journal)
        )
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        before_stale_retry = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in sorted(project_dir.rglob("*"))
            if path.is_file()
        }
        with self.assertRaisesRegex(
            ValueError, "migration_record_authority_conflict"
        ):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.5",
                migration_id=migration_id,
                request_session_id=session_id,
            )
        self.assertEqual(
            {
                str(path.relative_to(project_dir)): path.read_bytes()
                for path in sorted(project_dir.rglob("*"))
                if path.is_file()
            },
            before_stale_retry,
        )
        still_invalid = self.service.agent_status(
            self.namespace, project_id
        )
        self.assertEqual(still_invalid.status, "migration_required")
        self.assertFalse(still_invalid.data["state_valid"])

        recovered = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=recovery_work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=None,
            request_session_id=recovery_session_id,
        )
        self.assertEqual(recovered.status, "migrated")
        self.assertNotEqual(
            recovered.data["migration"]["migration_id"], migration_id
        )
        self.assertNotIn(
            "runtime_test_certificates",
            self.service.store.load_hub(self.namespace, project_id),
        )
        recovered_status = self.service.agent_status(
            self.namespace, project_id
        )
        self.assertTrue(
            recovered_status.data["state_valid"],
            recovered_status.data["state_errors"],
        )
        self.assertEqual(
            self.service.store.list_project_records_strict(
                self.namespace, project_id, kind="certificates"
            ),
            certificates_before,
        )

    def test_migration_plan_execute_and_replay_are_non_destructive_and_idempotent(self) -> None:
        project_id = self._project("legacy-state-migration")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="migration"
        )
        artifact = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"historical_storyboard": "must remain byte-identical"},
            file_name="legacy-read-only.json",
            artifact_type="source_extraction",
            version="v0.5.4",
        )
        _record, artifact_path = self.service.store.get_artifact(
            self.namespace, project_id, artifact["artifact_id"]
        )
        artifact_bytes = artifact_path.read_bytes()

        self._make_legacy_state(self.service, self.namespace, project_id)

        invalid = self.service.agent_status(self.namespace, project_id)
        self.assertEqual(invalid.status, "migration_required")
        plan = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="plan",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        self.assertEqual(plan.status, "migration_plan_ready")
        self.assertTrue(plan.data["migration_plan"]["candidate_valid"])
        self.assertFalse(plan.data["migration_plan"]["authored_artifacts_modified"])

        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        self.assertEqual(migrated.status, "migrated")
        migration = migrated.data["migration"]
        self.assertTrue(self.service.store.project_record_is_signed(migration))
        migration_id = str(migration["migration_id"])
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id)["policy"][
                "compact_master_version"
            ],
            "0.5.5",
        )
        _after_record, after_path = self.service.store.get_artifact(
            self.namespace, project_id, artifact["artifact_id"]
        )
        self.assertEqual(after_path.read_bytes(), artifact_bytes)

        replay = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(replay.status, "already_migrated")
        self.assertEqual(replay.data["migration"]["migration_id"], migration_id)
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, project_id, kind="migrations"
                )
            ),
            1,
        )

    def test_committed_migration_replay_rejects_all_authority_binding_mismatches(self) -> None:
        project_id = self._project("committed-migration-binding")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="committed-binding"
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        migration = copy.deepcopy(migrated.data["migration"])
        migration_id = str(migration["migration_id"])
        mismatches = {
            "work_order": ("work_order_id", "WO-unrelated"),
            "from_version": ("from_version", "0.5.3"),
            "target_version": ("target_version", "0.5.6"),
            "runtime_certificate": (
                "runtime_test_certificate_id",
                "CERT-stale-runtime",
            ),
            "ruleset": ("ruleset_version", "R10:stale-ruleset"),
        }
        for label, (field, invalid_value) in mismatches.items():
            with self.subTest(binding=label):
                tampered_binding = copy.deepcopy(migration)
                tampered_binding[field] = invalid_value
                resigned = self.service.store.save_project_record(
                    self.namespace,
                    project_id,
                    kind="migrations",
                    record_id=migration_id,
                    value=tampered_binding,
                )
                self.assertTrue(
                    self.service.store.project_record_is_signed(resigned)
                )
                with self.assertRaisesRegex(
                    ValueError, "migration_record_authority_conflict"
                ):
                    self.service.migrate_project_state(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=work_order_id,
                        action="execute",
                        target_version="0.5.5",
                        expected_current_version="0.5.4",
                        migration_id=migration_id,
                        request_session_id=session_id,
                    )
                restored = self.service.store.save_project_record(
                    self.namespace,
                    project_id,
                    kind="migrations",
                    record_id=migration_id,
                    value=migration,
                )
                self.assertTrue(
                    self.service.store.project_record_is_signed(restored)
                )

        replay = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(replay.status, "already_migrated")

    def test_replay_checkpoint_requires_signed_journal_and_event_derived_suffix(self) -> None:
        project_id = self._project("checkpoint-signed-journal-authority")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="checkpoint-authority"
        )
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        service_module.storyboard_agent.ingest_event(
            ledger,
            {
                "event_id": "LEGACY-SERVICE-E1",
                "timestamp": "2026-08-30T00:00:00Z",
                "episode_id": "EP01",
                "file_name": "legacy-service.json",
                "file_fingerprint": "legacy-service-file-v1",
                "source_version": "legacy-service-source-v1",
                "audit_cycle_id": "LEGACY-SERVICE-CYCLE",
                "task_type": "audit_repair",
                "audit_round": 1,
                "result": "fail_current",
                "checked_shot_count": 1,
                "blockers": [],
                "issues": [{
                    "error_code": "E-LEGACY-SERVICE",
                    "category": "表演链",
                    "severity": "fail",
                    "blocking": True,
                    "shot_ids": ["S01"],
                    "field": "人物表演链",
                    "issue_type": "服务迁移检查点负例",
                    "evidence": "迁移前保留的一条完整历史问题。",
                    "source_sentence": "角色听见真相。",
                    "repair_action": "补齐听话反应。",
                    "impact_scope": ["S01"],
                    "status": "initial",
                }],
            },
        )
        self.service.store.save_ledger(self.namespace, project_id, ledger)
        self._make_legacy_state(self.service, self.namespace, project_id)

        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        self.assertEqual(migrated.status, "migrated")
        migration = copy.deepcopy(migrated.data["migration"])
        migration_id = str(migration["migration_id"])
        migrated_ledger = self.service.store.load_ledger(
            self.namespace, project_id
        )
        checkpoint = copy.deepcopy(migrated_ledger["replay_checkpoint"])
        self.assertTrue(
            self.service.store.ledger_replay_checkpoint_is_authorized(
                self.namespace, project_id, checkpoint
            )
        )

        wrong_checkpoint = copy.deepcopy(checkpoint)
        wrong_checkpoint["migration_id"] = "MIG-NOT-SIGNED-FOR-THIS-PROJECT"
        self.assertFalse(
            self.service.store.ledger_replay_checkpoint_is_authorized(
                self.namespace, project_id, wrong_checkpoint
            )
        )

        record_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "migrations"
            / f"{migration_id}.json"
        )
        unsigned_tamper = copy.deepcopy(migration)
        unsigned_tamper["status"] = "prepared"
        self.service.store.atomic_write_json(record_path, unsigned_tamper)
        self.assertFalse(
            self.service.store.ledger_replay_checkpoint_is_authorized(
                self.namespace, project_id, checkpoint
            )
        )
        self.service.store.atomic_write_json(record_path, migration)
        restored = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
        )
        self.assertTrue(self.service.store.project_record_is_signed(restored))

        forged_ledger = copy.deepcopy(migrated_ledger)
        forged_row = copy.deepcopy(forged_ledger["issues"][0]["history"][0])
        forged_row["event_id"] = "legacy-forged-after-checkpoint"
        forged_ledger["issues"][0]["history"].append(forged_row)
        self.assertEqual(
            list(
                Draft202012Validator(
                    self.service._ledger_schema
                ).iter_errors(forged_ledger)
            ),
            [],
        )
        self.service.store.save_ledger(
            self.namespace, project_id, forged_ledger
        )
        persisted_before = (
            self.service.store.project_dir(self.namespace, project_id)
            / "ledger.json"
        ).read_bytes()
        status = self.service.agent_status(self.namespace, project_id)
        self.assertEqual(status.status, "migration_required")
        self.assertEqual(
            (
                self.service.store.project_dir(self.namespace, project_id)
                / "ledger.json"
            ).read_bytes(),
            persisted_before,
        )

    def test_replay_quality_profile_uses_only_pre_event_checkpoint_authority(self) -> None:
        agent = service_module.storyboard_agent
        ledger = agent.new_ledger(
            "检查点质量前缀", "SBP-CHECKPOINT-QUALITY", "2026-08-30T00:00:00Z"
        )
        agent.ingest_event(
            ledger,
            {
                "event_id": "LEGACY-QUALITY-E1",
                "timestamp": "2026-08-30T00:01:00Z",
                "episode_id": "EP01",
                "file_name": "legacy-quality.json",
                "file_fingerprint": "legacy-quality-file-v1",
                "source_version": "legacy-quality-source-v1",
                "audit_cycle_id": "LEGACY-QUALITY-CYCLE",
                "task_type": "audit_repair",
                "audit_round": 1,
                "result": "fail_current",
                "checked_shot_count": 1,
                "blockers": [],
                "issues": [
                    {
                        "error_code": "E-LEGACY-QUALITY",
                        "category": "表演链",
                        "severity": "fail",
                        "blocking": True,
                        "shot_ids": ["S01"],
                        "field": "人物表演链",
                        "issue_type": "检查点前已知模式",
                        "evidence": "迁移前已有的完整历史问题。",
                        "source_sentence": "角色听见真相。",
                        "repair_action": "补齐听话反应。",
                        "impact_scope": ["S01"],
                        "status": "initial",
                    }
                ],
            },
        )
        issue = ledger["issues"][0]
        pattern_key = str(issue["pattern_key"])
        for row in issue["history"]:
            row["event_id"] = "legacy-baseline-history"
        for row in issue["occurrence_locations"]:
            row["event_id"] = "legacy-baseline-history"

        migrated, _changes = agent.migrate_ledger_to_current(
            ledger,
            timestamp="2026-08-31T00:00:00Z",
            migration_id="MIG-LEGACY-QUALITY-PREFIX",
        )
        self.assertEqual(
            agent.validate_ledger(
                migrated, checkpoint_authorizer=lambda _checkpoint: True
            ),
            [],
        )
        checkpoint_count = int(migrated["replay_checkpoint"]["event_count"])
        self.assertEqual(
            self.service._known_prevention_pattern_keys_before_event(
                migrated, checkpoint_count
            ),
            {pattern_key},
        )
        # The sealed baseline is authority only after its checkpoint boundary;
        # it must not be projected backwards into an event inside that prefix.
        self.assertEqual(
            self.service._known_prevention_pattern_keys_before_event(migrated, 0),
            set(),
        )

        forged = copy.deepcopy(migrated)
        forged["issues"][0]["history"].append(
            copy.deepcopy(forged["issues"][0]["history"][0])
        )
        suffix_errors = agent.validate_ledger(
            forged, checkpoint_authorizer=lambda _checkpoint: True
        )
        self.assertTrue(
            any(
                "appends a legacy-only row after the sealed checkpoint" in error
                for error in suffix_errors
            ),
            suffix_errors,
        )

    def test_execute_recovers_a_signed_prepared_half_written_migration(self) -> None:
        project_id = self._project("migration-half-write-recovery")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="half-write"
        )
        wrong_work_order_id, wrong_session_id = self._begin(
            project_id, operation="rule_improvement", label="half-write-wrong-owner"
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        hub_before = self.service.store.load_hub(self.namespace, project_id)
        ledger_before = self.service.store.load_ledger(self.namespace, project_id)
        migration_id = "MIG-half-write-recovery"
        hub_after, hub_changes = service_module.project_agent.migrate_hub_to_current(
            hub_before,
            timestamp="2026-08-31T12:00:00Z",
            migration_id=migration_id,
        )
        ledger_after, ledger_changes = (
            service_module.storyboard_agent.migrate_ledger_to_current(
                ledger_before,
                timestamp="2026-08-31T12:00:00Z",
            )
        )
        migration_key = service_module.sha256_bytes(
            service_module._stable_json_bytes(
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "from_version": "0.5.4",
                    "target_version": "0.5.5",
                    "spec": "project-state-migration-v055",
                }
            )
        )
        runtime_certificate = self.service._require_runtime_test_certificate(
            self.namespace, project_id
        )
        prepared = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
            value={
                "record_id": migration_id,
                "migration_id": migration_id,
                "migration_key": migration_key,
                "project_id": project_id,
                "work_order_id": work_order_id,
                "status": "prepared",
                "created_at": "2026-08-31T12:00:00Z",
                "from_version": "0.5.4",
                "target_version": "0.5.5",
                "hub_before_sha256": self.service.store.project_state_sha256(
                    hub_before
                ),
                "ledger_before_sha256": self.service.store.project_state_sha256(
                    ledger_before
                ),
                "hub_after_sha256": self.service.store.project_state_sha256(
                    hub_after
                ),
                "ledger_after_sha256": self.service.store.project_state_sha256(
                    ledger_after
                ),
                "hub_before": hub_before,
                "ledger_before": ledger_before,
                "hub_after": hub_after,
                "ledger_after": ledger_after,
                "runtime_test_certificate_id": runtime_certificate[
                    "certificate_id"
                ],
                "ruleset_version": self.service._ruleset_version,
                "validator_version": self.service._validator_version(),
                "changed_paths": hub_changes + ledger_changes,
                "preserves_artifacts": True,
            },
        )
        self.assertTrue(self.service.store.project_record_is_signed(prepared))

        # Simulate a crash after hub.json replacement and before ledger.json.
        self.service.store.atomic_write_json(
            self.service.store.project_dir(self.namespace, project_id)
            / "hub.json",
            hub_after,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            prepared["hub_after_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            prepared["ledger_before_sha256"],
        )

        with self.assertRaisesRegex(ValueError, "migration_record_authority_conflict"):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=wrong_work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id=migration_id,
                request_session_id=wrong_session_id,
            )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            prepared["hub_after_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            prepared["ledger_before_sha256"],
        )

        before_missing_cas = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(ValueError, "expected_current_version is required"):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version=None,
                migration_id=migration_id,
                request_session_id=session_id,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before_missing_cas)

        recovered = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(recovered.status, "migration_recovered")
        journal = recovered.data["migration"]
        self.assertEqual(journal["status"], "committed")
        self.assertTrue(self.service.store.project_record_is_signed(journal))
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            journal["hub_after_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            journal["ledger_after_sha256"],
        )
        self.assertTrue(
            self.service.agent_status(self.namespace, project_id).data["state_valid"]
        )

    def test_prepared_migration_selection_is_exact_and_cas_bound(self) -> None:
        for explicit_id in (True, False):
            with self.subTest(explicit_id=explicit_id):
                label = f"prepared-selector-{explicit_id}"
                project_id = self._project(label)
                work_order_id, session_id = self._begin(
                    project_id,
                    operation="rule_improvement",
                    label=label,
                )
                self._make_legacy_state(
                    self.service, self.namespace, project_id
                )
                migration_id = f"MIG-{label}"
                prepared = self._save_prepared_migration(
                    project_id=project_id,
                    work_order_id=work_order_id,
                    migration_id=migration_id,
                )
                recovered = self.service.migrate_project_state(
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    action="execute",
                    target_version="0.5.5",
                    expected_current_version="0.5.4",
                    migration_id=migration_id if explicit_id else None,
                    request_session_id=session_id,
                )
                self.assertEqual(recovered.status, "migration_recovered")
                self.assertEqual(
                    recovered.data["migration"]["migration_id"],
                    prepared["migration_id"],
                )

        fresh_project_id = self._project("explicit-new-migration-id")
        fresh_work_order_id, fresh_session_id = self._begin(
            fresh_project_id,
            operation="rule_improvement",
            label="explicit-new-migration-id",
        )
        self._make_legacy_state(
            self.service, self.namespace, fresh_project_id
        )
        fresh_migration_id = "MIG-explicit-new-authority"
        fresh = self.service.migrate_project_state(
            self.namespace,
            project_id=fresh_project_id,
            work_order_id=fresh_work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=fresh_migration_id,
            request_session_id=fresh_session_id,
        )
        self.assertEqual(fresh.status, "migrated")
        self.assertEqual(
            fresh.data["migration"]["migration_id"], fresh_migration_id
        )

        project_id = self._project("prepared-selector-rejections")
        work_order_id, session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="prepared-selector-rejections",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migration_id = "MIG-prepared-selector-authority"
        prepared = self._save_prepared_migration(
            project_id=project_id,
            work_order_id=work_order_id,
            migration_id=migration_id,
        )
        rejection_cases = (
            ("unknown_explicit_id", "MIG-unrelated-explicit", "0.5.4"),
            ("cas_mismatch", migration_id, "0.5.3"),
            ("invalid_explicit_id", "../invalid", "0.5.4"),
        )
        for label, requested_id, expected_version in rejection_cases:
            with self.subTest(rejection=label):
                before = self._project_tree_bytes(project_id)
                with self.assertRaises(ValueError):
                    self.service.migrate_project_state(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=work_order_id,
                        action="execute",
                        target_version="0.5.5",
                        expected_current_version=expected_version,
                        migration_id=requested_id,
                        request_session_id=session_id,
                    )
                self.assertEqual(
                    self._project_tree_bytes(project_id), before
                )

        damaged = copy.deepcopy(prepared)
        damaged["target_version"] = "damaged-without-resigning"
        record_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "migrations"
            / f"{migration_id}.json"
        )
        self.service.store.atomic_write_json(record_path, damaged)
        before_damaged_retry = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "project_record_integrity_mismatch"
        ):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id=migration_id,
                request_session_id=session_id,
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_damaged_retry
        )

    def test_prepared_candidate_is_validated_before_any_state_write(self) -> None:
        project_id = self._project("prepared-candidate-prevalidation")
        work_order_id, session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="prepared-candidate-prevalidation",
        )
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        service_module.storyboard_agent.ingest_event(
            ledger,
            {
                "event_id": "LEGACY-PREPARED-PREVALIDATION",
                "timestamp": "2026-08-30T00:00:00Z",
                "episode_id": "EP01",
                "file_name": "legacy-prepared.json",
                "file_fingerprint": "legacy-prepared-file-v1",
                "source_version": "legacy-prepared-source-v1",
                "audit_cycle_id": "LEGACY-PREPARED-CYCLE",
                "task_type": "audit_repair",
                "audit_round": 1,
                "result": "fail_current",
                "checked_shot_count": 1,
                "blockers": [],
                "issues": [
                    {
                        "error_code": "E-PREPARED-PREVALIDATION",
                        "category": "表演链",
                        "severity": "fail",
                        "blocking": True,
                        "shot_ids": ["S01"],
                        "field": "人物表演链",
                        "issue_type": "prepared候选预校验",
                        "evidence": "迁移前保留的完整问题证据。",
                        "source_sentence": "角色听见真相。",
                        "repair_action": "补齐听话反应。",
                        "impact_scope": ["S01"],
                        "status": "initial",
                    }
                ],
            },
        )
        self.service.store.save_ledger(self.namespace, project_id, ledger)
        self._make_legacy_state(self.service, self.namespace, project_id)
        migration_id = "MIG-prepared-candidate-prevalidation"
        prepared = self._save_prepared_migration(
            project_id=project_id,
            work_order_id=work_order_id,
            migration_id=migration_id,
        )
        self.assertIn(
            "replay_checkpoint", prepared["ledger_after"]
        )

        invalid_schema = copy.deepcopy(prepared)
        invalid_schema["hub_after"].pop("policy")
        invalid_schema["hub_after_sha256"] = (
            self.service.store.project_state_sha256(
                invalid_schema["hub_after"]
            )
        )
        invalid_checkpoint = copy.deepcopy(prepared)
        invalid_checkpoint["ledger_after"]["replay_checkpoint"][
            "migration_id"
        ] = "MIG-forged-checkpoint"
        invalid_checkpoint["ledger_after_sha256"] = (
            self.service.store.project_state_sha256(
                invalid_checkpoint["ledger_after"]
            )
        )
        for label, invalid_record in (
            ("schema", invalid_schema),
            ("checkpoint_authority", invalid_checkpoint),
        ):
            with self.subTest(validation=label):
                resigned = self._rewrite_signed_migration_fixture(
                    project_id,
                    migration_id,
                    invalid_record,
                )
                self.assertTrue(
                    self.service.store.project_record_is_signed(resigned)
                )
                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError, "migration_candidate_invalid"
                ):
                    self.service.migrate_project_state(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=work_order_id,
                        action="execute",
                        target_version="0.5.5",
                        expected_current_version="0.5.4",
                        migration_id=migration_id,
                        request_session_id=session_id,
                    )
                self.assertEqual(self._project_tree_bytes(project_id), before)

        restored = self._rewrite_signed_migration_fixture(
            project_id,
            migration_id,
            prepared,
        )
        self.assertTrue(self.service.store.project_record_is_signed(restored))
        with patch.object(
            service_module.project_agent,
            "validate_hub",
            return_value=["forced-current-validator-rejection"],
        ):
            before_validator_rejection = self._project_tree_bytes(project_id)
            with self.assertRaisesRegex(
                ValueError, "forced-current-validator-rejection"
            ):
                self.service.migrate_project_state(
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    action="execute",
                    target_version="0.5.5",
                    expected_current_version="0.5.4",
                    migration_id=migration_id,
                    request_session_id=session_id,
                )
            self.assertEqual(
                self._project_tree_bytes(project_id),
                before_validator_rejection,
            )

    def test_storage_requires_signed_wal_before_accepting_after_images(self) -> None:
        project_id = self._project("migration-signed-wal-required")
        work_order_id, _session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="signed-wal-required",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migration_id = "MIG-signed-wal-required"
        prepared = self._save_prepared_migration(
            project_id=project_id,
            work_order_id=work_order_id,
            migration_id=migration_id,
        )
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        self.service.store.atomic_write_json(
            project_dir / "hub.json", prepared["hub_after"]
        )
        self.service.store.atomic_write_json(
            project_dir / "ledger.json", prepared["ledger_after"]
        )
        (project_dir / "migrations" / f"{migration_id}.json").unlink()

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "signed migration journal required"
        ):
            self.service.store.commit_project_state_migration(
                self.namespace,
                project_id,
                migration_id=migration_id,
                migration_key=str(prepared["migration_key"]),
                work_order_id=work_order_id,
                from_version="0.5.4",
                target_version="0.5.5",
                hub_before=prepared["hub_before"],
                ledger_before=prepared["ledger_before"],
                hub_after=prepared["hub_after"],
                ledger_after=prepared["ledger_after"],
                runtime_test_certificate_id=str(
                    prepared["runtime_test_certificate_id"]
                ),
                runtime_test_certificate_key=(
                    self.service._runtime_certificate_key()
                ),
                ruleset_version=self.service._ruleset_version,
                validator_version=self.service._validator_version(),
                changed_paths=list(prepared["changed_paths"]),
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_storage_rechecks_runtime_certificate_inside_migration_lock(self) -> None:
        project_id = self._project("migration-runtime-cert-atomic")
        work_order_id, _session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="runtime-cert-atomic",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        hub_before = self.service.store.load_hub(self.namespace, project_id)
        ledger_before = self.service.store.load_ledger(
            self.namespace, project_id
        )
        migration_id = "MIG-runtime-cert-atomic"
        timestamp = "2026-08-31T14:00:00Z"
        hub_after, hub_changes = (
            service_module.project_agent.migrate_hub_to_current(
                hub_before,
                timestamp=timestamp,
                migration_id=migration_id,
            )
        )
        ledger_after, ledger_changes = (
            service_module.storyboard_agent.migrate_ledger_to_current(
                ledger_before,
                timestamp=timestamp,
                migration_id=migration_id,
            )
        )
        stale_pass = self.service._require_runtime_test_certificate(
            self.namespace, project_id
        )
        fail_id = "CERT-runtime-fail-after-pass"
        fail_current = {
            key: copy.deepcopy(value)
            for key, value in stale_pass.items()
            if key
            not in {
                "record_id",
                "project_id",
                "created_at",
                "integrity_hmac",
            }
        }
        fail_current.update(
            {
                "certificate_id": fail_id,
                "result": "fail_current",
                "created_at": "9999-12-31T23:59:59Z",
            }
        )
        self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=fail_id,
            value=fail_current,
        )
        self.assertIsNone(
            self.service._matching_runtime_certificate(
                self.namespace, project_id
            )
        )

        before = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "migration_runtime_certificate_conflict"
        ):
            self.service.store.commit_project_state_migration(
                self.namespace,
                project_id,
                migration_id=migration_id,
                migration_key=self.service._project_migration_key(
                    project_id=project_id,
                    work_order_id=work_order_id,
                    from_version="0.5.4",
                    target_version="0.5.5",
                ),
                work_order_id=work_order_id,
                from_version="0.5.4",
                target_version="0.5.5",
                hub_before=hub_before,
                ledger_before=ledger_before,
                hub_after=hub_after,
                ledger_after=ledger_after,
                runtime_test_certificate_id=str(
                    stale_pass["certificate_id"]
                ),
                runtime_test_certificate_key=(
                    self.service._runtime_certificate_key()
                ),
                ruleset_version=self.service._ruleset_version,
                validator_version=self.service._validator_version(),
                changed_paths=hub_changes + ledger_changes,
            )
        self.assertEqual(self._project_tree_bytes(project_id), before)

    def test_prepared_migration_can_be_cancelled_without_manual_file_repair(
        self,
    ) -> None:
        project_id = self._project("prepared-migration-cancel")
        work_order_id, session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="prepared-migration-cancel",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migration_id = "MIG-prepared-migration-cancel"
        prepared = self._save_prepared_migration(
            project_id=project_id,
            work_order_id=work_order_id,
            migration_id=migration_id,
        )
        with self.assertRaisesRegex(ValueError, "migration_commit_in_progress"):
            self.service.store.save_hub(
                self.namespace,
                project_id,
                self.service.store.load_hub(self.namespace, project_id),
            )

        cancelled = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(cancelled.status, "rolled_back")
        self.assertEqual(cancelled.data["migration"]["status"], "rolled_back")
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id),
            prepared["hub_before"],
        )
        self.assertEqual(
            self.service.store.load_ledger(self.namespace, project_id),
            prepared["ledger_before"],
        )
        current_hub = self.service.store.load_hub(
            self.namespace, project_id
        )
        self.service.store.save_hub(
            self.namespace, project_id, current_hub
        )

    def test_any_project_rolling_back_journal_blocks_execute(self) -> None:
        project_id = self._project("project-global-rolling-back")
        first_work_order_id, first_session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="project-global-rolling-back-first",
        )
        other_work_order_id, other_session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="project-global-rolling-back-other",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=first_work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=first_session_id,
        )
        other_migration_id = "MIG-other-committed-history"
        other_migration = copy.deepcopy(migrated.data["migration"])
        other_migration.update(
            {
                "record_id": other_migration_id,
                "migration_id": other_migration_id,
                "migration_key": self.service._project_migration_key(
                    project_id=project_id,
                    work_order_id=other_work_order_id,
                    from_version="0.5.4",
                    target_version="0.5.5",
                ),
                "work_order_id": other_work_order_id,
            }
        )
        self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=other_migration_id,
            value=other_migration,
        )
        rolling_back = copy.deepcopy(migrated.data["migration"])
        rolling_back["status"] = "rolling_back"
        rolling_back["rollback_started_at"] = "2026-08-31T13:00:00Z"
        rolling_back = self.service.store.save_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=rolling_back["migration_id"],
            value=rolling_back,
        )
        project_dir = self.service.store.project_dir(
            self.namespace, project_id
        )
        # Model a hard-crash image directly: ordinary storage writers are now
        # fenced once the signed rolling_back intent exists.
        self.service.store.atomic_write_json(
            project_dir / "hub.json", rolling_back["hub_before"]
        )
        self.service.store.atomic_write_json(
            project_dir / "ledger.json", rolling_back["ledger_before"]
        )

        for label, write_attempt in (
            (
                "hub_ledger_mutation",
                lambda: self.service.store.mutate_project(
                    self.namespace, project_id
                ),
            ),
            (
                "artifact_write",
                lambda: self.service.store.save_artifact_bytes(
                    self.namespace,
                    project_id,
                    b"must-not-persist",
                    file_name="rollback-fenced.txt",
                    artifact_type="diagnostic",
                    version="v0",
                    artifact_id="ART-rollback-fenced",
                ),
            ),
        ):
            with self.subTest(project_write_fence=label):
                before_fenced_write = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError, "migration_rollback_in_progress"
                ):
                    attempted = write_attempt()
                    if label == "hub_ledger_mutation":
                        with attempted as (hub, _ledger):
                            hub["forbidden_while_rolling_back"] = True
                self.assertEqual(
                    self._project_tree_bytes(project_id),
                    before_fenced_write,
                )

        before_cross_rollback = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "migration_rollback_in_progress"
        ):
            self.service.store.rollback_project_state_migration(
                self.namespace,
                project_id,
                other_migration_id,
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_cross_rollback
        )

        for requested_id in (None, "MIG-unrelated-new-id"):
            with self.subTest(requested_id=requested_id):
                before = self._project_tree_bytes(project_id)
                with self.assertRaisesRegex(
                    ValueError, "migration_rollback_in_progress"
                ):
                    self.service.migrate_project_state(
                        self.namespace,
                        project_id=project_id,
                        work_order_id=other_work_order_id,
                        action="execute",
                        target_version="0.5.5",
                        expected_current_version="0.5.4",
                        migration_id=requested_id,
                        request_session_id=other_session_id,
                    )
                self.assertEqual(self._project_tree_bytes(project_id), before)

        direct_before = self._project_tree_bytes(project_id)
        direct_hub = self.service.store.load_hub(
            self.namespace, project_id
        )
        direct_ledger = self.service.store.load_ledger(
            self.namespace, project_id
        )
        direct_migration_id = "MIG-direct-storage-fenced"
        direct_key = self.service._project_migration_key(
            project_id=project_id,
            work_order_id=other_work_order_id,
            from_version="0.5.4",
            target_version="0.5.5",
        )
        with self.assertRaisesRegex(
            ValueError, "migration_rollback_in_progress"
        ):
            self.service.store.commit_project_state_migration(
                self.namespace,
                project_id,
                migration_id=direct_migration_id,
                migration_key=direct_key,
                work_order_id=other_work_order_id,
                from_version="0.5.4",
                target_version="0.5.5",
                hub_before=direct_hub,
                ledger_before=direct_ledger,
                hub_after=copy.deepcopy(direct_hub),
                ledger_after=copy.deepcopy(direct_ledger),
                runtime_test_certificate_id="RTC-direct-storage-fenced",
                runtime_test_certificate_key=self.service._runtime_certificate_key(),
                ruleset_version=self.service._ruleset_version,
                validator_version=self.service._validator_version(),
                changed_paths=[],
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), direct_before
        )

    def test_rolled_back_exact_reexecute_is_time_stable_and_key_unique(
        self,
    ) -> None:
        project_id = self._project("rolled-back-exact-reexecute")
        work_order_id, session_id = self._begin(
            project_id,
            operation="rule_improvement",
            label="rolled-back-exact-reexecute",
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        migration = copy.deepcopy(migrated.data["migration"])
        migration_id = str(migration["migration_id"])
        migration_key = str(migration["migration_key"])
        rolled_back = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(rolled_back.status, "rolled_back")

        before_alternate = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(
            ValueError, "migration_record_authority_conflict"
        ):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id="MIG-ALTERNATE-SAME-KEY",
                request_session_id=session_id,
            )
        self.assertEqual(
            self._project_tree_bytes(project_id), before_alternate
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records_strict(
                    self.namespace, project_id, kind="migrations"
                )
            ),
            1,
        )

        original_atomic_write = self.service.store.atomic_write_json
        ledger_crashed = False

        def crash_between_reexecution_files(path, value):
            nonlocal ledger_crashed
            if path.name == "ledger.json" and not ledger_crashed:
                ledger_crashed = True
                raise RuntimeError("simulated rolled-back reexecute crash")
            return original_atomic_write(path, value)

        with patch.object(
            self.service.store,
            "atomic_write_json",
            side_effect=crash_between_reexecution_files,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "rolled-back reexecute crash"
            ):
                self.service.migrate_project_state(
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    action="execute",
                    target_version="0.5.5",
                    expected_current_version="0.5.4",
                    migration_id=migration_id,
                    request_session_id=session_id,
                )
        self.assertTrue(ledger_crashed)
        interrupted_record = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
        )
        self.assertEqual(interrupted_record["status"], "replaying")
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            migration["hub_after_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            migration["ledger_before_sha256"],
        )

        fenced_replay = self._project_tree_bytes(project_id)
        with self.assertRaisesRegex(ValueError, "migration_commit_in_progress"):
            self.service.store.save_hub(
                self.namespace,
                project_id,
                self.service.store.load_hub(self.namespace, project_id),
            )
        with self.assertRaisesRegex(ValueError, "migration_record_authority_conflict"):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id="MIG-REPLAY-UNRELATED",
                request_session_id=session_id,
            )
        self.assertEqual(self._project_tree_bytes(project_id), fenced_replay)

        with patch.object(
            service_module,
            "utc_stamp",
            return_value="2099-12-31T23:59:59Z",
        ):
            replayed = self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id=migration_id,
                request_session_id=session_id,
            )
        self.assertEqual(replayed.status, "migration_recovered")
        self.assertEqual(
            replayed.data["migration"]["migration_id"], migration_id
        )
        self.assertEqual(
            replayed.data["migration"]["migration_key"], migration_key
        )
        self.assertEqual(
            replayed.data["migration"]["status"], "committed"
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            migration["hub_after_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            migration["ledger_after_sha256"],
        )
        records = self.service.store.list_project_records_strict(
            self.namespace, project_id, kind="migrations"
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["migration_id"], migration_id)
        self.assertEqual(records[0]["migration_key"], migration_key)

    def test_migration_rollback_replay_is_idempotent(self) -> None:
        project_id = self._project("migration-rollback-replay")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="rollback"
        )
        wrong_work_order_id, wrong_session_id = self._begin(
            project_id, operation="rule_improvement", label="rollback-wrong-owner"
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        migration_id = str(migrated.data["migration"]["migration_id"])

        with self.assertRaisesRegex(ValueError, "migration_record_authority_conflict"):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=wrong_work_order_id,
                action="rollback",
                target_version="0.5.5",
                expected_current_version="0.5.5",
                migration_id=migration_id,
                request_session_id=wrong_session_id,
            )
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id)["policy"][
                "compact_master_version"
            ],
            "0.5.5",
        )

        first = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(first.status, "rolled_back")
        self.assertEqual(first.data["migration"]["status"], "rolled_back")
        before_replay_hub = self.service.store.project_state_sha256(
            self.service.store.load_hub(self.namespace, project_id)
        )
        before_replay_ledger = self.service.store.project_state_sha256(
            self.service.store.load_ledger(self.namespace, project_id)
        )

        replay = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(replay.status, "rolled_back")
        self.assertTrue(replay.data["migration"]["idempotent_replay"])
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            before_replay_hub,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            before_replay_ledger,
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, project_id, kind="migrations"
                )
            ),
            1,
        )

    def test_migration_rollback_recovers_signed_half_write_and_rejects_external_hash(self) -> None:
        project_id = self._project("migration-rollback-half-write")
        work_order_id, session_id = self._begin(
            project_id, operation="rule_improvement", label="rollback-half-write"
        )
        self._make_legacy_state(self.service, self.namespace, project_id)
        migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=session_id,
        )
        migration_id = str(migrated.data["migration"]["migration_id"])
        migration = copy.deepcopy(migrated.data["migration"])
        ledger_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "ledger.json"
        )
        original_atomic_write = self.service.store.atomic_write_json
        injected = {"raised": False}

        def fail_before_ledger_rollback(path: Path, value: Any) -> None:
            if Path(path) == ledger_path and not injected["raised"]:
                injected["raised"] = True
                raise OSError("injected rollback failure before ledger write")
            original_atomic_write(path, value)

        with patch.object(
            self.service.store,
            "atomic_write_json",
            side_effect=fail_before_ledger_rollback,
        ):
            with self.assertRaisesRegex(
                OSError, "injected rollback failure before ledger write"
            ):
                self.service.migrate_project_state(
                    self.namespace,
                    project_id=project_id,
                    work_order_id=work_order_id,
                    action="rollback",
                    target_version="0.5.5",
                    expected_current_version="0.5.5",
                    migration_id=migration_id,
                    request_session_id=session_id,
                )
        self.assertTrue(injected["raised"])
        interrupted = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="migrations",
            record_id=migration_id,
        )
        self.assertEqual(interrupted["status"], "rolling_back")
        self.assertTrue(self.service.store.project_record_is_signed(interrupted))
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            migration["hub_before_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            migration["ledger_after_sha256"],
        )

        rolling_back_snapshot = {
            "hub": self.service.store.load_hub(self.namespace, project_id),
            "ledger": self.service.store.load_ledger(
                self.namespace, project_id
            ),
            "journal": interrupted,
        }
        with self.assertRaisesRegex(
            ValueError, "migration_rollback_in_progress"
        ):
            self.service.migrate_project_state(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                action="execute",
                target_version="0.5.5",
                expected_current_version="0.5.4",
                migration_id=migration_id,
                request_session_id=session_id,
            )
        self.assertEqual(
            {
                "hub": self.service.store.load_hub(
                    self.namespace, project_id
                ),
                "ledger": self.service.store.load_ledger(
                    self.namespace, project_id
                ),
                "journal": self.service.store.get_project_record(
                    self.namespace,
                    project_id,
                    kind="migrations",
                    record_id=migration_id,
                ),
            },
            rolling_back_snapshot,
        )

        recovered = self.service.migrate_project_state(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            action="rollback",
            target_version="0.5.5",
            expected_current_version="0.5.5",
            migration_id=migration_id,
            request_session_id=session_id,
        )
        self.assertEqual(recovered.status, "rolled_back")
        self.assertEqual(recovered.data["migration"]["status"], "rolled_back")
        self.assertTrue(
            self.service.store.project_record_is_signed(
                recovered.data["migration"]
            )
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            migration["hub_before_sha256"],
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            migration["ledger_before_sha256"],
        )

        conflict_project_id = self._project("migration-rollback-external-hash")
        conflict_work_order_id, conflict_session_id = self._begin(
            conflict_project_id,
            operation="rule_improvement",
            label="rollback-external-hash",
        )
        self._make_legacy_state(
            self.service, self.namespace, conflict_project_id
        )
        conflict_migrated = self.service.migrate_project_state(
            self.namespace,
            project_id=conflict_project_id,
            work_order_id=conflict_work_order_id,
            action="execute",
            target_version="0.5.5",
            expected_current_version="0.5.4",
            migration_id=None,
            request_session_id=conflict_session_id,
        )
        conflict_id = str(
            conflict_migrated.data["migration"]["migration_id"]
        )
        conflict_hub = self.service.store.load_hub(
            self.namespace, conflict_project_id
        )
        conflict_hub["external_writer_marker"] = "not-in-migration-journal"
        self.service.store.save_hub(
            self.namespace, conflict_project_id, conflict_hub
        )
        external_hub_sha = self.service.store.project_state_sha256(conflict_hub)
        self.assertNotIn(
            external_hub_sha,
            {
                conflict_migrated.data["migration"]["hub_before_sha256"],
                conflict_migrated.data["migration"]["hub_after_sha256"],
            },
        )
        with self.assertRaisesRegex(
            ValueError, "migration_rollback_cas_mismatch"
        ):
            self.service.migrate_project_state(
                self.namespace,
                project_id=conflict_project_id,
                work_order_id=conflict_work_order_id,
                action="rollback",
                target_version="0.5.5",
                expected_current_version="0.5.5",
                migration_id=conflict_id,
                request_session_id=conflict_session_id,
            )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(
                    self.namespace, conflict_project_id
                )
            ),
            external_hub_sha,
        )
        conflict_journal = self.service.store.get_project_record(
            self.namespace,
            conflict_project_id,
            kind="migrations",
            record_id=conflict_id,
        )
        self.assertEqual(conflict_journal["status"], "committed")
        self.assertTrue(
            self.service.store.project_record_is_signed(conflict_journal)
        )

    def test_legacy_pass_without_independent_certificate_is_pending(self) -> None:
        project_id = self._project("legacy-pass-is-not-trusted")
        artifact = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"legacy": "storyboard"},
            file_name="legacy-pass.json",
            artifact_type="storyboard",
            version="v0.5.4",
            metadata={
                "audit_status": "pass",
                "independent_audit_status": "pass",
                "issue_count": 0,
                "canonical_sha256": "a" * 64,
            },
        )
        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (hub, _ledger):
            hub["project_state"]["latest_artifacts"]["storyboard"] = {
                "artifact_id": artifact["artifact_id"],
                "version": artifact["version"],
            }

        status = self.service.agent_status(self.namespace, project_id)
        latest = status.data["latest_storyboard"]
        self.assertEqual(latest["effective_audit_status"], "PENDING_AUDIT")
        self.assertEqual(latest["audit_status"], "PENDING_AUDIT")
        self.assertIsNone(latest["issue_count"])
        self.assertEqual(latest["legacy_audit_status"], "pass")
        self.assertEqual(latest["legacy_issue_count"], 0)
        self.assertFalse(latest["legacy_claim_accepted"])
        self.assertEqual(latest["audit_evidence_state"], "missing_certificate")

    def test_same_root_cause_across_batches_normalizes_to_one_pattern(self) -> None:
        agent = service_module.storyboard_agent
        ledger = agent.new_ledger(
            "跨批次模式测试", "SBP-PATTERN", "2026-08-31T00:00:00Z"
        )

        def event(
            event_id: str,
            episode_id: str,
            file_name: str,
            shot_id: str,
            index: int,
        ) -> dict[str, Any]:
            return {
                "event_id": event_id,
                "timestamp": f"2026-08-31T00:0{index}:00Z",
                "episode_id": episode_id,
                "file_name": file_name,
                "file_fingerprint": f"sha256:{event_id}",
                "source_version": f"v{index}",
                "audit_cycle_id": f"CYCLE-{event_id}",
                "task_type": "production_draft_preflight",
                "audit_class": "production_draft_preflight",
                "audit_round": 1,
                "result": "fail_current",
                "checked_shot_count": 30,
                "issues": [
                    {
                        "error_code": "E-TRANSITION-DUP",
                        "category": "正式母版单镜执行完整性",
                        "severity": "fail",
                        "blocking": True,
                        "shot_ids": [shot_id],
                        "field": f"shots[{index}].切镜/转场提示词",
                        "issue_type": "重复转场",
                        "root_cause": "转场模板重复拼接",
                        "evidence": "同一转场短句连续出现两次",
                        "source_sentence": "候选草稿",
                        "repair_action": "修复转场模板并回归",
                        "status": "initial",
                    }
                ],
            }

        agent.ingest_event(ledger, event("EV-EP01", "EP01", "ep01.json", "EP01-001", 1))
        agent.ingest_event(ledger, event("EV-EP08", "EP08", "ep08.json", "EP08-021", 2))

        # Concrete occurrences remain separately auditable, while their
        # reusable prevention identity is one cross-batch pattern.
        self.assertEqual(len(ledger["issues"]), 2)
        self.assertEqual(
            len({row["pattern_key"] for row in ledger["issues"]}), 1
        )
        self.assertTrue(
            all(int(row["occurrence_count"]) == 1 for row in ledger["issues"])
        )
        self.assertEqual(
            sum(int(row["occurrence_count"]) for row in ledger["issues"]), 2
        )
        locations = [
            location
            for row in ledger["issues"]
            for location in row["occurrence_locations"]
        ]
        self.assertEqual(len(locations), 2)
        self.assertEqual(
            {row["episode_id"] for row in locations},
            {"EP01", "EP08"},
        )
        profile = agent.active_prevention_profile(ledger)
        self.assertEqual(profile["known_pattern_count"], 1)
        self.assertEqual(profile["blocking_pattern_count"], 1)
        self.assertEqual(profile["patterns"][0]["occurrence_count"], 2)
        self.assertTrue(profile["patterns"][0]["block_generation"])

    def test_issue_query_minimum_occurrence_uses_cross_batch_pattern_count(self) -> None:
        project_id = self._project("query-cross-batch-pattern-occurrences")
        agent = service_module.storyboard_agent

        def event(
            event_id: str,
            episode_id: str,
            file_name: str,
            shot_id: str,
            index: int,
        ) -> dict[str, Any]:
            return {
                "event_id": event_id,
                "timestamp": f"2026-08-31T01:0{index}:00Z",
                "episode_id": episode_id,
                "file_name": file_name,
                "file_fingerprint": f"sha256:{event_id}",
                "source_version": f"batch-{index}",
                "audit_cycle_id": f"CYCLE-{event_id}",
                "task_type": "production_draft_preflight",
                "audit_class": "production_draft_preflight",
                "audit_round": 1,
                "result": "fail_current",
                "checked_shot_count": 30,
                "issues": [
                    {
                        "error_code": "E-KEYFRAME-DYNAMIC",
                        "category": "正式母版单镜执行完整性",
                        "severity": "fail",
                        "blocking": True,
                        "shot_ids": [shot_id],
                        "field": f"shots[{index}].起始状态",
                        "issue_type": "关键帧动态文字",
                        "root_cause": "动态字段错误投影进关键帧",
                        "evidence": "关键帧含有随后动作",
                        "source_sentence": "候选草稿",
                        "repair_action": "改为字段白名单投影",
                        "status": "initial",
                    }
                ],
            }

        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (_hub, ledger):
            agent.ingest_event(
                ledger,
                event("EV-Q-EP02", "EP02", "ep02.json", "EP02-004", 1),
            )
            agent.ingest_event(
                ledger,
                event("EV-Q-EP09", "EP09", "ep09.json", "EP09-019", 2),
            )
        hub_before = self.service.store.project_state_sha256(
            self.service.store.load_hub(self.namespace, project_id)
        )
        ledger_before = self.service.store.project_state_sha256(
            self.service.store.load_ledger(self.namespace, project_id)
        )

        queried = self.service.query_project_issues(
            self.namespace,
            project_id=project_id,
            status="all",
            error_codes=None,
            episode_ids=None,
            recurrence_states=None,
            minimum_occurrence_count=2,
            include_history=True,
            limit=50,
            cursor=0,
        )
        self.assertEqual(queried.status, "ok")
        self.assertEqual(queried.data["total"], 2)
        self.assertEqual(
            len({row["issue_id"] for row in queried.data["issues"]}), 2
        )
        self.assertEqual(
            len({row["pattern_key"] for row in queried.data["issues"]}), 1
        )
        self.assertTrue(
            all(
                row["pattern_summary"]["occurrence_count"] == 2
                for row in queried.data["issues"]
            )
        )
        self.assertEqual(
            {
                row["episode_id"]
                for row in queried.data["issues"]
            },
            {"EP02", "EP09"},
        )
        too_high = self.service.query_project_issues(
            self.namespace,
            project_id=project_id,
            status="all",
            error_codes=None,
            episode_ids=None,
            recurrence_states=None,
            minimum_occurrence_count=3,
            include_history=False,
            limit=50,
            cursor=0,
        )
        self.assertEqual(too_high.data["total"], 0)
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, project_id)
            ),
            hub_before,
        )
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_ledger(self.namespace, project_id)
            ),
            ledger_before,
        )

    def test_record_audit_uses_stable_field_not_issue_location_for_pattern(self) -> None:
        project_id = self._project("record-audit-stable-field-pattern")
        draft = valid_storyboard()
        artifact_a = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"batch": "A"},
            file_name="audit-batch-a.json",
            artifact_type="source_extraction",
            version="v1",
        )
        artifact_b = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"batch": "B"},
            file_name="audit-batch-b.json",
            artifact_type="source_extraction",
            version="v2",
        )
        shot_id = str(draft["shots"][0]["镜头编号"])

        def report(issue_id: str, location: str) -> dict[str, Any]:
            return {
                "status": "fail_current",
                "issues": [
                    {
                        "issue_id": issue_id,
                        "error_code": "E-STATE-ATOM-001",
                        "severity": "fail",
                        "blocking": True,
                        "shot_ids": [shot_id],
                        "field": "起始状态",
                        "location": location,
                        "issue_type": "起始状态提前包含结果",
                        "root_cause": "状态原子模板混入镜尾结果",
                        "evidence": "起始状态已经写入本镜完成态",
                        "repair_action": "将完成态移回结束状态",
                    }
                ],
            }

        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (_hub, ledger):
            first = self.service._record_audit(
                namespace=self.namespace,
                project_id=project_id,
                artifact_record=artifact_a,
                storyboard=draft,
                report=report("SEM-A-UNSTABLE-ID", "semantic.issue.SEM-A-UNSTABLE-ID"),
                ledger=ledger,
                audit_cycle_id="AUD-BATCH-A",
                audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
                audit_cache_key="CACHE-BATCH-A",
                event_id="EVT-STABLE-FIELD-A",
            )
            second = self.service._record_audit(
                namespace=self.namespace,
                project_id=project_id,
                artifact_record=artifact_b,
                storyboard=draft,
                report=report("SEM-B-OTHER-ID", "semantic.issue.SEM-B-OTHER-ID"),
                ledger=ledger,
                audit_cycle_id="AUD-BATCH-B",
                audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
                audit_cache_key="CACHE-BATCH-B",
                event_id="EVT-STABLE-FIELD-B",
            )
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "recorded")

        ledger = self.service.store.load_ledger(self.namespace, project_id)
        matching = [
            row
            for row in ledger["issues"]
            if row.get("error_code") == "E-STATE-ATOM-001"
        ]
        self.assertEqual(len(matching), 2)
        self.assertEqual({row["field_class"] for row in matching}, {"起始状态"})
        self.assertEqual(len({row["pattern_key"] for row in matching}), 1)
        self.assertEqual(
            {row["history"][0]["field"] for row in matching}, {"起始状态"}
        )
        queried = self.service.query_project_issues(
            self.namespace,
            project_id=project_id,
            status="all",
            error_codes=["E-STATE-ATOM-001"],
            episode_ids=None,
            recurrence_states=None,
            minimum_occurrence_count=2,
            include_history=True,
            limit=50,
            cursor=0,
        )
        self.assertEqual(queried.data["total"], 2)
        self.assertTrue(
            all(
                row["pattern_summary"]["occurrence_count"] == 2
                for row in queried.data["issues"]
            )
        )

    def test_issue_query_minimum_counts_reobservations_of_one_occurrence(self) -> None:
        project_id = self._project("query-pattern-observation-count")
        draft = valid_storyboard()
        artifact = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"batch": "same-location"},
            file_name="same-occurrence.json",
            artifact_type="source_extraction",
            version="v1",
        )
        shot_id = str(draft["shots"][0]["镜头编号"])
        report = {
            "status": "fail_current",
            "issues": [
                {
                    "issue_id": "SEM-SAME-OCCURRENCE",
                    "error_code": "E-PROP-OWNER-001",
                    "severity": "fail",
                    "blocking": True,
                    "shot_ids": [shot_id],
                    "field": "道具持有人",
                    "location": "semantic.issue.SEM-SAME-OCCURRENCE",
                    "issue_type": "道具双持",
                    "root_cause": "道具状态机未继承",
                    "evidence": "同一时点存在两个持有人",
                    "repair_action": "重建道具唯一持有人状态",
                }
            ],
        }
        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (_hub, ledger):
            self.service._record_audit(
                namespace=self.namespace,
                project_id=project_id,
                artifact_record=artifact,
                storyboard=draft,
                report=report,
                ledger=ledger,
                audit_cycle_id="AUD-SAME-OCCURRENCE",
                audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
                audit_cache_key="CACHE-SAME-OCCURRENCE-R1",
                event_id="EVT-SAME-OCCURRENCE-R1",
            )
            self.service._record_audit(
                namespace=self.namespace,
                project_id=project_id,
                artifact_record=artifact,
                storyboard=draft,
                report=report,
                ledger=ledger,
                audit_cycle_id="AUD-SAME-OCCURRENCE",
                audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
                audit_cache_key="CACHE-SAME-OCCURRENCE-R2",
                event_id="EVT-SAME-OCCURRENCE-R2",
            )

        profile = service_module.storyboard_agent.active_prevention_profile(
            self.service.store.load_ledger(self.namespace, project_id)
        )
        pattern = next(
            row
            for row in profile["patterns"]
            if row["error_code"] == "E-PROP-OWNER-001"
        )
        self.assertEqual(pattern["occurrence_count"], 1)
        self.assertEqual(pattern["observation_count"], 2)
        queried = self.service.query_project_issues(
            self.namespace,
            project_id=project_id,
            status="all",
            error_codes=["E-PROP-OWNER-001"],
            episode_ids=None,
            recurrence_states=None,
            minimum_occurrence_count=2,
            include_history=True,
            limit=50,
            cursor=0,
        )
        self.assertEqual(queried.data["total"], 1)
        self.assertEqual(
            queried.data["issues"][0]["pattern_summary"]["observation_count"],
            2,
        )

    def test_unrelated_old_clean_audit_cannot_clear_quality_block(self) -> None:
        project_id = self._project("quality-block-lineage")
        blocked_order_id, blocked_session = self._begin(
            project_id, operation="storyboard_create", label="quality-block-source"
        )
        clean_order_id, clean_session = self._begin(
            project_id, operation="storyboard_create", label="older-clean-source"
        )
        draft = valid_storyboard()
        blocked_draft = copy.deepcopy(draft)
        blocked_draft["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        blocked_preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=blocked_order_id,
            storyboard=blocked_draft,
            scope_id="EP01",
            request_session_id=blocked_session,
        )
        clean_preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=clean_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=clean_session,
        )
        blocked_artifact = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=blocked_order_id,
            storyboard=blocked_draft,
            file_name="将触发C级质量阻断.xlsx",
            source_artifact_id=None,
            request_session_id=blocked_session,
            draft_preflight_id=str(blocked_preflight.trace_id),
        )
        clean_artifact = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=clean_order_id,
            storyboard=draft,
            file_name="无血缘旧clean.xlsx",
            source_artifact_id=None,
            request_session_id=clean_session,
            draft_preflight_id=str(clean_preflight.trace_id),
        )
        blocked_artifact_id = str(blocked_artifact.artifact_id)
        clean_artifact_id = str(clean_artifact.artifact_id)
        clean_audit_id, clean_audit_session = self._begin_audit(
            project_id, clean_artifact_id, label="old-clean"
        )
        blocked_audit_id, blocked_audit_session = self._begin_audit(
            project_id, blocked_artifact_id, label="quality-block"
        )

        old_clean = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=clean_artifact_id,
            audit_cycle_id=None,
            work_order_id=clean_audit_id,
            handoff_artifact_id=str(clean_artifact.data["handoff_artifact_id"]),
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                clean_artifact_id,
            ),
            request_session_id=clean_audit_session,
        )
        self.assertEqual(old_clean.status, "pass")

        failed = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=blocked_artifact_id,
            audit_cycle_id=None,
            work_order_id=blocked_audit_id,
            handoff_artifact_id=str(blocked_artifact.data["handoff_artifact_id"]),
            force_machine_recheck=False,
            semantic_audit=failing_semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                blocked_artifact_id,
                shot_id=str(draft["shots"][0]["镜头编号"]),
                field="光源来源",
                error_code="E-TEMPLATE-008",
                issue_id="shots[1].light_source_semantic_mismatch",
                evidence="独立审计确认当前右侧窗光与锁定的左侧自然光事实冲突。",
                repair_action="仅修正该镜光源来源并执行完整生产回归后返回原审计窗口。",
            ),
            request_session_id=blocked_audit_session,
        )
        self.assertEqual(failed.status, "fail_current")
        hub = self.service.store.load_hub(self.namespace, project_id)
        block = copy.deepcopy(
            hub["project_state"]["production_quality_block"]
        )
        self.assertTrue(block["active"])
        self.assertIn(block["grade"], {"B", "C"})
        self.assertEqual(block["artifact_id"], blocked_artifact_id)

        replayed_clean = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=clean_artifact_id,
            audit_cycle_id=None,
            work_order_id=clean_audit_id,
            handoff_artifact_id=str(clean_artifact.data["handoff_artifact_id"]),
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                clean_artifact_id,
            ),
            request_session_id=clean_audit_session,
        )
        self.assertEqual(replayed_clean.status, "pass")
        after = self.service.store.load_hub(self.namespace, project_id)[
            "project_state"
        ]["production_quality_block"]
        self.assertTrue(after["active"])
        self.assertEqual(after["artifact_id"], block["artifact_id"])
        self.assertEqual(after["audit_cycle_id"], block["audit_cycle_id"])
        self.assertNotIn("cleared_at", after)
        status = self.service.agent_status(self.namespace, project_id)
        self.assertTrue(status.data["state_valid"], status.data["state_errors"])
        # A completed replay is now a pure receipt read.  It must not move the
        # project's latest quality authority back to an older clean artifact.
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id)[
                "project_state"
            ]["latest_first_draft_quality"]["grade"],
            block["grade"],
        )
        self.assertIn(after["grade"], {"B", "C"})
        self.assertEqual(status.status, "action_required")
        self.assertEqual(status.data["execution_mode"], "root_cause_repair_only")

        repair_session = "v055-quality-block-repair"
        repair_task = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_repair",
            mode="audit_and_repair",
            authorization="explicit_execute",
            request_text="消费最新独立审计修复包并生成新证据绑定",
            targets=[str(draft["shots"][0]["镜头编号"])],
            authoritative_source="最新独立审计证书与不可变修复包",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["未授权镜头与字段不变"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["补丁修复版", "repair_to_audit"],
            window_key=repair_session,
            request_session_id=repair_session,
        )
        repaired = self.service.commit_storyboard_patch(
            self.namespace,
            project_id=project_id,
            work_order_id=str(repair_task.work_order_id),
            base_artifact_id=blocked_artifact_id,
            expected_canonical_hash=str(blocked_artifact.data["canonical_sha256"]),
            expected_raw_artifact_fingerprint=None,
            audit_certificate_id=str(failed.data["certificate"]["certificate_id"]),
            operations=[
                {
                    "op": "replace_field",
                    "shot_id": str(draft["shots"][0]["镜头编号"]),
                    "field": "光源来源",
                    "value": draft["shots"][0]["光源来源"],
                    "issue_ids": ["E-TEMPLATE-008"],
                    "reason": "按最新独立审计证据恢复锁定的左侧自然光来源",
                }
            ],
            impact_scope=[str(draft["shots"][0]["镜头编号"])],
            file_name="质量阻断_真实修复版.xlsx",
            request_session_id=repair_session,
        )
        repaired_artifact_id = str(repaired.artifact_id)
        repair_handoff_id = str(repaired.data["handoff_artifact_id"])
        same_cycle_clean = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=repaired_artifact_id,
            audit_cycle_id=None,
            work_order_id=blocked_audit_id,
            handoff_artifact_id=repair_handoff_id,
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                repaired_artifact_id,
            ),
            request_session_id=blocked_audit_session,
        )
        self.assertEqual(same_cycle_clean.status, "pass")
        cleared = self.service.store.load_hub(self.namespace, project_id)[
            "project_state"
        ]["production_quality_block"]
        self.assertFalse(cleared["active"])
        self.assertEqual(cleared["artifact_id"], blocked_artifact_id)
        self.assertEqual(
            cleared["cleared_by_audit_cycle_id"], block["audit_cycle_id"]
        )
        self.assertEqual(
            cleared["cleared_by_artifact_id"], repaired_artifact_id
        )
        self.assertEqual(
            cleared["cleared_by_certificate_id"],
            same_cycle_clean.data["certificate"]["certificate_id"],
        )
        self.assertTrue(
            self.service.agent_status(self.namespace, project_id).data[
                "state_valid"
            ]
        )

        repair_reaudit_semantic = semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            repaired_artifact_id,
        )
        repair_reaudit_semantic["layers"][0]["summary"] += (
            "｜修复产物使用新证据完成显式复审。"
        )
        repair_reaudit = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=repaired_artifact_id,
            audit_cycle_id=None,
            work_order_id=blocked_audit_id,
            handoff_artifact_id=repair_handoff_id,
            force_machine_recheck=True,
            semantic_audit=repair_reaudit_semantic,
            request_session_id=blocked_audit_session,
        )
        self.assertEqual(repair_reaudit.status, "pass")
        repair_successor_id = str(
            repair_reaudit.data["handoff"]["handoff_id"]
        )
        repair_successor = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=repair_successor_id,
        )
        self.assertTrue(
            self.service.store.project_record_is_signed(repair_successor)
        )
        self.service._validate_schema_record(
            self.service._project_schema,
            "handoff_manifest",
            repair_successor,
        )
        self.assertEqual(
            repair_successor["reaudit_kind"],
            "independent_audit_successor",
        )
        self.assertEqual(repair_successor["status"], "consumed")
        self.assertEqual(
            repair_successor["reaudit_root_handoff_id"], repair_handoff_id
        )
        self.assertEqual(
            repair_successor["reaudit_parent_handoff_id"], repair_handoff_id
        )
        self.assertEqual(repair_successor["reaudit_sequence"], 1)
        self.assertEqual(
            repair_successor["previous_audit_certificate_id"],
            same_cycle_clean.data["certificate"]["certificate_id"],
        )
        self.assertEqual(
            repair_successor["audit_certificate_id"],
            repair_reaudit.data["certificate"]["certificate_id"],
        )
        self.assertEqual(
            repair_reaudit.data["certificate"]["handoff_id"],
            repair_successor_id,
        )
        self.assertEqual(repair_successor["handoff_type"], "repair_to_audit")
        self.assertEqual(repair_successor["from_window_role"], "repair")
        self.assertEqual(
            repair_successor["to_window_role"], "independent_audit"
        )
        Draft202012Validator(self.service._project_schema).validate(
            self.service.store.load_hub(self.namespace, project_id)
        )

    def test_status_requires_valid_overlay_and_resolvable_authoritative_pointer(self) -> None:
        project_id = self._project("status-overlay-authority")
        work_order_id, production_session = self._begin(
            project_id, operation="storyboard_create", label="overlay-production"
        )
        draft = valid_storyboard()
        preflight = self._preflight_full_with_scene_coverage(
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            scope_id="EP01",
            request_session_id=production_session,
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=draft,
            file_name="独立审计状态权威.xlsx",
            source_artifact_id=None,
            request_session_id=production_session,
            draft_preflight_id=str(preflight.trace_id),
        )
        artifact_id = str(committed.artifact_id)
        audit_work_order_id, audit_session = self._begin_audit(
            project_id, artifact_id, label="overlay-status"
        )
        audited = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_work_order_id,
            handoff_artifact_id=str(committed.data["handoff_artifact_id"]),
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                artifact_id,
            ),
            request_session_id=audit_session,
        )
        self.assertEqual(audited.status, "pass")
        overlay_id = str(audited.data["audited_workbook"]["artifact_id"])

        valid = self.service.agent_status(self.namespace, project_id)
        self.assertTrue(valid.data["state_valid"], valid.data["state_errors"])
        latest = valid.data["latest_storyboard"]
        self.assertEqual(latest["authoritative_artifact_id"], overlay_id)
        self.assertEqual(latest["artifact_id"], artifact_id)
        self.assertEqual(latest["effective_audit_status"], "PASS")
        self.assertEqual(latest["audit_evidence_state"], "verified_independent_certificate")
        self.assertEqual(
            latest["independent_audit_certificate_id"],
            audited.data["certificate"]["certificate_id"],
        )

        overlay_record, overlay_path = self.service.store.get_artifact(
            self.namespace, project_id, overlay_id
        )
        overlay_meta_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "artifacts"
            / f"{overlay_id}.meta.json"
        )
        missing_source = copy.deepcopy(overlay_record)
        missing_source.pop("source_artifact_id", None)
        self.service.store.atomic_write_json(overlay_meta_path, missing_source)
        missing_source_status = self.service.agent_status(
            self.namespace, project_id
        )
        self.assertEqual(missing_source_status.status, "migration_required")
        self.assertFalse(missing_source_status.data["state_valid"])
        self.assertTrue(
            any(
                "OVERLAY" in error.upper() and "SOURCE" in error.upper()
                for error in missing_source_status.data["state_errors"]
            )
        )

        nonexistent_source = copy.deepcopy(overlay_record)
        nonexistent_source["source_artifact_id"] = "ART-missing-overlay-source"
        self.service.store.atomic_write_json(overlay_meta_path, nonexistent_source)
        nonexistent_source_status = self.service.agent_status(
            self.namespace, project_id
        )
        self.assertEqual(nonexistent_source_status.status, "migration_required")
        self.assertFalse(nonexistent_source_status.data["state_valid"])
        self.assertTrue(
            any(
                "OVERLAY" in error.upper() and "SOURCE" in error.upper()
                for error in nonexistent_source_status.data["state_errors"]
            )
        )
        self.service.store.atomic_write_json(overlay_meta_path, overlay_record)
        self.assertEqual(
            self.service.agent_status(self.namespace, project_id)
            .data["latest_storyboard"]["effective_audit_status"],
            "PASS",
        )

        overlay_bytes = overlay_path.read_bytes()
        overlay_path.write_bytes(overlay_bytes + b"\nTAMPERED")
        invalid_overlay = self.service.agent_status(self.namespace, project_id)
        invalid_latest = invalid_overlay.data["latest_storyboard"]
        self.assertEqual(
            invalid_latest["effective_audit_status"], "AUDIT_EVIDENCE_INVALID"
        )
        self.assertEqual(
            invalid_latest["audit_evidence_state"],
            "invalid_certificate_or_binding",
        )
        self.assertIsNone(invalid_latest["independent_audit_certificate_id"])
        overlay_path.write_bytes(overlay_bytes)
        self.assertEqual(
            self.service.agent_status(self.namespace, project_id)
            .data["latest_storyboard"]["effective_audit_status"],
            "PASS",
        )

        # The artifact metadata is a mutable index, not audit authority.  Even
        # if a tamperer edits the visible audit page and updates that index's
        # public raw fingerprint to match, status must reparse the workbook and
        # reject the page because its claims are not bound to the signed
        # independent-audit certificate.
        from openpyxl import load_workbook

        workbook = load_workbook(overlay_path)
        workbook["00_范围与审计"]["B4"] = "FAIL_CURRENT"
        workbook.save(overlay_path)
        workbook.close()
        metadata_synchronized_tamper = copy.deepcopy(overlay_record)
        tampered_overlay_bytes = overlay_path.read_bytes()
        metadata_synchronized_tamper["fingerprint"] = (
            service_module.sha256_bytes(tampered_overlay_bytes)
        )
        metadata_synchronized_tamper["size_bytes"] = len(tampered_overlay_bytes)
        self.service.store.atomic_write_json(
            overlay_meta_path,
            metadata_synchronized_tamper,
        )
        synchronized_tamper_status = self.service.agent_status(
            self.namespace,
            project_id,
        )
        self.assertEqual(synchronized_tamper_status.status, "migration_required")
        self.assertFalse(synchronized_tamper_status.data["state_valid"])
        synchronized_tamper_latest = synchronized_tamper_status.data[
            "latest_storyboard"
        ]
        self.assertEqual(
            synchronized_tamper_latest["effective_audit_status"],
            "AUDIT_EVIDENCE_INVALID",
        )
        self.assertEqual(
            synchronized_tamper_latest["audit_evidence_state"],
            "invalid_certificate_or_binding",
        )
        overlay_path.write_bytes(overlay_bytes)
        self.service.store.atomic_write_json(overlay_meta_path, overlay_record)
        self.assertEqual(
            self.service.agent_status(self.namespace, project_id)
            .data["latest_storyboard"]["effective_audit_status"],
            "PASS",
        )

        with self.service.store.mutate_project(
            self.namespace, project_id
        ) as (hub, _ledger):
            hub["project_state"]["latest_artifacts"]["storyboard"][
                "deliverable_id"
            ] = "ART-missing-authoritative-overlay"
        invalid_pointer = self.service.agent_status(self.namespace, project_id)
        self.assertEqual(invalid_pointer.status, "migration_required")
        self.assertFalse(invalid_pointer.data["state_valid"])
        self.assertIsNone(invalid_pointer.data["latest_storyboard"])
        self.assertTrue(
            any(
                "AUTHORITATIVE_ARTIFACT_MISSING" in error
                for error in invalid_pointer.data["state_errors"]
            )
        )

    def test_external_storyboard_audit_overlay_resolves_its_json_source(self) -> None:
        project_id = self._project("external-overlay-source-resolution")
        external = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            valid_storyboard(),
            file_name="外部分镜规范.json",
            artifact_type="storyboard_json",
            version="external-v1",
        )
        artifact_id = str(external["artifact_id"])
        audit_work_order_id, audit_session = self._begin_audit(
            project_id,
            artifact_id,
            label="external-json-overlay",
        )
        audited = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_work_order_id,
            handoff_artifact_id=None,
            force_machine_recheck=False,
            semantic_audit=semantic_audit_for_artifact(
                self.service,
                self.settings,
                project_id,
                artifact_id,
            ),
            request_session_id=audit_session,
        )
        self.assertEqual(audited.status, "pass")
        status = self.service.agent_status(self.namespace, project_id)
        self.assertTrue(status.data["state_valid"], status.data["state_errors"])
        latest = status.data["latest_storyboard"]
        self.assertEqual(latest["artifact_id"], artifact_id)
        self.assertEqual(latest["effective_audit_status"], "PASS")
        self.assertEqual(
            latest["authoritative_artifact_id"],
            audited.data["audited_workbook"]["artifact_id"],
        )

        intake_handoff_id = str(audited.data["handoff"]["handoff_id"])
        intake_reaudit_semantic = semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            artifact_id,
        )
        intake_reaudit_semantic["layers"][0]["summary"] += (
            "｜外部 intake 使用新证据完成显式复审。"
        )
        intake_reaudit = self.service.audit_storyboard(
            self.namespace,
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=None,
            work_order_id=audit_work_order_id,
            handoff_artifact_id=intake_handoff_id,
            force_machine_recheck=True,
            semantic_audit=intake_reaudit_semantic,
            request_session_id=audit_session,
        )
        self.assertEqual(intake_reaudit.status, "pass")
        intake_successor_id = str(
            intake_reaudit.data["handoff"]["handoff_id"]
        )
        intake_successor = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=intake_successor_id,
        )
        self.assertTrue(
            self.service.store.project_record_is_signed(intake_successor)
        )
        self.service._validate_schema_record(
            self.service._project_schema,
            "handoff_manifest",
            intake_successor,
        )
        self.assertEqual(
            intake_successor["reaudit_kind"],
            "independent_audit_successor",
        )
        self.assertEqual(intake_successor["status"], "consumed")
        self.assertEqual(
            intake_successor["reaudit_root_handoff_id"], intake_handoff_id
        )
        self.assertEqual(
            intake_successor["reaudit_parent_handoff_id"], intake_handoff_id
        )
        self.assertEqual(intake_successor["reaudit_sequence"], 1)
        self.assertEqual(
            intake_successor["previous_audit_certificate_id"],
            audited.data["certificate"]["certificate_id"],
        )
        self.assertEqual(
            intake_successor["audit_certificate_id"],
            intake_reaudit.data["certificate"]["certificate_id"],
        )
        self.assertEqual(
            intake_reaudit.data["certificate"]["handoff_id"],
            intake_successor_id,
        )
        self.assertEqual(intake_successor["handoff_type"], "audit_intake")
        self.assertEqual(
            intake_successor["from_window_role"], "external_input"
        )
        self.assertEqual(
            intake_successor["to_window_role"], "independent_audit"
        )
        Draft202012Validator(self.service._project_schema).validate(
            self.service.store.load_hub(self.namespace, project_id)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
