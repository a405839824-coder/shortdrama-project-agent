from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from typing import Any

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings

try:  # unittest discovery adds tests/ directly to sys.path.
    from test_v040_architecture import three_shot_storyboard
except ModuleNotFoundError:  # Direct module execution uses tests as a namespace package.
    from tests.test_v040_architecture import three_shot_storyboard

from semantic_audit_helpers import inject_semantic_audit


LONG_SOURCE_CHARACTERS = 366_541
BULK_RESPONSE_CHARACTERS = 16_000
MAX_EXPECTED_BULK_CALLS = 23


def _long_source() -> str:
    seed = "〔效率基准〕甲乙丙丁戊己庚辛壬癸。\n"
    repeats = (LONG_SOURCE_CHARACTERS + len(seed) - 1) // len(seed)
    value = (seed * repeats)[:LONG_SOURCE_CHARACTERS]
    assert len(value) == LONG_SOURCE_CHARACTERS
    return value


class CompactMasterPerformanceContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-compact-performance-")
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
        arguments = inject_semantic_audit(self.service, self.settings, name, arguments)
        result = await self.server.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.content)
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def create_project(self, name: str, *, certify_runtime: bool = False) -> str:
        created = await self.call("create_project", {"project_name": name})
        project_id = str(created["project_id"])
        if certify_runtime:
            status = await self.call(
                "get_agent_status",
                {"project_id": project_id, "full_self_test": True},
            )
            self.assertTrue(status["data"]["runtime_test_certificate_valid"])
        return project_id

    async def begin_storyboard_task(
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
                "request_text": "0.5.0紧凑母版性能与等质契约测试",
                "targets": targets,
                "authoritative_source": "锁定测试源文",
                "latest_version": "current",
                "target_boundary": ",".join(targets),
                "must_keep": ["剧情、对白、认知、声画和未影响镜头语义"],
                "must_remove": [],
                "protected_scope": protected_scope or [],
                "expected_outputs": ["正式分镜", "正式生产母版", "生产审计交接"],
                "window_key": f"performance-{operation}",
            },
        )
        return str(result["work_order_id"])

    async def test_automatic_bulk_reconstructs_exact_366541_characters_in_at_most_23_calls(
        self,
    ) -> None:
        project_id = await self.create_project("366541字符自动批量读取")
        source = _long_source()
        record = self.service.store.save_artifact_json(
            self.namespace,
            project_id,
            {"kind": "text", "format": "txt", "text": source},
            file_name="366541字符原文_提取内容.json",
            artifact_type="source_extraction",
            version="v1",
            metadata={"source_role": "novel"},
        )

        cursor = 0
        pieces: list[str] = []
        call_count = 0
        while cursor < len(source):
            response = await self.call(
                "read_project_artifact",
                {
                    "project_id": project_id,
                    "artifact_id": record["artifact_id"],
                    "offset": cursor,
                },
            )
            call_count += 1
            self.assertLessEqual(call_count, MAX_EXPECTED_BULK_CALLS)
            self.assertEqual(response["data"]["read_mode"], "automatic_bulk")
            content = response["data"]["content"]
            self.assertEqual(content["max_chars"], BULK_RESPONSE_CHARACTERS)
            self.assertEqual(len(content["ranges"]), 1)

            current = content["ranges"][0]
            self.assertEqual(current["unit"], "characters")
            self.assertEqual(current["offset"], cursor, "automatic ranges must not overlap or leave a gap")
            piece = str(current["content"])
            next_offset = int(current["next_offset"])
            self.assertGreater(next_offset, cursor)
            self.assertLessEqual(len(piece), BULK_RESPONSE_CHARACTERS)
            self.assertEqual(content["returned_chars"], len(piece))
            self.assertEqual(piece, source[cursor:next_offset])
            self.assertEqual(bool(current["complete"]), next_offset == len(source))
            pieces.append(piece)
            cursor = next_offset

        reconstructed = "".join(pieces)
        self.assertEqual(cursor, LONG_SOURCE_CHARACTERS)
        self.assertEqual(call_count, MAX_EXPECTED_BULK_CALLS)
        self.assertEqual(len(reconstructed), len(source))
        self.assertEqual(
            hashlib.sha256(reconstructed.encode("utf-8")).hexdigest(),
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
        )

    async def test_delta_trace_is_server_only_with_real_payload_bytes_and_unmeasured_model_time(
        self,
    ) -> None:
        project_id = await self.create_project("增量性能trace", certify_runtime=True)
        storyboard = three_shot_storyboard()
        create_order = await self.begin_storyboard_task(
            project_id,
            operation="storyboard_create",
            targets=["EP01-EP03"],
        )
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": create_order,
                "storyboard": storyboard,
                "file_name": "三集性能基线.xlsx",
            },
        )
        self.assertEqual(committed["status"], "production_preflight_pass")
        audited = await self.call(
            "audit_storyboard",
            {"project_id": project_id, "artifact_id": committed["artifact_id"]},
        )
        self.assertEqual(audited["status"], "pass")

        identity_read = await self.call(
            "read_project_artifact",
            {
                "project_id": project_id,
                "artifact_id": committed["artifact_id"],
                "section": "canonical_storyboard",
            },
        )
        identities = identity_read["data"]["shot_identity_manifest"]["shot_uids"]
        self.assertEqual(len(identities), 3)

        change_order = await self.begin_storyboard_task(
            project_id,
            operation="storyboard_change",
            targets=["EP02"],
            protected_scope=["episode:EP01", "episode:EP03"],
        )
        replacement_shot = copy.deepcopy(storyboard["shots"][1])
        replacement_shot["画面内容"] = (
            "【0.0—0.8秒】EP02女主在原位停步；【0.8—3.3秒】她确认桌上信件，"
            "视线从门框移向桌面信件，眼球先完成轻微焦点调整，头部随后轻微跟转；"
            "视线落稳前自然短眨一次，重新睁眼后上眼睑略微下压，"
            "表情、视线与呼吸连续，本镜无对白；【3.3—4.0秒】她保持视线并收束动作。"
        )
        performance = copy.deepcopy(
            storyboard["prevention_evidence"]["performance_chains"][1]
        )
        performance["body_or_breath"] = "停步确认后呼吸轻收，再恢复稳定"
        result = await self.call(
            "commit_storyboard_delta",
            {
                "project_id": project_id,
                "work_order_id": change_order,
                "base_artifact_id": committed["artifact_id"],
                "expected_base_canonical_sha256": committed["data"]["canonical_sha256"],
                "expected_base_raw_fingerprint": committed["data"]["artifact"]["fingerprint"],
                "scope_target": "EP02",
                "affected_shot_uids": [identities[1]["shot_uid"]],
                "left_boundary_shot_uid": identities[0]["shot_uid"],
                "right_boundary_shot_uid": identities[2]["shot_uid"],
                "replacement_shots": [replacement_shot],
                "evidence_updates": {"performance_chains": [performance]},
                "file_name": "EP02增量性能版.xlsx",
            },
        )
        self.assertEqual(result["status"], "production_preflight_pass")
        self.assertEqual(result["data"]["measurement_scope"], "server_only")
        self.assertIn("chatgpt_model_generation", result["data"]["unmeasured_stages"])

        telemetry = result["data"]["telemetry"]
        self.assertEqual(result["trace_id"], telemetry["trace_id"])
        self.assertEqual(telemetry["measurement_scope"], "server_only")
        self.assertIn("chatgpt_model_generation", telemetry["unmeasured_stages"])
        self.assertGreater(telemetry["input_payload_bytes"], 0)
        self.assertGreater(telemetry["output_payload_bytes"], 0)
        self.assertGreater(telemetry["output_payload_bytes"], telemetry["input_payload_bytes"])
        self.assertEqual(telemetry["affected_shot_count"], 1)
        self.assertEqual(telemetry["preserved_semantic_count"], 2)
        self.assertEqual(telemetry["model_authored_derived_chars"], 0)
        self.assertEqual(telemetry["legacy_generation_calls"], 0)
        self.assertEqual(telemetry["seedance_batches_generated"], 0)
        self.assertEqual(telemetry["duplicate_generated_bytes"], 0)
        self.assertTrue(any(stage["payload_bytes"] > 0 for stage in telemetry["stages"]))

        persisted = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="telemetry",
            record_id=telemetry["trace_id"],
        )
        self.assertEqual(persisted["measurement_scope"], "server_only")
        self.assertEqual(persisted["input_payload_bytes"], telemetry["input_payload_bytes"])
        self.assertEqual(persisted["output_payload_bytes"], telemetry["output_payload_bytes"])
        self.assertNotIn("model_generation_duration_ms", persisted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
