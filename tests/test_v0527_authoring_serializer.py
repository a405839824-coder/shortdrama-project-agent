"""Regression cases for EP33's authoring structure failures, not story fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/shortdrama-storyboard/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from author_storyboard import CONTRACT, COMPUTED, FIELDS, RejectedDecisions, record_attempt, _render_decisions
import author_storyboard

# Unit tests isolate the pure internal renderer. Every real author attempt must
# use record_attempt (or its CLI), including rejected decisions.
render_decisions = _render_decisions


def decisions():
    """Small explicitly authored data to exercise structural rendering only."""
    fields = {field: "测试字段内容。" for field in FIELDS if field not in COMPUTED}
    fields.update({"场景连续性ID": "SC-TEST", "生成限制": "9:16竖屏；禁止额外配乐。",
                   "机位": "平视50mm。",
                   "起始状态": "甲双手放在桌边，信纸平放桌面。", "音色": "无对白，不适用。",
                   "心理音轨": "无。", "原文支撑句": "甲坐在桌旁，信纸留在桌上。"})
    state = {"人物位置": {"甲": "桌南侧坐姿"}, "身体朝向": {"甲": "朝北"},
             "视线落点": {"甲": "信纸"},
             "手持道具": [{"prop_id": "letter", "holder": "桌面", "state": "平放"}],
             "门窗状态": "南门敞开，东窗关闭", "动作进度": "甲双手放在桌边",
             "光源方向": "东窗漫射天光"}
    row = {"shot_id": "EP33-001", "shot_uid": "EXP33-S001", "episode_id": "EP33", "duration": 4.0,
           "source_unit_ids": ["P01", "P02"], "fields": fields,
           "phases": [{"start": 0.0, "end": 0.7, "text": "甲看向信纸。"},
                      {"start": 0.7, "end": 3.3, "text": "甲保持双手放在桌边。"},
                      {"start": 3.3, "end": 4.0, "text": "甲视线仍落信纸。"}],
           "events": [{"id": "hold-1", "kind": "hold", "actor": "甲", "start": 0.7, "end": 3.3,
                       "action": "保持", "basis": "甲保持双手放在桌边。"}],
           "start_state": copy.deepcopy(state), "end_state": copy.deepcopy(state),
           "transition_explanation": "本次切点仍为桌边状态，独立连续性结论待审。"}
    project = {"source_lock": {"output_contract": "R10-SPC-1", "target_model": "Seedance 2.5", "aspect_ratio": "9:16"},
               "prevention_evidence": {
                   "scope_lock": {"authoritative_source": "测试用作者数据，非正式源文登记"},
                   "source_ledger": [{"source_id": "P01", "source_text": "甲坐在桌旁。"},
                                     {"source_id": "P02", "source_text": "信纸留在桌上。"}],
                   "change_lock": {"must_include": [], "must_exclude": []},
                   "cognition_matrix": [{"character": "甲", "knows": ["信纸在桌上"], "does_not_know": ["信纸来历待定"]}],
                   "shot_plan": [{"shot_id": "EP33-001", "core_function": "观察信纸"}],
                   "performance_chains": [],
                   "audio_workflow": {"timing": {"contract": "shot-timing-v1", "utterances": []}},
                   "audit_layers": ["源文对白", "机位可见性", "空间连续", "表演链", "镜头节奏", "正式母版单镜执行完整性", "禁项与声音", "统计闭合"],
                   "repair_history": []}}
    return {"contract": CONTRACT, "project": project, "shots": [row]}


def speaking_decisions():
    data = decisions(); row = data["shots"][0]
    row["phases"][0]["text"] = '甲说道：“放这里。”'
    data["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"] = [{
        "id": "say-1", "kind": "dialogue", "speaker": "甲",
        "source": {"shot_id": "EP33-001", "dialogue_index": 0},
        "segments": [{"shot_id": "EP33-001", "start": 0.0, "end": 0.7,
                      "char_range": [0, len("放这里。")]}]}]
    row["events"][0]["after"] = ["say-1"]
    return data


class AuthoringSerializerTests(unittest.TestCase):
    def test_real_ep33_source_array_is_serialized_without_mutating_prose_or_decisions(self):
        data = decisions(); before = copy.deepcopy(data)
        output = render_decisions(data)
        shot = output["shots"][0]
        self.assertEqual(shot["覆盖原文"], "P01；P02")
        self.assertEqual(list(shot)[:27], FIELDS)
        self.assertEqual(shot["原文支撑句"], data["shots"][0]["fields"]["原文支撑句"])
        self.assertEqual(data, before)
        self.assertEqual(output["total_duration"], 4.0)

    def test_episode_cumulative_time_uses_exact_tenths_and_resets(self):
        data = decisions()
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP33-002", shot_uid="EXP33-S002", duration=4.3)
        second["phases"][-1]["end"] = 4.3; second["events"][0]["id"] = "hold-2"
        third = copy.deepcopy(data["shots"][0]); third.update(shot_id="EP34-001", episode_id="EP34", shot_uid="EXP34-S001")
        third["events"][0]["id"] = "hold-3"
        data["shots"].extend([second, third])
        output = render_decisions(data)
        self.assertEqual([s["累计时长"] for s in output["shots"]], [4.0, 8.3, 4.0])
        self.assertEqual(output["total_duration"], 12.3)

    def test_missing_actor_is_a_recorded_rejected_attempt_with_no_canonical(self):
        data = decisions(); del data["shots"][0]["events"][0]["actor"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8"); path.write_bytes(raw)
            report = record_attempt(path, root / "attempts", attempt_id="missing-actor")
            self.assertEqual(report["status"], "rejected")
            self.assertEqual(report["input_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertIn("actor", report["findings"][0]["path"])
            attempt = Path(report["report_path"]).parent
            self.assertEqual((attempt / "input.original.json").read_bytes(), raw)
            self.assertFalse((attempt / "canonical.json").exists())
            self.assertTrue(report["include_in_attempt_accounting"])

    def test_structured_prop_requires_explicit_holder_and_state(self):
        for value in ("信纸留在桌上", [{"prop_id": "letter", "holder": "桌面"}],
                      [{"prop_id": "letter", "holder": ["甲", "乙"], "state": "平放"}],
                      [{"prop_id": "letter", "holder": "甲/乙", "state": "平放"}],
                      [{"prop_id": "letter", "holder": "桌面", "state": ""}]):
            with self.subTest(value=value):
                data = decisions(); data["shots"][0]["start_state"]["手持道具"] = value
                with self.assertRaises(RejectedDecisions): render_decisions(data)

    def test_unknown_prop_state_and_unequal_cuts_are_preserved_without_audit_evidence(self):
        data = decisions()
        data["shots"][0]["end_state"]["手持道具"] = [{"prop_id": "letter", "holder": "未知", "state": "待核"}]
        second = copy.deepcopy(data["shots"][0]); second.update(shot_id="EP33-002", shot_uid="EXP33-S002")
        second["events"][0]["id"] = "hold-2"; data["shots"].append(second)
        result = render_decisions(data); evidence = result["prevention_evidence"]
        states = evidence["scene_state_ledger"]
        self.assertNotEqual(states[0]["end_state"], states[1]["start_state"])
        self.assertEqual(states[0]["end_state"]["手持道具"][0]["holder"], "未知")
        self.assertNotIn("adjacent_continuity_audit", evidence)
        self.assertNotIn("dialogue_adaptation_audit", evidence)
        self.assertEqual(evidence["repair_history"], [])
        self.assertNotIn("entailed_by_source", json.dumps(result))

    def test_phase_gaps_extra_markers_and_unrounded_times_are_rejected(self):
        for change in ("gap", "extra", "precision", "numeric_string", "boolean"):
            with self.subTest(change=change):
                data = decisions(); row = data["shots"][0]
                if change == "gap": row["phases"][1]["start"] = 0.8
                if change == "extra": row["phases"][1]["text"] += "【1.0—2.0秒】再保持。"
                if change == "precision": row["duration"] = 4.01
                if change == "numeric_string": row["duration"] = "4.0"
                if change == "boolean": row["duration"] = True
                with self.assertRaises(RejectedDecisions): render_decisions(data)

    def test_shadow_metadata_and_unbound_or_duplicate_sources_are_rejected(self):
        for change in ("shadow_coverage", "shadow_duration", "unbound", "duplicate", "delimiter"):
            with self.subTest(change=change):
                data = decisions(); row = data["shots"][0]
                if change == "shadow_coverage": row["fields"]["覆盖原文"] = ["P01", "P02"]
                if change == "shadow_duration": data["project"]["total_duration"] = 999
                if change == "unbound": row["source_unit_ids"] = ["P99"]
                if change == "duplicate": row["source_unit_ids"] = ["P01", "P01"]
                if change == "delimiter": row["source_unit_ids"] = ["P01；P02"]
                with self.assertRaises(RejectedDecisions): render_decisions(data)

    def test_existing_attempt_is_untouched_and_collision_is_also_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            path.write_text(json.dumps(decisions(), ensure_ascii=False), encoding="utf-8")
            first = record_attempt(path, root / "attempts", attempt_id="fixed")
            self.assertEqual(first["status"], "rendered_pending_preflight")
            before = {p.name: p.read_bytes() for p in (root / "attempts/fixed").iterdir()}
            second = record_attempt(path, root / "attempts", attempt_id="fixed")
            self.assertEqual(second["status"], "rejected")
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / "attempts/fixed").iterdir()})
            reports = list((root / "attempts").glob("*/report.json"))
            self.assertEqual(len(reports), 2)
            self.assertIsNone(second["canonical_path"])
            self.assertIsNone(first["semantic_accuracy_percent"])
            self.assertFalse(first["commit_eligible"])

    def test_invalid_json_and_duplicate_keys_preserve_raw_failed_input(self):
        for raw in (b"not JSON", b'{"contract":1,"contract":2}', b'{"value":NaN}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); path = root / "bad.json"; path.write_bytes(raw)
                report = record_attempt(path, root / "attempts")
                self.assertEqual(report["status"], "rejected")
                self.assertEqual(Path(report["report_path"]).with_name("input.original.json").read_bytes(), raw)
                self.assertEqual(report["input_sha256"], hashlib.sha256(raw).hexdigest())

    def test_current_event_binding_validator_is_reused(self):
        data = decisions(); data["shots"][0]["events"][0]["actor"] = "乙"
        with self.assertRaisesRegex(RejectedDecisions, "事件|actor|主体"):
            render_decisions(data)

    def test_valid_speech_dependency_is_bound_without_inventing_evidence(self):
        data = speaking_decisions(); original = copy.deepcopy(data)
        output = render_decisions(data)
        actual = output["prevention_evidence"]["audio_workflow"]["timing"]
        self.assertEqual(actual["events"][0]["after"], ["say-1"])
        self.assertEqual(actual["utterances"], original["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"])
        self.assertEqual(data, original)
        self.assertNotIn("adjacent_continuity_audit", output["prevention_evidence"])

    def test_invalid_speech_references_and_actor_are_rejected(self):
        for change in ("source", "segment", "actor", "duplicate_identity", "characters"):
            with self.subTest(change=change):
                data = speaking_decisions(); row = data["shots"][0]
                row["events"][0].pop("after")
                utterance = data["project"]["prevention_evidence"]["audio_workflow"]["timing"]["utterances"][0]
                if change == "source": utterance["source"]["shot_id"] = "EP99-999"
                if change == "segment": utterance["segments"][0]["shot_id"] = "EP99-999"
                if change == "actor": utterance["speaker"] = "乙"
                if change == "duplicate_identity": row["events"][0]["id"] = "say-1"
                if change == "characters": utterance["segments"][0]["char_range"] = [0, 99]
                with self.assertRaises(RejectedDecisions): render_decisions(data)

    def test_mouth_action_cannot_overlap_same_actor_speech(self):
        data = speaking_decisions(); row = data["shots"][0]
        row["phases"][0]["text"] += "甲喝水。"
        row["events"] = [{"id": "drink", "kind": "action", "actor": "甲", "start": 0.1,
                          "end": 0.5, "action": "喝水", "basis": "甲喝水。"}]
        with self.assertRaisesRegex(RejectedDecisions, "重叠"):
            render_decisions(data)

    def test_whitespace_identity_cannot_hide_duplicate_prop_or_source(self):
        for change in ("prop", "source", "uid"):
            with self.subTest(change=change):
                data = decisions()
                if change == "prop":
                    data["shots"][0]["start_state"]["手持道具"].append(
                        {"prop_id": " letter ", "holder": "乙", "state": "手持"})
                if change == "source":
                    data["project"]["prevention_evidence"]["source_ledger"].append(
                        {"source_id": " P01 ", "source_text": "different source"})
                if change == "uid": data["shots"][0]["shot_uid"] = " EXP33-S001 "
                with self.assertRaises(RejectedDecisions): render_decisions(data)

    def test_state_placeholders_are_rejected_but_coordinates_and_unknowns_survive(self):
        for value in ("  ", {"甲": ""}, {"甲": None}, {"甲": {}}, {"甲": []}):
            with self.subTest(value=value):
                data = decisions(); data["shots"][0]["start_state"]["人物位置"] = value
                with self.assertRaises(RejectedDecisions): render_decisions(data)
        data = decisions()
        location = {"甲": {"xy": [0, 1.2], "observed": False, "heading": "待核"}}
        data["shots"][0]["start_state"]["人物位置"] = location
        result = render_decisions(data)
        self.assertEqual(result["prevention_evidence"]["scene_state_ledger"][0]["start_state"]["人物位置"], location)

    def test_empty_reserved_and_path_escape_ids_are_recorded_rejections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = json.dumps(decisions(), ensure_ascii=False).encode("utf-8"); path.write_bytes(raw)
            for name in ("", "CON", "com1", "../escape", "part/name", "name:stream"):
                with self.subTest(attempt_id=name):
                    report = record_attempt(path, root / "attempts", attempt_id=name)
                    self.assertEqual(report["status"], "rejected")
                    location = Path(report["report_path"]).parent
                    self.assertEqual(location.parent, root / "attempts")
                    self.assertEqual((location / "input.original.json").read_bytes(), raw)
                    self.assertFalse((location / "canonical.json").exists())
            self.assertFalse((root / "escape").exists())
            self.assertEqual(len(list((root / "attempts").glob("*/report.json"))), 6)

    def test_raw_time_precision_is_not_rounded_away_by_json_float_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = json.dumps(decisions(), ensure_ascii=False).replace(
                '"duration": 4.0', '"duration": 4.0000000000000000000000000000000001').encode("utf-8")
            path.write_bytes(raw)
            report = record_attempt(path, root / "attempts", attempt_id="precision")
            self.assertEqual(report["status"], "rejected")
            self.assertIn("0.1-second", report["findings"][0]["message"])
            self.assertEqual(Path(report["report_path"]).with_name("input.original.json").read_bytes(), raw)

    def test_failed_canonical_write_does_not_publish_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = json.dumps(decisions(), ensure_ascii=False).encode("utf-8"); path.write_bytes(raw)
            real_fsync = author_storyboard.os.fsync
            calls = 0
            def fail_canonical(fd):
                nonlocal calls
                calls += 1
                if calls == 3: raise OSError("simulated canonical flush failure")
                return real_fsync(fd)
            with mock.patch.object(author_storyboard.os, "fsync", side_effect=fail_canonical):
                report = record_attempt(path, root / "attempts", attempt_id="write-failure")
            self.assertEqual(report["status"], "rejected")
            attempt = Path(report["report_path"]).parent
            self.assertEqual((attempt / "input.original.json").read_bytes(), raw)
            self.assertFalse((attempt / "canonical.json").exists())
            self.assertEqual(list(attempt.glob("*.pending")), [])

    def test_unexpected_validator_error_is_a_recorded_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = json.dumps(decisions(), ensure_ascii=False).encode("utf-8"); path.write_bytes(raw)
            with mock.patch.object(author_storyboard, "_render_decisions", side_effect=RuntimeError("validator bug")):
                report = record_attempt(path, root / "attempts", attempt_id="validator-error")
            self.assertEqual(report["status"], "rejected")
            self.assertIn("RuntimeError", report["findings"][0]["message"])
            self.assertEqual(Path(report["report_path"]).with_name("input.original.json").read_bytes(), raw)
            self.assertFalse(Path(report["report_path"]).with_name("canonical.json").exists())

    def test_cli_runs_and_reports_pending_preflight_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            path.write_text(json.dumps(decisions(), ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPTS / "author_storyboard.py"), "--input", str(path),
                                     "--attempts-root", str(root / "attempts")], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "rendered_pending_preflight")
            self.assertIsNone(report["semantic_accuracy_percent"])


if __name__ == "__main__":
    unittest.main()
