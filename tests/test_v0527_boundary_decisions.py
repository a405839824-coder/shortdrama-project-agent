import unittest

from shortdrama_mcp.service import validate_storyboard as validator
from boundary_decisions import BoundaryDecision
from shortdrama_mcp.delta import _build_adjacent_continuity_audit


class BoundaryDecisionTests(unittest.TestCase):
    def test_explicit_editorial_relations_compile_for_existing_boundary_gate(self):
        for relation, basis in [
            ("later_same_place", "争执一段时间后仍坐原位，省略经过时长。"),
            ("prerequisite_reordering", "人员核验结束后才交接，没有先上岗。"),
            ("same_event_reframing", "重复叙述合并为一次查验，物品仍未拆封。")]:
            with self.subTest(relation=relation):
                text = BoundaryDecision("ROOM-A", "ROOM-B", relation, basis).explanation()
                self.assertIn(basis, text)
                shots = [{"镜头编号": "EP01-001", "场景连续性ID": "ROOM-A"},
                         {"镜头编号": "EP01-002", "场景连续性ID": "ROOM-B"}]
                states = [{"shot_id": s["镜头编号"], "scene_id": s["场景连续性ID"],
                           "start_state": {"动作进度": "等待"}, "end_state": {"动作进度": "完成"},
                           "transition_explanation": basis} for s in shots]
                before = _build_adjacent_continuity_audit(shots, states)
                self.assertEqual(before["pairs"][0]["continuity_status"], "fail_current")
                states[1]["transition_explanation"] = text
                after = _build_adjacent_continuity_audit(shots, states)
                self.assertEqual(after["pairs"][0]["continuity_status"], "explained_transition")
                self.assertEqual(before["pairs"][0]["left_end_state_sha256"],
                                 after["pairs"][0]["left_end_state_sha256"])
                self.assertEqual(before["pairs"][0]["right_start_state_sha256"],
                                 after["pairs"][0]["right_start_state_sha256"])

    def test_explanation_cannot_waive_same_scene(self):
        with self.assertRaises(ValueError):
            BoundaryDecision("ROOM-A", "ROOM-A", "later_same_place", "时间过去了。").explanation()

    def test_missing_or_invented_relation_is_rejected(self):
        for relation, basis in [("same_event_reframing", "无"), ("auto_pass", "时间过去了。")]:
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                BoundaryDecision("ROOM-A", "ROOM-B", relation, basis).explanation()

if __name__ == "__main__":
    unittest.main()
