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
from semantic_audit_helpers import inject_semantic_audit


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
    ) -> str:
        task = await self.call(
            "begin_project_task",
            {
                "project_id": self.project_id,
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
        broken["shots"][0]["光源来源"] = ""
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
        self.assertEqual(self.committed["status"], "fail_current")
        self.audited = await self.call(
            "audit_storyboard",
            {
                "project_id": self.project_id,
                "artifact_id": self.committed["artifact_id"],
            },
        )
        self.assertEqual(self.audited["status"], "fail_current")
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
        self.assertEqual(repaired["status"], "pass")
        self.assertTrue(repaired["data"]["structural_repair"])
        self.assertEqual(repaired["data"]["repair_transaction_state"], "committed")
        self.assertEqual(repaired["data"]["repair_package"]["status"], "consumed")
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
        self.assertEqual(first["status"], "pass")
        replay = await self.rejection("commit_storyboard", self.full_arguments())
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
        handoff_id = failed["data"]["handoff_artifact_id"]
        handoff = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        self.assertEqual(handoff["status"], "rejected")

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
