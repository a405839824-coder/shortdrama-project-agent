"""Compute explicit 2D staging facts; never infer poses or certify a storyboard.

Coordinates are [x east, y north], in one caller-selected common unit. Yaw is
clockwise from north: 0 north, 90 east. No camera roll or image mirroring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

CONTRACT = "staging-geometry-v1"
SCOPE = ("Planar geometry only; no proof of framing, occlusion, lens/filmback, "
         "depth of field, visible eye detail, neck comfort, source entailment or actual action.")
DIRECTIONS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
ROUND_OFF = 1e-12


class GeometryInputError(ValueError):
    def __init__(self, path: str, reason: str):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _number(value: Any, path: str) -> float:
    if type(value) not in (int, float):
        raise GeometryInputError(path, "requires a finite number, not boolean/string/null")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise GeometryInputError(path, "number is outside finite arithmetic range") from None
    if not math.isfinite(result):
        raise GeometryInputError(path, "requires a finite number")
    return result


def _point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise GeometryInputError(path, "requires [xEast, yNorth]")
    return (_number(value[0], path + "[0]"), _number(value[1], path + "[1]"))


def _yaw(value: Any, path: str) -> float:
    result = _number(value, path)
    if not 0 <= result < 360:
        raise GeometryInputError(path, "yaw must be in [0, 360); no silent wrapping")
    return result


def _object(value: Any, path: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise GeometryInputError(path, "requires an object")
    missing, unknown = required - value.keys(), value.keys() - required - optional
    if missing:
        raise GeometryInputError(path, "missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise GeometryInputError(path, "unknown fields: " + ", ".join(sorted(unknown)))
    return value


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(c) < 32 for c in value):
        raise GeometryInputError(path, "requires a nonempty, unpadded identifier without controls")
    return value


def _subtract(a: tuple[float, float], b: tuple[float, float], path: str) -> tuple[float, float]:
    result = (a[0] - b[0], a[1] - b[1])
    if not all(math.isfinite(v) for v in result):
        raise GeometryInputError(path, "coordinate difference exceeds finite arithmetic range")
    return result


def _unit(vector: tuple[float, float], path: str) -> tuple[float, float]:
    scale = max(abs(component) for component in vector)
    if scale == 0:
        raise GeometryInputError(path, "coincident points give an undefined direction")
    # Normalize at unit scale: hypot of subnormal coordinates can round to a
    # single component and would otherwise yield a vector such as (1, 1).
    scaled = (vector[0] / scale, vector[1] / scale)
    length = math.hypot(*scaled)
    return (scaled[0] / length, scaled[1] / length)


def _dot(a: tuple[float, float], b: tuple[float, float], path: str) -> float:
    value = a[0] * b[0] + a[1] * b[1]
    if not math.isfinite(value):
        raise GeometryInputError(path, "projection exceeds finite arithmetic range")
    scale_a, scale_b = max(map(abs, a)), max(map(abs, b))
    scaled_value = (0.0 if not scale_a or not scale_b else
                    (a[0] / scale_a) * (b[0] / scale_b) +
                    (a[1] / scale_a) * (b[1] / scale_b))
    underflowed_zero = (value == 0 and scaled_value != 0 and
                       math.log(abs(scaled_value)) + math.log(scale_a) + math.log(scale_b)
                       < math.log(sys.float_info.min))
    if 0 < abs(value) < sys.float_info.min or underflowed_zero:
        raise GeometryInputError(
            path, "projection falls below stable arithmetic range; rescale the common coordinate unit"
        )
    return value


def _heading(vector: tuple[float, float]) -> float:
    return math.degrees(math.atan2(vector[0], vector[1])) % 360


def _direction(yaw: float) -> str:
    # At an exact halfway bearing choose the clockwise octant.
    return DIRECTIONS[int(math.floor((yaw + 22.5) / 45)) % 8]


def _signed_delta(a: float, b: float) -> float:
    """Clockwise change from b to a; a half-turn is returned as -180."""
    return (a - b + 180) % 360 - 180


def analyze_scene(payload: dict) -> dict:
    """Validate authored inputs, return derived facts without changing the input.

    A nonnegative head-to-camera cosine does NOT certify a clear face. A
    negative cosine does contradict an explicit clear_face=True assertion in
    this planar model. A profile exactly at 90 degrees is left unproven.
    """
    obj = _object(payload, "$", {"contract", "camera", "camera_target", "actors"}, {"screen_left_of"})
    if obj["contract"] != CONTRACT:
        raise GeometryInputError("$.contract", f"expected {CONTRACT}")
    camera = _point(obj["camera"], "$.camera")
    target = _point(obj["camera_target"], "$.camera_target")
    forward = _unit(_subtract(target, camera, "$.camera_target"), "$.camera_target")
    right = (forward[1], -forward[0])
    if not isinstance(obj["actors"], list) or not obj["actors"]:
        raise GeometryInputError("$.actors", "requires a nonempty array of explicit actor decisions")

    findings, actors, by_id = [], [], {}
    for index, value in enumerate(obj["actors"]):
        path = f"$.actors[{index}]"
        actor = _object(value, path, {"actor_id", "actor_position", "body_yaw", "head_yaw", "gaze_target"}, {"clear_face"})
        name = _identifier(actor["actor_id"], path + ".actor_id")
        if name in by_id:
            raise GeometryInputError(path + ".actor_id", "duplicate actor ID")
        position = _point(actor["actor_position"], path + ".actor_position")
        body_yaw, head_yaw = _yaw(actor["body_yaw"], path + ".body_yaw"), _yaw(actor["head_yaw"], path + ".head_yaw")
        gaze_target = _point(actor["gaze_target"], path + ".gaze_target")
        if "clear_face" in actor and type(actor["clear_face"]) is not bool:
            raise GeometryInputError(path + ".clear_face", "requires an explicit boolean")
        to_actor = _subtract(position, camera, path + ".actor_position")
        to_camera = _unit(_subtract(camera, position, path + ".actor_position"), path + ".actor_position")
        gaze_vector = _unit(_subtract(gaze_target, position, path + ".gaze_target"), path + ".gaze_target")
        gaze_yaw = _heading(gaze_vector)
        depth = _dot(to_actor, forward, path)
        horizontal = _dot(to_actor, right, path)
        screen_x = horizontal / depth if depth > 0 else None
        if screen_x is not None and not math.isfinite(screen_x):
            raise GeometryInputError(path, "perspective division exceeds finite arithmetic range")
        head_vector = (math.sin(math.radians(head_yaw)), math.cos(math.radians(head_yaw)))
        cosine = max(-1.0, min(1.0, _dot(head_vector, to_camera, path)))
        angle = math.degrees(math.acos(cosine))
        clear_face = actor.get("clear_face")
        contradictions = []
        if clear_face is True:
            if depth <= 0:
                contradictions.append("actor_not_in_camera_forward_half_plane")
            if cosine < -ROUND_OFF:
                contradictions.append("head_front_points_away_from_camera")
            for reason in contradictions:
                findings.append({"kind": "clear_face_contradiction", "actor_id": name, "reason": reason})
        result = {
            "actor_id": name,
            "actor_position": list(position),
            "body_yaw": body_yaw, "body_direction": _direction(body_yaw),
            "head_yaw": head_yaw, "head_direction": _direction(head_yaw),
            "gaze_yaw": gaze_yaw, "gaze_world_direction": _direction(gaze_yaw),
            "head_to_camera_angle_degrees": angle,
            "camera_world_direction_from_actor": _direction(_heading(to_camera)),
            "head_minus_body_degrees": _signed_delta(head_yaw, body_yaw),
            "gaze_minus_head_degrees": _signed_delta(gaze_yaw, head_yaw),
            "camera_depth": depth, "screen_x_world": horizontal,
            "screen_x_normalized": screen_x,
            "screen_side_of_optical_axis": ("undefined_not_forward" if screen_x is None else
                                             "center" if abs(screen_x) <= ROUND_OFF else
                                             "left" if screen_x < 0 else "right"),
            "clear_face_claim": clear_face,
            "clear_face_claim_check": ("contradiction" if contradictions else
                                       "not_disproved_by_2d_geometry" if clear_face is True else
                                       "not_asserted"),
        }
        actors.append(result)
        by_id[name] = result

    constraints = obj.get("screen_left_of", [])
    if not isinstance(constraints, list):
        raise GeometryInputError("$.screen_left_of", "requires an array")
    pair_results, pairs = [], set()
    for index, value in enumerate(constraints):
        path = f"$.screen_left_of[{index}]"
        pair = _object(value, path, {"left", "right"})
        left, right_id = _identifier(pair["left"], path + ".left"), _identifier(pair["right"], path + ".right")
        if left == right_id or left not in by_id or right_id not in by_id:
            raise GeometryInputError(path, "requires two different, declared actor IDs")
        if (left, right_id) in pairs:
            raise GeometryInputError(path, "duplicate left/right assertion")
        pairs.add((left, right_id))
        a, b = by_id[left]["screen_x_normalized"], by_id[right_id]["screen_x_normalized"]
        if a is None or b is None:
            check, reason = "contradiction", "asserted_actor_not_in_camera_forward_half_plane"
        elif a < b - ROUND_OFF:
            check, reason = "consistent_2d_order_only", None
        else:
            check, reason = "contradiction", "asserted_left_actor_is_not_left_of_right_actor"
        pair_results.append({"left": left, "right": right_id, "check": check,
                             "left_screen_x": a, "right_screen_x": b})
        if reason:
            findings.append({"kind": "screen_order_contradiction", "left": left, "right": right_id, "reason": reason})
    return {"contract": CONTRACT, "status": "contradiction_found" if findings else "no_2d_contradiction_found",
            "scope": SCOPE, "commit_eligible": False,
            "camera": list(camera), "camera_target": list(target),
            "camera_forward": list(forward), "camera_right": list(right),
            "actors": actors, "screen_left_of_checks": pair_results, "findings": findings}


def _reject_constant(value: str):
    raise GeometryInputError("$", f"non-finite JSON constant {value} is not allowed")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise GeometryInputError("$", f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="explicit staging-geometry-v1 JSON decisions")
    parser.add_argument("--output", type=Path, help="new diagnostic file; refuses an existing path")
    args = parser.parse_args(argv)
    try:
        raw = args.input.read_bytes()
    except OSError as exc:
        print(f"Cannot read input: {exc}", file=sys.stderr)
        return 2
    input_hash = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        report = analyze_scene(payload)
    except (GeometryInputError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report = {"contract": CONTRACT, "status": "invalid_input", "scope": SCOPE,
                  "commit_eligible": False,
                  "findings": [{"kind": "invalid_input", "path": getattr(exc, "path", "$"), "reason": str(exc)}]}
    report["input_sha256"] = input_hash
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
        except OSError as exc:
            print(f"Cannot create a new diagnostic output: {exc}", file=sys.stderr)
            return 2
    else:
        print(serialized, end="")
    return 0 if report["status"] == "no_2d_contradiction_found" else 2


if __name__ == "__main__":
    raise SystemExit(main())
