from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.canonical import (
    COMPACT_SHOT_FIELDS,
    LEGACY_DERIVED_SHOT_FIELDS,
    NO_DUPLICATE_GENERATION_EVIDENCE,
    compact_storyboard_canonical,
)
from shortdrama_mcp.config import Settings
from shortdrama_mcp.delta import ensure_shot_uids, merge_storyboard_delta
from shortdrama_mcp.file_io import storyboard_to_xlsx
from shortdrama_mcp.materializer import migrate_legacy_shot
from shortdrama_mcp.models import (
    SEMANTIC_AUDIT_LAYER_NAMES,
    SemanticAuditInput,
    SemanticAuditIssueInput,
    SemanticAuditLayerInput,
    StoryboardDeltaRequestInput,
)
from shortdrama_mcp.service import (
    StoryboardService,
    _export_compact_storyboard,
    _gate_results,
    _prepare_compact_storyboard_for_commit,
)


def _compact_shot(shot_id: str, *, cumulative: float) -> dict:
    shot = {field: f"{field}-{shot_id}" for field in COMPACT_SHOT_FIELDS}
    shot.update(
        {
            "镜头编号": shot_id,
            "时长": 3.0,
            "累计时长": cumulative,
            "功能类型": "推进对话",
            "场景连续性ID": "E01-S01-HALL-MORNING",
            "空间站位描述": "主角在中央，听者在右侧主位",
            "起始状态": "主角面向右侧，双手收于身前",
            "机位": "左前方平视50mm。",
            "画面内容": "0.0-1.2s主角抬眼；1.2-3.0s【主角】请见谅。",
            "音色": "平静克制",
            "环境音效": "近处衣料与呼吸声。",
            "光影": "左侧晨光向右衰减",
            "光源来源": "左侧木格窗自然晨光",
            "光源方向": "左向右",
            "色温倾向": "中性微暖",
        }
    )
    return shot


def _compact_base() -> dict:
    storyboard = {
        "schema_version": "R10.1",
        "shots": [
            _compact_shot("EP01-001", cumulative=3.0),
            _compact_shot("EP01-002", cumulative=6.0),
        ],
        "prevention_evidence": {
            "shot_plan": [
                {"shot_id": "EP01-001", "main_action_stage": "旧动作1"},
                {"shot_id": "EP01-002", "main_action_stage": "旧动作2"},
            ],
            "performance_chains": [
                {"shot_id": "EP01-001", "body_or_breath": "稳定1"},
                {"shot_id": "EP01-002", "body_or_breath": "稳定2"},
            ],
            "scene_state_ledger": [
                {"shot_id": "EP01-001", "transition_explanation": "连续1"},
                {"shot_id": "EP01-002", "transition_explanation": "连续2"},
            ],
            "source_mapping": [{"source": "locked"}],
        },
    }
    identified, _manifest = ensure_shot_uids(storyboard)
    return compact_storyboard_canonical(identified)


