"""Real signed intake/repair transactions; synthetic public storyboards only."""
import copy
import unittest

import test_v040_full_repair as repair_support
from shortdrama_mcp import service as service_module
from shortdrama_mcp.quality import machine_issue_map
from shortdrama_mcp.service import validate_storyboard


class RepairHistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.support = repair_support.CompactMasterFullRepairTests()
        await self.support.asyncSetUp()

    async def asyncTearDown(self):
        await self.support.asyncTearDown()

    async def test_source_owned_failure_history_survives_real_clean_repair_and_reaudit(self):
        h = self.support
        project = (await h.call("create_project", {"project_name": "历史计数闭合对照"}))["project_id"]
        await h.call("get_agent_status", {"project_id": project, "full_self_test": True})
        base = h.broken_storyboard()
        # The archived counts are explicit synthetic observations. A fresh
        # eye-performance finding is independently machine read.
        old = {"round": 1, "initial_issue_count": 3, "residual_issue_count": 2,
               "introduced_issue_count": 1, "repeat_rounds": 1, "action": "local_fix"}
        base["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        base["shots"][0]["画面内容"] = base["shots"][0]["画面内容"].replace("自然短眨一次", "保持不眨眼")
        base, _ = service_module._prepare_compact_storyboard_for_commit(base)
        observed = validate_storyboard.validate_project(base)
        old["issue_repeat_counts"] = {key: 7 for key in machine_issue_map(
            base, observed["issues"], service_module._issue_shot_ids)}
        self.assertTrue(old["issue_repeat_counts"])
        old["current_check"] = {"checker_sha256": "0" * 64}
        base["prevention_evidence"]["repair_history"] = [copy.deepcopy(old)]
        source = h.service.store.save_artifact_json(h.namespace, project, base,
            file_name="外部带失败记录的基线.json", artifact_type="storyboard_json", version="external-v1")
        order = await h.begin("storyboard_audit", mode="audit_only", authorization="read_only",
                              window_key="history-intake", project_id=project)
        evidence = repair_support.failing_semantic_audit_for_artifact(
            h.service, h.settings, project, source["artifact_id"], shot_id="EP01-001",
            field="光源来源", error_code="E-TEMPLATE-008", issue_id="history-light",
            evidence="右侧窗光与该场景左侧窗光冲突。", repair_action="恢复正确左侧窗光。")
        audit = await h.call("audit_storyboard", {"project_id": project,
            "artifact_id": source["artifact_id"], "work_order_id": order, "semantic_audit": evidence})
        self.assertEqual(audit["status"], "fail_current")
        failed_certificate = h.service.store.get_project_record(h.namespace, project,
            kind="certificates", record_id=audit["data"]["certificate"]["certificate_id"])
        unavailable = copy.deepcopy(failed_certificate)
        unavailable["quality_thresholds"]["shot_pass_percent"] = None
        h.service._validate_schema_record(h.service._ledger_schema, "independent_audit_certificate", unavailable)
        repair_order = await h.begin("storyboard_repair", mode="audit_and_repair",
            authorization="explicit_execute", window_key="history-repair", project_id=project)
        candidate = repair_support.three_shot_storyboard()
        candidate["shots"][0]["shot_uid"] = base["shots"][0]["shot_uid"]
        # A submitter cannot rewrite the source-owned observation.
        candidate["prevention_evidence"]["repair_history"] = [{"forged": True}]
        failing = copy.deepcopy(candidate)
        failing["shots"][0]["光源来源"] = "右侧木格窗透入的日光"
        failing["shots"][0]["画面内容"] = failing["shots"][0]["画面内容"].replace("自然短眨一次", "保持不眨眼")
        failed = await h.call("commit_storyboard", {"project_id": project,
            "work_order_id": repair_order, "source_artifact_id": source["artifact_id"],
            "repair_package_id": audit["data"]["repair_package"]["repair_package_id"],
            "storyboard": failing, "file_name": "仍未修正的候选.xlsx"})
        self.assertEqual(failed["status"], "fail_current")
        systemic = [i for i in failed["data"]["report"]["issues"]
                    if i["error_code"] == "E-SYSTEMIC-001" and i["location"].startswith("repair_history[")]
        self.assertTrue(systemic)
        self.assertTrue(all("连续2轮" in i["evidence"] for i in systemic), systemic)
        self.assertFalse(failed["data"]["artifact_created"])
        repair_order = await h.begin("storyboard_repair", mode="audit_and_repair",
            authorization="explicit_execute", window_key="history-repair-fixed", project_id=project)
        repaired = await h.call("commit_storyboard", {"project_id": project,
            "work_order_id": repair_order, "source_artifact_id": source["artifact_id"],
            "repair_package_id": audit["data"]["repair_package"]["repair_package_id"],
            "storyboard": candidate, "file_name": "实际修复结果.xlsx"})
        self.assertEqual(repaired["status"], "production_preflight_pass")
        canonical, _ = h.service._storyboard_from_artifact(h.namespace, project, repaired["artifact_id"])
        history = canonical["prevention_evidence"]["repair_history"]
        self.assertEqual(history[0], old)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["current_check"]["current_issue_count"], 0)
        self.assertEqual(validate_storyboard.validate_project(canonical, mode="repair")["issues"], [])
        original, _ = h.service._storyboard_from_artifact(h.namespace, project, source["artifact_id"])
        self.assertEqual(original, base)
        self.assertEqual(repaired["data"]["next_action"], "independent_audit")
        final = await h.call("audit_storyboard", {"project_id": project,
            "artifact_id": repaired["artifact_id"], "work_order_id": order,
            "handoff_artifact_id": repaired["data"]["handoff"]["handoff_id"]})
        self.assertEqual(final["status"], "pass")
        passed_certificate = h.service.store.get_project_record(h.namespace, project,
            kind="certificates", record_id=final["data"]["certificate"]["certificate_id"])
        unavailable = copy.deepcopy(passed_certificate)
        unavailable["quality_thresholds"]["shot_pass_percent"] = None
        with self.assertRaisesRegex(ValueError, "shot_pass_percent"):
            h.service._validate_schema_record(h.service._ledger_schema, "independent_audit_certificate", unavailable)


if __name__ == "__main__":
    unittest.main()
