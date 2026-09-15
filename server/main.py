#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from shortdrama_mcp.app import create_mcp  # noqa: E402
from shortdrama_mcp.config import Settings  # noqa: E402

# Full two-episode canonicals include their source/action derivation. Keep a
# finite transport ceiling without dropping that evidence to fit the SDK's 4 MiB default.
MAX_MCP_REQUEST_BODY_BYTES = 32 * 1024 * 1024


def create_http_app(server, settings: Settings):
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host=settings.host,
        max_request_body_size=MAX_MCP_REQUEST_BODY_BYTES,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Short-drama project Agent MCP server")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="run with MCP stdio transport")
    mode.add_argument("--http", action="store_true", help="run streamable HTTP on /mcp")
    mode.add_argument("--self-test", action="store_true", help="run bundled R10 tests and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    server, service = create_mcp(settings)
    if args.self_test:
        report = service.agent_status("local-single-user", full_self_test=True)
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
        return 0 if report.status == "pass" else 1
    if args.http:
        import uvicorn

        app = create_http_app(server, settings)
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
        return 0
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
