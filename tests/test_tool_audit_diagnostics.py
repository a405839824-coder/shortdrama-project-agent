from __future__ import annotations

import json
import ast
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from shortdrama_mcp.access import AccessControlledMCPServer, AccessStore, Principal
from shortdrama_mcp.config import Settings
from shortdrama_mcp.tool_audit import _literal_prefix


def settings_for(directory):
    return Settings(data_dir=Path(directory), public_base_url="http://localhost:8000",
                    host="127.0.0.1", port=8000, deployment_mode="local", auth_mode="none",
                    oauth_issuer=None, oauth_audience=None, oauth_jwks_url=None, oauth_scopes=(),
                    allow_local_file_urls=True, max_file_bytes=1024)


class AuditSchemaMigrationTests(unittest.TestCase):
    def test_static_code_inventory_handles_concatenation_without_evaluating_user_values(self):
        samples = {
            "'safe_error: ' + private_value": "safe_error: ",
            "('safe_error: ' + private_value) + another_call()": "safe_error: ",
            "f'safe_error: {private_value}'": "safe_error: ",
            "private_value + ': safe_error'": None,
            "f'{private_value}: safe_error'": None,
            "read_secret()": None,
        }
        for expression, expected in samples.items():
            with self.subTest(expression=expression):
                self.assertEqual(_literal_prefix(ast.parse(expression, mode="eval").body), expected)

    def test_old_database_is_migrated_idempotently_without_rewriting_events(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "account_access.sqlite3"
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute("CREATE TABLE access_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                           "occurred_at TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, "
                           "role TEXT NOT NULL, tool TEXT NOT NULL, project_id TEXT, "
                           "outcome TEXT NOT NULL, reason TEXT NOT NULL)")
                db.execute("INSERT INTO access_audit VALUES (1,'before','local','local-single-user',"
                           "'admin','audit_storyboard',NULL,'error','')")
            store = AccessStore(settings_for(directory))
            AccessStore(settings_for(directory))
            rows = store.manage(store.principal(), "audit")["events"]
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["id"], rows[0]["occurred_at"], rows[0]["reason"]), (1, "before", ""))
            self.assertIsNone(rows[0]["trace_id"])
            self.assertIsNone(rows[0]["error_code"])
            store.audit(store.principal(), "manual", None, "success")
            self.assertEqual(len(store.manage(store.principal(), "audit")["events"]), 2)


class DispatchAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = AccessControlledMCPServer(name="audit-tests")
        self.server.access = AccessStore(settings_for(self.temp.name))
        self.server.storyboard_service = SimpleNamespace(
            _ruleset_fingerprint="a" * 64, _runtime_fingerprint="b" * 64)

        @self.server.tool(name="audit_storyboard")
        async def tool() -> str:
            # Assemble the synthetic credential so the source package scanner
            # does not mistake this negative-test fixture for a real secret.
            raise ValueError("canonical_projection_migration_required: secret-text https://download/?token=" + "secret")

        self.args = {"project_id": "SBP-1234567890ab", "work_order_id": "WO-1234567890abcdef",
                     "artifact_id": "ART-abcdef1234567890"}

    def events(self):
        return list(reversed(self.server.access.manage(self.server.access.principal(), "audit")["events"]))

    async def test_sdk_exception_retains_known_code_and_original_cause_without_message(self):
        with self.assertRaises(Exception) as caught:
            await self.server.call_tool("audit_storyboard", {})
        self.assertIn("secret-text", str(caught.exception))  # outward exception is unchanged
        rows = self.events()
        self.assertEqual([r["outcome"] for r in rows], ["started", "error"])
        self.assertEqual(rows[0]["trace_id"], rows[1]["trace_id"])
        self.assertTrue(rows[1]["trace_id"].startswith("ACC-"))
        self.assertEqual(rows[1]["error_code"], "canonical_projection_migration_required")
        self.assertEqual(rows[1]["error_type"], "ValueError")
        self.assertEqual(rows[1]["stage"], "tool_execution")
        self.assertNotIn("secret", json.dumps(rows))
        self.assertNotIn("https://", json.dumps(rows))

    async def test_unknown_exception_never_logs_payload_arguments_or_secrets(self):
        exc = RuntimeError("private_dialogue: sk-secret OAuth Bearer-secret signature-secret")
        args = self.args | {"content": "private_dialogue", "file": {"download_url": "https://secret"},
                            "token": "sk-secret", "signature": "signature-secret"}
        with patch.object(MCPServer, "call_tool", new=AsyncMock(side_effect=exc)):
            with self.assertRaises(RuntimeError) as caught:
                await self.server.call_tool("audit_storyboard", args)
        self.assertIs(caught.exception, exc)
        row = self.events()[-1]
        self.assertEqual(row["error_code"], "unexpected_tool_exception")
        self.assertEqual(row["error_type"], "RuntimeError")
        self.assertEqual(row["project_id"], self.args["project_id"])
        self.assertEqual(row["work_order_id"], self.args["work_order_id"])
        self.assertEqual(row["artifact_id"], self.args["artifact_id"])
        self.assertEqual(row["rules_fingerprint"], "a" * 64)
        self.assertEqual(row["runtime_fingerprint"], "b" * 64)
        for secret in ("private_dialogue", "sk-secret", "Bearer-secret", "signature-secret", "https://secret"):
            self.assertNotIn(secret, json.dumps(self.events()))

    async def test_actual_concatenated_coverage_error_retains_its_code(self):
        from shortdrama_mcp.audit_coverage import validate_coverage
        with self.assertRaisesRegex(ValueError, "audit_coverage_required") as raised:
            validate_coverage({"shots": [{"镜头编号": "EP01-001"}]}, {}, {})
        with patch.object(MCPServer, "call_tool", new=AsyncMock(side_effect=raised.exception)):
            with self.assertRaises(ValueError):
                await self.server.call_tool("audit_storyboard", self.args)
        self.assertEqual(self.events()[-1]["error_code"], "audit_coverage_required")

    async def test_is_error_result_records_code_and_preserves_result(self):
        result = CallToolResult(is_error=True, content=[TextContent(type="text", text=
            "Error executing tool audit_storyboard: canonical_projection_migration_required: private-script")])
        with patch.object(MCPServer, "call_tool", new=AsyncMock(return_value=result)):
            returned = await self.server.call_tool("audit_storyboard", self.args)
        self.assertIs(returned, result)
        row = self.events()[-1]
        self.assertEqual(row["outcome"], "error")
        self.assertEqual(row["stage"], "tool_result")
        self.assertEqual(row["error_code"], "canonical_projection_migration_required")
        self.assertIsNone(row["business_status"])
        self.assertNotIn("private-script", json.dumps(self.events()))

    async def test_business_failure_is_distinct_from_transport_success(self):
        result = CallToolResult(content=[], structured_content={
            "status": "fail_current", "summary": "private-script", "trace_id": "TRC-0123456789abcdef",
            "data": {"download_url": "https://secret", "signature": "secret"}})
        with patch.object(MCPServer, "call_tool", new=AsyncMock(return_value=result)):
            self.assertIs(await self.server.call_tool("audit_storyboard", self.args), result)
        row = self.events()[-1]
        self.assertEqual(row["outcome"], "success")
        self.assertEqual(row["business_status"], "fail_current")
        self.assertEqual(row["response_trace_id"], "TRC-0123456789abcdef")
        self.assertNotEqual(row["trace_id"], row["response_trace_id"])
        self.assertEqual(json.loads(row["reason"])["tool_outcome"], "success")
        self.assertNotIn("secret", json.dumps(self.events()))
        self.assertNotIn("private-script", json.dumps(self.events()))

    async def test_created_ids_are_taken_only_from_typed_response_fields(self):
        result = CallToolResult(content=[], structured_content={"status": "created", **self.args})
        with patch.object(MCPServer, "call_tool", new=AsyncMock(return_value=result)):
            await self.server.call_tool("audit_storyboard", {})
        self.assertIsNone(self.events()[0]["project_id"])
        for field, value in self.args.items():
            self.assertEqual(self.events()[-1][field], value)

    async def test_untrusted_id_status_and_trace_values_do_not_become_log_fields(self):
        result = CallToolResult(content=[], structured_content={
            "status": "private_status_token", "trace_id": "https://secret-trace",
            "project_id": "https://secret-project", "artifact_id": "sk-secret-artifact"})
        args = {"project_id": "https://secret-arg", "work_order_id": "sk-secret-work-order",
                "resume_work_order_id": "WO-abcdef1234567890"}
        with patch.object(MCPServer, "call_tool", new=AsyncMock(return_value=result)):
            await self.server.call_tool("audit_storyboard", args)
        row = self.events()[-1]
        for field in ("project_id", "work_order_id", "artifact_id", "business_status", "response_trace_id"):
            self.assertIsNone(row[field])
        self.assertNotIn("secret", json.dumps(self.events()))
        self.assertNotIn("private_status", json.dumps(self.events()))

    async def test_permission_denial_does_not_dispatch_and_records_stage(self):
        actor = Principal("local", "local-single-user", "producer", enabled=False)
        with patch.object(self.server.access, "principal", return_value=actor), \
                patch.object(MCPServer, "call_tool", new=AsyncMock()) as dispatch:
            result = await self.server.call_tool("audit_storyboard", self.args)
        self.assertTrue(result.is_error)
        dispatch.assert_not_awaited()
        row = self.events()[-1]
        self.assertEqual(row["outcome"], "denied")
        self.assertEqual(row["stage"], "authorization")
        self.assertEqual(row["error_code"], "account_disabled")

    async def test_sink_failure_does_not_replace_exception_or_successful_result(self):
        exc = ValueError("original-private-error")
        with patch.object(MCPServer, "call_tool", new=AsyncMock(side_effect=exc)), \
                patch.object(self.server.access, "audit", side_effect=[None, OSError("sink-secret")]), \
                self.assertLogs("shortdrama_mcp.access", level="ERROR") as logs:
            with self.assertRaises(ValueError) as caught:
                await self.server.call_tool("audit_storyboard", self.args)
        self.assertIs(caught.exception, exc)
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertNotIn("private", " ".join(logs.output))
        result = CallToolResult(content=[], structured_content={"status": "ok"})
        with patch.object(MCPServer, "call_tool", new=AsyncMock(return_value=result)), \
                patch.object(self.server.access, "audit", side_effect=[None, OSError("sink-secret")]), \
                self.assertLogs("shortdrama_mcp.access", level="ERROR"):
            self.assertIs(await self.server.call_tool("audit_storyboard", self.args), result)

    async def test_start_log_failure_blocks_dispatch(self):
        with patch.object(self.server.access, "audit", side_effect=OSError("sink unavailable")), \
                patch.object(MCPServer, "call_tool", new=AsyncMock()) as dispatch:
            with self.assertRaises(OSError):
                await self.server.call_tool("audit_storyboard", self.args)
        dispatch.assert_not_awaited()

    async def test_unprintable_exception_is_not_replaced_by_diagnostics(self):
        class UnprintableError(Exception):
            def __str__(self):
                raise ValueError("do not expose or replace original error")

        exc = UnprintableError()
        with patch.object(MCPServer, "call_tool", new=AsyncMock(side_effect=exc)):
            with self.assertRaises(UnprintableError) as caught:
                await self.server.call_tool("audit_storyboard", self.args)
        self.assertIs(caught.exception, exc)
        self.assertEqual(self.events()[-1]["error_code"], "unexpected_tool_exception")


if __name__ == "__main__":
    unittest.main()
