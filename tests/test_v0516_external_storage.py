#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

# The production image installs these packages.  This focused unit test also
# runs in the repository's lightweight validation environment, where only the
# data-path behavior is exercised and transport classes are intentionally
# absent.
if "filelock" not in sys.modules:
    try:
        import filelock  # noqa: F401
    except ModuleNotFoundError:
        filelock_module = types.ModuleType("filelock")

        class FileLock:  # type: ignore[no-redef]
            def __init__(self, _path: str) -> None:
                pass

            def __enter__(self) -> "FileLock":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        filelock_module.FileLock = FileLock
        sys.modules["filelock"] = filelock_module

try:
    from mcp.server.mcpserver import Context as _Context  # noqa: F401
except ModuleNotFoundError:
    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_mcpserver_module = types.ModuleType("mcp.server.mcpserver")
    mcp_mcpserver_module.Context = type("Context", (), {})
    sys.modules.update(
        {
            "mcp": mcp_module,
            "mcp.server": mcp_server_module,
            "mcp.server.mcpserver": mcp_mcpserver_module,
        }
    )

try:
    from starlette.requests import Request as _Request  # noqa: F401
except ModuleNotFoundError:
    starlette_module = types.ModuleType("starlette")
    starlette_requests_module = types.ModuleType("starlette.requests")
    starlette_responses_module = types.ModuleType("starlette.responses")
    starlette_requests_module.Request = type("Request", (), {})
    starlette_responses_module.FileResponse = type("FileResponse", (), {})
    starlette_responses_module.PlainTextResponse = type("PlainTextResponse", (), {})
    sys.modules.update(
        {
            "starlette": starlette_module,
            "starlette.requests": starlette_requests_module,
            "starlette.responses": starlette_responses_module,
        }
    )

from shortdrama_mcp.external_storage import ExternalProjectStorage  # noqa: E402
from shortdrama_mcp.models import OpenAIFile  # noqa: E402
from shortdrama_mcp.storage import ProjectStore, sha256_bytes  # noqa: E402


