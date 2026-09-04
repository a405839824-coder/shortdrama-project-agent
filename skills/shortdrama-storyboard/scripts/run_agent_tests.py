#!/usr/bin/env python3
"""Run deterministic safety and lifecycle tests for storyboard_agent.py."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parent.parent
AGENT_PATH = Path(__file__).resolve().parent / "storyboard_agent.py"
RUNTIME_PATH = ROOT / "references" / "storyboard_runtime_rules.json"
SCHEMA_PATH = ROOT / "references" / "agent_ledger_schema.json"


def load_agent():
    spec = importlib.util.spec_from_file_location("r10_storyboard_agent", AGENT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Agent: {AGENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_agent()
LEDGER_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
EVENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": LEDGER_SCHEMA["$defs"],
    "$ref": "#/$defs/current_audit_event",
}
EVENT_SCHEMA_VALIDATOR = Draft202012Validator(EVENT_SCHEMA)
SEALED_LEGACY_EVENT_SCHEMA_VALIDATOR = Draft202012Validator({
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": LEDGER_SCHEMA["$defs"],
    "$ref": "#/$defs/audit_event",
})
LEDGER_SCHEMA_VALIDATOR = Draft202012Validator(LEDGER_SCHEMA)


def event(
    event_id: str,
    audit_round: int,
    status: str = "initial",
    result: str = "fail_current",
    cycle: str = "CYCLE-001",
    category: str = "表演链",
) -> dict[str, Any]:
    issues = [] if result == "pass" else [{
        "error_code": "E-PERFORM-TEST",
        "category": category,
        "severity": "fail",
        "blocking": True,
        "shot_ids": ["S03"],
        "field": "人物表演链",
        "issue_type": "听话反应缺失",
        "evidence": "角色听到关键台词后直接执行动作，没有接收和反应过程。",
        "source_sentence": "角色听见对方说出真相。",
        "repair_action": "补齐刺激、接收、视线、微表情、生态动作、呼吸和结束状态。",
        "impact_scope": ["S02", "S03", "S04"],
        "status": status,
    }]
    return {
        "event_id": event_id,
        "timestamp": f"2026-08-14T00:0{audit_round}:00Z",
        "window_key": "test-window",
        "episode_id": "EP01",
        "file_name": "episode-01.json",
        "file_fingerprint": f"sha256:test-{event_id}",
        "source_version": f"v{audit_round}",
        "audit_cycle_id": cycle,
        "task_type": "audit_repair",
        "audit_round": audit_round,
        "result": result,
        "checked_shot_count": 10,
        "blockers": [],
        "issues": issues,
    }


def fresh() -> dict[str, Any]:
    return agent.new_ledger("分镜项目", "SB-TEST", "2026-08-14T00:00:00Z")


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def independent_event(
    event_id: str,
    audit_round: int,
    *,
    result: str = "pass",
    status: str = "initial",
    cycle: str = "CYCLE-INDEPENDENT",
    file_name: str = "episode-independent.json",
    episode_id: str = "EP01",
    binding_label: str | None = None,
) -> dict[str, Any]:
    label = binding_label or event_id
    value = event(
        event_id,
        audit_round,
        status=status,
        result=result,
        cycle=cycle,
    )
    value.update({
        "episode_id": episode_id,
        "file_name": file_name,
        "file_fingerprint": digest(f"file:{label}"),
        "source_version": f"source-{label}",
        "task_type": "audit",
        "audit_class": "independent_audit",
        "validation_role": "independent_audit",
        "handoff_id": f"HND-{label}",
        "artifact_semantic_fingerprint": digest(f"artifact:{label}"),
        "canonical_fingerprint": digest(f"canonical:{label}"),
        "source_fingerprint": digest("source:stable"),
        "boundary_fingerprint": digest("boundary:stable"),
        "runtime_fingerprint": digest("runtime:stable"),
        "rules_fingerprint": digest("rules:stable"),
        "validator_fingerprint": digest("validator:stable"),
        "runtime_test_certificate_id": "RTC-STABLE",
        "stage_trace_id": f"TRC-{label}",
        "semantic_audit_sha256": digest(f"semantic-audit:{label}"),
        "semantic_report_input_sha256": digest(
            f"semantic-report-input:{label}"
        ),
        "cache_evidence": [],
        "independence_assertions": {
            "artifact_read_only": True,
            "production_verdict_not_reused": True,
            "quality_conclusions_recomputed": True,
            "eight_layers_completed": True,
        },
    })
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def ledger_bytes(ledger: dict[str, Any]) -> bytes:
    return json.dumps(
        ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def assert_rejected_without_mutation(
    ledger: dict[str, Any],
    operation: Callable[[], Any],
    message: str,
) -> None:
    before = ledger_bytes(ledger)
    try:
        operation()
    except ValueError:
        pass
    else:
        raise AssertionError(message)
    assert_true(ledger_bytes(ledger) == before, f"{message}: ledger mutated")


def schema_event_errors(value: dict[str, Any]) -> list[str]:
    return [error.message for error in EVENT_SCHEMA_VALIDATOR.iter_errors(value)]


def checkpoint_authorizer(value: dict[str, Any]) -> Callable[[dict[str, Any]], bool]:
    expected = copy.deepcopy(value["replay_checkpoint"])
    return lambda candidate: candidate == expected


def multi_issue_event(
    event_id: str,
    audit_round: int,
    issue_count: int,
    *,
    cycle: str,
    file_name: str,
    episode_id: str,
) -> dict[str, Any]:
    value = event(event_id, audit_round, cycle=cycle)
    value.update({"file_name": file_name, "episode_id": episode_id})
    template = copy.deepcopy(value["issues"][0])
    value["issues"] = []
    for index in range(1, issue_count + 1):
        issue = copy.deepcopy(template)
        issue.update({
            "error_code": f"E-LEGACY-{event_id}-{index:02}",
            "shot_ids": [f"S{index:03}"],
            "field": f"legacy_field_{index:02}",
            "issue_type": f"legacy_issue_{event_id}_{index:02}",
            "evidence": f"sealed legacy evidence {event_id}/{index}",
        })
        value["issues"].append(issue)
    return value


def synthetic_legacy_33_event_ledger() -> dict[str, Any]:
    """Build a fictional topology matching the persisted migration edge cases."""

    ledger = fresh()
    events_by_number: dict[int, dict[str, Any]] = {}
    for number in range(1, 34):
        if number == 5:
            cycle = "LEGACY-CYCLE-04"
            file_name = "legacy-04.json"
            episode_id = "EP04"
            audit_round = 2
        else:
            cycle = f"LEGACY-CYCLE-{number:02}"
            file_name = f"legacy-{number:02}.json"
            episode_id = f"EP{number:02}"
            audit_round = 1
        issue_count = {4: 3, 10: 8, 12: 7}.get(number, 0)
        if issue_count:
            value = multi_issue_event(
                f"LEGACY-E{number:02}",
                audit_round,
                issue_count,
                cycle=cycle,
                file_name=file_name,
                episode_id=episode_id,
            )
        else:
            value = event(
                f"LEGACY-E{number:02}",
                audit_round,
                result="pass",
                cycle=cycle,
            )
            value.update({"file_name": file_name, "episode_id": episode_id})
        value["timestamp"] = f"2026-07-01T00:{number:02}:00Z"
        value["file_fingerprint"] = f"legacy-file-{number:02}"
        value["source_version"] = f"legacy-source-{number:02}"
        agent.ingest_event(ledger, value)
        events_by_number[number] = ledger["events"][-1]

    # Atomic ingest replaces the candidate tree on every commit, so reacquire
    # persisted object identities only after all 33 appends complete.
    events_by_number = {
        int(row["event_id"].rsplit("E", 1)[-1]): row
        for row in ledger["events"]
    }

    # Four sealed scopes legitimately start above round one.
    for number, first_round in {1: 2, 11: 2, 13: 2, 31: 3}.items():
        events_by_number[number]["audit_round"] = first_round

    # Five persisted legacy scope aliases map bijectively to canonical scopes.
    alias_numbers = {11, 13, 17, 23, 33}
    for number in alias_numbers:
        stored_event = events_by_number[number]
        canonical_scope = stored_event["scope_key"]
        legacy_scope = f"LEGACY-SCOPE-ALIAS-{number:02}"
        stored_event["scope_key"] = legacy_scope
        cached_scope = next(
            row for row in ledger["scope_states"]
            if row["scope_key"] == canonical_scope
        )
        cached_scope["scope_key"] = legacy_scope

    # Persist the three known legacy input aliases without rewriting history.
    for number in (4, 10, 12):
        events_by_number[number]["issues"][0]["category"] = "Seedance独立执行"

    # Seven sealed issue evidence payloads used structured evidence objects.
    for issue_index, issue in enumerate(events_by_number[12]["issues"], 1):
        issue["evidence"] = {"legacy_probe": issue_index, "status": "recorded"}
    for record in ledger["issues"]:
        if any(row.get("event_id") == "LEGACY-E12" for row in record["history"]):
            for row in record["history"]:
                if row.get("event_id") == "LEGACY-E12":
                    row["evidence"] = {
                        "legacy_probe": row.get("field"),
                        "status": "recorded",
                    }

    # Legacy derived counts are sealed but never trusted by modern summaries.
    events_by_number[11]["counts"]["initial_error_count"] = 8
    events_by_number[13]["counts"]["initial_error_count"] = 7

    # Fifteen cross-scope issues were historically marked resolved without
    # independent authority. Migration must reopen them, not rewrite history.
    for failed_number, clean_number in ((10, 11), (12, 13)):
        clean_event = events_by_number[clean_number]
        for record in ledger["issues"]:
            if not any(
                row.get("event_id") == f"LEGACY-E{failed_number:02}"
                for row in record["history"]
            ):
                continue
            record["status"] = "resolved"
            record["open_scope_keys"] = []
            record["history"].append({
                "event_id": clean_event["event_id"],
                "timestamp": clean_event["timestamp"],
                "scope_key": clean_event["scope_key"],
                "audit_round": clean_event["audit_round"],
                "source_version": clean_event["source_version"],
                "resolution": "legacy_cross_scope_implicit_clean",
            })
            record["occurrence_locations"].append({
                "event_id": clean_event["event_id"],
                "scope_key": clean_event["scope_key"],
                "episode_id": clean_event["episode_id"],
                "file_name": clean_event["file_name"],
                "source_version": clean_event["source_version"],
                "shot_ids": [],
                "field": "legacy_resolution_projection",
            })

    # Old cached state omitted two event scopes and trusted two cross-scope
    # last_event links. The checkpoint and replay must derive all 32 scopes
    # from events, never from this cache.
    omitted_keys = {
        events_by_number[17]["scope_key"],
        events_by_number[33]["scope_key"],
    }
    ledger["scope_states"] = [
        row for row in ledger["scope_states"]
        if row.get("scope_key") not in omitted_keys
    ]
    for failed_number, clean_number in ((10, 11), (12, 13)):
        failed_event = events_by_number[failed_number]
        clean_event = events_by_number[clean_number]
        cached_scope = next(
            row for row in ledger["scope_states"]
            if row["scope_key"] == failed_event["scope_key"]
        )
        cached_scope.update({
            "last_event_id": clean_event["event_id"],
            "last_result": clean_event["result"],
            "last_audit_round": clean_event["audit_round"],
            "closure_state": "awaiting_human_signoff",
            "open_issue_ids": [],
        })

    # Nine old signoffs have no exact event binding or authority class. Two
    # share the event second; deterministic timestamps must not grant power.
    for number in (8, 15, 20, 21, 22, 23, 24, 25, 27):
        stored_event = events_by_number[number]
        decision = "reopen" if number == 8 else "approve"
        timestamp = (
            stored_event["timestamp"]
            if number in {15, 27}
            else f"2026-07-01T01:{number:02}:00Z"
        )
        record = {
            "scope_key": stored_event["scope_key"],
            "decision": decision,
            "timestamp": timestamp,
        }
        ledger["human_signoffs"].append(record)
        cached_scope = next(
            row for row in ledger["scope_states"]
            if row["scope_key"] == stored_event["scope_key"]
        )
        cached_scope["human_signoff"] = copy.deepcopy(record)
        cached_scope["closure_state"] = (
            "reopened_by_human" if decision == "reopen" else "human_signed_off"
        )
        cached_scope["can_advance"] = decision == "approve"

    for record in ledger["issues"]:
        record.pop("legacy_resolution_requires_reaudit", None)
    ledger["policy"].pop("ledger_replay_contract_version", None)
    ledger.pop("authority_reopens", None)
    return ledger


def migrate_synthetic_legacy() -> tuple[
    dict[str, Any], dict[str, Any], Callable[[dict[str, Any]], bool]
]:
    original = synthetic_legacy_33_event_ledger()
    migrated, _changed = agent.migrate_ledger_to_current(
        original,
        timestamp="2026-08-31T00:00:00Z",
        migration_id="MIG-SYNTHETIC-33",
    )
    return original, migrated, checkpoint_authorizer(migrated)


def test_init_and_validate() -> None:
    ledger = fresh()
    assert_true(agent.validate_ledger(ledger) == [], "new ledger must validate")
    assert_true(ledger["policy"]["formal_rule_auto_mutation"] is False, "safe mode must forbid rule mutation")
    assert_true(agent.AUDIT_CATEGORIES[5] == "正式母版单镜执行完整性", "canonical audit category must use compact master")


def test_first_occurrence_no_candidate() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    assert_true(ledger["issues"][0]["occurrence_count"] == 1, "first occurrence count")
    assert_true(not ledger["rule_candidates"], "first occurrence must not create candidate")


def test_second_round_root_cause_and_candidate() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    result = agent.ingest_event(ledger, event("EV-2", 2, "residual"))
    issue = ledger["issues"][0]
    assert_true(issue["repair_strategy"] == "root_cause_repair_required", "second round requires root cause")
    assert_true(len(result["candidate_ids_created"]) == 1, "second round creates one candidate")
    assert_true(ledger["rule_candidates"][0]["status"] == "proposed", "candidate starts proposed")


def test_third_round_upstream_rebuild() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    agent.ingest_event(ledger, event("EV-2", 2, "residual"))
    agent.ingest_event(ledger, event("EV-3", 3, "residual"))
    issue = ledger["issues"][0]
    assert_true(issue["repair_strategy"] == "upstream_rebuild_required", "third round requires upstream rebuild")
    assert_true(issue["max_consecutive_rounds"] == 3, "third round count")


def test_duplicate_event_is_idempotent() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    before = copy.deepcopy(ledger)
    result = agent.ingest_event(ledger, event("EV-1", 1))
    assert_true(result["status"] == "duplicate", "duplicate must be reported")
    assert_true(ledger == before, "duplicate must not mutate ledger")


def test_hard_round_limit() -> None:
    invalid = event("EV-4", 4, "residual")
    errors = agent.validate_event(invalid, 3)
    assert_true(any("audit_round" in error for error in errors), "round four must fail")


def test_fail_current_blocks_advance() -> None:
    ledger = fresh()
    result = agent.ingest_event(ledger, event("EV-1", 1))
    assert_true(result["can_advance"] is False, "fail_current must block next episode")


def test_clean_pass_requires_human_signoff() -> None:
    ledger = fresh()
    result = agent.ingest_event(
        ledger,
        independent_event("EV-PASS", 1, result="pass", cycle="CYCLE-PASS"),
    )
    assert_true(result["closure_state"] == "awaiting_human_signoff", "pass must await human")
    assert_true(result["can_advance"] is False, "pass alone cannot advance")
    signed = agent.signoff_scope(ledger, result["scope_key"], "approve", "2026-08-14T00:10:00Z")
    assert_true(signed["can_advance"] is True, "human sign-off enables advance")


def test_clean_pass_blocks_cross_window_continuation() -> None:
    ledger = fresh()
    result = agent.ingest_event(ledger, event("EV-PASS", 1, result="pass", cycle="CYCLE-PASS"))
    try:
        agent.ingest_event(ledger, event("EV-AFTER-PASS", 2, status="introduced", cycle="CYCLE-PASS"))
    except ValueError as exc:
        assert_true("independent audit" in str(exc), "legacy pass must require independent re-audit")
    else:
        raise AssertionError("another window must not continue an unsigned clean scope")
    assert_true(result["can_advance"] is False, "unsigned scope remains blocked")


def test_issue_remains_open_in_another_cycle() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-A1", 1, cycle="CYCLE-A"))
    agent.ingest_event(ledger, event("EV-B1", 1, cycle="CYCLE-B"))
    agent.ingest_event(ledger, event("EV-A2-PASS", 2, result="pass", cycle="CYCLE-A"))
    issue = ledger["issues"][0]
    assert_true(issue["status"] == "open", "resolving one cycle must not close another cycle")
    assert_true(len(issue["open_scope_keys"]) == 1, "one open cycle must remain")


def test_candidate_checks_and_approval_do_not_mutate_runtime() -> None:
    ledger = fresh()
    before_hash = sha256(RUNTIME_PATH)
    agent.ingest_event(ledger, event("EV-1", 1))
    agent.ingest_event(ledger, event("EV-2", 2, "residual"))
    candidate_id = ledger["rule_candidates"][0]["candidate_id"]
    checked = agent.check_candidate(
        ledger,
        candidate_id,
        ["VAL-E-PERFORM-TEST"],
        ["GT-NEG", "GT-POS", "GT-REG"],
        "pass",
        True,
        "2026-08-14T00:20:00Z",
    )
    assert_true(checked["status"] == "awaiting_human", "validated candidate waits for human")
    approved = agent.decide_candidate(ledger, candidate_id, "approve", "2026-08-14T00:21:00Z")
    assert_true(approved["status"] == "ready_for_manual_promotion", "approved candidate is only ready for manual promotion")
    assert_true(approved["formal_rule_mutated"] is False, "decision must not mutate formal rule")
    assert_true(sha256(RUNTIME_PATH) == before_hash, "runtime file must remain unchanged")


def test_approval_without_validation_stays_pending() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    agent.ingest_event(ledger, event("EV-2", 2, "residual"))
    candidate_id = ledger["rule_candidates"][0]["candidate_id"]
    result = agent.decide_candidate(ledger, candidate_id, "approve", "2026-08-14T00:22:00Z")
    assert_true(result["status"] == "approved_pending_validation", "approval cannot bypass tests")


def test_resolution_and_metrics() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-1", 1))
    result = agent.ingest_event(ledger, event("EV-PASS", 2, result="pass"))
    summary = result["summary"]
    assert_true(ledger["issues"][0]["status"] == "resolved", "absent follow-up issue is resolved")
    assert_true(summary["metrics"]["initial_error_count"] == 1, "initial metric")
    assert_true(summary["metrics"]["residual_error_count"] == 0, "residual metric")


def test_malformed_event_rejected() -> None:
    ledger = fresh()
    invalid = event("EV-X", 1)
    invalid["issues"][0].pop("evidence")
    try:
        agent.ingest_event(ledger, invalid)
    except ValueError as exc:
        assert_true("evidence" in str(exc), "missing evidence must be named")
    else:
        raise AssertionError("malformed event must be rejected")


def test_compact_master_audit_category_is_canonical() -> None:
    ledger = fresh()
    agent.ingest_event(
        ledger,
        event("EV-COMPACT", 1, category="正式母版单镜执行完整性"),
    )
    assert_true(
        ledger["events"][0]["issues"][0]["category"] == "正式母版单镜执行完整性",
        "compact category must persist unchanged",
    )


def test_legacy_seedance_category_is_input_alias_only() -> None:
    ledger = fresh()
    agent.ingest_event(
        ledger,
        event("EV-LEGACY", 1, category="Seedance独立执行"),
    )
    stored = ledger["events"][0]
    assert_true(
        stored["issues"][0]["category"] == "正式母版单镜执行完整性",
        "legacy input must normalize to compact category",
    )
    assert_true(
        stored["legacy_aliases_applied"] == {"Seedance独立执行": "正式母版单镜执行完整性"},
        "legacy mapping must be explicit and traceable",
    )


def test_runtime_and_schema_pass_negative_matrix() -> None:
    pass_with_issue = event("EV-PASS-ISSUE", 1, result="pass")
    pass_with_issue["issues"] = copy.deepcopy(event("EV-FAIL", 1)["issues"])
    pass_with_blocker = event("EV-PASS-BLOCKER", 1, result="pass")
    pass_with_blocker["blockers"] = ["BLOCKER-1"]
    blocker_not_array = event("EV-BLOCKER-TYPE", 1, result="pass")
    blocker_not_array["blockers"] = "BLOCKER-1"
    independent_zero = independent_event("EV-INDEPENDENT-ZERO", 1)
    independent_zero["checked_shot_count"] = 0
    independent_role_without_class = independent_event("EV-NO-CLASS", 1)
    independent_role_without_class.pop("audit_class")
    independent_whitespace_id = independent_event("EV-ID-WHITESPACE", 1)
    independent_whitespace_id["handoff_id"] = " HND-ID-WHITESPACE "
    cases = [
        pass_with_issue,
        pass_with_blocker,
        blocker_not_array,
        independent_zero,
        independent_role_without_class,
        independent_whitespace_id,
    ]
    for field in agent.INDEPENDENT_AUDIT_REQUIRED_FIELDS:
        invalid = independent_event(f"EV-MISSING-{field.upper()}", 1)
        invalid.pop(field)
        cases.append(invalid)
    for assertion in agent.INDEPENDENT_ASSERTIONS:
        invalid = independent_event(f"EV-FALSE-{assertion.upper()}", 1)
        invalid["independence_assertions"][assertion] = False
        cases.append(invalid)
    for field in agent.SHA256_FIELDS:
        invalid = independent_event(f"EV-BAD-SHA-{field.upper()}", 1)
        invalid[field] = "g" * 64
        cases.append(invalid)
    for invalid in cases:
        assert_true(agent.validate_event(invalid), f"runtime must reject {invalid['event_id']}")
        assert_true(schema_event_errors(invalid), f"schema must reject {invalid['event_id']}")
        ledger = fresh()
        assert_rejected_without_mutation(
            ledger,
            lambda invalid=invalid, ledger=ledger: agent.ingest_event(ledger, invalid),
            f"ingest must reject {invalid['event_id']}",
        )


def test_valid_independent_pass_matches_schema_and_signs_off() -> None:
    ledger = fresh()
    clean = independent_event("EV-VALID-INDEPENDENT", 1)
    assert_true(agent.validate_event(clean) == [], "valid independent event must pass runtime")
    assert_true(schema_event_errors(clean) == [], "valid independent event must pass schema")
    result = agent.ingest_event(ledger, clean)
    assert_true(list(LEDGER_SCHEMA_VALIDATOR.iter_errors(ledger)) == [], "persisted ledger must pass schema")
    signed = agent.signoff_scope(
        ledger, result["scope_key"], "approve", "2026-08-14T00:10:00Z"
    )
    assert_true(signed["can_advance"] is True, "fresh independent pass may be signed")


def test_current_independent_event_requires_semantic_recovery_bindings() -> None:
    current = independent_event("EV-SEMANTIC-RECOVERY-BINDINGS", 1)
    assert_true(
        schema_event_errors(current) == [],
        "current independent event with both semantic hashes must pass schema",
    )
    for field in ("semantic_audit_sha256", "semantic_report_input_sha256"):
        missing = copy.deepcopy(current)
        missing.pop(field)
        assert_true(
            schema_event_errors(missing),
            f"current independent event missing {field} must fail event-only schema",
        )
        assert_true(
            agent.validate_event(missing),
            f"current independent event missing {field} must fail runtime",
        )
        assert_true(
            list(SEALED_LEGACY_EVENT_SCHEMA_VALIDATOR.iter_errors(missing)) == [],
            f"sealed legacy independent event may omit {field}",
        )

        persisted = fresh()
        agent.ingest_event(persisted, current)
        persisted["events"][0].pop(field)
        assert_true(
            list(LEDGER_SCHEMA_VALIDATOR.iter_errors(persisted)),
            f"current ledger missing {field} must fail Whole Schema",
        )

        malformed = copy.deepcopy(current)
        malformed[field] = "g" * 64
        assert_true(
            schema_event_errors(malformed),
            f"current independent event with malformed {field} must fail",
        )
        assert_true(
            agent.validate_event(malformed),
            f"runtime must reject malformed {field}",
        )
        assert_true(
            list(SEALED_LEGACY_EVENT_SCHEMA_VALIDATOR.iter_errors(malformed)),
            f"sealed legacy event cannot claim malformed {field}",
        )

    sealed_legacy = copy.deepcopy(current)
    sealed_legacy.pop("semantic_audit_sha256")
    sealed_legacy.pop("semantic_report_input_sha256")
    assert_true(
        list(SEALED_LEGACY_EVENT_SCHEMA_VALIDATOR.iter_errors(sealed_legacy)) == [],
        "generic audit_event keeps an externally sealed legacy prefix readable",
    )

    legacy_ledger = fresh()
    agent.ingest_event(legacy_ledger, current)
    for field in ("semantic_audit_sha256", "semantic_report_input_sha256"):
        legacy_ledger["events"][0].pop(field)
    migrated, _changes = agent.migrate_ledger_to_current(
        legacy_ledger,
        timestamp="2026-08-31T00:00:00Z",
        migration_id="MIG-SEMANTIC-BINDING-LEGACY",
    )
    assert_true(
        all(
            field not in migrated["events"][0]
            for field in (
                "semantic_audit_sha256",
                "semantic_report_input_sha256",
            )
        ),
        "migration must not fabricate semantic hashes in a sealed legacy event",
    )
    assert_true(
        list(LEDGER_SCHEMA_VALIDATOR.iter_errors(migrated)) == [],
        "Whole Schema must keep an explicitly checkpointed legacy event readable",
    )
    assert_true(
        agent.validate_ledger(
            migrated,
            checkpoint_authorizer=checkpoint_authorizer(migrated),
        ) == [],
        "runtime must keep the explicitly checkpointed legacy event readable",
    )


def test_checkpoint_post_prefix_semantic_bindings_are_runtime_current() -> None:
    _original, migrated, authorizer = migrate_synthetic_legacy()
    for index, field in enumerate(
        ("semantic_audit_sha256", "semantic_report_input_sha256"), 1
    ):
        missing = independent_event(
            f"POST-CHECKPOINT-MISSING-{index}",
            1,
            cycle=f"POST-CHECKPOINT-MISSING-CYCLE-{index}",
            file_name=f"post-checkpoint-missing-{index}.json",
        )
        missing.pop(field)
        assert_rejected_without_mutation(
            migrated,
            lambda missing=missing: agent.ingest_event(
                migrated,
                missing,
                checkpoint_authorizer=authorizer,
            ),
            f"post-checkpoint independent event missing {field} must fail",
        )

        malformed = independent_event(
            f"POST-CHECKPOINT-BAD-{index}",
            1,
            cycle=f"POST-CHECKPOINT-BAD-CYCLE-{index}",
            file_name=f"post-checkpoint-bad-{index}.json",
        )
        malformed[field] = "g" * 64
        assert_rejected_without_mutation(
            migrated,
            lambda malformed=malformed: agent.ingest_event(
                migrated,
                malformed,
                checkpoint_authorizer=authorizer,
            ),
            f"post-checkpoint independent event with malformed {field} must fail",
        )

    persisted = copy.deepcopy(migrated)
    persisted_authorizer = checkpoint_authorizer(persisted)
    valid = independent_event(
        "POST-CHECKPOINT-VALID",
        1,
        cycle="POST-CHECKPOINT-VALID-CYCLE",
        file_name="post-checkpoint-valid.json",
    )
    agent.ingest_event(
        persisted,
        valid,
        checkpoint_authorizer=persisted_authorizer,
    )
    for field in ("semantic_audit_sha256", "semantic_report_input_sha256"):
        tampered = copy.deepcopy(persisted)
        tampered["events"][-1].pop(field)
        assert_true(
            any(
                field in error
                for error in agent.validate_ledger(
                    tampered,
                    checkpoint_authorizer=persisted_authorizer,
                )
            ),
            f"replay must reject persisted post-checkpoint event missing {field}",
        )


def test_independent_reopen_requires_new_audit_before_signoff() -> None:
    ledger = fresh()
    first = independent_event("EV-REAUDIT-1", 1, cycle="CYCLE-REAUDIT")
    result = agent.ingest_event(ledger, first)
    agent.reopen_scope_for_independent_reaudit(
        ledger,
        result["scope_key"],
        "EV-REAUDIT-2",
        "2026-08-14T00:01:30Z",
    )
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.signoff_scope(
            ledger, result["scope_key"], "approve", "2026-08-14T00:01:40Z"
        ),
        "reopened scope must not sign off the stale pass",
    )
    second = independent_event(
        "EV-REAUDIT-2", 2, cycle="CYCLE-REAUDIT", binding_label="reaudit-2"
    )
    second_result = agent.ingest_event(ledger, second)
    signed = agent.signoff_scope(
        ledger,
        second_result["scope_key"],
        "approve",
        "2026-08-14T00:10:00Z",
    )
    assert_true(signed["can_advance"] is True, "new independent pass restores sign-off eligibility")


def test_handoff_and_trace_are_globally_unique_across_scopes() -> None:
    for field in ("handoff_id", "stage_trace_id"):
        ledger = fresh()
        first = independent_event(
            "EV-GLOBAL-1", 1, result="fail_current", cycle="CYCLE-GLOBAL-1"
        )
        agent.ingest_event(ledger, first)
        second = independent_event(
            "EV-GLOBAL-2",
            1,
            result="fail_current",
            cycle="CYCLE-GLOBAL-2",
            file_name="renamed-independent.json",
            binding_label="global-2",
        )
        second[field] = first[field].swapcase()
        assert_rejected_without_mutation(
            ledger,
            lambda second=second, ledger=ledger: agent.ingest_event(ledger, second),
            f"case variant of {field} must not create a fresh identity",
        )


def test_failed_bindings_cannot_be_relabelled_pass() -> None:
    for field in agent.FAILED_BINDING_FIELDS:
        ledger = fresh()
        failed = independent_event(
            f"EV-FAILED-{field}",
            1,
            result="fail_current",
            cycle=f"CYCLE-FAILED-{field}",
            binding_label=f"failed-{field}",
        )
        agent.ingest_event(ledger, failed)
        clean = independent_event(
            f"EV-CLEAN-{field}",
            2,
            cycle=f"CYCLE-FAILED-{field}",
            binding_label=f"clean-{field}",
        )
        clean[field] = str(failed[field]).upper()
        assert_rejected_without_mutation(
            ledger,
            lambda clean=clean, ledger=ledger: agent.ingest_event(ledger, clean),
            f"failed {field} must not be reused with case relabeling",
        )


def test_failed_binding_cannot_escape_by_cycle_or_filename() -> None:
    ledger = fresh()
    failed = independent_event(
        "EV-FAILED-SCOPE-1",
        1,
        result="blocked",
        cycle="CYCLE-FAILED-SCOPE-1",
        file_name="failed-name.json",
    )
    failed["issues"] = []
    failed["blockers"] = ["SOURCE-BLOCKED"]
    agent.ingest_event(ledger, failed)
    clean = independent_event(
        "EV-CLEAN-SCOPE-2",
        1,
        cycle="CYCLE-CLEAN-SCOPE-2",
        file_name="renamed-clean.json",
        binding_label="clean-scope-2",
    )
    for field in agent.FAILED_BINDING_FIELDS:
        clean[field] = failed[field]
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.ingest_event(ledger, clean),
        "new cycle and file_name must not wash failed bindings",
    )


def test_round_and_scope_rejections_are_atomic() -> None:
    ledger = fresh()
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.ingest_event(ledger, event("EV-NEW-R2", 2)),
        "new scope round two must fail",
    )
    agent.ingest_event(ledger, event("EV-R1", 1))
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.ingest_event(ledger, event("EV-R3", 3, status="residual")),
        "round jump must fail",
    )

    signed_ledger = fresh()
    passed = agent.ingest_event(
        signed_ledger,
        independent_event("EV-SIGNED-R1", 1, cycle="CYCLE-SIGNED"),
    )
    agent.signoff_scope(
        signed_ledger, passed["scope_key"], "approve", "2026-08-14T00:10:00Z"
    )
    follow_up = independent_event(
        "EV-SIGNED-R2", 2, cycle="CYCLE-SIGNED", binding_label="signed-r2"
    )
    assert_rejected_without_mutation(
        signed_ledger,
        lambda: agent.ingest_event(signed_ledger, follow_up),
        "signed scope must reject an in-place continuation",
    )


def test_normalized_event_id_is_idempotent_only_for_same_payload() -> None:
    ledger = fresh()
    original = event("EV-NORMALIZED", 1)
    agent.ingest_event(ledger, original)
    before = ledger_bytes(ledger)
    case_variant = copy.deepcopy(original)
    case_variant["event_id"] = "ev-normalized"
    duplicate = agent.ingest_event(ledger, case_variant)
    assert_true(duplicate["status"] == "duplicate", "case-only event ID variant is idempotent")
    assert_true(ledger_bytes(ledger) == before, "idempotent duplicate must not rewrite ledger")
    conflict = copy.deepcopy(case_variant)
    conflict["issues"][0]["evidence"] = "different payload under the same normalized ID"
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.ingest_event(ledger, conflict),
        "same normalized event ID with different payload must conflict",
    )


def test_validate_ledger_replays_scope_round_freshness_and_counters() -> None:
    ledger = fresh()
    agent.ingest_event(ledger, event("EV-SCOPE-A", 1, cycle="CYCLE-A"))
    second = event("EV-SCOPE-B", 1, cycle="CYCLE-B")
    second["file_name"] = "episode-02.json"
    second["episode_id"] = "EP02"
    agent.ingest_event(ledger, second)
    assert_true(agent.validate_ledger(ledger) == [], "untampered ledger must replay cleanly")

    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda value: value["events"][1].__setitem__("audit_round", 2),
        lambda value: value["events"][0].__setitem__("scope_key", value["events"][1]["scope_key"]),
        lambda value: value["scope_states"][0].__setitem__("last_event_id", value["events"][1]["event_id"]),
        lambda value: value["counters"].__setitem__("event_sequence", 99),
        lambda value: value["events"][0].__setitem__("sequence", 2),
        lambda value: value["issues"][0]["occurrence_locations"][0].__setitem__(
            "scope_key", value["events"][1]["scope_key"]
        ),
        lambda value: value["issues"][0].__setitem__("open_scope_keys", []),
        lambda value: value["issues"][0].__setitem__("status", "resolved"),
    ]
    for mutate in mutations:
        tampered = copy.deepcopy(ledger)
        mutate(tampered)
        assert_true(agent.validate_ledger(tampered), "tampered replay state must fail validation")


def test_schema_scope_state_matrix_matches_runtime() -> None:
    invalid = fresh()
    invalid["scope_states"] = [42]
    assert_true(list(LEDGER_SCHEMA_VALIDATOR.iter_errors(invalid)), "schema rejects scalar scope state")
    assert_true(agent.validate_ledger(invalid), "runtime rejects scalar scope state")


def test_legacy_checkpoint_migration_preserves_history_and_downgrades_authority() -> None:
    original, migrated, authorizer = migrate_synthetic_legacy()
    original_events = ledger_bytes({"events": original["events"]})
    migrated_events = ledger_bytes({"events": migrated["events"]})
    original_histories = [record["history"] for record in original["issues"]]
    migrated_histories = [record["history"] for record in migrated["issues"]]
    original_locations = [
        record["occurrence_locations"] for record in original["issues"]
    ]
    migrated_locations = [
        record["occurrence_locations"] for record in migrated["issues"]
    ]
    assert_true(original_events == migrated_events, "migration must not rewrite event bytes")
    assert_true(original_histories == migrated_histories, "migration must not rewrite history")
    assert_true(original_locations == migrated_locations, "migration must not rewrite locations")

    checkpoint = migrated["replay_checkpoint"]
    boundaries = checkpoint["scope_boundaries"]
    assert_true(checkpoint["event_count"] == 33, "checkpoint seals all 33 events")
    assert_true(len(boundaries) == 32, "checkpoint derives 32 canonical scopes")
    assert_true(sum(row["event_count"] for row in boundaries) == 33, "scope counts close")
    assert_true(
        sum(row["legacy_scope_key"] != row["scope_key"] for row in boundaries) == 5,
        "checkpoint binds five legacy scope aliases",
    )
    assert_true(
        sum(row["first_round"] > 1 for row in boundaries) == 4,
        "checkpoint explicitly seals four non-one starting rounds",
    )
    assert_true(len(migrated["scope_states"]) == 32, "cached scopes rebuild from events")
    assert_true(
        all(scope["can_advance"] is False for scope in migrated["scope_states"]),
        "no legacy conclusion may authorize release",
    )
    closure_counts = {
        state: sum(
            scope["closure_state"] == state for scope in migrated["scope_states"]
        )
        for state in {
            "legacy_resolution_requires_reaudit",
            "legacy_signoff_requires_reaudit",
            "reopened_by_human",
            "open",
        }
    }
    assert_true(
        closure_counts == {
            "legacy_resolution_requires_reaudit": 21,
            "legacy_signoff_requires_reaudit": 8,
            "reopened_by_human": 1,
            "open": 2,
        },
        "legacy closures must be downgraded deterministically",
    )
    assert_true(
        sum(issue["status"] == "open" for issue in migrated["issues"]) == 15,
        "cross-scope implicit resolutions must reopen",
    )
    assert_true(
        sum(issue["status"] == "resolved" for issue in migrated["issues"]) == 3,
        "same-scope semantic resolutions remain historical facts",
    )
    assert_true(
        all(issue["legacy_resolution_requires_reaudit"] for issue in migrated["issues"]),
        "all legacy issue conclusions require a fresh independent audit",
    )
    assert_true(
        agent.validate_ledger(migrated, checkpoint_authorizer=authorizer) == [],
        "authorized migrated ledger must replay cleanly",
    )
    assert_true(
        list(LEDGER_SCHEMA_VALIDATOR.iter_errors(migrated)) == [],
        "authorized migration shape must pass Whole Schema",
    )
    assert_true(
        any("externally verified" in error for error in agent.validate_ledger(migrated)),
        "checkpoint without external signed-CAS authority must fail closed",
    )
    assert_true(
        agent.validate_ledger(migrated, checkpoint_authorizer=lambda _value: False),
        "rejected external authority must fail closed",
    )


def test_checkpoint_tamper_and_prefix_mutation_fail_closed() -> None:
    original, migrated, authorizer = migrate_synthetic_legacy()
    original_before = ledger_bytes(original)
    try:
        agent.migrate_ledger_to_current(
            original, timestamp="2026-08-31T00:00:00Z"
        )
    except ValueError as exc:
        assert_true("migration_id" in str(exc), "checkpoint requires signed journal ID")
    else:
        raise AssertionError("historical migration without migration_id must fail")
    assert_true(
        ledger_bytes(original) == original_before,
        "missing migration authority must not mutate input",
    )
    assert_true(
        migrated["replay_checkpoint"]["migration_id"] == "MIG-SYNTHETIC-33",
        "checkpoint stores the externally supplied signed-journal ID",
    )
    wrong_migration_id = copy.deepcopy(migrated)
    wrong_migration_id["replay_checkpoint"]["migration_id"] = "MIG-WRONG-RECORD"
    assert_true(
        agent.validate_ledger(
            wrong_migration_id, checkpoint_authorizer=authorizer
        ),
        "external journal authorizer rejects a mismatched migration_id",
    )
    mutations: list[Callable[[dict[str, Any]], None]] = [
        lambda value: value["events"][0].__setitem__("source_version", "tampered"),
        lambda value: value["issues"][0]["history"][0].__setitem__(
            "source_version", "tampered"
        ),
        lambda value: value["issues"][0]["occurrence_locations"][0].__setitem__(
            "field", "tampered"
        ),
        lambda value: value["replay_checkpoint"]["scope_boundaries"][0].__setitem__(
            "legacy_scope_key", "FORGED-ALIAS"
        ),
        lambda value: value["replay_checkpoint"].__setitem__(
            "events_prefix_canonical_sha256", "f" * 64
        ),
    ]
    for mutate in mutations:
        tampered = copy.deepcopy(migrated)
        mutate(tampered)
        assert_true(
            agent.validate_ledger(
                tampered, checkpoint_authorizer=lambda _value: True
            ),
            "sealed checkpoint/event/history/location tamper must fail",
        )

    forged = fresh()
    agent.ingest_event(forged, event("FORGED-R1", 1))
    forged["events"][0]["audit_round"] = 2
    forged["scope_states"][0]["last_audit_round"] = 2
    forged["replay_checkpoint"] = agent._build_replay_checkpoint(
        forged,
        migration_id="MIG-SELF-REPORTED-FORGED",
        old_ledger_canonical_sha256=digest("self-reported-old-ledger"),
        timestamp="2026-08-31T00:00:00Z",
    )
    assert_true(
        any("externally verified" in error for error in agent.validate_ledger(forged)),
        "ordinary callers cannot self-authorize a checkpoint",
    )


def test_checkpoint_issue_suffix_forgery_matrix_fails_closed() -> None:
    """Schema-valid issue projections after a checkpoint must be event-derived."""

    _original, migrated, authorizer = migrate_synthetic_legacy()

    def append_fake_history(value: dict[str, Any]) -> None:
        row = copy.deepcopy(value["issues"][0]["history"][0])
        row["event_id"] = "legacy-forged-history"
        value["issues"][0]["history"].append(row)

    def append_fake_location(value: dict[str, Any]) -> None:
        row = copy.deepcopy(value["issues"][0]["occurrence_locations"][0])
        row["event_id"] = "legacy-forged-location"
        value["issues"][0]["occurrence_locations"].append(row)
        value["issues"][0]["distinct_event_count"] += 1

    def inflate_counters(value: dict[str, Any]) -> None:
        issue = value["issues"][0]
        pattern_key = issue["pattern_key"]
        issue["occurrence_count"] += 7
        issue["observation_count"] += 7
        for record in value["issues"]:
            if record["pattern_key"] == pattern_key:
                record["pattern_observation_count"] += 7

    def append_whole_fake_issue(value: dict[str, Any]) -> None:
        record = copy.deepcopy(value["issues"][0])
        record.update({
            "issue_id": "ISS-FORGED-CHECKPOINT",
            "dedup_key": "DEDUP-FORGED-CHECKPOINT",
            "error_code": "E-FORGED-CHECKPOINT",
            "issue_type": "forged_checkpoint_issue",
            "normalized_root_cause": "forged_checkpoint_issue",
            "status": "resolved",
            "open_scope_keys": [],
            "legacy_resolution_requires_reaudit": True,
            "pattern_occurrence_count": 1,
            "pattern_observation_count": record["observation_count"],
        })
        record["pattern_key"] = agent.issue_pattern_key(
            value["project_id"], record
        )
        value["issues"].append(record)
        value["counters"]["issue_record_count"] += 1

    def mutate_immutable_identity(value: dict[str, Any]) -> None:
        value["issues"][0]["category"] = "镜头节奏"

    attacks: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("post-checkpoint legacy history", append_fake_history),
        ("post-checkpoint legacy location", append_fake_location),
        ("post-checkpoint counters", inflate_counters),
        ("whole forged issue", append_whole_fake_issue),
        ("sealed immutable issue identity", mutate_immutable_identity),
    ]
    for label, mutate in attacks:
        tampered = copy.deepcopy(migrated)
        mutate(tampered)
        assert_true(
            list(LEDGER_SCHEMA_VALIDATOR.iter_errors(tampered)) == [],
            f"{label} fixture must remain structurally Schema-valid",
        )
        assert_true(
            agent.validate_ledger(
                tampered, checkpoint_authorizer=authorizer
            ),
            f"runtime replay must reject {label}",
        )
        probe = event(
            f"ATTACK-GUARD-{label}",
            1,
            result="pass",
            cycle=f"ATTACK-GUARD-{label}",
        )
        assert_rejected_without_mutation(
            tampered,
            lambda value=tampered, event_value=probe: agent.ingest_event(
                value,
                event_value,
                checkpoint_authorizer=authorizer,
            ),
            f"{label} must block further mutation atomically",
        )


def test_checkpoint_post_prefix_rounds_modern_shape_and_idempotency() -> None:
    _original, migrated, authorizer = migrate_synthetic_legacy()
    checkpoint_before = copy.deepcopy(migrated["replay_checkpoint"])

    new_scope_round_two = independent_event(
        "POST-NEW-SCOPE-R2", 2, cycle="POST-NEW-CYCLE", binding_label="post-new-r2"
    )
    assert_rejected_without_mutation(
        migrated,
        lambda: agent.ingest_event(
            migrated,
            new_scope_round_two,
            checkpoint_authorizer=authorizer,
        ),
        "post-checkpoint new scope must start at round one",
    )

    sealed = migrated["events"][1]
    skipped = independent_event(
        "POST-EXISTING-R3",
        3,
        cycle=sealed["audit_cycle_id"],
        file_name=sealed["file_name"],
        episode_id=sealed["episode_id"],
        binding_label="post-existing-r3",
    )
    assert_rejected_without_mutation(
        migrated,
        lambda: agent.ingest_event(
            migrated, skipped, checkpoint_authorizer=authorizer
        ),
        "post-checkpoint existing scope cannot skip a round",
    )

    legal = independent_event(
        "POST-EXISTING-R2",
        2,
        cycle=sealed["audit_cycle_id"],
        file_name=sealed["file_name"],
        episode_id=sealed["episode_id"],
        binding_label="post-existing-r2",
    )
    legal["timestamp"] = "2026-09-01T00:00:00Z"
    agent.ingest_event(migrated, legal, checkpoint_authorizer=authorizer)
    assert_true(
        migrated["replay_checkpoint"] == checkpoint_before,
        "ordinary ingest must not modify checkpoint",
    )
    assert_true(
        agent.validate_ledger(migrated, checkpoint_authorizer=authorizer) == [],
        "legal post-checkpoint continuation must replay",
    )

    legacy_shaped = independent_event(
        "POST-LEGACY-SHAPE",
        2,
        result="fail_current",
        cycle=migrated["events"][2]["audit_cycle_id"],
        file_name=migrated["events"][2]["file_name"],
        episode_id=migrated["events"][2]["episode_id"],
        binding_label="post-legacy-shape",
    )
    legacy_shaped["issues"][0]["evidence"] = {"legacy": True}
    assert_rejected_without_mutation(
        migrated,
        lambda: agent.ingest_event(
            migrated, legacy_shaped, checkpoint_authorizer=authorizer
        ),
        "legacy evidence shape is allowed only in the sealed prefix",
    )

    tampered_counts = copy.deepcopy(migrated)
    tampered_counts["events"][-1]["counts"]["total_error_points"] = 99
    assert_true(
        agent.validate_ledger(
            tampered_counts, checkpoint_authorizer=authorizer
        ),
        "post-checkpoint derived counts tamper must fail",
    )

    migrated_again, changes = agent.migrate_ledger_to_current(
        migrated, timestamp="2026-09-01T00:10:00Z"
    )
    assert_true(changes == [], "repeat migration must be idempotent")
    assert_true(migrated_again == migrated, "idempotent migration must not rewrite state")


def test_same_second_actions_bind_exact_basis_event() -> None:
    shared_timestamp = "2026-08-14T00:01:00Z"
    for second_result in ("pass", "fail_current"):
        ledger = fresh()
        first = independent_event("SAME-SECOND-R1", 1, cycle=f"SAME-{second_result}")
        first["timestamp"] = shared_timestamp
        accepted = agent.ingest_event(ledger, first)
        agent.signoff_scope(ledger, accepted["scope_key"], "approve", shared_timestamp)
        agent.signoff_scope(ledger, accepted["scope_key"], "reopen", shared_timestamp)
        second = independent_event(
            "SAME-SECOND-R2",
            2,
            result=second_result,
            status="residual",
            cycle=f"SAME-{second_result}",
            binding_label=f"same-second-{second_result}",
        )
        second["timestamp"] = shared_timestamp
        second_result_row = agent.ingest_event(ledger, second)
        if second_result == "pass":
            agent.signoff_scope(
                ledger, second_result_row["scope_key"], "approve", shared_timestamp
            )
            assert_true(
                ledger["scope_states"][0]["closure_state"] == "human_signed_off",
                "R2 pass requires its own exact-basis signoff",
            )
        else:
            assert_true(
                ledger["scope_states"][0]["closure_state"] == "open",
                "R1 same-second approval must not apply to R2 failure",
            )
        assert_true(agent.validate_ledger(ledger) == [], "same-second replay must be exact")


def test_legacy_signoff_ambiguity_and_invalid_history_fail_without_mutation() -> None:
    ambiguous = fresh()
    first = event("AMBIGUOUS-R1", 1, cycle="AMBIGUOUS")
    second = event("AMBIGUOUS-R2", 2, status="residual", cycle="AMBIGUOUS")
    first["timestamp"] = second["timestamp"] = "2026-07-01T00:00:00Z"
    agent.ingest_event(ambiguous, first)
    agent.ingest_event(ambiguous, second)
    ambiguous["human_signoffs"] = [{
        "scope_key": ambiguous["events"][0]["scope_key"],
        "decision": "approve",
        "timestamp": "2026-07-01T00:00:00Z",
    }]
    ambiguous["scope_states"] = []
    ambiguous["policy"].pop("ledger_replay_contract_version", None)
    before = ledger_bytes(ambiguous)
    try:
        agent.migrate_ledger_to_current(
            ambiguous,
            timestamp="2026-08-31T00:00:00Z",
            migration_id="MIG-AMBIGUOUS",
        )
    except ValueError as exc:
        assert_true("multiple historical event candidates" in str(exc), "must name ambiguity")
    else:
        raise AssertionError("same-second ambiguous legacy signoff must fail closed")
    assert_true(ledger_bytes(ambiguous) == before, "failed migration must not mutate input")

    invalid_round = synthetic_legacy_33_event_ledger()
    invalid_round["events"][4]["audit_round"] = 3
    before = ledger_bytes(invalid_round)
    try:
        agent.migrate_ledger_to_current(
            invalid_round,
            timestamp="2026-08-31T00:00:00Z",
            migration_id="MIG-INVALID-ROUND",
        )
    except ValueError as exc:
        assert_true("non-contiguous" in str(exc), "must reject rewritten round history")
    else:
        raise AssertionError("non-contiguous sealed history must fail closed")
    assert_true(ledger_bytes(invalid_round) == before, "invalid history input must stay unchanged")


def test_persisted_shape_timestamp_and_reopen_authority_matrix() -> None:
    for bad_timestamp in ([], {}, "not-a-time"):
        invalid = event(f"BAD-TIME-{type(bad_timestamp).__name__}", 1)
        invalid["timestamp"] = bad_timestamp
        assert_true(agent.validate_event(invalid), "runtime rejects invalid timestamp")
        assert_true(schema_event_errors(invalid), "schema rejects invalid timestamp")
        ledger = fresh()
        assert_rejected_without_mutation(
            ledger,
            lambda ledger=ledger, invalid=invalid: agent.ingest_event(ledger, invalid),
            "invalid timestamp ingest must be atomic",
        )

    missing_reopens = fresh()
    missing_reopens.pop("authority_reopens")
    assert_true(agent.validate_ledger(missing_reopens), "runtime requires authority_reopens")
    assert_true(
        list(LEDGER_SCHEMA_VALIDATOR.iter_errors(missing_reopens)),
        "schema requires authority_reopens",
    )

    malformed = fresh()
    agent.ingest_event(malformed, event("MALFORMED-PERSISTED", 1))
    malformed["events"][0]["issues"] = [1]
    assert_true(agent.validate_ledger(malformed), "malformed persisted issues return errors")

    ledger = fresh()
    first = independent_event("AUTH-R1", 1, cycle="AUTH-CYCLE")
    accepted = agent.ingest_event(ledger, first)
    agent.reopen_scope_for_independent_reaudit(
        ledger, accepted["scope_key"], "AUTH-R2", "2026-08-14T00:02:00Z"
    )
    second = independent_event(
        "AUTH-R2", 2, cycle="AUTH-CYCLE", binding_label="auth-r2"
    )
    agent.ingest_event(ledger, second)
    downgraded = copy.deepcopy(ledger)
    persisted_second = downgraded["events"][-1]
    persisted_second.update({
        "audit_class": "production_preflight",
        "validation_role": "production",
        "task_type": "generation_audit",
    })
    for field in agent.INDEPENDENT_AUDIT_REQUIRED_FIELDS:
        if field != "validation_role":
            persisted_second.pop(field, None)
    assert_true(
        agent.validate_ledger(downgraded),
        "authority reopen trigger cannot be downgraded after persistence",
    )


def test_clean_preflight_supersession_is_narrowly_scoped() -> None:
    ledger = fresh()
    first = event("PREFLIGHT-R1", 1, result="pass", cycle="PREFLIGHT-CYCLE")
    first["task_type"] = "production_draft_preflight"
    accepted = agent.ingest_event(ledger, first)
    assert_true(accepted["closure_state"] == "preflight_clean", "preflight closes cleanly")
    second = event("PREFLIGHT-R2", 2, result="pass", cycle="PREFLIGHT-CYCLE")
    second["task_type"] = "production_draft_preflight"
    agent.ingest_event(ledger, second)
    assert_true(agent.validate_ledger(ledger) == [], "new preflight revision supersedes old")
    invalid = event("PREFLIGHT-R3-AUDIT", 3, result="pass", cycle="PREFLIGHT-CYCLE")
    assert_rejected_without_mutation(
        ledger,
        lambda: agent.ingest_event(ledger, invalid),
        "preflight_clean cannot authorize a non-preflight continuation",
    )


def test_certificate_pass_contract_requires_reviewed_shot_and_pass_conclusion() -> None:
    certificate_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": LEDGER_SCHEMA["$defs"],
        "$ref": "#/$defs/independent_audit_certificate",
    }
    validator = Draft202012Validator(certificate_schema)
    pass_check = {"status": "pass", "evidence_count": 1, "evidence_ids": ["EV-1"]}
    semantic_pass = {
        **pass_check,
        "summary": "independently checked",
    }
    certificate = {
        "certificate_id": "CERT-1",
        "event_id": "CERT-EVENT-1",
        "event_sha256": digest("CERT-EVENT-1"),
        "handoff_id": "CERT-HND-1",
        "work_order_id": "WO-1",
        "audit_actor_binding_id": "ACTOR-1",
        "audit_session_fingerprint": digest("session"),
        "stage_trace_id": "TRC-CERT-1",
        "production_trace_id": "TRC-PROD-1",
        "audit_trace_id": "TRC-AUDIT-1",
        "created_at": "2026-08-31T00:00:00Z",
        "fingerprints": {
            field: digest(field)
            for field in (
                "source", "boundary", "artifact_raw", "artifact_semantic",
                "canonical", "parser", "schema", "runtime", "rules",
                "validator", "project_anchors", "target_model_contract",
            )
        },
        "source_ingestion_mode": "non_chapter",
        "independent_of_production": True,
        "preventive_gate_results": {
            f"G{index}": copy.deepcopy(pass_check) for index in range(14)
        },
        "audit_layer_results": {
            layer: copy.deepcopy(semantic_pass) for layer in agent.AUDIT_CATEGORIES
        },
        "quality_thresholds": {
            "source_and_key_dialogue_mapping_percent": 100,
            "blocking_error_count": 0,
            "old_content_hit_count": 0,
            "cross_context_term_count": 0,
            "shot_pass_percent": 100,
            "residual_issue_count": 0,
            "introduced_issue_count": 0,
        },
        "semantic_audit_contract": "semantic-audit-v2-compact-master",
        "semantic_audit_sha256": digest("semantic"),
        "reviewed_shot_count": 1,
        "semantic_conclusion": {"status": "pass", "summary": "clean"},
        "result": "pass",
    }
    assert_true(list(validator.iter_errors(certificate)) == [], "valid PASS certificate")
    zero = copy.deepcopy(certificate)
    zero["reviewed_shot_count"] = 0
    assert_true(list(validator.iter_errors(zero)), "PASS certificate must review a shot")
    inconsistent = copy.deepcopy(certificate)
    inconsistent["semantic_conclusion"]["status"] = "fail_current"
    assert_true(list(validator.iter_errors(inconsistent)), "PASS conclusion must be pass")
    current = copy.deepcopy(certificate)
    current["semantic_audit_contract"] = "semantic-audit-v3-adjacent-continuity"
    current["reviewed_adjacent_pair_count"] = 0
    current["adjacent_pair_order_sha256"] = digest("empty ordered pair list")
    current["quality_thresholds"]["adjacent_pair_coverage_percent"] = 100
    current["adjacent_continuity"] = {
        "contract": "adjacent-pair-continuity-v1",
        "expected_pair_count": 0,
        "reviewed_pair_count": 0,
        "coverage_percent": 100,
        "ordered_pair_sha256": digest("empty ordered pair list"),
        "pair_ids": [],
        "production_proof_verified": True,
        "production_failed_pair_count": 0,
        "spatial_layer_status": "pass",
    }
    assert_true(
        list(validator.iter_errors(current)) == [],
        "valid v3 PASS certificate must carry the complete adjacent-pair proof",
    )
    current_v5 = copy.deepcopy(current)
    current_v5["semantic_audit_contract"] = (
        "semantic-audit-v7-adjacent-continuity-structured-voice-conversation-t2i-lighting"
    )
    current_v5["voice_field_coverage"] = {
        "contract": "fixed-voice-template-v1",
        "expected_dialogue_shot_count": 0,
        "reviewed_voice_field_count": 0,
        "coverage_percent": 100,
        "evidence_ids": [],
        "audio_layer_status": "pass",
        "machine_voice_issue_count": 0,
    }
    current_v5["t2i_lighting_field_coverage"] = {
        "contract": "t2i-static-observable-lighting-v1",
        "expected_shot_count": 1,
        "expected_field_count": 4,
        "reviewed_field_count": 4,
        "coverage_percent": 100,
        "ordered_fields": ["光影", "光源来源", "光源方向", "色温倾向"],
        "evidence_ids": [
            "field:EP01-001.光影",
            "field:EP01-001.光源来源",
            "field:EP01-001.光源方向",
            "field:EP01-001.色温倾向",
        ],
        "camera_visibility_layer_status": "pass",
        "machine_t2i_issue_count": 0,
        "machine_lighting_issue_count": 0,
    }
    current_v5["adapted_dialogue_coverage"] = {
        "contract": "source-fact-dialogue-v1",
        "expected_adapted_dialogue_count": 0,
        "expected_target_shot_count": 0,
        "reviewed_target_shot_count": 0,
        "coverage_percent": 100,
        "ordered_entry_sha256": digest("empty adapted dialogue list"),
        "evidence_ids": [],
        "source_layer_status": "pass",
        "machine_adaptation_issue_count": 0,
    }
    current_v5["conversation_scene_classification_coverage"] = {
        "contract": "narrative-conversation-scene-v1",
        "expected_source_unit_count": 0,
        "reviewed_source_unit_count": 0,
        "conversation_scene_count": 0,
        "coverage_percent": 100,
        "evidence_ids": [],
        "source_layer_status": "pass",
    }
    assert_true(
        list(validator.iter_errors(current_v5)) == [],
        "valid v7 PASS certificate must carry voice, dialogue, conversation, T2I, and lighting proofs",
    )
    missing_voice_proof = copy.deepcopy(current_v5)
    missing_voice_proof.pop("voice_field_coverage")
    assert_true(
        list(validator.iter_errors(missing_voice_proof)),
        "v5 certificate without voice-field proof must fail schema validation",
    )
    missing_dialogue_proof = copy.deepcopy(current_v5)
    missing_dialogue_proof.pop("adapted_dialogue_coverage")
    assert_true(
        list(validator.iter_errors(missing_dialogue_proof)),
        "v5 certificate without source-fact-dialogue proof must fail schema validation",
    )
    missing_scene_proof = copy.deepcopy(current_v5)
    missing_scene_proof.pop("conversation_scene_classification_coverage")
    assert_true(
        list(validator.iter_errors(missing_scene_proof)),
        "v6 certificate without conversation-scene classification proof must fail schema validation",
    )
    missing_t2i_lighting_proof = copy.deepcopy(current_v5)
    missing_t2i_lighting_proof.pop("t2i_lighting_field_coverage")
    assert_true(
        list(validator.iter_errors(missing_t2i_lighting_proof)),
        "v7 certificate without T2I/lighting field proof must fail schema validation",
    )
    missing_adjacent = copy.deepcopy(current)
    missing_adjacent.pop("adjacent_continuity")
    assert_true(
        list(validator.iter_errors(missing_adjacent)),
        "v3 certificate without adjacent proof must fail schema validation",
    )


def test_migrated_legacy_block_has_independent_fail_repair_pass_path() -> None:
    _original, migrated, authorizer = migrate_synthetic_legacy()
    legacy_event = next(
        row for row in migrated["events"] if row["event_id"] == "LEGACY-E10"
    )
    legacy_issue_ids = {
        issue["issue_id"]
        for issue in migrated["issues"]
        if issue["logical_file"] == legacy_event["file_name"]
    }
    assert_true(len(legacy_issue_ids) == 8, "fixture has eight legacy file blockers")

    failed = independent_event(
        "NEW-AUDIT-FAIL",
        1,
        result="fail_current",
        cycle="NEW-INDEPENDENT-CYCLE",
        file_name=legacy_event["file_name"],
        episode_id=legacy_event["episode_id"],
        binding_label="new-independent-fail",
    )
    failed["timestamp"] = "2026-09-02T00:00:00Z"
    failed_result = agent.ingest_event(
        migrated, failed, checkpoint_authorizer=authorizer
    )
    assert_true(
        all(
            issue["status"] == "open"
            for issue in migrated["issues"]
            if issue["issue_id"] in legacy_issue_ids
        ),
        "failed independent audit must retain legacy blockers",
    )

    repaired = independent_event(
        "NEW-AUDIT-PASS",
        2,
        cycle="NEW-INDEPENDENT-CYCLE",
        file_name=legacy_event["file_name"],
        episode_id=legacy_event["episode_id"],
        binding_label="new-independent-repaired",
    )
    repaired["timestamp"] = "2026-09-02T00:10:00Z"
    pass_result = agent.ingest_event(
        migrated, repaired, checkpoint_authorizer=authorizer
    )
    assert_true(
        pass_result["closure_state"] == "awaiting_human_signoff",
        "fresh repaired PASS reaches signoff gate",
    )
    assert_true(
        all(
            issue["status"] == "resolved"
            and issue["legacy_resolution_requires_reaudit"] is False
            for issue in migrated["issues"]
            if issue["issue_id"] in legacy_issue_ids
        ),
        "fresh independent PASS clears only its covered legacy blockers",
    )
    signed = agent.signoff_scope(
        migrated,
        failed_result["scope_key"],
        "approve",
        "2026-09-02T00:20:00Z",
        checkpoint_authorizer=authorizer,
    )
    assert_true(signed["can_advance"] is True, "new independent closure is reachable")
    assert_true(
        agent.validate_ledger(migrated, checkpoint_authorizer=authorizer) == [],
        "full migrated fail-repair-pass-signoff path must replay",
    )


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("init_and_validate", test_init_and_validate),
    ("first_occurrence_no_candidate", test_first_occurrence_no_candidate),
    ("second_round_root_cause_and_candidate", test_second_round_root_cause_and_candidate),
    ("third_round_upstream_rebuild", test_third_round_upstream_rebuild),
    ("duplicate_event_is_idempotent", test_duplicate_event_is_idempotent),
    ("hard_round_limit", test_hard_round_limit),
    ("fail_current_blocks_advance", test_fail_current_blocks_advance),
    ("clean_pass_requires_human_signoff", test_clean_pass_requires_human_signoff),
    ("clean_pass_blocks_cross_window_continuation", test_clean_pass_blocks_cross_window_continuation),
    ("issue_remains_open_in_another_cycle", test_issue_remains_open_in_another_cycle),
    ("candidate_checks_and_approval_do_not_mutate_runtime", test_candidate_checks_and_approval_do_not_mutate_runtime),
    ("approval_without_validation_stays_pending", test_approval_without_validation_stays_pending),
    ("resolution_and_metrics", test_resolution_and_metrics),
    ("malformed_event_rejected", test_malformed_event_rejected),
    ("compact_master_audit_category_is_canonical", test_compact_master_audit_category_is_canonical),
    ("legacy_seedance_category_is_input_alias_only", test_legacy_seedance_category_is_input_alias_only),
    ("runtime_and_schema_pass_negative_matrix", test_runtime_and_schema_pass_negative_matrix),
    ("valid_independent_pass_matches_schema_and_signs_off", test_valid_independent_pass_matches_schema_and_signs_off),
    ("current_independent_event_requires_semantic_recovery_bindings", test_current_independent_event_requires_semantic_recovery_bindings),
    ("checkpoint_post_prefix_semantic_bindings_are_runtime_current", test_checkpoint_post_prefix_semantic_bindings_are_runtime_current),
    ("independent_reopen_requires_new_audit_before_signoff", test_independent_reopen_requires_new_audit_before_signoff),
    ("handoff_and_trace_are_globally_unique_across_scopes", test_handoff_and_trace_are_globally_unique_across_scopes),
    ("failed_bindings_cannot_be_relabelled_pass", test_failed_bindings_cannot_be_relabelled_pass),
    ("failed_binding_cannot_escape_by_cycle_or_filename", test_failed_binding_cannot_escape_by_cycle_or_filename),
    ("round_and_scope_rejections_are_atomic", test_round_and_scope_rejections_are_atomic),
    ("normalized_event_id_is_idempotent_only_for_same_payload", test_normalized_event_id_is_idempotent_only_for_same_payload),
    ("validate_ledger_replays_scope_round_freshness_and_counters", test_validate_ledger_replays_scope_round_freshness_and_counters),
    ("schema_scope_state_matrix_matches_runtime", test_schema_scope_state_matrix_matches_runtime),
    ("legacy_checkpoint_migration_preserves_history_and_downgrades_authority", test_legacy_checkpoint_migration_preserves_history_and_downgrades_authority),
    ("checkpoint_tamper_and_prefix_mutation_fail_closed", test_checkpoint_tamper_and_prefix_mutation_fail_closed),
    ("checkpoint_issue_suffix_forgery_matrix_fails_closed", test_checkpoint_issue_suffix_forgery_matrix_fails_closed),
    ("checkpoint_post_prefix_rounds_modern_shape_and_idempotency", test_checkpoint_post_prefix_rounds_modern_shape_and_idempotency),
    ("same_second_actions_bind_exact_basis_event", test_same_second_actions_bind_exact_basis_event),
    ("legacy_signoff_ambiguity_and_invalid_history_fail_without_mutation", test_legacy_signoff_ambiguity_and_invalid_history_fail_without_mutation),
    ("persisted_shape_timestamp_and_reopen_authority_matrix", test_persisted_shape_timestamp_and_reopen_authority_matrix),
    ("clean_preflight_supersession_is_narrowly_scoped", test_clean_preflight_supersession_is_narrowly_scoped),
    ("certificate_pass_contract_requires_reviewed_shot_and_pass_conclusion", test_certificate_pass_contract_requires_reviewed_shot_and_pass_conclusion),
    ("migrated_legacy_block_has_independent_fail_repair_pass_path", test_migrated_legacy_block_has_independent_fail_repair_pass_path),
]


def main() -> int:
    results = []
    for name, test in TESTS:
        try:
            test()
        except Exception as exc:
            results.append({"test_id": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            results.append({"test_id": name, "passed": True})
    passed = sum(result["passed"] for result in results)
    output = {
        "suite_version": agent.SCHEMA_VERSION,
        "test_count": len(results),
        "test_pass_count": passed,
        "test_fail_count": len(results) - passed,
        "status": "pass" if passed == len(results) else "fail",
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
