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

from test_efficiency import valid_storyboard
from test_v040_architecture import three_shot_storyboard
from semantic_audit_helpers import (
    failing_semantic_audit_for_artifact,
    inject_semantic_audit,
)


class CandidatePublicationFaultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-publication-test-")
        os.environ["SHORTDRAMA_DATA_DIR"] = self.temp.name
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8765"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        self.settings = Settings.from_env()
        self.server, self.service = create_mcp(self.settings)
        self.namespace = request_namespace(self.settings)

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

    async def create_project(self, name: str) -> str:
        created = await self.call("create_project", {"project_name": name})
        project_id = str(created["project_id"])
        await self.call(
            "get_agent_status", {"project_id": project_id, "full_self_test": True}
        )
        return project_id

    async def begin(
        self,
        project_id: str,
        *,
        operation: str,
        targets: list[str],
        mode: str = "execute",
        window_key: str,
    ) -> str:
        result = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": operation,
                "mode": mode,
                "authorization": "explicit_execute",
                "request_text": "successor候选发布原子性故障测试",
                "targets": targets,
                "authoritative_source": "锁定测试源文",
                "latest_version": "current",
                "target_boundary": ",".join(targets),
                "must_keep": ["剧情、对白、认知、声画与光影"],
                "must_remove": [],
                "protected_scope": [],
                "expected_outputs": ["正式分镜", "正式生产母版"],
                "window_key": window_key,
            },
        )
        return str(result["work_order_id"])

    def pending_candidates(
        self, project_id: str, *, kind: str
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.service.store.list_artifacts(
                self.namespace, project_id, include_uncommitted=True
            )
            if row.get("metadata", {}).get("successor_kind") == kind
        ]

    def assert_pending_is_sealed(
        self,
        project_id: str,
        *,
        kind: str,
        transaction_id: str,
    ) -> None:
        candidates = self.pending_candidates(project_id, kind=kind)
        self.assertEqual(len(candidates), 2)
        public_ids = {
            row["artifact_id"]
            for row in self.service.store.list_artifacts(
                self.namespace, project_id
            )
        }
        for candidate in candidates:
            self.assertEqual(
                candidate["metadata"]["publication_state"], "pending"
            )
            self.assertNotIn(candidate["artifact_id"], public_ids)
            with self.assertRaisesRegex(ValueError, "artifact_not_committed"):
                self.service.store.get_artifact(
                    self.namespace, project_id, candidate["artifact_id"]
                )
            url = self.service.store.signed_download_url(
                self.namespace,
                project_id,
                candidate["artifact_id"],
                candidate["file_name"],
            )
            token = url.split("/files/", 1)[1].split("/", 1)[0]
            with self.assertRaisesRegex(ValueError, "artifact_not_committed"):
                self.service.store.resolve_download_token(token)

        handoffs = [
            row
            for row in self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            )
            if row.get("successor_transaction_id") == transaction_id
        ]
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["publication_state"], "pending")
        with self.assertRaisesRegex(ValueError, "uncommitted successor"):
            self.service.store.claim_handoff(
                self.namespace,
                project_id,
                str(handoffs[0]["handoff_id"]),
                consumer_work_order_id="WO-fault-audit",
                expected_to_role="independent_audit",
                expected_types={"production_to_audit", "repair_to_audit"},
            )

    async def assert_aborted_is_sealed(
        self,
        project_id: str,
        *,
        kind: str,
        base_artifact_id: str,
    ) -> None:
        candidates = self.pending_candidates(project_id, kind=kind)
        self.assertEqual(len(candidates), 2)
        storyboard_candidate = next(
            row for row in candidates if row.get("artifact_type") == "storyboard"
        )
        for candidate in candidates:
            self.assertEqual(
                candidate["metadata"]["publication_state"], "aborted"
            )
            with self.assertRaisesRegex(ValueError, "artifact_not_committed"):
                self.service.store.get_artifact(
                    self.namespace, project_id, candidate["artifact_id"]
                )
        public_ids = {
            row["artifact_id"]
            for row in self.service.store.list_artifacts(
                self.namespace, project_id
            )
        }
        self.assertNotIn(storyboard_candidate["artifact_id"], public_ids)
        audit_error = await self.rejection(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": storyboard_candidate["artifact_id"],
            },
        )
        self.assertIn("artifact_not_committed", audit_error)

        base, _path = self.service.store.get_artifact(
            self.namespace, project_id, base_artifact_id
        )
        self.assertFalse(base.get("metadata", {}).get("successor_artifact_id"))
        self.assertEqual(
            base["metadata"]["successor_transaction"]["state"], "aborted"
        )
        transaction_id = candidates[0]["metadata"]["successor_transaction_id"]
        handoff = next(
            row
            for row in self.service.store.list_project_records(
                self.namespace, project_id, kind="handoffs"
            )
            if row.get("successor_transaction_id") == transaction_id
        )
        self.assertEqual(handoff["status"], "rejected")
        self.assertEqual(handoff["publication_state"], "aborted")
        hub = self.service.store.load_hub(self.namespace, project_id)
        latest = hub["project_state"]["latest_artifacts"].get("storyboard")
        self.assertFalse(
            isinstance(latest, dict)
            and latest.get("deliverable_id") == storyboard_candidate["artifact_id"]
        )

    async def test_delta_final_cas_failure_never_publishes_candidate(self) -> None:
        project_id = await self.create_project("delta候选半发布故障")
        create_order = await self.begin(
            project_id,
            operation="storyboard_create",
            targets=["EP01-EP03"],
            window_key="delta-create",
        )
        storyboard = three_shot_storyboard()
        base = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": create_order,
                "storyboard": storyboard,
                "file_name": "delta基线.xlsx",
            },
        )
        audited = await self.call(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": base["artifact_id"],
                "handoff_artifact_id": base["data"]["handoff"]["handoff_id"],
            },
        )
        self.assertEqual(audited["status"], "pass")
        change_order = await self.begin(
            project_id,
            operation="storyboard_change",
            targets=["EP02"],
            window_key="delta-change",
        )
        read = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": base["artifact_id"],
                "section": "canonical_storyboard",
            },
        )
        identities = read["data"]["shot_identity_manifest"]["shot_uids"]
        replacement_shot = copy.deepcopy(storyboard["shots"][1])
        replacement_shot["画面内容"] = (
            "【0.0—0.8秒】女主在原位停步；【0.8—3.3秒】她确认桌上信件，"
            "因果、认知与表演连续，本镜无对白；【3.3—4.0秒】她保持视线与稳定呼吸。"
        )
        performance = copy.deepcopy(
            storyboard["prevention_evidence"]["performance_chains"][1]
        )
        performance["body_or_breath"] = "停步确认后呼吸轻收，再恢复稳定"
        arguments = {
            "project_id": project_id,
            "work_order_id": change_order,
            "base_artifact_id": base["artifact_id"],
            "expected_base_canonical_sha256": base["data"]["canonical_sha256"],
            "expected_base_raw_fingerprint": base["data"]["artifact"]["fingerprint"],
            "scope_target": "EP02",
            "affected_shot_uids": [identities[1]["shot_uid"]],
            "left_boundary_shot_uid": identities[0]["shot_uid"],
            "right_boundary_shot_uid": identities[2]["shot_uid"],
            "replacement_shots": [replacement_shot],
            "evidence_updates": {"performance_chains": [performance]},
            "file_name": "delta故障候选.xlsx",
        }

        def fail_cas(*_args: Any, **kwargs: Any) -> None:
            self.assert_pending_is_sealed(
                project_id,
                kind="delta",
                transaction_id=str(kwargs["transaction_id"]),
            )
            raise RuntimeError("simulated delta final CAS failure")

        with patch.object(
            self.service.store, "commit_storyboard_successor", side_effect=fail_cas
        ):
            error = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("simulated delta final CAS failure", error)
        await self.assert_aborted_is_sealed(
            project_id, kind="delta", base_artifact_id=base["artifact_id"]
        )

    async def test_patch_final_cas_failure_restores_package_and_seals_candidate(
        self,
    ) -> None:
        project_id = await self.create_project("patch候选半发布故障")
        create_order = await self.begin(
            project_id,
            operation="storyboard_create",
            targets=["第一集"],
            window_key="patch-create",
        )
        broken = valid_storyboard()
        broken["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        base = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": create_order,
                "storyboard": broken,
                "file_name": "patch待修基线.xlsx",
            },
        )
        self.assertEqual(base["status"], "production_preflight_pass")
        semantic_failure = failing_semantic_audit_for_artifact(
            self.service,
            self.settings,
            project_id,
            str(base["artifact_id"]),
            shot_id="S001",
            field="光源来源",
            error_code="E-TEMPLATE-008",
            issue_id="shots[1].light_source_semantic_mismatch",
            evidence="独立审计确认场景锁要求左侧窗光，当前右侧来源与其不一致。",
            repair_action="仅将光源来源修正为左侧木格窗透入的日光。",
        )
        audited = await self.call(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": base["artifact_id"],
                "semantic_audit": semantic_failure,
            },
        )
        repair_order = await self.begin(
            project_id,
            operation="storyboard_repair",
            targets=["第一集"],
            mode="audit_and_repair",
            window_key="patch-repair",
        )
        package_id = audited["data"]["repair_package"]["repair_package_id"]
        arguments = {
            "project_id": project_id,
            "work_order_id": repair_order,
            "base_artifact_id": base["artifact_id"],
            "expected_canonical_hash": base["data"]["canonical_sha256"],
            "audit_certificate_id": audited["data"]["certificate"]["certificate_id"],
            "operations": [
                {
                    "op": "replace_field",
                    "shot_id": "EP01-001",
                    "field": "光源来源",
                    "value": "左侧木格窗透入的日光",
                    "issue_ids": ["E-TEMPLATE-008"],
                    "reason": "修复独立审计确认的光源语义错位",
                }
            ],
            "impact_scope": ["EP01-001"],
            "file_name": "patch故障候选.xlsx",
        }

        def fail_cas(*_args: Any, **kwargs: Any) -> None:
            self.assert_pending_is_sealed(
                project_id,
                kind="patch",
                transaction_id=str(kwargs["transaction_id"]),
            )
            package = self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="repair_packages",
                record_id=package_id,
            )
            self.assertEqual(package["status"], "consumed")
            raise RuntimeError("simulated patch final CAS failure")

        with patch.object(
            self.service.store, "commit_storyboard_successor", side_effect=fail_cas
        ):
            error = await self.rejection("commit_storyboard_patch", arguments)
        self.assertIn("simulated patch final CAS failure", error)
        package = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=package_id,
        )
        self.assertEqual(package["status"], "issued")
        self.assertFalse(package.get("successor_artifact_id"))
        await self.assert_aborted_is_sealed(
            project_id, kind="patch", base_artifact_id=base["artifact_id"]
        )


if __name__ == "__main__":
    unittest.main()
