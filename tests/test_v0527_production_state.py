"""Synthetic state ownership and dependency behavior; no manuscript fixtures."""
import copy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills/shortdrama-storyboard/scripts"
sys.path[:0] = [str(SCRIPTS), str(TESTS)]
from production_state import (CONTRACT, ProductionStateChain, ProductionStateError,
                              StateSnapshot, shot_states, validate_named_targets)
from author_storyboard import record_attempt, _render_decisions, RejectedDecisions
from test_v0527_staging_text_bindings import bound_decisions


def state():
    return {
        "人物位置": {"甲": {"scene_id": "ROOM", "position_xy": [-1, 0]},
                   "乙": {"scene_id": "ROOM", "position_xy": [1, 0]}},
        "身体朝向": {"甲": {"body_yaw": 90, "head_yaw": 90},
                   "乙": {"body_yaw": 0, "head_yaw": 0}},
        "视线落点": {
            "甲": {"kind": "entity", "target_type": "actor", "target_id": "乙",
                  "status": "resolved", "scene_id": "ROOM", "position_xy": [1, 0]},
            "乙": {"kind": "point", "name": "北墙标记", "status": "resolved",
                  "scene_id": "ROOM", "position_xy": [1, 2]}},
        "手持道具": [], "门窗状态": "门关闭，东窗关闭",
        "动作进度": {"实际进展": "甲仍坐于案边，尚未触碰信纸",
                   "物件位置与状态": {"信纸": {"scene_id": "ROOM", "position_xyz": [0, 0, 0.8],
                                               "holder": "桌面", "state": "平放"}}},
        "光源方向": "东窗漫射天光",
    }


def package(*, strict=True):
    data = bound_decisions(); row = data["shots"][0]
    row["start_state"] = state(); row["end_state"] = state()
    for a in row["staging"]["geometry"]["actors"]:
        if a["actor_id"] == "甲":
            a.update(body_yaw=90, head_yaw=90, gaze_target=[1, 0])
        else:
            a.update(gaze_target=[1, 2])
    row["phases"][0]["text"] = "甲看向乙。"
    row["phases"][-1]["text"] = "甲视线仍落乙。"
    if strict:
        data["project"]["prevention_evidence"]["authoring_evidence"] = {
            "production_state_contract": CONTRACT}
    return data