class CompactCanonicalContractTests(unittest.TestCase):
    def test_legacy_split_fields_fold_once_and_batches_are_removed(self) -> None:
        legacy = {
            "schema_version": "R10.0",
            "shots": [
                {
                    "镜头编号": "EP06-004",
                    "时长": 3.6,
                    "累计时长": 12.2,
                    "功能类型": "礼数致歉",
                    "场景连续性ID": "E06-S01-HALL-MORNING",
                    "镜头类型": "中近景",
                    "可见主体": "苏清柳",
                    "机位": "平视65mm",
                    "运镜方式": "极缓推近",
                    "运镜主体": "苏清柳",
                    "运镜功能": "聚焦注意力",
                    "人物调度": "苏清柳站中央偏左，面向右侧主位",
                    "画面内容": "0.0-1.0s她先看向听者",
                    "动作与表演": "垂眸整理袖口后重新抬眼",
                    "台词": "【苏清柳】母亲，儿媳失手，请见谅。",
                    "音色": "平静克制、温和带锋",
                    "发声方式": "对白清晰，速度自然",
                    "标点语气": "句号落下收住",
                    "心理音轨": "不适用",
                    "心理音轨属性": "本镜无心理音轨",
                    "环境音效": "近处衣料声",
                    "环境动态": "私宅礼仪空间",
                    "光影": "左侧晨光向右衰减",
                    "光源来源": "左侧木格窗自然晨光",
                    "光源方向": "左向右",
                    "色温倾向": "中性微暖",
                    "生成限制": "9:16真人古装短剧",
                    "转场类型": "视线与动作切",
                    "切镜/转场提示词": "直身站稳后切",
                    "参考图锚定": "A01",
                    "覆盖原文": "是",
                    "原文依据类型": "直接对白",
                    "原文支撑句": "母亲，儿媳失手，请见谅。",
                    "生成批次": "B01",
                    "正式分镜母版·字段合并": "旧派生长文",
                }
            ],
            "batches": [{"批次编号": "B01"}],
        }
        result = compact_storyboard_canonical(legacy)
        shot = result["shots"][0]
        self.assertEqual(set(shot), set(COMPACT_SHOT_FIELDS))
        self.assertNotIn("batches", result)
        self.assertFalse(set(shot) & LEGACY_DERIVED_SHOT_FIELDS)
        self.assertIn("苏清柳", shot["运镜方式"])
        self.assertIn("聚焦注意力", shot["运镜方式"])
        self.assertIn("垂眸整理袖口", shot["画面内容"])
        self.assertIn("【苏清柳】母亲", shot["画面内容"])
        self.assertEqual(
            shot["音色"],
            "平静克制、温和带锋；历史发声参数（待按固定音色子模板修订）：对白清晰，速度自然",
        )

    def test_declared_compact_input_cannot_smuggle_legacy_fields(self) -> None:
        shot = _compact_shot("EP01-001", cumulative=3.0)
        shot["台词"] = "不得保留"
        with self.assertRaisesRegex(ValueError, "compact_contract_violation"):
            compact_storyboard_canonical(
                {"schema_version": "R10.1", "shots": [shot]}
            )

    def test_old_delta_batch_parameter_is_accepted_then_erased(self) -> None:
        request = StoryboardDeltaRequestInput.model_validate(
            {
                "affected_shot_uids": ["SHOT-1"],
                "replacement_shots": [_compact_shot("EP01-001", cumulative=3.0)],
                "replacement_batches": [{"批次编号": "OLD-B01"}],
            }
        )
        self.assertEqual(request.replacement_batches, [])

    def test_full_commit_preparation_compacts_exactly_once(self) -> None:
        source = {
            "schema_version": "R10.1",
            "shots": [_compact_shot("EP01-001", cumulative=3.0)],
        }
        with patch(
            "shortdrama_mcp.service.compact_storyboard_canonical",
            wraps=compact_storyboard_canonical,
        ) as compact:
            prepared, manifest = _prepare_compact_storyboard_for_commit(source)
        self.assertEqual(compact.call_count, 1)
        self.assertEqual(len(manifest["shot_uids"]), 1)
        self.assertTrue(prepared["shots"][0]["shot_uid"])
        with patch(
            "shortdrama_mcp.service.storyboard_to_xlsx", return_value=b"xlsx"
        ) as exporter:
            self.assertEqual(_export_compact_storyboard(prepared, {"issues": []}), b"xlsx")
        exporter.assert_called_once_with(
            prepared,
            {"issues": []},
            assume_compact=True,
            audit_overlay=None,
        )

    def test_old_audit_layer_name_reads_as_compact_layer(self) -> None:
        row = SemanticAuditLayerInput.model_validate(
            {
                "layer": "Seedance独立执行",
                "status": "pass",
                "summary": "旧证书只读兼容",
                "evidence_ids": ["shot:EP01-001"],
            }
        )
        self.assertEqual(row.layer, "正式母版单镜执行完整性")

    def test_compact_audit_rejects_active_batch_counts_and_targets(self) -> None:
        layers = [
            {
                "layer": layer,
                "status": "pass",
                "summary": "pass",
                "evidence_ids": ["shot:EP01-001"],
            }
            for layer in SEMANTIC_AUDIT_LAYER_NAMES
        ]
        with self.assertRaisesRegex(ValueError, "Input should be 0"):
            SemanticAuditInput.model_validate(
                {
                    "canonical_sha256": "a" * 64,
                    "reviewed_shot_count": 1,
                    "reviewed_batch_count": 1,
                    "layers": layers,
                    "issues": [],
                    "conclusion": {"status": "pass", "summary": "pass"},
                }
            )

    def test_formal_master_issue_uses_new_gate_with_legacy_ledger_fallback(self) -> None:
        issue = [{"error_code": "E-SEEDANCE-CROSSCONTEXT-001"}]
        current = _gate_results(
            ["compact_master_single_shot_integrity"], issue
        )
        self.assertEqual(current["compact_master_single_shot_integrity"], "fail")
        legacy = _gate_results(["seedance_independence"], issue)
        self.assertEqual(legacy["seedance_independence"], "fail")
        with self.assertRaisesRegex(ValueError, "semantic_audit_batch_removed"):
            SemanticAuditIssueInput.model_validate(
                {
                    "issue_id": "I-1",
                    "layer": "正式母版单镜执行完整性",
                    "error_code": "E-COMPACT-1",
                    "batch_ids": ["B01"],
                    "evidence_ids": ["shot:EP01-001"],
                    "evidence": "单镜证据",
                    "repair_action": "修复正式母版",
                }
            )

    def test_patch_rejects_all_server_identity_and_derived_fields(self) -> None:
        forbidden = (
            "shot_uid",
            "episode_id",
            "scene_uid",
            "artifact_id",
            "work_order_id",
            "handoff_id",
            "trace_id",
            "canonical_sha256",
            "source_fingerprint",
            "schema_version",
            "materializer_contract",
            "duplicate_generated_bytes",
            "server_actor_binding_id",
            "正式分镜母版·字段合并",
            "GPT关键帧生成指令",
        )
        for field in forbidden:
            with self.subTest(field=field):
                self.assertTrue(
                    StoryboardService._patch_field_requires_full_rebuild(field)
                )
        for authored_detail in ("音色", "光源来源", "光源方向", "色温倾向"):
            with self.subTest(authored_detail=authored_detail):
                self.assertFalse(
                    StoryboardService._patch_field_requires_full_rebuild(
                        authored_detail
                    )
                )


