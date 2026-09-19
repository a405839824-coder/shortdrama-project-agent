"""Remove historical default caps without weakening actual failure or host gates."""
import copy
import unittest

from shortdrama_mcp import service as service_module
from test_efficiency import valid_storyboard
import test_v055_quality_migration as migration_tests
import test_v051_service_guards as service_tests
import run_agent_tests as ledger_tests


class RoundHistoryTests(unittest.TestCase):
    def test_four_real_scene_preflights_keep_one_history_and_allow_corrected_candidate(self):
        # Historical canonical input fixture, current service/validator/ledger.
        # This tests observation accounting, not a new-authoring quality claim.
        fixture = migration_tests.V055QualityAndMigrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        project = fixture._project("four-preflight-observations")
        order, session = fixture._begin(project, operation="storyboard_create", label="same-order")
        results = []
        for number in range(1, 5):
            draft = valid_storyboard()
            if number < 4:
                draft["shots"][0]["环境音效"] += f"；BGM渐入，错误样例{number}"
            result = fixture.service.preflight_storyboard_draft(fixture.namespace,
                project_id=project, work_order_id=order, storyboard=draft,
                scope_kind="scene", scope_id="EP01-SC01", request_session_id=session)
            results.append(result)
        self.assertEqual([r.status for r in results], [service_module.DRAFT_PREFLIGHT_FAIL] * 3
                         + [service_module.DRAFT_PREFLIGHT_PASS])
        ledger = fixture.service.store.load_ledger(fixture.namespace, project)
        events = [e for e in ledger["events"] if e["task_type"] == "production_draft_preflight"]
        self.assertEqual([e["audit_round"] for e in events], [1, 2, 3, 4])
        self.assertEqual(len({e["audit_cycle_id"] for e in events}), 1)
        self.assertEqual(events[-1]["result"], "pass")
        self.assertIsNone(ledger["policy"]["max_auto_repair_rounds"])
        self.assertFalse(results[-1].data["artifact_created"])
        self.assertFalse(results[-1].data["handoff_created"])
        self.assertEqual(len(fixture.service.store.list_project_records(fixture.namespace, project,
                         kind="draft_observations")), 4)

    def test_four_real_independent_audit_records_preserve_lineage_and_reopen(self):
        fixture = service_tests.V051ServiceGuardTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        f = fixture._passed_audit_fixture("four-independent-records")
        for number in range(2, 5):
            submission = copy.deepcopy(f["clean_semantic"])
            submission["layers"][0]["summary"] += f"｜合成独立复核{number}，不计真实内容准确率。"
            result = fixture.service.audit_storyboard(fixture.namespace,
                project_id=f["project_id"], artifact_id=f["artifact_id"], audit_cycle_id=None,
                work_order_id=f["audit_id"], handoff_artifact_id=f["root_handoff_id"],
                force_machine_recheck=True, semantic_audit=submission,
                request_session_id=f["audit_session"])
            self.assertEqual(result.status, "pass", result.data)
        ledger = fixture.service.store.load_ledger(fixture.namespace, f["project_id"])
        events = [e for e in ledger["events"] if e.get("audit_class") == "independent_audit"]
        self.assertEqual([e["audit_round"] for e in events], [1, 2, 3, 4])
        self.assertEqual(len({e["handoff_id"] for e in events}), 4)
        self.assertEqual(len({e["audit_cycle_id"] for e in events}), 1)
        self.assertEqual(service_module.storyboard_agent.validate_ledger(ledger), [])

    def test_root_cause_advice_and_positive_round_schema_are_retained(self):
        ledger_tests.test_round_count_preserves_observations()
        ledger_tests.test_second_round_root_cause_and_candidate()
        ledger_tests.test_third_round_root_cause_review()
        fourth = ledger_tests.event("EV-round-four-schema", 4, "residual")
        self.assertEqual(list(ledger_tests.SEALED_LEGACY_EVENT_SCHEMA_VALIDATOR.iter_errors(fourth)), [])

    def test_historical_policy_three_is_readable_but_does_not_stop_observations(self):
        ledger = ledger_tests.fresh()
        ledger["policy"]["max_auto_repair_rounds"] = 3
        for number in range(1, 5):
            ledger_tests.agent.ingest_event(ledger, ledger_tests.event(
                f"EV-historical-{number}", number, "initial" if number == 1 else "residual"))
        self.assertEqual(ledger["events"][-1]["audit_round"], 4)
        self.assertEqual(ledger["policy"]["max_auto_repair_rounds"], 3)
        self.assertEqual(ledger_tests.agent.validate_ledger(ledger), [])


if __name__ == "__main__":
    unittest.main()
