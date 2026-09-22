"""Independent public boundary controls; failures are preserved, not re-labelled."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"
sys.path.insert(0, str(SCRIPTS))


def public_environment_metadata():
    return {"功能类型": "房内交接", "参考图锚定": "无",
            "原文依据类型": "动作叙述", "原文支撑句": "甲在房内移动。"}


def environment_world(*, posture="站立", light_state="closed", completed=()):
    return {"scenes": {"ROOM": {"light": {"name": "东纸窗", "state": light_state}}},
            "actors": {"甲": {"scene_id": "ROOM", "posture": posture}},
            "completed": list(completed)}


class EnvironmentAdversarialTests(unittest.TestCase):
    def test_supported_clear_room_preserves_explicit_light_state_and_pending_scope(self):
        from production_environment import environment_editorial, render_environment
        editorial = environment_editorial(metadata=public_environment_metadata(), sound_cues=[])
        for state, expected in (("closed", "东纸窗关闭"), ("open", "东纸窗开启")):
            with self.subTest(state=state):
                world = environment_world(light_state=state)
                fields, evidence = render_environment(editorial, initial_world=world,
                    final_world=copy.deepcopy(world), scene_id="ROOM", actions=[])
                self.assertEqual(fields["环境动态"], "无环境动态。")
                self.assertEqual(world["scenes"]["ROOM"]["light"]["state"], state)
                self.assertEqual(fields["环境音效"], "无额外环境音效。")
                self.assertEqual(evidence["semantic_status"], "pending_independent_review")

    def test_free_environment_prose_is_rejected_at_decision_boundary(self):
        from production_environment import validate_editorial, EnvironmentDecisionError
        editorial = {**public_environment_metadata(), "环境动态": "室内蒸汽持续升起。",
                     "环境音效": {"mode": "none"}}
        with self.assertRaisesRegex(EnvironmentDecisionError, "free environmental prose"):
            validate_editorial(editorial)

    def test_constructor_rejects_colliding_environment_prose_in_metadata(self):
        from production_environment import environment_editorial, EnvironmentDecisionError
        for field in ("环境动态", "环境音效"):
            with self.subTest(field=field):
                metadata = {**public_environment_metadata(), field: "额外的未建模事件不能静默删除。"}
                before = copy.deepcopy(metadata)
                with self.assertRaises(EnvironmentDecisionError):
                    environment_editorial(metadata=metadata, sound_cues=[])
                self.assertEqual(metadata, before)

    def test_bound_standing_movement_can_have_explicit_footstep_cue(self):
        from production_environment import environment_editorial, render_environment
        editorial = environment_editorial(metadata=public_environment_metadata(), sound_cues=[
            {"action_id": "walk", "sound": "footsteps", "volume": "soft"}])
        action = {"id": "walk", "kind": "move_actor", "actor": "甲", "start": 10, "end": 20}
        fields, evidence = render_environment(editorial, initial_world=environment_world(),
            final_world=environment_world(completed=["walk"]), scene_id="ROOM", actions=[action])
        self.assertIn("1.0—2.0秒", fields["环境音效"])
        self.assertIn("甲行走时的脚步声", fields["环境音效"])
        self.assertEqual(evidence["sound_bindings"][0]["action_id"], "walk")

    def test_generic_translation_of_seated_or_lying_actor_does_not_prove_walking(self):
        from production_environment import environment_editorial, render_environment, EnvironmentDecisionError
        editorial = environment_editorial(metadata=public_environment_metadata(), sound_cues=[
            {"action_id": "move", "sound": "footsteps", "volume": "soft"}])
        action = {"id": "move", "kind": "move_actor", "actor": "甲", "start": 10, "end": 20}
        for posture in ("坐姿", "躺卧"):
            with self.subTest(posture=posture), self.assertRaises(EnvironmentDecisionError):
                render_environment(editorial, initial_world=environment_world(posture=posture),
                    final_world=environment_world(posture=posture, completed=["move"]),
                    scene_id="ROOM", actions=[action])

    def test_cue_cannot_borrow_a_hold_or_uncompleted_action(self):
        from production_environment import environment_editorial, render_environment, EnvironmentDecisionError
        editorial = environment_editorial(metadata=public_environment_metadata(), sound_cues=[
            {"action_id": "event", "sound": "footsteps", "volume": "soft"}])
        for kind, completed in (("hold", ["event"]), ("move_actor", [])):
            with self.subTest(kind=kind, completed=completed), self.assertRaises(EnvironmentDecisionError):
                render_environment(editorial, initial_world=environment_world(),
                    final_world=environment_world(completed=completed), scene_id="ROOM",
                    actions=[{"id": "event", "kind": kind, "actor": "甲", "start": 10, "end": 20}])


def public_source_motion_case(*, plan_kind="move_prop", opening="closed"):
    """Own public source/world construction, independent of producer fixtures."""
    from source_decisions import compile_source_decisions, graph_sha256
    from source_action_plans import construct_source_action_plans, source_step
    source_text = "甲把铜牌从案左移到案右。"
    light = {"period": "day", "kind": "paper_window", "name": "东纸窗", "state": "closed",
             "position_xyz": [3, 0, 2], "main_tone": "neutral", "shadow_tone": "neutral", "materials": ["木案"]}
    world = {"scenes": {"ROOM": {"label": "房内", "door_window_state": {"东纸窗": opening},
             "opening_labels": {"东纸窗": "纸窗"}, "light": light, "barriers": []}},
             "actors": {"甲": {"scene_id": "ROOM", "position_xyz": [0, 0, 0], "body_yaw": 0, "head_yaw": 0,
                 "eye_height": 1.5, "eye_state": "natural_open", "posture": "站立",
                 "hands": {"left": [-0.2, 0.3, 1.0], "right": [0.2, 0.3, 1.0]}, "gaze": None,
                 "reach_origin_xyz": [0, 0, 1.2], "reach_m": 0.9}},
             "props": {"铜牌": {"anchor": {"point": "案左"}, "written_ref": None}},
             "points": {"案左": {"scene_id": "ROOM", "position_xyz": [0.2, 0.3, 1.0]},
                        "案右": {"scene_id": "ROOM", "position_xyz": [0.5, 0.3, 1.0]},
                        "取景中心": {"scene_id": "ROOM", "position_xyz": [0, 0, 1.1]}}}
    fact = {"id": "F_MOVE", "subject": "甲", "event_id": "E_MOVE", "polarity": "affirmative",
            "conditions": [], "modality": "asserted", "occurrence": "occurred",
            "expression_occurrence": "not_applicable", "expression_status": "not_applicable", "channel": "action",
            "content_availability": "not_applicable", "content_policy": "not_applicable", "content_text": "",
            "supports": [{"source_id": "P_MOVE", "start": 0, "end": len(source_text), "text": source_text}],
            "depends_on": [], "required": True}
    use = {"id": "U_MOVE", "fact_ids": ["F_MOVE"], "mode": "physical_action", "wording": "authored",
           "actor": "甲", "text": source_text}
    plan = construct_source_action_plans(initial_world=world, events=[{"source_use_id": "U_MOVE", "steps": [
        {"step_id": "S_MOVE", "kind": plan_kind, "parameters":
         {"prop": "铜牌", "hand": "right", "to": "案右"} if plan_kind == "move_prop" else {}, "after": []}]}])
    graph = {"contract": "source-facts-v1", "sources": {"P_MOVE": hashlib.sha256(source_text.encode()).hexdigest()},
             "facts": [fact], "action_plans": plan}
    compiled = compile_source_decisions(graph, {"contract": "source-decisions-v1", "uses": [use], "written_objects": []},
        source_texts={"P_MOVE": source_text}, expected_graph_sha256=graph_sha256(graph))
    row = {"shot_id": "EP90-001", "shot_uid": "PUBLIC-SOURCE-MOVE", "episode_id": "EP90", "scene_id": "ROOM",
           "continuity_id": "ROOM-C1", "ticks": [0, 10, 30, 40],
           "camera": {"position_xyz": [0, 4, 1.5], "target": "取景中心", "hfov_deg": 50, "vfov_deg": 50, "frame": "wide"},
           "actions": [source_step(action_id="move", source_use_id="U_MOVE", step_id="S_MOVE", start=10, end=20)],
           "editorial": {**public_environment_metadata(), "环境动态": {"mode": "shared_world"}, "环境音效": {"mode": "none"}},
           "source_unit_ids": ["P_MOVE"], "transition": {"relation": "initial", "basis": "房内事件开始。"}}
    payload = {"contract": "production-decisions-v1", "project": {"prevention_evidence": {"audio_workflow": {"timing": {"utterances": []}}}},
               "initial_world": world, "shots": [row]}
    return payload, compiled


class SourcePlanAdversarialTests(unittest.TestCase):
    def lower(self, payload, compiled):
        from production_decisions import lower_production_decisions
        return lower_production_decisions(payload, source_compilation=compiled)

    def test_real_source_move_and_null_blink_keep_actual_effect_and_pending_semantics(self):
        payload, compiled = public_source_motion_case()
        payload["shots"][0]["actions"].append({"id": "blink", "kind": "blink", "actor": "甲",
            "start": 32, "end": 34, "after": [], "source_use_id": None})
        result = self.lower(payload, compiled)
        self.assertEqual(result["derivation"]["final_world"]["props"]["铜牌"]["anchor"], {"point": "案右"})
        report = result["derivation"]["source_action_execution"]
        self.assertEqual(len(report["completed_steps"]), 1)
        self.assertFalse(report["source_entailment_proved"])
        self.assertEqual(report["semantic_status"], "pending_independent_review")
        self.assertFalse(result["derivation"]["commit_eligible"])

    def test_null_action_cannot_replace_required_source_step(self):
        from production_decisions import ProductionDecisionError
        payload, compiled = public_source_motion_case()
        payload["shots"][0]["actions"] = [{"id": "replacement", "kind": "move_prop", "actor": "甲",
            "start": 10, "end": 20, "after": [], "source_use_id": None, "prop": "铜牌", "hand": "right", "to": "案右"}]
        with self.assertRaisesRegex(ProductionDecisionError, "source steps are missing"):
            self.lower(payload, compiled)

    def test_second_reference_cannot_repeat_source_occurrence_under_new_action_id(self):
        from production_decisions import ProductionDecisionError
        payload, compiled = public_source_motion_case()
        duplicate = {**payload["shots"][0]["actions"][0], "id": "repeat", "start": 21, "end": 25}
        payload["shots"][0]["actions"].append(duplicate)
        with self.assertRaisesRegex(ProductionDecisionError, "already scheduled"):
            self.lower(payload, compiled)

    def test_reference_cannot_override_frozen_object_target(self):
        from production_decisions import ProductionDecisionError
        payload, compiled = public_source_motion_case()
        payload["shots"][0]["actions"][0]["to"] = "案左"
        with self.assertRaisesRegex(ProductionDecisionError, "requires exactly"):
            self.lower(payload, compiled)

    def test_unreviewed_wrong_plan_does_not_gain_source_semantic_PASS(self):
        # A producer can misinterpret "move" as "hold" in its own graph plan.
        # Structural conformance must stay pending, never become source truth.
        payload, compiled = public_source_motion_case(plan_kind="hold")
        result = self.lower(payload, compiled)
        self.assertEqual(result["derivation"]["final_world"]["props"]["铜牌"]["anchor"], {"point": "案左"})
        self.assertFalse(result["derivation"]["source_action_execution"]["source_entailment_proved"])
        self.assertIsNone(result["derivation"]["semantic_accuracy_percent"])
        self.assertEqual(compiled.to_dict()["review_obligations"][0]["status"], "pending_independent_review")
        self.assertFalse(compiled.to_dict()["release_eligible"])

    def test_null_move_after_source_completion_cannot_reverse_its_object_result(self):
        from production_decisions import ProductionDecisionError
        payload, compiled = public_source_motion_case()
        payload["shots"][0]["actions"].append({"id": "undo", "kind": "move_prop", "actor": "甲",
            "start": 22, "end": 28, "after": ["move"], "source_use_id": None,
            "prop": "铜牌", "hand": "right", "to": "案左"})
        with self.assertRaises(ProductionDecisionError):
            self.lower(payload, compiled)

    def test_door_registry_and_light_cannot_publish_opposite_window_states(self):
        from production_decisions import ProductionDecisionError
        payload, compiled = public_source_motion_case(opening="open")
        with self.assertRaisesRegex(ProductionDecisionError, "paper-window opening state"):
            self.lower(payload, compiled)


if __name__ == "__main__":
    unittest.main()
