#!/usr/bin/env python3
"""Deterministic, safe cross-window issue ledger for the R10 storyboard Agent."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "R10-AGENT-SAFE-1"
RUNTIME_VERSION = "V16.3-148-R10-StableRuntime"
WORKFLOW_REVISION = "R10-PG1"
AUDIT_CATEGORIES = [
    "源文对白",
    "机位可见性",
    "空间连续",
    "表演链",
    "镜头节奏",
    "正式母版单镜执行完整性",
    "禁项与声音",
    "统计闭合",
]
LEGACY_AUDIT_CATEGORY_ALIASES = {
    "Seedance独立执行": "正式母版单镜执行完整性",
}
CURRENT_SEMANTIC_AUDIT_SHA256_FIELDS = (
    "semantic_audit_sha256",
    "semantic_report_input_sha256",
)
TASK_TYPES = {
    "production_draft_preflight",
    "generation_audit",
    "audit",
    "audit_repair",
    "repair_regression",
    "bootstrap_import",
}
RESULTS = {"pass", "fail_current", "blocked"}
SEVERITIES = {"fail", "warn", "info", "blocking", "error"}
ISSUE_STATUS_ALIASES = {
    "initial": "initial",
    "初审发现": "initial",
    "residual": "residual",
    "修正后残留": "residual",
    "introduced": "introduced",
    "修正新增": "introduced",
    "missed_in_prior_audit": "missed_in_prior_audit",
    "rule_changed": "rule_changed",
    "attribution_pending": "attribution_pending",
}

INDEPENDENT_AUDIT_REQUIRED_FIELDS = (
    "validation_role",
    "handoff_id",
    "artifact_semantic_fingerprint",
    "canonical_fingerprint",
    "source_fingerprint",
    "boundary_fingerprint",
    "runtime_fingerprint",
    "rules_fingerprint",
    "validator_fingerprint",
    "runtime_test_certificate_id",
    "stage_trace_id",
    *CURRENT_SEMANTIC_AUDIT_SHA256_FIELDS,
    "cache_evidence",
    "independence_assertions",
)
INDEPENDENT_ASSERTIONS = (
    "artifact_read_only",
    "production_verdict_not_reused",
    "quality_conclusions_recomputed",
    "eight_layers_completed",
)
FAILED_BINDING_FIELDS = (
    "file_fingerprint",
    "source_version",
    "artifact_semantic_fingerprint",
    "canonical_fingerprint",
)
SHA256_FIELDS = (
    "artifact_semantic_fingerprint",
    "canonical_fingerprint",
    "source_fingerprint",
    "boundary_fingerprint",
    "runtime_fingerprint",
    "rules_fingerprint",
    "validator_fingerprint",
    *CURRENT_SEMANTIC_AUDIT_SHA256_FIELDS,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: str | None) -> str:
    return value or utc_now()


def _stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _normalize(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+", "", text)


def _identity(value: Any) -> str:
    """Compare persisted identifiers without whitespace/case bypasses."""

    return str(value or "").strip().casefold()


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _checkpoint_scope_boundaries(
    ledger: dict[str, Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    boundaries: dict[str, dict[str, Any]] = {}
    last_rounds: dict[str, int] = {}
    legacy_to_canonical: dict[str, str] = {}
    for position, event in enumerate(events, 1):
        expected_scope = _scope_key(ledger, event)
        legacy_scope = str(event.get("scope_key") or "")
        if not _is_canonical_identifier(legacy_scope):
            raise ValueError(
                f"checkpoint event {event.get('event_id')} has no canonical persisted scope_key"
            )
        mapped_scope = legacy_to_canonical.setdefault(legacy_scope, expected_scope)
        if mapped_scope != expected_scope:
            raise ValueError(
                f"checkpoint legacy scope {legacy_scope} maps to multiple canonical scopes"
            )
        audit_round = event.get("audit_round")
        if isinstance(audit_round, bool) or not isinstance(audit_round, int):
            raise ValueError(
                f"checkpoint event {event.get('event_id')} has a non-integer audit_round"
            )
        previous_round = last_rounds.get(expected_scope)
        if previous_round is not None and audit_round != previous_round + 1:
            raise ValueError(
                f"checkpoint scope {expected_scope} has a non-contiguous audit_round"
            )
        last_rounds[expected_scope] = audit_round
        boundary = boundaries.get(expected_scope)
        if boundary is None:
            boundary = {
                "scope_key": expected_scope,
                "legacy_scope_key": legacy_scope,
                "first_position": position,
                "last_position": position,
                "event_count": 1,
                "first_round": audit_round,
                "last_round": audit_round,
                "last_event_id": event.get("event_id"),
            }
            boundaries[expected_scope] = boundary
        else:
            if boundary.get("legacy_scope_key") != legacy_scope:
                raise ValueError(
                    f"checkpoint canonical scope {expected_scope} has multiple legacy aliases"
                )
            boundary["last_position"] = position
            boundary["event_count"] += 1
            boundary["last_round"] = audit_round
            boundary["last_event_id"] = event.get("event_id")
    return sorted(boundaries.values(), key=lambda row: int(row["first_position"]))


def _build_replay_checkpoint(
    ledger: dict[str, Any],
    *,
    migration_id: str,
    old_ledger_canonical_sha256: str,
    timestamp: str | None,
) -> dict[str, Any]:
    events = ledger.get("events", [])
    if not isinstance(events, list) or not events or any(
        not isinstance(event, dict) for event in events
    ):
        raise ValueError("a replay checkpoint requires one or more historical event objects")
    if not _is_canonical_identifier(migration_id):
        raise ValueError("replay checkpoint requires an explicit signed-journal migration_id")
    for position, event in enumerate(events, 1):
        if event.get("sequence") != position:
            raise ValueError(
                f"checkpoint event {event.get('event_id')} has a non-append-only sequence"
            )
    return {
        "contract_version": "R10-LEDGER-REPLAY-CHECKPOINT-1",
        "migration_id": migration_id,
        "old_ledger_canonical_sha256": old_ledger_canonical_sha256,
        "event_count": len(events),
        "events_prefix_canonical_sha256": _canonical_json_sha256(events),
        "issue_histories_canonical_sha256": _canonical_json_sha256([
            issue.get("history", [])
            for issue in ledger.get("issues", [])
            if isinstance(issue, dict)
        ]),
        "occurrence_locations_canonical_sha256": _canonical_json_sha256([
            issue.get("occurrence_locations", [])
            for issue in ledger.get("issues", [])
            if isinstance(issue, dict)
        ]),
        "issue_prefixes": [
            _checkpoint_issue_baseline(issue)
            for issue in ledger.get("issues", [])
            if isinstance(issue, dict)
        ],
        "scope_boundaries": _checkpoint_scope_boundaries(ledger, events),
        "created_at": _timestamp(timestamp),
        "authority_verification": "required_external_signed_cas",
    }


_CHECKPOINT_IMMUTABLE_ISSUE_FIELDS = (
    "dedup_key",
    "pattern_key",
    "error_code",
    "category",
    "logical_file",
    "episode_id",
    "issue_type",
    "field_class",
    "normalized_root_cause",
    "first_seen_at",
)


def _checkpoint_issue_baseline(issue: dict[str, Any]) -> dict[str, Any]:
    """Describe one migrated issue at the sealed event-prefix boundary."""

    return {
        "issue_id": issue.get("issue_id"),
        "history_count": len(issue.get("history", [])),
        "occurrence_location_count": len(issue.get("occurrence_locations", [])),
        "immutable_identity": {
            field: copy.deepcopy(issue.get(field))
            for field in _CHECKPOINT_IMMUTABLE_ISSUE_FIELDS
        },
        "baseline_open_scope_keys": copy.deepcopy(
            issue.get("open_scope_keys", [])
        ),
        "baseline_status": issue.get("status"),
        "baseline_legacy_resolution_requires_reaudit": issue.get(
            "legacy_resolution_requires_reaudit"
        ),
        "baseline_occurrence_count": issue.get("occurrence_count"),
        "baseline_observation_count": issue.get("observation_count"),
        "baseline_introduced_by_repair_count": issue.get(
            "introduced_by_repair_count"
        ),
        "baseline_consecutive_rounds": issue.get("consecutive_rounds"),
        "baseline_max_consecutive_rounds": issue.get(
            "max_consecutive_rounds"
        ),
        "baseline_last_seen_at": issue.get("last_seen_at"),
    }


def _replay_checkpoint_errors(
    ledger: dict[str, Any],
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> list[str]:
    checkpoint = ledger.get("replay_checkpoint")
    if checkpoint is None:
        return []
    if not isinstance(checkpoint, dict):
        return ["replay_checkpoint must be an object"]
    errors: list[str] = []
    required = (
        "contract_version", "migration_id", "old_ledger_canonical_sha256",
        "event_count", "events_prefix_canonical_sha256",
        "issue_histories_canonical_sha256",
        "occurrence_locations_canonical_sha256", "issue_prefixes",
        "scope_boundaries", "created_at",
        "authority_verification",
    )
    for field in required:
        if field not in checkpoint:
            errors.append(f"replay_checkpoint missing field: {field}")
    if checkpoint.get("contract_version") != "R10-LEDGER-REPLAY-CHECKPOINT-1":
        errors.append("replay_checkpoint.contract_version is invalid")
    old_sha = checkpoint.get("old_ledger_canonical_sha256")
    if not _is_sha256(old_sha):
        errors.append("replay_checkpoint.old_ledger_canonical_sha256 must be SHA-256")
    if not _is_canonical_identifier(checkpoint.get("migration_id")):
        errors.append("replay_checkpoint.migration_id must bind an external signed journal")
    if checkpoint.get("authority_verification") != "required_external_signed_cas":
        errors.append("replay_checkpoint must require external signed CAS authority")
    if checkpoint_authorizer is None:
        errors.append("replay_checkpoint has no externally verified migration/CAS authority")
    else:
        try:
            authorized = checkpoint_authorizer(copy.deepcopy(checkpoint)) is True
        except Exception:
            authorized = False
        if not authorized:
            errors.append("replay_checkpoint external migration/CAS authority was rejected")
    if not _validate_iso_timestamp(checkpoint.get("created_at")):
        errors.append("replay_checkpoint.created_at must be ISO-8601")
    events = ledger.get("events", [])
    event_count = checkpoint.get("event_count")
    if (
        isinstance(event_count, bool)
        or not isinstance(event_count, int)
        or event_count < 1
        or not isinstance(events, list)
        or event_count > len(events)
    ):
        errors.append("replay_checkpoint.event_count is outside the event history")
        return errors
    event_prefix = events[:event_count]
    if _identity(checkpoint.get("events_prefix_canonical_sha256")) != _canonical_json_sha256(event_prefix):
        errors.append("replay_checkpoint event-prefix canonical SHA-256 does not match")
    issues_by_id = {
        _identity(issue.get("issue_id")): issue
        for issue in ledger.get("issues", [])
        if isinstance(issue, dict) and _identity(issue.get("issue_id"))
    }
    issue_prefixes = checkpoint.get("issue_prefixes")
    if not isinstance(issue_prefixes, list):
        errors.append("replay_checkpoint.issue_prefixes must be an array")
        issue_prefixes = []
    issue_histories: list[list[Any]] = []
    occurrence_locations: list[list[Any]] = []
    seen_prefix_issue_ids: set[str] = set()
    for index, issue_prefix in enumerate(issue_prefixes, 1):
        if not isinstance(issue_prefix, dict):
            errors.append(f"replay_checkpoint.issue_prefixes[{index}] must be an object")
            continue
        prefix_issue_id = _identity(issue_prefix.get("issue_id"))
        if prefix_issue_id in seen_prefix_issue_ids:
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}] duplicates issue_id"
            )
        seen_prefix_issue_ids.add(prefix_issue_id)
        issue = issues_by_id.get(prefix_issue_id)
        history_count = issue_prefix.get("history_count")
        location_count = issue_prefix.get("occurrence_location_count")
        immutable_identity = issue_prefix.get("immutable_identity")
        baseline_open_scope_keys = issue_prefix.get("baseline_open_scope_keys")
        baseline_status = issue_prefix.get("baseline_status")
        baseline_requires_reaudit = issue_prefix.get(
            "baseline_legacy_resolution_requires_reaudit"
        )
        baseline_count_fields = (
            "baseline_occurrence_count",
            "baseline_observation_count",
            "baseline_introduced_by_repair_count",
            "baseline_consecutive_rounds",
            "baseline_max_consecutive_rounds",
        )
        if (
            issue is None
            or isinstance(history_count, bool)
            or not isinstance(history_count, int)
            or history_count < 0
            or isinstance(location_count, bool)
            or not isinstance(location_count, int)
            or location_count < 0
            or len(issue.get("history", [])) < history_count
            or len(issue.get("occurrence_locations", [])) < location_count
        ):
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}] does not bind an available immutable prefix"
            )
            continue
        if (
            not isinstance(immutable_identity, dict)
            or set(immutable_identity) != set(_CHECKPOINT_IMMUTABLE_ISSUE_FIELDS)
            or any(
                issue.get(field) != immutable_identity.get(field)
                for field in _CHECKPOINT_IMMUTABLE_ISSUE_FIELDS
            )
        ):
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}] immutable issue identity differs"
            )
        if (
            not isinstance(baseline_open_scope_keys, list)
            or any(not _is_canonical_identifier(value) for value in baseline_open_scope_keys)
            or baseline_open_scope_keys != sorted(set(baseline_open_scope_keys))
        ):
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}].baseline_open_scope_keys is invalid"
            )
        if baseline_status not in {"open", "resolved"}:
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}].baseline_status is invalid"
            )
        if not isinstance(baseline_requires_reaudit, bool):
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}].baseline_legacy_resolution_requires_reaudit must be boolean"
            )
        for field in baseline_count_fields:
            value = issue_prefix.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(
                    f"replay_checkpoint.issue_prefixes[{index}].{field} must be a non-negative integer"
                )
        if not _validate_iso_timestamp(issue_prefix.get("baseline_last_seen_at")):
            errors.append(
                f"replay_checkpoint.issue_prefixes[{index}].baseline_last_seen_at must be ISO-8601"
            )
        issue_histories.append(copy.deepcopy(issue.get("history", [])[:history_count]))
        occurrence_locations.append(
            copy.deepcopy(issue.get("occurrence_locations", [])[:location_count])
        )
    if _identity(checkpoint.get("issue_histories_canonical_sha256")) != _canonical_json_sha256(issue_histories):
        errors.append("replay_checkpoint issue-history canonical SHA-256 does not match")
    if _identity(checkpoint.get("occurrence_locations_canonical_sha256")) != _canonical_json_sha256(occurrence_locations):
        errors.append("replay_checkpoint occurrence-location canonical SHA-256 does not match")
    try:
        expected_boundaries = _checkpoint_scope_boundaries(ledger, event_prefix)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"replay_checkpoint sealed prefix is invalid: {exc}")
    else:
        if checkpoint.get("scope_boundaries") != expected_boundaries:
            errors.append("replay_checkpoint.scope_boundaries do not match the sealed event prefix")
    return errors


def _is_canonical_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\n" not in value
        and "\r" not in value
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Fa-f0-9]{64}", value) is not None


def _project_id(project_name: str) -> str:
    return f"SB-{_stable_hash(_normalize(project_name), 12)}"


def new_ledger(project_name: str, project_id: str | None = None, timestamp: str | None = None) -> dict[str, Any]:
    now = _timestamp(timestamp)
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id or _project_id(project_name),
        "project_name": project_name,
        "runtime_version": RUNTIME_VERSION,
        "workflow_revision": WORKFLOW_REVISION,
        "agent_mode": "safe",
        "created_at": now,
        "updated_at": now,
        "policy": {
            "preferred_auto_repair_rounds": 2,
            "max_auto_repair_rounds": 3,
            "fail_current_blocks_next_episode": True,
            "human_approval_required_for_rule_activation": True,
            "human_signoff_required_for_archive": True,
            "formal_rule_auto_mutation": False,
            "exact_cross_window_claim_requires_shared_ledger": True,
            "pass_requires_zero_issues": True,
            "ledger_replay_contract_version": "R10-LEDGER-REPLAY-1",
            "cross_batch_pattern_identity_required": True,
            "failed_draft_recording_required": True,
            "first_draft_correctness_threshold_percent": 98.0,
            "first_draft_direct_usability_threshold_percent": 98.0,
            "max_new_local_issue_rate_percent": 2.0,
            "minimum_episode_correctness_percent": 98.0,
            "minimum_episode_direct_usability_percent": 98.0,
            "hard_major_known_systemic_tolerance": 0,
        },
        "counters": {
            "event_sequence": 0,
            "event_count": 0,
            "issue_record_count": 0,
            "rule_candidate_count": 0,
        },
        "scope_states": [],
        "events": [],
        "issues": [],
        "rule_candidates": [],
        "promotion_history": [],
        "human_signoffs": [],
        "authority_reopens": [],
        "historical_patterns": [],
    }


def _field_class(value: Any) -> str:
    """Normalize a location to a reusable semantic field, not one shot cell."""

    text = str(value or "全表")
    text = re.sub(r"shots\[\d+\]", "shots[]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bEP\d{1,4}-\d{1,4}\b", "shot", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\d+\]", "[]", text)
    terminal = text.rsplit(".", 1)[-1]
    return _normalize(terminal) or "scope"


def _normalized_root_cause(issue: dict[str, Any]) -> str:
    value = (
        issue.get("normalized_root_cause")
        or issue.get("problem_key")
        or issue.get("root_cause")
        or issue.get("issue_type")
        or issue.get("category")
        or "unknown"
    )
    return _normalize(value) or "unknown"


def issue_pattern_key(
    project_id: str,
    issue: dict[str, Any],
) -> str:
    """Identify one reusable defect pattern across files, episodes and shots."""

    value = "|".join(
        [
            str(project_id),
            _normalize(issue.get("error_code")),
            _field_class(
                issue.get("field_class")
                or issue.get("field")
                or issue.get("location")
            ),
            _normalized_root_cause(issue),
        ]
    )
    return f"PATTERN-{_stable_hash(value, 24)}"


def migrate_ledger_to_current(
    ledger: dict[str, Any],
    *,
    timestamp: str | None = None,
    migration_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return a history-preserving candidate with cross-batch pattern identity."""

    if not isinstance(ledger, dict):
        raise ValueError("ledger must be an object")
    old_ledger_canonical_sha256 = _canonical_json_sha256(ledger)
    migrated = copy.deepcopy(ledger)
    changed: list[str] = []
    pending_checkpoint = None
    if migrated.get("events") and "replay_checkpoint" not in migrated:
        if not _is_canonical_identifier(migration_id):
            raise ValueError(
                "historical event migration requires the signed-journal migration_id"
            )
        pending_checkpoint = _build_replay_checkpoint(
            migrated,
            migration_id=str(migration_id),
            old_ledger_canonical_sha256=old_ledger_canonical_sha256,
            timestamp=timestamp,
        )
    defaults = new_ledger(
        str(migrated.get("project_name") or "迁移项目"),
        project_id=str(migrated.get("project_id") or "SB-migration"),
        timestamp=timestamp,
    )
    for key in ("schema_version", "runtime_version", "workflow_revision", "agent_mode"):
        if migrated.get(key) != defaults[key]:
            migrated[key] = defaults[key]
            changed.append(key)
    policy = migrated.setdefault("policy", {})
    for key, value in defaults["policy"].items():
        if policy.get(key) != value:
            policy[key] = copy.deepcopy(value)
            changed.append(f"policy.{key}")
    migrated.setdefault("authority_reopens", [])
    migrated.setdefault("historical_patterns", [])
    project_id = str(migrated.get("project_id") or "")
    for index, record in enumerate(migrated.setdefault("issues", [])):
        if not isinstance(record, dict):
            continue
        history_rows = [
            row for row in record.get("history", []) if isinstance(row, dict)
        ]
        if not history_rows:
            raise ValueError(
                f"legacy issue {record.get('issue_id') or index} has no immutable history"
            )
        latest_history = next(
            (
                row
                for row in reversed(history_rows)
                if row.get("field")
                or row.get("root_cause")
                or row.get("issue_type")
            ),
            {},
        )
        legacy_defaults = {
            "logical_file": str(record.get("logical_file") or latest_history.get("file_name") or "legacy-issue"),
            "episode_id": str(record.get("episode_id") or latest_history.get("episode_id") or "legacy"),
            "issue_type": str(record.get("issue_type") or record.get("category") or "legacy_issue"),
            "recurrence_state": "first_occurrence",
            "introduced_by_repair_count": 0,
            "first_seen_at": str(
                record.get("first_seen_at")
                or next((row.get("timestamp") for row in history_rows if row.get("timestamp")), None)
                or migrated.get("created_at")
                or _timestamp(timestamp)
            ),
            "last_seen_at": str(
                record.get("last_seen_at")
                or next((row.get("timestamp") for row in reversed(history_rows) if row.get("timestamp")), None)
                or migrated.get("updated_at")
                or _timestamp(timestamp)
            ),
            "root_cause_gate": "not_triggered",
            "repair_strategy": "local_repair",
        }
        for field, value in legacy_defaults.items():
            if field not in record or record[field] in (None, ""):
                record[field] = value
                changed.append(f"issues[{index}].{field}")
        if not record.get("field_class"):
            record["field_class"] = _field_class(
                record.get("field") or latest_history.get("field")
            )
            changed.append(f"issues[{index}].field_class")
        if not record.get("normalized_root_cause"):
            record["normalized_root_cause"] = _normalized_root_cause(
                {
                    **record,
                    "root_cause": record.get("root_cause")
                    or latest_history.get("root_cause"),
                }
            )
            changed.append(f"issues[{index}].normalized_root_cause")
        pattern = issue_pattern_key(project_id, record)
        if record.get("pattern_key") != pattern:
            record["pattern_key"] = pattern
            changed.append(f"issues[{index}].pattern_key")
        locations_existed = isinstance(record.get("occurrence_locations"), list)
        if not locations_existed:
            raise ValueError(
                f"legacy issue {record.get('issue_id') or index} has no immutable occurrence_locations"
            )
        locations = record["occurrence_locations"]
        observation_count = int(record.get("occurrence_count") or 0)
        if observation_count <= 0:
            observation_count = sum(
                bool(row.get("issue_status"))
                for row in history_rows
            ) or 1
            record["occurrence_count"] = observation_count
            changed.append(f"issues[{index}].occurrence_count")
        if record.get("observation_count") != observation_count:
            record["observation_count"] = observation_count
            changed.append(f"issues[{index}].observation_count")
        distinct_event_count = len(
            {
                str(row.get("event_id"))
                for row in locations
                if isinstance(row, dict) and row.get("event_id")
            }
        )
        distinct_scope_count = len(
            {
                str(row.get("scope_key"))
                for row in locations
                if isinstance(row, dict) and row.get("scope_key")
            }
        )
        if record.get("distinct_event_count") != distinct_event_count:
            record["distinct_event_count"] = distinct_event_count
            changed.append(f"issues[{index}].distinct_event_count")
        if record.get("distinct_scope_count") != distinct_scope_count:
            record["distinct_scope_count"] = distinct_scope_count
            changed.append(f"issues[{index}].distinct_scope_count")
    records_by_pattern: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(migrated.get("issues", [])):
        if isinstance(record, dict) and record.get("pattern_key"):
            records_by_pattern.setdefault(str(record["pattern_key"]), []).append((index, record))
    for indexed_pattern_records in records_by_pattern.values():
        pattern_records = [record for _, record in indexed_pattern_records]
        pattern_occurrence_count = len(pattern_records)
        pattern_observation_count = sum(
            int(record.get("observation_count", record.get("occurrence_count", 0)))
            for record in pattern_records
        )
        for index, record in indexed_pattern_records:
            if record.get("pattern_occurrence_count") != pattern_occurrence_count:
                record["pattern_occurrence_count"] = pattern_occurrence_count
                changed.append(f"issues[{index}].pattern_occurrence_count")
            if record.get("pattern_observation_count") != pattern_observation_count:
                record["pattern_observation_count"] = pattern_observation_count
                changed.append(f"issues[{index}].pattern_observation_count")
    _migrate_ledger_control_replay(migrated, changed)
    if pending_checkpoint is not None:
        # Control replay establishes the authoritative post-migration issue
        # baseline (canonical scope aliases, open scopes and legacy re-audit
        # state).  Seal that baseline only after replay; the history/location
        # arrays themselves remain the byte-identical old prefix hashed above.
        pending_checkpoint["issue_prefixes"] = [
            _checkpoint_issue_baseline(issue)
            for issue in migrated.get("issues", [])
            if isinstance(issue, dict)
        ]
        migrated["replay_checkpoint"] = pending_checkpoint
        changed.append("replay_checkpoint")
    if changed:
        migrated["updated_at"] = _timestamp(timestamp)
        changed.append("updated_at")
    return migrated, sorted(set(changed))


