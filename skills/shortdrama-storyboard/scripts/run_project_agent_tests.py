#!/usr/bin/env python3
"""Run deterministic lifecycle tests for the full storyboard project Agent."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable


AGENT_PATH = Path(__file__).resolve().parent / "project_agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("r10_project_agent", AGENT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Agent: {AGENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_agent()


def fresh() -> dict[str, Any]:
    return agent.new_hub("分镜项目", "SBP-TEST", "2026-08-14T00:00:00Z")


def task(
    work_order_id: str = "WO-001",
    operation: str = "storyboard_change",
    mode: str = "execute",
    authorization: str = "explicit_execute",
) -> dict[str, Any]:
    return {
        "work_order_id": work_order_id,
        "timestamp": "2026-08-14T00:01:00Z",
        "window_key": "test-window",
        "request_text": "修改第8集剧情并同步生成受影响分镜。",
        "operation": operation,
        "mode": mode,
        "targets": ["EP08", "S12-S20"],
        "source_lock": {
            "authoritative_source": "用户当前修改剧情",
            "latest_version": "v3",
            "episode_boundary": "EP08",
            "must_keep": ["原有对话"],
            "must_remove": ["旧动作链"]
        },
        "protected_scope": ["EP01-EP07", "EP09以后"],
        "expected_outputs": ["修正版剧本", "compact canonical", "正式母版导出"],
        "authorization": authorization,
    }


def change(
    change_id: str = "CH-001",
    work_order_id: str = "WO-001",
    operation_names: list[str] | None = None,
    authorization: str = "explicit_execute",
) -> dict[str, Any]:
    names = operation_names or ["add", "modify", "delete", "replace"]
    operations = []
    for index, name in enumerate(names, 1):
        operations.append({
            "operation": name,
            "target_ids": [f"SC-{index:02d}"],
            "instruction": f"执行{name}操作",
            "authoritative_basis": "用户当前明确指令",
            "protected_scope": ["未点名场次"],
        })
    return {
        "change_id": change_id,
        "work_order_id": work_order_id,
        "timestamp": "2026-08-14T00:02:00Z",
        "target_type": "script_and_storyboard",
        "target_file": "第8集.xlsx",
        "source_version": "v3",
        "operations": operations,
        "authorization": authorization,
    }


def deliverable(work_order_id: str = "WO-001") -> dict[str, Any]:
    return {
        "deliverable_id": f"DEL-{work_order_id}",
        "work_order_id": work_order_id,
        "timestamp": "2026-08-14T00:03:00Z",
        "artifact_type": "storyboard",
        "file_name": "第8集_修正版.xlsx",
        "version": "v4",
        "fingerprint": "sha256:test-v4",
        "status": "validated",
    }


def clean_validation(hub: dict[str, Any], work_order_id: str = "WO-001") -> dict[str, Any]:
    work_order = next(item for item in hub["work_orders"] if item["work_order_id"] == work_order_id)
    return {
        "work_order_id": work_order_id,
        "timestamp": "2026-08-14T00:04:00Z",
        "result": "pass",
        "gate_results": {gate: "pass" for gate in work_order["required_validation_gates"]},
        "residual_issue_count": 0,
        "introduced_issue_count": 0,
        "blockers": [],
    }


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def test_new_hub_and_registry() -> None:
    hub = fresh()
    assert_true(agent.validate_hub(hub) == [], "new hub must validate")
    summary = agent.summarize(hub)
    assert_true(summary["capability_count"] >= 30, "full Agent must expose broad capability registry")
    assert_true(summary["capability_status_counts"]["supported"] >= 20, "supported capability count")
    assert_true(hub["policy"]["compact_master_version"] == "0.5.5", "hub must pin compact master 0.5.5")
    assert_true(hub["policy"]["background_seedance_generation"] is False, "background Seedance must stay disabled")


def test_plan_only_never_mutates() -> None:
    hub = fresh()
    request = task("WO-PLAN", "rewrite_plan", "plan_only", "plan_only")
    result = agent.create_work_order(hub, request)
    work_order = hub["work_orders"][0]
    assert_true(result["work_order_status"] == "scope_locked", "plan starts scope locked")
    assert_true(work_order["mutates_source"] is False, "plan must not mutate source")


def test_execute_requires_authorization() -> None:
    hub = fresh()
    result = agent.create_work_order(hub, task(authorization="pending"))
    assert_true(result["work_order_status"] == "awaiting_approval", "pending execution waits for approval")


def test_all_change_operation_types() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    names = ["add", "modify", "delete", "replace", "merge", "split", "move"]
    result = agent.create_change_request(hub, change(operation_names=names))
    assert_true(result["change_status"] == "approved", "explicit changes are approved")
    assert_true(result["destructive_within_scope"] is True, "destructive operations detected")


def test_change_impact_propagates_all_layers() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    result = agent.create_change_request(hub, change(operation_names=["replace", "move"]))
    required = set(result["required_downstream_updates"])
    expected = {
        "source_ledger", "cognition_matrix", "scene_state_ledger", "compact_canonical",
        "formal_master_export", "keyframe_assets", "voice_audio_bgm", "on_demand_production_package",
        "stale_reference_scan", "boundary_bridge", "causal_continuity",
    }
    assert_true(expected <= required, "script change must propagate to every downstream layer")
    assert_true("seedance_batches" not in required, "impact propagation must not recreate Seedance batches")


def test_destructive_change_without_approval_waits() -> None:
    hub = fresh()
    agent.create_work_order(hub, task(authorization="pending"))
    result = agent.create_change_request(hub, change(operation_names=["delete"], authorization="pending"))
    assert_true(result["change_status"] == "awaiting_approval", "delete cannot bypass approval")
    assert_true(hub["change_requests"][0]["preserve_previous_version"] is True, "prior version preserved")


def test_duplicate_task_is_idempotent() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    before = copy.deepcopy(hub)
    result = agent.create_work_order(hub, task())
    assert_true(result["status"] == "duplicate", "duplicate task reported")
    assert_true(hub == before, "duplicate task must not mutate hub")


def test_invalid_transition_rejected() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    try:
        agent.transition_work_order(hub, "WO-001", "archived", "2026-08-14T00:05:00Z")
    except ValueError as exc:
        assert_true("invalid transition" in str(exc), "invalid transition must be named")
    else:
        raise AssertionError("direct archive must fail")


def test_round_count_does_not_abandon_unresolved_work() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    agent.transition_work_order(hub, "WO-001", "executing")
    for _ in range(4):
        agent.transition_work_order(hub, "WO-001", "failed_current")
        agent.transition_work_order(hub, "WO-001", "executing")
    assert_true(hub["work_orders"][0]["internal_round"] == 5, "rounds remain recorded")
    assert_true(not agent.validate_hub(hub), "later rounds remain readable")


def test_validation_requires_deliverable() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    agent.transition_work_order(hub, "WO-001", "executing")
    try:
        agent.transition_work_order(hub, "WO-001", "validating")
    except ValueError as exc:
        assert_true("deliverable" in str(exc), "missing deliverable named")
    else:
        raise AssertionError("validation without deliverable must fail")


def test_dirty_validation_cannot_pass() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    agent.transition_work_order(hub, "WO-001", "executing")
    agent.record_deliverable(hub, deliverable())
    agent.transition_work_order(hub, "WO-001", "validating")
    record = clean_validation(hub)
    record["introduced_issue_count"] = 1
    record["result"] = "fail"
    agent.record_validation(hub, record)
    try:
        agent.transition_work_order(hub, "WO-001", "passed")
    except ValueError as exc:
        assert_true("clean validation" in str(exc), "dirty validation cannot pass")
    else:
        raise AssertionError("dirty validation must block pass")


def test_bare_storyboard_pass_stays_pending_audit() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    agent.transition_work_order(hub, "WO-001", "executing")
    agent.record_deliverable(hub, deliverable())
    agent.transition_work_order(hub, "WO-001", "validating")
    try:
        agent.record_validation(hub, clean_validation(hub))
    except ValueError as exc:
        assert_true(
            "independent_audit_required" in str(exc),
            "bare storyboard PASS must name the independent-audit requirement",
        )
    else:
        raise AssertionError("bare storyboard PASS must not validate production")
    work_order = hub["work_orders"][0]
    assert_true(work_order["status"] == "validating", "production stays validating")
    assert_true(
        work_order["validation"]["status"] == "pending",
        "production validation stays pending",
    )
    assert_true(
        work_order["awaiting_independent_audit"] is True,
        "production explicitly awaits independent audit",
    )


def test_non_storyboard_clean_flow_requires_human_signoff() -> None:
    hub = fresh()
    agent.create_work_order(hub, task(operation="script_change"))
    agent.transition_work_order(hub, "WO-001", "executing")
    agent.record_deliverable(hub, deliverable())
    agent.transition_work_order(hub, "WO-001", "validating")
    # Simulate the service-owned binding for this low-level sign-off positive.
    # Unverified narrative PASS claims have separate negative coverage.
    hub["work_orders"][0]["verified_narrative_review_id"] = "SERVER-REVIEW-SYNTHETIC"
    agent.record_validation(hub, clean_validation(hub))
    agent.transition_work_order(hub, "WO-001", "passed")
    result = agent.transition_work_order(hub, "WO-001", "awaiting_signoff")
    assert_true(result["to"] == "awaiting_signoff", "clean work waits for sign-off")
    assert_true(hub["work_orders"][0]["status"] != "archived", "not archived before human")
    signed = agent.signoff_work_order(hub, "WO-001", "approve", "2026-08-14T00:06:00Z")
    assert_true(signed["status"] == "archived", "human approval archives work")
    assert_true(agent.validate_hub(hub) == [], "signed hub validates")


def test_latest_artifact_pointer_updates() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    agent.record_deliverable(hub, deliverable())
    latest = hub["project_state"]["latest_artifacts"]["storyboard"]
    assert_true(latest["version"] == "v4", "validated deliverable becomes latest")


def test_capability_gap_is_proposal_only() -> None:
    hub = fresh()
    result = agent.record_capability_gap(hub, {
        "gap_id": "GAP-001",
        "timestamp": "2026-08-14T00:07:00Z",
        "request": "自动后台扫描所有休眠聊天",
        "missing_dependency": "持续后台触发器与聊天读取权限",
        "risk": "越权读取或虚构同步",
        "safe_fallback": "每次Agent任务触发时写入共享中枢",
        "proposed_implementation": "经授权配置外部触发工作流",
        "required_tests": ["permission gate", "idempotency", "cross-window writeback"],
    })
    assert_true(result["formal_capability_mutated"] is False, "gap cannot auto-install capability")
    assert_true(hub["capability_gaps"][0]["status"] == "proposed", "gap starts proposed")


def test_unknown_operation_rejected() -> None:
    hub = fresh()
    request = task(operation="unknown_magic")
    try:
        agent.create_work_order(hub, request)
    except ValueError as exc:
        assert_true("unsupported operation" in str(exc), "unknown operation named")
    else:
        raise AssertionError("unknown operation must fail")


def test_incomplete_source_lock_rejected() -> None:
    hub = fresh()
    request = task()
    request["source_lock"] = {}
    try:
        agent.create_work_order(hub, request)
    except ValueError as exc:
        assert_true("source_lock" in str(exc), "source lock fields must be enforced")
    else:
        raise AssertionError("incomplete source lock must fail")


def test_unrelated_task_cannot_own_change_request() -> None:
    hub = fresh()
    agent.create_work_order(hub, task(operation="novel_analysis", mode="plan_only", authorization="plan_only"))
    try:
        agent.create_change_request(hub, change())
    except ValueError as exc:
        assert_true("cannot own a change request" in str(exc), "change parent must be a change workflow")
    else:
        raise AssertionError("analysis task must not own destructive changes")


def test_conditional_capabilities_are_explicit() -> None:
    hub = fresh()
    capabilities = agent.capability_map(hub)
    assert_true(capabilities["keyframe_image"]["status"] == "conditional", "image generation conditions explicit")
    assert_true(capabilities["format_convert"]["status"] == "conditional", "format tool conditions explicit")
    assert_true(capabilities["production_batch"]["status"] == "supported", "explicit on-demand production remains supported")
    assert_true("seedance_create" not in capabilities, "background Seedance workflow is retired")


def test_storyboard_window_roles_and_compact_gate() -> None:
    hub = fresh()
    production = agent.create_work_order(hub, task("WO-PROD", "storyboard_change", "execute", "explicit_execute"))
    audit = agent.create_work_order(hub, task("WO-AUDIT", "storyboard_audit", "audit_only", "read_only"))
    repair = agent.create_work_order(hub, task("WO-REPAIR", "storyboard_repair", "audit_and_repair", "explicit_execute"))
    by_id = {row["work_order_id"]: row for row in hub["work_orders"]}
    assert_true(by_id["WO-PROD"]["server_actor_role"] == "production", "production role isolated")
    assert_true(by_id["WO-AUDIT"]["server_actor_role"] == "independent_audit", "audit role isolated")
    assert_true(by_id["WO-AUDIT"]["mutates_source"] is False, "independent audit is read-only")
    assert_true(by_id["WO-REPAIR"]["server_actor_role"] == "repair", "repair role isolated")
    assert_true(by_id["WO-REPAIR"]["repair_activation_condition"] == "independent_audit_fail_and_explicit_authorization", "repair stays conditional")
    assert_true(by_id["WO-REPAIR"]["repair_return_role"] == "independent_audit", "repair returns to original audit authority")
    for result in (production, audit, repair):
        assert_true("compact_master_single_shot_integrity" in result["required_validation_gates"], "compact master gate required")


def test_background_seedance_outputs_are_rejected() -> None:
    hub = fresh()
    request = task()
    request["expected_outputs"] = ["compact canonical", "独立Seedance批次"]
    try:
        agent.create_work_order(hub, request)
    except ValueError as exc:
        assert_true("forbids background" in str(exc), "forbidden output must name background generation")
    else:
        raise AssertionError("new workflow must reject independent Seedance batches")


def test_persisted_seedance_deliverable_is_rejected() -> None:
    hub = fresh()
    agent.create_work_order(hub, task())
    record = deliverable()
    record["artifact_type"] = "seedance_batches"
    try:
        agent.record_deliverable(hub, record)
    except ValueError as exc:
        assert_true("forbids persisted" in str(exc), "persisted duplicate generation must be rejected")
    else:
        raise AssertionError("Seedance batch deliverable must fail")


TESTS: list[tuple[str, Callable[[], None]]] = [
    ("new_hub_and_registry", test_new_hub_and_registry),
    ("plan_only_never_mutates", test_plan_only_never_mutates),
    ("execute_requires_authorization", test_execute_requires_authorization),
    ("all_change_operation_types", test_all_change_operation_types),
    ("change_impact_propagates_all_layers", test_change_impact_propagates_all_layers),
    ("destructive_change_without_approval_waits", test_destructive_change_without_approval_waits),
    ("duplicate_task_is_idempotent", test_duplicate_task_is_idempotent),
    ("invalid_transition_rejected", test_invalid_transition_rejected),
    ("round_count_is_not_repair_permission", test_round_count_does_not_abandon_unresolved_work),
    ("validation_requires_deliverable", test_validation_requires_deliverable),
    ("dirty_validation_cannot_pass", test_dirty_validation_cannot_pass),
    ("bare_storyboard_pass_stays_pending_audit", test_bare_storyboard_pass_stays_pending_audit),
    ("non_storyboard_clean_flow_requires_human_signoff", test_non_storyboard_clean_flow_requires_human_signoff),
    ("latest_artifact_pointer_updates", test_latest_artifact_pointer_updates),
    ("capability_gap_is_proposal_only", test_capability_gap_is_proposal_only),
    ("unknown_operation_rejected", test_unknown_operation_rejected),
    ("incomplete_source_lock_rejected", test_incomplete_source_lock_rejected),
    ("unrelated_task_cannot_own_change_request", test_unrelated_task_cannot_own_change_request),
    ("conditional_capabilities_are_explicit", test_conditional_capabilities_are_explicit),
    ("storyboard_window_roles_and_compact_gate", test_storyboard_window_roles_and_compact_gate),
    ("background_seedance_outputs_are_rejected", test_background_seedance_outputs_are_rejected),
    ("persisted_seedance_deliverable_is_rejected", test_persisted_seedance_deliverable_is_rejected),
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
