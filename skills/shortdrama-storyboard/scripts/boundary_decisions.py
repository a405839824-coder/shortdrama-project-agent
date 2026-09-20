"""Serialize an explicit editorial boundary without rewriting physical state.

The author supplies the actual relation and its source/staging basis. This
helper neither certifies that basis nor relaxes same-scene state continuity.
"""
from dataclasses import dataclass

RELATIONS = {"later_same_place": "同一地点省略时间后的场景切换",
             "prerequisite_reordering": "按先决条件重排的场景切换",
             "same_event_reframing": "同一事件重新取景的场景切换",
             "different_place": "地点变化的场景切换",
             "simultaneous_different_place": "同时异地的平行场景切换",
             "episode_boundary": "跨集场景切换"}


@dataclass(frozen=True)
class BoundaryDecision:
    left_scene: str
    right_scene: str
    relation: str
    basis: str

    def explanation(self):
        for field in ("left_scene", "right_scene", "basis"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or value.strip() in {"无", "不适用", "同上"}:
                raise ValueError(f"boundary_decision: explicit {field} required")
        if self.left_scene == self.right_scene:
            raise ValueError("boundary_decision: same-scene continuity cannot be waived by an explanation")
        if self.relation not in RELATIONS:
            raise ValueError("boundary_decision: unsupported relation")
        return f"{RELATIONS[self.relation]}：{self.basis.strip()}"
