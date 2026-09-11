"""Host-managed identities for individually provisioned confidential OAuth clients.

An OAuth client ID is public, not a password. This mode requires one confidential
Auth0 client per member, with only code + refresh grants and a private secret
stored in that member's ChatGPT connection. See docs/member-connections.md.
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .config import Settings


MEMBER_CONTRACT = "member-connections-v1"
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")


@dataclass(frozen=True)
class MemberIdentity:
    member_id: str
    name: str
    issuer: str
    oauth_subject: str
    client_id: str
    role: str
    enabled: bool
    namespace: str


# Pin identity for one dispatch: a config replacement must not authorize as A
# and then resolve the handler's storage namespace as B in the same call.
current_member: ContextVar[MemberIdentity | None] = ContextVar("shortdrama_member", default=None)


def _text(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 512
            and value == value.strip() and all(ord(c) >= 32 for c in value))


def validate_registry(value: Any, settings: Settings) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"schema_version", "issuer", "audience", "members"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value["issuer"] != settings.oauth_issuer or value["audience"] != settings.oauth_audience):
        raise ValueError("invalid member registry header or OAuth resource")
    members = value["members"]
    if not isinstance(members, list) or not 1 <= len(members) <= 20:
        raise ValueError("member registry requires 1-20 members")
    ids, clients, namespaces = set(), set(), set()
    required = {"member_id", "name", "client_id", "role", "enabled", "allowed_subjects", "tokens_valid_after"}
    for member in members:
        if not isinstance(member, dict) or not required <= member.keys() or member.keys() - required - {"legacy_subject"}:
            raise ValueError("invalid member fields")
        if any(not isinstance(member[k], str) or not IDENTIFIER.fullmatch(member[k]) for k in ("member_id", "client_id")):
            raise ValueError("invalid member or confidential client identifier")
        if (not _text(member["name"]) or not isinstance(member["role"], str)
                or member["role"] not in {"admin", "producer"} or type(member["enabled"]) is not bool):
            raise ValueError("invalid member name, role or enabled flag")
        subjects = member["allowed_subjects"]
        if (not isinstance(subjects, list) or not 1 <= len(subjects) <= 20 or any(not _text(s) for s in subjects)
                or len(set(subjects)) != len(subjects) or any(s.endswith("@clients") for s in subjects)):
            raise ValueError("explicit, unique human OAuth subjects are required")
        if type(member["tokens_valid_after"]) is not int or member["tokens_valid_after"] < 0:
            raise ValueError("invalid member token revocation time")
        legacy = member.get("legacy_subject")
        if legacy is not None and (not _text(legacy) or legacy not in settings.admin_subjects or member["role"] != "admin"):
            raise ValueError("legacy namespace may only belong to a configured bootstrap owner's admin member")
        namespace = f"{settings.oauth_issuer}|{legacy}" if legacy else f"{settings.oauth_issuer}|member:{member['member_id']}"
        if member["member_id"] in ids or member["client_id"] in clients or namespace in namespaces:
            raise ValueError("duplicate member, client or namespace")
        ids.add(member["member_id"])
        clients.add(member["client_id"])
        namespaces.add(namespace)
    if not any(m["role"] == "admin" and m["enabled"] for m in members):
        raise ValueError("member registry requires an enabled administrator")
    if len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) + 1 > 65536:
        raise ValueError("member registry exceeds 64 KiB")
    return value


def load_registry(settings: Settings) -> dict[str, Any]:
    # Fixed path, deliberately outside all project/import/snapshot directories.
    try:
        with (settings.data_dir / "member_connections.json").open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("registry too large")
        return validate_registry(json.loads(raw), settings)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Never include file contents, paths or parser snippets in client errors.
        raise PermissionError("member_registry_unavailable") from exc


def resolve_member(settings: Settings, token: Any) -> MemberIdentity:
    if token is None:
        raise PermissionError("authentication_required")
    claims = token.claims or {}
    issuer = claims.get("iss")
    client_id = claims.get("azp")
    if (issuer != settings.oauth_issuer or not _text(token.subject) or not _text(client_id)
            or token.client_id != client_id
            or ("client_id" in claims and claims["client_id"] != client_id)):
        raise PermissionError("member_identity_invalid")
    # No role/employee ID from token metadata, tool arguments, email or chat name.
    for member in load_registry(settings)["members"]:
        if member["client_id"] != client_id:
            continue
        if token.subject not in member["allowed_subjects"]:
            raise PermissionError("member_google_account_not_allowed")
        issued = claims.get("iat")
        if type(issued) is not int or issued < member["tokens_valid_after"]:
            raise PermissionError("member_token_revoked")
        legacy = member.get("legacy_subject")
        return MemberIdentity(
            member["member_id"], member["name"], issuer, token.subject, client_id,
            member["role"], member["enabled"],
            f"{issuer}|{legacy}" if legacy else f"{issuer}|member:{member['member_id']}",
        )
    raise PermissionError("member_connection_not_registered")