def _migrate_ledger_control_replay(
    ledger: dict[str, Any], changed: list[str]
) -> None:
    """Bind legacy control records without granting them independent authority."""

    events = ledger.setdefault("events", [])
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        raise ValueError("legacy ledger events must be an array of objects")
    original_scope_by_event: dict[str, str] = {}
    event_by_id: dict[str, dict[str, Any]] = {}
    event_position: dict[str, int] = {}
    scope_rounds: dict[str, int] = {}
    for position, event in enumerate(events, 1):
        event_id = _identity(event.get("event_id"))
        if not event_id or not _is_canonical_identifier(event.get("event_id")):
            raise ValueError(f"legacy event at position {position} has no canonical event_id")
        if event_id in event_by_id:
            raise ValueError(f"legacy event identity collision: {event.get('event_id')}")
        event_by_id[event_id] = event
        event_position[event_id] = position
        original_scope_by_event[event_id] = str(event.get("scope_key") or "")
        if event.get("result") == "pass" and event.get("blockers"):
            raise ValueError(
                f"legacy pass event {event.get('event_id')} contains blockers and cannot be promoted"
            )
        scope_key = _scope_key(ledger, event)
        if event.get("sequence") != position:
            raise ValueError(
                f"legacy event {event.get('event_id')} has a non-append-only sequence"
            )
        previous_round = scope_rounds.get(scope_key)
        if previous_round is not None and event.get("audit_round") != previous_round + 1:
            raise ValueError(
                f"legacy event {event.get('event_id')} has a non-contiguous audit_round"
            )
        scope_rounds[scope_key] = int(event.get("audit_round"))

    old_scopes = [
        row for row in ledger.get("scope_states", []) if isinstance(row, dict)
    ]
    old_scope_last_event = {
        str(row.get("scope_key") or ""): _identity(row.get("last_event_id"))
        for row in old_scopes
        if row.get("scope_key") and row.get("last_event_id")
    }

    def resolve_legacy_basis(record: dict[str, Any], record_name: str) -> str:
        supplied = _identity(record.get("basis_event_id"))
        if supplied:
            if supplied not in event_by_id:
                raise ValueError(f"{record_name} basis_event_id is unknown")
            return supplied
        old_scope_key = str(record.get("scope_key") or "")
        timestamp = record.get("timestamp")
        if not _validate_iso_timestamp(timestamp):
            raise ValueError(f"{record_name} timestamp is not ISO-8601")
        candidates = [
            event_id
            for event_id, event in event_by_id.items()
            if original_scope_by_event.get(event_id) == old_scope_key
            and _validate_iso_timestamp(event.get("timestamp"))
            and _parse_iso_timestamp(str(event.get("timestamp")))
            <= _parse_iso_timestamp(str(timestamp))
        ]
        last_event_id = old_scope_last_event.get(old_scope_key, "")
        if last_event_id in candidates:
            return last_event_id
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise ValueError(f"{record_name} cannot be uniquely bound to a historical event")
        raise ValueError(
            f"{record_name} has multiple historical event candidates and no provable scope last_event"
        )

    human_signoffs = ledger.setdefault("human_signoffs", [])
    if not isinstance(human_signoffs, list):
        raise ValueError("legacy human_signoffs must be an array")
    for index, record in enumerate(human_signoffs):
        if not isinstance(record, dict):
            raise ValueError(f"human_signoffs[{index}] must be an object")
        was_unbound = not record.get("basis_event_id")
        basis_id = resolve_legacy_basis(record, f"human_signoffs[{index}]")
        basis_event = event_by_id[basis_id]
        expected_values = {
            "scope_key": _scope_key(ledger, basis_event),
            "basis_event_id": basis_event["event_id"],
            "authority_class": (
                "legacy" if was_unbound else record.get("authority_class", "legacy")
            ),
        }
        for field, value in expected_values.items():
            if record.get(field) != value:
                record[field] = value
                changed.append(f"human_signoffs[{index}].{field}")

    authority_reopens = ledger.setdefault("authority_reopens", [])
    if not isinstance(authority_reopens, list):
        raise ValueError("legacy authority_reopens must be an array")
    for index, record in enumerate(authority_reopens):
        if not isinstance(record, dict):
            raise ValueError(f"authority_reopens[{index}] must be an object")
        basis_id = resolve_legacy_basis(record, f"authority_reopens[{index}]")
        basis_event = event_by_id[basis_id]
        if (
            basis_event.get("audit_class") != "independent_audit"
            or basis_event.get("result") != "pass"
        ):
            raise ValueError(
                f"authority_reopens[{index}] cannot bind to a non-independent or failed event"
            )
        for field, value in {
            "scope_key": _scope_key(ledger, basis_event),
            "basis_event_id": basis_event["event_id"],
        }.items():
            if record.get(field) != value:
                record[field] = value
                changed.append(f"authority_reopens[{index}].{field}")

    human_by_basis: dict[str, list[dict[str, Any]]] = {}
    for record in human_signoffs:
        human_by_basis.setdefault(_identity(record.get("basis_event_id")), []).append(record)
    authority_by_basis: dict[str, list[dict[str, Any]]] = {}
    for record in authority_reopens:
        authority_by_basis.setdefault(_identity(record.get("basis_event_id")), []).append(record)

    replay: dict[str, dict[str, Any]] = {}
    for event in events:
        scope_key = _scope_key(ledger, event)
        normalized_issues = [
            _normalize_issue(issue)
            for issue in event.get("issues", [])
            if isinstance(issue, dict) and issue.get("status") in ISSUE_STATUS_ALIASES
        ]
        incoming_issue_ids = sorted(
            {
                f"ISS-{_stable_hash(_issue_dedup_key(ledger, event, issue))}"
                for issue in normalized_issues
            }
        )
        closure_state = _event_closure_state(event)
        state = {
            "scope_key": scope_key,
            "episode_id": event.get("episode_id"),
            "file_name": event.get("file_name"),
            "audit_cycle_id": event.get("audit_cycle_id"),
            "last_event_id": event.get("event_id"),
            "last_result": event.get("result"),
            "last_audit_round": event.get("audit_round"),
            "open_issue_ids": incoming_issue_ids,
            "closure_state": closure_state,
            "can_advance": False,
            "human_signoff": None,
        }
        replay[scope_key] = state
        event_id = _identity(event.get("event_id"))
        for record in human_by_basis.get(event_id, []):
            state["human_signoff"] = copy.deepcopy(record)
            if record.get("authority_class") == "legacy":
                state["closure_state"] = (
                    "reopened_by_human"
                    if record.get("decision") == "reopen"
                    else "legacy_signoff_requires_reaudit"
                )
                state["can_advance"] = False
            elif record.get("decision") == "approve":
                state["closure_state"] = "human_signed_off"
                state["can_advance"] = True
            else:
                state["closure_state"] = "reopened_by_human"
                state["can_advance"] = False
        for record in authority_by_basis.get(event_id, []):
            state["closure_state"] = "reopened_by_independent_reaudit"
            state["can_advance"] = False
            state["authority_reopen"] = copy.deepcopy(record)

    rebuilt_scopes = list(replay.values())
    if ledger.get("scope_states") != rebuilt_scopes:
        ledger["scope_states"] = rebuilt_scopes
        changed.append("scope_states")

    for issue_index, issue in enumerate(ledger.get("issues", [])):
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("issue_id") or "")
        expected_open_scopes = sorted(
            scope_key
            for scope_key, state in replay.items()
            if issue_id in state.get("open_issue_ids", [])
        )
        if (
            (issue.get("status") == "open" or issue.get("open_scope_keys"))
            and not expected_open_scopes
        ):
            raise ValueError(
                f"issues[{issue_index}] is open but has no replayable event occurrence"
            )
        if issue.get("open_scope_keys") != expected_open_scopes:
            issue["open_scope_keys"] = expected_open_scopes
            changed.append(f"issues[{issue_index}].open_scope_keys")
        expected_status = "open" if expected_open_scopes else "resolved"
        if issue.get("status") != expected_status:
            issue["status"] = expected_status
            changed.append(f"issues[{issue_index}].status")
        last_history_event = next(
            (
                event_by_id.get(_identity(row.get("event_id")))
                for row in reversed(issue.get("history", []))
                if isinstance(row, dict)
                and _identity(row.get("event_id")) in event_by_id
            ),
            None,
        )
        requires_reaudit = not (
            isinstance(last_history_event, dict)
            and last_history_event.get("audit_class") == "independent_audit"
            and last_history_event.get("result") == "pass"
        )
        if issue.get("legacy_resolution_requires_reaudit") != requires_reaudit:
            issue["legacy_resolution_requires_reaudit"] = requires_reaudit
            changed.append(
                f"issues[{issue_index}].legacy_resolution_requires_reaudit"
            )

    counters = ledger.setdefault("counters", {})
    expected_counters = {
        "event_sequence": len(events),
        "event_count": len(events),
        "issue_record_count": len(ledger.get("issues", [])),
        "rule_candidate_count": len(ledger.get("rule_candidates", [])),
    }
    for field, value in expected_counters.items():
        if counters.get(field) != value:
            counters[field] = value
            changed.append(f"counters.{field}")


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json_atomic(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ) is None:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _parse_iso_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def validate_event(event: Any, max_rounds: int = 3) -> list[str]:
    errors: list[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]
    required = [
        "event_id", "timestamp", "episode_id", "file_name", "file_fingerprint",
        "source_version", "audit_cycle_id", "task_type", "audit_round", "result",
        "checked_shot_count", "issues",
    ]
    for field in required:
        if field not in event or event[field] in (None, ""):
            errors.append(f"missing event field: {field}")
    for field in ("episode_id", "file_name", "source_version"):
        if field in event and (
            not isinstance(event.get(field), str) or not event.get(field).strip()
        ):
            errors.append(f"{field} must be a non-empty string")
    for field in ("event_id", "audit_cycle_id"):
        if field in event and not _is_canonical_identifier(event.get(field)):
            errors.append(f"{field} must be a non-empty identifier without leading or trailing whitespace")
    if "file_fingerprint" in event and not _is_canonical_identifier(
        event.get("file_fingerprint")
    ):
        errors.append(
            "file_fingerprint must be a non-empty identifier without leading or trailing whitespace"
        )
    if not _validate_iso_timestamp(event.get("timestamp")):
        errors.append("timestamp must be ISO-8601")
    if event.get("task_type") not in TASK_TYPES:
        errors.append(f"unsupported task_type: {event.get('task_type')!r}")
    round_value = event.get("audit_round")
    # A persisted machine observation is not another authorized automatic
    # repair or an independent audit. Record the real count even when a later
    # explicit repair succeeds after three failures; never reset the history.
    repair_observation = (
        event.get("assessment_contract") == "storyboard-quality-v2"
        and event.get("audit_class") == "production_draft_preflight"
        and event.get("task_type") == "production_draft_preflight"
        and _is_canonical_identifier(event.get("repair_root_artifact_id"))
    )
    if (not isinstance(round_value, int) or isinstance(round_value, bool) or round_value < 1
            or (not repair_observation and round_value > max_rounds)):
        errors.append(f"audit_round must be an integer from 1 to {max_rounds}")
    if event.get("result") not in RESULTS:
        errors.append(f"unsupported result: {event.get('result')!r}")
    checked = event.get("checked_shot_count")
    if not isinstance(checked, int) or isinstance(checked, bool) or checked < 0:
        errors.append("checked_shot_count must be a non-negative integer")
    issues = event.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be an array")
        issues = []
    issue_required = [
        "error_code", "category", "severity", "shot_ids", "field", "evidence",
        "source_sentence", "repair_action", "status",
    ]
    for index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            errors.append(f"issue[{index}] must be an object")
            continue
        for field in issue_required:
            if field not in issue or issue[field] in (None, ""):
                errors.append(f"issue[{index}] missing field: {field}")
        for field in ("error_code", "field", "source_sentence", "repair_action"):
            if field in issue and (
                not isinstance(issue.get(field), str) or not issue.get(field, "").strip()
            ):
                errors.append(f"issue[{index}].{field} must be a non-empty string")
        evidence = issue.get("evidence")
        if "evidence" in issue and not (
            isinstance(evidence, str) and bool(evidence.strip())
        ):
            errors.append(f"issue[{index}].evidence must be a non-empty string")
        category = issue.get("category")
        if category not in AUDIT_CATEGORIES and category not in LEGACY_AUDIT_CATEGORY_ALIASES:
            errors.append(f"issue[{index}] unsupported category: {issue.get('category')!r}")
        if issue.get("severity") not in SEVERITIES:
            errors.append(f"issue[{index}] unsupported severity: {issue.get('severity')!r}")
        if not isinstance(issue.get("shot_ids"), list):
            errors.append(f"issue[{index}].shot_ids must be an array")
        if issue.get("status") not in ISSUE_STATUS_ALIASES:
            errors.append(f"issue[{index}] unsupported status: {issue.get('status')!r}")
        if "blocking" in issue and not isinstance(issue.get("blocking"), bool):
            errors.append(f"issue[{index}].blocking must be boolean")
    blockers = event.get("blockers", [])
    if not isinstance(blockers, list):
        errors.append("blockers must be an array")
        blockers = []
    else:
        for index, blocker in enumerate(blockers, 1):
            if not _is_canonical_identifier(blocker):
                errors.append(
                    f"blockers[{index}] must be a non-empty string without leading or trailing whitespace"
                )
    if event.get("result") == "pass":
        if issues:
            errors.append("result=pass requires issues=[]")
        if blockers:
            errors.append("result=pass requires blockers=[]")
    if event.get("result") in {"fail_current", "blocked"} and not issues and not blockers:
        errors.append("failed or blocked event requires issues or blockers")
    if event.get("audit_class") == "independent_audit":
        for field in INDEPENDENT_AUDIT_REQUIRED_FIELDS:
            if field not in event or event[field] in (None, ""):
                errors.append(f"independent_audit missing field: {field}")
        if event.get("task_type") != "audit":
            errors.append("independent_audit task_type must equal 'audit'")
        if event.get("validation_role") != "independent_audit":
            errors.append("independent_audit validation_role must equal 'independent_audit'")
        for field in ("handoff_id", "runtime_test_certificate_id", "stage_trace_id"):
            if field in event and not _is_canonical_identifier(event.get(field)):
                errors.append(
                    f"{field} must be a non-empty identifier without leading or trailing whitespace"
                )
        for field in SHA256_FIELDS:
            if field in event and not _is_sha256(event.get(field)):
                errors.append(f"{field} must be a 64-character SHA-256 hex string")
        if "cache_evidence" in event and not isinstance(event.get("cache_evidence"), list):
            errors.append("independent_audit cache_evidence must be an array")
        assertions = event.get("independence_assertions")
        if not isinstance(assertions, dict):
            errors.append("independent_audit independence_assertions must be an object")
        else:
            for assertion in INDEPENDENT_ASSERTIONS:
                if assertions.get(assertion) is not True:
                    errors.append(
                        f"independent_audit independence_assertions.{assertion} must equal true"
                    )
        if event.get("result") == "pass" and checked == 0:
            errors.append("independent_audit result=pass requires checked_shot_count >= 1")
    elif event.get("validation_role") == "independent_audit":
        errors.append("validation_role=independent_audit requires audit_class=independent_audit")
    return errors


