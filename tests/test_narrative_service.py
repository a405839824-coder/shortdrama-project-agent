"""Real MCP/service boundary tests with public synthetic prose, not quality scores."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from shortdrama_mcp.app import create_mcp
from shortdrama_mcp.config import Settings
from shortdrama_mcp.narrative_service import digest
from test_narrative_contract import make_plan, make_candidate, make_review, make_repair, scene, event


class NarrativeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix="narrative-service-")
        self.addCleanup(self.temp.cleanup)
        settings = Settings(Path(self.temp.name), "http://127.0.0.1:8765", "127.0.0.1", 8765,
            "local", "none", None, None, None, ("storyboard:read", "storyboard:write"), False, 50*1024*1024)
        self.server, self.service = create_mcp(settings)
        self.ns = "local-single-user"
        self.project = (await self.ok("create_project", {"project_name": "synthetic narrative"}))["project_id"]
        self.plan, source, self.request = make_plan()
        # Imported source fixture uses real storage bytes and the active source
        # pointer, so the service has to verify and extract them on every use.
        source_record = self.service.store.save_artifact_bytes(self.ns, self.project, source.encode("utf-8"),
            file_name="synthetic.txt", artifact_type="source:novel", version="v1", metadata={"source_role": "novel"})
        self.plan["source_artifact_id"] = source_record["artifact_id"]
        self.plan["source_sha256"] = source_record["fingerprint"]
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _ledger):
            entry = {"artifact_id": source_record["artifact_id"], "fingerprint": source_record["fingerprint"],
                     "source_role": "novel", "file_name": "synthetic.txt", "version": "v1"}
            hub["project_state"]["latest_artifacts"]["source:novel"] = entry
            hub["project_state"].setdefault("authoritative_sources", []).append(entry)
        self.order = await self.begin()

    async def ok(self, tool, args):
        result = await self.server.call_tool(tool, args)
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    async def begin(self, *, audit_target=None, operation="script_create"):
        result = await self.ok("begin_project_task", {
            "project_id": self.project, "operation": "narrative_audit" if audit_target else operation,
            "mode": "audit_only" if audit_target else "execute",
            "authorization": "read_only" if audit_target else "explicit_execute",
            "request_text": self.request, "targets": [audit_target] if audit_target else ["S1"],
            "authoritative_source": self.plan["source_artifact_id"], "latest_version": "v1",
            "target_boundary": "synthetic", "must_keep": [], "must_remove": [], "protected_scope": [],
            "expected_outputs": ["review"] if audit_target else ["script"], "window_key": "audit" if audit_target else "production"})
        return result["work_order_id"]

    async def preflight(self):
        return await self.ok("preflight_narrative_plan", {"project_id": self.project,
            "work_order_id": self.order, "plan": self.plan})

    async def review(self, target_id, target_sha, candidate=None, override=None):
        audit_order = await self.begin(audit_target=target_id)
        review = make_review(self.plan, candidate)
        review["target_sha256"] = target_sha
        if override: review.update(override)
        return await self.ok("review_narrative_artifact", {"project_id": self.project,
            "work_order_id": audit_order, "target_id": target_id, "review": review})

    async def prepared(self):
        plan = await self.preflight()
        self.assertEqual(plan["status"], "plan_checked_pending_review", plan)
        self.plan_id = plan["data"]["plan_id"]
        await self.review(self.plan_id, plan["data"]["plan_sha256"])

    async def commit(self, candidate=None, **envelope):
        if candidate is None: candidate = make_candidate(self.plan)
        return await self.ok("commit_project_artifact", {
            "project_id": self.project, "work_order_id": self.order, "artifact_type": "script",
            "output_format": "text", "file_name": "synthetic.txt", "source_artifact_id": self.plan["source_artifact_id"],
            "narrative_candidate": {"plan_id": self.plan_id, "candidate": candidate, **envelope}})

    def validate(self):
        hub = self.service.store.load_hub(self.ns, self.project)
        order = next(o for o in hub["work_orders"] if o["work_order_id"] == self.order)
        return self.service.complete_validation(self.ns, project_id=self.project, work_order_id=self.order,
            gate_results={g: "pass" for g in order["required_validation_gates"]},
            residual_issue_count=0, introduced_issue_count=0, blockers=[])

    def signoff(self, decision="approve"):
        return self.service.signoff_task(self.ns, project_id=self.project, work_order_id=self.order,
                                        decision=decision, signer="synthetic authorized signer")

    async def test_complete_flow_blocks_self_pass_then_allows_exact_review_and_signoff(self):
        await self.prepared()
        candidate = make_candidate(self.plan)
        result = await self.commit(candidate)
        self.assertEqual(result["status"], "saved_pending_independent_review", result)
        with self.assertRaisesRegex(ValueError, "independent_review_required"):
            self.validate()
        await self.review(result["data"]["publication_id"], result["data"]["artifact"]["fingerprint"], candidate)
        self.assertEqual(self.validate().status, "pass")
        self.assertEqual(self.signoff().status, "archived")
        self.assertIsNone(result["data"]["manuscript_accuracy"])

    async def test_raw_first_draft_before_plan_is_retained_in_later_count(self):
        rejected = await self.ok("commit_project_artifact", {"project_id": self.project,
            "work_order_id": self.order, "artifact_type": "script", "output_format": "text",
            "file_name": "raw.txt", "content_text": "最早写出的错误正文。"})
        self.assertEqual(rejected["status"], "blocked")
        await self.prepared()
        result = await self.commit()
        self.assertEqual(result["data"]["candidate_ordinal"], 2)
        self.assertEqual(result["data"]["first_candidate_id"], rejected["data"]["attempt_id"])
        record = self.service._narrative_record(self.ns, self.project, rejected["data"]["attempt_id"])
        self.assertEqual(record["raw_submission"]["content_text"], "最早写出的错误正文。")

    async def test_plan_structure_cannot_authorize_body_without_review(self):
        result = await self.preflight(); self.plan_id = result["data"]["plan_id"]
        draft = await self.commit()
        self.assertEqual(draft["status"], "blocked")
        self.assertIn("independent_review_required", draft["data"]["error"])

    async def test_separate_producer_cannot_submit_independent_review(self):
        result = await self.preflight()
        with self.assertRaisesRegex(ValueError, "narrative_authority_required"):
            self.service.review_narrative_artifact(self.ns, project_id=self.project, work_order_id=self.order,
                target_id=result["data"]["plan_id"], review={"target_sha256": result["data"]["plan_sha256"]})

    async def test_partial_can_continue_without_fabricated_failure(self):
        self.plan["scenes"].append(scene("S2", 20, "HOME", ["A", "B"],
            [event("E2", 21, "assert_known", "A", {"fact_id": "F_REL"})], depends_on=["S1"]))
        await self.prepared()
        partial = make_candidate(self.plan, ["S1"])
        first = await self.commit(partial)
        self.assertEqual(first["status"], "saved_pending_independent_review", first)
        await self.review(first["data"]["publication_id"], first["data"]["artifact"]["fingerprint"], partial)
        with self.assertRaisesRegex(ValueError, "partial_candidate"):
            self.validate()
        full = make_candidate(self.plan)
        second = await self.commit(full, previous_attempt_id=first["data"]["attempt_id"],
                                   repair=make_repair(self.plan, partial, full))
        self.assertEqual(second["status"], "saved_pending_independent_review", second)
        await self.review(second["data"]["publication_id"], second["data"]["artifact"]["fingerprint"], full)
        self.assertEqual(self.validate().status, "pass")
        hub = self.service.store.load_hub(self.ns, self.project)
        rows = {r["deliverable_id"]: r for r in hub["deliverables"]}
        self.assertEqual(rows[first["artifact_id"]]["status"], "draft")
        self.assertEqual(rows[second["artifact_id"]]["status"], "validated")

    async def test_body_cannot_smuggle_text_outside_checked_candidate(self):
        await self.prepared()
        result = await self.ok("commit_project_artifact", {"project_id": self.project,
            "work_order_id": self.order, "artifact_type": "script", "output_format": "text",
            "file_name": "bad.txt", "content_text": "另一个没审过的故事",
            "source_artifact_id": self.plan["source_artifact_id"],
            "narrative_candidate": {"plan_id": self.plan_id, "candidate": make_candidate(self.plan)}})
        self.assertEqual(result["status"], "blocked")
        self.assertIn("body_mismatch", result["data"]["error"])

    async def test_source_byte_change_and_forged_review_marker_cannot_signoff(self):
        await self.prepared(); candidate = make_candidate(self.plan); result = await self.commit(candidate)
        await self.review(result["data"]["publication_id"], result["data"]["artifact"]["fingerprint"], candidate)
        self.validate()
        _meta, path = self.service.store.get_artifact(self.ns, self.project, self.plan["source_artifact_id"])
        path.write_text("替换了原文", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fingerprint_mismatch"):
            self.signoff()

    async def test_bad_structure_raw_does_not_deadlock_its_correction(self):
        await self.prepared(); bad = make_candidate(self.plan); bad["scenes"][0]["event_ids"] = []
        first = await self.commit(bad)
        self.assertEqual(first["status"], "blocked")
        result = await self.commit(make_candidate(self.plan))
        self.assertEqual(result["status"], "saved_pending_independent_review", result)
        self.assertEqual(result["data"]["candidate_ordinal"], 2)

    async def test_new_plan_invalidates_old_body_verdict(self):
        await self.prepared(); first = await self.commit()
        second_plan = await self.preflight()
        self.assertNotEqual(second_plan["data"]["plan_id"], self.plan_id)
        with self.assertRaisesRegex(ValueError, "failed_current"):
            self.validate()
        hub = self.service.store.load_hub(self.ns, self.project)
        self.assertEqual(next(d for d in hub["deliverables"] if d["deliverable_id"] == first["artifact_id"])["status"], "invalidated")

    async def test_adverse_reaudit_invalidates_final_pointer_and_preserves_signoff_history(self):
        await self.prepared(); candidate = make_candidate(self.plan); saved = await self.commit(candidate)
        first = await self.review(saved["data"]["publication_id"], saved["data"]["artifact"]["fingerprint"], candidate)
        self.validate(); self.signoff()
        changed = make_review(self.plan, candidate)
        changed["scene_checks"][0].update(status="issue", issue_ids=["LATE-1"])
        changed["issues"] = [{"id": "LATE-1", "kind": "hard", "rule_id": "source_fidelity",
            "location": {"scene_id": "S1", "event_id": "E1", "source_map_id": "M1", "candidate_span": None},
            "basis": "模拟独审后来发现的原文误读", "conflict": "旧审查判断遗漏了冲突", "impact": "需要重查本场",
            "suggestion": None}]
        changed.update(supersedes_review_id=first["data"]["review_id"], change_reason="旧标准下补充真实遗漏，撤回旧判断")
        result = await self.review(saved["data"]["publication_id"], saved["data"]["artifact"]["fingerprint"], candidate, changed)
        self.assertEqual(result["status"], "independent_review_unresolved")
        hub = self.service.store.load_hub(self.ns, self.project)
        self.assertNotIn("script", hub["project_state"]["latest_artifacts"])
        draft = next(d for d in hub["deliverables"] if d["deliverable_id"] == saved["artifact_id"])
        self.assertEqual(draft["status"], "invalidated")
        self.assertEqual(draft["prior_statuses"][-1]["status"], "final")
        self.assertEqual(len(hub["human_signoffs"]), 1)

    async def test_forged_marker_cannot_replace_a_signed_review(self):
        await self.prepared(); await self.commit()
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _ledger):
            order = next(o for o in hub["work_orders"] if o["work_order_id"] == self.order)
            order["verified_narrative_review_id"] = "NAR-fake"
        with self.assertRaisesRegex(ValueError, "independent_review_required"):
            self.validate()

    async def test_legacy_passed_work_order_can_reopen_without_faking_a_review(self):
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _ledger):
            order = next(o for o in hub["work_orders"] if o["work_order_id"] == self.order)
            order["status"] = "passed"; order["validation"]["status"] = "pass"
        self.assertEqual(self.signoff("reopen").status, "approved")

    async def test_new_work_order_cannot_reset_same_scope_first_candidate(self):
        await self.prepared(); candidate = make_candidate(self.plan); first = await self.commit(candidate)
        self.order = await self.begin()
        await self.prepared()
        second = await self.commit(candidate, previous_attempt_id=first["data"]["attempt_id"],
                                   repair=make_repair(self.plan, candidate, candidate))
        self.assertEqual(second["status"], "saved_pending_independent_review", second)
        self.assertEqual(second["data"]["candidate_ordinal"], 2)
        self.assertEqual(second["data"]["first_candidate_id"], first["data"]["attempt_id"])

    async def test_refusal_history_and_exact_readback(self):
        result = await self.ok("record_narrative_attempt", {"project_id": self.project,
            "work_order_id": self.order, "outcome": "incomplete", "reason": "原文资料尚不完整"})
        record = await self.ok("read_narrative_record", {"project_id": self.project, "record_id": result["data"]["record_id"]})
        value = json.loads(record["data"]["content"])
        self.assertFalse(value["complete_candidate"])
        self.assertIsNone(value["manuscript_accuracy"])

    async def test_remote_caller_can_read_current_input_contract_before_authoring(self):
        result = await self.ok("read_narrative_record", {"project_id": self.project, "record_id": "contract"})
        self.assertEqual(result["data"]["record_type"], "workflow_reference")
        self.assertIn("source_artifact_id", result["data"]["content"])
        self.assertEqual(len(result["data"]["reference_sha256"]), 64)

    async def test_nonnarrative_plan_storage_still_available(self):
        result = await self.ok("begin_project_task", {"project_id": self.project,
            "operation": "episode_plan", "mode": "plan_only", "authorization": "plan_only",
            "request_text": "仅整理分集提案", "targets": ["S1"], "authoritative_source": "synthetic",
            "latest_version": "v1", "target_boundary": "S1", "must_keep": [], "must_remove": [],
            "protected_scope": [], "expected_outputs": ["episode_plan"], "window_key": "plan"})
        artifact = await self.ok("commit_project_artifact", {"project_id": self.project,
            "work_order_id": result["work_order_id"], "artifact_type": "episode_plan", "output_format": "text",
            "file_name": "plan.txt", "content_text": "正常的未获正文生产授权的提案草稿。"})
        self.assertEqual(artifact["status"], "saved_pending_validation")


if __name__ == "__main__":
    unittest.main()
