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
from typing import Any


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
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: str | None) -> str:
    return value or utc_now()


def _stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _normalize(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+", "", text)


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
            "cross_batch_pattern_identity_required": True,
            "failed_draft_recording_required": True,
            "first_draft_correctness_threshold_percent": 98.0,
            "first_draft_direct_usability_threshold_percent": 98.0,
            "max_new_local_issue_rate_percent": 2.0,
            "minimum_episode_correctness_percent": 96.0,
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
) -> tuple[dict[str, Any], list[str]]:
    """Return a history-preserving candidate with cross-batch pattern identity."""

    if not isinstance(ledger, dict):
        raise ValueError("ledger must be an object")
    migrated = copy.deepcopy(ledger)
    changed: list[str] = []
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
            synthetic_event_id = f"LEGACY-{_stable_hash(str(record.get('issue_id') or index), 12)}"
            synthetic_scope_key = str(
                next(iter(record.get("open_scope_keys", [])), "LEGACY-SCOPE")
            )
            history_rows = [{
                "event_id": synthetic_event_id,
                "timestamp": str(record.get("last_seen_at") or migrated.get("updated_at") or _timestamp(timestamp)),
                "scope_key": synthetic_scope_key,
                "audit_round": 0,
                "source_version": "legacy",
                "migration_note": "legacy issue history reconstructed from retained issue record",
            }]
            record["history"] = copy.deepcopy(history_rows)
            changed.append(f"issues[{index}].history")
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
        if "occurrence_locations" not in record:
            record["occurrence_locations"] = []
            changed.append(f"issues[{index}].occurrence_locations")
        locations = record["occurrence_locations"]
        for location_index, location in enumerate(locations):
            if not isinstance(location, dict):
                continue
            for field, value in {
                "event_id": "unknown",
                "scope_key": "unknown",
                "episode_id": str(record.get("episode_id") or "unknown"),
                "file_name": str(record.get("logical_file") or "unknown"),
                "source_version": "unknown",
                "shot_ids": [],
                "field": "全表",
            }.items():
                if field not in location or location[field] in (None, ""):
                    location[field] = copy.deepcopy(value)
                    changed.append(
                        f"issues[{index}].occurrence_locations[{location_index}].{field}"
                    )
        for history in record.get("history", []):
            if not isinstance(history, dict):
                continue
            location = {
                "event_id": str(history.get("event_id") or "unknown"),
                "scope_key": str(history.get("scope_key") or "unknown"),
                "episode_id": str(history.get("episode_id") or record.get("episode_id") or "unknown"),
                "file_name": str(history.get("file_name") or record.get("logical_file") or "unknown"),
                "source_version": str(history.get("source_version") or "unknown"),
                "shot_ids": copy.deepcopy(history.get("shot_ids", [])),
                "field": str(history.get("field") or "全表"),
            }
            if location not in locations:
                locations.append(location)
                changed.append(f"issues[{index}].occurrence_locations")
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
    if changed:
        migrated["updated_at"] = _timestamp(timestamp)
        changed.append("updated_at")
    return migrated, sorted(set(changed))


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
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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
    if event.get("timestamp") and not _validate_iso_timestamp(event.get("timestamp")):
        errors.append("timestamp must be ISO-8601")
    if event.get("task_type") not in TASK_TYPES:
        errors.append(f"unsupported task_type: {event.get('task_type')!r}")
    round_value = event.get("audit_round")
    if not isinstance(round_value, int) or isinstance(round_value, bool) or not 1 <= round_value <= max_rounds:
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
    if event.get("result") == "pass" and issues:
        errors.append("result=pass requires zero issues")
    if event.get("result") in {"fail_current", "blocked"} and not issues and not event.get("blockers"):
        errors.append("failed or blocked event requires issues or blockers")
    return errors


def validate_ledger(ledger: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(ledger, dict):
        return ["ledger must be an object"]
    required = [
        "schema_version", "project_id", "project_name", "runtime_version", "workflow_revision",
        "agent_mode", "created_at", "updated_at", "policy", "counters", "scope_states",
        "events", "issues", "rule_candidates", "promotion_history", "human_signoffs",
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
        "cross_batch_pattern_identity_required": True,
        "failed_draft_recording_required": True,
        "first_draft_correctness_threshold_percent": 98.0,
        "first_draft_direct_usability_threshold_percent": 98.0,
        "max_new_local_issue_rate_percent": 2.0,
        "minimum_episode_correctness_percent": 96.0,
        "hard_major_known_systemic_tolerance": 0,
    }
    for field, value in policy_expected.items():
        if policy.get(field) != value:
            errors.append(f"policy.{field} must equal {value!r}")
    events = ledger.get("events")
    if not isinstance(events, list):
        errors.append("events must be an array")
        events = []
    event_ids: set[str] = set()
    for index, event in enumerate(events, 1):
        for error in validate_event(event, max_rounds=3):
            errors.append(f"events[{index}]: {error}")
        event_id = str(event.get("event_id")) if isinstance(event, dict) else ""
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
        issue_id = str(issue.get("issue_id", ""))
        dedup_key = str(issue.get("dedup_key", ""))
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
            not isinstance(value, str) or not value for value in open_scope_keys
        ):
            errors.append(f"issues[{index}].open_scope_keys must be an array of non-empty strings")
        elif len(open_scope_keys) != len(set(open_scope_keys)):
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
        candidate_id = str(candidate.get("candidate_id", ""))
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
        key = str(scope.get("scope_key", ""))
        if key in scope_keys:
            errors.append(f"duplicate scope_key: {key}")
        scope_keys.add(key)
        if scope.get("can_advance") is True:
            if scope.get("closure_state") != "human_signed_off" or scope.get("last_result") != "pass" or scope.get("open_issue_ids"):
                errors.append(f"scope {key} can_advance without clean human sign-off")
    return errors