class CompactDeltaEvidenceTests(unittest.TestCase):
    def test_delta_runs_one_full_table_pass_and_one_affected_range_pass(self) -> None:
        base = _compact_base()
        uid = str(base["shots"][0]["shot_uid"])
        replacement = copy.deepcopy(base["shots"][0])
        with patch(
            "shortdrama_mcp.delta.compact_storyboard_canonical",
            wraps=compact_storyboard_canonical,
        ) as compact, patch(
            "shortdrama_mcp.delta.migrate_legacy_shot",
            wraps=migrate_legacy_shot,
        ) as local_normalize:
            merged, manifest = merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[replacement],
            )
        shot_counts = [len(call.args[0]["shots"]) for call in compact.call_args_list]
        self.assertEqual(shot_counts, [2])
        self.assertEqual(compact.call_count, 1)
        self.assertEqual(local_normalize.call_count, 1)
        self.assertEqual(manifest["base_canonicalization_passes"], 0)
        self.assertEqual(manifest["full_table_canonicalization_passes"], 1)
        self.assertEqual(manifest["delta_replacement_normalization_passes"], 1)
        self.assertEqual(manifest["delta_replacement_normalized_shot_count"], 1)
        self.assertEqual(manifest["materialization_passes"], 1)
        self.assertNotIn("batches", merged)

    def test_picture_change_requires_performance_evidence_and_generates_no_batch(self) -> None:
        base = _compact_base()
        uid = str(base["shots"][0]["shot_uid"])
        replacement = copy.deepcopy(base["shots"][0])
        replacement["画面内容"] += "；镜尾听者延后0.6s吸气"
        with self.assertRaisesRegex(ValueError, "performance_chains"):
            merge_storyboard_delta(
                base,
                affected_shot_uids=[uid],
                replacement_shots=[replacement],
            )

        performance = copy.deepcopy(
            base["prevention_evidence"]["performance_chains"][0]
        )
        performance["body_or_breath"] = "听者延后0.6s吸气，主角保持稳定"
        merged, manifest = merge_storyboard_delta(
            base,
            affected_shot_uids=[uid],
            replacement_shots=[replacement],
            evidence_updates={"performance_chains": [performance]},
        )
        self.assertNotIn("batches", merged)
        self.assertFalse(set(merged["shots"][0]) & LEGACY_DERIVED_SHOT_FIELDS)
        for key, expected in NO_DUPLICATE_GENERATION_EVIDENCE.items():
            self.assertEqual(manifest[key], expected)
        self.assertEqual(manifest["materialization_passes"], 1)

    def test_position_or_start_change_requires_scene_state_evidence(self) -> None:
        base = _compact_base()
        uid = str(base["shots"][0]["shot_uid"])
        for field in ("空间站位描述", "起始状态"):
            with self.subTest(field=field):
                replacement = copy.deepcopy(base["shots"][0])
                replacement[field] += "；与右侧主位距离保持两步"
                with self.assertRaisesRegex(ValueError, "scene_state_ledger"):
                    merge_storyboard_delta(
                        base,
                        affected_shot_uids=[uid],
                        replacement_shots=[replacement],
                    )


