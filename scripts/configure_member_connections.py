"""Validate member access, verify Auth0 confidential clients, then install atomically.

No credentials are accepted on the command line or written to the registry.
AUTH0_MANAGEMENT_TOKEN needs read:clients only. --check is local validation only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from shortdrama_mcp.config import Settings
from shortdrama_mcp.members import validate_registry


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_client(client: dict, expected_id: str) -> None:
    """Client ID alone is insufficient: rule out public and implicit flows."""
    if (client.get("client_id") != expected_id or client.get("app_type") != "regular_web"
            or client.get("is_first_party") is not False
            or client.get("token_endpoint_auth_method") not in {"client_secret_post", "client_secret_basic"}
            or set(client.get("grant_types", [])) != {"authorization_code", "refresh_token"}):
        raise ValueError("Auth0 client must be a confidential third-party code/refresh web client")
    callbacks = client.get("callbacks")
    if not isinstance(callbacks, list) or not callbacks or any(
        not isinstance(url, str) or not re.fullmatch(
            r"https://chatgpt\.com/(?:connector_platform_oauth_redirect|connector/oauth/[A-Za-z0-9_-]+)", url
        ) for url in callbacks
    ):
        raise ValueError("Auth0 callbacks must be exact ChatGPT callback URLs; no wildcards")


def validate_update(previous: dict, proposed: dict) -> None:
    old_by_id = {m["member_id"]: m for m in previous["members"]}
    old_clients = {m["client_id"]: m["member_id"] for m in previous["members"]}
    new_by_id = {m["member_id"]: m for m in proposed["members"]}
    # Keep disabled member records to reserve their identity and namespace.
    if not old_by_id.keys() <= new_by_id.keys():
        raise ValueError("disable existing members instead of removing them")
    for m in proposed["members"]:
        if m["client_id"] in old_clients and old_clients[m["client_id"]] != m["member_id"]:
            raise ValueError("an existing OAuth client cannot be reassigned to another member")
        old = old_by_id.get(m["member_id"])
        if old and (old.get("legacy_subject") != m.get("legacy_subject")
                    or m["tokens_valid_after"] < old["tokens_valid_after"]):
            raise ValueError("existing namespace and token revocation history must be preserved")


def verify_auth0(registry: dict, token: str) -> None:
    issuer = urlparse(registry["issuer"])
    if issuer.scheme != "https" or not issuer.netloc or issuer.username or issuer.password or issuer.query or issuer.fragment:
        raise ValueError("Auth0 management verification requires an HTTPS issuer")
    fields = "client_id,app_type,is_first_party,token_endpoint_auth_method,grant_types,callbacks"
    opener = build_opener(NoCredentialRedirect())
    for member in registry["members"]:
        # Disabled/retired clients may have been deleted from Auth0.
        if not member["enabled"]:
            continue
        url = registry["issuer"].rstrip("/") + "/api/v2/clients/" + quote(member["client_id"], safe="")
        url += "?" + urlencode({"fields": fields, "include_fields": "true"})
        request = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with opener.open(request, timeout=20) as response:
            client = json.load(response)
        validate_client(client, member["client_id"])


def write_atomic(path: Path, registry: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".member-connections-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(registry, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, help="reviewed member registry JSON, containing no client secrets")
    parser.add_argument("--check", action="store_true", help="local validation only; does not verify Auth0 or install")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        registry = validate_registry(json.loads(args.registry.read_text(encoding="utf-8")), settings)
        if args.check:
            print("Local registry valid. Auth0 configuration and ChatGPT connection are NOT verified.")
            return 0
        token = os.getenv("AUTH0_MANAGEMENT_TOKEN")
        if not token:
            raise ValueError("AUTH0_MANAGEMENT_TOKEN with read:clients is required for installation")
        verify_auth0(registry, token)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        target = settings.data_dir / "member_connections.json"
        with FileLock(str(target) + ".lock", timeout=15):
            if target.exists():
                previous = validate_registry(json.loads(target.read_text(encoding="utf-8")), settings)
                validate_update(previous, registry)
            write_atomic(target, registry)
        print(f"Installed {len(registry['members'])} members after live Auth0 client verification.")
        print("Enable SHORTDRAMA_MEMBER_CONNECTION_ACCESS=1 only after staging OAuth acceptance.")
        return 0
    except HTTPError as exc:
        print(f"Auth0 verification failed: HTTP {exc.code}. No registry installed.", file=sys.stderr)
    except URLError:
        print("Auth0 verification failed: network unavailable. No registry installed.", file=sys.stderr)
    except (ValueError, OSError) as exc:
        # JSON error messages may contain input snippets; never echo them.
        message = "Invalid registry JSON" if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(f"Member configuration failed: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
