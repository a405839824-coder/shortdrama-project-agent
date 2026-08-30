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

    def test_source_lock_without_session_fails_before_work_order_creation(self) -> None:
        with self.assertRaisesRegex(ValueError, "openai/session metadata"):
            self._begin_source_lock(None)
        hub = self.service.store.load_hub(self.namespace, self.project_id)
        self.assertEqual(hub["work_orders"], [])

    def test_source_lock_reports_bound_session(self) -> None:
        response = self._begin_source_lock("openai:conversation-a")
        self.assertEqual(response.data["session_binding_status"], "bound")
        self.assertEqual(response.data["server_actor_role"], "production")


if __name__ == "__main__":
    unittest.main()
