from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jsonschema import Draft202012Validator

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings
import shortdrama_mcp.service as service_module


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "shortdrama-storyboard"

import sys

sys.path.insert(0, str(SKILL_ROOT / "scripts"))
from run_golden_tests import inject_pg1_fixture_evidence  # noqa: E402


def valid_storyboard() -> dict[str, Any]:
    suite = json.loads((SKILL_ROOT / "references" / "golden_tests.json").read_text(encoding="utf-8"))
    value = copy.deepcopy(suite["fixtures"]["valid_base"])
    inject_pg1_fixture_evidence(value)
    return value


def _result_text(result: Any) -> str:
    """Return both structured and error tool output for rejection assertions."""

    values: list[str] = []
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        values.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            values.append(str(text))
        else:
            values.append(str(item))
    return "\n".join(values)


class EfficiencyContractTests(unittest.IsolatedAsyncioTestCase):
    """No-loss latency contracts for the phase-1 and phase-2 runtime.

    These tests intentionally exercise public MCP behavior where possible.  The
    patch API tests are executable specifications for phase 2 and are expected
    to remain red until that tool is implemented.
    """

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-efficiency-test-")
        os.environ["SHORTDRAMA_DATA_DIR"] = self.temp.name
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8765"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        os.environ.pop("ALLOW_LOCAL_FILE_URLS", None)
        self.settings = Settings.from_env()
        self.server, self.service = create_mcp(self.settings)
        self.namespace = request_namespace(self.settings)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.server.call_tool(name, arguments)
        self.assertFalse(result.is_error, _result_text(result))
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def rejection_text(self, name: str, arguments: dict[str, Any]) -> str:
        """Normalize MCP ToolError and structured rejections for stable assertions."""

        try:
            result = await self.server.call_tool(name, arguments)
        except Exception as exc:  # MCPServer raises ToolError for tool-side ValueError.
            return str(exc)
        return _result_text(result)

    async def create_project(self, name: str) -> str:
        result = await self.call("create_project", {"project_name": name})
        self.assertEqual(result["status"], "created")
        project_id = str(result["project_id"])
        checked = await self.call(
            "get_agent_status", {"project_id": project_id, "full_self_test": True}
        )
        self.assertTrue(checked["data"]["runtime_test_certificate_valid"])
        return project_id

    async def begin_storyboard_task(
        self,
        project_id: str,
        *,
        operation: str = "storyboard_create",
        mode: str = "execute",
        protected_scope: list[str] | None = None,
        window_key: str = "efficiency-test",
    ) -> str:
        result = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": operation,
                "mode": mode,
                "authorization": "explicit_execute" if mode != "audit_only" else "read_only",
                "request_text": "效率优化等质回归测试",
                "targets": ["第一集"],
                "authoritative_source": "测试剧本",
                "latest_version": "v1",
                "target_boundary": "第一集",
                "must_keep": ["剧情、对白、人物认知与空间连续"],
                "must_remove": [],
                "protected_scope": protected_scope or [],
                "expected_outputs": ["正式分镜", "Seedance批次"],
                "window_key": window_key,
            },
        )
        return str(result["work_order_id"])

    def save_extracted_text(self, project_id: str, text: str, *, name: str = "长篇原文") -> dict[str, Any]:
        return self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"kind": "text", "format": "txt", "text": text},
            file_name=f"{name}_提取内容.json",
            artifact_type="source_extraction",
            version="v1",
            metadata={"source_role": "novel"},
        )

    async def commit_fixture(
        self,
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        *,
        file_name: str = "效率测试分镜.xlsx",
    ) -> dict[str, Any]:
        return await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "storyboard": storyboard,
                "file_name": file_name,
            },
        )

    async def test_bulk_text_ranges_are_lossless_and_non_overlapping(self) -> None:
        project_id = await self.create_project("批量读取无损测试")
        source = "".join(f"〔段落{i:04d}〕甲乙丙丁戊己庚辛壬癸。\n" for i in range(560))
        record = self.save_extracted_text(project_id, source)
        width = 4_000
        requested_ranges = [
            {
                "offset": offset,
                "limit": min(width, len(source) - offset),
                "unit": "characters",
            }
            for offset in range(0, len(source), width)
        ]

        result = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": record["artifact_id"],
                "bulk_ranges": requested_ranges,
                "unit": "characters",
                "max_chars": 16_000,
            },
        )
        content = result["data"]["content"]
        returned = content["ranges"]
        self.assertEqual(len(returned), len(requested_ranges))
        self.assertEqual(content["returned_chars"], len(source))

        cursor = 0
        pieces: list[str] = []
        for requested, actual in zip(requested_ranges, returned, strict=True):
            self.assertEqual(actual["unit"], "characters")
            self.assertEqual(actual["offset"], requested["offset"])
            self.assertEqual(actual["offset"], cursor, "bulk ranges must not overlap or leave a gap")
            piece = actual["content"]
            pieces.append(piece)
            self.assertEqual(piece, source[actual["offset"] : actual["next_offset"]])
            cursor = actual["next_offset"]
        self.assertEqual(cursor, len(source))
        self.assertTrue(returned[-1]["complete"])
        self.assertEqual("".join(pieces), source)

    async def test_read_if_none_match_returns_no_payload(self) -> None:
        project_id = await self.create_project("条件读取测试")
        record = self.save_extracted_text(project_id, "原文内容" * 1_200)
        first = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": record["artifact_id"],
                "offset": 0,
                "limit": 4_000,
                "unit": "characters",
            },
        )
        etag = first["data"]["etag"]
        self.assertEqual(etag, record["fingerprint"])

        with patch.object(
            self.service,
            "_load_extracted_or_payload",
            side_effect=AssertionError("matching if_none_match must not reload or parse the payload"),
        ):
            second = await self.call(
                "read_project_artifact",
                {
                    "project_id": project_id,
                    "artifact_id": record["artifact_id"],
                    "offset": 0,
                    "limit": 4_000,
                    "unit": "characters",
                    "if_none_match": etag,
                },
            )
        self.assertEqual(second["status"], "not_modified")
        self.assertTrue(second["data"]["not_modified"])
        self.assertEqual(second["data"]["etag"], etag)
        self.assertNotIn("content", second["data"])

    async def test_linked_canonical_json_is_preferred_over_xlsx_roundtrip(self) -> None:
        project_id = await self.create_project("Canonical优先测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())

        with patch(
            "shortdrama_mcp.service.extract_file",
            side_effect=AssertionError("linked canonical JSON must be loaded before parsing XLSX"),
        ):
            storyboard, record = self.service._storyboard_from_artifact(
                self.namespace,
                project_id,
                committed["artifact_id"],
            )

        self.assertEqual(record["artifact_id"], committed["artifact_id"])
        self.assertEqual(storyboard["shots"], valid_storyboard()["shots"])
        self.assertEqual(
            committed["data"]["canonical_sha256"],
            service_module.canonical_storyboard_sha256(storyboard),
        )

    async def test_production_preflight_and_independent_audit_have_separate_one_time_runs(self) -> None:
        project_id = await self.create_project("预检审计去重测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        original_validator = service_module.validate_storyboard.validate_project

        with patch.object(
            service_module.validate_storyboard,
            "validate_project",
            wraps=original_validator,
        ) as validator:
            committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
            self.assertEqual(validator.call_count, 1, "production preflight must validate once")

            first_audit = await self.call(
                "audit_storyboard",
                {"project_id": project_id, "artifact_id": committed["artifact_id"]},
            )
            self.assertEqual(first_audit["data"]["audit_class"], "independent_audit")
            self.assertFalse(first_audit["data"]["cache_hit"])
            self.assertEqual(validator.call_count, 2, "the first independent audit must make its own judgment")

            repeated_audit = await self.call(
                "audit_storyboard",
                {"project_id": project_id, "artifact_id": committed["artifact_id"]},
            )
            self.assertEqual(repeated_audit["data"]["audit_class"], "independent_audit")
            self.assertTrue(repeated_audit["data"]["cache_hit"])
            self.assertEqual(validator.call_count, 2, "same independent audit cache key must not validate twice")

    async def test_audit_cache_key_invalidates_on_every_quality_input(self) -> None:
        base = {
            "canonical_sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
            "boundary_hash": "c" * 64,
            "ruleset_version": "R10-rules-1",
            "validator_version": "validator-1",
            "audit_class": "independent_audit",
            "artifact_fingerprint": "1" * 64,
        }
        stable = service_module.build_audit_cache_key(**base)
        self.assertEqual(stable, service_module.build_audit_cache_key(**base))

        replacements = {
            "canonical_sha256": "d" * 64,
            "source_fingerprint": "e" * 64,
            "boundary_hash": "f" * 64,
            "ruleset_version": "R10-rules-2",
            "validator_version": "validator-2",
            "audit_class": "production_preflight",
            "artifact_fingerprint": "2" * 64,
        }
        invalidated = set()
        for component, changed_value in replacements.items():
            changed = dict(base)
            changed[component] = changed_value
            changed_key = service_module.build_audit_cache_key(**changed)
            self.assertNotEqual(stable, changed_key, f"{component} must invalidate the audit cache")
            invalidated.add(changed_key)
        self.assertEqual(len(invalidated), len(replacements))

    async def test_telemetry_never_contains_story_text_dialogue_url_or_subject(self) -> None:
        project_id = await self.create_project("遥测脱敏测试")
        sentinels = {
            "正文绝密标记-NEVER-LOG",
            "台词绝密标记-DIALOGUE-NEVER-LOG",
            "https://private.example.test/secret?token=NEVER-LOG",
            "auth0|private-user-subject-NEVER-LOG",
        }
        source = "｜".join(sorted(sentinels)) + "｜普通原文" * 400
        record = self.save_extracted_text(project_id, source)
        result = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": record["artifact_id"],
                "offset": 0,
                "limit": 4_000,
                "unit": "characters",
            },
        )
        telemetry = result["data"]["telemetry"]
        serialized = json.dumps(telemetry, ensure_ascii=False, sort_keys=True)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, serialized)

        forbidden_key_fragments = ("text", "content", "dialogue", "台词", "正文", "url", "subject")

        def check_keys(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = str(key).casefold()
                    self.assertFalse(
                        any(fragment in normalized for fragment in forbidden_key_fragments),
                        f"sensitive telemetry key is forbidden: {key}",
                    )
                    check_keys(item)
            elif isinstance(value, list):
                for item in value:
                    check_keys(item)

        check_keys(telemetry)
        self.assertIn("trace_id", telemetry)
        self.assertIn("duration_ms", telemetry)
        self.assertIn("stages", telemetry)

        # Commit/audit traces are persisted separately from the read response.
        # They must obey the same content-free contract even when sensitive
        # values participate in source locking and validation.
        work_order_id = await self.begin_storyboard_task(project_id, window_key="telemetry-persistence")
        storyboard = valid_storyboard()
        storyboard["source_lock"]["上集终点"] = source
        committed = await self.commit_fixture(project_id, work_order_id, storyboard)
        await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        persisted = self.service.store.list_project_records(
            self.namespace,
            project_id,
            kind="telemetry",
        )
        self.assertGreaterEqual(len(persisted), 2)
        for trace in persisted:
            trace_text = json.dumps(trace, ensure_ascii=False, sort_keys=True)
            for sentinel in sentinels:
                self.assertNotIn(sentinel, trace_text)
            check_keys(trace)

    async def test_artifacts_and_cache_records_are_isolated_by_project(self) -> None:
        project_a = await self.create_project("隔离项目A")
        project_b = await self.create_project("隔离项目B")
        record_a = self.save_extracted_text(project_a, "两个项目即使内容相同也不能越权读取")
        self.save_extracted_text(project_b, "两个项目即使内容相同也不能越权读取")

        cross_read = await self.rejection_text(
            "read_project_artifact",
            {
                "project_id": project_b,
                "artifact_id": record_a["artifact_id"],
                "if_none_match": record_a["fingerprint"],
            },
        )
        self.assertIn("unknown artifact_id", cross_read)

        work_order_a = await self.begin_storyboard_task(project_a, window_key="project-a")
        committed_a = await self.commit_fixture(project_a, work_order_a, valid_storyboard())
        cross_audit = await self.rejection_text(
            "audit_storyboard",
            {"project_id": project_b, "artifact_id": committed_a["artifact_id"]},
        )
        self.assertIn("unknown artifact_id", cross_audit)

    async def test_full_runtime_regression_certificate_is_reused_only_on_exact_fingerprints(self) -> None:
        project_id = await self.create_project("运行时回归证书测试")
        checked = await self.call(
            "get_agent_status",
            {"project_id": project_id, "full_self_test": True},
        )
        self.assertTrue(checked["data"]["runtime_test_certificate_valid"])
        certificate = checked["data"]["runtime_test_certificate"]
        self.assertEqual(certificate["certificate_type"], "runtime_full_regression")
        self.assertEqual(certificate["tests"]["golden_tests"]["test_pass_count"], 73)
        self.assertEqual(certificate["tests"]["public_scene_tests"]["test_pass_count"], 11)
        self.assertEqual(certificate["tests"]["agent_tests"]["test_pass_count"], 14)
        self.assertEqual(certificate["tests"]["project_agent_tests"]["test_pass_count"], 18)

        with patch.object(
            service_module.r10_bootstrap,
            "verify_bundled",
            side_effect=AssertionError("normal status must reuse the exact certificate, not rerun suites"),
        ):
            resumed = await self.call(
                "get_agent_status",
                {"project_id": project_id, "full_self_test": False},
            )
        self.assertTrue(resumed["data"]["runtime_test_certificate_valid"])
        self.assertEqual(
            resumed["data"]["runtime_test_certificate"]["certificate_id"],
            certificate["certificate_id"],
        )

    async def test_storyboard_signoff_requires_exact_clean_independent_audit(self) -> None:
        project_id = await self.create_project("独立终审签核门禁测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())

        blocked = await self.rejection_text(
            "signoff_task",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "decision": "approve",
                "signer": "测试签核人",
            },
        )
        self.assertIn("independent_audit_required", blocked)

        audited = await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        self.assertEqual(audited["status"], "pass")
        signed = await self.call(
            "signoff_task",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "decision": "approve",
                "signer": "测试签核人",
            },
        )
        self.assertEqual(signed["status"], "archived")
        self.assertEqual(
            signed["data"]["independent_audit_certificate_id"],
            audited["data"]["certificate"]["certificate_id"],
        )

    async def _prepare_patch_base(
        self,
        *,
        project_name: str,
        protected_scope: list[str] | None = None,
    ) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
        project_id = await self.create_project(project_name)
        create_work_order = await self.begin_storyboard_task(project_id, window_key=f"{project_name}-create")
        broken = valid_storyboard()
        broken["shots"][0]["光源来源"] = ""
        committed = await self.commit_fixture(project_id, create_work_order, broken, file_name="待修分镜.xlsx")
        self.assertEqual(committed["status"], "fail_current")
        audited = await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        repair_work_order = await self.begin_storyboard_task(
            project_id,
            operation="storyboard_repair",
            mode="audit_and_repair",
            protected_scope=protected_scope,
            window_key=f"{project_name}-repair",
        )
        return project_id, repair_work_order, committed, audited

    @staticmethod
    def _patch_arguments(
        project_id: str,
        work_order_id: str,
        committed: dict[str, Any],
        audited: dict[str, Any],
        *,
        expected_hash: str | None = None,
        field: str = "光源来源",
        value: str = "窗纸透入的日光",
    ) -> dict[str, Any]:
        certificate = audited["data"]["certificate"]
        return {
            "project_id": project_id,
            "work_order_id": work_order_id,
            "base_artifact_id": committed["artifact_id"],
            "expected_canonical_hash": expected_hash or committed["data"]["canonical_sha256"],
            "audit_certificate_id": certificate["certificate_id"],
            "operations": [
                {
                    "op": "replace_field",
                    "shot_id": "S001",
                    "field": field,
                    "value": value,
                    # Only the certificate row whose exact location is
                    # S001.光源来源 may authorize this field replacement.
                    "issue_ids": ["E-TEMPLATE-008"],
                    "reason": "修复独立审计确认的缺失光源，不改变剧情、认知或空间边界",
                }
            ],
            "impact_scope": ["S001"],
            "file_name": "待修分镜_补丁修复版.xlsx",
        }

    async def test_storyboard_patch_rejects_stale_canonical_hash(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁哈希冲突测试"
        )
        arguments = self._patch_arguments(
            project_id,
            work_order_id,
            committed,
            audited,
            expected_hash="0" * 64,
        )
        result = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("canonical_hash_conflict", result)

    async def test_storyboard_patch_cannot_modify_work_order_protected_scope(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁保护范围测试",
            protected_scope=["S001"],
        )
        arguments = self._patch_arguments(project_id, work_order_id, committed, audited)
        result = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("protected_scope_violation", result)

    async def test_storyboard_patch_rejects_invalid_audit_certificate(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁审计证书测试"
        )
        arguments = self._patch_arguments(project_id, work_order_id, committed, audited)
        arguments["audit_certificate_id"] = "CERT-does-not-exist"
        result = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("invalid_audit_certificate", result)

    async def test_storyboard_patch_rejects_structural_or_boundary_field(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁结构字段测试"
        )
        arguments = self._patch_arguments(
            project_id,
            work_order_id,
            committed,
            audited,
            field="场景连续性ID",
            value="SC99",
        )
        result = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("full_rebuild_required", result)

    async def test_storyboard_patch_runs_final_full_table_validation(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁全表终验测试"
        )
        arguments = self._patch_arguments(project_id, work_order_id, committed, audited)
        original_validator = service_module.validate_storyboard.validate_project
        with patch.object(
            service_module.validate_storyboard,
            "validate_project",
            wraps=original_validator,
        ) as validator:
            result = await self.call("commit_storyboard_patch", arguments)

        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["data"]["final_full_validation"])
        self.assertEqual(result["data"]["report"]["issues"], [])
        self.assertEqual(
            result["data"]["report"]["shot_count"],
            len(valid_storyboard()["shots"]),
        )
        self.assertGreaterEqual(validator.call_count, 1)
        validated_storyboard = validator.call_args.args[0]
        self.assertEqual(len(validated_storyboard["shots"]), len(valid_storyboard()["shots"]))
        self.assertEqual(validated_storyboard["shots"][0]["光源来源"], "窗纸透入的日光")

    async def test_repair_package_is_signed_consumed_and_bidirectionally_linked(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="不可变维修包闭环测试"
        )
        issued = audited["data"]["repair_package"]
        audit_to_repair = audited["data"]["audit_to_repair_handoff"]
        self.assertEqual(issued["status"], "issued")
        self.assertTrue(self.service.store.project_record_is_signed(issued))
        self.service._validate_schema_record(
            self.service._project_schema, "immutable_repair_package", issued
        )
        self.service._validate_schema_record(
            self.service._project_schema, "handoff_manifest", audit_to_repair
        )
        self.assertEqual(
            audit_to_repair["immutable_repair_package_id"],
            issued["repair_package_id"],
        )
        self.assertEqual(
            issued["audit_to_repair_handoff_id"], audit_to_repair["handoff_id"]
        )

        result = await self.call(
            "commit_storyboard_patch",
            self._patch_arguments(project_id, work_order_id, committed, audited),
        )
        consumed = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="repair_packages",
            record_id=issued["repair_package_id"],
        )
        repair_to_audit = result["data"]["handoff"]
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(consumed["successor_artifact_id"], result["artifact_id"])
        self.assertEqual(
            consumed["repair_to_audit_handoff_id"], repair_to_audit["handoff_id"]
        )
        self.assertEqual(
            repair_to_audit["immutable_repair_package_id"],
            consumed["repair_package_id"],
        )
        self.service._validate_schema_record(
            self.service._project_schema, "immutable_repair_package", consumed
        )
        self.service._validate_schema_record(
            self.service._project_schema, "handoff_manifest", repair_to_audit
        )
        hub = self.service.store.load_hub(self.namespace, project_id)
        Draft202012Validator(self.service._project_schema).validate(hub)
        hub_package = next(
            row
            for row in hub["repair_packages"]
            if row["repair_package_id"] == consumed["repair_package_id"]
        )
        self.assertEqual(hub_package["status"], "consumed")
        with self.assertRaisesRegex(ValueError, "repair_package_unavailable"):
            self.service.store.claim_repair_package(
                self.namespace,
                project_id,
                consumed["repair_package_id"],
                work_order_id=work_order_id,
            )

    async def test_storyboard_patch_rejects_tampered_repair_package(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="维修包篡改阻断测试"
        )
        package_id = audited["data"]["repair_package"]["repair_package_id"]
        package_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "repair_packages"
            / f"{package_id}.json"
        )
        tampered = self.service.store.read_json(package_path)
        tampered["authorized_scope"]["fields"] = ["任意字段"]
        self.service.store.atomic_write_json(package_path, tampered)
        rejection = await self.rejection_text(
            "commit_storyboard_patch",
            self._patch_arguments(project_id, work_order_id, committed, audited),
        )
        self.assertIn("repair_package_required", rejection)

    async def test_production_preflight_never_marks_storyboard_work_order_passed(self) -> None:
        project_id = await self.create_project("生产预检不得终审测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
        self.assertEqual(committed["status"], "pass")
        hub = self.service.store.load_hub(self.namespace, project_id)
        work_order = next(row for row in hub["work_orders"] if row["work_order_id"] == work_order_id)
        self.assertEqual(work_order["status"], "validating")
        self.assertTrue(work_order["awaiting_independent_audit"])
        self.assertFalse(committed["data"]["validation"]["final_verdict"])

    async def test_same_transport_session_cannot_self_sign_independent_audit(self) -> None:
        project_id = await self.create_project("同会话自签阻断测试")
        production = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_create",
            mode="execute",
            authorization="explicit_execute",
            request_text="生产",
            targets=["第一集"],
            authoritative_source="测试原文",
            latest_version="v1",
            target_boundary="第一集",
            must_keep=["全部剧情"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["正式分镜"],
            window_key="production-window",
            request_session_id="transport-session-shared",
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=str(production.work_order_id),
            storyboard=valid_storyboard(),
            file_name="同会话测试.xlsx",
            source_artifact_id=None,
            request_session_id="transport-session-shared",
        )
        audit = self.service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="storyboard_audit",
            mode="audit_only",
            authorization="read_only",
            request_text="独立审计",
            targets=[str(committed.artifact_id)],
            authoritative_source="handoff",
            latest_version="current",
            target_boundary="第一集",
            must_keep=["只读"],
            must_remove=[],
            protected_scope=["全部源内容"],
            expected_outputs=["audit certificate"],
            window_key="audit-window",
            request_session_id="transport-session-shared",
        )
        with self.assertRaisesRegex(ValueError, "independent_audit_separation_required"):
            self.service.audit_storyboard(
                self.namespace,
                project_id=project_id,
                artifact_id=str(committed.artifact_id),
                audit_cycle_id=None,
                work_order_id=str(audit.work_order_id),
                handoff_artifact_id=committed.data["handoff"]["handoff_id"],
                request_session_id="transport-session-shared",
            )

    async def test_storyboard_patch_blocks_shot_range_protected_scope(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁范围保护测试", protected_scope=["S001-S010"]
        )
        rejection = await self.rejection_text(
            "commit_storyboard_patch",
            self._patch_arguments(project_id, work_order_id, committed, audited),
        )
        self.assertIn("protected_scope_violation", rejection)

    async def test_storyboard_patch_rejects_seedance_topology_reference(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁拓扑字段测试"
        )
        arguments = self._patch_arguments(
            project_id,
            work_order_id,
            committed,
            audited,
            field="Seedance批次引用",
            value="BATCH-02",
        )
        rejection = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("full_rebuild_required", rejection)

    async def test_storyboard_patch_issue_must_match_exact_shot_and_field(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁精确证据测试"
        )
        arguments = self._patch_arguments(project_id, work_order_id, committed, audited)
        arguments["operations"][0]["issue_ids"] = ["E-LIGHT-001"]
        rejection = await self.rejection_text("commit_storyboard_patch", arguments)
        self.assertIn("patch_evidence_mismatch", rejection)

    async def test_xlsx_byte_tampering_blocks_signoff(self) -> None:
        project_id = await self.create_project("XLSX字节篡改签核阻断测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
        await self.call(
            "audit_storyboard", {"project_id": project_id, "artifact_id": committed["artifact_id"]}
        )
        _record, path = self.service.store.get_artifact(
            self.namespace, project_id, committed["artifact_id"]
        )
        path.write_bytes(path.read_bytes() + b"tampered")
        rejection = await self.rejection_text(
            "signoff_task",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "decision": "approve",
                "signer": "测试签核人",
            },
        )
        self.assertIn("artifact_raw_fingerprint_mismatch", rejection)

    async def test_certificate_report_tampering_blocks_signoff(self) -> None:
        project_id = await self.create_project("证书报告篡改签核阻断测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
        audited = await self.call(
            "audit_storyboard", {"project_id": project_id, "artifact_id": committed["artifact_id"]}
        )
        certificate_id = audited["data"]["certificate"]["certificate_id"]
        certificate_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "certificates"
            / f"{certificate_id}.json"
        )
        certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
        certificate["report"]["status"] = "tampered"
        self.service.store.atomic_write_json(certificate_path, certificate)
        rejection = await self.rejection_text(
            "signoff_task",
            {
                "project_id": project_id,
                "work_order_id": work_order_id,
                "decision": "approve",
                "signer": "测试签核人",
            },
        )
        self.assertIn("independent_audit_required", rejection)

    async def test_internal_storyboard_missing_handoff_blocks_audit(self) -> None:
        project_id = await self.create_project("内部产物缺交接阻断测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
        handoff_id = committed["data"]["handoff"]["handoff_id"]
        handoff_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "handoffs"
            / f"{handoff_id}.json"
        )
        handoff_path.unlink()
        rejection = await self.rejection_text(
            "audit_storyboard", {"project_id": project_id, "artifact_id": committed["artifact_id"]}
        )
        self.assertIn("handoff_required", rejection)

    async def test_patch_successor_inherits_original_source_fingerprint(self) -> None:
        project_id, work_order_id, committed, audited = await self._prepare_patch_base(
            project_name="补丁来源链继承测试"
        )
        result = await self.call(
            "commit_storyboard_patch",
            self._patch_arguments(project_id, work_order_id, committed, audited),
        )
        base, _ = self.service.store.get_artifact(
            self.namespace, project_id, committed["artifact_id"]
        )
        successor, _ = self.service.store.get_artifact(
            self.namespace, project_id, result["artifact_id"]
        )
        self.assertEqual(
            successor["metadata"]["source_fingerprint"],
            base["metadata"]["source_fingerprint"],
        )
        self.assertNotEqual(
            successor["metadata"]["source_fingerprint"],
            committed["data"]["canonical_sha256"],
        )

    async def test_lifecycle_schemas_and_persisted_records_resolve_all_refs(self) -> None:
        Draft202012Validator.check_schema(self.service._project_schema)
        Draft202012Validator.check_schema(self.service._ledger_schema)
        self.assertIn("sha256", self.service._ledger_schema["$defs"])
        project_id = await self.create_project("生命周期Schema闭环测试")
        work_order_id = await self.begin_storyboard_task(project_id)
        committed = await self.commit_fixture(project_id, work_order_id, valid_storyboard())
        audited = await self.call(
            "audit_storyboard", {"project_id": project_id, "artifact_id": committed["artifact_id"]}
        )
        handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=audited["data"]["certificate"]["handoff_id"],
        )
        certificate = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="certificates",
            record_id=audited["data"]["certificate"]["certificate_id"],
        )
        ledger = self.service.store.load_ledger(self.namespace, project_id)
        event = next(row for row in ledger["events"] if row["event_id"] == certificate["event_id"])
        trace = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="telemetry",
            record_id=certificate["stage_trace_id"],
        )
        self.service._validate_schema_record(self.service._project_schema, "handoff_manifest", handoff)
        self.service._validate_schema_record(
            self.service._ledger_schema, "independent_audit_certificate", certificate
        )
        self.service._validate_schema_record(self.service._ledger_schema, "audit_event", event)
        self.service._validate_schema_record(self.service._project_schema, "stage_timing_trace", trace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
