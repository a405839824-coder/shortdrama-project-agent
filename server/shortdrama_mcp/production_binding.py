"""Server-observed production context, separate from content/audit authority.

These helpers are for new production admission only.  They must not be run
while reading or auditing a historical artifact: observing today's runtime
does not establish the runtime that originally generated that artifact.

The resulting binding is a sidecar record, never a source lock or an audit
certificate.  The caller remains responsible for authorization, immutable
persistence and the existing source, production and independent-audit gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any


BINDING_CONTRACT = "server-production-context-v1"
_AUTOFILL_FIELDS = ("project_version", "workflow_profile")
_CONTEXT_FIELDS = (
    "project_version", "workflow_profile", "schema_version", "schema_profile",
    "plugin_version", "ruleset_version", "ruleset_sha256", "validator_version",
    "validator_sha256", "schema_sha256", "runtime_version", "runtime_sha256",
    "output_contract", "target_model", "aspect_ratio",
)
_FINGERPRINT_FIELDS = ("ruleset_sha256", "validator_sha256", "schema_sha256", "runtime_sha256")
_SOURCE_ALIASES = (
    ("artifact_id", ("source_artifact_id", "authoritative_source_artifact_id")),
    ("bytes_sha256", ("source_fingerprint", "authoritative_source_fingerprint")),
    ("role", ("source_role", "authoritative_source_role")),
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _present_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.strip().casefold() != "unknown"


def _check_declarations(candidate: Mapping[str, Any], server_context: Mapping[str, Any]) -> None:
    """Reject declarations, not missing evidence, that conflict with this run."""
    for field in _CONTEXT_FIELDS:
        if field not in candidate:
            continue
        expected = server_context.get(field)
        if not _present_string(expected):
            # An unavailable server value must not confirm a caller assertion.
            if field in _AUTOFILL_FIELDS:
                raise ValueError(f"production_context_unavailable: {field}")
            continue
        actual = candidate[field]
        if not isinstance(actual, str) or actual != expected:
            raise ValueError(f"production_context_conflict: {field}={actual!r}, expected {expected!r}")
    lock = candidate.get("source_lock")
    if isinstance(lock, Mapping):
        # These names declare runtime/output identity.  Free-form source
        # metadata such as latest_version is intentionally not interpreted as
        # a claim about the plugin version.
        for field in ("plugin_version", "ruleset_version", "ruleset_sha256", "validator_version",
                      "validator_sha256", "runtime_version", "runtime_sha256", "schema_sha256",
                      "output_contract", "target_model", "aspect_ratio"):
            if field not in lock or not _present_string(server_context.get(field)):
                continue
            if lock[field] != server_context[field]:
                raise ValueError(f"production_context_conflict: source_lock.{field}={lock[field]!r}, "
                                 f"expected {server_context[field]!r}")


def prepare_new_candidate_context(
    candidate: dict[str, Any], *, server_context: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fill only absent server-owned version labels for a NEW candidate.

    Empty, malformed or explicitly stale values are conflicts.  In particular,
    this does not create source_lock, prevention_evidence, coverage or schema
    declarations, or upgrade an existing source/output contract in place.
    """
    if not isinstance(candidate, dict):
        raise ValueError("production_candidate_invalid: candidate must be an object")
    if not isinstance(server_context, Mapping):
        raise ValueError("production_context_invalid: server context must be an object")
    _check_declarations(candidate, server_context)
    prepared = copy.deepcopy(candidate)
    changes: list[dict[str, Any]] = []
    for field in _AUTOFILL_FIELDS:
        expected = server_context.get(field)
        if not _present_string(expected):
            raise ValueError(f"production_context_unavailable: {field}")
        if field not in candidate:
            prepared[field] = expected
            changes.append({"path": field, "before_present": False, "before": None,
                            "after": expected, "reason": "bind_current_server_version",
                            "authority": "current_server_runtime"})
    return prepared, changes


def _source_value(field: str, value: str) -> str:
    if field == "bytes_sha256":
        return value.strip().casefold()
    if field == "role":
        return value.strip().casefold().removeprefix("source:")
    return value.strip()


def global_missing_evidence_key(project_id: str, scope_key: str, path: str) -> str:
    """A scope-level missing-evidence family, not a content-version identity.

    Retry/round/runtime is deliberately absent.  Per-version displays must
    additionally use the observation's input/prepared hashes; this key does
    not claim that two different manuscripts are the same file/version.
    """
    return "PMISS-" + _sha({"contract": BINDING_CONTRACT, "project_id": project_id,
                            "scope_key": scope_key, "path": path})[:24]


