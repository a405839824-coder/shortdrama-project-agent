from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
import shortdrama_mcp.service as service_module

from test_efficiency import valid_storyboard


class V056HandoffRecoveryTests(unittest.IsolatedAsyncioTestCase):
    namespace = "local-single-user"

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v056-recovery-")
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
        self.project_id = str(
            self.service.create_project(self.namespace, "handoff recovery").project_id
        )
        self.runtime_certificate = self.service._persist_runtime_certificate(
            self.namespace,
            self.project_id,
            {
                "status": "pass",
                "version": self.service.bootstrap.get("version"),
                "workflow_revision": self.service.bootstrap.get(
                    "workflow_revision"
                ),
            },
        )
        self.old_handoff_id = self._create_stale_handoff()
        self.gap_id = "GAP-test-handoff-recovery"
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            service_module.project_agent.record_capability_gap(
                hub,
                {
                    "gap_id": self.gap_id,
                    "timestamp": service_module.utc_stamp(),
                    "request": "recover stale prepared storyboard handoff",
                    "missing_dependency": "controlled handoff recovery tool",
                    "risk": "stale fingerprint blocks independent audit",
                    "safe_fallback": "keep old handoff unconsumed",
                    "proposed_implementation": "signed journal plus CAS supersession",
                    "required_tests": ["atomic recovery", "audit separation"],
                },
            )
        self.session_id = "v056-production-recovery"
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="rule_improvement",
            mode="execute",
            authorization="explicit_execute",
            request_text="authorized controlled handoff recovery",
            targets=[self.gap_id, self.old_handoff_id],
            authoritative_source="signed project records",
            latest_version="0.5.6",
            target_boundary="one exact handoff",
            must_keep=["history", "independent audit separation"],
            must_remove=[],
            protected_scope=["storyboard bytes", "canonical", "audit evidence"],
            expected_outputs=["superseded old handoff", "current successor handoff"],
            window_key=self.session_id,
            request_session_id=self.session_id,
        )
        self.recovery_work_order_id = str(response.work_order_id)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _create_stale_handoff(self) -> str:
        production_session = "v056-original-production"
        response = self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="fixture production",
            targets=["EP01"],
            authoritative_source="fixture source",
            latest_version="v1",
            target_boundary="EP01",
            must_keep=["story"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["storyboard"],
            window_key=production_session,
            request_session_id=production_session,
        )
        production_work_order_id = str(response.work_order_id)
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        production_work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == production_work_order_id
        )
        storyboard = valid_storyboard()
        canonical_hash = service_module.canonical_storyboard_sha256(storyboard)
        source_fingerprint = self.service._source_fingerprint(
            self.namespace, self.project_id, storyboard, None
        )
        boundary_hash = self.service._boundary_hash(storyboard)
        canonical_record = self.service.store.save_artifact_json(
            self.namespace,
            self.project_id,
            storyboard,
            file_name="fixture.json",
            artifact_type="storyboard_json",
            version="v1",
            work_order_id=production_work_order_id,
            metadata={"canonical_sha256": canonical_hash},
        )
        artifact_record = self.service.store.save_artifact_bytes(
            self.namespace,
            self.project_id,
            b"immutable-xlsx-fixture",
            file_name="fixture.xlsx",
            artifact_type="storyboard",
            version="v1",
            work_order_id=production_work_order_id,
            metadata={
                "canonical_json_artifact_id": canonical_record["artifact_id"],
                "canonical_json_fingerprint": canonical_record["fingerprint"],
                "canonical_sha256": canonical_hash,
                "source_fingerprint": source_fingerprint,
                "boundary_hash": boundary_hash,
                "production_preflight_status": service_module.PRODUCTION_PREFLIGHT_PASS,
                "production_preflight_issue_count": 0,
                "independent_audit_status": "PENDING_AUDIT",
            },
        )
        report = service_module.validate_storyboard.validate_project(
            storyboard, mode="audit"
        )
        self.assertEqual(report.get("issues"), [])
        cache_components = self.service._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=service_module.AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            artifact_fingerprint=artifact_record["fingerprint"],
            storyboard=storyboard,
            scope={"targets": ["EP01"], "protected_scope": []},
            runtime_test_certificate_id=self.runtime_certificate["certificate_id"],
        )
        certificate = self.service._persist_audit_certificate(
            self.namespace,
            self.project_id,
            artifact_id=artifact_record["artifact_id"],
            canonical_artifact_id=canonical_record["artifact_id"],
            cache_components=cache_components,
            report=report,
            work_order_id=production_work_order_id,
        )
        handoff = self.service._persist_production_handoff(
            self.namespace,
            self.project_id,
            work_order_id=production_work_order_id,
            storyboard_artifact_id=artifact_record["artifact_id"],
            canonical_artifact_id=canonical_record["artifact_id"],
            certificate=certificate,
            storyboard=storyboard,
            artifact_record=artifact_record,
            work_order=production_work_order,
            stage_trace_id="TRC-fixture-production",
        )
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (live_hub, _ledger):
            live_hub.setdefault("handoffs", []).append(
                self.service._handoff_record_projection(handoff)
            )
        with self.service.store.mutate_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=handoff["handoff_id"],
        ) as stale:
            stale["fingerprints"]["runtime"] = "0" * 64
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (live_hub, _ledger):
            row = next(
                item
                for item in live_hub["handoffs"]
                if item["handoff_id"] == handoff["handoff_id"]
            )
            row["fingerprints"]["runtime"] = "0" * 64
        return str(handoff["handoff_id"])

    def _plan(self) -> Any:
        return self.service.recover_storyboard_handoff(
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

    def _execute(self, plan_sha256: str) -> Any:
        return self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=[self.old_handoff_id],
            action="execute",
            expected_plan_sha256=plan_sha256,
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=self.session_id,
        )

    async def test_tool_contract_and_plan_are_controlled_and_read_only(self) -> None:
        tools = await self.server.list_tools()
        by_name = {
            tool.name: tool.model_dump(mode="json", by_alias=True)
            for tool in tools
        }
        recovery = by_name["recover_storyboard_handoff"]
        self.assertFalse(recovery["annotations"]["readOnlyHint"])
        self.assertTrue(recovery["annotations"]["destructiveHint"])
        self.assertTrue(recovery["annotations"]["idempotentHint"])
        self.assertFalse(recovery["annotations"]["openWorldHint"])

        hub_before = self.service.store.project_state_sha256(
            self.service.store.load_hub(self.namespace, self.project_id)
        )
        records_before = len(
            self.service.store.list_project_records(
                self.namespace, self.project_id, kind="handoffs"
            )
        )
        plan = self._plan()
        self.assertEqual(plan.status, "handoff_recovery_plan_ready")
        item = plan.data["recovery_plan"]["items"][0]
        self.assertIn("runtime", item["stale_components"])
        self.assertEqual(item["cache_status"], "miss")
        self.assertEqual(
            self.service.store.project_state_sha256(
                self.service.store.load_hub(self.namespace, self.project_id)
            ),
            hub_before,
        )
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, self.project_id, kind="handoffs"
                )
            ),
            records_before,
        )

    async def test_execute_supersedes_preserves_history_and_replays_idempotently(self) -> None:
        plan = self._plan().data["recovery_plan"]
        result = self._execute(plan["plan_sha256"])
        self.assertEqual(result.status, "handoffs_recovered")
        mapping = result.data["mappings"][0]
        old = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        new = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=mapping["new_handoff_id"],
        )
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by_handoff_id"], new["handoff_id"])
        self.assertEqual(new["status"], "prepared")
        self.assertEqual(new["supersedes_handoff_id"], old["handoff_id"])
        self.assertEqual(new["fingerprints"], self.service._fingerprint_bundle(
            *self.service._storyboard_from_artifact(
                self.namespace, self.project_id, new["storyboard_artifact_id"]
            ),
            canonical_hash=new["canonical_sha256"],
            source_fingerprint=new["source_fingerprint"],
            boundary_hash=new["boundary_hash"],
        ))
        certificates = self.service.store.list_project_records(
            self.namespace, self.project_id, kind="certificates"
        )
        self.assertFalse(
            any(row.get("certificate_type") == "independent_audit" for row in certificates)
        )
        with self.assertRaisesRegex(ValueError, "handoff_superseded"):
            self.service.store.claim_handoff(
                self.namespace,
                self.project_id,
                self.old_handoff_id,
                consumer_work_order_id="WO-auditor",
                expected_to_role="independent_audit",
                expected_types={"production_to_audit"},
            )
        replay = self._execute(plan["plan_sha256"])
        self.assertEqual(
            replay.data["mappings"][0]["new_handoff_id"],
            result.data["mappings"][0]["new_handoff_id"],
        )
        self.assertTrue(replay.data["mappings"][0]["idempotent_replay"])
        self.assertEqual(
            len(
                self.service.store.list_project_records(
                    self.namespace, self.project_id, kind="handoffs"
                )
            ),
            2,
        )

    async def test_execute_requires_exact_confirmation_before_mutation(self) -> None:
        plan = self._plan().data["recovery_plan"]
        with self.assertRaisesRegex(
            ValueError, "handoff_recovery_confirmation_required"
        ):
            self.service.recover_storyboard_handoff(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.recovery_work_order_id,
                gap_id=self.gap_id,
                handoff_ids=[self.old_handoff_id],
                action="execute",
                expected_plan_sha256=plan["plan_sha256"],
                confirmation="wrong",
                request_session_id=self.session_id,
            )
        old = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        self.assertEqual(old["status"], "prepared")

    async def test_batch_recovers_each_handoff_with_one_shared_trace(self) -> None:
        original = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        second_handoff_id = service_module._new_id("HND")
        clone = self.service._handoff_record_projection(original)
        clone["handoff_id"] = second_handoff_id
        clone["created_at"] = service_module.utc_stamp()
        persisted = self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=second_handoff_id,
            value=clone,
        )
        with self.service.store.mutate_project(
            self.namespace, self.project_id
        ) as (hub, _ledger):
            hub["handoffs"].append(
                self.service._handoff_record_projection(persisted)
            )
            work_order = next(
                row
                for row in hub["work_orders"]
                if row["work_order_id"] == self.recovery_work_order_id
            )
            work_order["targets"].append(second_handoff_id)
        handoff_ids = [self.old_handoff_id, second_handoff_id]
        plan_response = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="plan",
            expected_plan_sha256=None,
            confirmation=None,
            request_session_id=self.session_id,
        )
        plan = plan_response.data["recovery_plan"]
        result = self.service.recover_storyboard_handoff(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.recovery_work_order_id,
            gap_id=self.gap_id,
            handoff_ids=handoff_ids,
            action="execute",
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=service_module.HANDOFF_RECOVERY_CONFIRMATION,
            request_session_id=self.session_id,
        )
        self.assertEqual(len(result.data["mappings"]), 2)
        self.assertEqual(result.trace_id, plan["trace_id"])
        self.assertEqual(
            {row["old_handoff_id"] for row in result.data["mappings"]},
            set(handoff_ids),
        )
        for mapping in result.data["mappings"]:
            new = self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=mapping["new_handoff_id"],
            )
            self.assertEqual(new["recovery_trace_id"], result.trace_id)
            self.assertEqual(new["status"], "prepared")

    async def test_interrupted_commit_fails_closed_and_retry_recovers(self) -> None:
        plan = self._plan().data["recovery_plan"]
        new_handoff_id = plan["items"][0]["new_handoff_id"]
        original = self.service.store.atomic_write_json
        tripped = False

        def fail_once(path: Path, value: dict[str, Any]) -> None:
            nonlocal tripped
            if (
                not tripped
                and path.parent.name == "handoffs"
                and path.name == f"{new_handoff_id}.json"
            ):
                tripped = True
                raise OSError("simulated handoff successor write interruption")
            original(path, value)

        with patch.object(self.service.store, "atomic_write_json", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "simulated handoff successor"):
                self._execute(plan["plan_sha256"])
        old = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="handoffs",
            record_id=self.old_handoff_id,
        )
        self.assertEqual(old["status"], "superseded")
        with self.assertRaisesRegex(ValueError, "unknown handoffs"):
            self.service.store.get_project_record(
                self.namespace,
                self.project_id,
                kind="handoffs",
                record_id=new_handoff_id,
            )
        recovered = self._execute(plan["plan_sha256"])
        self.assertEqual(
            recovered.data["mappings"][0]["new_handoff_id"], new_handoff_id
        )
        self.assertTrue(recovered.data["mappings"][0]["idempotent_replay"])


if __name__ == "__main__":
    unittest.main()
