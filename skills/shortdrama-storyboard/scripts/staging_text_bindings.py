"""Expand explicit initial-staging tokens; no free-prose or visibility inference."""
from __future__ import annotations

import re
from typing import Any

from staging_geometry import (GeometryInputError, ROUND_OFF, _direction, _heading,
                              _identifier, _object, _point, _subtract, _unit,
                              analyze_scene)

CONTRACT = "initial-staging-text-v1"
TOKEN = re.compile(r"\[\[staging:([A-Za-z0-9_-]+)\]\]")
ALLOWED_FIELDS = {"机位", "起始状态"}
SCOPE = ("Initial planar directions only; no field-of-view, offscreen, occlusion, "
         "anatomy, action, source, continuity or release certification.")


def _inside_quote(text: str, stop: int) -> bool:
    """Reject tokens in any still-open quote, including nested or partial input."""
    paired = {"“": "”", "‘": "’", "「": "」", "『": "』"}
    symmetric = {'"', "'"}
    stack = []
    escaped = False
    for char in text[:stop]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if stack and char == stack[-1]:
            stack.pop()
        elif char in paired:
            stack.append(paired[char])
        elif char in symmetric:
            stack.append(char)
    return bool(stack)


def bind_fields(fields: dict[str, Any], payload: dict) -> tuple[dict, dict]:
    """Return new fields and diagnostics from a single authored geometry record.

    Tokens are exact replacements in unquoted initial-state/camera clauses.
    All other text is preserved, including contradictions outside these tokens.
    """
    obj = _object(payload, "staging", {"contract", "geometry", "target_label", "bindings"})
    if obj["contract"] != CONTRACT:
        raise GeometryInputError("staging.contract", f"expected {CONTRACT}")
    label = _identifier(obj["target_label"], "staging.target_label")
    if re.search(r'[\[\]“”「」『』"。；;\n]', label):
        raise GeometryInputError("staging.target_label", "requires a plain target label, not a sentence")
    geometry = _object(obj["geometry"], "staging.geometry",
                       {"contract", "camera", "camera_target", "actors"}, {"screen_left_of"})
    if geometry["contract"] != "staging-geometry-v1":
        raise GeometryInputError("staging.geometry.contract", "expected staging-geometry-v1")
    camera = _point(geometry["camera"], "staging.geometry.camera")
    target = _point(geometry["camera_target"], "staging.geometry.camera_target")
    forward = _unit(_subtract(target, camera, "staging.geometry.camera_target"),
                    "staging.geometry.camera_target")
    if not isinstance(geometry["actors"], list):
        raise GeometryInputError("staging.geometry.actors", "requires an explicit array; [] means no actors")
    if geometry["actors"]:
        diagnostic = analyze_scene(geometry)
        if diagnostic["findings"]:
            raise GeometryInputError("staging.geometry", "contradictory initial geometry assertions: " +
                                     "; ".join(x["reason"] for x in diagnostic["findings"]))
    else:
        if geometry.get("screen_left_of", []) != []:
            raise GeometryInputError("staging.geometry.screen_left_of", "no actors exist for screen-order assertions")
        diagnostic = {"camera": list(camera), "camera_target": list(target),
                      "camera_forward": list(forward), "camera_right": [forward[1], -forward[0]],
                      "actors": [], "findings": []}
    actors = {a["actor_id"]: a for a in diagnostic["actors"]}
    if any(re.search(r'[\[\]“”「」『』"。；;\n]', name) for name in actors):
        raise GeometryInputError("staging.geometry.actors", "actor IDs used in prose must be plain names")
    if not isinstance(obj["bindings"], list) or not obj["bindings"]:
        raise GeometryInputError("staging.bindings", "requires explicit bindings")
    replacements, kinds, usages = {}, {}, {}

    def actor(name: str, path: str) -> dict:
        _identifier(name, path)
        if name not in actors:
            raise GeometryInputError(path, "requires an actor declared in this initial geometry")
        a = actors[name]
        if a["screen_x_normalized"] is None:
            raise GeometryInputError(path, "actor is not in the camera forward half-plane")
        return a

    for i, binding in enumerate(obj["bindings"]):
        path = f"staging.bindings[{i}]"
        if not isinstance(binding, dict):
            raise GeometryInputError(path, "requires an object")
        kind = binding.get("kind")
        extra = {"actor", "relative_to"} if kind == "screen_relation" else {"actor"} if kind == "axis_side" else set()
        _object(binding, path, {"id", "kind"} | extra)
        name = _identifier(binding["id"], path + ".id")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in replacements:
            raise GeometryInputError(path + ".id", "requires a unique ASCII token identifier")
        if kind == "camera_pose":
            bearing = _direction(_heading(forward))
            side = _direction((_heading(forward) + 180) % 360)
            value = f"摄影机位于{label}{side}侧，朝{bearing}。"
        elif kind == "screen_relation":
            a, b = actor(binding["actor"], path + ".actor"), actor(binding["relative_to"], path + ".relative_to")
            if a["actor_id"] == b["actor_id"]:
                raise GeometryInputError(path, "requires different actors")
            delta = a["screen_x_normalized"] - b["screen_x_normalized"]
            if abs(delta) <= ROUND_OFF:
                value = f"{a['actor_id']}与{b['actor_id']}处于同一画面横向位置。"
            else:
                value = f"{a['actor_id']}位于{b['actor_id']}的画面{'左' if delta < 0 else '右'}侧。"
        elif kind == "axis_side":
            a = actor(binding["actor"], path + ".actor")
            x = a["screen_x_normalized"]
            value = (f"{a['actor_id']}处于当前视轴上。" if abs(x) <= ROUND_OFF else
                     f"{a['actor_id']}位于当前视轴的画面{'左' if x < 0 else '右'}侧。")
        else:
            raise GeometryInputError(path + ".kind", "expected camera_pose, screen_relation or axis_side")
        replacements[name], kinds[name], usages[name] = value, kind, []

    result = dict(fields)
    for field, text in fields.items():
        if not isinstance(text, str):
            continue
        if "[[staging:" not in text:
            continue
        if field not in ALLOWED_FIELDS:
            raise GeometryInputError(f"fields.{field}", "initial staging tokens are only allowed in 机位 or 起始状态")
        # A token in a quotation would alter source or character text. Reject,
        # never silently strip quote marks or infer that it was an instruction.
        def replace(match):
            if _inside_quote(text, match.start()):
                raise GeometryInputError(f"fields.{field}", "staging tokens must not replace quoted or unclosed-quote text")
            name = match.group(1)
            if name not in replacements:
                raise GeometryInputError(f"fields.{field}", f"unbound staging token: {name}")
            before = text[:match.start()].rstrip(" \t\r")
            if before and before[-1] not in "。；;\n":
                raise GeometryInputError(f"fields.{field}", "a staging token must start its own affirmative clause")
            usages[name].append(field)
            return replacements[name]

        result[field] = TOKEN.sub(replace, text)
        if "[[staging:" in result[field]:
            raise GeometryInputError(f"fields.{field}", "malformed or recursively generated staging token")
    if any(not places for places in usages.values()):
        raise GeometryInputError("staging.bindings", "every binding must be used in the actual initial fields")
    if not any(kinds[name] == "camera_pose" and "机位" in places for name, places in usages.items()):
        raise GeometryInputError("fields.机位", "requires an actual camera_pose binding in 机位")
    return result, {"contract": CONTRACT, "scope": SCOPE, "geometry": diagnostic,
                    "rendered_bindings": replacements, "used_in_fields": usages,
                    "semantic_accuracy_percent": None, "commit_eligible": False}
