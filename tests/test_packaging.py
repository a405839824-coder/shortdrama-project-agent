from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from shortdrama_mcp import __version__
from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.auth import JwtTokenVerifier
from shortdrama_mcp.config import Settings


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from configure_chatgpt_connection import configure  # noqa: E402
import check_cloud_endpoint  # noqa: E402
from configure_railway_env import render as render_railway_env  # noqa: E402
from configure_railway_env import write_environment  # noqa: E402
from install_repo_marketplace import install  # noqa: E402


class PackagingTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_paths_and_cloud_security_schemes(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "shortdrama-project-agent")
        self.assertEqual(__version__, "0.5.18")
        self.assertEqual(manifest["version"], __version__)
        runtime_rules = json.loads(
            (
                PLUGIN_ROOT
                / "skills"
                / "shortdrama-storyboard"
                / "references"
                / "storyboard_runtime_rules.json"
            ).read_text(encoding="utf-8")
        )
        capabilities = json.loads(
            (
                PLUGIN_ROOT
                / "skills"
                / "shortdrama-storyboard"
                / "references"
                / "project_agent_capabilities.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            runtime_rules["compact_master_contract"]["plugin_version"],
            __version__,
        )
        self.assertEqual(
            capabilities["compact_master_contract"]["plugin_version"],
            __version__,
        )
        for key in ("skills", "mcpServers"):
            self.assertTrue((PLUGIN_ROOT / manifest[key]).exists(), key)
        bundled_mcp = json.loads((PLUGIN_ROOT / manifest["mcpServers"]).read_text(encoding="utf-8"))
        self.assertEqual(set(bundled_mcp), {"mcpServers"})
        self.assertIn("shortdrama-project-agent", bundled_mcp["mcpServers"])
        with tempfile.TemporaryDirectory(prefix="shortdrama-cloud-test-") as data_dir:
            local = Settings(
                data_dir=Path(data_dir),
                public_base_url="https://storyboard.example.test",
                host="0.0.0.0",
                port=8000,
                deployment_mode="cloud",
                auth_mode="oauth",
                oauth_issuer="https://auth.example.test",
                oauth_audience="https://storyboard.example.test",
                oauth_jwks_url="https://auth.example.test/.well-known/jwks.json",
                oauth_scopes=("storyboard:read", "storyboard:write"),
                allow_local_file_urls=False,
                max_file_bytes=50 * 1024 * 1024,
            )
            local.validate()
            server, _service = create_mcp(local)
            tools = await server.list_tools()
            self.assertEqual(len(tools), 19)
            for tool in tools:
                descriptor = tool.model_dump(by_alias=True, exclude_none=True)
                self.assertEqual(descriptor["_meta"]["securitySchemes"][0]["type"], "oauth2")
            with self.assertRaisesRegex(ValueError, "refuses unauthenticated"):
                replace(local, auth_mode="none").validate()
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                replace(local, public_base_url="http://storyboard.example.test").validate()
            with self.assertRaisesRegex(ValueError, "HTTPS for OAUTH_ISSUER"):
                replace(local, oauth_issuer="http://auth.example.test").validate()

    async def test_auth0_style_jwt_and_scopes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shortdrama-auth0-test-") as data_dir:
            settings = Settings(
                data_dir=Path(data_dir),
                public_base_url="https://storyboard.example.test",
                host="0.0.0.0",
                port=8000,
                deployment_mode="cloud",
                auth_mode="oauth",
                oauth_issuer="https://tenant.example.auth0.com/",
                oauth_audience="https://storyboard.example.test",
                oauth_jwks_url="https://tenant.example.auth0.com/.well-known/jwks.json",
                oauth_scopes=("storyboard:read", "storyboard:write"),
                allow_local_file_urls=False,
                max_file_bytes=50 * 1024 * 1024,
            )
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

            class FakeJwksClient:
                def get_signing_key_from_jwt(self, _token: str):
                    return type("SigningKey", (), {"key": private_key.public_key()})()

            verifier = JwtTokenVerifier(settings)
            verifier.jwks_client = FakeJwksClient()
            now = int(time.time())
            claims = {
                "iss": settings.oauth_issuer,
                "aud": settings.oauth_audience,
                "sub": "auth0|user-123",
                "iat": now,
                "exp": now + 300,
                "scope": "storyboard:read storyboard:write",
            }
            token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})
            access = verifier._verify_sync(token)
            self.assertIsNotNone(access)
            self.assertEqual(access.subject, "auth0|user-123")
            insufficient = jwt.encode(
                {**claims, "scope": "storyboard:read"},
                private_key,
                algorithm="RS256",
                headers={"kid": "test"},
            )
            self.assertIsNone(verifier._verify_sync(insufficient))

    async def test_plugin_data_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shortdrama-plugin-data-") as data_dir:
            env = {
                "PLUGIN_DATA": data_dir,
                "PUBLIC_BASE_URL": "http://127.0.0.1:8000",
                "SHORTDRAMA_DEPLOYMENT_MODE": "local",
                "SHORTDRAMA_AUTH_MODE": "none",
            }
            with patch.dict(os.environ, env, clear=False):
                with patch.dict(os.environ, {"SHORTDRAMA_DATA_DIR": ""}, clear=False):
                    os.environ.pop("SHORTDRAMA_DATA_DIR", None)
                    settings = Settings.from_env()
            self.assertEqual(settings.data_dir, Path(data_dir).resolve())

    async def test_connection_and_marketplace_helpers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shortdrama-package-test-") as root_value:
            repo_root = Path(root_value)
            plugin_root = repo_root / "plugins" / "shortdrama-project-agent"
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            (plugin_root / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": "shortdrama-project-agent", "version": "0.1.0"}),
                encoding="utf-8",
            )
            app_path, manifest_path = configure(
                plugin_root,
                "plugin_asdk_app_1234567890abcdef",
            )
            app = json.loads(app_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(app["apps"]["shortdrama-project-agent"]["id"], "plugin_asdk_app_1234567890abcdef")
            self.assertEqual(manifest["apps"], "./.app.json")
            self.assertNotIn("mcpServers", manifest)
            marketplace_path = install(repo_root, plugin_root)
            marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
            self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/shortdrama-project-agent")

    async def test_railway_deployment_contract_and_environment_generator(self) -> None:
        config = json.loads((PLUGIN_ROOT / "railway.json").read_text(encoding="utf-8"))
        self.assertEqual(config["build"]["builder"], "DOCKERFILE")
        self.assertEqual(config["build"]["dockerfilePath"], "Dockerfile")
        self.assertEqual(config["deploy"]["healthcheckPath"], "/health")
        self.assertEqual(config["deploy"]["numReplicas"], 1)
        dockerfile = (PLUGIN_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("HOST=0.0.0.0", dockerfile)
        self.assertNotIn("PORT=", dockerfile)

        content = render_railway_env("agent-prod.up.railway.app", "tenant.us.auth0.com")
        self.assertIn("PUBLIC_BASE_URL=https://agent-prod.up.railway.app", content)
        self.assertIn("OAUTH_ISSUER=https://tenant.us.auth0.com/", content)
        self.assertIn("OAUTH_AUDIENCE=https://agent-prod.up.railway.app", content)
        self.assertNotIn("PORT=", content)
        with self.assertRaisesRegex(ValueError, "不能包含路径"):
            render_railway_env("agent-prod.up.railway.app/mcp", "tenant.us.auth0.com")

        with tempfile.TemporaryDirectory(prefix="shortdrama-railway-env-") as root_value:
            output = Path(root_value) / ".railway.env"
            write_environment(output, content)
            self.assertEqual(output.read_text(encoding="utf-8"), content)
            with self.assertRaises(FileExistsError):
                write_environment(output, content)

    async def test_cloud_endpoint_checker_requires_oauth_challenge(self) -> None:
        healthy = (
            200,
            b'{"status":"pass","service":"shortdrama-project-agent","version":"0.1.0"}',
            {},
        )
        protected = (
            401,
            b"",
            {"WWW-Authenticate": 'Bearer resource_metadata="https://agent.up.railway.app/.well-known/oauth-protected-resource"'},
        )
        with patch.object(check_cloud_endpoint, "_request", side_effect=[healthy, protected]):
            report = check_cloud_endpoint.check("https://agent.up.railway.app")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["unauthenticated_mcp_status"], 401)

        with patch.object(check_cloud_endpoint, "_request", side_effect=[healthy, (200, b"{}", {})]):
            with self.assertRaisesRegex(RuntimeError, "应被拒绝"):
                check_cloud_endpoint.check("https://agent.up.railway.app")


if __name__ == "__main__":
    unittest.main(verbosity=2)
