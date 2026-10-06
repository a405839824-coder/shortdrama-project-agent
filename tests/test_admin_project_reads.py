"""Real MCP reads across admin identities, with unchanged stored project bytes."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.external_storage import ExternalProjectStorage
from test_account_access import settings_for, token_for as subject_token
from test_member_connections import registry_for, token_for, write_atomic


class AdminProjectReadsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="admin-project-read-")
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(settings_for(self.temp.name), member_connection_access=True)
        self.registry = registry_for(self.settings)
        self.registry["members"][1]["role"] = "admin"
        self.save_registry()
        self.server, self.service = create_mcp(self.settings)
        self.owner_ns = self.settings.oauth_issuer + "|auth0|owner"
        self.admin_ns = self.settings.oauth_issuer + "|member:staff1"
        self.staff_ns = self.settings.oauth_issuer + "|member:staff2"
        self.owner_project = self.service.create_project(self.owner_ns, "owner storyboards").project_id
        self.admin_project = self.service.create_project(self.admin_ns, "other admin storyboards").project_id
        self.staff_project = self.service.create_project(self.staff_ns, "private producer project").project_id
        self.artifact = self.service.store.save_artifact_bytes(
            self.owner_ns, self.owner_project, "原始分镜 EP36-001".encode(),
            file_name="storyboard.txt", artifact_type="source:storyboard", version="v1")

    def save_registry(self):
        write_atomic(self.settings.data_dir / "member_connections.json", self.registry)

    async def call(self, name, args, client="staff_client_1"):
        with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings, client)):
            result = await self.server.call_tool(name, args)
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    def tree(self, namespace, project_id):
        root = self.service.store.project_dir(namespace, project_id)
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    async def test_both_admins_list_each_other_and_staff_stays_private(self):
        for client in ("staff_client_1", "owner_private_client"):
            data = (await self.call("get_agent_status", {}, client))["data"]
            self.assertEqual({p["project_id"] for p in data["projects"]},
                             {self.owner_project, self.admin_project})
            self.assertEqual(data["project_count"], 2)
            self.assertEqual(data["own_project_count"], 1)
            self.assertEqual(sum(p["project_access"]["shared"] for p in data["projects"]), 1)
        staff = (await self.call("get_agent_status", {}, "staff_client_2"))["data"]
        self.assertEqual([p["project_id"] for p in staff["projects"]], [self.staff_project])

    async def test_shared_details_files_versions_and_download_are_read_only(self):
        before = self.tree(self.owner_ns, self.owner_project)
        state = (await self.call("get_agent_status", {"project_id": self.owner_project}))["data"]
        self.assertEqual(state["account_access"]["member_id"], "staff1")
        self.assertEqual(state["project_access"]["owner_member_id"], "owner")
        self.assertFalse(state["execution_available"])
        self.assertEqual(state["execution_mode"], "shared_read_only")
        self.assertIn(self.artifact["artifact_id"], [a["artifact_id"] for a in state["artifact_versions"]])
        args = {"project_id": self.owner_project, "artifact_id": self.artifact["artifact_id"]}
        for bulk in (True, False):
            read = await self.call("read_project_artifact", {**args, "automatic_bulk": bulk})
            self.assertIn("原始分镜 EP36-001", json.dumps(read["data"]["content"], ensure_ascii=False))
            token = read["download_url"].split("/files/", 1)[1].split("/", 1)[0]
            record, stream, _ = self.service.store.open_verified_download(token)
            with stream:
                self.assertEqual(stream.read(), "原始分镜 EP36-001".encode())
            self.assertEqual(record["version"], "v1")
        cached = await self.call("read_project_artifact", {**args, "if_none_match": self.artifact["fingerprint"]})
        self.assertEqual(cached["status"], "not_modified")
        self.assertTrue(cached["data"]["project_access"]["read_only"])
        self.assertEqual(before, self.tree(self.owner_ns, self.owner_project))
        with closing(self.server.access.connect()) as db:
            actors = {row[0] for row in db.execute("SELECT DISTINCT subject FROM access_audit")}
        self.assertEqual(actors, {"member:staff1"})

    async def test_issue_ledger_and_narrative_history_are_from_the_owner(self):
        narrative = self.service._save_narrative_record(
            self.owner_ns, self.owner_project, "attempt", outcome="failed", reason="historical failure")
        before = self.tree(self.owner_ns, self.owner_project)
        issues = await self.call("query_project_issues", {"project_id": self.owner_project, "include_history": True})
        self.assertEqual(issues["data"]["project_access"]["owner_id"], "member:owner")
        history = await self.call("read_narrative_record", {"project_id": self.owner_project})
        self.assertEqual(history["data"]["records"][0]["record_id"], narrative["record_id"])
        detail = await self.call("read_narrative_record", {"project_id": self.owner_project,
                                                            "record_id": narrative["record_id"]})
        self.assertIn("historical failure", detail["data"]["content"])
        self.assertEqual(before, self.tree(self.owner_ns, self.owner_project))

    async def test_cross_admin_writes_and_full_self_test_still_fail(self):
        before = self.tree(self.owner_ns, self.owner_project)
        cases = [
            ("get_agent_status", {"project_id": self.owner_project, "full_self_test": True}),
            ("record_narrative_attempt", {"project_id": self.owner_project, "work_order_id": "WO-other",
                                          "outcome": "failed", "reason": "must not write"}),
            ("migrate_project_state", {"project_id": self.owner_project, "storage_action": "export_snapshot"}),
        ]
        for name, args in cases:
            with self.subTest(tool=name), self.assertRaises((ToolError, AssertionError)):
                await self.call(name, args)
        self.assertEqual(before, self.tree(self.owner_ns, self.owner_project))
        self.assertFalse(self.service.store.project_exists(self.admin_ns, self.owner_project))

    async def test_producer_and_forged_owner_cannot_read_shared_artifacts(self):
        args = {"project_id": self.owner_project, "artifact_id": self.artifact["artifact_id"]}
        for client, extra in (("staff_client_2", {}), ("staff_client_2", {"owner_id": "member:owner"}),
                              ("staff_client_1", {"owner_id": "member:staff2"}),
                              ("staff_client_1", {"owner_id": self.owner_ns})):
            with self.subTest(client=client, extra=extra), self.assertRaises((ToolError, AssertionError)):
                await self.call("read_project_artifact", {**args, **extra}, client)

    async def test_new_admin_is_included_then_demotion_or_disable_removes_access(self):
        self.registry["members"][2]["role"] = "admin"
        self.save_registry()
        listed = (await self.call("get_agent_status", {}))["data"]["projects"]
        self.assertIn(self.staff_project, [p["project_id"] for p in listed])
        self.registry["members"][2]["enabled"] = False
        self.save_registry()
        listed = (await self.call("get_agent_status", {}))["data"]["projects"]
        self.assertNotIn(self.staff_project, [p["project_id"] for p in listed])
        self.registry["members"][1]["role"] = "producer"
        self.save_registry()
        listed = (await self.call("get_agent_status", {}))["data"]["projects"]
        self.assertEqual([p["project_id"] for p in listed], [self.admin_project])
        with self.assertRaises((ToolError, AssertionError)):
            await self.call("read_project_artifact", {"project_id": self.owner_project,
                                                      "artifact_id": self.artifact["artifact_id"]})

    async def test_colliding_project_ids_require_explicit_authorized_owner(self):
        hub = self.service.store.load_hub(self.owner_ns, self.owner_project)
        ledger = self.service.store.load_ledger(self.owner_ns, self.owner_project)
        hub["project_name"] = "separate local project with identical ID"
        self.service.store.create_project(self.admin_ns, hub, ledger)
        with self.assertRaisesRegex(ToolError, "ambiguous_project_id"):
            await self.call("get_agent_status", {"project_id": self.owner_project})
        read = await self.call("get_agent_status", {"project_id": self.owner_project, "owner_id": "member:owner"})
        self.assertTrue(read["data"]["project_access"]["shared"])
        read = await self.call("get_agent_status", {"project_id": self.owner_project, "owner_id": "member:staff1"})
        self.assertFalse(read["data"]["project_access"]["shared"])

    async def test_legacy_subject_admins_share_without_opening_producer_namespaces(self):
        (self.settings.data_dir / "member_connections.json").unlink()
        server, service = create_mcp(replace(self.settings, member_connection_access=False))
        with patch("shortdrama_mcp.auth.get_access_token", return_value=subject_token(self.settings, "auth0|owner")):
            actor = server.access.principal()
            server.access.manage(actor, "set", "auth0|other", "admin")
        with patch("shortdrama_mcp.auth.get_access_token", return_value=subject_token(self.settings, "auth0|other")):
            result = await server.call_tool("read_project_artifact", {
                "project_id": self.owner_project, "artifact_id": self.artifact["artifact_id"]})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["data"]["project_access"]["owner_id"], "auth0|owner")

    async def test_shared_library_pointers_are_listed_and_require_owner_mount(self):
        with patch.dict(os.environ, {"SHORTDRAMA_PROJECT_STORAGE_MODE": "library",
                                     "SHORTDRAMA_RUNTIME_DATA_DIR": str(Path(self.temp.name) / "runtime")}):
            server, service = create_mcp(self.settings)
            external = ExternalProjectStorage(service, self.settings)
            external._write_registry(self.owner_ns, "SBP-library", {
                "generation": 1, "status": "verified", "bundle_library_file_id": "test-only-library-file",
                "library_path": "test.zip", "bundle_sha256": "a" * 64, "tree_sha256": "b" * 64,
                "file_count": 2, "expanded_size_bytes": 100})
            with patch("shortdrama_mcp.auth.get_access_token", return_value=token_for(self.settings)):
                result = await server.call_tool("get_agent_status", {})
                self.assertIn("SBP-library", [p["project_id"] for p in result.structured_content["data"]["projects"]])
                result = await server.call_tool("get_agent_status", {"project_id": "SBP-library"})
            self.assertEqual(result.structured_content["status"], "mount_required")
            self.assertTrue(result.structured_content["data"]["project_access"]["read_only"])
            self.assertFalse(service.store.project_exists(self.owner_ns, "SBP-library"))


if __name__ == "__main__":
    unittest.main()