def validate_ledger(
    ledger: Any,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(ledger, dict):
        return ["ledger must be an object"]
    required = [
        "schema_version", "project_id", "project_name", "runtime_version", "workflow_revision",
        "agent_mode", "created_at", "updated_at", "policy", "counters", "scope_states",
        "events", "issues", "rule_candidates", "promotion_history", "human_signoffs",
        "authority_reopens",
        "historical_patterns",
    ]
    for field in required:
        if field not in ledger:
            errors.append(f"missing ledger field: {field}")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "workflow_revision": WORKFLOW_REVISION,
        "agent_mode": "safe",
    }
    for field, value in expected.items():
        if ledger.get(field) != value:
            errors.append(f"{field} must equal {value!r}")
    policy = ledger.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy must be an object")
        policy = {}
    policy_expected = {
        "preferred_auto_repair_rounds": 2,
        "max_auto_repair_rounds": 3,
        "fail_current_blocks_next_episode": True,
        "human_approval_required_for_rule_activation": True,
        "human_signoff_required_for_archive": True,
        "formal_rule_auto_mutation": False,
        "exact_cross_window_claim_requires_shared_ledger": True,
        "pass_requires_zero_issues": True,
        "ledger_replay_contract_version": "R10-LEDGER-REPLAY-1",
        "cross_batch_pattern_identity_required": True,
        "failed_draft_recording_required": True,
        "first_draft_correctness_threshold_percent": 98.0,
        "first_draft_direct_usability_threshold_percent": 98.0,
        "max_new_local_issue_rate_percent": 2.0,
        "minimum_episode_correctness_percent": 98.0,
        "minimum_episode_direct_usability_percent": 98.0,
        "hard_major_known_systemic_tolerance": 0,
    }
    for field, value in policy_expected.items():
        if (field in {"minimum_episode_correctness_percent", "minimum_episode_direct_usability_percent"}
                and policy.get("minimum_episode_correctness_percent") == 96.0
                and "minimum_episode_direct_usability_percent" not in policy):
            continue
        if policy.get(field) != value:
            errors.append(f"policy.{field} must equal {value!r}")
    errors.extend(_replay_checkpoint_errors(ledger, checkpoint_authorizer))
    events = ledger.get("events")
    if not isinstance(events, list):
        errors.append("events must be an array")
        events = []
    event_ids: set[str] = set()
    checkpoint = ledger.get("replay_checkpoint")
    sealed_event_count = (
        int(checkpoint.get("event_count"))
        if isinstance(checkpoint, dict)
        and isinstance(checkpoint.get("event_count"), int)
        and not isinstance(checkpoint.get("event_count"), bool)
        else 0
    )
    for index, event in enumerate(events, 1):
        event_validation_errors = validate_event(event, max_rounds=3)
        if index <= sealed_event_count:
            sealed_legacy_missing_semantic_bindings = {
                f"independent_audit missing field: {field}"
                for field in CURRENT_SEMANTIC_AUDIT_SHA256_FIELDS
            }
            event_validation_errors = [
                error
                for error in event_validation_errors
                if (
                    error not in sealed_legacy_missing_semantic_bindings
                    and not re.fullmatch(
                        r"issue\[\d+\]\.evidence must be a non-empty string",
                        error,
                    )
                )
            ]
        for error in event_validation_errors:
            errors.append(f"events[{index}]: {error}")
        event_id = _identity(event.get("event_id")) if isinstance(event, dict) else ""
        if event_id in event_ids:
            errors.append(f"duplicate event_id: {event_id}")
        event_ids.add(event_id)
    issues = ledger.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be an array")
        issues = []
    issue_ids: set[str] = set()
    dedup_keys: set[str] = set()
    for index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            errors.append(f"issues[{index}] must be an object")
            continue
        required_issue_fields = [
            "issue_id", "dedup_key", "pattern_key", "error_code", "category",
            "logical_file", "episode_id", "issue_type", "field_class",
            "normalized_root_cause", "status", "open_scope_keys",
            "legacy_resolution_requires_reaudit",
            "recurrence_state", "occurrence_count", "observation_count",
            "pattern_occurrence_count", "pattern_observation_count",
            "distinct_event_count", "distinct_scope_count", "consecutive_rounds",
            "max_consecutive_rounds", "introduced_by_repair_count",
            "first_seen_at", "last_seen_at", "root_cause_gate",
            "repair_strategy", "occurrence_locations", "history",
        ]
        for field in required_issue_fields:
            if field not in issue:
                errors.append(f"issues[{index}] missing field: {field}")
        issue_id = _identity(issue.get("issue_id", ""))
        dedup_key = _identity(issue.get("dedup_key", ""))
        if issue_id in issue_ids:
            errors.append(f"duplicate issue_id: {issue_id}")
        if dedup_key in dedup_keys:
            errors.append(f"duplicate issue dedup_key: {dedup_key}")
        issue_ids.add(issue_id)
        dedup_keys.add(dedup_key)
        for field in [
            "issue_id", "dedup_key", "pattern_key", "error_code", "category",
            "logical_file", "episode_id", "issue_type", "field_class",
            "normalized_root_cause", "first_seen_at", "last_seen_at",
        ]:
            if field in issue and (
                not isinstance(issue.get(field), str) or not issue.get(field).strip()
            ):
                errors.append(f"issues[{index}].{field} must be a non-empty string")
        expected_pattern_key = issue_pattern_key(str(ledger.get("project_id") or ""), issue)
        if issue.get("pattern_key") != expected_pattern_key:
            errors.append(f"issues[{index}].pattern_key does not match its normalized pattern identity")
        if issue.get("status") not in {"open", "resolved"}:
            errors.append(f"issues[{index}].status must be open or resolved")
        if issue.get("recurrence_state") not in {
            "first_occurrence", "consecutive", "recurred_in_parallel_scope",
            "recurred_after_resolution", "reobserved", "repeated_two_rounds",
            "systemic_three_rounds", "systemic_same_draft",
            "cross_batch_recurrence",
        }:
            errors.append(f"issues[{index}].recurrence_state is invalid")
        if issue.get("root_cause_gate") not in {"not_triggered", "recorded", "required"}:
            errors.append(f"issues[{index}].root_cause_gate is invalid")
        if issue.get("repair_strategy") not in {
            "local_repair", "root_cause_repair_required", "upstream_rebuild_required",
        }:
            errors.append(f"issues[{index}].repair_strategy is invalid")
        open_scope_keys = issue.get("open_scope_keys")
        if not isinstance(open_scope_keys, list) or any(
            not _is_canonical_identifier(value) for value in open_scope_keys
        ):
            errors.append(f"issues[{index}].open_scope_keys must be an array of canonical identifiers")
        elif len(open_scope_keys) != len({_identity(value) for value in open_scope_keys}):
            errors.append(f"issues[{index}].open_scope_keys must be unique")
        if issue.get("status") == "resolved" and open_scope_keys:
            errors.append(f"issues[{index}] resolved issue cannot retain open scopes")
        if issue.get("status") == "open" and isinstance(open_scope_keys, list) and not open_scope_keys:
            errors.append(f"issues[{index}] open issue requires at least one open scope")
        count_fields = {
            "occurrence_count": 1,
            "observation_count": 1,
            "pattern_occurrence_count": 1,
            "pattern_observation_count": 1,
            "distinct_event_count": 1,
            "distinct_scope_count": 1,
            "consecutive_rounds": 0,
            "max_consecutive_rounds": 1,
            "introduced_by_repair_count": 0,
        }
        for field, minimum in count_fields.items():
            value = issue.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                errors.append(f"issues[{index}].{field} must be an integer >= {minimum}")
        if (
            isinstance(issue.get("observation_count"), int)
            and isinstance(issue.get("occurrence_count"), int)
            and issue.get("observation_count") != issue.get("occurrence_count")
        ):
            errors.append(f"issues[{index}].observation_count must equal occurrence_count")
        history = issue.get("history")
        if not isinstance(history, list) or not history:
            errors.append(f"issues[{index}].history must be a non-empty array")
            history = []
        locations = issue.get("occurrence_locations")
        if not isinstance(locations, list) or not locations:
            errors.append(f"issues[{index}].occurrence_locations must be a non-empty array")
            locations = []
        required_location_fields = [
            "event_id", "scope_key", "episode_id", "file_name",
            "source_version", "shot_ids", "field",
        ]
        for location_index, location in enumerate(locations, 1):
            if not isinstance(location, dict):
                errors.append(f"issues[{index}].occurrence_locations[{location_index}] must be an object")
                continue
            for field in required_location_fields:
                if field not in location:
                    errors.append(
                        f"issues[{index}].occurrence_locations[{location_index}] missing field: {field}"
                    )
            for field in [
                "event_id", "scope_key", "episode_id", "file_name",
                "source_version", "field",
            ]:
                if field in location and (
                    not isinstance(location.get(field), str) or not location.get(field).strip()
                ):
                    errors.append(
                        f"issues[{index}].occurrence_locations[{location_index}].{field} must be a non-empty string"
                    )
            if "shot_ids" in location and not isinstance(location.get("shot_ids"), list):
                errors.append(
                    f"issues[{index}].occurrence_locations[{location_index}].shot_ids must be an array"
                )
        distinct_event_count = len(
            {
                str(row.get("event_id"))
                for row in locations
                if isinstance(row, dict) and row.get("event_id")
            }
        )
        distinct_scope_count = len(
            {
                str(row.get("scope_key"))
                for row in locations
                if isinstance(row, dict) and row.get("scope_key")
            }
        )
        if issue.get("distinct_event_count") != distinct_event_count:
            errors.append(f"issues[{index}].distinct_event_count does not match history")
        if issue.get("distinct_scope_count") != distinct_scope_count:
            errors.append(f"issues[{index}].distinct_scope_count does not match history")
    patterns: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, issue in enumerate(issues, 1):
        if isinstance(issue, dict) and issue.get("pattern_key"):
            patterns.setdefault(str(issue["pattern_key"]), []).append((index, issue))
    for records in patterns.values():
        expected_occurrences = len(records)
        expected_observations = sum(
            int(issue.get("observation_count", 0)) for _, issue in records
        )
        for index, issue in records:
            if issue.get("pattern_occurrence_count") != expected_occurrences:
                errors.append(f"issues[{index}].pattern_occurrence_count does not match ledger pattern group")
            if issue.get("pattern_observation_count") != expected_observations:
                errors.append(f"issues[{index}].pattern_observation_count does not match ledger pattern group")
    candidates = ledger.get("rule_candidates")
    if not isinstance(candidates, list):
        errors.append("rule_candidates must be an array")
        candidates = []
    candidate_ids: set[str] = set()
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            errors.append(f"rule_candidates[{index}] must be an object")
            continue
        candidate_id = _identity(candidate.get("candidate_id", ""))
        if candidate_id in candidate_ids:
            errors.append(f"duplicate candidate_id: {candidate_id}")
        candidate_ids.add(candidate_id)
        if candidate.get("status") == "ready_for_manual_promotion":
            validation = candidate.get("validation", {})
            decision = candidate.get("human_decision", {})
            checks_ready = (
                validation.get("validator_status") == "pass"
                and validation.get("golden_tests_status") == "pass"
                and validation.get("conflict_check") == "pass"
                and validation.get("all_tests_pass") is True
            )
            if not checks_ready or decision.get("decision") != "approve":
                errors.append(f"candidate {candidate_id} bypasses promotion gates")
    scopes = ledger.get("scope_states")
    if not isinstance(scopes, list):
        errors.append("scope_states must be an array")
        scopes = []
    scope_keys: set[str] = set()
    for index, scope in enumerate(scopes, 1):
        if not isinstance(scope, dict):
            errors.append(f"scope_states[{index}] must be an object")
            continue
        key = _identity(scope.get("scope_key", ""))
        if key in scope_keys:
            errors.append(f"duplicate scope_key: {key}")
        scope_keys.add(key)
        if scope.get("can_advance") is True:
            if scope.get("closure_state") != "human_signed_off" or scope.get("last_result") != "pass" or scope.get("open_issue_ids"):
                errors.append(f"scope {key} can_advance without clean human sign-off")
    errors.extend(_ledger_replay_errors(ledger))
    return errors