class ProductionStateTests(unittest.TestCase):
    def chain(self):
        return ProductionStateChain(state(), label="shot-1-start", reason="作者给出实际首态")

    def test_input_and_export_mutation_cannot_change_snapshot_or_history(self):
        original = state(); chain = ProductionStateChain(original, label="first", reason="首态")
        first_hash = chain.current.sha256
        original["人物位置"]["乙"]["position_xy"][0] = 7
        exported = chain.current.to_dict(); exported["人物位置"]["乙"]["position_xy"][0] = 9
        self.assertEqual(chain.current.to_dict()["人物位置"]["乙"]["position_xy"], [1, 0])
        self.assertEqual(chain.current.sha256, first_hash)
        with self.assertRaises(FrozenInstanceError): chain.current.label = "changed"

    def test_later_setup_and_action_never_rewrite_saved_end_or_other_export(self):
        chain = self.chain(); first = chain.current
        end = chain.advance(first.to_dict(), label="shot-1-end", reason="本镜位置保持")
        saved = shot_states(first, end); digest = end.sha256
        following = end.to_dict()
        following["人物位置"]["丙"] = {"scene_id": "OTHER", "position_xy": [0, 1]}
        following["视线落点"]["丙"] = {"kind": "point", "name": "另一室案面", "status": "resolved",
                                      "scene_id": "OTHER", "position_xy": [0, 0]}
        added = chain.advance(following, label="shot-2-start", reason="另室人物明确入场")
        following["人物位置"]["丙"]["position_xy"][0] = 100
        self.assertNotIn("丙", end.to_dict()["人物位置"])
        self.assertNotIn("丙", saved["end_state"]["人物位置"])
        self.assertEqual(added.to_dict()["人物位置"]["丙"]["position_xy"], [0, 1])
        self.assertEqual(end.sha256, digest)
        saved["start_state"]["人物位置"]["乙"]["position_xy"][0] = 2
        self.assertEqual(saved["end_state"]["人物位置"]["乙"]["position_xy"], [1, 0])

    def test_move_with_stale_target_rejects_atomically_without_auto_follow(self):
        chain = self.chain(); before = chain.history; candidate = chain.current.to_dict()
        candidate["人物位置"]["乙"]["position_xy"] = [2, 0]
        with self.assertRaisesRegex(ProductionStateError, "stale named-target"):
            chain.advance(candidate, label="after-move", reason="乙向东移动")
        self.assertEqual(chain.history, before)
        self.assertEqual(candidate["视线落点"]["甲"]["position_xy"], [1, 0])

    def test_explicit_coordinate_update_preserves_pose_and_prior_state(self):
        chain = self.chain(); prior = chain.current.to_dict(); candidate = copy.deepcopy(prior)
        candidate["人物位置"]["乙"]["position_xy"] = [2, 0]
        candidate["视线落点"]["甲"]["position_xy"] = [2, 0]
        result = chain.advance(candidate, label="after-move", reason="乙移动且甲实际换焦由作者另列阶段")
        self.assertEqual(result.to_dict(), candidate)
        self.assertEqual(result.to_dict()["身体朝向"], prior["身体朝向"])
        self.assertEqual(chain.history[0].to_dict(), prior)

    def test_explicit_invalidation_retains_unknown_without_fabricated_coordinates(self):
        chain = self.chain(); candidate = chain.current.to_dict()
        candidate["人物位置"]["乙"]["scene_id"] = "OTHER"
        candidate["视线落点"]["甲"] = {"kind": "entity", "target_type": "actor", "target_id": "乙",
                                      "status": "unresolved", "reason": "乙离开本室，当前看向何处待作者决定"}
        candidate["视线落点"]["乙"] = {"kind": "point", "name": "新的目光目标", "status": "unresolved",
                                      "reason": "进入另室后的视线尚未确定"}
        result = chain.advance(candidate, label="pending", reason="跨室后的视线显式失效")
        self.assertNotIn("position_xy", result.to_dict()["视线落点"]["甲"])
        report = validate_named_targets(result.to_dict(), strict=True)
        self.assertEqual(len(report["unresolved"]), 2)
        self.assertEqual(report["dependencies"], [])
        self.assertNotIn("PASS", json.dumps(report["dependencies"]))

    def test_pending_reference_cannot_keep_stale_coordinate_or_missing_reason(self):
        for change in ("position", "reason"):
            with self.subTest(change=change):
                candidate = state(); gaze = candidate["视线落点"]["甲"]
                gaze.update(status="unresolved", reason="暂不知位置")
                if change == "reason":
                    for key in ["position_xy", "scene_id", "reason"]: gaze.pop(key)
                with self.assertRaises(ProductionStateError): validate_named_targets(candidate, strict=True)

    def test_deleted_entity_and_other_room_cannot_support_resolved_gaze(self):
        for change in ("delete", "room"):
            with self.subTest(change=change):
                candidate = state()
                if change == "delete":
                    del candidate["人物位置"]["乙"]; del candidate["视线落点"]["乙"]
                else:
                    candidate["人物位置"]["乙"]["scene_id"] = "OTHER"
                    candidate["视线落点"]["甲"]["scene_id"] = "OTHER"
                with self.assertRaisesRegex(ProductionStateError, "absent|cross-scene"):
                    validate_named_targets(candidate, strict=True)

    def test_named_entity_cannot_be_disguised_as_fixed_point(self):
        candidate = state(); candidate["视线落点"]["甲"] = {
            "kind": "point", "name": "乙", "status": "resolved", "scene_id": "ROOM", "position_xy": [1, 0]}
        with self.assertRaisesRegex(ProductionStateError, "relabeled"):
            validate_named_targets(candidate, strict=True)

    def test_former_location_is_an_explicit_different_point_not_auto_tracking(self):
        candidate = state(); candidate["人物位置"]["乙"]["position_xy"] = [2, 0]
        candidate["视线落点"]["甲"] = {"kind": "point", "name": "原站位地面点", "status": "resolved",
                                      "scene_id": "ROOM", "position_xy": [1, 0]}
        result = StateSnapshot(candidate, label="ground", reason="作者明确目光留原地")
        self.assertEqual(result.to_dict()["视线落点"]["甲"]["position_xy"], [1, 0])

    def test_prop_height_changes_require_full_explicit_vector_update(self):
        candidate = state(); candidate["视线落点"]["甲"] = {
            "kind": "entity", "target_type": "prop", "target_id": "信纸", "status": "resolved",
            "scene_id": "ROOM", "position_xyz": [0, 0, .8]}
        chain = ProductionStateChain(candidate, label="desk", reason="注视案面信纸")
        lifted = chain.current.to_dict()
        lifted["动作进度"]["物件位置与状态"]["信纸"]["position_xyz"][2] = 1.2
        with self.assertRaisesRegex(ProductionStateError, "stale named-target"):
            chain.advance(lifted, label="raised", reason="信纸抬高")
        lifted["视线落点"]["甲"]["position_xyz"][2] = 1.2
        chain.advance(lifted, label="raised", reason="作者明确目光对应抬高后的纸面")
        self.assertEqual(chain.history[0].to_dict()["视线落点"]["甲"]["position_xyz"][2], .8)

    def test_nan_boolean_zero_vector_dimension_and_conflicting_coordinates(self):
        for bad in ([float('nan'), 0], [True, 0], [0], [0, 0, 0]):
            with self.subTest(bad=bad):
                candidate = state(); candidate["人物位置"]["乙"]["position_xy"] = bad
                with self.assertRaises(ProductionStateError): validate_named_targets(candidate, strict=True)
        # Zero coordinates are valid world positions; they are not direction vectors.
        candidate = state(); candidate["人物位置"]["乙"]["position_xy"] = [0, 0]
        candidate["视线落点"]["甲"]["position_xy"] = [0, 0]
        validate_named_targets(candidate, strict=True)
        candidate["人物位置"]["乙"]["position_xyz"] = [1, 0, .9]
        with self.assertRaisesRegex(ProductionStateError, "conflict"):
            validate_named_targets(candidate, strict=True)

    def test_duplicate_label_and_empty_reason_do_not_commit(self):
        chain = self.chain()
        for label, reason in ((chain.current.label, "动作"), ("new", "")):
            with self.subTest(label=label):
                with self.assertRaises(ProductionStateError): chain.advance(state(), label=label, reason=reason)
                self.assertEqual(len(chain.history), 1)

    def test_unknown_observer_missing_actor_target_and_unknown_contract_reject(self):
        candidate = state(); candidate["视线落点"]["丙"] = copy.deepcopy(candidate["视线落点"]["甲"])
        with self.assertRaises(ProductionStateError): validate_named_targets(candidate, strict=True)
        data = package(); data["project"]["prevention_evidence"]["authoring_evidence"]["production_state_contract"] = "unknown"
        with self.assertRaises(RejectedDecisions): _render_decisions(data)

    def test_no_people_state_does_not_invent_an_observer(self):
        candidate = state(); candidate["人物位置"] = "无人物"; candidate["视线落点"] = "无人物"
        report = validate_named_targets(candidate, strict=True)
        self.assertEqual(report["dependencies"], [])

    def test_state_fields_empty_placeholders_and_prop_identity_are_not_snapshots(self):
        for mutate in (
            lambda s: s.pop('门窗状态'),
            lambda s: s.update(门窗状态={}),
            lambda s: s['人物位置'].update(甲={}),
            lambda s: s.update(手持道具='无'),
            lambda s: s.update(手持道具=[{'prop_id': 'letter', 'holder': '甲'}]),
            lambda s: s.update(手持道具=[{'prop_id': 'letter', 'holder': '甲', 'state': '握持'}] * 2),
            lambda s: s['视线落点'].pop('乙'),
        ):
            with self.subTest(mutation=mutate):
                candidate = state(); mutate(candidate)
                with self.assertRaises(ProductionStateError):
                    StateSnapshot(candidate, label='invalid', reason='合成无效输入')

    def test_actual_typed_v2_rejects_stale_raw_and_preserves_attempt_bytes(self):
        data = package(); data["shots"][0]["end_state"]["人物位置"]["乙"]["position_xy"] = [2, 0]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "input.json"
            raw = (json.dumps(data, ensure_ascii=False) + '\n').encode(); path.write_bytes(raw)
            result = record_attempt(path, root / 'attempts', attempt_id='stale')
            self.assertEqual(result['status'], 'rejected', result)
            self.assertIn('end_state', result['findings'][0]['path'])
            self.assertIn('stale named-target', result['findings'][0]['message'])
            directory = Path(result['report_path']).parent
            self.assertEqual((directory / 'input.original.json').read_bytes(), raw)
            self.assertFalse((directory / 'canonical.json').exists())
            self.assertEqual(result['input_sha256'], hashlib.sha256(raw).hexdigest())

    def test_actual_valid_record_diagnostics_are_not_independent_evidence(self):
        data = package()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'input.json'; path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            result = record_attempt(path, root / 'attempts', attempt_id='valid')
            self.assertEqual(result['status'], 'rendered_pending_preflight', result)
            diagnostics = json.loads(Path(result['production_state_dependencies_path']).read_text('utf-8'))
            self.assertEqual(len(diagnostics), 2)
            self.assertEqual(len(diagnostics[0]['dependencies']), 2)
            output = json.loads(Path(result['canonical_path']).read_text('utf-8'))
            self.assertEqual(output['prevention_evidence']['scene_state_ledger'][0]['start_state'], state())
            self.assertNotIn('dialogue_adaptation_audit', output['prevention_evidence'])
            self.assertFalse(result['commit_eligible']); self.assertIsNone(result['semantic_accuracy_percent'])

    def test_recognized_legacy_target_cannot_evade_v2_dependency_check(self):
        data = package(strict=False)
        for endpoint in ['start_state', 'end_state']:
            data['shots'][0][endpoint]['视线落点']['甲'] = {'name': '乙', 'position_xy': [0, 1]}
        with self.assertRaisesRegex(RejectedDecisions, 'stale named-target'):
            _render_decisions(data)

    def test_legacy_v1_keeps_compatibility_without_claiming_new_validation(self):
        data = package(strict=False); data['contract'] = 'typed-authoring-decisions-v1'
        row = data['shots'][0]; row.pop('staging')
        row['fields']['机位'] = '固定机位。'; row['fields']['起始状态'] = '甲坐在桌旁。'
        row['start_state']['视线落点']['甲'] = {'name': '乙', 'position_xy': [99, 0]}
        output = _render_decisions(data)
        self.assertEqual(output['prevention_evidence']['scene_state_ledger'][0]['start_state']['视线落点']['甲']['position_xy'], [99, 0])

    def test_deleted_legacy_target_is_recognized_from_the_other_endpoint(self):
        data = package(strict=False)
        for endpoint in ['start_state', 'end_state']:
            data['shots'][0][endpoint]['视线落点']['甲'] = {'name': '乙', 'position_xy': [1, 0]}
        end = data['shots'][0]['end_state']; del end['人物位置']['乙']; del end['视线落点']['乙']
        with self.assertRaisesRegex(RejectedDecisions, 'absent'):
            _render_decisions(data)


if __name__ == '__main__':
    unittest.main()
