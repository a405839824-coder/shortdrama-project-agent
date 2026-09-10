"""Work-order gates, not a substitute for the service's signed narrative review."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "shortdrama-storyboard"
SPEC = importlib.util.spec_from_file_location(
    "narrative_work_orders_project_agent", SKILL / "scripts" / "project_agent.py"
)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)
STAMP = "2026-09-10T00:00:00Z"
OPERATIONS = ("script_create", "script_rewrite", "script_change", "dialogue_rewrite")


def request(operation: str = "script_create") -> dict:
    mode, authorization = {
        "narrative_audit": ("audit_only", "read_only"),
        "storyboard_audit": ("audit_only", "read_only"),
        "storyboard_repair": ("audit_and_repair", "explicit_execute"),
        "episode_plan": ("plan_only", "plan_only"),
        "rewrite_plan": ("plan_only", "plan_only"),
    }.get(operation, ("execute", "explicit_execute"))
    return {
        "work_order_id": "WO-SYNTHETIC", "timestamp": STAMP, "window_key": "synthetic",
        "request_text": "Process the declared synthetic scope.", "operation": operation,
        "mode": mode, "authorization": authorization, "targets": ["scene-a"],
        "source_lock": {"authoritative_source": "synthetic-source", "latest_version": "v1",
                        "must_keep": [], "must_remove": [], "target_boundary": "scene-a"},
        "protected_scope": [], "expected_outputs": ["review" if operation == "narrative_audit" else "script"],
    }


def setup_order(operation: str = "script_create") -> tuple[dict, dict]:
    hub = agent.new_hub("Synthetic narrative contract", timestamp=STAMP)
    agent.create_work_order(hub, request(operation))
    return hub, hub["work_orders"][0]


def clean_claim(order: dict) -> dict:
    return {
        "work_order_id": order["work_order_id"], "timestamp": STAMP, "result": "pass",
        "gate_results": {gate: "pass" for gate in order["required_validation_gates"]},
        "residual_issue_count": 0, "introduced_issue_count": 0, "blockers": [],
    }


class NarrativeWorkOrderTests(unittest.TestCase):
    def test_audit_registration_is_read_only_and_independent(self):
        hub, order = setup_order("narrative_audit")
        self.assertEqual(order["server_actor_role"], "independent_audit")
        self.assertEqual(order["status"], "scope_locked")
        self.assertFalse(order["mutates_source"])
        self.assertEqual(order["required_validation_gates"], agent.SCRIPT_GATES)
        self.assertEqual(agent.capability_map(hub)["narrative_audit"]["approval_policy"], "read_only")
        self.assertEqual(agent.validate_hub(hub), [])

    def test_audit_rejects_mutation_authority_and_caller_role(self):
        for key, value in (("mode", "execute"), ("mode", "audit_and_repair"),
                           ("authorization", "explicit_execute"), ("authorization", "pending"),
                           ("server_actor_role", "production"), ("server_actor_role", "repair")):
            with self.subTest(key=key, value=value):
                hub = agent.new_hub("Synthetic", timestamp=STAMP)
                raw = request("narrative_audit")
                raw[key] = value
                with self.assertRaisesRegex(ValueError, "narrative_audit requires"):
                    agent.create_work_order(hub, raw)
                self.assertEqual(hub["work_orders"], [])

    def test_new_request_cannot_supply_service_marker(self):
        for value in ("caller-invented", None, True, {"verified": True}):
            with self.subTest(value=value):
                raw = request()
                raw["verified_narrative_review_id"] = value
                with self.assertRaisesRegex(ValueError, "service-owned"):
                    agent.create_work_order(agent.new_hub("Synthetic", timestamp=STAMP), raw)

    def test_each_body_operation_rejects_self_reported_clean_validation(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                hub, order = setup_order(operation)
                record = clean_claim(order)
                record["verified_narrative_review_id"] = "claim-cannot-set-order-marker"
                with self.assertRaisesRegex(ValueError, "narrative_review_required"):
                    agent.record_validation(hub, record)
                self.assertEqual(order["validation"]["status"], "pending")
                self.assertEqual(order["validation"]["records"], [])
                self.assertNotIn("verified_narrative_review_id", order)

    def test_invalid_marker_types_do_not_become_a_verified_id(self):
        for value in (None, "", " \t", False, 7, {"id": "review"}, ["review"]):
            with self.subTest(value=value):
                hub, order = setup_order()
                order["verified_narrative_review_id"] = value
                with self.assertRaisesRegex(ValueError, "narrative_review_required"):
                    agent.record_validation(hub, clean_claim(order))

    def test_new_pass_signoff_and_historical_pass_cannot_bypass_review(self):
        for operation in OPERATIONS:
            for start, target in (("validating", "passed"), ("proposal_ready", "passed"),
                                  ("passed", "awaiting_signoff")):
                with self.subTest(operation=operation, start=start, target=target):
                    hub, order = setup_order(operation)
                    order["status"] = start
                    order["validation"]["status"] = "pass"
                    with self.assertRaisesRegex(ValueError, "narrative_review_required"):
                        agent.transition_work_order(hub, order["work_order_id"], target, STAMP)
                    self.assertEqual(order["status"], start)
            hub, order = setup_order(operation)
            order["status"] = "awaiting_signoff"
            with self.assertRaisesRegex(ValueError, "narrative_review_required"):
                agent.signoff_work_order(hub, order["work_order_id"], "approve", STAMP)
            self.assertEqual(order["status"], "awaiting_signoff")
            self.assertEqual(hub["human_signoffs"], [])

    def test_trusted_marker_allows_low_level_pass_without_claiming_signature_proof(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                hub, order = setup_order(operation)
                order["status"] = "validating"
                # Simulate only the trusted service's completed binding, not a caller request.
                order["verified_narrative_review_id"] = "server-review-example"
                self.assertEqual(agent.record_validation(hub, clean_claim(order))["status"], "pass")
                agent.transition_work_order(hub, order["work_order_id"], "passed", STAMP)
                agent.transition_work_order(hub, order["work_order_id"], "awaiting_signoff", STAMP)
                self.assertEqual(agent.signoff_work_order(hub, order["work_order_id"], "approve", STAMP)["status"], "archived")
                self.assertFalse(order["awaiting_narrative_review"])

    def test_fail_records_remain_recordable_without_review(self):
        hub, order = setup_order()
        record = clean_claim(order)
        record.update(result="fail", residual_issue_count=1, blockers=["source content still unresolved"])
        self.assertEqual(agent.record_validation(hub, record)["status"], "fail")
        self.assertEqual(order["validation"]["records"][0]["residual_issue_count"], 1)

    def test_narrative_round_four_is_executable_readable_and_loses_old_review(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                hub, order = setup_order(operation)
                order.update(status="failed_current", internal_round=3, verified_narrative_review_id="old-round-review")
                result = agent.transition_work_order(hub, order["work_order_id"], "executing", STAMP)
                self.assertEqual(result["internal_round"], 4)
                self.assertNotIn("verified_narrative_review_id", order)
                self.assertEqual(order["validation"]["status"], "pending")
                self.assertEqual(agent.validate_hub(hub), [])
                order["internal_round"] = 20
                self.assertEqual(agent.validate_hub(hub), [])

    def test_legacy_storyboard_round_limit_and_certificate_gate_are_unchanged(self):
        hub, order = setup_order("storyboard_repair")
        self.assertEqual(order["server_actor_role"], "repair")
        self.assertEqual(order["repair_return_role"], "independent_audit")
        order.update(status="failed_current", internal_round=3)
        with self.assertRaisesRegex(ValueError, "maximum internal repair rounds"):
            agent.transition_work_order(hub, order["work_order_id"], "executing", STAMP)
        self.assertEqual(order["internal_round"], 3)
        with self.assertRaisesRegex(ValueError, "independent_audit_required"):
            agent.record_validation(hub, clean_claim(order))
        order["internal_round"] = 4
        self.assertTrue(any("invalid internal_round" in error for error in agent.validate_hub(hub)))

    def test_historical_narrative_pass_and_archive_can_still_be_read(self):
        for status in ("passed", "awaiting_signoff", "archived"):
            with self.subTest(status=status):
                hub, order = setup_order()
                order["status"] = status
                order["validation"]["status"] = "pass"
                if status == "archived":
                    hub["human_signoffs"].append({"work_order_id": order["work_order_id"], "decision": "approve", "timestamp": STAMP})
                before = copy.deepcopy(hub)
                self.assertEqual(agent.validate_hub(hub), [])
                self.assertEqual(hub, before)

    def test_reopen_old_narrative_order_remains_possible_and_invalidates_marker(self):
        for marker in (None, "old-review"):
            hub, order = setup_order()
            order["status"] = "awaiting_signoff"
            if marker:
                order["verified_narrative_review_id"] = marker
            agent.signoff_work_order(hub, order["work_order_id"], "reopen", STAMP)
            self.assertEqual(order["status"], "approved")
            self.assertNotIn("verified_narrative_review_id", order)

    def test_planning_family_keeps_ordinary_claim_path(self):
        for operation in ("episode_plan", "rewrite_plan"):
            with self.subTest(operation=operation):
                hub, order = setup_order(operation)
                self.assertFalse(order["mutates_source"])
                self.assertEqual(agent.record_validation(hub, clean_claim(order))["status"], "pass")
                agent.transition_work_order(hub, order["work_order_id"], "proposal_ready", STAMP)
                agent.transition_work_order(hub, order["work_order_id"], "passed", STAMP)

    def test_schema_describes_marker_and_read_only_audit_without_retroactive_requirement(self):
        schema = json.loads((SKILL / "references" / "project_agent_schema.json").read_text(encoding="utf-8"))
        order_schema = schema["$defs"]["work_order"]
        self.assertNotIn("verified_narrative_review_id", order_schema["required"])
        self.assertEqual(order_schema["properties"]["verified_narrative_review_id"]["type"], "string")
        audit_rule = next(rule for rule in order_schema["allOf"] if rule["if"]["properties"]["operation"]["const"] == "narrative_audit")
        self.assertEqual(audit_rule["then"]["properties"]["mode"]["const"], "audit_only")
        self.assertEqual(audit_rule["then"]["properties"]["authorization"]["const"], "read_only")
        self.assertIs(audit_rule["then"]["properties"]["mutates_source"]["const"], False)


if __name__ == "__main__":
    unittest.main()
