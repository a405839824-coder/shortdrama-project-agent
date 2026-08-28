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
TASK_TYPES = {"generation_audit", "audit", "audit_repair", "repair_regression", "bootstrap_import"}
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
        "historical_patterns": [],
    }


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
        for field in ["issue_id", "dedup_key", "error_code", "category", "status", "open_scope_keys", "occurrence_count", "consecutive_rounds", "max_consecutive_rounds", "history"]:
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
    normalized.setdefault("issue_type", issue.get("category"))
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
    candidate_id = f"RC-{_stable_hash(issue['issue_id'] + '|preventive-gate')}"
    return {
        "candidate_id": candidate_id,
        "created_at": event["timestamp"],
        "updated_at": event["timestamp"],
        "trigger_issue_ids": [issue["issue_id"]],
        "trigger_error_codes": [issue["error_code"]],
        "trigger_recurrence": {
            "occurrence_count": issue["occurrence_count"],
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
    records_by_key = {item["dedup_key"]: item for item in ledger["issues"]}
    incoming_ids: set[str] = set()
    created_candidate_ids: list[str] = []

    for issue in event["issues"]:
        dedup_key = _issue_dedup_key(ledger, event, issue)
        issue_id = f"ISS-{_stable_hash(dedup_key)}"
        incoming_ids.add(issue_id)
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
                "error_code": issue["error_code"],
                "category": issue["category"],
                "logical_file": event["file_name"],
                "episode_id": event["episode_id"],
                "issue_type": issue.get("issue_type", issue["category"]),
                "status": "open",
                "open_scope_keys": [scope_key],
                "recurrence_state": "first_occurrence",
                "occurrence_count": 1,
                "consecutive_rounds": 1,
                "max_consecutive_rounds": 1,
                "introduced_by_repair_count": int(issue["status"] == "introduced"),
                "first_seen_at": event["timestamp"],
                "last_seen_at": event["timestamp"],
                "root_cause_gate": "not_triggered",
                "repair_strategy": "local_repair",
                "history": [history_entry],
            }
            ledger["issues"].append(record)
            records_by_key[dedup_key] = record
        else:
            was_open = record["issue_id"] in previous_open
            record["occurrence_count"] += 1
            record["consecutive_rounds"] = record["consecutive_rounds"] + 1 if was_open else 1
            record["max_consecutive_rounds"] = max(record["max_consecutive_rounds"], record["consecutive_rounds"])
            record["introduced_by_repair_count"] += int(issue["status"] == "introduced")
            record["last_seen_at"] = event["timestamp"]
            record["status"] = "open"
            record["open_scope_keys"] = sorted(set(record.get("open_scope_keys", [])) | {scope_key})
            record["recurrence_state"] = "consecutive" if was_open else "recurred_after_resolution"
            record["history"].append(history_entry)

        if record["consecutive_rounds"] >= 3:
            record["root_cause_gate"] = "required"
            record["repair_strategy"] = "upstream_rebuild_required"
            record["recurrence_state"] = "systemic_three_rounds"
        elif record["consecutive_rounds"] >= 2:
            record["root_cause_gate"] = "recorded" if issue.get("root_cause") else "required"
            record["repair_strategy"] = "root_cause_repair_required"
            record["recurrence_state"] = "repeated_two_rounds"

        if record["max_consecutive_rounds"] >= 2:
            candidate = _candidate_for_issue(record, event)
            existing_candidate = next((item for item in ledger["rule_candidates"] if item["candidate_id"] == candidate["candidate_id"]), None)
            if existing_candidate is None:
                ledger["rule_candidates"].append(candidate)
                created_candidate_ids.append(candidate["candidate_id"])
            else:
                existing_candidate["updated_at"] = event["timestamp"]
                existing_candidate["trigger_recurrence"] = candidate["trigger_recurrence"]

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
    metrics = {
        **totals,
        "consecutive_2_issue_count": sum(issue.get("max_consecutive_rounds", 0) >= 2 for issue in issues),
        "consecutive_3_system_issue_count": sum(issue.get("max_consecutive_rounds", 0) >= 3 for issue in issues),
        "first_repair_effectiveness_percent": _rate(max(initial - residual, 0), initial),
        "residual_rate_percent": _rate(residual, initial),
        "repair_side_effect_rate_percent": _rate(introduced, max(initial + residual, 1)),
        "final_pass_rate_percent": _rate(sum(scope.get("closure_state") == "human_signed_off" for scope in scopes), len(scopes)),
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
        "open_issue_count": sum(issue.get("status") == "open" for issue in issues),
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
