"""Public source-plan controls: actual objects/results, not matching ID claims."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "skills/shortdrama-storyboard/scripts"), str(ROOT / "tests")]
import production_decisions as production
from source_action_plans import (construct_source_action_plans, source_step,
                                 migrate_source_actions, SourceActionPlanError)
from source_decisions import compile_source_decisions, graph_sha256
from author_storyboard import _render_decisions
from test_v0527_production_decisions import production_fixture, action
import test_v0527_source_decisions as source_controls
fact = source_controls.fact


def physical_source(text="甲把信纸从案左移到案右。"):
    graph = {"contract": "source-facts-v1", "sources": {"DOC": hashlib.sha256(text.encode()).hexdigest()},
             "facts": [fact("F1", text, channel="action", content="", availability="not_applicable",
                            occurrence="occurred", expression="not_applicable")]}
    uses = {"contract": "source-decisions-v1", "uses": [
        {"id": "U1", "fact_ids": ["F1"], "mode": "physical_action", "wording": "authored",
         "actor": "甲", "text": text}], "written_objects": []}
    return graph, uses, {"DOC": text}


def compile_plan(data, events, *, source=None):
    graph, uses, texts = source or physical_source()
    graph = copy.deepcopy(graph)
    graph["action_plans"] = construct_source_action_plans(initial_world=data["initial_world"], events=events)
    frozen = json.dumps(graph, ensure_ascii=False, indent=2).encode()
    return compile_source_decisions(frozen, uses, source_texts=texts,
                                    expected_graph_sha256=hashlib.sha256(frozen).hexdigest())


def move_example():
    data = production_fixture()
    events = [{"source_use_id": "U1", "steps": [
        {"step_id": "move", "kind": "move_prop", "parameters": {"prop": "信纸", "hand": "right", "to": "案右"}, "after": []}]}]
    data["shots"][0]["actions"] = [source_step(action_id="actual_move", source_use_id="U1", step_id="move", start=5, end=35)]
    return data, events


def transfer_chain():
    data = production_fixture()
    world = data["initial_world"]
    other = copy.deepcopy(world["actors"]["甲"])
    other["position_xyz"] = [0.9, 0, 0]
    other["reach_origin_xyz"] = [0.9, 0, 1.2]
    world["actors"]["乙"] = other
    world["points"]["会面点"] = {"scene_id": "ROOM", "position_xyz": [0.5, 0, 0]}
    world["props"]["信纸"]["anchor"] = {"actor": "甲", "hand": "right"}
    events = [{"source_use_id": "U1", "steps": [
        {"step_id": "approach", "kind": "move_actor", "parameters": {"to": "会面点"}, "after": []},
        {"step_id": "handover", "kind": "transfer", "parameters": {"prop": "信纸", "to_actor": "乙", "to_hand": "left"},
         "after": ["approach"]}]}]
    data["shots"][0]["actions"] = [source_step(action_id="walk", source_use_id="U1", step_id="approach", start=5, end=35)]
    second = copy.deepcopy(data["shots"][0])
    second.update(shot_id="EP90-002", shot_uid="SOURCE-HANDOVER-002",
                  transition={"relation": "continuous", "basis": "同一书房连续交接。"})
    second["actions"] = [source_step(action_id="give", source_use_id="U1", step_id="handover", start=10, end=30)]
    data["shots"].append(second)
    return data, events


class SourceActionPlanTests(unittest.TestCase):
    def test_actual_move_is_constructed_once_and_reaches_real_renderer(self):
        data, events = move_example()
        before = copy.deepcopy(data)
        source = compile_plan(data, events)
        result = production.lower_production_decisions(data, source_compilation=source)
        canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(data, before)
        self.assertIn("信纸", canonical["shots"][0]["画面内容"])
        self.assertEqual(result["derivation"]["final_world"]["props"]["信纸"]["anchor"], {"point": "案右"})
        report = result["derivation"]["source_action_execution"]
        self.assertEqual(len(report["completed_steps"]), 1)
        self.assertEqual(report["completed_steps"][0]["effect_occurrences"], 1)
        self.assertEqual(report["completed_steps"][0]["observed_effect"]["prop_anchor"], {"point": "案右"})
        self.assertFalse(report["source_entailment_proved"])
        self.assertEqual(report["semantic_status"], "pending_independent_review")
        self.assertEqual(result["derivation"]["input_sha256"], production._hash(before))

    def test_one_source_event_move_then_transfer_across_shots_is_legal(self):
        data, events = transfer_chain()
        source = compile_plan(data, events, source=physical_source("甲走近乙，把信纸交到乙手中。"))
        result = production.lower_production_decisions(data, source_compilation=source)
        canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(len(canonical["shots"]), 2)
        self.assertEqual(result["derivation"]["final_world"]["props"]["信纸"]["anchor"], {"actor": "乙", "hand": "left"})
        steps = result["derivation"]["source_action_execution"]["completed_steps"]
        self.assertEqual([step["step_id"] for step in steps], ["approach", "handover"])
        self.assertEqual({step["source_event_id"] for step in steps}, {"E1"})
        self.assertEqual(steps[1]["action"]["after"], ["walk"])
        self.assertEqual(result["derivation"]["world_events"][0]["effect_occurrences"], 1)
        self.assertEqual(result["typed_decisions"]["shots"][0]["end_state"], result["typed_decisions"]["shots"][1]["start_state"])

    def test_same_actor_raw_object_or_kind_override_cannot_bypass_plan(self):
        data, events = move_example()
        source = compile_plan(data, events)
        for key, value in (("prop", "木盒"), ("actor", "甲"), ("to_actor", "乙")):
            bad = copy.deepcopy(data)
            bad["shots"][0]["actions"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(production.ProductionDecisionError, "requires exactly"):
                production.lower_production_decisions(bad, source_compilation=source)
        bad = copy.deepcopy(data)
        bad["shots"][0]["actions"] = [action("hold", source_use_id="U1")]
        with self.assertRaisesRegex(production.ProductionDecisionError, "bare physical source action"):
            production.lower_production_decisions(bad, source_compilation=source)

    def test_duplicate_ref_with_new_action_id_and_omitted_step_are_rejected(self):
        data, events = move_example()
        source = compile_plan(data, events)
        second = copy.deepcopy(data["shots"][0]["actions"][0])
        second["id"] = "renamed_duplicate"
        data["shots"][0]["actions"].append(second)
        with self.assertRaisesRegex(production.ProductionDecisionError, "already scheduled"):
            production.lower_production_decisions(data, source_compilation=source)
        data["shots"][0]["actions"] = []
        with self.assertRaisesRegex(production.ProductionDecisionError, "missing from the actual"):
            production.lower_production_decisions(data, source_compilation=source)

    def test_plan_cannot_declare_second_handover_for_same_object(self):
        data, events = transfer_chain()
        duplicate = copy.deepcopy(events[0]["steps"][1])
        duplicate.update(step_id="again", after=["handover"])
        duplicate["parameters"]["to_hand"] = "right"
        events[0]["steps"].append(duplicate)
        with self.assertRaisesRegex(SourceActionPlanError, "same object twice"):
            construct_source_action_plans(initial_world=data["initial_world"], events=events)

    def test_renamed_point_coordinates_or_object_holder_in_initial_world_invalidate_plan(self):
        data, events = move_example()
        source = compile_plan(data, events)
        for change in ("point", "holder"):
            bad = copy.deepcopy(data)
            if change == "point":
                bad["initial_world"]["points"]["案右"]["position_xyz"][0] = 0.4
            else:
                bad["initial_world"]["props"]["信纸"]["anchor"] = {"actor": "甲", "hand": "left"}
            with self.subTest(change=change), self.assertRaisesRegex(production.ProductionDecisionError, "frozen plan does not match"):
                production.lower_production_decisions(bad, source_compilation=source)

    def test_actual_effect_failure_is_detected_not_signed_by_matching_action_ids(self):
        data, events = move_example()
        source = compile_plan(data, events)
        actual_apply = production._apply
        def corrupt(world, action, info, tick, path):
            actual_apply(world, action, info, tick, path)
            if action["kind"] == "move_prop" and tick == action["end"]:
                world["props"]["信纸"]["anchor"] = {"point": "案左"}
        with patch.object(production, "_apply", corrupt):
            with self.assertRaisesRegex(production.ProductionDecisionError, "actual action/object/result differs"):
                production.lower_production_decisions(data, source_compilation=source)

    def test_correct_named_result_plus_unrelated_side_effect_is_rejected(self):
        data, events = move_example()
        data["initial_world"]["props"]["木盒"] = {"anchor": {"point": "案右"}, "written_ref": None}
        source = compile_plan(data, events)
        actual_apply = production._apply
        def corrupt(world, action, info, tick, path):
            actual_apply(world, action, info, tick, path)
            if action["kind"] == "move_prop" and tick == action["end"]:
                world["props"]["木盒"]["anchor"] = {"point": "案左"}
        with patch.object(production, "_apply", corrupt):
            with self.assertRaisesRegex(production.ProductionDecisionError, "outside its declared effect footprint"):
                production.lower_production_decisions(data, source_compilation=source)

    def test_duplicate_effect_application_is_rejected_even_with_correct_final_holder(self):
        data, events = transfer_chain()
        source = compile_plan(data, events)
        actual_apply = production._apply
        def duplicate(world, action, info, tick, path):
            actual_apply(world, action, info, tick, path)
            if action["kind"] == "transfer" and tick == action["end"]:
                world["completed"].append(action["id"])
        with patch.object(production, "_apply", duplicate):
            with self.assertRaisesRegex(production.ProductionDecisionError, "occurrence is not exactly once"):
                production.lower_production_decisions(data, source_compilation=source)

    def test_explicit_legacy_migration_preserves_source_and_parameters_without_approval(self):
        data = production_fixture()
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右", source_use_id="U1")]
        graph, uses, texts = physical_source()
        source = compile_source_decisions(graph, uses, source_texts=texts, expected_graph_sha256=graph_sha256(graph))
        with self.assertRaisesRegex(production.ProductionDecisionError, "require a frozen plan"):
            production.lower_production_decisions(data, source_compilation=source)
        original = copy.deepcopy(data)
        migration = migrate_source_actions(data, source)
        self.assertEqual(data, original)
        self.assertTrue(migration["requires_new_frozen_source_graph"])
        graph["action_plans"] = migration["action_plans"]
        source = compile_source_decisions(graph, uses, source_texts=texts, expected_graph_sha256=graph_sha256(graph))
        result = production.lower_production_decisions(migration["payload"], source_compilation=source)
        self.assertEqual(result["derivation"]["final_world"]["props"]["信纸"]["anchor"], {"point": "案右"})
        self.assertEqual(migration["semantic_status"], "pending_independent_review")

    def test_null_staging_is_not_credited_as_physical_source_consumption(self):
        data, events = move_example()
        source = compile_plan(data, events)
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        with self.assertRaisesRegex(production.ProductionDecisionError, "missing from the actual"):
            production.lower_production_decisions(data, source_compilation=source)
        pure = production_fixture()
        self.assertEqual(production.lower_production_decisions(pure)["derivation"]["source_action_execution"]["completed_steps"], [])

    def test_action_plans_are_inspected_through_the_normal_production_entry(self):
        data, events = move_example()
        report = production.inspect_production_decisions(data, source_compilation=compile_plan(data, events))
        self.assertEqual(report["findings"], [])
        self.assertIn("lowered", report)
        self.assertFalse(report["lowered"]["derivation"]["commit_eligible"])

    def test_second_write_cannot_reconsume_the_same_written_event(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper(False)
        uses["written_objects"][0]["carrier_id"] = "信纸"
        uses["uses"][0]["actor"] = "甲"
        source = compile_source_decisions(graph, uses, source_texts=texts, expected_graph_sha256=graph_sha256(graph))
        data = production_fixture()
        data["initial_world"]["props"]["笔"] = {"anchor": {"actor": "甲", "hand": "right"}, "written_ref": None}
        data["shots"][0]["actions"] = [
            action("write", id="write1", paper_ref="P1", hand="right", tool="笔", source_use_id="U1"),
            action("write", id="write2", start=30, end=39, paper_ref="P1", hand="right", tool="笔", source_use_id="U1")]
        with self.assertRaisesRegex(production.ProductionDecisionError, "writing event cannot execute twice"):
            production.lower_production_decisions(data, source_compilation=source)

    def test_null_prop_edit_is_rejected_before_or_after_a_source_step(self):
        for start, end in ((1, 4), (36, 39)):
            data, events = move_example()
            data["shots"][0]["actions"].append(
                action("move_prop", id="unbound_change", start=start, end=end,
                       prop="信纸", hand="right", to="案左"))
            with self.subTest(start=start), self.assertRaisesRegex(production.ProductionDecisionError, "source-protected prop"):
                production.lower_production_decisions(data, source_compilation=compile_plan(data, events))

    def test_compiled_written_carrier_is_protected_without_a_physical_plan(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper()
        uses["written_objects"][0]["carrier_id"] = "信纸"
        source = compile_source_decisions(graph, uses, source_texts=texts, expected_graph_sha256=graph_sha256(graph))
        data = production_fixture()
        data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        with self.assertRaisesRegex(production.ProductionDecisionError, "source-protected prop"):
            production.lower_production_decisions(data, source_compilation=source)

    def test_unrelated_prop_and_null_blink_remain_legal_staging(self):
        data, events = move_example()
        data["initial_world"]["props"]["木盒"] = {"anchor": {"point": "案右"}, "written_ref": None}
        data["shots"][0]["actions"] += [
            action("blink", id="natural_blink", start=1, end=3),
            action("move_prop", id="arrange_box", start=36, end=39, prop="木盒", hand="right", to="案左")]
        result = production.lower_production_decisions(data, source_compilation=compile_plan(data, events))
        final = result["derivation"]["final_world"]
        self.assertEqual(final["props"]["信纸"]["anchor"], {"point": "案右"})
        self.assertEqual(final["props"]["木盒"]["anchor"], {"point": "案左"})
        self.assertEqual(final["completed"].count("natural_blink"), 1)
        self.assertEqual(result["derivation"]["source_action_execution"]["protected_prop_ids"], ["信纸"])

    def test_null_actor_move_may_carry_source_prop_without_changing_ownership(self):
        data, events = transfer_chain()
        data["initial_world"]["points"]["乙新站位"] = {"scene_id": "ROOM", "position_xyz": [1.1, 0, 0]}
        data["shots"][1]["actions"].append(
            action("move_actor", id="recipient_walk", actor="乙", start=31, end=39,
                   to="乙新站位", after=["give"]))
        result = production.lower_production_decisions(data, source_compilation=compile_plan(data, events))
        final = result["derivation"]["final_world"]
        self.assertEqual(final["actors"]["乙"]["position_xyz"], [1.1, 0, 0])
        self.assertEqual(final["props"]["信纸"]["anchor"], {"actor": "乙", "hand": "left"})
        self.assertIsNone(final["props"]["信纸"]["written_ref"])

    def test_null_performance_cannot_secretly_mutate_source_prop_state(self):
        data, events = move_example()
        data["shots"][0]["actions"].append(action("blink", id="blink", start=1, end=3))
        source = compile_plan(data, events)
        actual_apply = production._apply
        def corrupt(world, action, info, tick, path):
            actual_apply(world, action, info, tick, path)
            if action["kind"] == "blink" and tick == action["end"]:
                world["props"]["信纸"]["anchor"] = {"point": "案右"}
        with patch.object(production, "_apply", corrupt):
            with self.assertRaisesRegex(production.ProductionDecisionError, "null staging changed a source-protected"):
                production.lower_production_decisions(data, source_compilation=source)


if __name__ == "__main__":
    unittest.main()
