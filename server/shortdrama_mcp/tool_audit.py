"""Content-free dispatch diagnostics; these records are not production certificates.

Never persist exception messages, tool payloads, downloads, tokens or signatures.
Only server-authored error codes and a small set of typed envelope fields survive.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__


_CODE = re.compile(r"^([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?=[:;\s]|$)")
_IDS = {
    "project_id": re.compile(r"SBP-[0-9a-f]{8,64}\Z"),
    "work_order_id": re.compile(r"WO-[0-9a-f]{8,64}\Z"),
    "artifact_id": re.compile(r"ART-[0-9a-f]{8,64}\Z"),
    "response_trace_id": re.compile(r"TRC-[0-9a-f]{8,64}\Z"),
}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_STATUSES = frozenset({
    "ok", "pass", "fail", "fail_current", "error", "blocked", "rejected", "pending",
    "created", "existing", "imported", "recorded", "planned", "continued", "approved",
    "action_required", "migration_required", "mount_required", "not_supported", "not_modified",
    "production_preflight_pass", "production_preflight_fail", "draft_preflight_pass", "draft_preflight_fail",
    "authoring_rejected", "saved_pending_validation", "diagnostic_complete",
    "diagnostic_completed", "diagnostic_started", "diagnostic_checkpointed",
    "migrated", "already_migrated", "already_current", "rolled_back", "migration_recovered",
    "migration_plan_ready", "handoffs_recovered", "handoff_recovery_plan_ready",
    "preflight_evidence_reissued", "preflight_evidence_reissue_plan_ready",
    "coverage_pending", "audit_obligations", "audit_evidence",
})


def _literal_prefix(node: ast.AST) -> str | None:
    # Only inspect the static left edge; never evaluate a name, call, formatted
    # value, or any other expression containing runtime/user-controlled data.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_prefix(node.left)
    if isinstance(node, ast.JoinedStr):
        return _literal_prefix(node.values[0]) if node.values else None
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


@lru_cache(maxsize=1)
def known_error_codes() -> frozenset[str]:
    """Allow only literal prefixes of committed server raise statements.

    This is a cached inventory of code, never a scan of user data. A new/unknown
    exception falls back to a generic type rather than leaking its message.
    """
    codes: set[str] = set()
    for path in Path(__file__).parent.glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call) or not node.exc.args:
                continue
            value = _literal_prefix(node.exc.args[0])
            if value is not None:
                match = _CODE.match(value)
                if match:
                    codes.add(match.group(1))
    return frozenset(codes)


def _safe_code(message: Any, name: str) -> str | None:
    if not isinstance(message, str):
        return None
    # The SDK wraps exceptions in this fixed prefix. Strip only this exact tool.
    prefix = f"Error executing tool {name}: "
    if message.startswith(prefix):
        message = message[len(prefix):]
    match = _CODE.match(message[:256])
    return match.group(1) if match and match.group(1) in known_error_codes() else None


def _safe_id(value: Any, field: str) -> str | None:
    return value if isinstance(value, str) and _IDS[field].fullmatch(value) else None


def _error_type(exc: Exception) -> str:
    # Do not serialize arbitrary exception class names or their repr/args.
    for cls in (PermissionError, FileNotFoundError, TimeoutError, ValueError,
                TypeError, ConnectionError, OSError, RuntimeError):
        if isinstance(exc, cls):
            return cls.__name__
    return "Exception"


def call_metadata(arguments: dict[str, Any], service: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "trace_id": f"ACC-{uuid.uuid4().hex}",
        "plugin_version": __version__ if _VERSION.fullmatch(__version__) else None,
    }
    for field in ("project_id", "work_order_id", "artifact_id"):
        value[field] = _safe_id(arguments.get(field), field)
    for field, attribute in (("rules_fingerprint", "_ruleset_fingerprint"),
                             ("runtime_fingerprint", "_runtime_fingerprint")):
        candidate = getattr(service, attribute, None)
        value[field] = candidate if isinstance(candidate, str) and _HASH.fullmatch(candidate) else None
    return value


def result_metadata(result: Any, name: str) -> dict[str, Any]:
    data: dict[str, Any] = {"stage": "tool_result"}
    envelope = getattr(result, "structured_content", None)
    if not isinstance(envelope, dict):
        envelope = {}
    for field in ("project_id", "work_order_id", "artifact_id"):
        data[field] = _safe_id(envelope.get(field), field)
    data["response_trace_id"] = _safe_id(envelope.get("trace_id"), "response_trace_id")
    status = envelope.get("status")
    data["business_status"] = status if isinstance(status, str) and status.lower() in _STATUSES else None
    if getattr(result, "is_error", False):
        code = _safe_code(envelope.get("error_code"), name)
        if code is None:
            for block in getattr(result, "content", ()):
                if getattr(block, "type", None) == "text":
                    code = _safe_code(getattr(block, "text", None), name)
                    if code:
                        break
        data["error_code"] = code or "tool_result_error"
    return data


def exception_metadata(exc: Exception, name: str, *, stage: str) -> dict[str, Any]:
    # Follow SDK causes to identify the underlying class without saving its text.
    current = exc
    seen: set[int] = set()
    code = None
    for _ in range(8):
        if id(current) in seen:
            break
        seen.add(id(current))
        try:
            message = str(current)
        except Exception:
            message = None
        code = code or _safe_code(message, name)
        if not isinstance(current.__cause__, Exception):
            break
        current = current.__cause__
    return {"stage": stage, "error_code": code or "unexpected_tool_exception",
            "error_type": _error_type(current)}


def reason_for(metadata: dict[str, Any], outcome: str) -> str:
    """A small backward-compatible reason; never includes payload or raw error."""
    return json.dumps({key: metadata[key] for key in
                       ("trace_id", "stage", "error_code", "error_type", "business_status")
                       if metadata.get(key) is not None} | {"tool_outcome": outcome},
                      ensure_ascii=True, separators=(",", ":"))
