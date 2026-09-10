from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"))
from source_decisions import (GRAPH_CONTRACT, DECISIONS_CONTRACT, SourceDecisionError,
    compile_source_decisions, validate_source_decisions, validate_source_consumption, graph_sha256)


def fact(fid, source, *, subject="甲", event="E1", channel="oral", content="明日再议。", availability="given", occurrence="future", expression="occurred", conditions=(), dependencies=(), polarity="affirmative", modality="asserted"):
    return {"id": fid, "subject": subject, "event_id": event, "polarity": polarity,
            "conditions": list(conditions), "modality": modality, "occurrence": occurrence,
            "expression_occurrence": expression, "expression_status": "asserted" if channel in {"oral","written"} else "not_applicable", "channel": channel,
            "content_availability": availability, "content_policy": "adaptable" if availability == "given" else "event_only" if availability == "unstated" else "not_applicable", "content_text": content,
            "supports": [{"source_id": "DOC", "start": 0, "end": len(source), "text": source}],
            "depends_on": list(dependencies), "required": True}


def fixture():
    source = "甲说道：明日再议。"
    graph = {"contract": GRAPH_CONTRACT, "sources": {"DOC": hashlib.sha256(source.encode()).hexdigest()},
             "facts": [fact("F1", source)]}
    decisions = {"contract": DECISIONS_CONTRACT, "uses": [{"id": "U1", "fact_ids": ["F1"], "mode": "spoken", "wording": "verbatim", "actor": "甲", "text": "明日再议。"}], "written_objects": []}
    return graph, decisions, {"DOC": source}


def build(g, d, texts):
    return compile_source_decisions(g, d, source_texts=texts, expected_graph_sha256=graph_sha256(g))


