from __future__ import annotations

import copy
import os
import tempfile
import unittest
from typing import Any

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings
from shortdrama_mcp.service import _episode_range_from_target

from test_v040_architecture import three_shot_storyboard


class EpisodeTargetParserTests(unittest.TestCase):
    def test_required_episode_target_forms(self) -> None:
        self.assertEqual(_episode_range_from_target("EP06"), {6})
        self.assertEqual(_episode_range_from_target("第06集"), {6})
        self.assertEqual(_episode_range_from_target("第06-07集"), {6, 7})
        self.assertEqual(_episode_range_from_target("episode:EP01"), {1})


class ProjectReuseAndFullScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v040-reuse-")
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
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def rejection(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = await self.server.call_tool(name, arguments)
        except Exception as exc:
            return str(exc)
        return "\n".join(str(item) for item in result.content)

    async def begin(self, project_id: str, target: str) -> str:
        response = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": "storyboard_change",
                "mode": "execute",
                "authorization": "explicit_execute",
                "request_text": "只修改授权分集",
                "targets": [target],
                "authoritative_source": "锁定源文",
                "latest_version": "current",
                "target_boundary": target,
                "must_keep": ["授权范围外镜头"],
                "must_remove": [],
                "protected_scope": ["其余分集"],
                "expected_outputs": ["正式分镜"],
                "window_key": f"scope-{target}",
            },
        )
        return str(response["work_order_id"])

    async def prepare_base(self) -> tuple[str, dict[str, Any], dict[str, Any]]:
        created = await self.call("create_project", {"project_name": "全量分集范围保护"})
        project_id = str(created["project_id"])
        await self.call(
            "get_agent_status", {"project_id": project_id, "full_self_test": True}
        )
        base = three_shot_storyboard()
        episodes = (5, 6, 8)
        for index, episode in enumerate(episodes):
            base["shots"][index]["episode_id"] = f"EP{episode:02d}"
            base["shots"][index]["场景连续性ID"] = f"EP{episode:02d}-SC01"
        create = await self.call(
            "begin_project_task",
            {
                "project_id": project_id,
                "operation": "storyboard_create",
                "mode": "execute",
                "authorization": "explicit_execute",
                "request_text": "建立范围保护基线",
                "targets": ["EP05-EP08"],
                "authoritative_source": "锁定源文",
                "latest_version": "current",
                "target_boundary": "EP05-EP08",
                "must_keep": [],
                "must_remove": [],
                "protected_scope": [],
                "expected_outputs": ["正式分镜"],
                "window_key": "base",
            },
        )
        committed = await self.call(
            "commit_storyboard",
            {
                "project_id": project_id,
                "work_order_id": create["work_order_id"],
                "storyboard": base,
                "file_name": "范围保护基线.xlsx",
            },
        )
        self.assertEqual(committed["status"], "pass")
        return project_id, base, committed

    async def test_full_commit_rejects_outside_delete_modify_and_add(self) -> None:
        project_id, base, committed = await self.prepare_base()
        artifact_count = len(self.service.store.list_artifacts(self.namespace, project_id))

        cases: list[tuple[str, dict[str, Any]]] = []
        deleted = copy.deepcopy(base)
        del deleted["shots"][0]
        cases.append(("EP06", deleted))

        modified = copy.deepcopy(base)
        modified["shots"][0]["画面内容"] = (
            "[0.0–0.8s] EP05人物停步；[0.8–3.3s] 她越权说："
            "【女主】这是未经授权的新台词。口型与发声一致；"
            "[3.3–4.0s] 她收住尾音并保持站位。"
        )
        modified["shots"][0]["音色"] = "克制低声，重音清楚"
        cases.append(("第06集", modified))

        added = copy.deepcopy(base)
        extra = copy.deepcopy(added["shots"][-1])
        extra.pop("shot_uid", None)
        extra.pop("scene_uid", None)
        extra["镜头编号"] = "S004"
        extra["场景连续性ID"] = "EP08-SC02"
        added["shots"].append(extra)
        cases.append(("第06-07集", added))

        for target, candidate in cases:
            with self.subTest(target=target):
                work_order_id = await self.begin(project_id, target)
                message = await self.rejection(
                    "commit_storyboard",
                    {
                        "project_id": project_id,
                        "work_order_id": work_order_id,
                        "storyboard": candidate,
                        "file_name": f"越权-{target}.xlsx",
                        "source_artifact_id": committed["artifact_id"],
                    },
                )
                self.assertIn("full_commit_protected_scope_violation", message)
                self.assertEqual(
                    len(self.service.store.list_artifacts(self.namespace, project_id)),
                    artifact_count,
                )

    async def test_status_exposes_reusable_source_storyboard_and_handoff_ids(self) -> None:
        project_id, _base, committed = await self.prepare_base()
        source = self.service.store.save_artifact_bytes(
            self.namespace,
            project_id,
            b"locked source",
            file_name="source.txt",
            artifact_type="source_file",
            version="v1",
        )
        with self.service.store.mutate_project(self.namespace, project_id) as (hub, _ledger):
            entry = {
                "artifact_id": source["artifact_id"],
                "source_role": "novel",
                "file_name": source["file_name"],
                "version": "v1",
                "fingerprint": source["fingerprint"],
                "imported_at": source["created_at"],
            }
            hub["project_state"]["authoritative_sources"].append(entry)
            hub["project_state"]["latest_artifacts"]["source:novel"] = entry

        status = await self.call("get_agent_status", {"project_id": project_id})
        self.assertEqual(
            status["data"]["authoritative_sources"][0]["artifact_id"],
            source["artifact_id"],
        )
        self.assertEqual(
            status["data"]["latest_storyboard"]["artifact_id"],
            committed["artifact_id"],
        )
        handoffs = status["data"]["pending_handoffs"]
        self.assertTrue(handoffs)
        self.assertEqual(handoffs[0]["storyboard_artifact_id"], committed["artifact_id"])
        self.assertTrue(handoffs[0]["handoff_id"])
        self.assertTrue(handoffs[0]["stage_trace_id"])
