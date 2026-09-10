"""Small source-unit constructor and optional frozen-plan interface controls."""
from __future__ import annotations

import copy
import hashlib
import unittest

from test_v0527_spoken_source_preservation import spoken_fixture, source, build
from test_v0527_source_decisions import fact


class SourceRealizationInterfaceTests(unittest.TestCase):
    def test_prior_speakers_conditional_rule_is_not_forced_into_a_reply(self):
        text = "乙说道：“若门打开，我会先入室。”甲答道：“走吧。”"
        rule = fact("RULE", text, subject="乙", event="RULE-E", content="若门打开，我会先入室。", conditions=["DOOR"])
        reply = fact("REPLY", text, event="REPLY-E", content="走吧。", dependencies=["RULE"], occurrence="ongoing")
        door = fact("DOOR", text, subject=None, event="DOOR-E", channel="narration", content="", availability="not_applicable", occurrence="conditional")
        left = text.index("若门打开")
        door["supports"] = [{"source_id":"DOC", "start":left, "end":left+4, "text":"若门打开"}]
        graph = {"contract":source.GRAPH_CONTRACT, "sources":{"DOC":hashlib.sha256(text.encode()).hexdigest()},
                 "facts":[rule, reply, door]}
        uses = {"contract":source.DECISIONS_CONTRACT, "uses":[
            {"id":"UR", "fact_ids":["RULE","DOOR"], "mode":"spoken", "wording":"verbatim", "actor":"乙", "text":rule["content_text"]},
            {"id":"UA", "fact_ids":["REPLY","RULE","DOOR"], "mode":"spoken", "wording":"verbatim", "actor":"甲", "text":"走吧。"}],
            "written_objects":[]}
        output = build(graph, uses, {"DOC":text}).to_dict()
        self.assertEqual(output["uses_by_id"]["UA"]["spoken_realization"]["condition_spans"], [])
        self.assertEqual(output["uses_by_id"]["UA"]["text"], "走吧。")
        self.assertEqual(output["uses_by_id"]["UR"]["spoken_realization"]["condition_spans"][0]["fact_id"], "DOOR")
        self.assertEqual(output["semantic_status"], "pending_independent_review")

    def test_multiple_units_construct_in_source_order_and_reversal_is_rejected(self):
        g, d, t = spoken_fixture()
        first = g["facts"][0]
        second = copy.deepcopy(first)
        first["content_text"], second["content_text"] = "若下雨，", "明日可能不去。"
        second["id"] = "RESULT"
        g["facts"].insert(1, second)
        use = source.construct_source_spoken_use(g, source_texts=t, expected_graph_sha256=source.graph_sha256(g),
                    use_id="U1", fact_ids=["SAY", "RESULT", "RAIN"])
        self.assertEqual(use["text"], "若下雨，明日可能不去。")
        d["uses"] = [use]
        self.assertEqual(len(build(g, d, t).to_dict()["uses_by_id"]["U1"]["spoken_realization"]["source_units"]), 2)
        with self.assertRaises(source.SourceDecisionError) as caught:
            source.construct_source_spoken_use(g, source_texts=t, expected_graph_sha256=source.graph_sha256(g),
                        use_id="U1", fact_ids=["RESULT", "SAY", "RAIN"])
        self.assertIn("spoken_unit_order", {issue.code for issue in caught.exception.issues})

    def test_constructor_checks_frozen_graph_identity_and_explicit_typography(self):
        g, d, t = spoken_fixture("明日去吗?", condition=False)
        with self.assertRaises(source.SourceDecisionError) as caught:
            source.construct_source_spoken_use(g, source_texts=t, expected_graph_sha256="0" * 64, use_id="U1", fact_ids=["SAY"])
        self.assertIn("graph_identity", {issue.code for issue in caught.exception.issues})
        use = source.construct_source_spoken_use(g, source_texts=t, expected_graph_sha256=source.graph_sha256(g),
                    use_id="U1", fact_ids=["SAY"], wording="adapted", typography="chinese_width")
        self.assertEqual(use["text"], "明日去吗？")
        d["uses"] = [use]
        self.assertEqual(build(g, d, t).uses[0].text, "明日去吗？")

    def test_optional_action_plan_is_immutable_bound_data_not_semantic_approval(self):
        g, d, t = spoken_fixture()
        self.assertIsNone(build(g, d, t).to_dict()["action_plans"])
        plan = {"contract": "source-action-plans-v1", "initial_world_sha256": "a" * 64, "events": []}
        g["action_plans"] = plan
        compiled = build(g, d, t)
        output = compiled.to_dict()
        self.assertEqual(output["action_plans"], plan)
        output["action_plans"]["initial_world_sha256"] = "b" * 64
        self.assertEqual(compiled.to_dict()["action_plans"]["initial_world_sha256"], "a" * 64)
        g["action_plans"]["initial_world_sha256"] = "c" * 64
        self.assertNotEqual(compiled.graph_sha256, build(g, d, t).graph_sha256)
        self.assertEqual(output["semantic_status"], "pending_independent_review")
        self.assertFalse(output["release_eligible"])

    def test_invalid_optional_plan_base_shape_is_rejected(self):
        g, d, t = spoken_fixture(); g["action_plans"] = []
        with self.assertRaises(source.SourceDecisionError) as caught:
            build(g, d, t)
        self.assertIn("action_plans_shape", {issue.code for issue in caught.exception.issues})


if __name__ == "__main__":
    unittest.main()
