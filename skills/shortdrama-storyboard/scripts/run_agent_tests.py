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


ROOT = Path(__file__).resolve().parent.parent
AGENT_PATH = Path(__file__).resolve().parent / "storyboard_agent.py"
RUNTIME_PATH = ROOT / "references" / "storyboard_runtime_rules.json"


def load_agent():
    spec = importlib.util.spec_from_file_location("r10_storyboard_agent", AGENT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Agent: {AGENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_agent()


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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


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
    result = agent.ingest_event(ledger, event("EV-PASS", 1, result="pass", cycle="CYCLE-PASS"))
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
        assert_true("human sign-off" in str(exc), "block must name human sign-off")
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
