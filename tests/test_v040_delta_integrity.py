from __future__ import annotations

import copy
import os
import tempfile
import unittest
from typing import Any

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings
from shortdrama_mcp.delta import ensure_shot_uids, merge_storyboard_delta
from shortdrama_mcp.models import MAX_DELTA_ITEMS, StoryboardDeltaRequestInput

from test_v040_architecture import three_shot_storyboard


def delta_parts() -> tuple[dict[str, Any], str, dict[str, Any]]:
    base = three_shot_storyboard()
    _identified, manifest = ensure_shot_uids(base)
    uid = str(manifest["shot_uids"][1]["shot_uid"])
    shot = copy.deepcopy(base["shots"][1])
    return base, uid, shot


class DeltaMergeIntegrityTests(unittest.TestCase):
    def test_episode_and_scene_identity_cannot_escape(self) -> None:
        base, uid, shot = delta_parts()
        shot["episode_id"] = "EP99"
        with self.assertRaisesRegex(ValueError, "delta_scope_move_requires_full_commit"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot],
            )

        base, uid, shot = delta_parts()
        shot["场景连续性ID"] = "SC99"
        with self.assertRaisesRegex(ValueError, "delta_scope_move_requires_full_commit"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot],
            )

    def test_missing_identity_is_inherited_and_structural_change_routes_full(self) -> None:
        base, uid, shot = delta_parts()
        for field in ("episode_id", "scene_uid", "shot_uid"):
            shot.pop(field, None)
        merged, _manifest = merge_storyboard_delta(
            base,
            affected_shot_uids=[uid],
            replacement_shots=[shot],
        )
        self.assertEqual(merged["shots"][1]["episode_id"], "EP02")
        self.assertEqual(merged["shots"][1]["shot_uid"], uid)
        self.assertTrue(merged["shots"][1]["scene_uid"])

        extra = copy.deepcopy(shot)
        extra["镜头编号"] = "S002B"
        with self.assertRaisesRegex(ValueError, "delta_structural_change_requires_full_commit"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot, extra],
            )

    def test_visual_change_refreshes_plan_and_rejects_stale_supplied_plan(self) -> None:
        base, uid, shot = delta_parts()
        old_plan = copy.deepcopy(base["prevention_evidence"]["shot_plan"][1])
        shot["画面内容"] = "新的受控画面动作，人物仍在原集原场景"
        performance = copy.deepcopy(
            base["prevention_evidence"]["performance_chains"][1]
        )
        performance["body_or_breath"] = "受控画面变化后呼吸与身体连带重新对齐"
        merged, manifest = merge_storyboard_delta(
            base,
            affected_shot_uids=[uid],
            replacement_shots=[shot],
            evidence_updates={"performance_chains": [performance]},
        )
        new_plan = merged["prevention_evidence"]["shot_plan"][1]
        self.assertNotEqual(old_plan, new_plan)
        self.assertEqual(new_plan["main_action_stage"], shot["画面内容"])
        self.assertEqual(new_plan["shot_uid"], uid)
        self.assertTrue(new_plan["shot_semantic_sha256"])
        self.assertTrue(manifest["global_evidence_hashes_verified"])

        with self.assertRaisesRegex(ValueError, "delta_evidence_stale"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot],
                evidence_updates={
                    "shot_plan": [old_plan],
                    "performance_chains": [performance],
                },
            )

    def test_performance_and_scene_changes_require_complete_affected_evidence(self) -> None:
        base, uid, shot = delta_parts()
        shot["画面内容"] = (
            "[0.0–0.8s] 人物在原位停步；[0.8–3.3s] 她收紧呼吸，再抬眼确认信件位置，"
            "眉眼和肩颈随动作变化，本镜无对白；[3.3–4.0s] 她保持视线，动作收束。"
        )
        shot["起始状态"] = "人物仍在原位，呼吸已收紧，视线尚未抬向信件"
        with self.assertRaisesRegex(ValueError, "delta_evidence_missing"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot],
            )

        performance = copy.deepcopy(base["prevention_evidence"]["performance_chains"][1])
        performance["body_or_breath"] = "呼吸收紧后恢复，肩颈随动作回落"
        scene_state = copy.deepcopy(base["prevention_evidence"]["scene_state_ledger"][1])
        scene_state["transition_explanation"] = "动作更新后仍在同一位置连续收束"
        merged, manifest = merge_storyboard_delta(
            base,
            affected_shot_uids=[uid],
            replacement_shots=[shot],
            evidence_updates={
                "performance_chains": [performance],
                "scene_state_ledger": [scene_state],
            },
        )
        self.assertEqual(manifest["updated_evidence_counts"]["performance_chains"], 1)
        self.assertEqual(manifest["updated_evidence_counts"]["scene_state_ledger"], 1)
        self.assertEqual(
            merged["prevention_evidence"]["performance_chains"][1]["shot_uid"], uid
        )

    def test_dialogue_or_speaker_delta_requires_full_commit(self) -> None:
        base, uid, shot = delta_parts()
        shot["画面内容"] = (
            "[0.0–0.8s] 人物先接住对方视线；[0.8–3.3s] 她压低声音说："
            "【女主】这是新的台词。口型、呼吸停顿与重音一致；"
            "[3.3–4.0s] 她收住尾音并保持原站位。"
        )
        shot["音色"] = "压低、克制，声量中等，重音清楚"
        with self.assertRaisesRegex(
            ValueError, "delta_semantic_requires_full_commit"
        ):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[shot],
            )

        tagged_base = copy.deepcopy(base)
        tagged_base["shots"][1]["画面内容"] = "【女主】原台词。"
        tagged_base, tagged_manifest = ensure_shot_uids(tagged_base)
        tagged_uid = str(tagged_manifest["shot_uids"][1]["shot_uid"])
        speaker_change = copy.deepcopy(tagged_base["shots"][1])
        speaker_change["画面内容"] = "【男主】原台词。"
        with self.assertRaisesRegex(
            ValueError, "delta_semantic_requires_full_commit"
        ):
            merge_storyboard_delta(
                tagged_base,
                affected_shot_uids=[tagged_uid],
                replacement_shots=[speaker_change],
            )

        quoted_change = copy.deepcopy(tagged_base["shots"][1])
        quoted_base = copy.deepcopy(tagged_base)
        quoted_base["shots"][1]["画面内容"] = "女主低声说：“原台词。”"
        quoted_change = copy.deepcopy(quoted_base["shots"][1])
        quoted_change["画面内容"] = "女主低声说：“新台词。”"
        with self.assertRaisesRegex(
            ValueError, "delta_semantic_requires_full_commit"
        ):
            merge_storyboard_delta(
                quoted_base,
                affected_shot_uids=[tagged_uid],
                replacement_shots=[quoted_change],
            )

    def test_source_and_psychological_audio_changes_require_full_route(self) -> None:
        for field in ("覆盖原文", "心理音轨"):
            with self.subTest(field=field):
                base, uid, shot = delta_parts()
                shot[field] = f"新的{field}"
                with self.assertRaisesRegex(
                    ValueError, "delta_semantic_requires_full_commit"
                ):
                    merge_storyboard_delta(
                        base,
                        affected_shot_uids=[uid],
                        replacement_shots=[shot],
                    )

    def test_typed_request_limits_apply_before_merge(self) -> None:
        base, _uid, shot = delta_parts()
        with self.assertRaisesRegex(ValueError, "at most"):
            StoryboardDeltaRequestInput.model_validate(
                {
                    "affected_shot_uids": [f"SHOT-{index}" for index in range(MAX_DELTA_ITEMS + 1)],
                    "replacement_shots": [shot],
                }
            )
        too_long = copy.deepcopy(shot)
        too_long["画面内容"] = "字" * 20_001
        with self.assertRaisesRegex(ValueError, "delta_field_too_large"):
            StoryboardDeltaRequestInput.model_validate(
                {
                    "affected_shot_uids": ["SHOT-one"],
                    "replacement_shots": [too_long],
                }
            )


class DeltaProtectedScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-compact-integrity-")
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
        result = await self.server.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.content)
        assert result.structured_content is not None
        return result.structured_content

    async def rejection(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self.server.call_tool(name, arguments)
        except Exception as exc:
            return str(exc)
        return "\n".join(str(item) for item in result.content)

    async def prepare(self, protected_scope: list[str]) -> dict[str, Any]:
        suffix = "-".join(protected_scope) or "无保护"
        project = await self.call(
            "create_project", {"project_name": f"差量字段保护专项-{suffix}"}
        )
        project_id = str(project["project_id"])
        await self.call("get_agent_status", {"project_id": project_id, "full_self_test": True})

        async def begin(operation: str, targets: list[str], protected: list[str]) -> str:
            task = await self.call(
                "begin_project_task",
                {
                    "project_id": project_id,
                    "operation": operation,
                    "mode": "execute",
                    "authorization": "explicit_execute",
                    "request_text": "字段保护专项",
                    "targets": targets,
                    "authoritative_source": "锁定源文",
                    "latest_version": "current",
                    "target_boundary": ",".join(targets),
                    "must_keep": ["未授权字段"],
                    "must_remove": [],
                    "protected_scope": protected,
                    "expected_outputs": ["正式分镜"],
                    "window_key": operation,
                },
            )
            return str(task["work_order_id"])

        base = three_shot_storyboard()
        create_order = await begin("storyboard_create", ["EP01-EP03"], [])
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": create_order,
                "storyboard": base,
                "file_name": "字段保护基线.xlsx",
            },
        )
        change_order = await begin("storyboard_change", ["EP02"], protected_scope)
        read = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": committed["artifact_id"],
                "section": "canonical_storyboard",
            },
        )
        identities = read["data"]["shot_identity_manifest"]["shot_uids"]
        shot = copy.deepcopy(base["shots"][1])
        shot["画面内容"] = "被保护字段的非法新画面"
        return {
            "project_id": project_id,
            "work_order_id": change_order,
            "base_artifact_id": committed["artifact_id"],
            "expected_base_canonical_sha256": committed["data"]["canonical_sha256"],
            "expected_base_raw_fingerprint": committed["data"]["artifact"]["fingerprint"],
            "scope_target": "EP02",
            "affected_shot_uids": [identities[1]["shot_uid"]],
            "left_boundary_shot_uid": identities[0]["shot_uid"],
            "right_boundary_shot_uid": identities[2]["shot_uid"],
            "replacement_shots": [shot],
            "file_name": "不应生成.xlsx",
        }

    async def test_exact_field_and_range_field_protection_block_actual_change(self) -> None:
        exact = await self.prepare(["S002.画面内容"])
        rejected = await self.rejection("commit_storyboard_delta", exact)
        self.assertIn("protected_scope_violation", rejected)
        self.assertIn("S002.画面内容", rejected)

        ranged = await self.prepare(["S001-S003.画面内容"])
        rejected = await self.rejection("commit_storyboard_delta", ranged)
        self.assertIn("protected_scope_violation", rejected)

    async def test_tool_schema_exposes_bounded_arrays_and_evidence_contract(self) -> None:
        tools = await self.server.list_tools()
        delta = next(tool for tool in tools if tool.name == "commit_storyboard_delta")
        schema = delta.model_dump(by_alias=True, exclude_none=True)["inputSchema"]
        self.assertEqual(schema["properties"]["affected_shot_uids"]["maxItems"], MAX_DELTA_ITEMS)
        self.assertEqual(schema["properties"]["replacement_shots"]["maxItems"], MAX_DELTA_ITEMS)
        self.assertNotIn("replacement_batches", schema.get("required", []))
        self.assertIn("evidence_updates", schema["properties"])

    async def test_expansion_ratio_rejects_pathological_scope_payload(self) -> None:
        arguments = await self.prepare([])
        shot = arguments["replacement_shots"][0]
        for index in range(10):
            shot[f"扩展字段{index}"] = "字" * 15_000
        rejected = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("delta_expansion_ratio_exceeded", rejected)

    async def test_dialogue_delta_rejection_has_no_artifact_or_version_side_effect(self) -> None:
        arguments = await self.prepare([])
        arguments["replacement_shots"][0]["画面内容"] = (
            "[0.0–1.0s] 女主保持原站位并抬眼；"
            "[1.0–4.0s] 【女主】这句对白必须走完整提交。"
        )
        project_id = str(arguments["project_id"])
        artifacts_before = copy.deepcopy(
            self.service.store.list_artifacts(self.namespace, project_id)
        )
        hub_before = copy.deepcopy(
            self.service.store.load_hub(self.namespace, project_id)
        )

        rejected = await self.rejection("commit_storyboard_delta", arguments)
        self.assertIn("delta_semantic_requires_full_commit", rejected)
        self.assertEqual(
            self.service.store.list_artifacts(self.namespace, project_id),
            artifacts_before,
        )
        hub_after = self.service.store.load_hub(self.namespace, project_id)
        self.assertEqual(hub_after.get("version_history"), hub_before.get("version_history"))
        self.assertEqual(hub_after.get("deliverables"), hub_before.get("deliverables"))
        self.assertEqual(
            hub_after.get("project_state", {}).get("latest_artifacts"),
            hub_before.get("project_state", {}).get("latest_artifacts"),
        )


if __name__ == "__main__":
    unittest.main()
