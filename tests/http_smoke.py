from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.5.39"
EXPECTED_TOOL_NAMES = {
    "preflight_narrative_plan",
    "review_narrative_artifact",
    "record_narrative_attempt",
    "read_narrative_record",
    "manage_user_access",
    "get_agent_status",
    "migrate_project_state",
    "recover_storyboard_handoff",
    "query_project_issues",
    "mark_performance_stage",
    "create_project",
    "begin_project_task",
    "import_source_file",
    "read_project_artifact",
    "plan_change_request",
    "commit_project_artifact",
    "preflight_storyboard_draft",
    "commit_storyboard",
    "commit_storyboard_delta",
    "commit_storyboard_patch",
    "audit_storyboard",
    "complete_task_validation",
    "record_capability_gap",
    "signoff_task",
}


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


async def inspect_endpoint(url: str) -> dict:
    # Local MCP verification must not inherit a workstation SOCKS/HTTP proxy.
    async with httpx2.AsyncClient(trust_env=False) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                status = await session.call_tool("get_agent_status", {"full_self_test": False})
                return {
                    "server": init.server_info.model_dump(by_alias=True),
                    "tool_count": len(tools.tools),
                    "tool_names": [tool.name for tool in tools.tools],
                    "status_call_is_error": bool(status.is_error),
                }


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="shortdrama-http-test-") as data_dir:
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
                "SHORTDRAMA_DATA_DIR": data_dir,
                "SHORTDRAMA_DEPLOYMENT_MODE": "local",
                "SHORTDRAMA_AUTH_MODE": "none",
            }
        )
        process = subprocess.Popen(
            [sys.executable, str(PLUGIN_ROOT / "server" / "main.py"), "--http"],
            cwd=PLUGIN_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            health = None
            for _ in range(80):
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    break
                except Exception:
                    time.sleep(0.1)
            if health is None:
                stdout, stderr = process.communicate(timeout=2)
                raise RuntimeError(f"server did not become healthy\nstdout={stdout}\nstderr={stderr}")
            inspection = asyncio.run(inspect_endpoint(f"http://127.0.0.1:{port}/mcp"))
            result = {"status": "pass", "health": health, "inspection": inspection}
            tool_names = inspection["tool_names"]
            if (
                health.get("version") != EXPECTED_VERSION
                or inspection["server"].get("version") != EXPECTED_VERSION
                or inspection["tool_count"] != len(EXPECTED_TOOL_NAMES)
                or len(tool_names) != len(set(tool_names))
                or set(tool_names) != EXPECTED_TOOL_NAMES
                or inspection["status_call_is_error"]
            ):
                result["status"] = "fail"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "pass" else 1
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
