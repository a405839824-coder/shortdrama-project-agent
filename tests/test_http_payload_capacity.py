"""Exercise the real HTTP entry point, including streaming and authority failures."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from starlette.testclient import TestClient
from main import MAX_MCP_REQUEST_BODY_BYTES, create_http_app
from shortdrama_mcp.app import create_mcp
from test_account_access import settings_for


class HttpPayloadCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-http-capacity-")
        self.addCleanup(self.temp.cleanup)
        self.settings = replace(settings_for(self.temp.name), deployment_mode="local",
                                auth_mode="none", public_base_url="http://127.0.0.1:8765")
        self.headers = {"Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json"}

    def client(self, settings=None):
        settings = settings or self.settings
        server, _ = create_mcp(settings)
        return TestClient(create_http_app(server, settings), base_url=settings.public_base_url)

    def large_ping(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping",
                          "params": {"_meta": {"fixture": "镜" * (2 * 1024 * 1024)}}},
                         ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(raw), 4 * 1024 * 1024)
        self.assertLess(len(raw), MAX_MCP_REQUEST_BODY_BYTES)
        return raw

    def test_unicode_request_above_sdk_default_reaches_mcp(self):
        with self.client() as client:
            result = client.post("/mcp", content=self.large_ping(), headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text[:1000])
            self.assertEqual(result.json()["result"], {})

    def test_advertised_size_over_ceiling_is_rejected(self):
        with self.client() as client:
            result = client.post("/mcp", content=b"{}", headers={**self.headers,
                                 "Content-Length": str(MAX_MCP_REQUEST_BODY_BYTES + 1)})
            self.assertEqual(result.status_code, 413)

    def test_streamed_size_over_ceiling_is_rejected(self):
        def chunks():
            for _ in range(33):
                yield b"x" * (1024 * 1024)
        with self.client() as client:
            result = client.post("/mcp", content=chunks(), headers=self.headers)
            self.assertEqual(result.status_code, 413)

    def test_large_body_does_not_bypass_oauth(self):
        cloud = replace(settings_for(self.temp.name), host="storyboard.example.test")
        with self.client(cloud) as client:
            result = client.post("/mcp", content=self.large_ping(), headers=self.headers)
            self.assertEqual(result.status_code, 401)

    def test_large_commit_still_requires_project_and_work_order(self):
        with self.client() as client:
            root = Path(self.temp.name)
            def project_files():
                return {p.relative_to(root): p.read_bytes() for p in root.rglob('*')
                        if p.is_file() and {'projects', 'exports'} & set(p.relative_to(root).parts)}
            before = project_files()
            body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "commit_storyboard", "arguments": {
                    "project_id": "SBP-missing", "work_order_id": "WO-missing",
                    "storyboard": {"untrusted": "镜" * (2 * 1024 * 1024)}}}}
            result = client.post("/mcp", content=json.dumps(body, ensure_ascii=False).encode(),
                                 headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text[:1000])
            self.assertTrue(result.json()["result"]["isError"])
            after = project_files()
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