def build_production_binding(
    candidate: dict[str, Any], *, project_id: str, server_context: Mapping[str, Any],
    source_artifact: Mapping[str, Any] | None = None, source_bytes: bytes | None = None,
    scope_key: str = "",
) -> dict[str, Any]:
    """Bind actual server state and imported source bytes without inventing proof.

    ``source_artifact`` must come from ProjectStore, never the candidate.  When
    present, its bytes are mandatory and its stored fingerprint must match.
    Missing source declarations remain visible even though the sidecar records
    the selected source.  A declared conflicting source is rejected.
    """
    if not isinstance(candidate, dict) or not isinstance(server_context, Mapping):
        raise ValueError("production_binding_invalid: candidate and server context must be objects")
    _check_declarations(candidate, server_context)
    server = {key: copy.deepcopy(server_context.get(key)) for key in _CONTEXT_FIELDS}
    missing: list[str] = []
    for field in ("project_version", "workflow_profile", "ruleset_version", *_FINGERPRINT_FIELDS):
        if not _present_string(server.get(field)):
            missing.append("server." + field)
    source: dict[str, Any] = {"status": "not_registered", "artifact_id": None,
                              "role": None, "bytes_sha256": None,
                              "stored_fingerprint": None, "bytes_verified": False}
    if source_artifact is not None:
        if not isinstance(source_artifact, Mapping) or not _present_string(source_artifact.get("artifact_id")):
            raise ValueError("production_source_invalid: server artifact ID is required")
        if not isinstance(source_bytes, bytes):
            raise ValueError("production_source_bytes_required: selected source must be read from storage")
        actual_sha = hashlib.sha256(source_bytes).hexdigest()
        stored_sha = source_artifact.get("fingerprint")
        if not isinstance(stored_sha, str) or stored_sha.casefold() != actual_sha:
            raise ValueError("production_source_identity_conflict: stored fingerprint does not match actual source bytes")
        role = str(source_artifact.get("artifact_type") or source_artifact.get("source_role") or "")
        source.update(status="bytes_verified", artifact_id=source_artifact["artifact_id"],
                      role=role.removeprefix("source:") or None, bytes_sha256=actual_sha,
                      stored_fingerprint=stored_sha, bytes_verified=True)
        if not source["role"]:
            missing.append("selected_source.role")
    elif source_bytes is not None:
        raise ValueError("production_source_identity_required: bytes without server artifact record")
    else:
        missing.extend(["selected_source.artifact_id", "selected_source.bytes_sha256"])

    lock = candidate.get("source_lock")
    if not isinstance(lock, dict):
        missing.append("source_lock")
        lock = {}
    for field in ("output_contract", "target_model", "aspect_ratio"):
        if not _present_string(lock.get(field)):
            missing.append("source_lock." + field)
    for target, aliases in _SOURCE_ALIASES:
        declarations = []
        for alias in aliases:
            if alias not in lock:
                continue
            value = lock[alias]
            if not _present_string(value):
                raise ValueError(f"production_source_claim_invalid: source_lock.{alias}")
            declarations.append((alias, _source_value(target, value)))
        if len({value for _alias, value in declarations}) > 1:
            raise ValueError(f"production_source_claim_conflict: {aliases[0]} aliases disagree")
        expected = source.get(target)
        for alias, value in declarations:
            if not isinstance(expected, str) or value != _source_value(target, expected):
                raise ValueError(f"production_source_claim_conflict: source_lock.{alias} differs from server source")
        if not declarations:
            missing.append("source_lock." + aliases[0])
    for field in ("coverage", "prevention_evidence"):
        if not isinstance(candidate.get(field), dict) or not candidate[field]:
            missing.append(field)
    missing = sorted(set(missing))
    result = {
        "contract": BINDING_CONTRACT, "project_id": project_id, "scope_key": scope_key,
        "authority": "server_observed_context_only", "server": server, "source": source,
        "missing_evidence": missing,
        "global_missing_evidence": [
            {"identity": global_missing_evidence_key(project_id, scope_key, path),
             "path": path, "category": "evidence_missing", "scope": "file_or_request",
             "content_defect": False, "is_new_content_defect": False}
            for path in missing
        ],
        "source_lock_created": False, "independent_content_coverage_verified": False,
        "coverage_verification_state": "not_performed_by_context_binding",
        "historical_evidence_repaired": False, "model_first_draft_verified": False,
    }
    result["binding_sha256"] = _sha(result)
    return result


def candidate_observation_fields(
    raw_candidate: dict[str, Any], *, prepared_candidate: dict[str, Any],
    production_binding: Mapping[str, Any], changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return immutable input evidence before any context/literal normalization.

    ``changes`` must contain both version-context and literal-normalization
    changes.  Canonical/CAS transformations are separately fingerprinted by
    the service; this helper records JSON identity at the preparation boundary.
    """
    if not isinstance(raw_candidate, dict) or not isinstance(prepared_candidate, dict):
        raise ValueError("production_observation_invalid: candidates must be objects")
    if not isinstance(changes, list) or not all(isinstance(row, dict) for row in changes):
        raise ValueError("production_observation_invalid: changes must be a list of objects")
    if raw_candidate != prepared_candidate and not changes:
        raise ValueError("production_observation_changes_missing: changed candidate requires a change record")
    return {
        "candidate_snapshot": copy.deepcopy(raw_candidate),
        "input_sha256": _sha(raw_candidate),
        "prepared_candidate_sha256": _sha(prepared_candidate),
        "preparation_changes": copy.deepcopy(changes),
        "production_binding": copy.deepcopy(dict(production_binding)),
        "measurement_basis": "prepared_candidate_machine_preflight",
        "raw_generation_quality": None,
        "raw_generation_state": "generation_provenance_unverified",
        "model_first_draft_verified": False,
        "model_settings_state": "not_observed_by_server",
    }
