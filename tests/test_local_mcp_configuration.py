"""Exercise local launch paths, safe source packaging, and a missing environment."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from configure_local_mcp import configure
from package_plugin import PackagingError, _validate_profile


class LocalMcpConfigurationTests(unittest.TestCase):
    def test_configuration_and_portable_package_boundary(self):
        with tempfile.TemporaryDirectory(prefix="storyboard install ") as folder:
            root = Path(folder)
            (root / "server").mkdir()
            (root / "server/main.py").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Create .venv"):
                configure(root)
            self.assertFalse((root / ".mcp.json").exists())
            executable = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.touch()
            manifest = {"name": "shortdrama-project-agent", "mcpServers": "./.mcp.json"}
            path = configure(root)
            entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["shortdrama-project-agent"]
            self.assertEqual(entry["command"], executable.as_posix())
            self.assertEqual(entry["args"], [(root / "server/main.py").as_posix(), "--stdio"])
            with self.assertRaisesRegex(PackagingError, "local absolute paths"):
                _validate_profile(root, manifest, "source")
            configure(root, portable=True)
            _validate_profile(root, manifest, "source")
            restored = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["shortdrama-project-agent"]
            self.assertEqual(restored["command"], "./.venv/bin/python")
            self.assertEqual(restored["cwd"], ".")


if __name__ == "__main__":
    unittest.main()
