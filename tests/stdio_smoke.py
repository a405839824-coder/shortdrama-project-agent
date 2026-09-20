from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from transport_smoke_support import PLUGIN_ROOT, inspect_session, inspection_passed


async def inspect_bundled_server() -> dict:
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    entry = config["mcpServers"]["shortdrama-project-agent"]
    command = entry["command"]
    # The bundled descriptor targets POSIX. Use the test interpreter for
    # its default Python command on Windows, including isolated release trees.
    if os.name == "nt" and command == "./.venv/bin/python":
        command = sys.executable
    parameters = StdioServerParameters(
        command=command,
        args=entry.get("args", []),
        cwd=PLUGIN_ROOT / entry.get("cwd", "."),
        env={**os.environ, **entry.get("env", {})},
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            return await inspect_session(session)


def main() -> int:
    inspection = asyncio.run(inspect_bundled_server())
    passed = inspection_passed(inspection)
    print(json.dumps({"status": "pass" if passed else "fail", "inspection": inspection}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
