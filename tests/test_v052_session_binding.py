from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

from shortdrama_mcp.app import _window_session_id, create_mcp
from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.config import Settings


class V052WindowSessionExtractionTests(unittest.TestCase):
    def test_chatgpt_openai_session_meta_is_primary(self) -> None:
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                meta={"openai/session": "conversation-a"},
                session=SimpleNamespace(
                    _connection=SimpleNamespace(session_id="transport-b")
                ),
            )
        )
        self.assertEqual(_window_session_id(ctx), "openai:conversation-a")

    def test_stateful_transport_is_a_non_chatgpt_fallback(self) -> None:
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                meta={},
                session=SimpleNamespace(
                    _connection=SimpleNamespace(session_id="transport-b")
                ),
            )
        )
        self.assertEqual(_window_session_id(ctx), "mcp:transport-b")


class V052CloudWorkOrderBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-v051-session-")
        os.environ["SHORTDRAMA_DATA_DIR"] = self.temp.name
        os.environ["PUBLIC_BASE_URL"] = "https://example.invalid"
        os.environ["SHORTDRAMA_DEPLOYMENT_MODE"] = "local"
        os.environ["SHORTDRAMA_AUTH_MODE"] = "none"
        self.settings = replace(Settings.from_env(), deployment_mode="cloud")
        _server, self.service = create_mcp(self.settings)
        self.namespace = request_namespace(self.settings)
        created = self.service.create_project(self.namespace, "v0.5.2会话绑定测试")
        self.project_id = str(created.project_id)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _begin_source_lock(self, session_id: str | None):
        return self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="source_lock",
            mode="execute",
            authorization="explicit_execute",
            request_text="登记剧情母本",
            targets=["v4.2剧情母本"],
            authoritative_source="v4.2剧情母本",
            latest_version="v4.2",
            target_boundary="全项目",
            must_keep=["剧情真源"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["authoritative source"],
            window_key="production",
            request_session_id=session_id,
        )

    def _begin_capability_gap(self, session_id: str | None):
        return self.service.begin_task(
            self.namespace,
            project_id=self.project_id,
            operation="capability_gap",
            mode="plan_only",
            authorization="plan_only",
            request_text="登记当前无法真实完成的能力",
            targets=["全项目"],
            authoritative_source="当前用户请求",
            latest_version="current",
            target_boundary="全项目",
            must_keep=["真实能力边界"],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["能力缺口提案"],
            window_key="capability-gap",
            request_session_id=session_id,
        )

    def _record_gap(self, work_order_id: str, session_id: str | None):
        return self.service.record_capability_gap(
            self.namespace,
            project_id=self.project_id,
            work_order_id=work_order_id,
            request="调用当前不存在的外部依赖",
            missing_dependency="external-tool",
            risk="若模拟执行会形成虚假交付",
            safe_fallback="停止该步骤并保留现有项目状态",
            proposed_implementation="增加受控连接器与权限门禁",
            required_tests=["错误会话拒绝", "依赖缺失时不写正式交付"],
            request_session_id=session_id,
        )

    def test_source_lock_without_session_fails_before_work_order_creation(self) -> None:
        with self.assertRaisesRegex(ValueError, "openai/session metadata"):
            self._begin_source_lock(None)
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        self.assertEqual(hub["work_orders"], [])

    def test_source_lock_reports_bound_session(self) -> None:
        response = self._begin_source_lock("openai:conversation-a")
        self.assertEqual(response.data["session_binding_status"], "bound")
        self.assertEqual(response.data["server_actor_role"], "production")

    def test_capability_gap_requires_exact_work_order_and_cloud_session(self) -> None:
        gap_order = self._begin_capability_gap("openai:capability-owner")
        work_order_id = str(gap_order.work_order_id)
        with self.assertRaisesRegex(ValueError, "window_session_mismatch"):
            self._record_gap(work_order_id, "openai:another-window")
        with self.assertRaisesRegex(ValueError, "window_session_required"):
            self._record_gap(work_order_id, None)

        wrong_order = self._begin_source_lock("openai:capability-owner")
        with self.assertRaisesRegex(ValueError, "capability_gap_authority_required"):
            self._record_gap(str(wrong_order.work_order_id), "openai:capability-owner")
        hub_before = self.service.store.load_hub(self.namespace, self.project_id)
        self.assertEqual(hub_before["capability_gaps"], [])

        recorded = self._record_gap(
            work_order_id,
            "openai:capability-owner",
        )
        self.assertEqual(recorded.status, "recorded")
        self.assertEqual(recorded.work_order_id, work_order_id)
        gap = self.service.store.load_hub(self.namespace, self.project_id)[
            "capability_gaps"
        ][0]
        self.assertEqual(gap["work_order_id"], work_order_id)

    def test_capability_gap_keeps_local_no_session_compatibility(self) -> None:
        local_settings = replace(self.settings, deployment_mode="local")
        _server, local_service = create_mcp(local_settings)
        created = local_service.create_project(
            self.namespace,
            "v0.5.5本地能力缺口兼容",
        )
        project_id = str(created.project_id)
        order = local_service.begin_task(
            self.namespace,
            project_id=project_id,
            operation="capability_gap",
            mode="plan_only",
            authorization="plan_only",
            request_text="本地直连记录能力缺口",
            targets=["全项目"],
            authoritative_source="当前请求",
            latest_version="current",
            target_boundary="全项目",
            must_keep=[],
            must_remove=[],
            protected_scope=[],
            expected_outputs=["能力缺口提案"],
            window_key="local-gap",
            request_session_id=None,
        )
        recorded = local_service.record_capability_gap(
            self.namespace,
            project_id=project_id,
            work_order_id=str(order.work_order_id),
            request="本地缺少外部依赖",
            missing_dependency="external-tool",
            risk="不能真实执行",
            safe_fallback="停止外部步骤",
            proposed_implementation="安装受控连接器",
            required_tests=["本地回归"],
            request_session_id=None,
        )
        self.assertEqual(recorded.status, "recorded")


if __name__ == "__main__":
    unittest.main()
