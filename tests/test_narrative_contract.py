"""Public synthetic declarations; review helpers are test data, not signatures."""
import copy
import hashlib
import unittest

from shortdrama_mcp import narrative_contract as nc


def make_plan():
    source = "甲是乙的父亲，甲已经知道此事。甲乙均在府内。甲受命管理院务，并可授权乙；甲已取得礼盒。"
    request = "保留原有人物与因果关系，写清场景。允许重新设定父子关系，但须同步更新全部下游引用。"
    span = {"start": 0, "end": len(source), "text": source}
    plan = {
        "contract": nc.CONTRACT, "source_artifact_id": "source-test",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "source_scope": {"start": 0, "end": len(source)},
        "source_map": [{"id": "M1", **span, "fact_ids": ["F_REL", "F_BASE"], "treatment": "retained", "authorization_id": None}],
        "authorizations": [{"id": "AUTH", "request_span": {"start": 0, "end": len(request), "text": request}, "fact_ids": ["F_REL", "F_BASE"]}],
        "goals": [{"id": "G1", "text": "保留人物与因果，清楚呈现场景。", "authorization_id": "AUTH"}],
        "characters": [{"id": "A", "name": "甲"}, {"id": "B", "name": "乙"}],
        "locations": ["HOME", "OUTSIDE"], "permissions": ["P_SCOPE", "P_GRANT", "P_ALL"], "props": ["GIFT"],
        "facts": [
            {"id": "F_REL", "subject": "B", "predicate": "father", "value": "A", "source_spans": [span], "policy": "locked", "authorization_id": None},
            {"id": "F_BASE", "subject": "A", "predicate": "prior_acquisitions", "value": "院务管理、授权与礼盒已取得", "source_spans": [span], "policy": "locked", "authorization_id": None},
        ],
        "prior_events": [{"id": "BASE", "fact_ids": ["F_REL", "F_BASE"],
                          "knowledge": [{"actor_id": "A", "fact_id": "F_REL"}, {"actor_id": "A", "fact_id": "F_BASE"}],
                          "permissions": [{"actor_id": "A", "permission_id": "P_SCOPE"}, {"actor_id": "A", "permission_id": "P_GRANT"}],
                          "ownership": [{"prop_id": "GIFT", "owner_id": "A"}]}],
        "initial_state": {
            "locations": {"A": "HOME", "B": "HOME"},
            "knowledge": {"A": [{"fact_id": "F_REL", "via_event_id": "BASE"}, {"fact_id": "F_BASE", "via_event_id": "BASE"}], "B": []},
            "permissions": {"A": [{"permission_id": "P_SCOPE", "via_event_id": "BASE"}, {"permission_id": "P_GRANT", "via_event_id": "BASE"}], "B": []},
            "ownership": {"GIFT": {"owner_id": "A", "via_event_id": "BASE"}},
        },
        "scenes": [scene("S1", 0, "HOME", ["A", "B"], [event("E1", 1, "assert_known", "A", {"fact_id": "F_REL"})])],
        "future_dependencies": [],
    }
    return plan, source, request


def event(eid, at, kind, actor, data, *, requires=None, uses_facts=None):
    return {"id": eid, "at": at, "kind": kind, "actor_id": actor, "requires": list(requires or []), "uses_facts": list(uses_facts or []), "data": data}


def scene(sid, start, location, present, events, *, depends_on=None):
    return {"id": sid, "start": start, "end": start + 10, "location_id": location, "present": present, "depends_on": list(depends_on or []), "events": events}


def make_candidate(plan, scene_ids=None):
    # Derive only declarations; this helper does not infer prose or certify it.
    base = nc._base(plan)
    trace = nc._simulate(*base)
    wanted = scene_ids if scene_ids is not None else trace["declaration_checked_scene_ids"]
    states = {row["scene_id"]: row["end_state"] for row in trace["state_trace"]}
    return {"contract": nc.CONTRACT, "plan_sha256": nc.canonical_sha256(plan), "scope_scene_ids": list(wanted),
            "scenes": [{"id": s["id"], "text": "甲在府内回想已经得知的人物关系。", "event_ids": [e["id"] for e in s["events"]], "end_state": states[s["id"]]}
                       for s in plan["scenes"] if s["id"] in wanted]}


