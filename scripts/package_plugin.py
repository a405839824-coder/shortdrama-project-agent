#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


EXCLUDED_PARTS = {".venv", "data", "dist", "__pycache__", ".pytest_cache"}
EXCLUDED_FILES = {".app.json", ".env.cloud", ".railway.env"}


def package(plugin_root: Path, output: Path | None = None) -> Path:
    plugin_root = plugin_root.resolve()
    manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    output = output or plugin_root / "dist" / f"{manifest['name']}-{manifest['version']}.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(plugin_root.rglob("*")):
            relative = path.relative_to(plugin_root)
            if path.is_dir() or any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_FILES or path.suffix == ".pyc":
                continue
            archive.write(path, Path(manifest["name"]) / relative)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a clean local plugin ZIP")
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = package(args.plugin_root, args.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
