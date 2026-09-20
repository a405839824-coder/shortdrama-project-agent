from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from transport_smoke_support import EXPECTED_VERSION, PLUGIN_ROOT, inspect_session, inspection_passed


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


async def inspect_endpoint(url: str) -> dict:
    # Local MCP verification must not inherit a workstation SOCKS/HTTP proxy.
    async with httpx2.AsyncClient(trust_env=False) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                return await inspect_session(session)


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="shortdrama-http-test-") as data_dir:
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
                "SHORTDRAMA_DATA_DIR": data_dir,
                "SHORTDRAMA_DEPLOYMENT_MODE": "local",
                "SHORTDRAMA_AUTH_MODE": "none",
            }
        )
        process = subprocess.Popen(
            [sys.executable, str(PLUGIN_ROOT / "server" / "main.py"), "--http"],
            cwd=PLUGIN_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            health = None
            health_client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(80):
                if process.poll() is not None:
                    break
                try:
                    with health_client.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    break
                except Exception:
                    time.sleep(0.1)
            if health is None:
                if process.poll() is None:
                    process.terminate()
                stdout, stderr = process.communicate(timeout=2)
                raise RuntimeError(f"server did not become healthy\nstdout={stdout}\nstderr={stderr}")
            inspection = asyncio.run(inspect_endpoint(f"http://127.0.0.1:{port}/mcp"))
            result = {"status": "pass", "health": health, "inspection": inspection}
            if (health.get("status") != "pass"
                    or health.get("version") != EXPECTED_VERSION
                    or not inspection_passed(inspection)):
                result["status"] = "fail"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "pass" else 1
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
