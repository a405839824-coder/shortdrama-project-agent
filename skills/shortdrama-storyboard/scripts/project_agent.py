#!/usr/bin/env python3
"""Deterministic project hub and work-order gates for the full storyboard Agent."""

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


SCHEMA_VERSION = "R10-PROJECT-AGENT-1"
RUNTIME_VERSION = "V16.3-148-R10-StableRuntime"
WORKFLOW_REVISION = "R10-PG1"
AGENT_MODE = "full_safe"
MODES = {"plan_only", "execute", "audit_only", "audit_and_repair"}
AUTHORIZATIONS = {"read_only", "plan_only", "explicit_execute", "pending"}
CHANGE_OPERATIONS = {"add", "modify", "delete", "replace", "merge", "split", "move"}
DESTRUCTIVE_CHANGE_OPERATIONS = {"delete", "replace", "merge", "move"}
MUTATING_OPERATIONS = {
    "script_create", "script_rewrite", "script_change", "dialogue_rewrite",
    "storyboard_create", "storyboard_regenerate", "storyboard_change", "storyboard_repair",
    "seedance_create", "anchor_management", "keyframe_prompt", "keyframe_image",
    "audio_bgm", "voice_design", "format_convert", "production_batch", "archive",
}
CHANGE_PARENT_OPERATIONS = {
    "script_rewrite", "script_change", "dialogue_rewrite", "change_impact",
    "storyboard_regenerate", "storyboard_change", "storyboard_repair",
}
STATES = {
    "intake", "scope_locked", "proposal_ready", "awaiting_approval", "approved",
    "executing", "validating", "passed", "awaiting_signoff", "archived",
    "failed_current", "blocked", "cancelled",
}
ALLOWED_TRANSITIONS = {
    "intake": {"scope_locked", "blocked", "cancelled"},
    "scope_locked": {"proposal_ready", "executing", "blocked", "cancelled"},
    "proposal_ready": {"awaiting_approval", "approved", "passed", "blocked", "cancelled"},
    "awaiting_approval": {"approved", "blocked", "cancelled"},
    "approved": {"executing", "blocked", "cancelled"},
    "executing": {"validating", "failed_current", "blocked"},
    "validating": {"passed", "failed_current", "blocked"},
    "failed_current": {"executing", "blocked", "cancelled"},
    "passed": {"awaiting_signoff"},
    "awaiting_signoff": {"approved", "cancelled"},
    "archived": set(),
    "blocked": {"scope_locked", "cancelled"},
    "cancelled": set(),
}

STORYBOARD_GATES = [
    "source_coverage", "camera_visibility", "spatial_continuity", "performance_chain",
    "shot_rhythm", "seedance_independence", "prohibitions_audio", "statistics",
]
SCRIPT_GATES = [
    "source_coverage", "character_cognition", "story_logic", "episode_boundary",
    "downstream_impact",
]
VISUAL_GATES = ["anchor_consistency", "camera_visibility", "spatial_continuity", "output_integrity"]
GENERAL_GATES = ["scope_lock", "output_integrity", "impact_regression"]


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
    return f"SBP-{_stable_hash(_normalize(project_name), 12)}"


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


def load_capability_registry() -> dict[str, Any]:
    return read_json(skill_root() / "references" / "project_agent_capabilities.json")