def make_review(plan, candidate=None):
    """A synthetic reviewer submission. The service must independently identify/sign it."""
    scene_ids = candidate["scope_scene_ids"] if candidate else [s["id"] for s in plan["scenes"]]
    texts = {s["id"]: s["text"] for s in candidate["scenes"]} if candidate else {}
    return {
        "contract": nc.CONTRACT, "stage": "candidate" if candidate else "plan",
        "plan_sha256": nc.canonical_sha256(plan), "candidate_sha256": nc.canonical_sha256(candidate) if candidate else None,
        "scene_checks": [{"scene_id": sid, "rule_id": rule, "status": "consistent", "basis": "合成夹具中的来源、声明与本场逐项核对。",
                          "source_map_ids": [m["id"] for m in plan["source_map"]], "fact_ids": [f["id"] for f in plan["facts"]],
                          "candidate_spans": ([{"scene_id": sid, "start": 0, "end": len(texts[sid]), "text": texts[sid]}] if candidate else []), "issue_ids": []}
                         for sid in scene_ids for rule in nc.RULE_IDS],
        "source_to_facts": [] if candidate else [{"source_map_id": m["id"], "fact_ids": m["fact_ids"], "status": "consistent", "basis": "已对照该段全部原文与事实声明；此为公开测试数据。"} for m in plan["source_map"]],
        "facts_to_source": [] if candidate else [{"fact_id": f["id"], "source_map_ids": [m["id"] for m in plan["source_map"] if f["id"] in m["fact_ids"]], "status": "consistent", "basis": "已逐项反向对照来源；此为公开测试数据。"} for f in plan["facts"]],
        "issues": [],
    }


def make_repair(plan, previous, candidate):
    old = {s["id"]: s for s in previous["scenes"]}; new = {s["id"]: s for s in candidate["scenes"]}
    changed = [sid for sid in dict.fromkeys([*old, *new]) if old.get(sid) != new.get(sid)]
    return {"contract": nc.CONTRACT, "plan_sha256": nc.canonical_sha256(plan),
            "previous_candidate_sha256": nc.canonical_sha256(previous), "candidate_sha256": nc.canonical_sha256(candidate),
            "items": ([{"issue_id": "FIX1", "root_cause": "实际正文需按已锁定声明修正。", "changed_scene_ids": changed,
                        "affected_scene_ids": [s["id"] for s in plan["scenes"]], "resolution": "修改正文并回验关联场景。",
                        "answers": {qid: "按本次实际修改、来源与下游关系分别核验。" for qid in nc.QUESTION_IDS}}] if changed else []),
            "rechecked_scene_ids": [s["id"] for s in plan["scenes"]]}


class NarrativeContractTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.source, self.request = make_plan()

    def check(self, plan=None):
        return nc.validate_plan(plan or self.plan, self.source, self.request)

    def test_public_helpers_preserve_pending(self):
        result = self.check()
        self.assertEqual(result["declaration_checked_scene_ids"], ["S1"])
        self.assertFalse(result["release_eligible"])
        candidate = make_candidate(self.plan)
        self.assertEqual(nc.validate_candidate(self.plan, candidate)["semantic_status"], "pending_independent_review")
        for c in (None, candidate):
            self.assertTrue(nc.validate_review(self.plan, c, make_review(self.plan, c))["clean"])
            self.assertFalse(nc.validate_review(self.plan, c, make_review(self.plan, c))["release_eligible"])

    def test_raw_artifact_hash_is_not_extracted_text_hash(self):
        self.plan["source_sha256"] = "a" * 64
        self.check()  # Authority and raw-byte hash are checked by the service.
        self.plan["source_text_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "extracted text identity"):
            self.check()

    def test_source_full_coverage_and_actual_characters(self):
        for alteration in ("gap", "text", "support"):
            with self.subTest(alteration=alteration):
                plan = copy.deepcopy(self.plan)
                if alteration == "gap": plan["source_map"][0]["start"] = 1
                elif alteration == "text": plan["source_map"][0]["text"] = "错" + self.source[1:]
                else: plan["facts"][0]["source_spans"][0]["text"] = "错" + self.source[1:]
                with self.assertRaises(ValueError): self.check(plan)

    def test_authorization_must_be_actual_request(self):
        self.plan["authorizations"][0]["request_span"]["text"] = "另" + self.request[1:]
        with self.assertRaisesRegex(ValueError, "actual source characters"): self.check()

    def test_separate_identity_and_locked_bloodline(self):
        self.plan["scenes"][0]["events"] = [event("E1", 1, "assert_fact", None, {"fact_id": "F_REL", "value": "B"})]
        with self.assertRaisesRegex(ValueError, "conflicts with the currently"): self.check()

    def test_contradictory_locked_baseline_rejected(self):
        fact = copy.deepcopy(self.plan["facts"][0]); fact.update(id="F_OTHER", value="B")
        self.plan["facts"].append(fact); self.plan["source_map"][0]["fact_ids"].append("F_OTHER")
        with self.assertRaisesRegex(ValueError, "contradictory locked"): self.check()

    def test_authorized_relation_change_requires_updated_downstream(self):
        self.plan["facts"][0].update(policy="adaptable", authorization_id="AUTH")
        self.plan["scenes"][0]["events"] = [event("E1", 1, "change_fact", "A", {"fact_id": "F_REL", "value": "B", "authorization_id": "AUTH"}),
                                            event("E2", 2, "assert_fact", None, {"fact_id": "F_REL", "value": "B"}, requires=["E1"])]
        self.check()
        self.plan["scenes"][0]["events"][1]["data"]["value"] = "A"
        with self.assertRaisesRegex(ValueError, "conflicts with the currently"): self.check()

    def test_locked_relation_cannot_use_unrelated_authorization(self):
        self.plan["scenes"][0]["events"] = [event("E1", 1, "change_fact", "A", {"fact_id": "F_REL", "value": "B", "authorization_id": "AUTH"})]
        with self.assertRaisesRegex(ValueError, "locked fact"): self.check()

    def test_knowledge_requires_actual_acquisition(self):
        self.plan["scenes"][0]["events"] = [event("E1", 1, "assert_known", "B", {"fact_id": "F_REL"})]
        with self.assertRaisesRegex(ValueError, "without acquiring"): self.check()
        self.plan["scenes"][0]["events"] = [event("SHOW", 1, "reveal", None, {"fact_ids": ["F_REL"]}),
                                            event("LEARN", 2, "learn", "B", {"fact_id": "F_REL", "via_event_id": "SHOW"}, requires=["SHOW"]),
                                            event("USE", 3, "assert_known", "B", {"fact_id": "F_REL"})]
        self.check()

    def test_initial_knowledge_cannot_self_declare_grant(self):
        self.plan["initial_state"]["knowledge"]["B"] = [{"fact_id": "F_REL", "via_event_id": "BASE"}]
        with self.assertRaisesRegex(ValueError, "not been acquired"): self.check()

    def test_no_second_departure_without_return(self):
        self.plan["scenes"] = [scene("S1", 0, "HOME", ["A"], [event("OUT1", 1, "move", "A", {"from_location_id": "HOME", "to_location_id": "OUTSIDE"})]),
                               scene("S2", 10, "HOME", ["A"], [event("OUT2", 11, "move", "A", {"from_location_id": "HOME", "to_location_id": "OUTSIDE"})])]
        with self.assertRaisesRegex(ValueError, "without an arrival/return"): self.check()
        self.plan["scenes"].insert(1, scene("RETURN", 10, "OUTSIDE", ["A"], [event("BACK", 11, "move", "A", {"from_location_id": "OUTSIDE", "to_location_id": "HOME"})]))
        self.plan["scenes"][-1].update(start=20, end=30)
        self.plan["scenes"][-1]["events"][0]["at"] = 21
        self.check()

    def test_actor_or_recipient_cannot_act_inside_after_departure(self):
        departure = event("OUT", 1, "move", "B", {"from_location_id": "HOME", "to_location_id": "OUTSIDE"})
        for later in (event("USE", 2, "assert_known", "B", {"fact_id": "F_REL"}),
                      event("GIVE", 2, "transfer_prop", "A", {"prop_id": "GIFT", "to_actor_id": "B"})):
            with self.subTest(kind=later["kind"]):
                plan = copy.deepcopy(self.plan); plan["scenes"][0]["events"] = [departure, later]
                with self.assertRaisesRegex(ValueError, "already left"): self.check(plan)

    def test_permission_scope_and_real_grant(self):
        self.plan["scenes"][0]["events"] = [event("USE", 1, "use_permission", "A", {"permission_id": "P_ALL"})]
        with self.assertRaisesRegex(ValueError, "not been granted"): self.check()
        self.plan["scenes"][0]["events"] = [event("GRANT", 1, "grant_permission", "A", {"permission_id": "P_SCOPE", "to_actor_id": "B", "required_permission_id": "P_GRANT"}),
                                            event("USE", 2, "use_permission", "B", {"permission_id": "P_SCOPE"}, requires=["GRANT"])]
        self.check()

    def test_gift_acquired_before_use(self):
        self.plan["scenes"][0]["events"] = [event("USE", 1, "use_prop", "B", {"prop_id": "GIFT"})]
        with self.assertRaisesRegex(ValueError, "has not been acquired"): self.check()
        self.plan["scenes"][0]["events"] = [event("GIVE", 1, "transfer_prop", "A", {"prop_id": "GIFT", "to_actor_id": "B"}),
                                            event("USE", 2, "use_prop", "B", {"prop_id": "GIFT"}, requires=["GIVE"])]
        self.check()

    def test_future_dependencies_preserved_or_explicitly_restructured(self):
        dep = {"id": "D1", "required_event_id": "GIFT_RECEIVED", "consumer": "后续乙使用礼盒。", "mode": "preserve", "authorization_id": None, "replacement_event_id": None}
        self.plan["future_dependencies"] = [dep]
        with self.assertRaisesRegex(ValueError, "no preceding gift"): self.check()
        dep.update(mode="authorized_change", authorization_id="AUTH", consumer="同时删除后续使用礼盒的情节。")
        self.check()  # Authorized scope/content still needs a real semantic review.

    def test_missing_event_dependency_not_hidden_by_unknown_fact(self):
        self.plan["facts"][0]["policy"] = "unknown"
        self.plan["scenes"][0]["events"][0]["requires"] = ["MISSING"]
        with self.assertRaisesRegex(ValueError, "missing or future prerequisite"): self.check()

    def test_unknown_blocks_only_dependent_scenes(self):
        self.plan["facts"][0]["policy"] = "unknown"
        self.plan["scenes"].append(scene("S2", 10, "HOME", ["B"], [event("OTHER", 11, "assert_fact", None, {"fact_id": "F_BASE", "value": self.plan["facts"][1]["value"]})]))
        result = self.check()
        self.assertEqual(result["blocked_scene_ids"], ["S1"])
        c = make_candidate(self.plan, ["S2"])
        self.assertEqual(nc.validate_candidate(self.plan, c)["not_included_scene_ids"], ["S1"])
        self.plan["scenes"][1]["depends_on"] = ["S1"]
        self.assertEqual(self.check()["blocked_scene_ids"], ["S1", "S2"])

    def test_unknown_acquisition_blocks_later_knowledge_without_explicit_scene_dependency(self):
        self.plan["facts"][1]["policy"] = "unknown"
        self.plan["scenes"] = [scene("S1", 0, "HOME", ["A", "B"], [event("SHOW", 1, "reveal", None, {"fact_ids": ["F_REL"]}, uses_facts=["F_BASE"]),
                                                                    event("LEARN", 2, "learn", "B", {"fact_id": "F_REL", "via_event_id": "SHOW"}, requires=["SHOW"])]),
                               scene("S2", 10, "HOME", ["B"], [event("USE", 11, "assert_known", "B", {"fact_id": "F_REL"})])]
        self.assertEqual(self.check()["blocked_scene_ids"], ["S1", "S2"])

    def test_candidate_text_is_retained_and_not_semantically_certified(self):
        c = make_candidate(self.plan)
        c["scenes"][0]["text"] = '  完整正文\n“这句是否忠实，仍需独审。”  '
        result = nc.validate_candidate(self.plan, c)
        self.assertEqual(result["normalized"]["scenes"][0]["text"], c["scenes"][0]["text"])
        self.assertFalse(result["release_eligible"])
        c["scenes"][0]["end_state"]["locations"]["A"] = "OUTSIDE"
        with self.assertRaisesRegex(ValueError, "state contradicts"): nc.validate_candidate(self.plan, c)

    def test_candidate_missing_event_or_wrong_plan_rejected(self):
        for key in ("event_ids", "plan_sha256"):
            c = make_candidate(self.plan)
            if key == "event_ids": c["scenes"][0][key] = []
            else: c[key] = "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError): nc.validate_candidate(self.plan, c)

    def test_review_eight_rules_and_both_source_directions(self):
        for key in ("scene_checks", "source_to_facts", "facts_to_source"):
            r = make_review(self.plan); r[key].pop()
            with self.subTest(key=key), self.assertRaises(ValueError): nc.validate_review(self.plan, None, r)

    def test_candidate_review_must_cite_actual_prose(self):
        c = make_candidate(self.plan); r = make_review(self.plan, c)
        r["scene_checks"][0]["candidate_spans"][0]["text"] = "伪造的引用"
        with self.assertRaisesRegex(ValueError, "actual source characters"): nc.validate_review(self.plan, c, r)

    def test_review_unknown_source_localizes_and_global_unmapped_is_explicit(self):
        self.plan["scenes"].append(scene("S2", 10, "HOME", ["B"], [event("OTHER", 11, "assert_fact", None, {"fact_id": "F_BASE", "value": self.plan["facts"][1]["value"]})]))
        r = make_review(self.plan); r["facts_to_source"][0]["status"] = "unknown"
        checked = nc.validate_review(self.plan, None, r)
        self.assertEqual(checked["blocked_scene_ids"], ["S1"])
        self.assertEqual(checked["clean_scene_ids"], ["S2"])
        # Source context without mapped facts cannot localize a discovered omission.
        old = self.plan["source_map"][0]
        left, right = copy.deepcopy(old), copy.deepcopy(old)
        left.update(end=1, text=self.source[:1])
        right.update(id="M2", start=1, text=self.source[1:], fact_ids=[], treatment="context")
        self.plan["source_map"] = [left, right]
        r = make_review(self.plan); r["source_to_facts"][1]["status"] = "unknown"
        checked = nc.validate_review(self.plan, None, r)
        self.assertEqual(checked["blocked_scene_ids"], ["S1", "S2"])
        self.assertTrue(checked["global_source_uncertainty"])

    def add_issue(self, review, *, kind="hard", scene_id="S1", suggestion=None):
        review["issues"].append({"id": "I1", "kind": kind, "rule_id": "causality", "location": {"scene_id": scene_id, "event_id": None, "source_map_id": "M1", "candidate_span": None},
                                 "basis": "对照本场声明与原文。", "conflict": "本场因果或表述存在待核问题。", "impact": "可能影响后续关联场景。", "suggestion": suggestion})
        cell = next(x for x in review["scene_checks"] if x["scene_id"] == scene_id and x["rule_id"] == "causality")
        cell.update(status="issue", issue_ids=["I1"])

    def test_hard_review_issue_propagates_knowledge_dependencies(self):
        self.plan["scenes"] = [scene("S1", 0, "HOME", ["A", "B"], [event("SHOW", 1, "reveal", None, {"fact_ids": ["F_REL"]}),
                                                                    event("LEARN", 2, "learn", "B", {"fact_id": "F_REL", "via_event_id": "SHOW"}, requires=["SHOW"])]),
                               scene("S2", 10, "HOME", ["B"], [event("USE", 11, "assert_known", "B", {"fact_id": "F_REL"})])]
        r = make_review(self.plan); self.add_issue(r)
        self.assertEqual(nc.validate_review(self.plan, None, r)["blocked_scene_ids"], ["S1", "S2"])

    def test_style_and_future_are_distinct_from_hard(self):
        for kind in ("style", "future", "hard", "unknown"):
            r = make_review(self.plan); self.add_issue(r, kind=kind)
            with self.subTest(kind=kind):
                self.assertEqual(nc.validate_review(self.plan, None, r)["clean"], kind in {"style", "future"})

    def test_suggestion_requires_four_specific_questions(self):
        suggestion = {"change": "调整本场表达。", "answers": {q: "逐项分析本次建议。" for q in nc.QUESTION_IDS}}
        r = make_review(self.plan); self.add_issue(r, suggestion=suggestion)
        nc.validate_review(self.plan, None, r)
        suggestion["answers"].pop(nc.QUESTION_IDS[0])
        with self.assertRaisesRegex(ValueError, "object keys"): nc.validate_review(self.plan, None, r)

    def test_repair_binds_actual_versions_and_changed_scope(self):
        old = make_candidate(self.plan); new = copy.deepcopy(old)
        new["scenes"][0]["text"] += "甲没有改变已经明确的人物关系。"
        repair = make_repair(self.plan, old, new)
        checked = nc.validate_repair(self.plan, old, new, repair)
        self.assertEqual(checked["changed_scene_ids"], ["S1"])
        self.assertFalse(checked["release_eligible"])
        repair["previous_candidate_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "actual preceding"): nc.validate_repair(self.plan, old, new, repair)

    def test_repair_requires_actual_downstream_recheck(self):
        self.plan["scenes"].append(scene("S2", 10, "HOME", ["A"], [event("OTHER", 11, "assert_known", "A", {"fact_id": "F_REL"})], depends_on=["S1"]))
        old = make_candidate(self.plan); new = copy.deepcopy(old); new["scenes"][0]["text"] += "重述来源。"
        repair = make_repair(self.plan, old, new)
        repair["items"][0]["affected_scene_ids"] = ["S1"]
        with self.assertRaisesRegex(ValueError, "downstream dependencies"): nc.validate_repair(self.plan, old, new, repair)
        repair["items"][0]["affected_scene_ids"] = ["S1", "S2"]; repair["rechecked_scene_ids"] = ["S1"]
        with self.assertRaisesRegex(ValueError, "no declared recheck"): nc.validate_repair(self.plan, old, new, repair)

    def test_no_repair_count_limit_or_unreported_changes(self):
        old = make_candidate(self.plan); new = copy.deepcopy(old); new["scenes"][0]["text"] += "修订。"
        repair = make_repair(self.plan, old, new); repair["items"] = []
        with self.assertRaisesRegex(ValueError, "every actual changed"): nc.validate_repair(self.plan, old, new, repair)


if __name__ == "__main__":
    unittest.main()
