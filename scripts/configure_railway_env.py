#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _https_origin(value: str, label: str) -> str:
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{label} 必须是 HTTPS 域名")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} 不能包含账号、查询参数或片段")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"{label} 不能包含路径")
    if parsed.port:
        raise ValueError(f"{label} 不能包含端口")
    return f"https://{parsed.hostname.lower()}"


def render(railway_domain: str, auth0_domain: str) -> str:
    public_base_url = _https_origin(railway_domain, "Railway 域名")
    auth0_origin = _https_origin(auth0_domain, "Auth0 租户域名")
    return "\n".join(
        [
            "HOST=0.0.0.0",
            f"PUBLIC_BASE_URL={public_base_url}",
            "SHORTDRAMA_DEPLOYMENT_MODE=cloud",
            "SHORTDRAMA_AUTH_MODE=oauth",
            "SHORTDRAMA_DATA_DIR=/data",
            f"OAUTH_ISSUER={auth0_origin}/",
            f"OAUTH_AUDIENCE={public_base_url}",
            f"OAUTH_JWKS_URL={auth0_origin}/.well-known/jwks.json",
            "OAUTH_SCOPES=storyboard:read,storyboard:write",
            "ALLOW_LOCAL_FILE_URLS=0",
            "MAX_FILE_BYTES=52428800",
            "",
        ]
    )


def write_environment(output: Path, content: str, *, force: bool = False) -> Path:
    output = output.resolve()
    if output.exists() and not force:
        raise FileExistsError(f"{output} 已存在；确认后使用 --force 覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可粘贴到 Railway Variables 的非密钥环境变量")
    parser.add_argument("--railway-domain", required=True, help="例如 my-agent.up.railway.app")
    parser.add_argument("--auth0-domain", required=True, help="例如 tenant.us.auth0.com")
    parser.add_argument("--output", type=Path, default=PLUGIN_ROOT / ".railway.env")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    try:
        content = render(args.railway_domain, args.auth0_domain)
        if args.stdout:
            print(content, end="")
            return 0
        output = write_environment(args.output, content, force=args.force)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
