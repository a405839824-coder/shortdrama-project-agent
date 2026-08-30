from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings


def _result_text(result: Any) -> str:
    values: list[str] = []
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        values.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        values.append(str(text if text is not None else item))
    return "\n".join(values)


class NoApiPerformanceDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v053-diagnostic-")
        os.environ["SHORTDRAMA_DATA_DIR"] = self.temp.name
        os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8765"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        self.settings = Settings.from_env()
        self.server, self.service = create_mcp(self.settings)
        self.namespace = request_namespace(self.settings)
        project = await self.call("create_project", {"project_name": "无API计时测试"})
        self.project_id = str(project["project_id"])
        task = await self.call(
            "begin_project_task",
            {
                "project_id": self.project_id,
                "operation": "storyboard_create",
                "mode": "execute",
                "authorization": "explicit_execute",
                "request_text": "固定样本性能诊断",
                "targets": ["EP06-EP07"],
                "authoritative_source": "已锁定样本",
                "latest_version": "v1",
                "target_boundary": "EP06-EP07",
                "must_keep": ["27字段与逐镜光源信息"],
                "must_remove": ["后台Seedance批次"],
                "protected_scope": ["EP01-EP05", "EP08-EP80"],
                "expected_outputs": ["正式分镜"],
                "window_key": "diagnostic-test",
            },
        )
        self.work_order_id = str(task["work_order_id"])

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.server.call_tool(name, arguments)
        self.assertFalse(result.is_error, _result_text(result))
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def rejection_text(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self.server.call_tool(name, arguments)
        except Exception as exc:
            return str(exc)
        return _result_text(result)

    async def start(self) -> dict[str, Any]:
        return await self.call(
            "mark_performance_stage",
            {
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "action": "start",
                "stage": "task_started",
                "reasoning_mode": "extra_high",
                "window_type": "fresh",
                "route": "full_rebuild",
                "episode_count": 2,
                "expected_shot_count": 59,
            },
        )

    async def test_five_stage_report_separates_plugin_time_without_changing_quality(self) -> None:
        started = await self.start()
        run_id = str(started["data"]["diagnostic_run_id"])
        for stage, shots, chars in (
            ("inputs_ready", 0, 0),
            ("plan_ready", 59, 0),
            ("canonical_ready", 59, 366_541),
        ):
            await asyncio.sleep(0.003)
            checkpoint = await self.call(
                "mark_performance_stage",
                {
                    "project_id": self.project_id,
                    "work_order_id": self.work_order_id,
                    "diagnostic_run_id": run_id,
                    "action": "mark",
                    "stage": stage,
                    "observed_shot_count": shots,
                    "observed_character_count": chars,
                },
            )
            self.assertEqual(checkpoint["status"], "diagnostic_checkpointed")

        telemetry_id = "TEL-diagnostic0001"
        self.service.store.save_project_record(
            self.namespace,
            self.project_id,
            kind="telemetry",
            record_id=telemetry_id,
            value={
                "telemetry_id": telemetry_id,
                "trace_id": telemetry_id,
                "operation": "commit_storyboard",
                "duration_ms": 2.0,
                "measurement_scope": "server_only",
                "window_role": "production",
                "cache_hit": False,
            },
        )
        await asyncio.sleep(0.003)
        finished = await self.call(
            "mark_performance_stage",
            {
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "diagnostic_run_id": run_id,
                "action": "finish",
                "stage": "delivery_ready",
                "observed_shot_count": 59,
                "observed_character_count": 366_541,
                "related_trace_ids": [telemetry_id],
            },
        )
        self.assertEqual(finished["status"], "diagnostic_complete")
        report = finished["data"]["report"]
        self.assertEqual(report["measurement_scope"], "tool_bracketed_chat")
        self.assertFalse(report["exact_end_to_end"])
        self.assertEqual(report["resolved_plugin_trace_count"], 1)
        self.assertEqual(report["resolved_plugin_server_ms"], 2.0)
        self.assertEqual(
            set(report["stage_gaps_ms"]),
            {
                "source_and_context",
                "storyboard_planning",
                "canonical_authorship",
                "commit_export_and_delivery",
            },
        )
        quality = report["quality_contract_unchanged"]
        self.assertEqual(quality["canonical_atomic_field_count"], 27)
        self.assertEqual(
            quality["lighting_fields"],
            ["光影", "光源来源", "光源方向", "色温倾向"],
        )
        self.assertFalse(quality["background_seedance_generation"])
        self.assertEqual(quality["seedance_batches_generated"], 0)

        replay = await self.call(
            "mark_performance_stage",
            {
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "diagnostic_run_id": run_id,
                "action": "finish",
                "stage": "delivery_ready",
                "observed_shot_count": 59,
                "observed_character_count": 366_541,
                "related_trace_ids": [telemetry_id],
            },
        )
        self.assertTrue(replay["data"]["idempotent_replay"])
        self.assertEqual(
            replay["data"]["report"]["total_tool_bracketed_ms"],
            report["total_tool_bracketed_ms"],
        )

        persisted = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="diagnostics",
            record_id=run_id,
        )
        serialized = json.dumps(persisted, ensure_ascii=False)
        self.assertNotIn("固定样本性能诊断", serialized)
        self.assertNotIn("prompt_text", serialized)
        self.assertEqual(persisted["model_authored_derived_chars"], 0)
        self.assertEqual(persisted["legacy_generation_calls"], 0)
        self.assertEqual(persisted["seedance_batches_generated"], 0)

    async def test_out_of_order_is_rejected_and_extra_content_is_not_retained(self) -> None:
        started = await self.start()
        run_id = str(started["data"]["diagnostic_run_id"])
        rejection = await self.rejection_text(
            "mark_performance_stage",
            {
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "diagnostic_run_id": run_id,
                "action": "mark",
                "stage": "plan_ready",
            },
        )
        self.assertIn("diagnostic_stage_out_of_order", rejection)

        injection = await self.call(
            "mark_performance_stage",
            {
                "project_id": self.project_id,
                "work_order_id": self.work_order_id,
                "diagnostic_run_id": run_id,
                "action": "mark",
                "stage": "inputs_ready",
                "prompt_text": "不得进入诊断记录的分镜正文",
            },
        )
        self.assertEqual(injection["status"], "diagnostic_checkpointed")
        persisted = self.service.store.get_project_record(
            self.namespace,
            self.project_id,
            kind="diagnostics",
            record_id=run_id,
        )
        serialized = json.dumps(persisted, ensure_ascii=False)
        self.assertNotIn("prompt_text", serialized)
        self.assertNotIn("不得进入诊断记录的分镜正文", serialized)

    async def test_diagnostic_run_is_bound_to_starting_session(self) -> None:
        started = self.service.mark_performance_stage(
            self.namespace,
            project_id=self.project_id,
            work_order_id=self.work_order_id,
            action="start",
            stage="task_started",
            reasoning_mode="medium",
            window_type="continued",
            route="full_rebuild",
            request_session_id="session-alpha",
        )
        run_id = str(started.data["diagnostic_run_id"])
        with self.assertRaisesRegex(ValueError, "diagnostic_window_session_mismatch"):
            self.service.mark_performance_stage(
                self.namespace,
                project_id=self.project_id,
                work_order_id=self.work_order_id,
                diagnostic_run_id=run_id,
                action="mark",
                stage="inputs_ready",
                request_session_id="session-beta",
            )


if __name__ == "__main__":
    unittest.main()