def _scope_key(ledger: dict[str, Any], event: dict[str, Any]) -> str:
    value = "|".join([
        str(ledger["project_id"]),
        _normalize(event["episode_id"]),
        _normalize(event["file_name"]),
        _normalize(event["audit_cycle_id"]),
    ])
    return f"SCOPE-{_stable_hash(value)}"


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
    return {
        "checked_shot_count": int(event["checked_shot_count"]),
        "problem_shot_count": len(shot_ids),
        "total_error_points": len(issues),
        "initial_error_count": sum(issue["status"] == "initial" for issue in issues),
        "residual_error_count": sum(issue["status"] == "residual" for issue in issues),
        "introduced_error_count": sum(issue["status"] == "introduced" for issue in issues),
        "blocking_error_count": sum(bool(issue.get("blocking")) for issue in issues),
    }


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


def ingest_event(ledger: dict[str, Any], raw_event: dict[str, Any]) -> dict[str, Any]:
    ledger_errors = validate_ledger(ledger)
    if ledger_errors:
        raise ValueError("invalid ledger: " + "; ".join(ledger_errors))
    event_errors = validate_event(raw_event, int(ledger["policy"]["max_auto_repair_rounds"]))
    if event_errors:
        raise ValueError("invalid event: " + "; ".join(event_errors))
    if any(event["event_id"] == raw_event["event_id"] for event in ledger["events"]):
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
    elif scope.get("closure_state") == "awaiting_human_signoff":
        raise ValueError("scope has a clean pass and awaits human sign-off; sign off or reopen it before another event")
    elif scope.get("closure_state") == "human_signed_off":
        raise ValueError("scope is already signed off; start a new audit_cycle_id for a changed source")
    else:
        expected_round = int(scope["last_audit_round"]) + 1
        if event["audit_round"] != expected_round:
            raise ValueError(f"audit_round must continue from {scope['last_audit_round']} to {expected_round} in this cycle")

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
            record["history"].append({
                "event_id": event["event_id"],
                "timestamp": event["timestamp"],
                "scope_key": scope_key,
                "audit_round": event["audit_round"],
                "resolution": "not_present_in_follow-up_event",
            })

    scope["last_event_id"] = event["event_id"]
    scope["last_result"] = event["result"]
    scope["last_audit_round"] = event["audit_round"]
    scope["open_issue_ids"] = sorted(incoming_ids)
    scope["human_signoff"] = None
    scope["can_advance"] = False
    if event.get("task_type") == "production_draft_preflight":
        scope["closure_state"] = "preflight_clean" if event["result"] == "pass" else "open"
    else:
        scope["closure_state"] = "awaiting_human_signoff" if event["result"] == "pass" else "open"

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
) -> dict[str, Any]:
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


def decide_candidate(ledger: dict[str, Any], candidate_id: str, decision: str, timestamp: str | None = None) -> dict[str, Any]:
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


def signoff_scope(ledger: dict[str, Any], scope_key: str, decision: str, timestamp: str | None = None) -> dict[str, Any]:
    if decision not in {"approve", "reopen"}:
        raise ValueError("decision must be approve or reopen")
    scope = next((item for item in ledger["scope_states"] if item["scope_key"] == scope_key), None)
    if scope is None:
        raise ValueError(f"unknown scope_key: {scope_key}")
    now = _timestamp(timestamp)
    if decision == "approve":
        if scope.get("last_result") != "pass" or scope.get("open_issue_ids"):
            raise ValueError("human sign-off requires a clean pass and zero open issues")
        scope["closure_state"] = "human_signed_off"
        scope["can_advance"] = True
    else:
        scope["closure_state"] = "reopened_by_human"
        scope["can_advance"] = False
    record = {"scope_key": scope_key, "decision": decision, "timestamp": now}
    scope["human_signoff"] = record
    ledger["human_signoffs"].append(record)
    ledger["updated_at"] = now
    return {"scope_key": scope_key, "closure_state": scope["closure_state"], "can_advance": scope["can_advance"]}


def reopen_scope_for_independent_reaudit(
    ledger: dict[str, Any],
    scope_key: str,
    trigger_event_id: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Reopen a clean audit scope without fabricating a human decision."""

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
    now = _timestamp(timestamp)
    record = {
        "scope_key": scope_key,
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
        counts = event.get("counts") or _event_counts(event)
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
            counts = event.get("counts") or _event_counts(event)
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
        (current_independent.get("counts") or _event_counts(current_independent))
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
            "counts": latest_event.get("counts"),
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
