"""Public synthetic controls for the action-owned production path."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS.parent / "skills/shortdrama-storyboard/scripts"), str(TESTS)]
from production_decisions import lower_production_decisions, inspect_production_decisions, ProductionDecisionError
from author_storyboard import _render_decisions
from staging_text_bindings import bind_fields
from performance_checks import eye_contract_faults, listener_reaction_faults
from timing_model import validate_timing
from picture_presentation import timed_picture
from test_v0527_authoring_serializer import decisions
import test_v0527_source_decisions as source_controls
source_fixture = source_controls.fixture
compile_source = source_controls.build


def production_fixture():
    project = decisions()["project"]
    # Public synthetic identities only; this is not a source/audit PASS fixture.
    actor = {"scene_id": "ROOM", "position_xyz": [0, 0, 0], "body_yaw": 0, "head_yaw": 0,
             "eye_height": 1.5, "eye_state": "natural_open", "posture": "站立",
             "hands": {"left": [-0.2, 0.3, 1.0], "right": [0.2, 0.3, 1.0]}, "gaze": None, "reach_m": 0.9,
             "reach_origin_xyz": [0, 0, 1.2]}
    light = {"period": "day", "kind": "paper_window", "name": "东纸窗", "state": "closed",
             "position_xyz": [3, 0, 2], "main_tone": "neutral", "shadow_tone": "neutral", "materials": ["纸面"]}
    world = {"scenes": {"ROOM": {"label": "书房", "door_window_state": "东纸窗关闭。", "opening_labels": {"东纸窗": "纸窗"}, "light": light, "barriers": []}},
             "actors": {"甲": actor}, "props": {"信纸": {"anchor": {"point": "案左"}, "written_ref": None}},
             "points": {"案左": {"scene_id": "ROOM", "position_xyz": [0.2, 0.3, 1.0]},
                        "案右": {"scene_id": "ROOM", "position_xyz": [0.5, 0.3, 1.0]},
                        "取景中心": {"scene_id": "ROOM", "position_xyz": [0, 0, 1.1]},
                        "落脚处": {"scene_id": "ROOM", "position_xyz": [1, 0, 0]}}}
    row = {"shot_id": "EP90-001", "shot_uid": "SYNTHETIC-UID-001", "episode_id": "EP90", "scene_id": "ROOM", "continuity_id": "ROOM-C1",
           "ticks": [0, 10, 30, 40], "camera": {"position_xyz": [0, 4, 1.5], "target": "取景中心", "hfov_deg": 50, "vfov_deg": 50, "frame": "wide"},
           "actions": [{"id": "hold1", "kind": "hold", "start": 10, "end": 30, "actor": "甲", "after": [], "source_use_id": None}],
           "editorial": {"功能类型": "观察信纸", "环境音效": {"mode": "none"}, "环境动态": {"mode": "shared_world"}, "参考图锚定": "无外部图像锚点。",
                         "原文依据类型": "动作叙述", "原文支撑句": "甲留在桌旁。"},
           "source_unit_ids": ["P01", "P02"], "transition": {"relation": "initial", "basis": "书房事件开始。"}}
    return {"contract": "production-decisions-v1", "project": project, "initial_world": world, "shots": [row]}


def action(kind, **fields):
    result = {"id": "act1", "kind": kind, "start": 10, "end": 30, "actor": "甲", "after": [], "source_use_id": None, **fields}
    if kind in {"speak", "narrate"}: result.pop("actor")
    return result


def voice_spec():
    return {"baseline": "年龄感为成年，中音区，厚薄适中，明暗中性，常态清楚。", "speech_act": "陈述延后商议的安排。", "current_expression": "平静，意在确认商议时间。",
            "volume": "近距离交谈音量。", "rhythm": "中速，逐字清楚。", "breath": "发声前自然吸气，句末轻呼气。", "pauses": "句中不停顿。",
            "emphasis": "“明日”。", "tail_direction": "落稳", "tail_execution": "句末自然收住。", "prohibited_tones": ["喊叫", "哭腔"]}


def performance_fixture():
    data = production_fixture()
    data["initial_world"]["points"]["观察点"] = {"scene_id": "ROOM", "position_xyz": [0, 2, 1.5]}
    data["initial_world"]["actors"]["甲"]["gaze"] = {"kind": "point", "id": "观察点"}
    data["initial_world"]["actors"]["甲"]["performance_state"] = {"eyelid": "slightly_tense", "breath": "resting"}
    data["shots"][0]["actions"] = [action("eyelid", id="relax", start=5, end=12, to="relaxed"),
        action("breath", id="exhale", start=20, end=26, phase="exhale"),
        action("blink", id="blink", start=32, end=34)]
    return data


class ProductionDecisionTests(unittest.TestCase):
    def lower(self, data): return lower_production_decisions(data)

    def test_turning_receiver_is_not_a_change_of_light_direction(self):
        from lighting_checks import scene_lighting_continuity_faults
        data=production_fixture()
        data['shots'][0]['actions']=[action('turn', start=0, end=10, body_yaw=180, head_yaw=180)]
        second=copy.deepcopy(data['shots'][0])
        second.update(shot_id='EP90-002',shot_uid='TURNED-RECEIVER',actions=[])
        second['transition']={'relation':'continuous','basis':'同光源同机位，人物已转身。'}
        data['shots'].append(second)
        rows=_render_decisions(self.lower(data)['typed_decisions'])['shots']
        self.assertIn('甲后脑',rows[1]['光源方向'])
        self.assertEqual(scene_lighting_continuity_faults(rows),[])
        rows[1]['光源方向']=rows[1]['光源方向'].replace('画面左侧','画面右侧')
        self.assertTrue(any('反打机位依据' in f for f in scene_lighting_continuity_faults(rows)))

    def test_face_frame_requires_actual_front_head_and_excludes_feet(self):
        data = production_fixture()
        camera = data['shots'][0]['camera']
        camera.update(position_xyz=[0, 1, 1.5], hfov_deg=18, vfov_deg=28, frame='face')
        data['initial_world']['points']['取景中心']['position_xyz'] = [0, 0, 1.45]
        canonical = _render_decisions(self.lower(data)['typed_decisions'])
        self.assertEqual(canonical['shots'][0]['镜头类型'], '面部近景')
        behind = copy.deepcopy(data)
        behind['shots'][0]['camera']['position_xyz'] = [0, -1, 1.5]
        self.rejected(behind, 'visible front-facing head')
        feet = copy.deepcopy(data)
        feet['shots'][0]['camera']['vfov_deg'] = 140
        self.rejected(feet, 'excludes foot landmarks')

    def test_three_people_staging_has_independent_clauses_in_actual_render(self):
        data = production_fixture()
        for name, x in [('乙', -.6), ('丙', .6)]:
            actor = copy.deepcopy(data['initial_world']['actors']['甲'])
            actor['position_xyz'][0] = x
            actor['reach_origin_xyz'][0] = x
            data['initial_world']['actors'][name] = actor
        before = copy.deepcopy(data)
        lowered = self.lower(data)
        canonical = _render_decisions(lowered['typed_decisions'])
        initial = canonical['shots'][0]['起始状态']
        self.assertIn('乙位于甲的画面', initial)
        self.assertIn('丙位于甲的画面', initial)
        self.assertNotIn('[[staging:', initial)
        self.assertEqual(data, before)
        self.assertFalse(lowered['derivation']['commit_eligible'])
        bad = copy.deepcopy(lowered['typed_decisions']['shots'][0])
        bad['fields']['起始状态'] = bad['fields']['起始状态'].replace(']]\n[[', ']][[')
        with self.assertRaisesRegex(Exception, 'own affirmative clause'):
            bind_fields(bad['fields'], bad['staging'])

    def rejected(self, data, message):
        with self.assertRaises(ProductionDecisionError) as ctx: self.lower(data)
        self.assertIn(message, ctx.exception.reason)

    def test_positive_hold_reaches_real_typed_renderer_and_preserves_input(self):
        data = production_fixture(); before = copy.deepcopy(data)
        result = self.lower(data); canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(data, before)
        self.assertEqual(len(canonical["shots"]), 1)
        self.assertEqual(len(result["typed_decisions"]["shots"][0]["phases"]), 3)
        self.assertEqual(canonical["prevention_evidence"]["repair_history"], [])
        self.assertFalse(result["derivation"]["commit_eligible"])
        self.assertIsNone(result["derivation"]["semantic_accuracy_percent"])

    def test_complete_declared_same_target_performance_passes_real_consumers(self):
        data = performance_fixture(); result = self.lower(data); canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(eye_contract_faults(canonical["shots"][0], duration=4), [])
        self.assertEqual(validate_timing(canonical, canonical["shots"])["faults"], [])
        self.assertNotIn("视线从观察点移向观察点", canonical["shots"][0]["画面内容"])
        self.assertIn("视线持续停在画面中部的观察点", canonical["shots"][0]["画面内容"])
        completed = result["derivation"]["final_world"]["completed"]
        self.assertEqual(completed.count("relax"), 1)
        self.assertEqual(result["derivation"]["shots"][0]["actions"][0]["phase_fragments"], ["relax-part1", "relax-part2"])

    def test_performance_state_unknown_and_cross_shot_snapshots_are_honest(self):
        data = performance_fixture(); del data["initial_world"]["actors"]["甲"]["performance_state"]
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP90-002", shot_uid="PERFORMANCE-CONTINUE", actions=[])
        second["transition"] = {"relation": "continuous", "basis": "同一地点同一时刻的后续取景。"}; data["shots"].append(second)
        result = self.lower(data); rows = result["typed_decisions"]["shots"]
        self.assertEqual(rows[0]["start_state"]["动作进度"]["表演状态"]["甲"]["eyelid"], "unknown")
        self.assertEqual(rows[0]["end_state"], rows[1]["start_state"])
        self.assertEqual(rows[1]["start_state"]["动作进度"]["表演状态"]["甲"], {"eyelid": "relaxed", "breath": "resting"})
        self.assertNotIn("由紧转松", rows[0]["phases"][0]["text"])
        old = self.lower(production_fixture())
        self.assertEqual([e["kind"] for e in old["derivation"]["world_events"]], ["hold"])

    def test_focus_adjust_reads_current_target_and_rejects_unknown(self):
        data = performance_fixture(); data["shots"][0]["actions"] = [action("focus_adjust", start=5, end=15)]
        result = self.lower(data); picture = _render_decisions(result["typed_decisions"])["shots"][0]["画面内容"]
        self.assertIn("眼球轻微进行焦点调整", picture)
        self.assertIn("视线持续停在画面中部的观察点", picture)
        self.assertEqual(result["derivation"]["final_world"]["actors"]["甲"]["gaze"]["ref"]["id"], "观察点")
        data["initial_world"]["actors"]["甲"]["gaze"] = None
        self.rejected(data, "resolved current target")
        data = performance_fixture(); data["initial_world"]["actors"]["甲"]["eye_state"] = "closed"
        data["initial_world"]["actors"]["甲"]["gaze"] = None
        data["shots"][0]["actions"] = [action("focus_adjust")]
        self.rejected(data, "open eyes")

    def test_closed_or_hidden_eyelid_effect_stays_in_world_not_visible_picture(self):
        for closed, hidden in [(True, False), (False, True)]:
            data = performance_fixture(); actor = data["initial_world"]["actors"]["甲"]
            actor["gaze"] = None
            if closed: actor["eye_state"] = "closed"
            if hidden: data["shots"][0]["camera"]["position_xyz"] = [0, -4, 1.5]
            data["shots"][0]["actions"] = [action("eyelid", to="relaxed")]
            result = self.lower(data); picture = _render_decisions(result["typed_decisions"])["shots"][0]["画面内容"]
            self.assertNotIn("眼睑放松", picture)
            self.assertEqual(result["derivation"]["final_world"]["actors"]["甲"]["performance_state"]["eyelid"], "relaxed")
            self.assertEqual(result["derivation"]["world_events"][0]["visible_projection_ids"], [])

    def test_actual_emphasis_restraint_and_complete_spoken_performance(self):
        source = compile_source(*source_fixture()); data = performance_fixture()
        data["shots"][0]["actions"] = [action("eyelid", id="relax", start=1, end=5, to="relaxed"),
            action("breath", id="inhale", start=6, end=9, phase="inhale"),
            action("speak", id="speech", start=10, end=30, source_use_id="U1", voice=voice_spec()),
            action("blink_restraint", id="steady", start=10, end=20, reason="emphasis", utterance_id="speech", char_range=[0, 2]),
            action("breath", id="exhale", start=31, end=34, phase="exhale"), action("blink", id="blink", start=35, end=37)]
        result = lower_production_decisions(data, source_compilation=source); canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(eye_contract_faults(canonical["shots"][0], duration=4, dialogue_rows=[("甲", "明日再议。")]), [])
        self.assertEqual(validate_timing(canonical, canonical["shots"])["faults"], [])
        binding = next(x["performance_binding"] for x in result["derivation"]["shots"][0]["actions"] if x["kind"] == "blink_restraint")
        self.assertEqual((binding["utterance_id"], binding["char_range"], binding["text"]), ("speech", [0, 2], "明日"))
        self.assertFalse(binding["is_measured"])
        for update in [{"utterance_id": "missing"}, {"char_range": [2, 4]}, {"start": 15}, {"reason": "unsupported"}]:
            bad = copy.deepcopy(data); bad["shots"][0]["actions"][3].update(update)
            with self.assertRaises(ProductionDecisionError): lower_production_decisions(bad, source_compilation=source)
        bad = copy.deepcopy(data); bad["shots"][0]["actions"][-1].update(start=15, end=17)
        with self.assertRaisesRegex(ProductionDecisionError, "overlapping writes"): lower_production_decisions(bad, source_compilation=source)

    def test_plot_reason_without_source_and_incompatible_performance_reject(self):
        data = performance_fixture(); data["shots"][0]["actions"] = [action("blink_restraint", reason="shock", utterance_id=None, char_range=None)]
        self.rejected(data, "real source use")
        data = performance_fixture(); data["shots"][0]["actions"] = [action("eyelid", to="relaxed"), action("eyelid", id="other", to="slightly_tense")]
        self.rejected(data, "overlapping writes")
        source = compile_source(*source_fixture()); data = production_fixture()
        data["shots"][0]["actions"] = [action("speak", id="speech", source_use_id="U1", voice=voice_spec()), action("breath", phase="inhale")]
        with self.assertRaisesRegex(ProductionDecisionError, "overlapping writes"): lower_production_decisions(data, source_compilation=source)

    def test_plot_reason_has_real_source_binding_and_never_self_signs_entailment(self):
        graph, uses, texts = source_fixture()
        texts["DOC"] = "甲因警觉而保持注视。"; graph["sources"]["DOC"] = hashlib.sha256(texts["DOC"].encode()).hexdigest()
        graph["facts"] = [source_controls.fact("F1", texts["DOC"], channel="action", content="",
            availability="not_applicable", occurrence="occurred", expression="not_applicable", modality="asserted")]
        uses["uses"][0].update(mode="physical_action", wording="authored", actor="甲", text="甲因警觉而保持注视。")
        source = compile_source(graph, uses, texts)
        data = performance_fixture(); data["shots"][0]["actions"] = [action("blink_restraint", id="watch", start=5, end=25,
            reason="vigilance", utterance_id=None, char_range=None, source_use_id="U1")]
        from source_action_plans import migrate_source_actions
        migration = migrate_source_actions(data, source)
        graph["action_plans"] = migration["action_plans"]
        data, source = migration["payload"], compile_source(graph, uses, texts)
        result = lower_production_decisions(data, source_compilation=source)
        _render_decisions(result["typed_decisions"])
        binding = result["derivation"]["shots"][0]["actions"][0]["performance_binding"]
        self.assertEqual(binding, {"reason": "vigilance", "source_use_id": "U1", "semantic_review": "pending_independent"})
        self.assertEqual(result["derivation"]["world_events"][0]["effect_occurrences"], 1)
        self.assertNotIn("blink_restraint", result["derivation"]["final_world"]["actors"]["甲"]["performance_state"])

    def test_focus_adjust_cannot_hide_a_stale_moving_entity_target(self):
        data = performance_fixture(); other = copy.deepcopy(data["initial_world"]["actors"]["甲"])
        other.update(position_xyz=[0, 2, 0], reach_origin_xyz=[0, 2, 1.2], gaze=None)
        data["initial_world"]["actors"]["乙"] = other
        data["initial_world"]["actors"]["甲"]["gaze"] = {"kind": "actor", "id": "乙"}
        data["initial_world"]["points"]["乙落脚点"] = {"scene_id": "ROOM", "position_xyz": [0.4, 2, 0]}
        data["shots"][0]["actions"] = [action("focus_adjust", id="focus", start=10, end=25),
            action("move_actor", id="move", actor="乙", start=10, end=20, to="乙落脚点")]
        self.rejected(data, "stale")

    def test_same_target_look_preserves_mid_action_gaze_without_implicit_tracking(self):
        data = performance_fixture()
        data["shots"][0]["actions"] = [action("look", id="hold_gaze", start=0, end=40, target={"kind": "point", "id": "观察点"}, tracking=False, head_mode="still"),
            action("focus_adjust", id="focus", start=5, end=15)]
        result = self.lower(data); _render_decisions(result["typed_decisions"])
        self.assertEqual(result["derivation"]["final_world"]["actors"]["甲"]["gaze"]["ref"]["id"], "观察点")
        other = copy.deepcopy(data["initial_world"]["actors"]["甲"]); other.update(position_xyz=[0,2,0], reach_origin_xyz=[0,2,1.2], gaze=None)
        data["initial_world"]["actors"]["乙"] = other
        data["initial_world"]["actors"]["甲"]["gaze"] = {"kind":"actor","id":"乙"}
        data["shots"][0]["actions"][0]["target"] = {"kind":"actor","id":"乙"}
        data["initial_world"]["points"]["乙落脚点"] = {"scene_id":"ROOM","position_xyz":[0.4,2,0]}
        data["shots"][0]["actions"].append(action("move_actor", id="move", actor="乙", start=10, end=20, to="乙落脚点"))
        self.rejected(data, "stale")

    def test_hidden_blink_to_visible_real_turn_keeps_spoken_ancestor(self):
        source = compile_source(*source_fixture()); data = production_fixture()
        other = copy.deepcopy(data["initial_world"]["actors"]["甲"])
        other.update(position_xyz=[0.5, 0, 0], reach_origin_xyz=[0.5, 0, 1.2], body_yaw=180, head_yaw=180)
        data["initial_world"]["actors"]["乙"] = other
        data["shots"][0]["actions"] = [action("speak", id="speech", start=10, end=30, source_use_id="U1", voice=voice_spec()),
            action("blink", id="hidden", actor="乙", start=30, end=32, after=["speech"]),
            action("turn", id="turn", actor="乙", start=32, end=38, body_yaw=0, head_yaw=0, after=["hidden"])]
        result = lower_production_decisions(data, source_compilation=source); shot = _render_decisions(result["typed_decisions"])["shots"][0]
        self.assertNotIn("乙自然眨眼", shot["画面内容"])
        self.assertIn("转头至头部朝镜头方向", shot["画面内容"])
        self.assertEqual(listener_reaction_faults(shot, duration=4, dialogue_rows=[("甲", "明日再议。")]), [])
        event = next(e for e in result["typed_decisions"]["shots"][0]["events"] if e["id"] == "turn")
        self.assertEqual(event["after"], ["speech"])

    def test_visible_speech_lip_sync_is_limited_to_actual_face_audio_intersection(self):
        graph, uses, texts = source_fixture(); source = compile_source(graph, uses, texts)
        data = production_fixture(); data["shots"][0]["ticks"] = [0, 10, 60, 80]
        data["shots"][0]["actions"] = [action("turn", id="turn", start=0, end=30, body_yaw=180, head_yaw=180),
                                        action("speak", id="voice", start=8, end=32, source_use_id="U1", voice=voice_spec())]
        result = lower_production_decisions(data, source_compilation=source)
        canonical = _render_decisions(result["typed_decisions"])
        picture = canonical["shots"][0]["画面内容"]
        intervals = result["derivation"]["shots"][0]["actions"][1]["mouth_sync_intervals"]
        self.assertEqual(intervals[0][0], 0.8)
        self.assertLess(intervals[-1][1], 3.2)
        self.assertIn("口型与对白一致", picture)
        self.assertNotIn("音轨身份", picture)
        data["shots"][0]["camera"]["position_xyz"] = [0, -4, 1.5]
        data["shots"][0]["actions"] = [action("speak", id="voice", start=8, end=32, source_use_id="U1", voice=voice_spec())]
        result = lower_production_decisions(data, source_compilation=source)
        picture = _render_decisions(result["typed_decisions"])["shots"][0]["画面内容"]
        self.assertNotIn("口型与对白一致", picture)
        self.assertIn("甲口部不入画", picture)
        self.assertEqual(result["derivation"]["shots"][0]["actions"][0]["mouth_sync_intervals"], [])

    def test_explicit_eyelid_tense_then_relax_uses_real_intermediate_state(self):
        data = performance_fixture(); data["initial_world"]["actors"]["甲"]["performance_state"]["eyelid"] = "relaxed"
        data["shots"][0]["actions"] = [action("eyelid", id="tighten", start=1, end=8, to="slightly_tense"),
            action("eyelid", id="relax", start=8, end=14, to="relaxed", after=["tighten"])]
        result = self.lower(data); picture = _render_decisions(result["typed_decisions"])["shots"][0]["画面内容"]
        self.assertIn("上眼睑轻收", picture); self.assertIn("上眼睑放松", picture)
        self.assertEqual(result["derivation"]["final_world"]["actors"]["甲"]["performance_state"]["eyelid"], "relaxed")
        self.assertEqual([x["effect_occurrences"] for x in result["derivation"]["world_events"]], [1, 1])

    def test_exhaling_with_speech_is_compatible_but_inhaling_is_not(self):
        source = compile_source(*source_fixture()); data = production_fixture()
        data["shots"][0]["actions"] = [action("speak", id="speech", source_use_id="U1", voice=voice_spec()),
            action("breath", id="breath", phase="exhale")]
        result = lower_production_decisions(data, source_compilation=source); canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(validate_timing(canonical, canonical["shots"])["faults"], [])
        data["shots"][0]["actions"][1]["phase"] = "inhale"
        with self.assertRaisesRegex(ProductionDecisionError, "overlapping writes"): lower_production_decisions(data, source_compilation=source)

    def test_visible_blink_clock_and_spoken_dependency_reach_existing_consumers(self):
        graph, uses, texts = source_fixture(); source = compile_source(graph, uses, texts)
        data = production_fixture(); other = copy.deepcopy(data["initial_world"]["actors"]["甲"])
        other["position_xyz"] = [0.5, 0, 0]; other["reach_origin_xyz"] = [0.5, 0, 1.2]
        data["initial_world"]["actors"]["乙"] = other
        data["shots"][0]["actions"] = [action("speak", id="voice", start=8, end=28, source_use_id="U1", voice=voice_spec()),
                                        action("blink", id="response", actor="乙", start=32, end=34, after=["voice"])]
        result = lower_production_decisions(data, source_compilation=source)
        shot = _render_decisions(result["typed_decisions"])["shots"][0]
        faults = eye_contract_faults(shot, duration=4, dialogue_rows=[("甲", uses["uses"][0]["text"])])
        self.assertFalse(any("眨眼未绑定数值时点" in x or "眨眼次数与明确数值时点" in x for x in faults))
        self.assertFalse(any("缺上/下眼睑松紧变化" in x for x in faults))  # No forced eyelid event in current production.
        self.assertEqual(listener_reaction_faults(shot, duration=4, dialogue_rows=[("甲", uses["uses"][0]["text"])]), [])
        event = result["typed_decisions"]["shots"][0]["events"][0]
        self.assertIn("乙在3.2秒", event["basis"])
        self.assertIn("于甲本句发言结束后", event["basis"])
        self.assertIn(event["basis"], timed_picture(shot))
        self.assertEqual((event["start"], event["end"], event["after"]), (3.2, 3.4, ["voice"]))
        data["shots"][0]["actions"].pop()
        shot = _render_decisions(lower_production_decisions(data, source_compilation=source)["typed_decisions"])["shots"][0]
        self.assertTrue(listener_reaction_faults(shot, duration=4, dialogue_rows=[("甲", uses["uses"][0]["text"])]))

    def test_narration_dependency_does_not_invent_audible_listener_reaction(self):
        graph, uses, texts = source_fixture()
        fact = graph["facts"][0]; fact.update(subject="叙述者", channel="narration", content_text="事情尚未结束。", content_policy="adaptable", expression_status="not_applicable")
        uses["uses"][0].update(mode="objective_narration", wording="authored", actor=None, text="事情尚未结束。")
        # This control tests channel projection; the synthetic source must also state this narrative fact.
        texts["DOC"] = "事情尚未结束。"; fact["supports"] = [{"source_id": "DOC", "start": 0, "end": len(texts["DOC"]), "text": texts["DOC"]}]
        graph["sources"]["DOC"] = hashlib.sha256(texts["DOC"].encode()).hexdigest()
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["shots"][0]["actions"] = [action("narrate", id="narration", start=8, end=28, source_use_id="U1"),
                                        action("blink", start=32, end=34, after=["narration"])]
        result = lower_production_decisions(data, source_compilation=source)
        picture = _render_decisions(result["typed_decisions"])["shots"][0]["画面内容"]
        self.assertNotIn("听完", picture)
        self.assertNotIn("发言结束后", picture)
        self.assertNotIn("口型与对白一致", picture)

    def test_paper_move_updates_later_static_and_snapshots_without_alias(self):
        data = production_fixture()
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP90-002", shot_uid="SYNTHETIC-UID-002")
        second["actions"] = []; second["transition"] = {"relation": "continuous", "basis": "仍在书房同一时刻。"}
        data["shots"].append(second)
        result = self.lower(data); rows = result["typed_decisions"]["shots"]
        self.assertEqual(rows[0]["start_state"]["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"], [0.2, 0.3, 1.0])
        self.assertEqual(rows[0]["end_state"], rows[1]["start_state"])
        self.assertEqual(rows[1]["start_state"]["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"], [0.5, 0.3, 1.0])
        self.assertIn("信纸位于画面中部", rows[1]["fields"]["起始状态"])
        rows[1]["start_state"]["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"][0] = 99
        self.assertEqual(rows[0]["end_state"]["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"][0], 0.5)
        _render_decisions(self.lower(data)["typed_decisions"])

    def test_deleted_move_cannot_change_position_and_manual_copies_are_rejected(self):
        data = production_fixture(); data["shots"][0]["actions"] = []
        row = self.lower(data)["typed_decisions"]["shots"][0]
        self.assertEqual(row["start_state"]["动作进度"]["物件位置与状态"], row["end_state"]["动作进度"]["物件位置与状态"])
        for key in ["end_state", "start_state", "phases", "events", "staging"]:
            bad = copy.deepcopy(data); bad["shots"][0][key] = {}
            self.rejected(bad, "requires exactly")

    def test_camera_rotation_changes_actual_bound_screen_relation(self):
        data = production_fixture(); other = copy.deepcopy(data["initial_world"]["actors"]["甲"])
        other["position_xyz"][0] = 0.7; data["initial_world"]["actors"]["乙"] = other
        r1 = self.lower(data)["typed_decisions"]["shots"][0]
        first, _ = bind_fields(r1["fields"], r1["staging"])
        data["shots"][0]["camera"]["position_xyz"] = [0, -4, 1.5]
        r2 = self.lower(data)["typed_decisions"]["shots"][0]
        second, _ = bind_fields(r2["fields"], r2["staging"])
        self.assertIn("乙位于甲的画面左侧", first["起始状态"])
        self.assertIn("乙位于甲的画面右侧", second["起始状态"])
        self.assertNotEqual(first["机位"], second["机位"])

    def test_turn_back_derives_partial_window_without_fourth_phase(self):
        data = production_fixture(); data["shots"][0]["actions"] = [action("turn", body_yaw=180, head_yaw=180)]
        result = self.lower(data); row = result["typed_decisions"]["shots"][0]
        windows = result["derivation"]["shots"][0]["eye_visibility_ticks"]["甲"]
        self.assertEqual(len(row["phases"]), 3)
        self.assertTrue(windows[0][1] < 30)
        self.assertNotIn("眼睛清楚可见", row["phases"][2]["text"])
        self.assertIn("眼睛不入画", row["phases"][2]["text"])
        _render_decisions(result["typed_decisions"])

    def test_back_to_camera_never_claims_visible_eyes(self):
        data = production_fixture()
        a = data["initial_world"]["actors"]["甲"]; a["head_yaw"] = a["body_yaw"] = 180
        row = self.lower(data)["typed_decisions"]["shots"][0]
        self.assertIn("后侧头部", row["fields"]["可见主体"])
        self.assertIn("眼睛不入画", row["fields"]["起始状态"])
        self.assertNotIn("眼神自然聚焦", row["fields"]["生成限制"])
        data["shots"][0]["actions"] = [action("blink", start=12, end=14)]
        hidden = self.lower(data)
        self.assertEqual(hidden["derivation"]["final_world"]["completed"], ["act1"])
        self.assertEqual(hidden["typed_decisions"]["shots"][0]["events"], [])
        self.assertNotIn("眨眼", "".join(p["text"] for p in hidden["typed_decisions"]["shots"][0]["phases"]))

    def test_stale_target_requires_explicit_tracking_or_clear(self):
        data = production_fixture(); world = data["initial_world"]
        observer = copy.deepcopy(world["actors"]["甲"]); observer["position_xyz"] = [0, -1, 0]
        observer["gaze"] = {"kind": "prop", "id": "信纸"}; world["actors"]["乙"] = observer
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        self.rejected(data, "stale named target")
        track = action("look", target={"kind": "prop", "id": "信纸"}, tracking=True, head_mode="follow")
        track.update(id="track", actor="乙")
        data["shots"][0]["actions"].append(track)
        row = self.lower(data)["typed_decisions"]["shots"][0]
        self.assertEqual(row["end_state"]["视线落点"]["乙"]["position_xyz"], [0.5, 0.3, 1.0])

    def test_known_wall_blocks_current_gaze(self):
        data = production_fixture(); w = data["initial_world"]
        w["points"]["墙后目标"] = {"scene_id": "ROOM", "position_xyz": [0, 2, 1.5]}
        w["actors"]["甲"]["gaze"] = {"kind": "point", "id": "墙后目标"}
        w["scenes"]["ROOM"]["barriers"] = [{"id": "墙", "a": [-1, 1, 0], "b": [1, 1, 0], "height_m": 2}]
        self.rejected(data, "barrier blocks gaze")

    def test_cross_room_target_keeps_real_scene_and_is_rejected(self):
        data = production_fixture(); w = data["initial_world"]
        w["scenes"]["OTHER"] = copy.deepcopy(w["scenes"]["ROOM"])
        other = copy.deepcopy(w["actors"]["甲"]); other["scene_id"] = "OTHER"; w["actors"]["乙"] = other
        row = self.lower(data)["typed_decisions"]["shots"][0]
        self.assertEqual(row["start_state"]["人物位置"]["乙"]["scene_id"], "OTHER")
        w["actors"]["甲"]["gaze"] = {"kind": "actor", "id": "乙"}
        self.rejected(data, "crosses physical scenes")

    def test_blink_basis_is_literal_and_inside_actual_macro_phase(self):
        data = production_fixture(); blink = action("blink"); blink.update(start=12, end=14)
        data["shots"][0]["actions"] = [blink]
        result = self.lower(data); row = result["typed_decisions"]["shots"][0]
        event = row["events"][0]
        self.assertIn(event["basis"], row["phases"][1]["text"])
        self.assertNotIn(event["basis"], row["phases"][0]["text"])
        _render_decisions(result["typed_decisions"])
        data["shots"][0]["actions"][0].update(start=9, end=11)
        self.rejected(data, "cross macro")

    def test_continuous_move_crosses_macro_once_with_final_effect_and_dependency(self):
        data = production_fixture(); move = action("move_prop", prop="信纸", hand="right", to="案右")
        move.update(start=5, end=35)
        after = action("hold"); after.update(id="after", start=36, end=39, after=["act1"])
        data["shots"][0]["actions"] = [move, after]
        result = self.lower(data); row = result["typed_decisions"]["shots"][0]
        self.assertEqual([x["id"] for x in row["events"][:3]], ["act1-part1", "act1-part2", "act1-part3"])
        self.assertEqual(row["events"][-1]["after"], ["act1-part3"])
        self.assertEqual(result["derivation"]["final_world"]["completed"].count("act1"), 1)
        self.assertEqual(row["end_state"]["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"], [0.5, 0.3, 1.0])
        _render_decisions(result["typed_decisions"])

    def test_actor_eye_target_keeps_base_and_derived_aim_distinct(self):
        data = production_fixture(); w = data["initial_world"]
        other = copy.deepcopy(w["actors"]["甲"]); other["position_xyz"] = [0.5, 1, 0]; w["actors"]["乙"] = other
        w["actors"]["甲"]["gaze"] = {"kind": "actor", "id": "乙"}
        row = self.lower(data)["typed_decisions"]["shots"][0]
        target = row["start_state"]["视线落点"]["甲"]
        self.assertEqual(target["position_xyz"], [0.5, 1, 0])
        self.assertEqual(target["aim_position_xyz"], [0.5, 1, 1.5])
        _render_decisions(self.lower(data)["typed_decisions"])

    def test_inspection_collects_independent_later_shot_errors(self):
        data = production_fixture(); second = copy.deepcopy(data["shots"][0])
        second.update(shot_id="EP90-002", shot_uid="SECOND")
        data["shots"].append(second)
        data["shots"][0]["actions"][0]["kind"] = "teleport"
        data["shots"][1]["camera"]["hfov_deg"] = False
        report = inspect_production_decisions(data)
        self.assertEqual(len(report["findings"]), 2)
        self.assertIn("shots[0].actions[0]", {x["path"] for x in report["findings"]})
        self.assertTrue(report["not_checked"])

    def test_source_unavailable_keeps_three_camera_and_action_checks(self):
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        data["shots"] = [copy.deepcopy(data["shots"][0]) for _ in range(3)]
        data["shots"][0]["camera"]["hfov_deg"] = False
        data["shots"][1]["camera"]["target"] = "不存在的点"
        data["shots"][2]["camera"]["position_xyz"][0] = float("nan")
        data["shots"][2]["actions"][0]["kind"] = "teleport"
        before = copy.deepcopy(data)
        report = inspect_production_decisions(data, source_unavailable_reason="source graph rejected before geometry")
        self.assertEqual(len(report["findings"]), 4)
        paths = [x["path"] for x in report["findings"]]
        for i in range(3): self.assertTrue(any(p.startswith(f"shots[{i}].camera") for p in paths))
        self.assertIn("shots[2].actions[0]", paths)
        self.assertFalse(any("unknown compiled written" in x["message"] or "written_ref" in x["path"] for x in report["findings"]))
        self.assertNotIn("lowered", report)
        self.assertEqual({x["stage"] for x in report["not_checked"]}, {"source_references", "state_effect_simulation_and_lowering"})
        self.assertEqual(data["initial_world"], before["initial_world"])

    def test_source_unavailable_does_not_claim_lowering_even_if_structure_is_valid(self):
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        reason = "source text binding failed"
        report = inspect_production_decisions(data, source_unavailable_reason=reason)
        self.assertEqual(report["findings"], [])
        self.assertIn("initial_world.structure", report["checked"])
        self.assertIn("shots[0].camera", report["checked"])
        self.assertIn("shots[0].actions[0]", report["checked"])
        self.assertNotIn("initial_world", report["checked"])
        self.assertNotIn("lowered", report)
        self.assertTrue(all(x["reason"] == reason for x in report["not_checked"]))

    def test_source_unavailable_still_rejects_actual_world_numbers_and_ref_shape(self):
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        data["initial_world"]["actors"]["甲"]["reach_origin_xyz"][0] = float("nan")
        report = inspect_production_decisions(data, source_unavailable_reason="source rejected")
        self.assertEqual(report["findings"][0]["path"], "actor.reach_origin_xyz")
        self.assertIn("finite range", report["findings"][0]["message"])
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = []
        report = inspect_production_decisions(data, source_unavailable_reason="source rejected")
        self.assertEqual(report["findings"][0]["path"], "prop.written_ref")

    def test_available_source_still_rejects_unknown_paper_and_accepts_bound_paper(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper()
        uses["written_objects"][0]["carrier_id"] = "信纸"
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "ABSENT"
        for supplied_source in (None, source):
            report = inspect_production_decisions(data, source_compilation=supplied_source)
            self.assertTrue(any(x["path"] == "prop.written_ref" for x in report["findings"]))
            self.assertNotIn("lowered", report)
        data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        report = inspect_production_decisions(data, source_compilation=source)
        self.assertEqual(report["findings"], [])
        self.assertIn("lowered", report)

    def test_source_unavailability_requires_real_nonempty_reason(self):
        for invalid in ("", False, []):
            report = inspect_production_decisions(production_fixture(), source_unavailable_reason=invalid)
            self.assertEqual(report["findings"][0]["path"], "source_unavailable_reason")
            self.assertNotIn("lowered", report)

    def test_overlapping_writes_and_missing_prerequisite_are_targeted(self):
        data = production_fixture(); first = action("move_actor", to="落脚处")
        second = action("turn", body_yaw=90, head_yaw=90); second["id"] = "turn"
        data["shots"][0]["actions"] = [first, second]
        self.rejected(data, "overlapping writes")
        data["shots"][0]["actions"] = [first]; first["after"] = ["absent"]
        self.rejected(data, "missing or unfinished")

    def test_held_paper_requires_transfer(self):
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["anchor"] = {"actor": "甲", "hand": "left"}
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        self.rejected(data, "held prop requires transfer")

    def test_unknown_type_and_numeric_inputs_do_not_silently_coerce(self):
        data = production_fixture(); data["shots"][0]["actions"] = [action("teleport")]
        self.rejected(data, "unsupported action")
        for number in [True, float("nan"), float("inf"), "1.2"]:
            bad = production_fixture(); bad["initial_world"]["actors"]["甲"]["position_xyz"][0] = number
            self.rejected(bad, "finite")

    def test_transition_does_not_replace_physical_state(self):
        data = production_fixture(); second = copy.deepcopy(data["shots"][0])
        second.update(shot_id="EP90-002", shot_uid="SYNTHETIC-UID-002", continuity_id="ROOM-C2", actions=[])
        second["transition"] = {"relation": "later_same_place", "basis": "同一书房稍后，人物仍留原位。"}
        data["shots"].append(second)
        rows = self.lower(data)["typed_decisions"]["shots"]
        self.assertEqual(rows[0]["end_state"], rows[1]["start_state"])
        self.assertIn("同一地点省略时间", rows[1]["transition_explanation"])

    def test_current_paper_text_comes_from_real_compilation_once(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper()
        uses["written_objects"][0]["carrier_id"] = "信纸"
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        result = lower_production_decisions(data, source_compilation=source)
        static = result["typed_decisions"]["shots"][0]["fields"]["起始状态"]
        self.assertIn(source.written_objects[0].text, static)
        self.assertEqual(result["derivation"]["consumed_source_use_ids"], ["U1"])
        self.assertEqual(result["derivation"]["consumed_written_object_ids"], ["P1"])
        self.assertEqual(result["derivation"]["shots"][0]["visible_current_paper_refs"], ["P1"])
        _render_decisions(result["typed_decisions"])
        data["initial_world"]["props"]["信纸"]["text"] = "第二份内容"
        with self.assertRaises(ProductionDecisionError): lower_production_decisions(data, source_compilation=source)

    def test_current_paper_visibility_is_per_first_view_not_world_membership(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper()
        uses["written_objects"][0]["carrier_id"] = "信纸"
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        data["initial_world"]["points"]["空处"] = {"scene_id": "ROOM", "position_xyz": [10, 0, 1.1]}
        data["initial_world"]["props"]["木盒"] = {"anchor": {"point": "空处"}, "written_ref": None}
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP90-002", shot_uid="PAPER-OFFSCREEN", actions=[])
        second["transition"] = {"relation": "continuous", "basis": "同室切至空处。"}
        second["camera"].update(position_xyz=[10, 4, 1.5], target="空处")
        data["shots"].append(second)
        result = lower_production_decisions(data, source_compilation=source)
        self.assertEqual([r["visible_current_paper_refs"] for r in result["derivation"]["shots"]], [["P1"], []])
        self.assertEqual(result["derivation"]["final_world"]["props"]["信纸"]["written_ref"], "P1")
        self.assertNotIn(source.written_objects[0].text, result["typed_decisions"]["shots"][1]["fields"]["起始状态"])
        _render_decisions(result["typed_decisions"])

    def test_future_write_is_absent_at_first_frame_and_present_only_after_effect(self):
        graph, uses, texts = source_controls.SourceDecisionTests().paper(False)
        uses["written_objects"][0]["carrier_id"] = "信纸"; uses["uses"][0]["actor"] = "甲"
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["initial_world"]["props"]["笔"] = {"anchor": {"actor": "甲", "hand": "right"}, "written_ref": None}
        write = action("write", paper_ref="P1", hand="right", tool="笔", source_use_id="U1")
        data["shots"][0]["actions"] = [write]
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP90-002", shot_uid="AFTER-WRITE", actions=[])
        second["transition"] = {"relation": "continuous", "basis": "书写完成后同场切换。"}; data["shots"].append(second)
        result = lower_production_decisions(data, source_compilation=source); rows = result["typed_decisions"]["shots"]
        self.assertNotIn(source.written_objects[0].text, rows[0]["fields"]["起始状态"])
        self.assertIn(source.written_objects[0].text, rows[1]["fields"]["起始状态"])
        self.assertIsNone(rows[0]["start_state"]["动作进度"]["物件位置与状态"]["信纸"].get("paper_text"))
        _render_decisions(result["typed_decisions"])
        data["initial_world"]["props"]["信纸"]["written_ref"] = "P1"
        with self.assertRaisesRegex(ProductionDecisionError, "must be current"):
            lower_production_decisions(data, source_compilation=source)

    def test_speech_consumes_actual_source_and_real_voice_and_timing(self):
        source = compile_source(*source_fixture())
        voice = voice_spec()
        data = production_fixture(); data["shots"][0]["actions"] = [action("speak", source_use_id="U1", voice=voice)]
        result = lower_production_decisions(data, source_compilation=source)
        row = result["typed_decisions"]["shots"][0]
        self.assertIn('甲说道：“明日再议。”', row["phases"][1]["text"])
        self.assertEqual(result["typed_decisions"]["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]["segments"][0]["char_range"], [0, 5])
        _render_decisions(result["typed_decisions"])
        data["shots"][0]["actions"][0]["text"] = "重复台词"
        with self.assertRaises(ProductionDecisionError): lower_production_decisions(data, source_compilation=source)

    def test_unconsumed_source_cannot_remain_only_in_metadata(self):
        with self.assertRaisesRegex(ProductionDecisionError, "source_consumption: missing"):
            lower_production_decisions(production_fixture(), source_compilation=compile_source(*source_fixture()))

    def test_objective_narration_is_not_onscreen_speech_or_current_paper(self):
        graph, uses, texts = source_fixture()
        uses["uses"][0].update(mode="objective_narration", wording="authored", actor=None, text="商议安排在明日。")
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["shots"][0]["actions"] = [action("narrate", actor=None, source_use_id="U1")]
        result = lower_production_decisions(data, source_compilation=source); row = result["typed_decisions"]["shots"][0]
        self.assertEqual(row["fields"]["音色"], "无对白，不适用。")
        self.assertIn("商议安排在明日。", row["fields"]["心理音轨"])
        self.assertNotIn("商议安排在明日。", row["fields"]["起始状态"])
        _render_decisions(result["typed_decisions"])

    def test_positive_point_and_transfer_have_real_hand_and_holder_effects(self):
        data = production_fixture(); w = data["initial_world"]
        w["points"]["指尖终点"] = {"scene_id": "ROOM", "position_xyz": [0.2, 0.5, 1.1]}
        w["points"]["远处目标"] = {"scene_id": "ROOM", "position_xyz": [0.4, 1, 1.0]}
        data["shots"][0]["actions"] = [action("point", hand="right", target={"kind": "point", "id": "远处目标"}, finger_to="指尖终点")]
        result = self.lower(data); _render_decisions(result["typed_decisions"])
        self.assertEqual(result["derivation"]["final_world"]["actors"]["甲"]["hands"]["right"], [0.2, 0.5, 1.1])
        data = production_fixture(); w = data["initial_world"]
        other = copy.deepcopy(w["actors"]["甲"]); other["position_xyz"] = [0.4, 0, 0]; w["actors"]["乙"] = other
        w["props"]["信纸"]["anchor"] = {"actor": "甲", "hand": "right"}
        data["shots"][0]["actions"] = [action("transfer", prop="信纸", to_actor="乙", to_hand="left")]
        result = self.lower(data); _render_decisions(result["typed_decisions"])
        self.assertEqual(result["derivation"]["final_world"]["props"]["信纸"]["anchor"], {"actor": "乙", "hand": "left"})
        w["actors"]["乙"]["position_xyz"][0] = 2
        self.rejected(data, "hands must meet")

    def test_hold_turn_and_look_across_macro_phases_are_not_reauthored(self):
        for kind, extras in [("hold", {}), ("turn", {"body_yaw": 180, "head_yaw": 180}),
                             ("look", {"target": {"kind": "point", "id": "注視目标"}, "tracking": False, "head_mode": "still"})]:
            with self.subTest(kind=kind):
                data = production_fixture(); data["initial_world"]["points"]["注視目标"] = {"scene_id": "ROOM", "position_xyz": [0, 2, 1.5]}
                a = action(kind, **extras); a.update(start=5, end=35)
                data["shots"][0]["actions"] = [a]
                result = self.lower(data); row = result["typed_decisions"]["shots"][0]
                self.assertEqual(len(row["events"]), 3)
                self.assertEqual(result["derivation"]["final_world"]["completed"], ["act1"])
                _render_decisions(result["typed_decisions"])

    def test_explicit_reach_origin_is_used_and_transforms_with_actor(self):
        data = production_fixture(); w = data["initial_world"]; actor = w["actors"]["甲"]
        actor.update(reach_origin_xyz=[0, 0, 0.8], reach_m=0.85)
        actor["hands"]["right"] = [0.2, 0, 0.4]
        w["points"]["案左"]["position_xyz"] = [0.2, 0, 0.4]
        w["points"]["案右"]["position_xyz"] = [0.7, 0, 0.4]
        data["shots"][0]["actions"] = [action("move_prop", prop="信纸", hand="right", to="案右")]
        _render_decisions(self.lower(data)["typed_decisions"])
        actor["reach_origin_xyz"] = [0, 0, 1.2]
        self.rejected(data, "exceeds declared reach")
        data = production_fixture(); actor = data["initial_world"]["actors"]["甲"]
        actor["reach_origin_xyz"] = [0.1, 0.2, 0.8]
        move = action("move_actor", to="落脚处", start=1, end=8)
        turn = action("turn", body_yaw=90, head_yaw=90, start=12, end=24, id="turn", after=["act1"])
        data["shots"][0]["actions"] = [move, turn]
        end = self.lower(data)["derivation"]["final_world"]["actors"]["甲"]["reach_origin_xyz"]
        for actual, expected in zip(end, [1.2, -0.1, 0.8]): self.assertAlmostEqual(actual, expected)
        del actor["reach_origin_xyz"]
        self.rejected(data, "reach_origin_xyz")

    def test_one_audio_crossing_macro_keeps_one_quote_and_predicted_characters(self):
        source = compile_source(*source_fixture()); data = production_fixture()
        data["shots"][0]["ticks"] = [0, 10, 60, 80]
        data["shots"][0]["actions"] = [action("speak", source_use_id="U1", voice=voice_spec(), start=8, end=32)]
        result = lower_production_decisions(data, source_compilation=source)
        canonical = _render_decisions(result["typed_decisions"])
        self.assertEqual(canonical["shots"][0]["画面内容"].count('甲说道：“明日再议。”'), 1)
        utterances = canonical["prevention_evidence"]["audio_workflow"]["timing"]["utterances"]
        self.assertEqual(len(utterances), 1)
        segments = utterances[0]["segments"]
        self.assertEqual([(s["start"], s["end"]) for s in segments], [(0.8, 1.0), (1.0, 3.2)])
        text = source.uses[0].text
        self.assertEqual("".join(text[a:b] for a, b in [s["char_range"] for s in segments]), text)
        self.assertFalse(result["derivation"]["shots"][0]["audio_alignments"][0]["is_measured"])

    def test_narration_then_speech_share_shot_without_channel_or_index_confusion(self):
        graph, uses, texts = source_fixture()
        texts["DOC"] = "甲留在书房。甲说道：明日再议。"
        graph["sources"]["DOC"] = hashlib.sha256(texts["DOC"].encode()).hexdigest()
        graph["facts"][0]["supports"] = [{"source_id": "DOC", "start": 0, "end": len(texts["DOC"]), "text": texts["DOC"]}]
        narration = copy.deepcopy(graph["facts"][0]); narration.update(id="FN", event_id="EN", subject=None,
            channel="narration", content_text="甲留在书房。", occurrence="occurred", expression_occurrence="not_applicable", expression_status="not_applicable")
        graph["facts"].append(narration)
        uses["uses"].append({"id": "UN", "fact_ids": ["FN"], "mode": "objective_narration", "wording": "authored", "actor": None, "text": "甲留在书房。"})
        source = compile_source(graph, uses, texts)
        data = production_fixture(); data["shots"][0]["ticks"] = [0, 10, 45, 60]
        data["shots"][0]["actions"] = [action("narrate", id="narrate", source_use_id="UN", start=8, end=28),
            action("speak", source_use_id="U1", voice=voice_spec(), start=32, end=42, after=["narrate"])]
        result = lower_production_decisions(data, source_compilation=source); canonical = _render_decisions(result["typed_decisions"])
        utterances = canonical["prevention_evidence"]["audio_workflow"]["timing"]["utterances"]
        self.assertEqual([u["kind"] for u in utterances], ["psychological", "dialogue"])
        self.assertEqual(utterances[1]["source"]["dialogue_index"], 0)
        self.assertEqual(len(utterances[0]["segments"]), 2)
        self.assertIn("甲留在书房。", canonical["shots"][0]["心理音轨"])
        self.assertNotIn("甲留在书房。", canonical["shots"][0]["起始状态"])
        data["shots"][0]["actions"][1].update(start=20, end=30, after=[])
        with self.assertRaisesRegex(ProductionDecisionError, "overlapping writes"):
            lower_production_decisions(data, source_compilation=source)

    def test_hidden_world_blink_retains_effect_and_transitive_visible_dependency(self):
        data = production_fixture(); data["shots"][0]["ticks"] = [0, 10, 60, 80]
        before = action("hold", id="before", start=12, end=20)
        blink = action("blink", id="blink", start=32, end=34, after=["before"])
        after = action("hold", id="after", start=36, end=40, after=["blink"])
        data["shots"][0]["actions"] = [before, blink, after]
        front = self.lower(data)
        data["shots"][0]["camera"]["position_xyz"] = [0, -4, 1.5]
        back = self.lower(data); canonical = _render_decisions(back["typed_decisions"])
        self.assertEqual(front["derivation"]["final_world"], back["derivation"]["final_world"])
        self.assertNotIn("眨眼", canonical["shots"][0]["画面内容"])
        events = canonical["prevention_evidence"]["audio_workflow"]["timing"]["events"]
        self.assertEqual([e["id"] for e in events], ["before", "after"])
        self.assertEqual(events[-1]["after"], ["before"])
        hidden = next(e for e in back["derivation"]["world_events"] if e["id"] == "blink")
        self.assertEqual(hidden["effect_occurrences"], 1)
        self.assertEqual(hidden["visible_projection_ids"], [])
        self.assertEqual(hidden["projection_terminal_dependencies"], ["before"])
        data["shots"][0]["actions"][-1].update(start=33, end=35)
        self.rejected(data, "missing or unfinished")

    def test_subcharacter_macro_cut_is_explicitly_unsupported_not_fake_alignment(self):
        graph, uses, texts = source_fixture(); texts["DOC"] = "甲说道：好。"
        graph["sources"]["DOC"] = hashlib.sha256(texts["DOC"].encode()).hexdigest()
        graph["facts"][0].update(content_text="好。", occurrence="occurred",
            supports=[{"source_id": "DOC", "start": 0, "end": len(texts["DOC"]), "text": texts["DOC"]}])
        uses["uses"][0]["text"] = "好。"; source = compile_source(graph, uses, texts)
        data = production_fixture(); voice = voice_spec(); voice.update(emphasis="“好”。", speech_act="回答对方。")
        data["shots"][0]["actions"] = [action("speak", source_use_id="U1", voice=voice, start=9, end=11)]
        with self.assertRaisesRegex(ProductionDecisionError, "not-supported audio alignment") as ctx:
            lower_production_decisions(data, source_compilation=source)
        self.assertEqual(ctx.exception.path, "shots[0].actions[0].audio_alignment")

    def test_partial_blink_projects_only_visible_intersection_and_one_world_effect(self):
        data = production_fixture()
        turn = action("turn", body_yaw=180, head_yaw=180, id="turn", start=0, end=30)
        blink = action("blink", start=12, end=16)
        data["shots"][0]["actions"] = [turn, blink]
        result = self.lower(data); canonical = _render_decisions(result["typed_decisions"])
        event = next(e for e in canonical["prevention_evidence"]["audio_workflow"]["timing"]["events"] if e["id"] == "act1")
        self.assertIn("局部", event["basis"])
        self.assertLess(event["end"], 1.6)
        self.assertEqual(result["derivation"]["final_world"]["completed"].count("act1"), 1)


if __name__ == "__main__": unittest.main()
