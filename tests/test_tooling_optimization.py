from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import atomic_io
import check_cloud_endpoint
from package_plugin import _collect_files
from configure_railway_env import render, write_environment
from transport_smoke_support import EXPECTED_TOOL_NAMES, EXPECTED_VERSION, inspection_passed


class ToolingOptimizationTests(unittest.TestCase):
    def test_transport_success_requires_healthy_runtime_and_exact_tool_set(self):
        inspection = {
            "server": {"version": EXPECTED_VERSION},
            "tool_count": len(EXPECTED_TOOL_NAMES),
            "tool_names": sorted(EXPECTED_TOOL_NAMES),
            "status_call_is_error": False,
            "agent_status": "pass", "runtime_status": "pass",
        }
        self.assertTrue(inspection_passed(inspection))
        for change in (
            {"agent_status": "fail"}, {"runtime_status": "fail"},
            {"status_call_is_error": True}, {"server": {"version": "stale"}},
            {"tool_names": [inspection["tool_names"][0]] * len(EXPECTED_TOOL_NAMES)},
        ):
            with self.subTest(change=change):
                self.assertFalse(inspection_passed({**inspection, **change}))

    def test_packaging_prunes_excluded_trees_before_reading_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (".venv", ".ruff_cache", "data", "dist", "server/__pycache__"):
                (root / name).mkdir(parents=True)
                (root / name / "private.txt").write_text("excluded", encoding="utf-8")
            (root / "server" / "main.py").write_bytes(b"pass\n")
            iterdir = Path.iterdir

            def guarded_iterdir(path):
                self.assertNotIn(path.name, {".venv", ".ruff_cache", "data", "dist", "__pycache__"})
                return iterdir(path)

            with patch.object(Path, "iterdir", guarded_iterdir):
                self.assertEqual(_collect_files(root, "source"), {"server/main.py": b"pass\n"})

    def test_atomic_failure_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            atomic_io.write_json_atomic(path, {"name": "分镜"})
            original = path.read_bytes()
            self.assertEqual(json.loads(original), {"name": "分镜"})
            for operation in ("fsync", "replace"):
                with self.subTest(operation=operation):
                    with patch.object(atomic_io.os, operation, side_effect=OSError("simulated")):
                        with self.assertRaisesRegex(OSError, "simulated"):
                            atomic_io.write_json_atomic(path, {"name": "changed"})
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_environment_write_does_not_overwrite_existing_temporary_name(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "railway.env"
            unrelated = output.with_name(f".{output.name}.tmp")
            unrelated.write_text("keep", encoding="utf-8")
            content = render("agent.example.test", "tenant.example.test")
            write_environment(output, content)
            self.assertEqual(output.read_text(encoding="utf-8"), content)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
            with self.assertRaises(FileExistsError):
                write_environment(output, "replacement")
            with self.assertRaises(ValueError):
                render("agent.example.test:0", "tenant.example.test")

    def test_cloud_check_rejects_non_origin_urls_before_network_access(self):
        for origin in (
            "https://user:password@example.test",
            "https://example.test?redirect=other",
            "https://example.test#other",
            "https://example.test:invalid",
            "https://example.test:70000",
            "https://example.test/mcp",
        ):
            with self.subTest(origin=origin), patch.object(check_cloud_endpoint, "_request") as request:
                with self.assertRaises(ValueError):
                    check_cloud_endpoint.check(origin)
                request.assert_not_called()

    def test_non_object_health_response_is_a_controlled_failure(self):
        for payload in (b"[]", b"null", b'"pass"'):
            with self.subTest(payload=payload):
                with patch.object(check_cloud_endpoint, "_request", return_value=(200, payload, {})):
                    with self.assertRaisesRegex(RuntimeError, "/health"):
                        check_cloud_endpoint.check("https://example.test")

    @unittest.skipUnless(os.name == "nt", "PowerShell setup regression runs on Windows")
    def test_setup_propagates_python_failure_without_success_message(self):
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        with tempfile.TemporaryDirectory() as directory:
            failing_python = Path(directory) / "python.cmd"
            failing_python.write_text("@exit /b 73\n", encoding="ascii")
            result = subprocess.run(
                [powershell, "-NoProfile", "-File", str(ROOT / "scripts" / "setup_local.ps1")],
                env={**os.environ, "PYTHON_BIN": str(failing_python)},
                capture_output=True, timeout=20,
            )
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertEqual(result.stdout.strip(), b"")


if __name__ == "__main__":
    unittest.main()
