from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import package_plugin as package_module  # noqa: E402
from package_plugin import PackagingError, package  # noqa: E402


class SafePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="shortdrama-safe-package-")
        self.root = Path(self.temporary.name) / "shortdrama-project-agent"
        (self.root / ".codex-plugin").mkdir(parents=True)
        (self.root / "assets").mkdir()
        (self.root / "server").mkdir()
        (self.root / "skills" / "demo").mkdir(parents=True)
        self._write_json(
            self.root / ".codex-plugin" / "plugin.json",
            {
                "name": "shortdrama-project-agent",
                "version": "0.4.0",
                "description": "test plugin",
                "author": {"name": "test"},
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
                "interface": {
                    "displayName": "Test",
                    "shortDescription": "Test",
                    "longDescription": "Test",
                    "developerName": "Test",
                    "category": "Productivity",
                    "capabilities": ["Read"],
                    "composerIcon": "./assets/icon.svg",
                    "logo": "./assets/icon.svg",
                },
            },
        )
        self._write_json(
            self.root / ".mcp.json",
            {
                "mcpServers": {
                    "shortdrama-project-agent": {
                        "command": "./.venv/bin/python",
                        "args": ["./server/main.py", "--stdio"],
                    }
                }
            },
        )
        (self.root / "assets" / "icon.svg").write_text("<svg></svg>\n", encoding="utf-8")
        (self.root / "server" / "main.py").write_text("print('test')\n", encoding="utf-8")
        (self.root / "skills" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: test\n---\n", encoding="utf-8"
        )
        (self.root / "README.md").write_text("test plugin\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def _manifest(self) -> dict:
        return json.loads(
            (self.root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )

    def _make_connected(self, *, app: object | None = None) -> None:
        manifest = self._manifest()
        manifest.pop("mcpServers", None)
        manifest["apps"] = "./.app.json"
        self._write_json(self.root / ".codex-plugin" / "plugin.json", manifest)
        if app is not None:
            self._write_json(self.root / ".app.json", app)

    @staticmethod
    def _members(archive: Path) -> set[str]:
        with zipfile.ZipFile(archive) as handle:
            return set(handle.namelist())

    @staticmethod
    def _archive_json(archive: Path, member: str) -> dict:
        with zipfile.ZipFile(archive) as handle:
            return json.loads(handle.read(member))

    def test_profiles_are_explicit_mutually_exclusive_and_reproducible(self) -> None:
        source_manifest_before = (self.root / ".codex-plugin" / "plugin.json").read_bytes()
        source = package(self.root)
        self.assertEqual(
            (self.root / ".codex-plugin" / "plugin.json").read_bytes(),
            source_manifest_before,
        )
        prefix = "shortdrama-project-agent/"
        source_members = self._members(source)
        self.assertIn(prefix + ".mcp.json", source_members)
        self.assertIn(prefix + "server/main.py", source_members)
        self.assertNotIn(prefix + ".app.json", source_members)
        source_metadata = self._archive_json(source, prefix + ".codex-package.json")
        self.assertEqual(source_metadata["profile"], "source")
        self.assertFalse(source_metadata["portable_install"])

        self._make_connected(
            app={
                "apps": {
                    "shortdrama-project-agent": {
                        "id": "plugin_asdk_app_1234567890abcdef",
                        "required": True,
                    }
                }
            }
        )
        connected_manifest_before = (self.root / ".codex-plugin" / "plugin.json").read_bytes()
        app_before = (self.root / ".app.json").read_bytes()
        connected = package(self.root, profile="connected")
        self.assertNotEqual(source.name, connected.name)
        self.assertEqual(
            (self.root / ".codex-plugin" / "plugin.json").read_bytes(),
            connected_manifest_before,
        )
        self.assertEqual((self.root / ".app.json").read_bytes(), app_before)
        connected_members = self._members(connected)
        self.assertIn(prefix + ".app.json", connected_members)
        self.assertNotIn(prefix + ".mcp.json", connected_members)
        self.assertFalse(any(name.startswith(prefix + ".venv/") for name in connected_members))
        self.assertFalse(any(name.startswith(prefix + "server/") for name in connected_members))
        connected_metadata = self._archive_json(
            connected, prefix + ".codex-package.json"
        )
        self.assertEqual(connected_metadata["profile"], "connected")
        self.assertTrue(connected_metadata["portable_install"])

        other_output = Path(self.temporary.name) / "repeat" / "connected.zip"
        repeated = package(self.root, other_output, profile="connected")
        self.assertEqual(connected.read_bytes(), repeated.read_bytes())

    def test_profile_preconditions_fail_before_writing(self) -> None:
        output = Path(self.temporary.name) / "out" / "candidate.zip"
        with self.assertRaisesRegex(PackagingError, "forbids manifest mcpServers"):
            package(self.root, output, profile="connected")
        self.assertFalse(output.exists())

        self._make_connected()
        with self.assertRaisesRegex(PackagingError, "requires the manifest apps file"):
            package(self.root, output, profile="connected")
        self.assertFalse(output.exists())

        self._write_json(self.root / ".app.json", {"apps": {}})
        with self.assertRaisesRegex(PackagingError, "matching plugin entry"):
            package(self.root, output, profile="connected")
        self.assertFalse(output.exists())

        self._write_json(
            self.root / ".app.json",
            {
                "apps": {
                    "shortdrama-project-agent": {
                        "id": "not-a-connection",
                        "required": True,
                    }
                }
            },
        )
        with self.assertRaisesRegex(PackagingError, "invalid ChatGPT connection ID"):
            package(self.root, output, profile="connected")

        self._write_json(
            self.root / ".app.json",
            {
                "apps": {
                    "shortdrama-project-agent": {
                        "id": "plugin_asdk_app_1234567890abcdef",
                        "required": False,
                    }
                }
            },
        )
        with self.assertRaisesRegex(PackagingError, "required=true"):
            package(self.root, output, profile="connected")

        with self.assertRaisesRegex(PackagingError, "source profile cannot contain"):
            package(self.root, output, profile="source")
        self.assertFalse(output.exists())

    def test_controlled_roots_and_sensitive_paths(self) -> None:
        protected_output = self.root / "overwrite.zip"
        with self.assertRaisesRegex(PackagingError, "inside the plugin must be under dist"):
            package(self.root, protected_output)
        self.assertFalse(protected_output.exists())

        (self.root / "unexpected.bin").write_bytes(b"canary")
        with self.assertRaisesRegex(PackagingError, "outside the controlled"):
            package(self.root, Path(self.temporary.name) / "unexpected.zip")
        (self.root / "unexpected.bin").unlink()

        sensitive = self.root / "skills" / "demo" / ".env.production"
        password_assignment = "PASS" + "WORD=" + "'real-password-value'\n"
        sensitive.write_text(password_assignment, encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "sensitive file"):
            package(self.root, Path(self.temporary.name) / "sensitive.zip")
        sensitive.unlink()

    @unittest.skipIf(os.name == "nt", "Windows cannot create a filename containing a backslash")
    def test_posix_backslash_filename_is_rejected(self) -> None:
        unsafe_name = self.root / "assets" / "..\\escape.svg"
        unsafe_name.write_text("unsafe\n", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "unsafe package path"):
            package(self.root, Path(self.temporary.name) / "unsafe-name.zip")
        unsafe_name.unlink()

    def test_input_and_output_symlinks_are_rejected(self) -> None:
        (self.root / "dist").mkdir()
        default_output = self.root / "dist" / "shortdrama-project-agent-0.4.0.zip"
        try:
            default_output.symlink_to(self.root / "README.md")
        except (NotImplementedError, OSError):
            self.skipTest("native symbolic links require unavailable OS privileges")
        with self.assertRaisesRegex(PackagingError, "output cannot be a symbolic link"):
            package(self.root)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "test plugin\n")
        default_output.unlink()
        outside = Path(self.temporary.name) / "outside.svg"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.root / "assets" / "outside.svg"
        try:
            link.symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest("symbolic links are unavailable")
        with self.assertRaisesRegex(PackagingError, "symbolic links"):
            package(self.root, Path(self.temporary.name) / "symlink.zip")

    def test_sensitive_content_is_rejected_without_echoing_it(self) -> None:
        readme = self.root / "README.md"
        secret = "sk-" + "proj-" + "A" * 28
        readme.write_text(f"credential={secret}\n", encoding="utf-8")
        with self.assertRaises(PackagingError) as caught:
            package(self.root, Path(self.temporary.name) / "secret.zip")
        self.assertIn("OpenAI API key", str(caught.exception))
        self.assertIn("README.md", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

        readme.write_text(
            'API_KEY="YOUR_API_KEY"\nhttps://example.test/x?token=NEVER-LOG\n',
            encoding="utf-8",
        )
        output = package(self.root, Path(self.temporary.name) / "placeholder.zip")
        self.assertTrue(output.is_file())

        readme.write_text(
            r"fixture = 'https://example.test/x?token=NEVER-LOG\n'" + "\n",
            encoding="utf-8",
        )
        escaped_output = package(
            self.root, Path(self.temporary.name) / "escaped-placeholder.zip"
        )
        self.assertTrue(escaped_output.is_file())

        embedded_test = "sk-proj-" + "A" * 12 + "test" + "B" * 12
        readme.write_text(embedded_test, encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "OpenAI API key"):
            package(self.root, Path(self.temporary.name) / "embedded-test.zip")

        unquoted = "API_" + "KEY=" + "production-value-123456"
        readme.write_text(unquoted, encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "unquoted credential"):
            package(self.root, Path(self.temporary.name) / "unquoted.zip")

        utf16_secret = "sk-proj-" + "C" * 28
        readme.write_bytes(utf16_secret.encode("utf-16"))
        with self.assertRaisesRegex(PackagingError, "OpenAI API key"):
            package(self.root, Path(self.temporary.name) / "utf16.zip")

    def test_manifest_reference_closure_and_path_safety(self) -> None:
        (self.root / "assets" / "icon.svg").unlink()
        with self.assertRaisesRegex(PackagingError, "manifest reference is missing"):
            package(self.root, Path(self.temporary.name) / "missing.zip")

        (self.root / "assets" / "icon.svg").mkdir()
        (self.root / "assets" / "icon.svg" / "payload").write_text(
            "not an icon\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(PackagingError, "manifest reference is missing"):
            package(self.root, Path(self.temporary.name) / "directory-icon.zip")
        (self.root / "assets" / "icon.svg" / "payload").unlink()
        (self.root / "assets" / "icon.svg").rmdir()

        (self.root / "assets" / "icon.svg").write_text("<svg></svg>\n", encoding="utf-8")
        manifest = self._manifest()
        manifest["interface"]["logo"] = "./../outside.svg"
        self._write_json(self.root / ".codex-plugin" / "plugin.json", manifest)
        with self.assertRaisesRegex(PackagingError, "escapes the plugin root"):
            package(self.root, Path(self.temporary.name) / "escape.zip")

        manifest["interface"]["logo"] = "./assets/icon.svg"
        manifest["version"] = "../../0.4.0"
        self._write_json(self.root / ".codex-plugin" / "plugin.json", manifest)
        with self.assertRaisesRegex(PackagingError, "invalid version"):
            package(self.root)

    def test_sha256sums_is_updated_without_duplicate_entries(self) -> None:
        output_directory = Path(self.temporary.name) / "release"
        output_directory.mkdir()
        sums = output_directory / "SHA256SUMS"
        sums.write_text(f"{'0' * 64}  older.zip\n", encoding="utf-8")
        output = output_directory / "source.zip"

        package(self.root, output)
        expected = hashlib.sha256(output.read_bytes()).hexdigest()
        lines = sums.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{'0' * 64}  older.zip", lines)
        self.assertIn(f"{expected}  source.zip", lines)

        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        package(self.root, output)
        rebuilt = hashlib.sha256(output.read_bytes()).hexdigest()
        lines = sums.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum(line.endswith("  source.zip") for line in lines), 1)
        self.assertIn(f"{rebuilt}  source.zip", lines)

        before = sums.read_bytes()
        private_key_marker = "-----BEGIN " + "PRIVATE KEY-----\nnot-a-placeholder\n"
        (self.root / "README.md").write_text(private_key_marker, encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "private key"):
            package(self.root, output)
        self.assertEqual(sums.read_bytes(), before)

        (self.root / "README.md").write_text("safe again\n", encoding="utf-8")
        old_archive = output.read_bytes()
        sums.write_text("malformed checksum line\n", encoding="utf-8")
        with self.assertRaisesRegex(PackagingError, "invalid SHA256SUMS"):
            package(self.root, output)
        self.assertEqual(output.read_bytes(), old_archive)

        sums.write_text(
            f"{hashlib.sha256(old_archive).hexdigest()}  source.zip\n", encoding="utf-8"
        )
        (self.root / "README.md").write_text("new archive bytes\n", encoding="utf-8")
        with patch.object(
            package_module, "_write_text_atomic", side_effect=OSError("simulated")
        ):
            with self.assertRaisesRegex(OSError, "simulated"):
                package(self.root, output)
        self.assertEqual(output.read_bytes(), old_archive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
