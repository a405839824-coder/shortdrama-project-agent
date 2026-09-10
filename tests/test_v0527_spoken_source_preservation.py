"""Frozen synthetic controls for supported spoken realization; no model calls."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"))
import source_decisions as source
from test_v0527_source_decisions import fact, build


def spoken_fixture(words="若下雨，明日可能不去。", *, condition=True):
    text = "甲说道：“" + words + "”"
    start = text.index(words)
    oral = fact("SAY", text, content=words, conditions=["RAIN"] if condition else (),
                polarity="negative", modality="possible")
    oral["supports"] = [{"source_id": "DOC", "start": start, "end": start + len(words), "text": words}]
    facts = [oral]
    if condition:
        clause = "若下雨"
        offset = text.index(clause)
        rain = fact("RAIN", text, event="WEATHER", channel="narration", content="", availability="not_applicable")
        rain["supports"] = [{"source_id": "DOC", "start": offset, "end": offset + len(clause), "text": clause}]
        facts.append(rain)
    graph = {"contract": source.GRAPH_CONTRACT, "sources": {"DOC": hashlib.sha256(text.encode()).hexdigest()}, "facts": facts}
    decisions = {"contract": source.DECISIONS_CONTRACT, "uses": [{"id": "U1", "fact_ids": [f["id"] for f in facts],
                  "mode": "spoken", "wording": "adapted", "actor": "甲", "text": words}], "written_objects": []}
    return graph, decisions, {"DOC": text}


class SpokenSourcePreservationTests(unittest.TestCase):
    def reject(self, fixture, code):
        with self.assertRaises(source.SourceDecisionError) as caught:
            build(*fixture)
        self.assertIn(code, {issue.code for issue in caught.exception.issues})

    def test_condition_ids_cannot_license_dropped_condition(self):
        g, d, t = spoken_fixture(); d["uses"][0]["text"] = "明日可能不去。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_negative_cannot_become_affirmative(self):
        g, d, t = spoken_fixture(); d["uses"][0]["text"] = "若下雨，明日可能去。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_possible_cannot_become_certain(self):
        g, d, t = spoken_fixture(); d["uses"][0]["text"] = "若下雨，明日一定不去。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_time_cannot_change(self):
        g, d, t = spoken_fixture(); d["uses"][0]["text"] = "若下雨，今日可能不去。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_given_content_must_come_from_real_support(self):
        g, d, t = spoken_fixture(); g["facts"][0]["content_text"] = d["uses"][0]["text"] = "若下雨，明日一定不去。"
        self.reject((g, d, t), "spoken_content_not_source_literal")

    def test_literal_substring_cannot_erase_retained_condition_span(self):
        g, d, t = spoken_fixture(); g["facts"][0]["content_text"] = d["uses"][0]["text"] = "明日可能不去。"
        self.reject((g, d, t), "spoken_condition_unrealized")

    def test_exact_source_is_constructible_and_bound_pending(self):
        g, d, t = spoken_fixture()
        use = source.construct_source_spoken_use(g, source_texts=t, expected_graph_sha256=source.graph_sha256(g),
                  use_id="U1", fact_ids=["SAY", "RAIN"])
        d["uses"] = [use]
        output = build(g, d, t).to_dict()
        self.assertEqual(use["text"], g["facts"][0]["content_text"])
        self.assertEqual(use["actor"], "甲")
        self.assertEqual(output["semantic_status"], "pending_independent_review")
        binding = output["uses_by_id"]["U1"]["spoken_realization"]
        self.assertEqual(binding["source_units"][0]["source_span"]["text"], use["text"])
        self.assertEqual(binding["condition_spans"][0]["fact_id"], "RAIN")
        self.assertEqual(output["review_obligations"][0]["binding"]["actual_source_use"]["spoken_realization"], binding)

    def test_safe_width_and_boundary_space_changes_preserve_content(self):
        g, d, t = spoken_fixture(); d["uses"][0]["text"] = "  若下雨,明日可能不去。\n"
        use = build(g, d, t).to_dict()["uses_by_id"]["U1"]
        self.assertEqual(use["spoken_realization"]["transform"], "spoken_typography_v1")
        self.assertEqual(use["text"], d["uses"][0]["text"])

    def test_question_cannot_turn_into_statement(self):
        g, d, t = spoken_fixture("明日去吗？", condition=False); d["uses"][0]["text"] = "明日去吗。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_quoted_punctuation_is_not_rewritten(self):
        g, d, t = spoken_fixture("请读出“甲,乙”。", condition=False); d["uses"][0]["text"] = "请读出“甲，乙”。"
        self.reject((g, d, t), "spoken_realization_requires_review")

    def test_protected_text_cannot_opt_into_typography(self):
        g, d, t = spoken_fixture(); g["facts"][0]["content_policy"] = "verbatim"
        d["uses"][0]["text"] = "若下雨,明日可能不去。"
        self.reject((g, d, t), "protected_content_adapted")

    def test_synonyms_need_verification_not_an_author_pass(self):
        g, d, t = spoken_fixture("明日再议。", condition=False); d["uses"][0]["text"] = "明天再商议。"
        self.reject((g, d, t), "spoken_realization_requires_review")
        d["uses"][0]["semantic_pass"] = True
        self.reject((g, d, t), "shape")


if __name__ == "__main__":
    unittest.main()