def _scope_key(ledger: dict[str, Any], event: dict[str, Any]) -> str:
    value = "|".join([
        str(ledger["project_id"]),
        _normalize(event["episode_id"]),
        _normalize(event["file_name"]),
        _normalize(event["audit_cycle_id"]),
    ])
    return f"SCOPE-{_stable_hash(value)}"


def _independent_freshness_errors(
    prior_events: list[dict[str, Any]],
    event: dict[str, Any],
) -> list[str]:
    """Reject evidence relabeling across rounds, cycles, and logical filenames."""

    if event.get("audit_class") != "independent_audit":
        return []
    errors: list[str] = []
    handoff = _identity(event.get("handoff_id"))
    trace = _identity(event.get("stage_trace_id"))
    failed_values: dict[str, set[str]] = {field: set() for field in FAILED_BINDING_FIELDS}
    for prior in prior_events:
        if not isinstance(prior, dict) or prior.get("audit_class") != "independent_audit":
            continue
        if handoff and handoff == _identity(prior.get("handoff_id")):
            errors.append("independent_audit handoff_id must be globally unique across audit events")
        if trace and trace == _identity(prior.get("stage_trace_id")):
            errors.append("independent_audit stage_trace_id must be globally unique across audit events")
        if prior.get("result") in {"fail_current", "blocked"}:
            for field in FAILED_BINDING_FIELDS:
                value = _identity(prior.get(field))
                if value:
                    failed_values[field].add(value)
    if event.get("result") == "pass":
        for field, values in failed_values.items():
            value = _identity(event.get(field))
            if value and value in values:
                errors.append(
                    f"independent_audit pass must not reuse failed {field}; issue a new repair binding"
                )
    return errors


def _checkpoint_issue_suffix_errors(
    ledger: dict[str, Any],
    checkpoint: dict[str, Any],
    event_scope_by_id: dict[str, str],
) -> list[str]:
    """Rebuild every post-checkpoint issue row from real appended events.

    Legacy history and occurrence locations are allowed only inside the
    checkpoint's hashed prefix.  Every suffix row, including a newly created
    issue, must be the deterministic projection that ``ingest_event`` would
    have produced from an event after ``event_count``.
    """

    errors: list[str] = []
    events = ledger.get("events", [])
    issues = ledger.get("issues", [])
    issue_prefixes = checkpoint.get("issue_prefixes", [])
    checkpoint_count = checkpoint.get("event_count")
    if (
        not isinstance(events, list)
        or not isinstance(issues, list)
        or not isinstance(issue_prefixes, list)
        or isinstance(checkpoint_count, bool)
        or not isinstance(checkpoint_count, int)
        or checkpoint_count < 0
        or checkpoint_count > len(events)
    ):
        return errors

    records_by_id = {
        _identity(issue.get("issue_id")): issue
        for issue in issues
        if isinstance(issue, dict) and _identity(issue.get("issue_id"))
    }
    prefixes_by_id = {
        _identity(prefix.get("issue_id")): prefix
        for prefix in issue_prefixes
        if isinstance(prefix, dict) and _identity(prefix.get("issue_id"))
    }
    expected_histories: dict[str, list[dict[str, Any]]] = {
        issue_id: [] for issue_id in prefixes_by_id
    }
    expected_locations: dict[str, list[dict[str, Any]]] = {
        issue_id: [] for issue_id in prefixes_by_id
    }
    states: dict[str, dict[str, Any]] = {}
    for issue_id, prefix in prefixes_by_id.items():
        def baseline_int(field: str) -> int:
            value = prefix.get(field, 0)
            return (
                value
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                else 0
            )

        identity = prefix.get("immutable_identity")
        if not isinstance(identity, dict):
            identity = {}
        states[issue_id] = {
            "identity": copy.deepcopy(identity),
            "open_scopes": set(prefix.get("baseline_open_scope_keys", [])),
            "requires_reaudit": prefix.get(
                "baseline_legacy_resolution_requires_reaudit"
            )
            is True,
            "occurrence_count": baseline_int("baseline_occurrence_count"),
            "observation_count": baseline_int("baseline_observation_count"),
            "introduced_by_repair_count": baseline_int(
                "baseline_introduced_by_repair_count"
            ),
            "consecutive_rounds": baseline_int(
                "baseline_consecutive_rounds"
            ),
            "max_consecutive_rounds": baseline_int(
                "baseline_max_consecutive_rounds"
            ),
            "last_seen_at": prefix.get("baseline_last_seen_at"),
        }

    for event in events[checkpoint_count:]:
        if not isinstance(event, dict):
            continue
        event_id = _identity(event.get("event_id"))
        event_record_id = event.get("event_id")
        scope_key = event_scope_by_id.get(event_id)
        if not event_id or not scope_key:
            continue
        previous_open = {
            issue_id
            for issue_id, state in states.items()
            if scope_key in state["open_scopes"]
        }
        incoming_ids: set[str] = set()
        for raw_issue in event.get("issues", []):
            if not isinstance(raw_issue, dict):
                continue
            try:
                issue = _normalize_issue(raw_issue)
                dedup_key = _issue_dedup_key(ledger, event, issue)
            except (KeyError, TypeError, ValueError):
                continue
            issue_id = _identity(f"ISS-{_stable_hash(dedup_key)}")
            incoming_ids.add(issue_id)
            identity = {
                "dedup_key": dedup_key,
                "pattern_key": issue_pattern_key(str(ledger.get("project_id") or ""), issue),
                "error_code": issue.get("error_code"),
                "category": issue.get("category"),
                "logical_file": event.get("file_name"),
                "episode_id": event.get("episode_id"),
                "issue_type": issue.get("issue_type", issue.get("category")),
                "field_class": _field_class(issue.get("field")),
                "normalized_root_cause": _normalized_root_cause(issue),
                "first_seen_at": event.get("timestamp"),
            }
            state = states.get(issue_id)
            if state is None:
                state = {
                    "identity": identity,
                    "open_scopes": set(),
                    "requires_reaudit": False,
                    "occurrence_count": 0,
                    "observation_count": 0,
                    "introduced_by_repair_count": 0,
                    "consecutive_rounds": 0,
                    "max_consecutive_rounds": 0,
                    "last_seen_at": event.get("timestamp"),
                }
                states[issue_id] = state
                expected_histories[issue_id] = []
                expected_locations[issue_id] = []
            elif any(
                state.get("identity", {}).get(field) != identity.get(field)
                for field in _CHECKPOINT_IMMUTABLE_ISSUE_FIELDS
                if field != "first_seen_at"
            ):
                errors.append(
                    f"post-checkpoint issue {issue_id} changes immutable identity"
                )

            was_open = scope_key in state["open_scopes"]
            state["occurrence_count"] = int(state["occurrence_count"]) + 1
            state["observation_count"] = int(state["observation_count"]) + 1
            state["introduced_by_repair_count"] = int(
                state["introduced_by_repair_count"]
            ) + int(issue.get("status") == "introduced")
            state["consecutive_rounds"] = (
                int(state["consecutive_rounds"]) + 1 if was_open else 1
            )
            state["max_consecutive_rounds"] = max(
                int(state["max_consecutive_rounds"]),
                int(state["consecutive_rounds"]),
            )
            state["last_seen_at"] = event.get("timestamp")
            state["open_scopes"].add(scope_key)
            expected_histories[issue_id].append(
                {
                    "event_id": event_record_id,
                    "timestamp": event.get("timestamp"),
                    "scope_key": scope_key,
                    "audit_round": event.get("audit_round"),
                    "source_version": event.get("source_version"),
                    "file_fingerprint": event.get("file_fingerprint"),
                    "issue_status": issue.get("status"),
                    "severity": issue.get("severity"),
                    "shot_ids": copy.deepcopy(issue.get("shot_ids", [])),
                    "field": issue.get("field"),
                    "evidence": issue.get("evidence"),
                    "source_sentence": issue.get("source_sentence"),
                    "repair_action": issue.get("repair_action"),
                    "impact_scope": copy.deepcopy(issue.get("impact_scope", [])),
                    "root_cause": issue.get("root_cause"),
                }
            )
            location = {
                "event_id": event_record_id,
                "scope_key": scope_key,
                "episode_id": event.get("episode_id"),
                "file_name": event.get("file_name"),
                "source_version": event.get("source_version"),
                "shot_ids": copy.deepcopy(issue.get("shot_ids", [])),
                "field": issue.get("field"),
            }
            if location not in expected_locations[issue_id]:
                expected_locations[issue_id].append(location)

        for issue_id in previous_open - incoming_ids:
            state = states[issue_id]
            state["open_scopes"].discard(scope_key)
            state["consecutive_rounds"] = 0
            state["requires_reaudit"] = (
                event.get("audit_class") != "independent_audit"
            )
            expected_histories[issue_id].append(
                {
                    "event_id": event_record_id,
                    "timestamp": event.get("timestamp"),
                    "scope_key": scope_key,
                    "audit_round": event.get("audit_round"),
                    "resolution": "not_present_in_follow-up_event",
                }
            )

        if (
            event.get("audit_class") == "independent_audit"
            and event.get("result") == "pass"
        ):
            for issue_id, state in states.items():
                identity = state["identity"]
                if (
                    not state["requires_reaudit"]
                    or _normalize(identity.get("logical_file"))
                    != _normalize(event.get("file_name"))
                    or _normalize(identity.get("episode_id"))
                    != _normalize(event.get("episode_id"))
                ):
                    continue
                cleared_scope_keys = sorted(state["open_scopes"])
                state["open_scopes"].clear()
                state["requires_reaudit"] = False
                expected_histories[issue_id].append(
                    {
                        "event_id": event_record_id,
                        "timestamp": event.get("timestamp"),
                        "scope_key": scope_key,
                        "audit_round": event.get("audit_round"),
                        "source_version": event.get("source_version"),
                        "resolution": "legacy_quality_block_cleared_by_independent_audit",
                        "cleared_scope_keys": cleared_scope_keys,
                        "handoff_id": event.get("handoff_id"),
                        "stage_trace_id": event.get("stage_trace_id"),
                        "artifact_semantic_fingerprint": event.get(
                            "artifact_semantic_fingerprint"
                        ),
                        "canonical_fingerprint": event.get("canonical_fingerprint"),
                    }
                )

    expected_issue_ids = set(states)
    actual_issue_ids = set(records_by_id)
    if actual_issue_ids != expected_issue_ids:
        errors.append(
            "post-checkpoint issue ids do not exactly match sealed issues plus appended events"
        )
    for issue_id, state in states.items():
        record = records_by_id.get(issue_id)
        if not isinstance(record, dict):
            continue
        prefix = prefixes_by_id.get(issue_id, {})
        raw_history_count = prefix.get("history_count", 0)
        raw_location_count = prefix.get("occurrence_location_count", 0)
        history_count = (
            raw_history_count
            if isinstance(raw_history_count, int)
            and not isinstance(raw_history_count, bool)
            and raw_history_count >= 0
            else 0
        )
        location_count = (
            raw_location_count
            if isinstance(raw_location_count, int)
            and not isinstance(raw_location_count, bool)
            and raw_location_count >= 0
            else 0
        )
        if record.get("history", [])[history_count:] != expected_histories[issue_id]:
            errors.append(
                f"issue {issue_id} history suffix is not derived from appended events"
            )
        if (
            record.get("occurrence_locations", [])[location_count:]
            != expected_locations[issue_id]
        ):
            errors.append(
                f"issue {issue_id} occurrence suffix is not derived from appended events"
            )
        if issue_id not in prefixes_by_id and any(
            record.get(field) != value
            for field, value in state["identity"].items()
        ):
            errors.append(
                f"new post-checkpoint issue {issue_id} identity is not event-derived"
            )
        for field in (
            "occurrence_count",
            "observation_count",
            "introduced_by_repair_count",
            "consecutive_rounds",
            "max_consecutive_rounds",
            "last_seen_at",
        ):
            if record.get(field) != state[field]:
                errors.append(
                    f"issue {issue_id}.{field} is not derived from the sealed baseline and appended events"
                )
        if record.get("legacy_resolution_requires_reaudit") is not state[
            "requires_reaudit"
        ]:
            errors.append(
                f"issue {issue_id}.legacy_resolution_requires_reaudit is not replay-derived"
            )
    return errors


