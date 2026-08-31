from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


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
            return {
                "server": init.server_info.model_dump(by_alias=True),
                "tool_count": len(tools.tools),
                "status_call_is_error": bool(status.is_error),
            }


def main() -> int:
    inspection = asyncio.run(inspect_bundled_server())
    passed = inspection["tool_count"] == 18 and not inspection["status_call_is_error"]
    print(json.dumps({"status": "pass" if passed else "fail", "inspection": inspection}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
