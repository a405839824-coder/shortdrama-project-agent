from __future__ import annotations

import copy
import os
import tempfile
import unittest
from typing import Any

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.canonical import stable_json_hash
from shortdrama_mcp.config import Settings


from semantic_audit_helpers import inject_semantic_audit
from test_efficiency import valid_storyboard


def three_shot_storyboard() -> dict[str, Any]:
    value = valid_storyboard()
    template_shot = value["shots"][0]
    shots: list[dict[str, Any]] = []
    for index in range(1, 4):
        shot = copy.deepcopy(template_shot)
        episode_id = f"EP{index:02d}"
        shot_id = f"{episode_id}-001"
        shot.update(
            {
                "镜头编号": shot_id,
                "累计时长": 4,
                "场景连续性ID": f"{episode_id}-SC01",
                "画面内容": (
                    f"【0.0—0.8秒】第{index}个连续剧情动作开始，女主停在既定站位；"
                    f"【0.8—3.3秒】女主转动视线并看向桌面信件，完成本镜表演，本镜无对白；"
                    f"【3.3—4.0秒】她保持视线、站位与稳定呼吸，动作收束。"
                ),
                "覆盖原文": f"第{index}个剧情动作。",
                "原文支撑句": f"第{index}个剧情动作。",
                "episode_id": episode_id,
            }
        )
        shots.append(shot)
    value["shots"] = shots
    value["total_duration"] = 12
    evidence = value["prevention_evidence"]
    evidence["source_ledger"] = [
        {
            "source_id": f"SRC-{index:03d}",
            "source_type": "关键事件",
            "source_text": f"第{index}个剧情动作。",
            "speaker": "叙述",
            "treatment": "保留",
            "target_shots": [f"EP{index:02d}-001"],
            "authorization": "不适用：未删除",
        }
        for index in range(1, 4)
    ]
    evidence["scene_state_ledger"] = [
        {
            **copy.deepcopy(evidence["scene_state_ledger"][0]),
            "scene_id": f"EP{index:02d}-SC01",
            "shot_id": f"EP{index:02d}-001",
        }
        for index in range(1, 4)
    ]
    evidence["shot_plan"] = [
        {
            **copy.deepcopy(evidence["shot_plan"][0]),
            "shot_id": f"EP{index:02d}-001",
            "main_action_stage": f"第{index}个连续剧情动作，女主看向桌面信件",
        }
        for index in range(1, 4)
    ]
    evidence["performance_chains"] = [
        {
            **copy.deepcopy(evidence["performance_chains"][0]),
            "shot_id": f"EP{index:02d}-001",
        }
        for index in range(1, 4)
    ]
    return value


class CompactMasterArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-compact-master-test-")
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
        await self.call("get_agent_status", {"project_id": project_id, "full_self_test": True})
        return project_id

    async def begin(
        self,
        project_id: str,
        *,
        operation: str,
        targets: list[str],
        protected_scope: list[str] | None = None,
    ) -> str:
        result = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": operation,
                "mode": "execute",
                "authorization": "explicit_execute",
                "request_text": "0.5.0紧凑母版三窗口与增量等质测试",
                "targets": targets,
                "authoritative_source": "锁定测试源文",
                "latest_version": "current",
                "target_boundary": ",".join(targets),
                "must_keep": ["未影响剧情、对白、认知、声画与光影"],
                "must_remove": [],
                "protected_scope": protected_scope or [],
                "expected_outputs": ["正式分镜", "正式生产母版", "生产审计交接"],
                "window_key": f"production-{operation}",
            },
        )
        return str(result["work_order_id"])

    async def committed_base(self, name: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        project_id = await self.create_project(name)
        work_order_id = await self.begin(
            project_id, operation="storyboard_create", targets=["EP01-EP03"]
        )
        storyboard = three_shot_storyboard()
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "storyboard": storyboard,
                "file_name": "三集基线.xlsx",
            },
        )
        self.assertEqual(committed["status"], "pass")
        audited = await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        self.assertEqual(audited["status"], "pass")
        return project_id, storyboard, committed

    async def delta_arguments(
        self,
        project_id: str,
        storyboard: dict[str, Any],
        committed: dict[str, Any],
        work_order_id: str,
    ) -> dict[str, Any]:
        read = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": committed["artifact_id"],
                "section": "canonical_storyboard",
            },
        )
        manifest = read["data"]["shot_identity_manifest"]
        identities = manifest["shot_uids"]
        replacement_shot = copy.deepcopy(storyboard["shots"][1])
        replacement_shot["画面内容"] = (
            "【0.0—0.8秒】EP02女主在原位先停步；"
            "【0.8—3.3秒】她再确认桌上信件，因果、认知与表演连续，本镜无对白；"
            "【3.3—4.0秒】她保持视线与稳定呼吸。"
        )
        performance = copy.deepcopy(
            storyboard["prevention_evidence"]["performance_chains"][1]
        )
        performance["body_or_breath"] = "停步后呼吸轻收，再随视线确认恢复稳定"
        return {
            "project_id": project_id,
            "work_order_id": work_order_id,
            "base_artifact_id": committed["artifact_id"],
            "expected_base_canonical_sha256": committed["data"]["canonical_sha256"],
            "expected_base_raw_fingerprint": committed["data"]["artifact"]["fingerprint"],
            "scope_target": "EP02",
            "affected_shot_uids": [identities[1]["shot_uid"]],
            "left_boundary_shot_uid": identities[0]["shot_uid"],
            "right_boundary_shot_uid": identities[2]["shot_uid"],
            "replacement_shots": [replacement_shot],
            "evidence_updates": {"performance_chains": [performance]},
            "file_name": "EP02增量版.xlsx",
        }

    async def test_delta_preserves_untouched_semantics_and_materializes_full_output(self) -> None:
        project_id, storyboard, committed = await self.committed_base("增量等质主路径")
        change_order = await self.begin(
            project_id,
            operation="storyboard_change",
            targets=["EP02"],
            protected_scope=["episode:EP01", "episode:EP03"],
        )
        arguments = await self.delta_arguments(
            project_id, storyboard, committed, change_order
        )
        committed_canonical, _ = self.service._storyboard_from_artifact(
            self.namespace, project_id, str(committed["artifact_id"])
        )
        result = await self.call("commit_storyboard_delta", arguments)
        self.assertEqual(result["status"], "pass")
        manifest = result["data"]["delta_manifest"]
        self.assertTrue(manifest["untouched_semantic_hashes_verified"])
        self.assertEqual(manifest["untouched_shot_count"], 2)
        self.assertEqual(manifest["materialization_stage"], "compact_canonical_only")
        self.assertEqual(manifest["model_authored_derived_chars"], 0)
        self.assertEqual(manifest["legacy_generation_calls"], 0)
        self.assertEqual(manifest["seedance_batches_generated"], 0)
        self.assertEqual(manifest["duplicate_generated_bytes"], 0)
        self.assertGreater(manifest["payload_reduction_percent"], 0)
        self.assertEqual(result["data"]["measurement_scope"], "server_only")
        self.assertIn("chatgpt_model_generation", result["data"]["unmeasured_stages"])
        final, _record = self.service._storyboard_from_artifact(
            self.namespace, project_id, str(result["artifact_id"])
        )
        self.assertEqual(final["shots"][0], committed_canonical["shots"][0])
        self.assertEqual(final["shots"][2], committed_canonical["shots"][2])
        self.assertIn("EP02女主在原位先停步", final["shots"][1]["画面内容"])
        self.assertNotIn("batches", final)
        self.assertNotIn("正式分镜母版·字段合并", final["shots"][1])
        self.assertNotIn("GPT关键帧生成指令", final["shots"][1])
        self.assertEqual(final["total_duration"], 12)
        self.assertTrue(all(row.get("shot_uid") for row in final["shots"]))
        self.assertEqual(result["data"]["report"]["issues"], [])

        from shortdrama_mcp.delta import merge_storyboard_delta
        from shortdrama_mcp.service import canonical_storyboard_sha256

        reference, _reference_manifest = merge_storyboard_delta(
            storyboard,
            affected_shot_uids=arguments["affected_shot_uids"],
            replacement_shots=arguments["replacement_shots"],
            evidence_updates=arguments["evidence_updates"],
        )
        self.assertEqual(
            canonical_storyboard_sha256(reference), result["data"]["canonical_sha256"]
        )

        hub = self.service.store.load_hub(self.namespace, project_id)
        self.assertFalse(
            any(row.get("server_actor_role") == "repair" for row in hub["work_orders"]),
            "clean production plus audit must not activate the repair window",
        )

        repeated = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("canonical_hash_conflict", repeated)

    async def test_delta_rejects_wrong_boundary_and_protected_scope(self) -> None:
        project_id, storyboard, committed = await self.committed_base("增量边界阻断")
        change_order = await self.begin(
            project_id, operation="storyboard_change", targets=["EP02"]
        )
        arguments = await self.delta_arguments(
            project_id, storyboard, committed, change_order
        )
        arguments["left_boundary_shot_uid"] = "SHOT-forged"
        rejected = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("delta_boundary_mismatch", rejected)

        protected_project, protected_storyboard, protected_committed = await self.committed_base(
            "增量保护范围阻断"
        )
        protected_order = await self.begin(
            protected_project,
            operation="storyboard_change",
            targets=["EP02"],
            protected_scope=["episode:EP02"],
        )
        protected_arguments = await self.delta_arguments(
            protected_project,
            protected_storyboard,
            protected_committed,
            protected_order,
        )
        protected_rejected = await self.rejection(
            "commit_storyboard_delta", protected_arguments
        )
        self.assertIn("protected_scope_violation", protected_rejected)

    async def test_delta_scope_is_bound_to_rows_and_top_level_evidence_is_immutable(self) -> None:
        project_id, storyboard, committed = await self.committed_base("增量目标归属阻断")
        change_order = await self.begin(
            project_id, operation="storyboard_change", targets=["EP01"]
        )
        arguments = await self.delta_arguments(
            project_id, storyboard, committed, change_order
        )
        arguments["scope_target"] = "EP01"
        rejected = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("delta_scope_binding_mismatch", rejected)

        from shortdrama_mcp.delta import ensure_shot_uids, merge_storyboard_delta

        migrated, manifest = ensure_shot_uids(storyboard)
        with self.assertRaisesRegex(ValueError, "delta_top_level_requires_full_commit"):
            merge_storyboard_delta(
                migrated,
                affected_shot_uids=[manifest["shot_uids"][1]["shot_uid"]],
                replacement_shots=arguments["replacement_shots"],
                top_level_updates={"coverage": {"forged": True}},
            )

    async def test_failed_delta_is_atomic_and_returns_trace(self) -> None:
        project_id, storyboard, committed = await self.committed_base("增量失败原子性")
        change_order = await self.begin(
            project_id, operation="storyboard_change", targets=["EP02"]
        )
        arguments = await self.delta_arguments(
            project_id, storyboard, committed, change_order
        )
        arguments["replacement_shots"][0]["光源来源"] = ""
        before = {row["artifact_id"] for row in self.service.store.list_artifacts(self.namespace, project_id)}
        result = await self.call("commit_storyboard_delta", arguments)
        after = {row["artifact_id"] for row in self.service.store.list_artifacts(self.namespace, project_id)}
        self.assertEqual(result["status"], "fail_current")
        self.assertEqual(before, after)
        self.assertIsNone(result.get("artifact_id"))
        self.assertTrue(result.get("trace_id"))
        self.assertGreater(len(result["data"]["report"]["issues"]), 0)

    async def test_default_long_read_uses_automatic_bulk(self) -> None:
        project_id = await self.create_project("自动批量读取")
        text = "甲乙丙丁戊己庚辛壬癸" * 36_655
        record = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"kind": "text", "format": "txt", "text": text},
            file_name="366k原文_提取内容.json",
            artifact_type="source_extraction",
            version="v1",
        )
        result = await self.call(
            "read_project_artifact",
            {"project_id": project_id, "artifact_id": record["artifact_id"]},
        )
        content = result["data"]["content"]
        self.assertEqual(result["data"]["read_mode"], "automatic_bulk")
        self.assertEqual(content["returned_chars"], 16_000)
        self.assertTrue(content["truncated"])
        self.assertEqual(content["ranges"][0]["next_offset"], 16_000)

    async def test_full_commit_persists_all_stable_internal_ids(self) -> None:
        project_id = await self.create_project("全量提交稳定身份")
        work_order_id = await self.begin(
            project_id, operation="storyboard_create", targets=["单集"]
        )
        storyboard = three_shot_storyboard()
        for shot in storyboard["shots"]:
            shot.pop("shot_uid", None)
            shot.pop("scene_uid", None)
            shot.pop("episode_id", None)
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "storyboard": storyboard,
                "file_name": "稳定身份全量版.xlsx",
            },
        )
        self.assertEqual(committed["status"], "pass")
        final, record = self.service._storyboard_from_artifact(
            self.namespace, project_id, str(committed["artifact_id"])
        )
        self.assertTrue(all(row.get("shot_uid") for row in final["shots"]))
        self.assertTrue(all(row.get("scene_uid") for row in final["shots"]))
        self.assertTrue(all(row.get("episode_id") for row in final["shots"]))
        self.assertNotIn("batches", final)
        self.assertEqual(len({row["shot_uid"] for row in final["shots"]}), 3)
        self.assertEqual(record["metadata"]["stable_shot_uid_count"], 3)
        self.assertEqual(record["metadata"].get("stable_batch_uid_count", 0), 0)
        self.assertEqual(len(committed["data"]["identity_manifest"]["shot_uids"]), 3)

    async def test_server_trace_marks_unobserved_metrics_instead_of_fabricating_them(self) -> None:
        project_id = await self.create_project("诚实时长证据")
        work_order_id = await self.begin(
            project_id, operation="storyboard_create", targets=["EP01-EP03"]
        )
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "storyboard": three_shot_storyboard(),
                "file_name": "诚实时长.xlsx",
            },
        )
        trace = committed["data"]["telemetry"]
        self.assertEqual(trace["measurement_scope"], "server_only")
        self.assertIn("chatgpt_model_generation", trace["unmeasured_stages"])
        self.assertIn("stage_tool_call_count", trace["unmeasured_stages"])
        self.assertIn("stage_retry_count", trace["unmeasured_stages"])
        self.assertTrue(trace["started_at"])
        self.assertTrue(trace["ended_at"])
        self.assertTrue(all(row["tool_call_count"] is None for row in trace["stages"]))
        self.assertTrue(all(row["retry_count"] is None for row in trace["stages"]))

    async def test_storyboard_role_matrix_blocks_plan_only_mutation(self) -> None:
        project_id = await self.create_project("窗口角色矩阵")
        rejected = await self.rejection(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": "storyboard_change",
                "mode": "plan_only",
                "authorization": "plan_only",
                "request_text": "只给方案却伪装成可写变更",
                "targets": ["EP02"],
                "authoritative_source": "测试",
                "latest_version": "v1",
                "target_boundary": "EP02",
                "must_keep": [],
                "must_remove": [],
                "protected_scope": [],
                "expected_outputs": ["方案"],
                "window_key": "invalid-role",
            },
        )
        self.assertIn("window_role_matrix_mismatch", rejected)

    async def test_repair_must_return_to_original_audit_window(self) -> None:
        project_id = await self.create_project("修复回原审计窗口")
        production_order = await self.begin(
            project_id, operation="storyboard_create", targets=["EP01"]
        )
        broken = three_shot_storyboard()
        broken["shots"] = [broken["shots"][0]]
        broken["total_duration"] = 4
        evidence = broken["prevention_evidence"]
        for field in ("source_ledger", "scene_state_ledger", "shot_plan", "performance_chains"):
            evidence[field] = evidence[field][:1]
        broken["shots"][0]["光源来源"] = ""
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": production_order,
                "storyboard": broken,
                "file_name": "待审计修复.xlsx",
            },
        )
        self.assertEqual(committed["status"], "fail_current")
        audited = await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        self.assertEqual(audited["status"], "fail_current")
        original_audit_order = audited["data"]["audit_work_order_id"]
        repair_task = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": "storyboard_repair",
                "mode": "audit_and_repair",
                "authorization": "explicit_execute",
                "request_text": "消费审计包修复光源字段",
                "targets": ["EP01-001"],
                "authoritative_source": "独立审计证书",
                "latest_version": "current",
                "target_boundary": "EP01-001",
                "must_keep": ["全部剧情与拓扑"],
                "must_remove": [],
                "protected_scope": [],
                "expected_outputs": ["修复版", "repair_to_audit"],
                "window_key": "repair-window",
            },
        )
        certificate = audited["data"]["certificate"]
        patched = await self.call(
            "commit_storyboard_patch",
            {
                "project_id": project_id,
                "work_order_id": repair_task["work_order_id"],
                "base_artifact_id": committed["artifact_id"],
                "expected_canonical_hash": committed["data"]["canonical_sha256"],
                "expected_raw_artifact_fingerprint": committed["data"]["artifact"]["fingerprint"],
                "audit_certificate_id": certificate["certificate_id"],
                "operations": [
                    {
                        "op": "replace_field",
                        "shot_id": "EP01-001",
                        "field": "光源来源",
                        "value": "窗纸透入的日光",
                        "issue_ids": ["E-TEMPLATE-008"],
                        "reason": "只修复证书确认的光源缺失",
                    }
                ],
                "impact_scope": ["EP01-001"],
                "file_name": "修复后.xlsx",
            },
        )
        wrong_auditor = await self.rejection(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": patched["artifact_id"],
                "handoff_artifact_id": patched["data"]["handoff"]["handoff_id"],
            },
        )
        self.assertIn("repair_return_audit_mismatch", wrong_auditor)

        final = await self.call(
            "audit_storyboard",
            {
                "project_id": project_id,
                "artifact_id": patched["artifact_id"],
                "work_order_id": original_audit_order,
                "handoff_artifact_id": patched["data"]["handoff"]["handoff_id"],
            },
        )
        self.assertEqual(final["status"], "pass")
        self.assertEqual(final["data"]["audit"]["event"]["audit_round"], 2)

    def test_internal_ids_survive_xlsx_roundtrip(self) -> None:
        from shortdrama_mcp.delta import ensure_shot_uids, merge_storyboard_delta
        from shortdrama_mcp.file_io import extract_xlsx, storyboard_to_xlsx
        import validate_storyboard

        base = three_shot_storyboard()
        identified, manifest = ensure_shot_uids(base)
        middle_uid = manifest["shot_uids"][1]["shot_uid"]
        replacement_shot = copy.deepcopy(base["shots"][1])
        merged, _delta = merge_storyboard_delta(
            base,
            affected_shot_uids=[middle_uid],
            replacement_shots=[replacement_shot],
        )
        report = validate_storyboard.validate_project(merged, mode="audit")
        self.assertEqual(report["issues"], [])
        extracted = extract_xlsx(storyboard_to_xlsx(merged, report))
        roundtrip = extracted["canonical_storyboard"]
        self.assertEqual(
            [row["shot_uid"] for row in roundtrip["shots"]],
            [row["shot_uid"] for row in merged["shots"]],
        )
        self.assertNotIn("batches", roundtrip)
        self.assertEqual(
            stable_json_hash(roundtrip["shots"]), stable_json_hash(merged["shots"])
        )