def _ledger_replay_errors(ledger: dict[str, Any]) -> list[str]:
    """Replay append-only control state instead of trusting cached scope labels."""

    events = ledger.get("events")
    scopes = ledger.get("scope_states")
    counters = ledger.get("counters")
    if not isinstance(events, list) or not isinstance(scopes, list) or not isinstance(counters, dict):
        return []
    errors: list[str] = []
    replay: dict[str, dict[str, Any]] = {}
    event_scope_by_id: dict[str, str] = {}
    event_by_id: dict[str, dict[str, Any]] = {}
    prior_events: list[dict[str, Any]] = []
    checkpoint = ledger.get("replay_checkpoint")
    checkpoint_count = (
        int(checkpoint.get("event_count"))
        if isinstance(checkpoint, dict)
        and isinstance(checkpoint.get("event_count"), int)
        and not isinstance(checkpoint.get("event_count"), bool)
        and checkpoint.get("event_count", 0) > 0
        else 0
    )
    checkpoint_boundaries = (
        checkpoint.get("scope_boundaries", [])
        if isinstance(checkpoint, dict)
        and isinstance(checkpoint.get("scope_boundaries"), list)
        else []
    )
    checkpoint_first_rounds = {
        str(boundary.get("scope_key")): boundary.get("first_round")
        for boundary in checkpoint_boundaries
        if isinstance(boundary, dict)
    }
    checkpoint_legacy_scopes = {
        str(boundary.get("scope_key")): str(boundary.get("legacy_scope_key"))
        for boundary in checkpoint_boundaries
        if isinstance(boundary, dict)
    }
    checkpoint_allowed_scopes_by_event: dict[str, set[str]] = {}

    for position, event in enumerate(events, 1):
        if not isinstance(event, dict):
            continue
        event_id = _identity(event.get("event_id"))
        try:
            expected_scope_key = _scope_key(ledger, event)
        except (KeyError, TypeError):
            continue
        if event_id:
            event_scope_by_id[event_id] = expected_scope_key
            event_by_id[event_id] = event
            checkpoint_allowed_scopes_by_event[event_id] = {
                expected_scope_key,
                (
                    str(event.get("scope_key"))
                    if position <= checkpoint_count
                    else expected_scope_key
                ),
            }

    if isinstance(checkpoint, dict):
        errors.extend(
            _checkpoint_issue_suffix_errors(
                ledger, checkpoint, event_scope_by_id
            )
        )

    human_signoffs = ledger.get("human_signoffs", [])
    if not isinstance(human_signoffs, list):
        errors.append("human_signoffs must be an array")
        human_signoffs = []
    human_actions_by_basis: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(human_signoffs, 1):
        if not isinstance(record, dict):
            errors.append(f"human_signoffs[{index}] must be an object")
            continue
        for field in ("scope_key", "basis_event_id"):
            if not _is_canonical_identifier(record.get(field)):
                errors.append(f"human_signoffs[{index}].{field} must be canonical")
        if not _validate_iso_timestamp(record.get("timestamp")):
            errors.append(f"human_signoffs[{index}].timestamp must be ISO-8601")
        if record.get("decision") not in {"approve", "reopen"}:
            errors.append(f"human_signoffs[{index}].decision must be approve or reopen")
        if record.get("authority_class") not in {"independent_audit", "legacy"}:
            errors.append(
                f"human_signoffs[{index}].authority_class must be independent_audit or legacy"
            )
        basis_id = _identity(record.get("basis_event_id"))
        if basis_id:
            human_actions_by_basis.setdefault(basis_id, []).append((index, record))

    authority_reopens = ledger.get("authority_reopens", [])
    if not isinstance(authority_reopens, list):
        errors.append("authority_reopens must be an array")
        authority_reopens = []
    authority_actions_by_basis: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    seen_reopen_triggers: set[str] = set()
    for index, record in enumerate(authority_reopens, 1):
        if not isinstance(record, dict):
            errors.append(f"authority_reopens[{index}] must be an object")
            continue
        for field in ("scope_key", "basis_event_id", "trigger_event_id"):
            if not _is_canonical_identifier(record.get(field)):
                errors.append(f"authority_reopens[{index}].{field} must be canonical")
        if record.get("authority") != "independent_audit":
            errors.append(f"authority_reopens[{index}].authority must equal independent_audit")
        if not isinstance(record.get("reason"), str) or not record.get("reason", "").strip():
            errors.append(f"authority_reopens[{index}].reason must be a non-empty string")
        if not _validate_iso_timestamp(record.get("timestamp")):
            errors.append(f"authority_reopens[{index}].timestamp must be ISO-8601")
        trigger_id = _identity(record.get("trigger_event_id"))
        if trigger_id in seen_reopen_triggers:
            errors.append(f"duplicate authority reopen trigger_event_id: {trigger_id}")
        seen_reopen_triggers.add(trigger_id)
        basis_id = _identity(record.get("basis_event_id"))
        if basis_id:
            authority_actions_by_basis.setdefault(basis_id, []).append((index, record))
        trigger_scope = event_scope_by_id.get(trigger_id)
        if trigger_scope is not None and trigger_scope != str(record.get("scope_key") or ""):
            errors.append(f"authority_reopens[{index}] trigger event belongs to another scope")

    consumed_human_actions: set[int] = set()
    consumed_authority_actions: set[int] = set()
    for position, event in enumerate(events, 1):
        if not isinstance(event, dict):
            continue
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != position:
            errors.append(f"events[{position}].sequence must equal append position {position}")
        try:
            expected_scope_key = _scope_key(ledger, event)
        except (KeyError, TypeError):
            continue
        persisted_scope_key = str(event.get("scope_key") or "")
        sealed_scope_alias = checkpoint_legacy_scopes.get(expected_scope_key)
        if persisted_scope_key != expected_scope_key and not (
            position <= checkpoint_count and persisted_scope_key == sealed_scope_alias
        ):
            errors.append(
                f"events[{position}].scope_key does not match episode/file/audit_cycle identity"
            )
        event_id = _identity(event.get("event_id"))
        try:
            expected_counts = _event_counts(event)
        except (AttributeError, KeyError, TypeError, ValueError):
            expected_counts = None
            errors.append(f"events[{position}].counts cannot be derived from malformed event data")
        if (
            expected_counts is not None
            and position > checkpoint_count
            and "counts" in event
            and event.get("counts") != expected_counts
        ):
            errors.append(f"events[{position}].counts does not match replayed event counts")
        state = replay.get(expected_scope_key)
        if state is None:
            expected_round = (
                checkpoint_first_rounds.get(expected_scope_key)
                if position <= checkpoint_count
                else 1
            )
            if not isinstance(expected_round, int) or isinstance(expected_round, bool):
                expected_round = 1
        else:
            expected_round = int(state["last_audit_round"]) + 1
            closure_state = state.get("closure_state")
            if closure_state == "reopened_by_independent_reaudit":
                if _identity(state.get("pending_trigger_event_id")) != event_id:
                    errors.append(
                        f"events[{position}] does not match the authority reopen trigger"
                    )
                if (
                    event.get("audit_class") != "independent_audit"
                    or event.get("validation_role") != "independent_audit"
                    or event.get("task_type") != "audit"
                ):
                    errors.append(
                        f"events[{position}] authority reopen trigger must be a complete independent audit"
                    )
            elif closure_state in {
                "legacy_signoff_requires_reaudit",
                "legacy_resolution_requires_reaudit",
            }:
                if (
                    event.get("audit_class") != "independent_audit"
                    or event.get("validation_role") != "independent_audit"
                    or event.get("task_type") != "audit"
                ):
                    errors.append(
                        f"events[{position}] after a legacy sign-off must be a complete independent audit"
                    )
            elif closure_state == "preflight_clean":
                if event.get("task_type") != "production_draft_preflight":
                    errors.append(
                        f"events[{position}] after preflight_clean must be a production draft preflight"
                    )
            elif closure_state not in {"open", "reopened_by_human"}:
                errors.append(
                    f"events[{position}] follows {closure_state} without an authorized reopen"
                )
        if event.get("audit_round") != expected_round:
            errors.append(
                f"events[{position}].audit_round must continue as {expected_round} in scope {expected_scope_key}"
            )
        errors.extend(
            f"events[{position}]: {error}"
            for error in _independent_freshness_errors(prior_events, event)
        )
        normalized_issues = [
            _normalize_issue(issue)
            for issue in event.get("issues", [])
            if isinstance(issue, dict) and issue.get("status") in ISSUE_STATUS_ALIASES
        ]
        incoming_issue_ids = sorted(
            {
                f"ISS-{_stable_hash(_issue_dedup_key(ledger, event, issue))}"
                for issue in normalized_issues
            }
        )
        closure_state = _event_closure_state(event)
        state = {
            "scope_key": expected_scope_key,
            "episode_id": event.get("episode_id"),
            "file_name": event.get("file_name"),
            "audit_cycle_id": event.get("audit_cycle_id"),
            "last_event_id": event.get("event_id"),
            "last_result": event.get("result"),
            "last_audit_round": event.get("audit_round"),
            "last_audit_class": event.get("audit_class"),
            "open_issue_ids": incoming_issue_ids,
            "closure_state": closure_state,
            "can_advance": False,
            "human_signoff": None,
            "pending_trigger_event_id": None,
        }
        replay[expected_scope_key] = state

        for index, record in human_actions_by_basis.get(event_id, []):
            consumed_human_actions.add(index)
            if record.get("scope_key") != expected_scope_key:
                errors.append(f"human_signoffs[{index}] basis event belongs to another scope")
                continue
            decision = record.get("decision")
            authority_class = record.get("authority_class", "independent_audit")
            if authority_class == "legacy":
                if decision == "reopen":
                    state["closure_state"] = "reopened_by_human"
                elif decision == "approve":
                    state["closure_state"] = "legacy_signoff_requires_reaudit"
                else:
                    errors.append(f"human_signoffs[{index}].decision must be approve or reopen")
                state["can_advance"] = False
                state["human_signoff"] = copy.deepcopy(record)
                continue
            if decision == "approve":
                if (
                    state.get("closure_state") != "awaiting_human_signoff"
                    or state.get("last_result") != "pass"
                    or state.get("last_audit_class") != "independent_audit"
                    or state.get("open_issue_ids")
                ):
                    errors.append(
                        f"human_signoffs[{index}] approves a scope without a fresh clean independent audit"
                    )
                state["closure_state"] = "human_signed_off"
                state["can_advance"] = True
            elif decision == "reopen":
                if state.get("closure_state") not in {"awaiting_human_signoff", "human_signed_off"}:
                    errors.append(f"human_signoffs[{index}] reopens a scope from an invalid state")
                state["closure_state"] = "reopened_by_human"
                state["can_advance"] = False
            else:
                errors.append(f"human_signoffs[{index}].decision must be approve or reopen")
            state["human_signoff"] = copy.deepcopy(record)

        for index, record in authority_actions_by_basis.get(event_id, []):
            consumed_authority_actions.add(index)
            if record.get("scope_key") != expected_scope_key:
                errors.append(f"authority_reopens[{index}] basis event belongs to another scope")
                continue
            if (
                state.get("closure_state") != "awaiting_human_signoff"
                or state.get("last_result") != "pass"
                or state.get("last_audit_class") != "independent_audit"
                or state.get("open_issue_ids")
            ):
                errors.append(
                    f"authority_reopens[{index}] requires a fresh clean independent audit basis"
                )
            if int(state.get("last_audit_round", 0)) >= 3:
                errors.append(f"authority_reopens[{index}] would exceed audit round three")
            state["closure_state"] = "reopened_by_independent_reaudit"
            state["can_advance"] = False
            state["pending_trigger_event_id"] = record.get("trigger_event_id")
        prior_events.append(event)

    for index, record in enumerate(human_signoffs, 1):
        if isinstance(record, dict) and index not in consumed_human_actions:
            errors.append(f"human_signoffs[{index}] basis_event_id is unknown")
    for index, record in enumerate(authority_reopens, 1):
        if isinstance(record, dict) and index not in consumed_authority_actions:
            errors.append(f"authority_reopens[{index}] basis_event_id is unknown")

    for issue_index, issue in enumerate(ledger.get("issues", []), 1):
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("issue_id") or "")
        for row_index, row in enumerate(issue.get("history", []), 1):
            if not isinstance(row, dict) or row.get("resolution") != (
                "legacy_quality_block_cleared_by_independent_audit"
            ):
                continue
            clearing_event = event_by_id.get(_identity(row.get("event_id")))
            if not (
                isinstance(clearing_event, dict)
                and clearing_event.get("audit_class") == "independent_audit"
                and clearing_event.get("validation_role") == "independent_audit"
                and clearing_event.get("task_type") == "audit"
                and clearing_event.get("result") == "pass"
                and not clearing_event.get("issues")
                and not clearing_event.get("blockers")
                and _normalize(clearing_event.get("file_name"))
                == _normalize(issue.get("logical_file"))
                and _normalize(clearing_event.get("episode_id"))
                == _normalize(issue.get("episode_id"))
            ):
                errors.append(
                    f"issues[{issue_index}].history[{row_index}] has no clean independent clearance authority"
                )
                continue
            cleared_scope_keys = row.get("cleared_scope_keys")
            if not isinstance(cleared_scope_keys, list):
                errors.append(
                    f"issues[{issue_index}].history[{row_index}].cleared_scope_keys must be an array"
                )
                continue
            for cleared_scope_key in cleared_scope_keys:
                state = replay.get(str(cleared_scope_key))
                if state is None or issue_id not in state.get("open_issue_ids", []):
                    errors.append(
                        f"issues[{issue_index}].history[{row_index}] clears a scope without that legacy issue"
                    )
                    continue
                state["open_issue_ids"] = sorted(
                    set(state.get("open_issue_ids", [])) - {issue_id}
                )

    stored_scopes: dict[str, dict[str, Any]] = {}
    for index, scope in enumerate(scopes, 1):
        if not isinstance(scope, dict):
            continue
        scope_key = str(scope.get("scope_key") or "")
        if scope_key != _scope_key(ledger, scope):
            errors.append(f"scope_states[{index}].scope_key does not match its scope identity")
        stored_scopes[scope_key] = scope
        last_event_id = _identity(scope.get("last_event_id"))
        if last_event_id and event_scope_by_id.get(last_event_id) != scope_key:
            errors.append(f"scope_states[{index}].last_event_id belongs to another scope or is unknown")
    if set(stored_scopes) != set(replay):
        errors.append("scope_states keys do not exactly match replayed event scopes")
    for scope_key, expected in replay.items():
        stored = stored_scopes.get(scope_key)
        if stored is None:
            continue
        for field in (
            "episode_id", "file_name", "audit_cycle_id", "last_event_id",
            "last_result", "last_audit_round", "open_issue_ids", "closure_state",
            "can_advance", "human_signoff",
        ):
            if stored.get(field) != expected.get(field):
                errors.append(f"scope {scope_key}.{field} does not match replayed event state")

    checkpoint_issue_prefix_by_id = {
        _identity(prefix.get("issue_id")): prefix
        for prefix in (
            checkpoint.get("issue_prefixes", [])
            if isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("issue_prefixes"), list)
            else []
        )
        if isinstance(prefix, dict) and _identity(prefix.get("issue_id"))
    }
    for issue_index, issue in enumerate(ledger.get("issues", []), 1):
        if not isinstance(issue, dict):
            continue
        issue_prefix = checkpoint_issue_prefix_by_id.get(
            _identity(issue.get("issue_id")), {}
        )
        for collection_name in ("occurrence_locations", "history"):
            raw_sealed_count = issue_prefix.get(
                "occurrence_location_count"
                if collection_name == "occurrence_locations"
                else "history_count",
                0,
            )
            sealed_count = (
                raw_sealed_count
                if isinstance(raw_sealed_count, int)
                and not isinstance(raw_sealed_count, bool)
                and raw_sealed_count >= 0
                else 0
            )
            for row_index, row in enumerate(issue.get(collection_name, []), 1):
                if not isinstance(row, dict) or not row.get("event_id") or not row.get("scope_key"):
                    continue
                if row_index <= sealed_count:
                    continue
                event_id = _identity(row.get("event_id"))
                if isinstance(checkpoint, dict) and event_id.startswith("legacy-"):
                    errors.append(
                        f"issues[{issue_index}].{collection_name}[{row_index}] appends a legacy-only row after the sealed checkpoint"
                    )
                    continue
                expected_scope_key = event_scope_by_id.get(event_id)
                if expected_scope_key is None:
                    errors.append(
                        f"issues[{issue_index}].{collection_name}[{row_index}] references unknown event"
                    )
                elif str(row.get("scope_key")) not in checkpoint_allowed_scopes_by_event.get(
                    event_id, {expected_scope_key}
                ):
                    errors.append(
                        f"issues[{issue_index}].{collection_name}[{row_index}] references an event from another scope"
                    )
        issue_id = str(issue.get("issue_id") or "")
        expected_open_scopes = sorted(
            scope_key
            for scope_key, state in replay.items()
            if issue_id in state.get("open_issue_ids", [])
        )
        if sorted(issue.get("open_scope_keys", [])) != expected_open_scopes:
            errors.append(
                f"issues[{issue_index}].open_scope_keys does not match replayed scope issue state"
            )
        expected_status = "open" if expected_open_scopes else "resolved"
        if issue.get("status") != expected_status:
            errors.append(f"issues[{issue_index}].status does not match replayed scope issue state")

    expected_counters = {
        "event_sequence": len(events),
        "event_count": len(events),
        "issue_record_count": len(ledger.get("issues", [])),
        "rule_candidate_count": len(ledger.get("rule_candidates", [])),
    }
    for field, expected in expected_counters.items():
        if counters.get(field) != expected:
            errors.append(f"counters.{field} must equal replayed value {expected}")
    return errors