def new_hub(project_name: str, project_id: str | None = None, timestamp: str | None = None) -> dict[str, Any]:
    now = _timestamp(timestamp)
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id or _project_id(project_name),
        "project_name": project_name,
        "runtime_version": RUNTIME_VERSION,
        "workflow_revision": WORKFLOW_REVISION,
        "agent_mode": AGENT_MODE,
        "created_at": now,
        "updated_at": now,
        "linked_shared_files": {
            "project_hub_title": "分镜项目Agent_项目中枢.json",
            "issue_ledger_title": "分镜项目Agent_问题台账.json",
        },
        "policy": {
            "plan_only_never_mutates": True,
            "preserve_previous_version": True,
            "explicit_scope_required_for_destructive_change": True,
            "max_internal_repair_rounds": 3,
            "impact_regression_required": True,
            "human_signoff_required_for_archive": True,
            "formal_rule_auto_mutation": False,
            "unavailable_capability_must_be_recorded": True,
        },
        "capability_registry": load_capability_registry(),
        "project_state": {
            "current_stage": "intake",
            "authoritative_sources": [],
            "latest_artifacts": {},
            "current_episode": None,
            "locked_story_range": None,
            "characters": {},
            "scenes": {},
            "props": {},
            "open_decisions": [],
            "blocked_scopes": [],
        },
        "work_orders": [],
        "change_requests": [],
        "deliverables": [],
        "decision_log": [],
        "capability_gaps": [],
        "version_history": [],
        "human_signoffs": [],
        "counters": {
            "work_order_count": 0,
            "change_request_count": 0,
            "deliverable_count": 0,
            "decision_count": 0,
            "capability_gap_count": 0,
        },
    }


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def capability_map(hub: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capabilities = hub.get("capability_registry", {}).get("capabilities", [])
    return {str(item.get("operation")): item for item in capabilities if isinstance(item, dict)}


def required_validation_gates(operation: str) -> list[str]:
    if operation.startswith("storyboard_") or operation == "seedance_create":
        return STORYBOARD_GATES
    if operation.startswith("script_") or operation in {"dialogue_rewrite", "episode_plan", "rewrite_plan"}:
        return SCRIPT_GATES
    if operation in {"anchor_management", "keyframe_prompt", "keyframe_image"}:
        return VISUAL_GATES
    return GENERAL_GATES


def validate_work_order_request(request: Any, hub: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(request, dict):
        return ["work-order request must be an object"]
    required = [
        "work_order_id", "timestamp", "window_key", "request_text", "operation", "mode",
        "targets", "source_lock", "protected_scope", "expected_outputs", "authorization",
    ]
    for field in required:
        if field not in request or request[field] in (None, ""):
            errors.append(f"missing work-order field: {field}")
    if request.get("timestamp") and not _is_iso_timestamp(request.get("timestamp")):
        errors.append("timestamp must be ISO-8601")
    if request.get("operation") not in capability_map(hub):
        errors.append(f"unsupported operation: {request.get('operation')!r}")
    if request.get("mode") not in MODES:
        errors.append(f"unsupported mode: {request.get('mode')!r}")
    if request.get("authorization") not in AUTHORIZATIONS:
        errors.append(f"unsupported authorization: {request.get('authorization')!r}")
    for field in ["targets", "protected_scope", "expected_outputs"]:
        if field in request and not isinstance(request.get(field), list):
            errors.append(f"{field} must be an array")
    if "source_lock" in request and not isinstance(request.get("source_lock"), dict):
        errors.append("source_lock must be an object")
    elif isinstance(request.get("source_lock"), dict):
        source_lock = request["source_lock"]
        for field in ["authoritative_source", "latest_version", "must_keep", "must_remove"]:
            if field not in source_lock or source_lock[field] in (None, ""):
                errors.append(f"source_lock missing field: {field}")
        if not source_lock.get("episode_boundary") and not source_lock.get("target_boundary"):
            errors.append("source_lock requires episode_boundary or target_boundary")
        for field in ["must_keep", "must_remove"]:
            if field in source_lock and not isinstance(source_lock.get(field), list):
                errors.append(f"source_lock.{field} must be an array")
    if request.get("mode") == "plan_only" and request.get("authorization") not in {"plan_only", "read_only"}:
        errors.append("plan_only mode requires plan_only or read_only authorization")
    if request.get("mode") in {"execute", "audit_and_repair"} and request.get("authorization") == "read_only":
        errors.append("mutating mode cannot use read_only authorization")
    return errors


def _initial_status(request: dict[str, Any], hub: dict[str, Any]) -> str:
    operation = request["operation"]
    mode = request["mode"]
    authorization = request["authorization"]
    capability = capability_map(hub)[operation]
    if mode in {"plan_only", "audit_only"}:
        return "scope_locked"
    if authorization == "explicit_execute":
        return "approved"
    if capability.get("approval_policy") in {"read_only", "automatic_recording"}:
        return "scope_locked"
    return "awaiting_approval"


def create_work_order(hub: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    errors = validate_work_order_request(request, hub)
    if errors:
        raise ValueError("invalid work-order request: " + "; ".join(errors))
    existing = next((item for item in hub["work_orders"] if item["work_order_id"] == request["work_order_id"]), None)
    if existing is not None:
        return {"status": "duplicate", "work_order_id": existing["work_order_id"], "work_order_status": existing["status"]}
    work_order = copy.deepcopy(request)
    work_order.update({
        "status": _initial_status(request, hub),
        "internal_round": 0,
        "mutates_source": bool(request["mode"] in {"execute", "audit_and_repair"} and request["operation"] in MUTATING_OPERATIONS),
        "required_validation_gates": required_validation_gates(request["operation"]),
        "validation": {"status": "pending", "records": []},
        "deliverable_ids": [],
        "change_request_ids": [],
        "state_history": [{"timestamp": request["timestamp"], "from": None, "to": _initial_status(request, hub)}],
        "blockers": [],
        "updated_at": request["timestamp"],
    })
    if request["mode"] == "plan_only":
        work_order["mutates_source"] = False
    hub["work_orders"].append(work_order)
    hub["updated_at"] = request["timestamp"]
    hub["counters"]["work_order_count"] = len(hub["work_orders"])
    return {
        "status": "created",
        "work_order_id": work_order["work_order_id"],
        "work_order_status": work_order["status"],
        "mutates_source": work_order["mutates_source"],
        "required_validation_gates": work_order["required_validation_gates"],
    }


def validate_change_request(request: Any, hub: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(request, dict):
        return ["change request must be an object"]
    required = [
        "change_id", "work_order_id", "timestamp", "target_type", "target_file",
        "source_version", "operations", "authorization",
    ]
    for field in required:
        if field not in request or request[field] in (None, ""):
            errors.append(f"missing change-request field: {field}")
    if request.get("target_type") not in {"script", "storyboard", "script_and_storyboard"}:
        errors.append(f"unsupported target_type: {request.get('target_type')!r}")
    if request.get("authorization") not in AUTHORIZATIONS:
        errors.append(f"unsupported authorization: {request.get('authorization')!r}")
    parent = next((item for item in hub.get("work_orders", []) if item.get("work_order_id") == request.get("work_order_id")), None)
    if parent is None:
        errors.append(f"unknown work_order_id: {request.get('work_order_id')!r}")
    elif parent.get("operation") not in CHANGE_PARENT_OPERATIONS:
        errors.append(f"work order operation {parent.get('operation')!r} cannot own a change request")
    operations = request.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations must be a non-empty array")
        operations = []
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            errors.append(f"operations[{index}] must be an object")
            continue
        for field in ["operation", "target_ids", "instruction", "authoritative_basis", "protected_scope"]:
            if field not in operation or operation[field] in (None, ""):
                errors.append(f"operations[{index}] missing field: {field}")
        if operation.get("operation") not in CHANGE_OPERATIONS:
            errors.append(f"operations[{index}] unsupported operation: {operation.get('operation')!r}")
        for field in ["target_ids", "protected_scope"]:
            if field in operation and not isinstance(operation.get(field), list):
                errors.append(f"operations[{index}].{field} must be an array")
    return errors


def _impact_matrix(target_type: str, operations: list[dict[str, Any]]) -> list[str]:
    updates = {"version_history", "change_notes", "impact_regression", "statistics"}
    if target_type in {"script", "script_and_storyboard"}:
        updates.update({
            "episode_beats", "source_ledger", "cognition_matrix", "must_include_exclude",
            "scene_state_ledger", "shot_plan", "formal_storyboard", "seedance_batches",
            "anchors", "keyframe_assets", "voice_audio_bgm", "production_batches",
        })
    if target_type in {"storyboard", "script_and_storyboard"}:
        updates.update({
            "scene_state_ledger", "shot_plan", "formal_storyboard", "seedance_batches",
            "shot_numbering", "cumulative_timing", "anchors", "keyframe_assets",
            "voice_audio_bgm", "production_batches",
        })
    operation_names = {item["operation"] for item in operations}
    if operation_names & {"delete", "replace", "merge", "move"}:
        updates.update({"stale_reference_scan", "boundary_bridge", "must_exclude_scan"})
    if operation_names & {"add", "split"}:
        updates.update({"entry_exit_boundary", "shot_numbering", "cumulative_timing"})
    if operation_names & {"merge", "split"}:
        updates.update({"dialogue_capacity", "shot_load", "rhythm_regression"})
    if "move" in operation_names:
        updates.update({"causal_continuity", "time_continuity", "spatial_continuity"})
    return sorted(updates)


def create_change_request(hub: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    errors = validate_change_request(request, hub)
    if errors:
        raise ValueError("invalid change request: " + "; ".join(errors))
    existing = next((item for item in hub["change_requests"] if item["change_id"] == request["change_id"]), None)
    if existing is not None:
        return {"status": "duplicate", "change_id": existing["change_id"], "change_status": existing["status"]}
    work_order = next(item for item in hub["work_orders"] if item["work_order_id"] == request["work_order_id"])
    operation_names = {item["operation"] for item in request["operations"]}
    destructive = bool(operation_names & DESTRUCTIVE_CHANGE_OPERATIONS)
    approved = request["authorization"] == "explicit_execute" and work_order["authorization"] == "explicit_execute"
    change = copy.deepcopy(request)
    change.update({
        "destructive_within_scope": destructive,
        "preserve_previous_version": True,
        "required_downstream_updates": _impact_matrix(request["target_type"], request["operations"]),
        "status": "approved" if approved else "awaiting_approval",
        "applied_version": None,
        "validation_status": "pending",
    })
    if destructive and not approved:
        change["status"] = "awaiting_approval"
    hub["change_requests"].append(change)
    work_order["change_request_ids"].append(change["change_id"])
    hub["updated_at"] = request["timestamp"]
    hub["counters"]["change_request_count"] = len(hub["change_requests"])
    return {
        "status": "created",
        "change_id": change["change_id"],
        "change_status": change["status"],
        "destructive_within_scope": destructive,
        "required_downstream_updates": change["required_downstream_updates"],
    }


def _find_work_order(hub: dict[str, Any], work_order_id: str) -> dict[str, Any]:
    work_order = next((item for item in hub["work_orders"] if item["work_order_id"] == work_order_id), None)
    if work_order is None:
        raise ValueError(f"unknown work_order_id: {work_order_id}")
    return work_order


def record_deliverable(hub: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    required = [
        "deliverable_id", "work_order_id", "timestamp", "artifact_type", "file_name",
        "version", "fingerprint", "status",
    ]
    missing = [field for field in required if field not in record or record[field] in (None, "")]
    if missing:
        raise ValueError(f"deliverable missing fields: {missing}")
    if record["status"] not in {"draft", "validated", "final"}:
        raise ValueError("deliverable status must be draft, validated, or final")
    work_order = _find_work_order(hub, record["work_order_id"])
    existing = next((item for item in hub["deliverables"] if item["deliverable_id"] == record["deliverable_id"]), None)
    if existing is not None:
        return {"status": "duplicate", "deliverable_id": existing["deliverable_id"]}
    deliverable = copy.deepcopy(record)
    deliverable.setdefault("source_versions", [])
    deliverable.setdefault("supersedes", None)
    hub["deliverables"].append(deliverable)
    work_order["deliverable_ids"].append(deliverable["deliverable_id"])
    work_order["updated_at"] = deliverable["timestamp"]
    hub["updated_at"] = deliverable["timestamp"]
    hub["counters"]["deliverable_count"] = len(hub["deliverables"])
    if deliverable["status"] in {"validated", "final"}:
        hub["project_state"]["latest_artifacts"][deliverable["artifact_type"]] = {
            "deliverable_id": deliverable["deliverable_id"],
            "file_name": deliverable["file_name"],
            "version": deliverable["version"],
            "fingerprint": deliverable["fingerprint"],
        }
    return {"status": "recorded", "deliverable_id": deliverable["deliverable_id"]}


def record_validation(hub: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    required = [
        "work_order_id", "timestamp", "result", "gate_results", "residual_issue_count",
        "introduced_issue_count", "blockers",
    ]
    missing = [field for field in required if field not in record or record[field] in (None, "") and field != "blockers"]
    if missing:
        raise ValueError(f"validation record missing fields: {missing}")
    if record["result"] not in {"pass", "fail"}:
        raise ValueError("validation result must be pass or fail")
    if not isinstance(record["gate_results"], dict):
        raise ValueError("gate_results must be an object")
    if not isinstance(record["blockers"], list):
        raise ValueError("blockers must be an array")
    work_order = _find_work_order(hub, record["work_order_id"])
    required_gates = work_order["required_validation_gates"]
    missing_gates = [gate for gate in required_gates if gate not in record["gate_results"]]
    failed_gates = [gate for gate in required_gates if record["gate_results"].get(gate) != "pass"]
    clean = (
        record["result"] == "pass"
        and not missing_gates
        and not failed_gates
        and int(record["residual_issue_count"]) == 0
        and int(record["introduced_issue_count"]) == 0
        and not record["blockers"]
    )
    validation_record = copy.deepcopy(record)
    validation_record["missing_gates"] = missing_gates
    validation_record["failed_gates"] = failed_gates
    validation_record["clean"] = clean
    work_order["validation"]["records"].append(validation_record)
    work_order["validation"]["status"] = "pass" if clean else "fail"
    work_order["updated_at"] = record["timestamp"]
    hub["updated_at"] = record["timestamp"]
    return {
        "status": work_order["validation"]["status"],
        "work_order_id": work_order["work_order_id"],
        "missing_gates": missing_gates,
        "failed_gates": failed_gates,
    }


def transition_work_order(
    hub: dict[str, Any], work_order_id: str, target_state: str, timestamp: str | None = None
) -> dict[str, Any]:
    if target_state not in STATES:
        raise ValueError(f"unsupported target state: {target_state}")
    work_order = _find_work_order(hub, work_order_id)
    current = work_order["status"]
    if target_state not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid transition: {current} -> {target_state}")
    if target_state == "executing":
        if work_order["authorization"] not in {"explicit_execute", "plan_only", "read_only"}:
            raise ValueError("execution requires explicit authorization or a non-mutating mode")
        if current == "failed_current" and work_order["internal_round"] >= hub["policy"]["max_internal_repair_rounds"]:
            raise ValueError("maximum internal repair rounds reached; upstream rebuild or new work order required")
        work_order["internal_round"] += 1
    if target_state == "validating" and not work_order["deliverable_ids"]:
        raise ValueError("validation requires at least one recorded deliverable")
    if target_state == "passed" and work_order["validation"]["status"] != "pass":
        raise ValueError("passed state requires a clean validation record")
    now = _timestamp(timestamp)
    work_order["status"] = target_state
    work_order["updated_at"] = now
    work_order["state_history"].append({"timestamp": now, "from": current, "to": target_state})
    hub["updated_at"] = now
    return {
        "work_order_id": work_order_id,
        "from": current,
        "to": target_state,
        "internal_round": work_order["internal_round"],
    }


def signoff_work_order(
    hub: dict[str, Any], work_order_id: str, decision: str, timestamp: str | None = None
) -> dict[str, Any]:
    if decision not in {"approve", "reopen"}:
        raise ValueError("decision must be approve or reopen")
    work_order = _find_work_order(hub, work_order_id)
    if work_order["status"] != "awaiting_signoff":
        raise ValueError("human sign-off requires awaiting_signoff state")
    now = _timestamp(timestamp)
    if decision == "approve":
        prior = work_order["status"]
        work_order["status"] = "archived"
        work_order["state_history"].append({"timestamp": now, "from": prior, "to": "archived"})
    else:
        prior = work_order["status"]
        work_order["status"] = "approved"
        work_order["validation"]["status"] = "pending"
        work_order["state_history"].append({"timestamp": now, "from": prior, "to": "approved"})
    signoff = {"work_order_id": work_order_id, "decision": decision, "timestamp": now}
    hub["human_signoffs"].append(signoff)
    work_order["updated_at"] = now
    hub["updated_at"] = now
    return {"work_order_id": work_order_id, "decision": decision, "status": work_order["status"]}


def record_capability_gap(hub: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    required = [
        "gap_id", "timestamp", "request", "missing_dependency", "risk", "safe_fallback",
        "proposed_implementation", "required_tests",
    ]
    missing = [field for field in required if field not in record or record[field] in (None, "")]
    if missing:
        raise ValueError(f"capability gap missing fields: {missing}")
    if not isinstance(record["required_tests"], list) or not record["required_tests"]:
        raise ValueError("required_tests must be a non-empty array")
    existing = next((item for item in hub["capability_gaps"] if item["gap_id"] == record["gap_id"]), None)
    if existing is not None:
        return {"status": "duplicate", "gap_id": existing["gap_id"]}
    gap = copy.deepcopy(record)
    gap.update({"status": "proposed", "human_decision": "pending", "formal_capability_mutated": False})
    hub["capability_gaps"].append(gap)
    hub["updated_at"] = gap["timestamp"]
    hub["counters"]["capability_gap_count"] = len(hub["capability_gaps"])
    return {"status": "recorded", "gap_id": gap["gap_id"], "formal_capability_mutated": False}


def validate_hub(hub: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(hub, dict):
        return ["hub must be an object"]
    required = [
        "schema_version", "project_id", "project_name", "runtime_version", "workflow_revision",
        "agent_mode", "created_at", "updated_at", "linked_shared_files", "policy",
        "capability_registry", "project_state", "work_orders", "change_requests",
        "deliverables", "decision_log", "capability_gaps", "version_history",
        "human_signoffs", "counters",
    ]
    for field in required:
        if field not in hub:
            errors.append(f"missing hub field: {field}")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "workflow_revision": WORKFLOW_REVISION,
        "agent_mode": AGENT_MODE,
    }
    for field, value in expected.items():
        if hub.get(field) != value:
            errors.append(f"{field} must equal {value!r}")
    policy = hub.get("policy", {})
    policy_expected = {
        "plan_only_never_mutates": True,
        "preserve_previous_version": True,
        "explicit_scope_required_for_destructive_change": True,
        "max_internal_repair_rounds": 3,
        "impact_regression_required": True,
        "human_signoff_required_for_archive": True,
        "formal_rule_auto_mutation": False,
    }
    for field, value in policy_expected.items():
        if policy.get(field) != value:
            errors.append(f"policy.{field} must equal {value!r}")
    capabilities = hub.get("capability_registry", {}).get("capabilities", [])
    operations = [item.get("operation") for item in capabilities if isinstance(item, dict)]
    if len(operations) != len(set(operations)) or not operations:
        errors.append("capability operations must be non-empty and unique")
    work_orders = hub.get("work_orders", [])
    if not isinstance(work_orders, list):
        errors.append("work_orders must be an array")
        work_orders = []
    work_order_ids: set[str] = set()
    for index, work_order in enumerate(work_orders, 1):
        if not isinstance(work_order, dict):
            errors.append(f"work_orders[{index}] must be an object")
            continue
        for field in [
            "work_order_id", "operation", "mode", "authorization", "status", "internal_round",
            "mutates_source", "validation", "deliverable_ids", "change_request_ids", "state_history",
        ]:
            if field not in work_order:
                errors.append(f"work_orders[{index}] missing field: {field}")
        work_order_id = str(work_order.get("work_order_id", ""))
        if work_order_id in work_order_ids:
            errors.append(f"duplicate work_order_id: {work_order_id}")
        work_order_ids.add(work_order_id)
        if work_order.get("operation") not in operations:
            errors.append(f"work order {work_order_id} has unsupported operation")
        if work_order.get("mode") == "plan_only" and work_order.get("mutates_source") is not False:
            errors.append(f"work order {work_order_id} mutates source in plan_only mode")
        if not isinstance(work_order.get("internal_round"), int) or not 0 <= work_order.get("internal_round", -1) <= 3:
            errors.append(f"work order {work_order_id} has invalid internal_round")
        if work_order.get("status") == "archived":
            signed = any(item.get("work_order_id") == work_order_id and item.get("decision") == "approve" for item in hub.get("human_signoffs", []))
            if not signed:
                errors.append(f"work order {work_order_id} archived without human sign-off")
    change_requests = hub.get("change_requests", [])
    if not isinstance(change_requests, list):
        errors.append("change_requests must be an array")
        change_requests = []
    change_ids: set[str] = set()
    for index, change in enumerate(change_requests, 1):
        if not isinstance(change, dict):
            errors.append(f"change_requests[{index}] must be an object")
            continue
        change_id = str(change.get("change_id", ""))
        if change_id in change_ids:
            errors.append(f"duplicate change_id: {change_id}")
        change_ids.add(change_id)
        if change.get("destructive_within_scope") and change.get("status") == "approved" and change.get("authorization") != "explicit_execute":
            errors.append(f"change {change_id} bypasses destructive approval")
        if change.get("preserve_previous_version") is not True:
            errors.append(f"change {change_id} does not preserve prior version")
    for collection_name, id_field in [("deliverables", "deliverable_id"), ("capability_gaps", "gap_id")]:
        collection = hub.get(collection_name, [])
        if not isinstance(collection, list):
            errors.append(f"{collection_name} must be an array")
            continue
        seen: set[str] = set()
        for item in collection:
            value = str(item.get(id_field, "")) if isinstance(item, dict) else ""
            if value in seen:
                errors.append(f"duplicate {id_field}: {value}")
            seen.add(value)
    return errors


def summarize(hub: dict[str, Any]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for work_order in hub.get("work_orders", []):
        status = str(work_order.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
    group_counts: dict[str, int] = {}
    capability_status_counts: dict[str, int] = {}
    for capability in hub.get("capability_registry", {}).get("capabilities", []):
        group = str(capability.get("group"))
        status = str(capability.get("status"))
        group_counts[group] = group_counts.get(group, 0) + 1
        capability_status_counts[status] = capability_status_counts.get(status, 0) + 1
    latest = hub["work_orders"][-1] if hub.get("work_orders") else None
    return {
        "schema_version": hub.get("schema_version"),
        "project_name": hub.get("project_name"),
        "capability_count": sum(capability_status_counts.values()),
        "capability_status_counts": capability_status_counts,
        "capability_group_counts": group_counts,
        "work_order_count": len(hub.get("work_orders", [])),
        "work_order_status_counts": status_counts,
        "change_request_count": len(hub.get("change_requests", [])),
        "deliverable_count": len(hub.get("deliverables", [])),
        "capability_gap_count": len(hub.get("capability_gaps", [])),
        "latest_work_order": None if latest is None else {
            "work_order_id": latest.get("work_order_id"),
            "operation": latest.get("operation"),
            "mode": latest.get("mode"),
            "status": latest.get("status"),
        },
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

    for name, file_flag in [
        ("create-task", "--request"),
        ("create-change", "--request"),
        ("record-deliverable", "--record"),
        ("record-validation", "--record"),
        ("gap", "--record"),
    ]:
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--hub", required=True)
        subparser.add_argument(file_flag, required=True)

    transition = subparsers.add_parser("transition")
    transition.add_argument("--hub", required=True)
    transition.add_argument("--work-order-id", required=True)
    transition.add_argument("--to", choices=sorted(STATES), required=True)
    transition.add_argument("--timestamp")

    signoff = subparsers.add_parser("signoff")
    signoff.add_argument("--hub", required=True)
    signoff.add_argument("--work-order-id", required=True)
    signoff.add_argument("--decision", choices=["approve", "reopen"], required=True)
    signoff.add_argument("--timestamp")

    validate = subparsers.add_parser("validate")
    validate.add_argument("--hub", required=True)

    summary = subparsers.add_parser("summary")
    summary.add_argument("--hub", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            hub = new_hub(args.project_name, args.project_id, args.timestamp)
            write_json_atomic(args.output, hub)
            _print({"status": "created", "hub": args.output, "summary": summarize(hub)})
            return 0
        hub = read_json(args.hub)
        if args.command == "validate":
            errors = validate_hub(hub)
            _print({"status": "pass" if not errors else "fail", "error_count": len(errors), "errors": errors})
            return 0 if not errors else 1
        errors = validate_hub(hub)
        if errors:
            raise ValueError("invalid hub: " + "; ".join(errors))
        if args.command == "summary":
            _print(summarize(hub))
            return 0
        if args.command == "create-task":
            result = create_work_order(hub, read_json(args.request))
        elif args.command == "create-change":
            result = create_change_request(hub, read_json(args.request))
        elif args.command == "record-deliverable":
            result = record_deliverable(hub, read_json(args.record))
        elif args.command == "record-validation":
            result = record_validation(hub, read_json(args.record))
        elif args.command == "transition":
            result = transition_work_order(hub, args.work_order_id, args.to, args.timestamp)
        elif args.command == "signoff":
            result = signoff_work_order(hub, args.work_order_id, args.decision, args.timestamp)
        elif args.command == "gap":
            result = record_capability_gap(hub, read_json(args.record))
        else:
            raise ValueError(f"unsupported command: {args.command}")
        post_errors = validate_hub(hub)
        if post_errors:
            raise ValueError("post-update hub validation failed: " + "; ".join(post_errors))
        write_json_atomic(args.hub, hub)
        _print(result)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print({"status": "fail", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