class SourceDecisionTests(unittest.TestCase):
    def rejected(self, code, g, d, texts):
        with self.assertRaises(SourceDecisionError) as cm:
            build(g, d, texts)
        self.assertIn(code, {i.code for i in cm.exception.issues})
        return cm.exception

    def test_current_speech_may_describe_future_and_remains_pending(self):
        c = build(*fixture()); output = c.to_dict()
        self.assertEqual(output["uses_by_id"]["U1"]["text"], "明日再议。")
        self.assertEqual(output["uses_by_id"]["U1"]["source_constraints"][0]["occurrence"], "future")
        self.assertEqual(output["semantic_status"], "pending_independent_review")
        self.assertIsNone(output["semantic_accuracy_percent"])
        self.assertFalse(output["release_eligible"])

    def test_cannot_change_source_speaker_or_drop_locked_words(self):
        g,d,t=fixture();d["uses"][0]["actor"]="乙";self.rejected("spoken_unprovided",g,d,t)
        g,d,t=fixture();g["facts"][0]["content_text"]="若乙同意，明日可能交付。";d["uses"][0]["text"]="明日交付。"
        self.rejected("decided_content_changed",g,d,t)

    def test_supported_adaptation_is_bound_pending_but_unreviewed_synonyms_block(self):
        g,d,t=fixture();d["uses"][0].update(wording="adapted",text=" 明日再议。")
        c=build(g,d,t);ob=c.to_dict()["review_obligations"][0]
        self.assertEqual(ob["kind"],"adapted_dialogue")
        self.assertEqual(ob["status"],"pending_independent_review")
        self.assertEqual(ob["binding"]["actual_source_use"]["text"]," 明日再议。")
        d["uses"][0]["text"]="明日再议。\n"
        self.assertNotEqual(ob["id"],build(g,d,t).to_dict()["review_obligations"][0]["id"])
        d["uses"][0]["text"]="明天我们再议。"
        self.rejected("spoken_realization_requires_review",g,d,t)
        g["facts"][0]["content_policy"]="verbatim";self.rejected("protected_content_adapted",g,d,t)
        g,d,t=fixture();g["facts"][0]["subject"]=None;self.rejected("spoken_unprovided",g,d,t)

    def test_source_file_identity_and_span_exactness(self):
        g,d,t=fixture();t["DOC"]+="改";self.rejected("source_identity",g,d,t)
        g,d,t=fixture();g["facts"][0]["supports"][0]["start"]=1;self.rejected("source_span",g,d,t)
        g,d,t=fixture();b=json.dumps(g,ensure_ascii=False,indent=2).encode()
        c=compile_source_decisions(b,d,source_texts=t,expected_graph_sha256=hashlib.sha256(b).hexdigest())
        self.assertEqual(c.graph_sha256,hashlib.sha256(b).hexdigest())
        errors=validate_source_decisions(b,d,source_texts=t,expected_graph_sha256="0"*64)
        self.assertIn("graph_identity",{e.code for e in errors})

    def test_missing_required_fact_stays_global_without_a_shot(self):
        g,d,t=fixture();g["facts"].append(fact("F2",t["DOC"],event="E2"));error=self.rejected("required_fact_unrepresented",g,d,t)
        self.assertEqual(error.global_missing_fact_ids,("F2",))
        self.assertTrue(any(x.global_issue and x.fact_ids==("F2",) for x in error.issues))

    def test_cross_fact_conditions_and_dependencies_cannot_disappear(self):
        g,d,t=fixture()
        t["DOC"]="甲说道：若乙同意，明日再议。"
        g["sources"]["DOC"]=hashlib.sha256(t["DOC"].encode()).hexdigest()
        g["facts"][0]=fact("F1",t["DOC"],content="若乙同意，明日再议。")
        d["uses"][0]["text"]="若乙同意，明日再议。"
        condition=fact("COND",t["DOC"],channel="narration",content="",availability="not_applicable",event="E-C")
        start=t["DOC"].index("若乙同意")
        condition["supports"]=[{"source_id":"DOC","start":start,"end":start+4,"text":"若乙同意"}]
        g["facts"].append(condition)
        g["facts"][0]["conditions"]=["COND"]
        self.rejected("use_dependency_missing",g,d,t)
        d["uses"][0]["fact_ids"].append("COND")
        self.assertEqual(build(g,d,t).uses[0].to_dict()["source_constraints"][0]["conditions"],["COND"])
        g["facts"][0]["depends_on"]=["ABSENT"];self.rejected("dependency_missing",g,d,t)
        g["facts"][0]["depends_on"]=["COND"];g["facts"][1]["depends_on"]=["F1"];self.rejected("dependency_cycle",g,d,t)

    def test_unknown_apology_does_not_erase_given_answer(self):
        source="甲向乙道歉时，乙回答以后不会独自离院。"
        g={"contract":GRAPH_CONTRACT,"sources":{"DOC":hashlib.sha256(source.encode()).hexdigest()},"facts":[
          fact("AP",source,event="E-AP",availability="unstated",content="",occurrence="occurred"),
          fact("ANS",source,subject="乙",event="E-ANS",content="以后不会独自离院。",polarity="negative")]}
        d={"contract":DECISIONS_CONTRACT,"uses":[
          {"id":"UAP","fact_ids":["AP"],"mode":"objective_narration","wording":"authored","actor":None,"text":"甲向乙道歉。"},
          {"id":"UA","fact_ids":["ANS"],"mode":"spoken","wording":"verbatim","actor":"乙","text":"以后不会独自离院。"}],"written_objects":[]}
        t={"DOC":source};self.assertEqual(len(build(g,d,t).uses),2)
        bad=copy.deepcopy(d);bad["uses"].pop();self.rejected("required_fact_unrepresented",g,bad,t)
        bad=copy.deepcopy(d);bad["uses"][0].update(mode="spoken",wording="adapted",actor="甲",text="我愿赔偿。");self.rejected("spoken_unprovided",g,bad,t)
        bad=copy.deepcopy(d);bad["uses"][0]["text"]="甲道歉：“我愿赔偿。”";self.rejected("unstated_quoted_words",g,bad,t)

    def test_same_event_cross_paragraph_merge_not_second_delivery(self):
        source="甲把礼盒送到外间。\n这只礼盒附有清单。"
        g={"contract":GRAPH_CONTRACT,"sources":{"DOC":hashlib.sha256(source.encode()).hexdigest()},"facts":[
          fact("F1",source,event="GIFT",channel="action",content="",availability="not_applicable",occurrence="occurred",expression="not_applicable"),
          fact("F2",source,event="GIFT",channel="action",content="",availability="not_applicable",occurrence="occurred",expression="not_applicable")]}
        split=source.index("\n")
        g["facts"][0]["supports"]=[{"source_id":"DOC","start":0,"end":split,"text":source[:split]}]
        g["facts"][1]["supports"]=[{"source_id":"DOC","start":split+1,"end":len(source),"text":source[split+1:]}]
        d={"contract":DECISIONS_CONTRACT,"uses":[{"id":"U","fact_ids":["F1","F2"],"mode":"physical_action","wording":"authored","actor":"甲","text":"甲送来附有清单的同一礼盒。"}],"written_objects":[]}
        self.assertEqual(build(g,d,{"DOC":source}).uses[0].event_ids,("GIFT",))
        d["uses"]=[{**d["uses"][0],"id":"U1","fact_ids":["F1"]},{**d["uses"][0],"id":"U2","fact_ids":["F2"]}]
        self.rejected("event_reenacted",g,d,{"DOC":source})

    def test_prior_other_actor_event_is_context_not_a_second_enactment(self):
        source="乙先离开。甲随后入室。"
        facts=[fact("ENTER",source,event="ENTER",channel="action",content="",availability="not_applicable",occurrence="occurred",expression="not_applicable",dependencies=["LEAVE"]),
               fact("LEAVE",source,subject="乙",event="LEAVE",channel="action",content="",availability="not_applicable",occurrence="occurred",expression="not_applicable")]
        g={"contract":GRAPH_CONTRACT,"sources":{"DOC":hashlib.sha256(source.encode()).hexdigest()},"facts":facts}
        d={"contract":DECISIONS_CONTRACT,"uses":[{"id":"ENTER-U","fact_ids":["ENTER","LEAVE"],"mode":"physical_action","wording":"authored","actor":"甲","text":"乙离开后，甲入室。"}],"written_objects":[]}
        c=build(g,d,{"DOC":source});self.assertEqual(c.uses[0].event_ids,("ENTER","LEAVE"))
        self.assertEqual(c.uses[0].to_dict()["source_constraints"][0]["depends_on"],["LEAVE"])

    def test_negative_possible_or_conditional_action_not_enacted_as_actual(self):
        for changes in ({"polarity":"negative"},{"modality":"possible"},{"occurrence":"future"},{"occurrence":"conditional"}):
            with self.subTest(changes=changes):
                g,d,t=fixture();g["facts"][0].update(channel="action",content_availability="not_applicable",content_policy="not_applicable",content_text="",occurrence="occurred",expression_occurrence="not_applicable")
                g["facts"][0].update(changes)
                d["uses"][0].update(mode="physical_action",wording="authored");self.rejected("nonactual_action",g,d,t)

    def paper(self, current=True):
        g,d,t=fixture();g["facts"][0].update(channel="written",occurrence="conditional",expression_occurrence="current" if current else "future",content_text="若条件满足，明日交付。")
        d["uses"][0].update(mode="current_written" if current else "write",actor=None,text="若条件满足，明日交付。")
        source = "纸面现在写有“若条件满足，明日交付。”" if current else "甲明日将在纸面写上“若条件满足，明日交付。”"
        t["DOC"] = source
        g["sources"]["DOC"] = hashlib.sha256(source.encode()).hexdigest()
        g["facts"][0]["supports"] = [{"source_id":"DOC","start":0,"end":len(source),"text":source}]
        d["written_objects"]=[{"id":"P1","use_id":"U1","carrier_id":"登记纸","at":"frame0" if current else "after:U1","state":"current" if current else "planned"}]
        return g,d,t

    def test_current_paper_may_contain_future_condition_and_renders_exact_text(self):
        c=build(*self.paper());p=c.written_objects[0]
        self.assertEqual(p.text,"若条件满足，明日交付。")
        self.assertIn(p.text,p.render_static())
        self.assertEqual(c.to_dict()["written_objects_by_id"]["P1"]["text"],p.text)

    def test_planned_write_never_becomes_frame0_or_borrows_oral_source(self):
        g,d,t=self.paper(False);p=build(g,d,t).written_objects[0]
        with self.assertRaises(SourceDecisionError):p.render_static()
        d["written_objects"][0].update(state="current",at="frame0");self.rejected("paper_time",g,d,t)
        d["uses"][0]["mode"]="current_written";self.rejected("written_scope",g,d,t)
        g,d,t=self.paper();g["facts"][0]["channel"]="oral";self.rejected("written_scope",g,d,t)

    def test_expression_denial_is_not_negative_words_on_existing_paper(self):
        g,d,t=self.paper()
        g["facts"][0].update(polarity="negative",content_text="不得交付。")
        d["uses"][0]["text"]="不得交付。"
        source="纸面现在写有“不得交付。”";t["DOC"]=source;g["sources"]["DOC"]=hashlib.sha256(source.encode()).hexdigest()
        g["facts"][0]["supports"]=[{"source_id":"DOC","start":0,"end":len(source),"text":source}]
        self.assertEqual(build(g,d,t).written_objects[0].text,"不得交付。")
        for status in ("negated","uncertain"):
            with self.subTest(status=status):
                bad=copy.deepcopy(g);bad["facts"][0]["expression_status"]=status
                self.rejected("written_scope",bad,d,t)
                oral,decision,texts=fixture();oral["facts"][0]["expression_status"]=status
                self.rejected("spoken_unprovided",oral,decision,texts)

    def test_paper_has_one_carrier_instant_and_no_second_text_copy(self):
        g,d,t=self.paper();d["written_objects"][0]["text"]="漂移文字";self.rejected("shape",g,d,t)
        g,d,t=self.paper();d["written_objects"].append({**d["written_objects"][0],"id":"P2"});self.rejected("paper_state_duplicated",g,d,t)
        g,d,t=self.paper();d["written_objects"]=[];self.rejected("paper_missing",g,d,t)

    def test_paper_obligation_binds_complete_object_and_carrier_change(self):
        g,d,t=self.paper();original=build(g,d,t).to_dict()
        obligation=original["review_obligations"][0]
        self.assertEqual(obligation["binding"]["written_objects"],[original["written_objects_by_id"]["P1"]])
        bound=obligation["binding"]["written_objects"][0]
        self.assertEqual((bound["carrier_id"],bound["at"],bound["state"],bound["text"]),
                         ("登记纸","frame0","current","若条件满足，明日交付。"))
        d["written_objects"][0]["carrier_id"]="另一张登记纸"
        changed=build(g,d,t).to_dict()["review_obligations"][0]
        self.assertNotEqual(obligation["id"],changed["id"])
        self.assertEqual(obligation["binding"]["actual_source_use"],changed["binding"]["actual_source_use"])
        self.assertEqual(changed["status"],"pending_independent_review")

    def test_real_lowering_consumption_and_immutable_outputs(self):
        c=build(*self.paper());self.assertFalse(validate_source_consumption(c,use_ids=["U1"],written_object_ids=["P1"]))
        errors=validate_source_consumption(c,use_ids=[],written_object_ids=[])
        self.assertTrue(all(e.global_issue for e in errors));self.assertEqual(errors[0].fact_ids,("F1",))
        changed=c.to_dict();changed["uses_by_id"]["U1"]["text"]="篡改"
        self.assertEqual(c.uses[0].text,"若条件满足，明日交付。")
        g,d,t=fixture();d["author_pass"]=True;self.rejected("shape",g,d,t)


if __name__ == "__main__":
    unittest.main()
