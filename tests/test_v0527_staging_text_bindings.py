"""Computed staging must drive the actual recorded canonical text."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from author_storyboard import BOUND_CONTRACT, RejectedDecisions, record_attempt, _render_decisions
from staging_text_bindings import bind_fields, GeometryInputError
from test_v0527_authoring_serializer import decisions


def bound_decisions():
    d = decisions(); d["contract"] = BOUND_CONTRACT
    row = d["shots"][0]
    row["fields"]["机位"] = "[[staging:camera]]50mm，中景。[[staging:pair]]"
    row["fields"]["起始状态"] += "[[staging:side]]不提前拿起纸上现有“待核”标题的信纸。"
    row["staging"] = {
        "contract": "initial-staging-text-v1", "target_label": "案面中心",
        "geometry": {"contract": "staging-geometry-v1", "camera": [0, -5], "camera_target": [0, 0],
                     "actors": [{"actor_id": name, "actor_position": pos, "body_yaw": 0,
                                 "head_yaw": 0, "gaze_target": [0, 1]}
                                for name, pos in [("甲", [-1, 0]), ("乙", [1, 0])]]},
        "bindings": [{"id": "camera", "kind": "camera_pose"},
                     {"id": "pair", "kind": "screen_relation", "actor": "甲", "relative_to": "乙"},
                     {"id": "side", "kind": "axis_side", "actor": "甲"}]}
    return d


class StagingTextBindingTests(unittest.TestCase):
    def test_camera_reverse_updates_actual_text_without_changing_source_quotes(self):
        d = bound_decisions(); before = copy.deepcopy(d)
        first = _render_decisions(d)["shots"][0]
        self.assertIn("案面中心南侧，朝北", first["机位"])
        self.assertIn("甲位于乙的画面左侧", first["机位"])
        self.assertIn("当前视轴的画面左侧", first["起始状态"])
        d["shots"][0]["staging"]["geometry"]["camera"] = [0, 5]
        reverse = _render_decisions(d)["shots"][0]
        self.assertIn("案面中心北侧，朝南", reverse["机位"])
        self.assertIn("甲位于乙的画面右侧", reverse["机位"])
        self.assertIn("当前视轴的画面右侧", reverse["起始状态"])
        self.assertIn('不提前拿起纸上现有“待核”标题的信纸。', reverse["起始状态"])
        self.assertEqual(d["shots"][0]["fields"], before["shots"][0]["fields"])
        self.assertEqual(reverse["原文支撑句"], first["原文支撑句"])

    def test_screen_order_uses_perspective_depth_not_only_horizontal_distance(self):
        d = bound_decisions(); row = d["shots"][0]
        g = row["staging"]["geometry"]
        g["camera"] = [0, 0]; g["camera_target"] = [0, 1]
        g["actors"][0]["actor_position"] = [2, 10]
        g["actors"][1]["actor_position"] = [1, 2]
        output = _render_decisions(d)
        self.assertIn("甲位于乙的画面左侧", output["shots"][0]["机位"])

    def test_no_actor_object_shot_uses_only_real_camera_target(self):
        d = bound_decisions(); row = d["shots"][0]
        row["staging"]["geometry"]["actors"] = []
        row["staging"]["bindings"] = row["staging"]["bindings"][:1]
        row["fields"]["机位"] = "[[staging:camera]]物件近景。"
        row["fields"]["起始状态"] = "无人入画，纸面平放。"
        fields, report = bind_fields(row["fields"], row["staging"])
        self.assertEqual(report["geometry"]["actors"], [])
        self.assertEqual(fields["起始状态"], "无人入画，纸面平放。")
        self.assertFalse(report["commit_eligible"])
        self.assertIsNone(report["semantic_accuracy_percent"])

    def test_unbound_unused_duplicate_unknown_actor_and_absent_camera_reject(self):
        mutations = [
            lambda r: r["fields"].update(机位="[[staging:unknown]]"),
            lambda r: r["staging"]["bindings"].append({"id": "unused", "kind": "camera_pose"}),
            lambda r: r["staging"]["bindings"].append({"id": "camera", "kind": "camera_pose"}),
            lambda r: r["staging"]["bindings"][1].update(actor="丙"),
            lambda r: r["fields"].update(机位="50mm，中景。[[staging:pair]]"),
            lambda r: r["fields"].update(机位="[[staging:camera 未完成]]"),
            lambda r: r["staging"]["bindings"][1].update(relative_to="甲"),
            lambda r: r["staging"].update(unknown=True),
            lambda r: r["fields"].update(机位="禁止[[staging:camera]]"),
            lambda r: r["phases"][0].update(text="[[staging:camera]]甲看向信纸。"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                d = bound_decisions(); mutate(d["shots"][0])
                with self.assertRaises(RejectedDecisions): _render_decisions(d)

    def test_geometry_contradictions_and_undefined_bearings_reject(self):
        for mutate in [
            lambda g: g.update(camera=[0, 0]),
            lambda g: g.update(screen_left_of=[{"left": "乙", "right": "甲"}]),
            lambda g: g["actors"][0].update(clear_face=True),
            lambda g: g["actors"][0].update(actor_position=[0, -6]),
        ]:
            d = bound_decisions(); mutate(d["shots"][0]["staging"]["geometry"])
            with self.subTest(mutation=mutate):
                with self.assertRaises(RejectedDecisions): _render_decisions(d)

    def test_tokens_cannot_modify_quotes_source_or_dynamic_phases(self):
        for field, value in [("机位", '[[staging:camera]]纸上现有“[[staging:pair]]”。'),
                             ("原文支撑句", "[[staging:camera]]"),
                             ("机位", '[[staging:camera]]纸上现有"[[staging:pair]]"。')]:
            d = bound_decisions(); d["shots"][0]["fields"][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(RejectedDecisions): _render_decisions(d)

    def test_nested_single_and_unclosed_quotes_never_expand_tokens(self):
        for text in ["‘备注；[[staging:pair]]’", "'备注；[[staging:pair]]'",
                     "“未闭合；[[staging:pair]]", '"未闭合；[[staging:pair]]',
                     "「外层‘嵌套；[[staging:pair]]’」", "『未闭合；[[staging:pair]]"]:
            with self.subTest(text=text):
                d = bound_decisions()
                d["shots"][0]["fields"]["机位"] = "[[staging:camera]]" + text
                before = copy.deepcopy(d)
                with self.assertRaises(RejectedDecisions): _render_decisions(d)
                self.assertEqual(d, before)

    def test_v2_reserves_markers_in_all_nonbinding_data_without_source_rewrite(self):
        marker = "[[staging:camera]]"
        mutations = [
            lambda d: d["project"]["prevention_evidence"]["source_ledger"][0].update(source_text="原文含" + marker),
            lambda d: d["shots"][0]["source_unit_ids"].append(marker),
            lambda d: d["shots"][0]["start_state"]["人物位置"].update(甲=marker),
            lambda d: d["shots"][0].update(transition_explanation=marker),
            lambda d: d["shots"][0]["end_state"]["人物位置"].update({marker: "原位"}),
            lambda d: d["shots"][0]["events"][0].update(basis=marker),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                d = bound_decisions(); mutate(d); before = copy.deepcopy(d)
                with self.assertRaisesRegex(RejectedDecisions, "reserved staging marker"):
                    _render_decisions(d)
                self.assertEqual(d, before)
        # v1 has no binding grammar and still represents literal source bytes.
        d = decisions(); d["project"]["prevention_evidence"]["source_ledger"][0]["source_text"] = marker
        out = _render_decisions(d)
        self.assertEqual(out["prevention_evidence"]["source_ledger"][0]["source_text"], marker)

    def test_recorded_v2_keeps_input_diagnostics_and_failed_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "input.json"; d = bound_decisions()
            raw = (json.dumps(d, ensure_ascii=False) + "\n").encode("utf-8"); path.write_bytes(raw)
            report = record_attempt(path, root / "attempts", attempt_id="bound")
            self.assertEqual(report["status"], "rendered_pending_preflight")
            self.assertEqual(report["input_contract"], BOUND_CONTRACT)
            self.assertEqual((root / "attempts/bound/input.original.json").read_bytes(), raw)
            evidence = json.loads(Path(report["initial_staging_bindings_path"]).read_text())
            self.assertIn("甲位于乙的画面左侧", evidence[0]["rendered_bindings"]["pair"])
            self.assertFalse(report["commit_eligible"])
            d["shots"][0]["staging"]["bindings"][1]["actor"] = "未声明的人"
            path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            rejected = record_attempt(path, root / "attempts", attempt_id="bad-binding")
            self.assertEqual(rejected["status"], "rejected")
            self.assertFalse((root / "attempts/bad-binding/canonical.json").exists())
            self.assertTrue((root / "attempts/bad-binding/input.original.json").exists())

    def test_v1_remains_exact_and_v2_requires_staging_on_every_shot(self):
        d = decisions(); output = _render_decisions(d)
        self.assertEqual(output["shots"][0]["机位"], d["shots"][0]["fields"]["机位"])
        d["contract"] = BOUND_CONTRACT
        with self.assertRaises(RejectedDecisions): _render_decisions(d)


if __name__ == "__main__":
    unittest.main()
