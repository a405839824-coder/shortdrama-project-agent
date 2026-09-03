from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.5.17"
EXPECTED_TOOL_NAMES = {
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


async def inspect_bundled_server() -> dict:
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    entry = config["mcpServers"]["shortdrama-project-agent"]
    parameters = StdioServerParameters(
        command=entry["command"],
        args=entry.get("args", []),
        cwd=PLUGIN_ROOT / entry.get("cwd", "."),
        env={**os.environ, **entry.get("env", {})},
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            status = await session.call_tool("get_agent_status", {"full_self_test": False})
            tool_names = [tool.name for tool in tools.tools]
            return {
                "server": init.server_info.model_dump(by_alias=True),
                "tool_count": len(tools.tools),
                "tool_names": tool_names,
                "status_call_is_error": bool(status.is_error),
            }


def main() -> int:
    inspection = asyncio.run(inspect_bundled_server())
    tool_names = inspection["tool_names"]
    passed = (
        inspection["server"].get("version") == EXPECTED_VERSION
        and inspection["tool_count"] == len(EXPECTED_TOOL_NAMES)
        and len(tool_names) == len(set(tool_names))
        and set(tool_names) == EXPECTED_TOOL_NAMES
        and not inspection["status_call_is_error"]
    )
    print(json.dumps({"status": "pass" if passed else "fail", "inspection": inspection}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
