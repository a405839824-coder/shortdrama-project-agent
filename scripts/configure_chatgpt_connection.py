#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


CONNECTION_RE = re.compile(r"^plugin_asdk_app[A-Za-z0-9_-]{8,}$")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(plugin_root: Path, connection_id: str, *, keep_bundled_mcp: bool = False) -> tuple[Path, Path]:
    if not CONNECTION_RE.fullmatch(connection_id):
        raise ValueError("connection ID 必须是 ChatGPT 返回的 plugin_asdk_app... 技术 ID")
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise ValueError(f"找不到插件清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apps"] = "./.app.json"
    if not keep_bundled_mcp:
        # The registered ChatGPT connection becomes authoritative. Keeping the
        # local stdio entry enabled would expose duplicate tools in some hosts.
        manifest.pop("mcpServers", None)
    app_path = plugin_root / ".app.json"
    app = {
        "apps": {
            "shortdrama-project-agent": {
                "id": connection_id,
                "required": True,
            }
        }
    }
    write_json_atomic(app_path, app)
    write_json_atomic(manifest_path, manifest)
    return app_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Link the plugin to a ChatGPT MCP connection")
    parser.add_argument("connection_id", help="ChatGPT technical ID beginning with plugin_asdk_app")
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="plugin root; defaults to this script's parent plugin",
    )
    parser.add_argument(
        "--keep-bundled-mcp",
        action="store_true",
        help="keep the local stdio MCP entry in addition to the ChatGPT connection",
    )
    args = parser.parse_args()
    app_path, manifest_path = configure(
        args.plugin_root.resolve(),
        args.connection_id.strip(),
        keep_bundled_mcp=args.keep_bundled_mcp,
    )
    print(f"已写入：{app_path}")
    print(f"已更新：{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