def _issue_dedup_key(ledger: dict[str, Any], event: dict[str, Any], issue: dict[str, Any]) -> str:
    """Identify one concrete occurrence while pattern_key crosses batches."""

    logical_problem = issue.get("problem_key") or issue.get("issue_type") or issue.get("category")
    shot_ids = ",".join(sorted(_normalize(value) for value in issue.get("shot_ids", []))) or "scope"
    value = "|".join([
        str(ledger["project_id"]),
        _normalize(event["episode_id"]),
        _normalize(event["file_name"]),
        _normalize(issue.get("error_code")),
        shot_ids,
        _normalize(issue.get("field")),
        _normalize(logical_problem),
    ])
    return f"DEDUP-{_stable_hash(value, 24)}"


def _normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(issue)
    normalized["category"] = LEGACY_AUDIT_CATEGORY_ALIASES.get(
        str(issue.get("category")), str(issue.get("category"))
    )
    normalized["status"] = ISSUE_STATUS_ALIASES[issue["status"]]
    normalized["shot_ids"] = [str(value) for value in issue.get("shot_ids", [])]
    normalized["blocking"] = bool(issue.get("blocking", issue.get("severity") in {"fail", "blocking", "error"}))
    normalized["issue_type"] = str(
        issue.get("issue_type") or issue.get("category") or "unknown_issue"
    )
    normalized.setdefault("impact_scope", normalized["shot_ids"])
    return normalized


def _event_counts(event: dict[str, Any]) -> dict[str, int]:
    issues = event["issues"]
    shot_ids = {shot_id for issue in issues for shot_id in issue.get("shot_ids", [])}
    counts = {
        "checked_shot_count": int(event["checked_shot_count"]),
        "problem_shot_count": len(shot_ids),
        "total_error_points": len(issues),
        "initial_error_count": sum(issue["status"] == "initial" for issue in issues),
        "residual_error_count": sum(issue["status"] == "residual" for issue in issues),
        "introduced_error_count": sum(issue["status"] == "introduced" for issue in issues),
        "blocking_error_count": sum(bool(issue.get("blocking")) for issue in issues),
    }
    # Preserve byte-for-byte legacy replay projections for historical events.
    if event.get("assessment_contract") == "storyboard-quality-v2":
        counts.update({
            "missed_error_count": sum(i["status"] == "missed_in_prior_audit" for i in issues),
            "rule_changed_error_count": sum(i["status"] == "rule_changed" for i in issues),
            "attribution_pending_count": sum(i["status"] == "attribution_pending" for i in issues),
        })
    return counts


def _event_closure_state(event: dict[str, Any]) -> str:
    if event.get("result") != "pass":
        return "open"
    if event.get("task_type") == "production_draft_preflight":
        return "preflight_clean"
    if event.get("audit_class") == "independent_audit":
        return "awaiting_human_signoff"
    return "legacy_resolution_requires_reaudit"


