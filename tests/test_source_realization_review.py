"""Public synthetic source-to-speech controls; no real quality-rate claims."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "tests"), str(ROOT / "skills/shortdrama-storyboard/scripts")]
import test_narrative_service as helpers
from test_v0527_source_decisions import fact, build, fixture
from source_decisions import SourceDecisionError
from source_realizations import PROPOSAL, REVIEW, DIMENSIONS, validate_proposal, digest


def proposal():
    text = "甲问乙茶是否已经放温，乙答茶已放温，自己也缓过劲了。"
    facts = [fact("F1", text, availability="unstated", content="", occurrence="occurred"),
             fact("F2", text, subject="乙", event="E2", availability="unstated", content="", occurrence="occurred")]
    uses = [{"id": "U1", "fact_ids": ["F1"], "mode": "spoken", "wording": "adapted", "actor": "甲", "text": "茶已经放温了？"},
            {"id": "U2", "fact_ids": ["F2"], "mode": "spoken", "wording": "adapted", "actor": "乙", "text": "已经放温了，我也缓过来了。"}]
    return {"contract": PROPOSAL, "source_artifact_id": "pending", "source_sha256": "pending", "source_texts": {"DOC": text},
            "binding": {"sources": {"DOC": hashlib.sha256(text.encode()).hexdigest()}, "facts": facts, "uses": uses}}


class SourceRealizationReviewTests(unittest.IsolatedAsyncioTestCase):
    ok = helpers.NarrativeServiceTests.ok

    async def asyncSetUp(self):
        await helpers.NarrativeServiceTests.asyncSetUp(self)
        self.plan = proposal()
        text = self.plan["source_texts"]["DOC"]
        record = self.service.store.save_artifact_bytes(self.ns, self.project, text.encode(), file_name="indirect.txt",
            artifact_type="source:novel", version="v2", metadata={"source_role": "novel"})
        self.plan.update(source_artifact_id=record["artifact_id"], source_sha256=record["fingerprint"])
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _ledger):
            entry = {"artifact_id": record["artifact_id"], "fingerprint": record["fingerprint"], "source_role": "novel", "version": "v2"}
            hub["project_state"]["latest_artifacts"]["source:novel"] = entry
            hub["project_state"]["authoritative_sources"].append(entry)
        self.order = await self.begin()

    async def begin(self, *, audit_target=None, operation="storyboard_plan"):
        args = {"project_id": self.project, "operation": "narrative_audit" if audit_target else operation,
            "mode": "audit_only" if audit_target else "plan_only", "authorization": "read_only" if audit_target else "plan_only",
            "request_text": "合成控制：将已有事实转为对白，原句不改", "targets": [audit_target or "scene:S1"],
            "authoritative_source": self.plan["source_artifact_id"], "latest_version": "v2", "target_boundary": "scene:S1",
            "must_keep": [], "must_remove": [], "protected_scope": [], "expected_outputs": ["review" if audit_target else "storyboard_plan"],
            "window_key": "audit" if audit_target else "production"}
        return (await self.ok("begin_project_task", args))["work_order_id"]

    async def prepared(self):
        result = await self.ok("preflight_narrative_plan", {"project_id": self.project, "work_order_id": self.order, "plan": self.plan})
        self.assertEqual(result["status"], "plan_checked_pending_review", result)
        self.pid, self.sha = result["data"]["plan_id"], result["data"]["plan_sha256"]
        self.audit = await self.begin(audit_target=self.pid)

    def review_input(self):
        span = self.plan["binding"]["facts"][0]["supports"][0]
        return {"contract": REVIEW, "target_sha256": self.sha,
            "checks": [{"use_id": u["id"], "dimension": dim, "status": "pass", "evidence": [span],
                        "reason": "合成控制：片段明确说话人、茶温与恢复事实，未改变原有台词。"}
                       for u in self.plan["binding"]["uses"] for dim in DIMENSIONS]}

    async def reviewed(self):
        await self.prepared()
        result = await self.review(self.review_input())
        self.assertTrue(result["data"]["clean"])
        self.receipt = result["data"]["dialogue_realization"]
        return result

    async def review(self, review):
        return await self.ok("review_narrative_artifact", {"project_id": self.project, "work_order_id": self.audit,
                            "target_id": self.pid, "review": review})

    def graph(self):
        binding = self.plan["binding"]
        return {"contract": "source-facts-v1", "sources": binding["sources"], "facts": binding["facts"], "dialogue_realizations": [self.receipt]}

    def compile(self, graph=None, uses=None):
        return build(graph or self.graph(), {"contract": "source-decisions-v1", "uses": uses or self.plan["binding"]["uses"], "written_objects": []}, self.plan["source_texts"])

    async def test_real_mcp_review_allows_only_exact_realization_and_keeps_semantics_pending(self):
        await self.reviewed()
        self.service._verify_source_realization_graph(self.ns, self.project, self.graph())
        compiled = self.compile().to_dict()
        self.assertEqual(compiled["uses_by_id"]["U1"]["text"], "茶已经放温了？")
        self.assertEqual(compiled["semantic_status"], "pending_independent_review")
        self.assertFalse(compiled["release_eligible"])
        self.assertIsNone(compiled["semantic_accuracy_percent"])
        for change in ({"text": "茶还是烫的？"}, {"actor": "乙"}, {"fact_ids": ["F2"]}):
            with self.subTest(change=change), self.assertRaises(SourceDecisionError):
                uses = copy.deepcopy(self.plan["binding"]["uses"]); uses[0].update(change)
                self.compile(uses=uses)

    async def test_unreviewed_self_review_unknown_and_missing_checks_do_not_authorize(self):
        with self.assertRaises(SourceDecisionError):
            g = {"contract": "source-facts-v1", **{k: self.plan["binding"][k] for k in ("sources", "facts")}}
            self.compile(graph=g)
        await self.prepared()
        with self.assertRaisesRegex(ValueError, "narrative_authority_required"):
            self.service.review_narrative_artifact(self.ns, project_id=self.project, work_order_id=self.order,
                target_id=self.pid, review=self.review_input())
        r = self.review_input(); r["checks"].pop()
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.service.review_narrative_artifact(self.ns, project_id=self.project, work_order_id=self.audit, target_id=self.pid, review=r)
        r = self.review_input(); r["checks"][1]["status"] = "unknown"
        result = await self.review(r)
        self.assertFalse(result["data"]["clean"]); self.assertIsNone(result["data"]["dialogue_realization"])

    async def test_forged_receipt_and_later_failure_block(self):
        await self.reviewed()
        g = self.graph(); g["dialogue_realizations"] = copy.deepcopy(g["dialogue_realizations"])
        g["dialogue_realizations"][0]["review_id"] = "NAR-forged"
        with self.assertRaisesRegex(ValueError, "receipt_mismatch"):
            self.service._verify_source_realization_graph(self.ns, self.project, g)
        r = self.review_input(); r.update(supersedes_review_id=self.receipt["review_id"], change_reason="合成反例：复审发现说话人不确定")
        r["checks"][1]["status"] = "fail"
        await self.review(r)
        with self.assertRaisesRegex(ValueError, "independent_review_required"):
            self.service._verify_source_realization_graph(self.ns, self.project, self.graph())

    async def test_new_proposal_and_checker_mutation_invalidate_approval(self):
        await self.reviewed()
        code = self.service._service_code_fingerprint
        self.service._service_code_fingerprint = "changed"
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service._verify_source_realization_graph(self.ns, self.project, self.graph())
        self.service._service_code_fingerprint = code
        await self.ok("preflight_narrative_plan", {"project_id": self.project, "work_order_id": self.order, "plan": self.plan})
        with self.assertRaisesRegex(ValueError, "stale"):
            self.service._verify_source_realization_graph(self.ns, self.project, self.graph())

    async def test_source_bytes_and_current_authoritative_version_are_rechecked(self):
        await self.reviewed()
        record, path = self.service.store.get_artifact(self.ns, self.project, self.plan["source_artifact_id"])
        original = path.read_bytes()
        try:
            path.write_bytes(original + "改变来源".encode())
            with self.assertRaises(ValueError):
                self.service._verify_source_realization_graph(self.ns, self.project, self.graph())
        finally:
            path.write_bytes(original)
        self.service._verify_source_realization_graph(self.ns, self.project, self.graph())
        with self.service.store.mutate_project(self.ns, self.project) as (hub, _ledger):
            hub["project_state"]["latest_artifacts"]["source:novel"]["artifact_id"] = "ART-new-version"
        with self.assertRaisesRegex(ValueError, "stale_or_not_authoritative"):
            self.service._verify_source_realization_graph(self.ns, self.project, self.graph())

    async def test_direct_source_words_cannot_use_indirect_approval(self):
        g, d, texts = fixture()
        p = {**self.plan, "source_texts": texts, "binding": {"sources": g["sources"], "facts": g["facts"], "uses": d["uses"]}}
        p["binding"]["uses"][0].update(wording="adapted", text="明天再说。")
        with self.assertRaisesRegex(ValueError, "protected_or_nonactual"):
            validate_proposal(p, texts["DOC"])

    async def test_actual_render_replay_and_artifact_read_recheck_live_authority(self):
        await self.reviewed()
        from test_v0527_production_decisions import production_fixture, action, voice_spec
        from production_decisions import lower_production_decisions
        from production_derivation import attach_derivation, inspect_derivation
        from author_storyboard import _render_decisions
        data = production_fixture()
        # Explicit synthetic actor pose; this is a renderer/security control,
        # not a production candidate or an independent quality assessment.
        data["initial_world"]["actors"]["乙"] = copy.deepcopy(data["initial_world"]["actors"]["甲"])
        data["initial_world"]["actors"]["乙"]["position_xyz"] = [1, 0, 0]
        data["shots"].append(copy.deepcopy(data["shots"][0]))
        data["shots"][1].update(shot_id="EP90-002", shot_uid="SYNTHETIC-UID-002", transition={"relation": "continuous", "basis": "合成对话继续"})
        for index, row in enumerate(data["shots"]):
            voice = voice_spec(); voice.update(emphasis="“放温”。", speech_act="询问茶温。" if index == 0 else "回答茶温与恢复情况。")
            row.update(ticks=[0, 10, 30, 40], source_unit_ids=["DOC"],
                       actions=[action("speak", id=f"speak{index}", start=0, end=40, source_use_id=f"U{index+1}", voice=voice)])
            row["editorial"]["原文支撑句"] = self.plan["source_texts"]["DOC"]
        evidence = data["project"]["prevention_evidence"]
        evidence["source_ledger"] = [{"source_id": "DOC", "source_text": self.plan["source_texts"]["DOC"]}]
        evidence["source_decisions"] = {"contract": "source-decisions-v1", "uses": self.plan["binding"]["uses"], "written_objects": []}
        compiled = self.compile()
        lowered = lower_production_decisions(data, source_compilation=compiled)
        from production_decisions import ProductionDecisionError
        reversed_dialogue = copy.deepcopy(data)
        reversed_dialogue["shots"][0]["actions"][0]["source_use_id"] = "U2"
        reversed_dialogue["shots"][1]["actions"][0]["source_use_id"] = "U1"
        with self.assertRaisesRegex(ProductionDecisionError, "approved order"):
            lower_production_decisions(reversed_dialogue, source_compilation=compiled)
        candidate = attach_derivation(_render_decisions(lowered["typed_decisions"]), data,
            json.dumps(self.graph(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
            production=lowered, source_compilation=compiled)
        proof = inspect_derivation(candidate)
        self.assertTrue(proof["valid"], proof["faults"])
        self.service._verify_storyboard_source_realizations(self.ns, self.project, candidate)
        self.assertIn("茶已经放温了？", candidate["shots"][0]["画面内容"])
        saved = self.service.store.save_artifact_json(self.ns, self.project, candidate,
            file_name="synthetic-canonical.json", artifact_type="storyboard_json", version="v1")
        self.service._storyboard_from_artifact(self.ns, self.project, saved["artifact_id"])
        r = self.review_input(); r.update(supersedes_review_id=self.receipt["review_id"], change_reason="合成控制：撤回说话人判断")
        r["checks"][1]["status"] = "unknown"
        await self.review(r)
        with self.assertRaisesRegex(ValueError, "independent_review_required"):
            self.service._storyboard_from_artifact(self.ns, self.project, saved["artifact_id"])


if __name__ == "__main__":
    unittest.main()