class WriteAuthorizationSideEffectTests(unittest.IsolatedAsyncioTestCase):
    namespace = "local-single-user"

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v050-auth-")
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

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def _ok(self, tool: str, arguments: dict) -> dict:
        result = await self.server.call_tool(tool, arguments)
        self.assertFalse(result.is_error, result.content)
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    def _project_bytes(self, project_id: str) -> dict[str, bytes]:
        directory = self.service.store.project_dir(self.namespace, project_id)
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    async def test_audit_window_cannot_write_source_or_generic_artifact(self) -> None:
        created = await self._ok("create_project", {"project_name": "audit-write-denial"})
        project_id = created["project_id"]
        await self._ok(
            "get_agent_status", {"project_id": project_id, "full_self_test": True}
        )
        task = await self._ok(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": "storyboard_audit",
                "mode": "audit_only",
                "authorization": "read_only",
                "request_text": "只读独立审计，不得写入来源或生产交付",
                "targets": ["artifact:pending"],
                "authoritative_source": "locked",
                "latest_version": "current",
                "target_boundary": "全表",
                "must_keep": [],
                "must_remove": [],
                "protected_scope": ["all"],
                "expected_outputs": ["独立审计结论"],
                "window_key": "audit-read-only",
            },
        )
        work_order_id = task["work_order_id"]
        hub_before = copy.deepcopy(
            self.service.store.load_hub(self.namespace, project_id)
        )
        artifacts_before = copy.deepcopy(
            self.service.store.list_artifacts(self.namespace, project_id)
        )
        bytes_before = self._project_bytes(project_id)

        with self.assertRaisesRegex(ToolError, "artifact_write_authority_mismatch"):
            await self.server.call_tool(
                "commit_project_artifact",
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "artifact_type": "rewrite_plan",
                    "output_format": "markdown",
                    "file_name": "audit-must-not-write.md",
                    "content_text": "forbidden",
                },
            )

        with self.assertRaisesRegex(ToolError, "source_import_authority_required"):
            await self.server.call_tool(
                "import_source_file",
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "file": {
                        "download_url": "https://example.invalid/must-not-download.txt",
                        "file_id": "file_forbidden_audit_write",
                        "file_name": "must-not-download.txt",
                        "mime_type": "text/plain",
                    },
                    "source_role": "novel",
                },
            )

        self.assertEqual(
            self.service.store.list_artifacts(self.namespace, project_id),
            artifacts_before,
        )
        self.assertEqual(
            self.service.store.load_hub(self.namespace, project_id), hub_before
        )
        self.assertEqual(self._project_bytes(project_id), bytes_before)

    async def test_wrong_audit_session_cannot_recover_or_link_legacy_workbook(self) -> None:
        created = await self._ok(
            "create_project", {"project_name": "audit-legacy-read-only"}
        )
        project_id = created["project_id"]
        await self._ok(
            "get_agent_status", {"project_id": project_id, "full_self_test": True}
        )
        workbook = storyboard_to_xlsx(
            _compact_base(), {"issues": []}, assume_compact=True
        )
        record = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            workbook,
            file_name="legacy-unlinked.xlsx",
            artifact_type="storyboard",
            version="v1",
            metadata={
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            },
        )
        task = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            request_text="只读审计未链接canonical的旧工作簿",
            targets=[record["artifact_id"]],
            authoritative_source="legacy-unlinked.xlsx",
            latest_version="v1",
            target_boundary="全表",
            must_keep=[],
            must_remove=[],
            protected_scope=["all"],
            expected_outputs=["independent_audit_certificate"],
            window_key="audit-session-a",
            request_session_id="audit-session-a",
        )
        before = self._project_bytes(project_id)
        artifact_count = len(
            self.service.store.list_artifacts(self.namespace, project_id)
        )

        with self.assertRaisesRegex(ValueError, "window_session_mismatch"):
            self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=record["artifact_id"],
                audit_cycle_id=None,
                work_order_id=str(task.work_order_id),
                request_session_id="audit-session-b",
            )
        self.assertEqual(self._project_bytes(project_id), before)
        self.assertEqual(
            len(self.service.store.list_artifacts(self.namespace, project_id)),
            artifact_count,
        )

        # A correctly bound audit read is also non-materializing.  Stop at the
        # next read-only lookup so no handoff/certificate is intentionally
        # created, then prove the unlinked workbook metadata stayed byte-exact.
        with patch.object(
            self.service,
            "_matching_handoff",
            side_effect=ValueError("stop_after_pure_audit_read"),
        ):
            with self.assertRaisesRegex(ValueError, "stop_after_pure_audit_read"):
                self.service.audit_storyboard(
                    self.namespace,
                    project_id=project_id,
                    artifact_id=record["artifact_id"],
                    audit_cycle_id=None,
                    work_order_id=str(task.work_order_id),
                    request_session_id="audit-session-a",
                )
        self.assertEqual(self._project_bytes(project_id), before)
        current_record, _path = self.service.store.get_artifact(
            self.namespace, project_id, record["artifact_id"]
        )
        self.assertNotIn(
            "canonical_json_artifact_id", current_record.get("metadata", {})
        )


if __name__ == "__main__":
    unittest.main()