def _candidate_for_issue(issue: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    pattern_key = str(issue.get("pattern_key") or issue["issue_id"])
    candidate_id = f"RC-{_stable_hash(pattern_key + '|preventive-gate')}"
    return {
        "candidate_id": candidate_id,
        "created_at": event["timestamp"],
        "updated_at": event["timestamp"],
        "trigger_issue_ids": list(issue.get("trigger_issue_ids") or [issue["issue_id"]]),
        "trigger_error_codes": [issue["error_code"]],
        "trigger_recurrence": {
            "occurrence_count": issue.get("pattern_occurrence_count", issue["occurrence_count"]),
            "observation_count": issue.get("pattern_observation_count", issue["occurrence_count"]),
            "max_consecutive_rounds": issue["max_consecutive_rounds"],
        },
        "target_artifact": "preventive_rule+deterministic_validator+golden_tests",
        "proposed_change": f"将 {issue['error_code']}（{issue['category']}）前置为生成门禁，并保留受影响范围回归。",
        "validator_change": f"新增或强化 {issue['error_code']} 的确定性检测，证据必须定位到镜头和字段。",
        "golden_tests": [
            f"{issue['error_code']} negative fixture must fail",
            f"{issue['error_code']} corrected fixture must pass",
            "unrelated impact-regression fixture must remain unchanged",
        ],
        "conflict_priority": "安全合规>源文忠实>分集边界>台词容量>统计闭合>可见性>图生视频可执行性>节奏留存>美学细节",
        "validation": {
            "validator_status": "pending",
            "validator_test_ids": [],
            "golden_tests_status": "pending",
            "golden_test_ids": [],
            "conflict_check": "pending",
            "all_tests_pass": False,
            "checked_at": None,
        },
        "human_decision": {"decision": "pending", "decided_at": None},
        "status": "proposed",
        "formal_rule_mutated": False,
    }


def _refresh_candidate_status(candidate: dict[str, Any]) -> None:
    if candidate.get("human_decision", {}).get("decision") == "reject":
        candidate["status"] = "rejected"
        return
    validation = candidate.get("validation", {})
    checks_ready = (
        validation.get("validator_status") == "pass"
        and validation.get("golden_tests_status") == "pass"
        and validation.get("conflict_check") == "pass"
        and validation.get("all_tests_pass") is True
    )
    approved = candidate.get("human_decision", {}).get("decision") == "approve"
    if checks_ready and approved:
        candidate["status"] = "ready_for_manual_promotion"
    elif checks_ready:
        candidate["status"] = "awaiting_human"
    elif approved:
        candidate["status"] = "approved_pending_validation"
    else:
        candidate["status"] = "proposed"


def _event_request_identity(event: dict[str, Any]) -> str:
    payload = copy.deepcopy(event)
    for field in ("scope_key", "sequence", "counts", "legacy_aliases_applied"):
        payload.pop(field, None)
    if isinstance(payload.get("issues"), list):
        payload["issues"] = [
            _normalize_issue(issue)
            for issue in payload["issues"]
            if isinstance(issue, dict) and issue.get("status") in ISSUE_STATUS_ALIASES
        ]
    for field in (
        "event_id", "audit_cycle_id", "handoff_id", "stage_trace_id",
        "runtime_test_certificate_id", "file_fingerprint", "source_version",
        *SHA256_FIELDS,
    ):
        if field in payload:
            payload[field] = _identity(payload[field])
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ingest_transition_errors(
    ledger: dict[str, Any],
    event: dict[str, Any],
) -> list[str]:
    errors = _independent_freshness_errors(ledger.get("events", []), event)
    scope_key = _scope_key(ledger, event)
    scope = next(
        (
            item for item in ledger.get("scope_states", [])
            if isinstance(item, dict) and item.get("scope_key") == scope_key
        ),
        None,
    )
    if scope is None:
        if event.get("audit_round") != 1:
            errors.append("a new audit scope must start at audit_round=1")
        return errors
    closure_state = scope.get("closure_state")
    if closure_state == "awaiting_human_signoff":
        errors.append(
            "scope has a clean pass and awaits human sign-off; sign off or reopen it before another event"
        )
    elif closure_state == "human_signed_off":
        errors.append("scope is already signed off; start a new audit_cycle_id for a changed source")
    elif closure_state == "preflight_clean":
        if event.get("task_type") != "production_draft_preflight":
            errors.append(
                "a clean production preflight may be superseded only by another production draft preflight"
            )
    elif closure_state == "reopened_by_independent_reaudit":
        reopen = scope.get("authority_reopen")
        if event.get("audit_class") != "independent_audit":
            errors.append("an independent re-audit reopen accepts only an independent_audit event")
        if not isinstance(reopen, dict) or _identity(reopen.get("trigger_event_id")) != _identity(event.get("event_id")):
            errors.append("independent re-audit event_id does not match the authorized reopen trigger")
    elif closure_state in {
        "legacy_signoff_requires_reaudit",
        "legacy_resolution_requires_reaudit",
    }:
        if (
            event.get("audit_class") != "independent_audit"
            or event.get("validation_role") != "independent_audit"
            or event.get("task_type") != "audit"
        ):
            errors.append("a legacy sign-off requires a new complete independent audit")
    expected_round = int(scope.get("last_audit_round", 0)) + 1
    if event.get("audit_round") != expected_round:
        errors.append(
            f"audit_round must continue from {scope.get('last_audit_round')} to {expected_round} in this cycle"
        )
    return errors


def ingest_event(
    ledger: dict[str, Any],
    raw_event: dict[str, Any],
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Commit an accepted event atomically; every rejection leaves byte state intact."""

    candidate = copy.deepcopy(ledger)
    checkpoint_before = copy.deepcopy(candidate.get("replay_checkpoint"))
    result = _ingest_event_in_place(
        candidate, raw_event, checkpoint_authorizer=checkpoint_authorizer
    )
    if result.get("status") == "duplicate":
        return result
    if candidate.get("replay_checkpoint") != checkpoint_before:
        raise ValueError("event mutation must not create or modify replay_checkpoint")
    post_errors = validate_ledger(
        candidate, checkpoint_authorizer=checkpoint_authorizer
    )
    if post_errors:
        raise ValueError("event would create invalid ledger: " + "; ".join(post_errors))
    ledger.clear()
    ledger.update(candidate)
    return result


def _ingest_event_in_place(
    ledger: dict[str, Any],
    raw_event: dict[str, Any],
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    ledger_errors = validate_ledger(
        ledger, checkpoint_authorizer=checkpoint_authorizer
    )
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    event_errors = validate_event(raw_event, int(ledger["policy"]["max_auto_repair_rounds"]))
    if event_errors:
        raise ValueError("invalid event: " + "; ".join(event_errors))
    duplicate = next(
        (
            event for event in ledger["events"]
            if _identity(event.get("event_id")) == _identity(raw_event.get("event_id"))
        ),
        None,
    )
    if duplicate is not None:
        if _event_request_identity(duplicate) != _event_request_identity(raw_event):
            raise ValueError("event_id conflicts with a different persisted event payload")
        return {"status": "duplicate", "event_id": raw_event["event_id"], "summary": summarize(ledger)}

    event = copy.deepcopy(raw_event)
    legacy_aliases = sorted({
        str(issue.get("category"))
        for issue in event.get("issues", [])
        if isinstance(issue, dict) and issue.get("category") in LEGACY_AUDIT_CATEGORY_ALIASES
    })
    event["issues"] = [_normalize_issue(issue) for issue in event["issues"]]
    if legacy_aliases:
        event["legacy_aliases_applied"] = {
            value: LEGACY_AUDIT_CATEGORY_ALIASES[value] for value in legacy_aliases
        }
    transition_errors = _ingest_transition_errors(ledger, event)
    if transition_errors:
        raise ValueError("invalid event transition: " + "; ".join(transition_errors))
    scope_key = _scope_key(ledger, event)
    event["scope_key"] = scope_key
    ledger["counters"]["event_sequence"] += 1
    event["sequence"] = ledger["counters"]["event_sequence"]
    event["counts"] = _event_counts(event)

    scope = next((item for item in ledger["scope_states"] if item["scope_key"] == scope_key), None)
    if scope is None:
        scope = {
            "scope_key": scope_key,
            "episode_id": event["episode_id"],
            "file_name": event["file_name"],
            "audit_cycle_id": event["audit_cycle_id"],
            "last_event_id": None,
            "last_result": None,
            "last_audit_round": 0,
            "open_issue_ids": [],
            "closure_state": "open",
            "can_advance": False,
            "human_signoff": None,
        }
        ledger["scope_states"].append(scope)

    previous_open = set(scope["open_issue_ids"])
    records_by_key: dict[str, dict[str, Any]] = {}
    for item in ledger["issues"]:
        if not isinstance(item, dict):
            continue
        records_by_key[str(item.get("dedup_key"))] = item
    incoming_ids: set[str] = set()
    created_candidate_ids: list[str] = []
    touched_pattern_keys: set[str] = set()

    for issue in event["issues"]:
        dedup_key = _issue_dedup_key(ledger, event, issue)
        pattern_key = issue_pattern_key(str(ledger["project_id"]), issue)
        touched_pattern_keys.add(pattern_key)
        issue_id = f"ISS-{_stable_hash(dedup_key)}"
        history_entry = {
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "scope_key": scope_key,
            "audit_round": event["audit_round"],
            "source_version": event["source_version"],
            "file_fingerprint": event["file_fingerprint"],
            "issue_status": issue["status"],
            "severity": issue["severity"],
            "shot_ids": issue["shot_ids"],
            "field": issue["field"],
            "evidence": issue["evidence"],
            "source_sentence": issue["source_sentence"],
            "repair_action": issue["repair_action"],
            "impact_scope": issue.get("impact_scope", []),
            "root_cause": issue.get("root_cause"),
        }
        record = records_by_key.get(dedup_key)
        if record is None:
            record = {
                "issue_id": issue_id,
                "dedup_key": dedup_key,
                "pattern_key": pattern_key,
                "error_code": issue["error_code"],
                "category": issue["category"],
                "logical_file": event["file_name"],
                "episode_id": event["episode_id"],
                "issue_type": issue.get("issue_type", issue["category"]),
                "normalized_root_cause": _normalized_root_cause(issue),
                "field_class": _field_class(issue.get("field")),
                "status": "open",
                "legacy_resolution_requires_reaudit": False,
                "open_scope_keys": [scope_key],
                "recurrence_state": "first_occurrence",
                "occurrence_count": 1,
                "observation_count": 1,
                "consecutive_rounds": 1,
                "max_consecutive_rounds": 1,
                "introduced_by_repair_count": int(issue["status"] == "introduced"),
                "first_seen_at": event["timestamp"],
                "last_seen_at": event["timestamp"],
                "root_cause_gate": "not_triggered",
                "repair_strategy": "local_repair",
                "history": [history_entry],
                "occurrence_locations": [],
                "distinct_event_count": 1,
                "distinct_scope_count": 1,
                "pattern_occurrence_count": 1,
                "pattern_observation_count": 1,
            }
            ledger["issues"].append(record)
            records_by_key[dedup_key] = record
        else:
            was_open = record["issue_id"] in previous_open
            open_elsewhere = bool(
                set(record.get("open_scope_keys", [])) - {scope_key}
            )
            was_resolved = record.get("status") == "resolved"
            record["occurrence_count"] += 1
            record["observation_count"] = int(
                record.get("observation_count", record["occurrence_count"] - 1)
            ) + 1
            record["consecutive_rounds"] = record["consecutive_rounds"] + 1 if was_open else 1
            record["max_consecutive_rounds"] = max(record["max_consecutive_rounds"], record["consecutive_rounds"])
            record["introduced_by_repair_count"] += int(issue["status"] == "introduced")
            record["last_seen_at"] = event["timestamp"]
            record["status"] = "open"
            record["open_scope_keys"] = sorted(set(record.get("open_scope_keys", [])) | {scope_key})
            if was_open:
                record["recurrence_state"] = "consecutive"
            elif open_elsewhere:
                record["recurrence_state"] = "recurred_in_parallel_scope"
            elif was_resolved:
                record["recurrence_state"] = "recurred_after_resolution"
            else:
                record["recurrence_state"] = "reobserved"
            record["history"].append(history_entry)

        issue_id = str(record["issue_id"])
        incoming_ids.add(issue_id)
        occurrence_location = {
            "event_id": event["event_id"],
            "scope_key": scope_key,
            "episode_id": event["episode_id"],
            "file_name": event["file_name"],
            "source_version": event["source_version"],
            "shot_ids": copy.deepcopy(issue["shot_ids"]),
            "field": issue["field"],
        }
        if occurrence_location not in record.setdefault("occurrence_locations", []):
            record["occurrence_locations"].append(occurrence_location)
        record["distinct_event_count"] = len(
            {str(row.get("event_id")) for row in record["history"] if row.get("event_id")}
        )
        record["distinct_scope_count"] = len(
            {str(row.get("scope_key")) for row in record["history"] if row.get("scope_key")}
        )

        recurrence_count = int(record.get("occurrence_count", 0))
        if recurrence_count >= 3 or record["consecutive_rounds"] >= 3:
            record["root_cause_gate"] = "required"
            record["repair_strategy"] = "upstream_rebuild_required"
            record["recurrence_state"] = "systemic_three_rounds"
        elif recurrence_count >= 2 or record["consecutive_rounds"] >= 2:
            record["root_cause_gate"] = "recorded" if issue.get("root_cause") else "required"
            record["repair_strategy"] = "root_cause_repair_required"
            record["recurrence_state"] = "repeated_two_rounds"

    # Escalate and propose one preventive rule per reusable cross-batch pattern,
    # while retaining separate occurrence records for exact repair/closure.
    for pattern_key in sorted(touched_pattern_keys):
        pattern_records = [
            row
            for row in ledger["issues"]
            if row.get("pattern_key") == pattern_key
        ]
        occurrence_count = len(pattern_records)
        observation_count = sum(
            int(row.get("observation_count", row.get("occurrence_count", 0)))
            for row in pattern_records
        )
        same_event_observations = sum(
            1
            for row in pattern_records
            for history in row.get("history", [])
            if history.get("event_id") == event["event_id"]
        )
        systemic_same_draft = same_event_observations >= 2
        cross_batch_recurrence = occurrence_count >= 2
        for row in pattern_records:
            row["pattern_occurrence_count"] = occurrence_count
            row["pattern_observation_count"] = observation_count
            if systemic_same_draft:
                row["root_cause_gate"] = "required"
                row["repair_strategy"] = "upstream_rebuild_required"
                row["recurrence_state"] = "systemic_same_draft"
            elif cross_batch_recurrence:
                row["root_cause_gate"] = (
                    "recorded" if row.get("normalized_root_cause") not in {None, "", "unknown"}
                    else "required"
                )
                row["repair_strategy"] = "root_cause_repair_required"
                row["recurrence_state"] = "cross_batch_recurrence"
        max_consecutive = max(
            (int(row.get("max_consecutive_rounds", 0)) for row in pattern_records),
            default=0,
        )
        if occurrence_count >= 2 or max_consecutive >= 2:
            representative = copy.deepcopy(pattern_records[0])
            representative["pattern_occurrence_count"] = occurrence_count
            representative["pattern_observation_count"] = observation_count
            representative["trigger_issue_ids"] = sorted(
                str(row["issue_id"]) for row in pattern_records
            )
            candidate = _candidate_for_issue(representative, event)
            existing_candidate = next(
                (
                    item
                    for item in ledger["rule_candidates"]
                    if item["candidate_id"] == candidate["candidate_id"]
                ),
                None,
            )
            if existing_candidate is None:
                ledger["rule_candidates"].append(candidate)
                created_candidate_ids.append(candidate["candidate_id"])
            else:
                existing_candidate["updated_at"] = event["timestamp"]
                existing_candidate["trigger_issue_ids"] = candidate[
                    "trigger_issue_ids"
                ]
                existing_candidate["trigger_recurrence"] = candidate[
                    "trigger_recurrence"
                ]

    for issue_id in previous_open - incoming_ids:
        record = next((item for item in ledger["issues"] if item["issue_id"] == issue_id), None)
        if record is not None:
            record["open_scope_keys"] = sorted(set(record.get("open_scope_keys", [])) - {scope_key})
            record["status"] = "open" if record["open_scope_keys"] else "resolved"
            record["consecutive_rounds"] = 0
            if record["status"] == "resolved":
                record["resolved_at"] = event["timestamp"]
                record["legacy_resolution_requires_reaudit"] = (
                    event.get("audit_class") != "independent_audit"
                )
            record["history"].append({
                "event_id": event["event_id"],
                "timestamp": event["timestamp"],
                "scope_key": scope_key,
                "audit_round": event["audit_round"],
                "resolution": "not_present_in_follow-up_event",
            })

    if event.get("audit_class") == "independent_audit" and event.get("result") == "pass":
        for record in ledger["issues"]:
            if not isinstance(record, dict) or not record.get(
                "legacy_resolution_requires_reaudit"
            ):
                continue
            if (
                _normalize(record.get("logical_file")) != _normalize(event.get("file_name"))
                or _normalize(record.get("episode_id")) != _normalize(event.get("episode_id"))
            ):
                continue
            cleared_scope_keys = sorted(set(record.get("open_scope_keys", [])))
            for legacy_scope_key in cleared_scope_keys:
                legacy_scope = next(
                    (
                        row
                        for row in ledger.get("scope_states", [])
                        if row.get("scope_key") == legacy_scope_key
                    ),
                    None,
                )
                if isinstance(legacy_scope, dict):
                    legacy_scope["open_issue_ids"] = sorted(
                        set(legacy_scope.get("open_issue_ids", []))
                        - {str(record.get("issue_id"))}
                    )
            record["open_scope_keys"] = []
            record["status"] = "resolved"
            record["legacy_resolution_requires_reaudit"] = False
            record["resolved_at"] = event["timestamp"]
            record["history"].append({
                "event_id": event["event_id"],
                "timestamp": event["timestamp"],
                "scope_key": scope_key,
                "audit_round": event["audit_round"],
                "source_version": event["source_version"],
                "resolution": "legacy_quality_block_cleared_by_independent_audit",
                "cleared_scope_keys": cleared_scope_keys,
                "handoff_id": event["handoff_id"],
                "stage_trace_id": event["stage_trace_id"],
                "artifact_semantic_fingerprint": event[
                    "artifact_semantic_fingerprint"
                ],
                "canonical_fingerprint": event["canonical_fingerprint"],
            })

    scope["last_event_id"] = event["event_id"]
    scope["last_result"] = event["result"]
    scope["last_audit_round"] = event["audit_round"]
    scope["open_issue_ids"] = sorted(incoming_ids)
    scope["human_signoff"] = None
    scope["can_advance"] = False
    scope["closure_state"] = _event_closure_state(event)

    ledger["events"].append(event)
    ledger["updated_at"] = event["timestamp"]
    ledger["counters"].update({
        "event_count": len(ledger["events"]),
        "issue_record_count": len(ledger["issues"]),
        "rule_candidate_count": len(ledger["rule_candidates"]),
    })
    return {
        "status": "accepted",
        "event_id": event["event_id"],
        "scope_key": scope_key,
        "candidate_ids_created": created_candidate_ids,
        "closure_state": scope["closure_state"],
        "can_advance": scope["can_advance"],
        "summary": summarize(ledger),
    }


def check_candidate(
    ledger: dict[str, Any],
    candidate_id: str,
    validator_test_ids: list[str],
    golden_test_ids: list[str],
    conflict_check: str,
    all_tests_pass: bool,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    ledger_errors = validate_ledger(
        ledger, checkpoint_authorizer=checkpoint_authorizer
    )
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    candidate = next((item for item in ledger["rule_candidates"] if item["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ValueError(f"unknown candidate_id: {candidate_id}")
    validation = candidate["validation"]
    validation["validator_test_ids"] = list(dict.fromkeys(validator_test_ids))
    validation["golden_test_ids"] = list(dict.fromkeys(golden_test_ids))
    validation["validator_status"] = "pass" if validator_test_ids and all_tests_pass else "fail"
    validation["golden_tests_status"] = "pass" if golden_test_ids and all_tests_pass else "fail"
    validation["conflict_check"] = conflict_check
    validation["all_tests_pass"] = bool(all_tests_pass)
    validation["checked_at"] = _timestamp(timestamp)
    candidate["updated_at"] = validation["checked_at"]
    _refresh_candidate_status(candidate)
    ledger["updated_at"] = validation["checked_at"]
    return {"status": candidate["status"], "candidate_id": candidate_id, "formal_rule_mutated": False}


def decide_candidate(
    ledger: dict[str, Any],
    candidate_id: str,
    decision: str,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    ledger_errors = validate_ledger(
        ledger, checkpoint_authorizer=checkpoint_authorizer
    )
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    candidate = next((item for item in ledger["rule_candidates"] if item["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ValueError(f"unknown candidate_id: {candidate_id}")
    now = _timestamp(timestamp)
    candidate["human_decision"] = {"decision": decision, "decided_at": now}
    candidate["updated_at"] = now
    _refresh_candidate_status(candidate)
    ledger["updated_at"] = now
    return {"status": candidate["status"], "candidate_id": candidate_id, "formal_rule_mutated": False}


def signoff_scope(
    ledger: dict[str, Any],
    scope_key: str,
    decision: str,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    candidate = copy.deepcopy(ledger)
    checkpoint_before = copy.deepcopy(candidate.get("replay_checkpoint"))
    result = _signoff_scope_in_place(
        candidate,
        scope_key,
        decision,
        timestamp,
        checkpoint_authorizer=checkpoint_authorizer,
    )
    if candidate.get("replay_checkpoint") != checkpoint_before:
        raise ValueError("sign-off must not create or modify replay_checkpoint")
    post_errors = validate_ledger(
        candidate, checkpoint_authorizer=checkpoint_authorizer
    )
    if post_errors:
        raise ValueError("sign-off would create invalid ledger: " + "; ".join(post_errors))
    ledger.clear()
    ledger.update(candidate)
    return result


def _signoff_scope_in_place(
    ledger: dict[str, Any],
    scope_key: str,
    decision: str,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    ledger_errors = validate_ledger(
        ledger, checkpoint_authorizer=checkpoint_authorizer
    )
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    if decision not in {"approve", "reopen"}:
        raise ValueError("decision must be approve or reopen")
    scope = next((item for item in ledger["scope_states"] if item["scope_key"] == scope_key), None)
    if scope is None:
        raise ValueError(f"unknown scope_key: {scope_key}")
    now = _timestamp(timestamp)
    if decision == "approve":
        last_event = next(
            (
                event for event in ledger.get("events", [])
                if _identity(event.get("event_id")) == _identity(scope.get("last_event_id"))
            ),
            None,
        )
        if (
            scope.get("closure_state") != "awaiting_human_signoff"
            or scope.get("last_result") != "pass"
            or scope.get("open_issue_ids")
            or not isinstance(last_event, dict)
            or last_event.get("audit_class") != "independent_audit"
        ):
            raise ValueError(
                "human sign-off requires a fresh clean independent audit after any reopen"
            )
        scope["closure_state"] = "human_signed_off"
        scope["can_advance"] = True
    else:
        if scope.get("closure_state") not in {"awaiting_human_signoff", "human_signed_off"}:
            raise ValueError("human reopen requires a clean pending or signed scope")
        scope["closure_state"] = "reopened_by_human"
        scope["can_advance"] = False
    last_event = next(
        (
            event for event in ledger.get("events", [])
            if _identity(event.get("event_id")) == _identity(scope.get("last_event_id"))
        ),
        None,
    )
    record = {
        "scope_key": scope_key,
        "basis_event_id": scope["last_event_id"],
        "authority_class": (
            "independent_audit"
            if isinstance(last_event, dict) and last_event.get("audit_class") == "independent_audit"
            else "legacy"
        ),
        "decision": decision,
        "timestamp": now,
    }
    scope["human_signoff"] = record
    ledger["human_signoffs"].append(record)
    ledger["updated_at"] = now
    return {"scope_key": scope_key, "closure_state": scope["closure_state"], "can_advance": scope["can_advance"]}


def reopen_scope_for_independent_reaudit(
    ledger: dict[str, Any],
    scope_key: str,
    trigger_event_id: str,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Reopen a clean audit scope without fabricating a human decision."""

    candidate = copy.deepcopy(ledger)
    checkpoint_before = copy.deepcopy(candidate.get("replay_checkpoint"))
    record = _reopen_scope_for_independent_reaudit_in_place(
        candidate,
        scope_key,
        trigger_event_id,
        timestamp,
        checkpoint_authorizer=checkpoint_authorizer,
    )
    if candidate.get("replay_checkpoint") != checkpoint_before:
        raise ValueError("reopen must not create or modify replay_checkpoint")
    post_errors = validate_ledger(
        candidate, checkpoint_authorizer=checkpoint_authorizer
    )
    if post_errors:
        raise ValueError("reopen would create invalid ledger: " + "; ".join(post_errors))
    ledger.clear()
    ledger.update(candidate)
    return record


def _reopen_scope_for_independent_reaudit_in_place(
    ledger: dict[str, Any],
    scope_key: str,
    trigger_event_id: str,
    timestamp: str | None = None,
    *,
    checkpoint_authorizer: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    ledger_errors = validate_ledger(
        ledger, checkpoint_authorizer=checkpoint_authorizer
    )
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    if not _is_canonical_identifier(trigger_event_id):
        raise ValueError("trigger_event_id must be a non-empty canonical identifier")

    scope = next(
        (
            item
            for item in ledger.get("scope_states", [])
            if item.get("scope_key") == scope_key
        ),
        None,
    )
    if scope is None:
        raise ValueError(f"unknown scope_key: {scope_key}")
    if scope.get("closure_state") != "awaiting_human_signoff":
        raise ValueError(
            "independent re-audit may reopen only a clean scope awaiting human sign-off"
        )
    if int(scope.get("last_audit_round", 0)) >= int(
        ledger.get("policy", {}).get("max_auto_repair_rounds", 3)
    ):
        raise ValueError("independent re-audit cannot exceed the maximum audit round")
    last_event = next(
        (
            event for event in ledger.get("events", [])
            if _identity(event.get("event_id")) == _identity(scope.get("last_event_id"))
        ),
        None,
    )
    if (
        not isinstance(last_event, dict)
        or last_event.get("audit_class") != "independent_audit"
        or last_event.get("result") != "pass"
        or last_event.get("issues")
        or last_event.get("blockers")
    ):
        raise ValueError("independent re-audit requires the latest clean independent audit event")
    if any(
        _identity(event.get("event_id")) == _identity(trigger_event_id)
        for event in ledger.get("events", [])
        if isinstance(event, dict)
    ):
        raise ValueError("trigger_event_id must identify the not-yet-recorded re-audit event")
    if any(
        _identity(record.get("trigger_event_id")) == _identity(trigger_event_id)
        for record in ledger.get("authority_reopens", [])
        if isinstance(record, dict)
    ):
        raise ValueError("trigger_event_id is already bound to an authority reopen")
    now = _timestamp(timestamp)
    record = {
        "scope_key": scope_key,
        "basis_event_id": scope["last_event_id"],
        "authority": "independent_audit",
        "reason": "fingerprint-invalidating explicit re-audit",
        "trigger_event_id": str(trigger_event_id),
        "timestamp": now,
    }
    scope["closure_state"] = "reopened_by_independent_reaudit"
    scope["can_advance"] = False
    scope["authority_reopen"] = copy.deepcopy(record)
    ledger.setdefault("authority_reopens", []).append(record)
    ledger["updated_at"] = now
    return copy.deepcopy(record)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) * 100.0 / float(denominator), 2)


def summarize(ledger: dict[str, Any]) -> dict[str, Any]:
    events = ledger.get("events", [])
    issues = ledger.get("issues", [])
    scopes = ledger.get("scope_states", [])
    candidates = ledger.get("rule_candidates", [])
    totals = {
        "checked_shot_count": 0,
        "problem_shot_count": 0,
        "total_error_points": 0,
        "initial_error_count": 0,
        "residual_error_count": 0,
        "introduced_error_count": 0,
        "blocking_error_count": 0,
    }
    for event in events:
        counts = _event_counts(event)
        for key in totals:
            totals[key] += int(counts.get(key, 0))
    initial = totals["initial_error_count"]
    residual = totals["residual_error_count"]
    introduced = totals["introduced_error_count"]
    independent_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("audit_class") == "independent_audit"
    ]
    audit_cycles: dict[str, list[dict[str, Any]]] = {}
    for event in independent_events:
        cycle_id = str(event.get("audit_cycle_id") or "")
        if cycle_id:
            audit_cycles.setdefault(cycle_id, []).append(event)
    first_audits: list[dict[str, Any]] = []
    latest_audits: list[dict[str, Any]] = []
    for cycle_events in audit_cycles.values():
        ordered = sorted(
            cycle_events,
            key=lambda row: (
                int(row.get("audit_round", 0)),
                str(row.get("timestamp") or ""),
            ),
        )
        first_audits.append(ordered[0])
        latest_audits.append(ordered[-1])

    first_checked = 0
    first_problem_shots = 0
    for event in first_audits:
        quality = event.get("first_draft_quality")
        if isinstance(quality, dict):
            first_checked += int(quality.get("checked_shot_count", 0))
            first_problem_shots += int(
                quality.get("unique_affected_shot_count", 0)
            )
        else:
            counts = _event_counts(event)
            first_checked += int(counts.get("checked_shot_count", 0))
            first_problem_shots += int(counts.get("problem_shot_count", 0))

    passed_latest = [
        event for event in latest_audits if event.get("result") == "pass"
    ]
    signed_passed = 0
    for event in passed_latest:
        expected_scope = _scope_key(ledger, event)
        if any(
            scope.get("scope_key") == expected_scope
            and scope.get("closure_state") == "human_signed_off"
            for scope in scopes
            if isinstance(scope, dict)
        ):
            signed_passed += 1

    open_issues = [
        issue
        for issue in issues
        if isinstance(issue, dict) and issue.get("status") == "open"
    ]
    open_pattern_keys = {
        str(
            issue.get("pattern_key")
            or issue_pattern_key(str(ledger.get("project_id") or ""), issue)
        )
        for issue in open_issues
    }
    open_blocking_pattern_keys = {
        str(
            issue.get("pattern_key")
            or issue_pattern_key(str(ledger.get("project_id") or ""), issue)
        )
        for issue in open_issues
        if bool(issue.get("blocking"))
        or issue.get("severity") in {"fail", "blocking", "error"}
    }
    current_independent = independent_events[-1] if independent_events else None
    current_counts = (
        _event_counts(current_independent)
        if isinstance(current_independent, dict)
        else None
    )
    metrics = {
        **totals,
        "historical_checked_shot_observations": totals["checked_shot_count"],
        "historical_issue_point_observations": totals["total_error_points"],
        "first_draft_shot_correctness_percent": _rate(
            max(first_checked - first_problem_shots, 0), first_checked
        ),
        "first_draft_problem_shot_rate_percent": _rate(
            first_problem_shots, first_checked
        ),
        "independent_audit_cycle_pass_rate_percent": _rate(
            len(passed_latest), len(latest_audits)
        ),
        "human_release_signoff_completion_percent": _rate(
            signed_passed, len(passed_latest)
        ),
        "open_issue_pattern_count": len(open_pattern_keys),
        "open_blocking_pattern_count": len(open_blocking_pattern_keys),
        "current_artifact_issue_point_count": (
            int(current_counts.get("total_error_points", 0))
            if current_counts is not None
            else None
        ),
        "current_artifact_problem_shot_count": (
            int(current_counts.get("problem_shot_count", 0))
            if current_counts is not None
            else None
        ),
        "consecutive_2_issue_count": sum(issue.get("max_consecutive_rounds", 0) >= 2 for issue in issues),
        "consecutive_3_system_issue_count": sum(issue.get("max_consecutive_rounds", 0) >= 3 for issue in issues),
        "first_repair_effectiveness_percent": _rate(max(initial - residual, 0), initial),
        "residual_rate_percent": _rate(residual, initial),
        "repair_side_effect_rate_percent": _rate(introduced, max(initial + residual, 1)),
        "archived_scope_rate_percent": _rate(
            sum(scope.get("closure_state") == "human_signed_off" for scope in scopes),
            sum(scope.get("closure_state") != "preflight_clean" for scope in scopes),
        ),
    }
    candidate_status_counts: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("status"))
        candidate_status_counts[status] = candidate_status_counts.get(status, 0) + 1
    latest_event = events[-1] if events else None
    return {
        "schema_version": ledger.get("schema_version"),
        "project_name": ledger.get("project_name"),
        "event_count": len(events),
        "scope_count": len(scopes),
        "open_issue_count": len(open_issues),
        "open_issue_pattern_count": len(open_pattern_keys),
        "open_blocking_pattern_count": len(open_blocking_pattern_keys),
        "issue_record_count": len(issues),
        "rule_candidate_status_counts": candidate_status_counts,
        "metrics": metrics,
        "latest_event": None if latest_event is None else {
            "event_id": latest_event.get("event_id"),
            "episode_id": latest_event.get("episode_id"),
            "audit_round": latest_event.get("audit_round"),
            "result": latest_event.get("result"),
            "counts": _event_counts(latest_event),
        },
        "advance_ready_scope_count": sum(scope.get("can_advance") is True for scope in scopes),
        "awaiting_human_signoff_count": sum(scope.get("closure_state") == "awaiting_human_signoff" for scope in scopes),
    }


