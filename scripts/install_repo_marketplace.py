#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_NAME = "shortdrama-project-agent"


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


def install(repo_root: Path, plugin_root: Path) -> Path:
    repo_root = repo_root.resolve()
    plugin_root = plugin_root.resolve()
    if repo_root not in plugin_root.parents:
        raise ValueError("插件目录必须位于 repo root 内，避免 marketplace 引用仓库外路径")
    manifest = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest.is_file():
        raise ValueError(f"找不到插件清单：{manifest}")
    relative = plugin_root.relative_to(repo_root).as_posix()
    if relative.startswith("."):
        raise ValueError("插件相对路径无效")
    marketplace_path = repo_root / ".agents" / "plugins" / "marketplace.json"
    if marketplace_path.exists():
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        if not isinstance(marketplace, dict) or not isinstance(marketplace.get("plugins", []), list):
            raise ValueError("现有 marketplace.json 结构无效，已停止且未覆盖")
    else:
        marketplace = {
            "name": "local-project-plugins",
            "interface": {"displayName": "本地项目插件"},
            "plugins": [],
        }
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./{relative}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    plugins = marketplace.setdefault("plugins", [])
    plugins[:] = [item for item in plugins if item.get("name") != PLUGIN_NAME]
    plugins.append(entry)
    write_json_atomic(marketplace_path, marketplace)
    return marketplace_path


def main() -> int:
    default_plugin = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Add this plugin to a repo-local ChatGPT marketplace")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, default=default_plugin)
    args = parser.parse_args()
    path = install(args.repo_root, args.plugin_root)
    print(f"已更新：{path}")
    print("请重启 ChatGPT 桌面端，再从 Plugins Directory 安装该插件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