class ExternalProjectStorageTests(unittest.TestCase):
    def test_register_mount_export_commit_and_unmount(self) -> None:
        with tempfile.TemporaryDirectory() as root_value, tempfile.TemporaryDirectory() as runtime_value:
            root = Path(root_value)
            runtime = Path(runtime_value)
            store = ProjectStore(root, "https://example.invalid")
            namespace = "owner@example.invalid"
            project_id = "SBP-test-library"
            legacy_project = store.project_dir(namespace, project_id)
            (legacy_project / "artifacts").mkdir(parents=True)
            (legacy_project / "handoffs").mkdir()
            (legacy_project / "hub.json").write_text(
                json.dumps({"project_id": project_id}), encoding="utf-8"
            )
            payload = b"immutable-storyboard-bytes"
            (legacy_project / "artifacts" / "ART-test__storyboard.xlsx").write_bytes(payload)
            (legacy_project / "handoffs" / "HND-test.json").write_text(
                json.dumps({"handoff_id": "HND-test", "status": "prepared"}),
                encoding="utf-8",
            )

            service = SimpleNamespace(store=store)
            settings = SimpleNamespace(
                public_base_url="https://example.invalid",
                max_file_bytes=50 * 1024 * 1024,
            )
            manager = ExternalProjectStorage(
                service,
                settings,
                mode="library",
                runtime_root=runtime,
            )
            tree_sha256, file_count, expanded_size = manager.tree_manifest(
                legacy_project
            )

            archive_buffer = io.BytesIO()
            archive_prefix = manager._archive_prefix(
                namespace, project_id
            ).rstrip("/")
            with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
                archive.add(legacy_project, arcname=archive_prefix)
            snapshot = archive_buffer.getvalue()
            bundle_sha256 = sha256_bytes(snapshot)
            library_file = OpenAIFile(
                download_url="https://example.invalid/library-snapshot",
                file_id="file_testbundle",
                mime_type="application/gzip",
                file_name="projects-before.tgz",
            )

            with patch(
                "shortdrama_mcp.external_storage.download_openai_file",
                return_value=(snapshot, "projects-before.tgz", "application/gzip"),
            ):
                response = manager.handle_action(
                    namespace,
                    storage_action="register_snapshot",
                    project_id=project_id,
                    snapshot=library_file,
                    bundle_library_file_id="libfile_testbundle",
                    bundle_file_id="file_testbundle",
                    library_path="/migration/projects-before.tgz",
                    bundle_sha256=bundle_sha256,
                    expected_tree_sha256=tree_sha256,
                )
                replay = manager.handle_action(
                    namespace,
                    storage_action="register_snapshot",
                    project_id=project_id,
                    snapshot=library_file,
                    bundle_library_file_id="libfile_testbundle",
                    bundle_file_id="file_testbundle",
                    library_path="/migration/projects-before.tgz",
                    bundle_sha256=bundle_sha256,
                    expected_tree_sha256=tree_sha256,
                )
            registered = response.data["pointer"]
            self.assertEqual(registered["generation"], 1)
            self.assertEqual(registered["file_count"], file_count)
            self.assertEqual(registered["expanded_size_bytes"], expanded_size)
            self.assertEqual(replay.data["pointer"]["generation"], 1)
            self.assertEqual(
                response.data["library_readback_verified"]["tree_sha256"],
                tree_sha256,
            )

            with patch(
                "shortdrama_mcp.external_storage.download_openai_file",
                return_value=(snapshot, "projects-before.tgz", "application/gzip"),
            ):
                mounted = manager.mount_snapshot(
                    namespace,
                    project_id=project_id,
                    snapshot=library_file,
                )
            self.assertEqual(mounted["tree_sha256"], tree_sha256)
            self.assertEqual(mounted["file_count"], file_count)
            self.assertEqual(mounted["expanded_size_bytes"], expanded_size)
            self.assertEqual(
                (
                    manager._mounted_project_dir(namespace, project_id)
                    / "artifacts"
                    / "ART-test__storyboard.xlsx"
                ).read_bytes(),
                payload,
            )

            exported = manager.export_snapshot(namespace, project_id)
            self.assertEqual(exported["tree_sha256"], tree_sha256)
            self.assertEqual(
                manager.resolve_export(exported["download_url"].split("/")[-2])[
                    "export_id"
                ],
                exported["export_id"],
            )
            exported_bytes = Path(exported["path"]).read_bytes()
            exported_file = OpenAIFile(
                download_url="https://example.invalid/exported-snapshot",
                file_id="file_nextbundle",
                mime_type="application/gzip",
                file_name="SBP-test-library-v2.tgz",
            )
            with patch(
                "shortdrama_mcp.external_storage.download_openai_file",
                return_value=(
                    exported_bytes,
                    "SBP-test-library-v2.tgz",
                    "application/gzip",
                ),
            ):
                committed_response = manager.handle_action(
                    namespace,
                    storage_action="commit_snapshot",
                    project_id=project_id,
                    snapshot=exported_file,
                    export_id=exported["export_id"],
                    expected_generation=1,
                    bundle_library_file_id="libfile_nextbundle",
                    bundle_file_id="file_nextbundle",
                    library_path="/migration/SBP-test-library-v2.tgz",
                    bundle_sha256=exported["bundle_sha256"],
                    tree_sha256=tree_sha256,
                )
                committed_replay = manager.handle_action(
                    namespace,
                    storage_action="commit_snapshot",
                    project_id=project_id,
                    snapshot=exported_file,
                    export_id=exported["export_id"],
                    expected_generation=1,
                    bundle_library_file_id="libfile_nextbundle",
                    bundle_file_id="file_nextbundle",
                    library_path="/migration/SBP-test-library-v2.tgz",
                    bundle_sha256=exported["bundle_sha256"],
                    tree_sha256=tree_sha256,
                )
            committed = committed_response.data["pointer"]
            self.assertEqual(committed["generation"], 2)
            self.assertEqual(committed_replay.data["pointer"]["generation"], 2)

            hub_path = manager._mounted_project_dir(namespace, project_id) / "hub.json"
            original_hub = hub_path.read_bytes()
            hub_path.write_bytes(original_hub + b"\n")
            with self.assertRaisesRegex(ValueError, "unsynced_changes"):
                manager.unmount(namespace, project_id)
            hub_path.write_bytes(original_hub)
            self.assertEqual(manager.unmount(namespace, project_id)["status"], "unmounted")
            self.assertFalse(manager._mounted_project_dir(namespace, project_id).exists())
            self.assertEqual(manager.status(namespace)["registered_project_count"], 1)

            with patch(
                "shortdrama_mcp.external_storage.download_openai_file",
                return_value=(snapshot + b"tampered", "bad.tgz", "application/gzip"),
            ):
                with self.assertRaisesRegex(ValueError, "bundle_mismatch"):
                    manager._verified_snapshot(
                        namespace,
                        project_id=project_id,
                        snapshot=library_file,
                        expected_bundle_sha256=bundle_sha256,
                        expected_tree_sha256=tree_sha256,
                        install=False,
                    )


if __name__ == "__main__":
    unittest.main()