def active_prevention_profile(ledger: dict[str, Any]) -> dict[str, Any]:
    """Return known patterns that generation must scan before authoring."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in ledger.get("issues", []):
        if not isinstance(record, dict):
            continue
        pattern_key = str(
            record.get("pattern_key")
            or issue_pattern_key(str(ledger.get("project_id") or ""), record)
        )
        grouped.setdefault(pattern_key, []).append(record)
    patterns = []
    for pattern_key, records in grouped.items():
        representative = records[0]
        observation_count = sum(
            int(row.get("observation_count", row.get("occurrence_count", 0)))
            for row in records
        )
        occurrence_count = len(records)
        max_consecutive = max(
            (int(row.get("max_consecutive_rounds", 0)) for row in records),
            default=0,
        )
        blocking = bool(
            occurrence_count >= 2
            or max_consecutive >= 2
            or any(
                row.get("root_cause_gate") in {"required", "recorded"}
                for row in records
            )
        )
        last_seen = max(
            (str(row.get("last_seen_at") or "") for row in records),
            default="",
        )
        patterns.append(
            {
                "pattern_key": pattern_key,
                "error_code": representative.get("error_code"),
                "category": representative.get("category"),
                "field_class": representative.get("field_class")
                or _field_class(representative.get("field")),
                "normalized_root_cause": representative.get("normalized_root_cause")
                or _normalized_root_cause(representative),
                "status": (
                    "open" if any(row.get("status") == "open" for row in records) else "resolved"
                ),
                "occurrence_count": occurrence_count,
                "observation_count": observation_count,
                "affected_issue_ids": sorted(str(row.get("issue_id")) for row in records),
                "affected_episode_ids": sorted(
                    {str(row.get("episode_id")) for row in records if row.get("episode_id")}
                ),
                "affected_file_names": sorted(
                    {str(row.get("logical_file")) for row in records if row.get("logical_file")}
                ),
                "recurrence_state": (
                    "cross_batch_recurrence"
                    if occurrence_count >= 2
                    else representative.get("recurrence_state")
                ),
                "repair_strategy": (
                    "root_cause_repair_required"
                    if blocking
                    else representative.get("repair_strategy")
                ),
                "block_generation": blocking,
                "last_seen_at": last_seen or None,
            }
        )
    patterns.sort(
        key=lambda row: (
            not bool(row["block_generation"]),
            str(row.get("error_code") or ""),
            str(row.get("pattern_key") or ""),
        )
    )
    return {
        "known_pattern_count": len(patterns),
        "blocking_pattern_count": sum(bool(row["block_generation"]) for row in patterns),
        "patterns": patterns,
    }


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--output", required=True)
    init.add_argument("--project-name", required=True)
    init.add_argument("--project-id")
    init.add_argument("--timestamp")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--ledger", required=True)
    ingest.add_argument("--event", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--ledger", required=True)

    summary = subparsers.add_parser("summary")
    summary.add_argument("--ledger", required=True)

    candidate_check = subparsers.add_parser("candidate-check")
    candidate_check.add_argument("--ledger", required=True)
    candidate_check.add_argument("--candidate-id", required=True)
    candidate_check.add_argument("--validator-test-id", action="append", default=[])
    candidate_check.add_argument("--golden-test-id", action="append", default=[])
    candidate_check.add_argument("--conflict-check", choices=["pass", "fail"], required=True)
    candidate_check.add_argument("--all-tests-pass", action="store_true")
    candidate_check.add_argument("--timestamp")

    approve = subparsers.add_parser("approve")
    approve.add_argument("--ledger", required=True)
    approve.add_argument("--candidate-id", required=True)
    approve.add_argument("--decision", choices=["approve", "reject"], required=True)
    approve.add_argument("--timestamp")

    signoff = subparsers.add_parser("signoff")
    signoff.add_argument("--ledger", required=True)
    signoff.add_argument("--scope-key", required=True)
    signoff.add_argument("--decision", choices=["approve", "reopen"], required=True)
    signoff.add_argument("--timestamp")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            ledger = new_ledger(args.project_name, args.project_id, args.timestamp)
            write_json_atomic(args.output, ledger)
            _print({"status": "created", "ledger": args.output, "summary": summarize(ledger)})
            return 0
        ledger = read_json(args.ledger)
        if args.command == "validate":
            errors = validate_ledger(ledger)
            _print({"status": "pass" if not errors else "fail", "error_count": len(errors), "errors": errors})
            return 0 if not errors else 1
        if args.command == "summary":
            errors = validate_ledger(ledger)
            if errors:
                raise ValueError("invalid ledger: " + "; ".join(errors))
            _print(summarize(ledger))
            return 0
        if args.command == "ingest":
            result = ingest_event(ledger, read_json(args.event))
        elif args.command == "candidate-check":
            result = check_candidate(
                ledger,
                args.candidate_id,
                args.validator_test_id,
                args.golden_test_id,
                args.conflict_check,
                args.all_tests_pass,
                args.timestamp,
            )
        elif args.command == "approve":
            result = decide_candidate(ledger, args.candidate_id, args.decision, args.timestamp)
        elif args.command == "signoff":
            result = signoff_scope(ledger, args.scope_key, args.decision, args.timestamp)
        else:
            raise ValueError(f"unsupported command: {args.command}")
        errors = validate_ledger(ledger)
        if errors:
            raise ValueError("post-update ledger validation failed: " + "; ".join(errors))
        write_json_atomic(args.ledger, ledger)
        _print(result)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print({"status": "fail", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
