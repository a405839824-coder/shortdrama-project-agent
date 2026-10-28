"""Configure a prepared checkout for a local MCP client, or restore portable paths."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from atomic_io import write_json_atomic


def configure(root: Path, *, portable: bool = False) -> Path:
    root = root.resolve()
    server = root / "server" / "main.py"
    if not server.is_file():
        raise ValueError("Expected a source checkout containing server/main.py")
    if portable:
        command, args, cwd = "./.venv/bin/python", ["./server/main.py", "--stdio"], "."
    else:
        executable = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not executable.is_file():
            raise ValueError("Create .venv and install requirements.txt before configuring MCP")
        # Keep the venv path: resolving a POSIX symlink would select system Python.
        command, args, cwd = executable.as_posix(), [server.as_posix(), "--stdio"], root.as_posix()
    target = root / ".mcp.json"
    write_json_atomic(target, {
        "mcpServers": {
            "shortdrama-project-agent": {
                "cwd": cwd, "command": command, "args": args,
                "env": {"SHORTDRAMA_DEPLOYMENT_MODE": "local"},
            }
        }
    })
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--portable", action="store_true", help="Restore paths suitable for distributing source")
    args = parser.parse_args()
    print(configure(args.plugin_root, portable=args.portable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
