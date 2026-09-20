#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _origin(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.path
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("BASE_URL 必须是无路径的 HTTPS 根地址")
    # Accessing port validates malformed/non-numeric ports before any request.
    _ = parsed.port
    return candidate


def _request(request: urllib.request.Request, timeout: float) -> tuple[int, bytes, dict[str, str]]:
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def check(base_url: str, timeout: float = 20.0) -> dict[str, object]:
    base_url = _origin(base_url)
    health_request = urllib.request.Request(f"{base_url}/health", headers={"Accept": "application/json"})
    health_status, health_body, _headers = _request(health_request, timeout)
    if health_status != 200:
        raise RuntimeError(f"/health 返回 HTTP {health_status}")
    try:
        health = json.loads(health_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("/health 没有返回有效 JSON") from exc
    if not isinstance(health, dict) or health.get("status") != "pass" or health.get("service") != "shortdrama-project-agent":
        raise RuntimeError(f"/health 内容异常：{health}")

    initialize = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "railway-preflight", "version": "1.0"},
            },
        }
    ).encode("utf-8")
    mcp_request = urllib.request.Request(
        f"{base_url}/mcp",
        data=initialize,
        method="POST",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        },
    )
    mcp_status, _mcp_body, mcp_headers = _request(mcp_request, timeout)
    if mcp_status not in {401, 403}:
        raise RuntimeError(f"未登录访问 /mcp 应被拒绝，实际 HTTP {mcp_status}")
    challenge = next((value for key, value in mcp_headers.items() if key.lower() == "www-authenticate"), "")
    if not challenge.lower().startswith("bearer"):
        raise RuntimeError("/mcp 拒绝了请求，但缺少 Bearer OAuth challenge")

    return {
        "status": "pass",
        "base_url": base_url,
        "health": health,
        "unauthenticated_mcp_status": mcp_status,
        "oauth_challenge": challenge,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Railway 上的健康状态和 OAuth 防护")
    parser.add_argument("base_url", help="例如 https://my-agent.up.railway.app")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        result = check(args.base_url, args.timeout)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
