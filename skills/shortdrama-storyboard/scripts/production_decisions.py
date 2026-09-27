"""Lower one action/state model to typed v2. No audit or release certificate.

The fixed-camera model covers declared landmarks and vertical opaque barriers.
It deliberately refuses unsupported effects instead of accepting free endpoint,
phase, visibility or event-basis prose. Production attempts belong to record_attempt.
"""
from __future__ import annotations

import copy
import math
import re
from bisect import bisect_right

from authoring_contract_blocks import eye_blocks, scene_lighting_blocks, transition_block, voice_block, psychological_safety_suffix, ContractBlockError
from boundary_decisions import BoundaryDecision
from production_state import ProductionStateChain, CONTRACT as STATE_CONTRACT
from staging_geometry import _direction, screen_direction
from field_content import world_position_matches
from timing_model import (estimate_utterance, normalize_pauses, segment_pauses, predicted_character_interval,
                          resolve_profile as timing_profile, _rules as timing_rules)
from production_environment import (METADATA_FIELDS, validate_editorial,
                                    render_environment, normalize_openings, opening_states, EnvironmentDecisionError)
import production_actions as extended_actions

CONTRACT = "production-decisions-v1"
SCOPE = ("0.1-second declared landmark simulation; fixed perspective camera and known vertical barriers; "
         "not complete body/mesh occlusion, natural performance, source entailment or independent approval")
EDITORIAL = {"功能类型", "环境音效", "环境动态", "参考图锚定", "原文依据类型", "原文支撑句"}
COMMON = {"id", "kind", "start", "end", "actor", "after", "source_use_id"}
ACTION_FIELDS = {
    "hold": set(), "move_actor": {"to"}, "move_prop": {"prop", "hand", "to"},
    "turn": {"body_yaw", "head_yaw"}, "look": {"target", "tracking", "head_mode"},
    "point": {"hand", "target", "finger_to"}, "blink": set(),
    "transfer": {"prop", "to_actor", "to_hand"}, "speak": {"voice"},
    "write": {"paper_ref", "hand", "tool"}, "narrate": set(), "inner_voice": set(),
    "eyelid": {"to"}, "breath": {"phase"}, "focus_adjust": set(),
    "blink_restraint": {"reason", "utterance_id", "char_range"},
}
ACTION_FIELDS.update(extended_actions.FIELDS)
CONTINUOUS = {"hold", "move_actor", "move_prop", "turn", "look", "point", "eyelid", "breath", "focus_adjust", "blink_restraint"}
CONTINUOUS |= extended_actions.CONTINUOUS
EYE_ACTIONS = {"blink", "eyelid", "focus_adjust", "blink_restraint"}
RESTRAINT_REASONS = {"emphasis": "重音", "decisive_tail": "决定性尾音", "high_pressure": "高压", "shock": "震惊", "recognition": "认出", "vigilance": "警觉"}
EPS = 1e-8


def _action_keys(kind, action=None):
    optional = {"timing", "pauses"} & set(action) if kind == "speak" and isinstance(action, dict) else set()
    return (COMMON - {"actor"} if kind in {"speak", "narrate", "inner_voice"} else COMMON) | ACTION_FIELDS[kind] | optional


class ProductionDecisionError(ValueError):
    def __init__(self, path: str, reason: str):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _need(ok: bool, path: str, reason: str):
    if not ok:
        raise ProductionDecisionError(path, reason)


def _obj(value, keys, path):
    _need(isinstance(value, dict) and set(value) == set(keys), path,
          "requires exactly " + ", ".join(sorted(keys)))
    return value


def _name(value, path):
    _need(isinstance(value, str) and bool(re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9_-]{1,64}", value)),
          path, "requires a plain stable identity, not prose or an unresolved placeholder")
    _need(value not in {"未知", "待定", "unknown", "unresolved"}, path, "unresolved executable identity")
    return value


def _text(value, path):
    _need(isinstance(value, str) and bool(value.strip()), path, "requires explicit text")
    return value


def _editorial(value, path):
    try:
        validate_editorial(value, path)
    except EnvironmentDecisionError as exc:
        raise ProductionDecisionError(exc.path, exc.reason) from exc


def _num(value, path):
    _need(isinstance(value, (int, float)) and not isinstance(value, bool), path, "requires finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ProductionDecisionError(path, "number outside finite range") from None
    _need(math.isfinite(value) and abs(value) < 1e6, path, "coordinate/angle outside supported finite range")
    return value


def _xyz(value, path):
    _need(isinstance(value, list) and len(value) == 3, path, "requires [east, north, height] metres")
    return [_num(x, path) for x in value]


def _yaw(value, path):
    value = _num(value, path)
    _need(0 <= value < 360, path, "yaw must be in [0,360)")
    return value


def _tick(value, path):
    _need(type(value) is int and 0 <= value <= 36000, path, "requires integer tenths in [0,36000]")
    return value


def _hash(value):
    from production_derivation import digest
    return digest(value)


def _add(a, b): return [x + y for x, y in zip(a, b)]
def _sub(a, b): return [x - y for x, y in zip(a, b)]
def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _length(a): return math.sqrt(_dot(a, a))
def _distance(a, b): return _length(_sub(a, b))
def _lerp(a, b, f): return [x + (y - x) * f for x, y in zip(a, b)]
def _delta(a, b): return (a - b + 180) % 360 - 180
def _bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def _unit(a, path):
    length = _length(a)
    _need(length > EPS, path, "coincident points have no direction")
    return [v / length for v in a]


def _eye(actor):
    return _add(actor["position_xyz"], [0, 0, actor["eye_height"]])


def _hand(actor, hand):
    local = actor["hands"][hand]
    angle = math.radians(actor["body_yaw"])
    return _add(actor["position_xyz"], [local[0] * math.cos(angle) + local[1] * math.sin(angle),
                                      -local[0] * math.sin(angle) + local[1] * math.cos(angle), local[2]])


def _set_hand(actor, hand, position):
    v = _sub(position, actor["position_xyz"]); angle = math.radians(actor["body_yaw"])
    actor["hands"][hand] = [v[0] * math.cos(angle) - v[1] * math.sin(angle),
                             v[0] * math.sin(angle) + v[1] * math.cos(angle), v[2]]


def _rotated_origin(before, body_yaw):
    """Rigidly rotate the explicit reach origin; never infer it from eye height."""
    v = _sub(before["reach_origin_xyz"], before["position_xyz"])
    angle = math.radians(_delta(body_yaw, before["body_yaw"]))
    return _add(before["position_xyz"], [v[0] * math.cos(angle) + v[1] * math.sin(angle),
                                         -v[0] * math.sin(angle) + v[1] * math.cos(angle), v[2]])


def _anchor(world, prop, seen=None):
    anchor = prop["anchor"]
    if set(anchor)=={'prop','offset_xyz'}:
        seen=set() if seen is None else set(seen)
        _need(len(seen)<16,'prop.anchor','supported prop chain exceeds finite depth')
        name=anchor['prop']
        _need(name in world['props'] and name not in seen,'prop.anchor','unknown or cyclic supported prop')
        seen.add(name)
        carrier=world['props'][name]
        _need(carrier.get('physical',{}).get('integrity','intact')=='intact',
              'prop.anchor','supported prop cannot remain on a broken carrier')
        scene,position=_anchor(world,carrier,seen)
        return scene,_add(position,anchor['offset_xyz'])
    if set(anchor) == {"free"}:
        return anchor['free']['scene_id'], anchor['free']['position_xyz']
    if set(anchor) == {"point"}:
        point = world["points"][anchor["point"]]
        return point["scene_id"], point["position_xyz"]
    actor = world["actors"][anchor["actor"]]
    if 'worn' in anchor:
        return actor['scene_id'], extended_actions.body_point(actor, anchor['worn'])
    if 'binding' in anchor:
        return actor['scene_id'],_lerp(_hand(actor,'left'),_hand(actor,'right'),.5)
    return actor["scene_id"], _hand(actor, anchor["hand"])


def _target(world, ref, path):
    _obj(ref, {"kind", "id"}, path)
    kind = ref["kind"]; identity = _name(ref["id"], path + ".id")
    _need(kind in {"actor", "prop", "point"}, path, "unknown target kind")
    registry = world[{"actor": "actors", "prop": "props", "point": "points"}[kind]]
    _need(identity in registry, path, "target identity does not exist")
    if kind == "prop": return _anchor(world, registry[identity])
    row = registry[identity]
    return row["scene_id"], _eye(row) if kind == "actor" else row["position_xyz"]


def _blocked(world, scene, a, b):
    """Closed line-segment intersection with a known vertical opaque barrier."""
    for wall in world["scenes"][scene]["barriers"]:
        c, d = wall["a"], wall["b"]
        r, s = _sub(b, a), _sub(d, c)
        cross = r[0] * s[1] - r[1] * s[0]
        if abs(cross) <= EPS:
            # Collinear sight/path is not certified as clear.
            if abs((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) <= EPS:
                if max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) + EPS and \
                   max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1])) + EPS:
                    if min(a[2], b[2]) <= wall["height_m"]: return wall["id"]
            continue
        t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / cross
        u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / cross
        if -EPS <= t <= 1 + EPS and -EPS <= u <= 1 + EPS:
            z = a[2] + t * (b[2] - a[2])
            if 0 <= z <= wall["height_m"]: return wall["id"]
    return None


def _camera(world, camera, scene, path):
    _obj(camera, {"position_xyz", "target", "hfov_deg", "vfov_deg", "frame"}, path)
    origin = _xyz(camera["position_xyz"], path + ".position_xyz")
    target_scene, target = _target(world, {"kind": "point", "id": camera["target"]}, path + ".target")
    _need(scene == target_scene, path, "camera target belongs to another physical scene")
    forward = _unit(_sub(target, origin), path)
    right = _unit([forward[1], -forward[0], 0], path)
    up = [right[1] * forward[2] - right[2] * forward[1],
          right[2] * forward[0] - right[0] * forward[2],
          right[0] * forward[1] - right[1] * forward[0]]
    hfov = _num(camera["hfov_deg"], path); vfov = _num(camera["vfov_deg"], path)
    _need(1 <= hfov <= 150 and 1 <= vfov <= 150, path, "supported FOV is 1–150 degrees")
    from production_plan import shot_policy
    _need(camera["frame"] in shot_policy()['framing'], path, "unsupported framing type")
    return {"origin": origin, "target": list(target), "forward": forward, "right": right, "up": up,
            "hx": math.tan(math.radians(hfov / 2)), "vy": math.tan(math.radians(vfov / 2))}


def _in_frame(world, scene, camera, position):
    v = _sub(position, camera["origin"]); depth = _dot(v, camera["forward"])
    return (depth > EPS and abs(_dot(v, camera["right"]) / depth) <= camera["hx"] + EPS
            and abs(_dot(v, camera["up"]) / depth) <= camera["vy"] + EPS
            and not _blocked(world, scene, camera["origin"], position))


def _view(world, camera, scene):
    actors, props = {}, []
    for identity, actor in world["actors"].items():
        if actor["scene_id"] != scene: continue
        eye = _eye(actor); hands = [h for h in ("left", "right") if _in_frame(world, scene, camera, _hand(actor, h))]
        head_in_frame = _in_frame(world, scene, camera, eye)
        front = _dot([math.sin(math.radians(actor["head_yaw"])), math.cos(math.radians(actor["head_yaw"])), 0],
                     _unit(_sub(camera["origin"], eye), "camera/eye")) > 0.2
        feet = _in_frame(world, scene, camera, actor["position_xyz"])
        if head_in_frame or hands or feet:
            actors[identity] = {"eye_visible": head_in_frame and front, "head": head_in_frame,
                                "hands": hands, "feet": feet, "front": front}
    for identity, prop in world["props"].items():
        ps, position = _anchor(world, prop)
        if ps == scene and _in_frame(world, scene, camera, position): props.append(identity)
    _need(len(actors) <= 3, "camera.visible_actors",
          "at most three identifiable main actors may be visible; reframe or split the shot, preserve world actors")
    return {"actors": actors, "props": props}


def _visible_intervals(samples, actor, start, end, *, require_face=True):
    """Intersections of the declared fixed-camera clear-face landmark model."""
    intervals = []; opened = None
    for tick in range(start, end):
        visible = (samples[tick]["view"]["actors"].get(actor, {}).get("eye_visible", False) if require_face
                   else actor in samples[tick]["view"]["actors"])
        if visible and opened is None: opened = tick
        if not visible and opened is not None: intervals.append((opened, tick)); opened = None
    if opened is not None: intervals.append((opened, end))
    return intervals


def _check_gazes(world, path):
    for identity, actor in world["actors"].items():
        gaze = actor["gaze"]
        if gaze is None: continue
        scene, actual = _target(world, gaze["ref"], path + "." + identity)
        _need(scene == actor["scene_id"], path + "." + identity, "gaze crosses physical scenes")
        _need(_distance(actual, gaze["position_xyz"]) <= EPS, path + "." + identity,
              "stale named target; explicit tracking look or clear is required")
        _need(actor["eye_state"] != "closed", path + "." + identity, "closed eyes cannot hold a current visual target")
        _need(not _blocked(world, scene, _eye(actor), actual), path + "." + identity, "known opaque barrier blocks gaze")
        horizontal = math.hypot(actual[0] - _eye(actor)[0], actual[1] - _eye(actor)[1])
        if horizontal > EPS:
            _need(abs(_delta(_bearing(_eye(actor), actual), actor["head_yaw"])) <= 60 + EPS,
                  path + "." + identity, "gaze endpoint lies behind the allowed head direction")


def _speaking_gaze(samples, action, camera, path, *, required):
    """Check the actual eye-to-target ray against the eye-to-camera ray.

    This is a declared landmark check, not an estimate of the final rendered
    eyes. Hidden/closed eyes impose no invented gaze or mouth performance.
    """
    from field_content import field_policy
    cone = field_policy()['speaking_gaze_policy']['camera_cone_degrees']
    angles = []; unknown = []
    for tick in range(action['start'], action['end']):
        sample = samples[tick]; actor = sample['world']['actors'][action['actor']]
        if not sample['view']['actors'].get(action['actor'], {}).get('eye_visible') or actor['eye_state'] == 'closed':
            continue
        if actor['gaze'] is None:
            unknown.append(tick); continue
        eye = _eye(actor)
        target = _unit(_sub(actor['gaze']['position_xyz'], eye), path)
        lens = _unit(_sub(camera['origin'], eye), path)
        angles.append(math.degrees(math.acos(max(-1., min(1., _dot(target, lens))))))
    status = 'insufficient_evidence' if unknown else 'fail' if angles and min(angles) <= cone else 'pass'
    if required:
        _need(status == 'pass', path,
              'speaking_gaze_' + status + ': visible speaking eyes require an explicit target outside the registered camera cone')
    return {'contract': 'speaking-gaze-camera-v1', 'status': status,
            'camera_cone_degrees': cone, 'checked_ticks': len(angles),
            'unknown_ticks': unknown, 'minimum_angle_degrees': min(angles) if angles else None,
            'scope': 'declared open-eye landmarks during actual speech; not rendered-eye verification'}


def _source(source_compilation):
    if source_compilation is None: return {"uses_by_id": {}, "written_objects_by_id": {}}
    # A caller-supplied dictionary would permit bypassing the source compiler.
    from source_decisions import SourceCompilation
    _need(isinstance(source_compilation, SourceCompilation), "source_compilation", "requires real SourceCompilation")
    return source_compilation.to_dict()


def _source_action_call(function, *args, **kwargs):
    from source_action_plans import SourceActionPlanError
    try:
        return function(*args, **kwargs)
    except SourceActionPlanError as exc:
        raise ProductionDecisionError(exc.path, exc.reason) from exc


def _production_plan_call(function, *args):
    from production_plan import ProductionPlanError
    try:
        return function(*args)
    except ProductionPlanError as exc:
        error = ProductionDecisionError(exc.path, exc.reason)
        error.findings = exc.findings
        error.status = exc.status
        raise error from exc


def _apply_source_action(world, action, info, tick, path, execution):
    # Observe this one actual effect application, not a separately replayed
    # expected world and not another hand-authored endpoint.
    before = copy.deepcopy(world) if execution.is_bound(action["id"]) else None
    staging_before = execution.staging_snapshot(action, world)
    _apply(world, action, info, tick, path)
    if before is not None:
        _source_action_call(execution.observe, action, before, world, tick, path)
    _source_action_call(execution.observe_staging, action, staging_before, world, path)


def _paper(sources, ref, path, *, current):
    _need(ref in sources["written_objects_by_id"], path, "unknown compiled paper reference")
    paper = sources["written_objects_by_id"][ref]
    state = "current" if current else "planned"
    _need(paper["state"] == state, path, f"paper must be {state} at this operation")
    if current: _need(paper["at"] == "frame0", path, "future paper cannot enter the initial frame")
    return paper


def _light_source(light):
    """The source is a finite position indoors and a world direction outdoors."""
    outdoor = light.get('kind') == 'outdoor_daylight'
    key = 'direction_xyz' if outdoor else 'position_xyz'
    _obj(light, {'period', 'kind', 'name', 'state', key, 'main_tone', 'shadow_tone', 'materials'}, 'scene.light')
    _name(light['name'], 'scene.light.name')
    if outdoor:
        _need(isinstance(light[key], list) and len(light[key]) == 3, 'scene.light.direction_xyz',
              'requires [east, north, up] direction toward daylight, not a source position')
        vector = [_num(x, 'scene.light.direction_xyz') for x in light[key]]
    else:
        vector = _xyz(light[key], 'scene.light.position_xyz')
    _need(math.hypot(*vector[:2]) > EPS, 'scene.light',
          'light world direction undefined at the common scene origin')
    source = {k: light[k] for k in ('period', 'kind', 'name', 'state')}
    source['world_side'] = _direction(_bearing([0, 0, 0], vector))
    source['elevation_deg' if outdoor else 'height_m'] = (
        math.degrees(math.atan2(vector[2], math.hypot(*vector[:2]))) if outdoor else vector[2])
    return source, vector


def _validate_scene_light(light, label):
    source, _ = _light_source(light)
    if light['kind'] == 'outdoor_daylight':
        from lighting_checks import classify_scene
        _need(classify_scene({'空间站位描述': label})['space_type'] != 'indoor', 'scene.light',
              'outdoor daylight cannot be assigned to an indoor scene')
    try:
        scene_lighting_blocks(source=source, screen_side='正前', receiving_surfaces=['人物面部'],
            weak_reflector=None, main_tone=light['main_tone'], shadow_tone=light['shadow_tone'],
            materials=light['materials'], air='clear', exposure='normal')
    except ContractBlockError as exc:
        raise ProductionDecisionError('scene.light', str(exc)) from exc


def _world(value, sources, *, check_source_refs=True):
    _obj(value, {"scenes", "actors", "props", "points"}, "initial_world")
    w = copy.deepcopy(value)
    for key in w: _need(isinstance(w[key], dict), "initial_world." + key, "requires identity registry")
    names = list(w["actors"]) + list(w["props"]) + list(w["points"])
    _need(len(set(names)) == len(names), "initial_world", "actor/prop/point identity collision")
    _need(bool(w["scenes"]), "initial_world.scenes", "requires a physical scene")
    for sid, scene in w["scenes"].items():
        _name(sid, "scene.id")
        _obj(scene, {"label", "door_window_state", "light", "barriers"} | ({'passages'} if 'passages' in scene else set()) | ({'opening_labels'} if 'opening_labels' in scene else set()), "scene." + sid)
        if 'passages' in scene: _need(isinstance(scene['passages'], list), 'scene.passages', 'requires explicit passage array')
        _name(scene["label"], "scene.label")
        if 'opening_labels' in scene:
            _need(isinstance(scene['opening_labels'], dict), 'scene.opening_labels', 'requires opening ID to display name mapping')
            for identity, label in scene['opening_labels'].items():
                _name(identity, 'scene.opening_labels.id'); _display_name(label, 'scene.opening_labels.' + identity)
        light = scene["light"]
        _need(isinstance(light, dict), 'scene.light', 'requires an explicit light object')
        if light.get('kind') == 'outdoor_daylight':
            _validate_scene_light(light, scene['label'])
        else:
            # Retain legacy validation timing for indoor scenes not yet filmed.
            _obj(light, {"period", "kind", "name", "state", "position_xyz", "main_tone", "shadow_tone", "materials"}, "scene.light")
            _name(light["name"], "scene.light.name")
            light["position_xyz"] = _xyz(light["position_xyz"], "scene.light.position_xyz")
        try:
            scene["door_window_state"] = normalize_openings(scene["door_window_state"], light, "scene." + sid + ".door_window_state")
        except EnvironmentDecisionError as exc:
            raise ProductionDecisionError(exc.path, exc.reason) from exc
        _need(isinstance(scene["barriers"], list), "scene.barriers", "requires explicit array; [] declares no known barriers")
        seen = set()
        for wall in scene["barriers"]:
            _obj(wall, {"id", "a", "b", "height_m"}, "scene.barrier")
            _name(wall["id"], "wall.id"); _need(wall["id"] not in seen, "wall.id", "duplicate wall")
            seen.add(wall["id"])
            wall["a"] = _xyz(wall["a"], "wall.a"); wall["b"] = _xyz(wall["b"], "wall.b")
            wall["height_m"] = _num(wall["height_m"], "wall.height_m")
            _need(wall["a"][2] == wall["b"][2] == 0 and wall["height_m"] > 0 and _distance(wall["a"], wall["b"]) > EPS,
                  "wall", "requires a nonzero ground segment and positive opaque height")
    for identity, point in w["points"].items():
        _name(identity, "point.id"); _obj(point, {"scene_id", "position_xyz"} | ({"display_name"} if "display_name" in point else set()), "point." + identity)
        _need(point["scene_id"] in w["scenes"], "point.scene_id", "unknown physical scene")
        point["position_xyz"] = _xyz(point["position_xyz"], "point.position_xyz")
        if 'display_name' in point: _display_name(point['display_name'], 'point.display_name')
    for identity, actor in w["actors"].items():
        _name(identity, "actor.id")
        keys = {"scene_id", "position_xyz", "body_yaw", "head_yaw", "eye_height", "eye_state", "posture", "hands", "gaze", "reach_m", "reach_origin_xyz"}
        _obj(actor, keys | ({"performance_state"} if "performance_state" in actor else set()) | ({'action_state'} if 'action_state' in actor else set()), "actor." + identity)
        performance = actor.setdefault("performance_state", {"eyelid": "unknown", "breath": "unknown"})
        _obj(performance, {"eyelid", "breath"}, "actor.performance_state")
        _need(performance["eyelid"] in {"unknown", "relaxed", "slightly_tense"} and performance["breath"] in {"unknown", "resting", "inhale", "exhale"},
              "actor.performance_state", "unsupported explicit performance state")
        _need(actor["scene_id"] in w["scenes"], "actor.scene_id", "unknown physical scene")
        actor["position_xyz"] = _xyz(actor["position_xyz"], "actor.position_xyz")
        actor["reach_origin_xyz"] = _xyz(actor["reach_origin_xyz"], "actor.reach_origin_xyz")
        actor["body_yaw"] = _yaw(actor["body_yaw"], "actor.body_yaw"); actor["head_yaw"] = _yaw(actor["head_yaw"], "actor.head_yaw")
        _need(abs(_delta(actor["head_yaw"], actor["body_yaw"])) <= 105, "actor.head_yaw", "head/body relation outside supported model")
        actor["eye_height"] = _num(actor["eye_height"], "actor.eye_height")
        actor["reach_m"] = _num(actor["reach_m"], "actor.reach_m")
        _need(actor["eye_height"] > 0 and 0 < actor["reach_m"] <= 2, "actor", "explicit positive eye height and bounded reach required")
        _need(actor["eye_state"] in {"natural_open", "closed"}, "actor.eye_state", "unsupported actual eye state")
        _need(actor["posture"] in extended_actions.POSTURES, "actor.posture", "unsupported posture")
        _obj(actor["hands"], {"left", "right"}, "actor.hands")
        for hand in actor["hands"]: actor["hands"][hand] = _xyz(actor["hands"][hand], "actor.hands." + hand)
    occupied = set()
    for identity, prop in w["props"].items():
        _name(identity, "prop.id"); _obj(prop, {"anchor", "written_ref"} | ({'physical'} if 'physical' in prop else set()) | ({'display_name'} if 'display_name' in prop else set()), "prop." + identity)
        if 'display_name' in prop: _display_name(prop['display_name'], 'prop.display_name')
        anchor = prop["anchor"]
        if isinstance(anchor, dict) and set(anchor) == {"point"}:
            _need(anchor["point"] in w["points"], "prop.anchor", "unknown anchor point")
        elif isinstance(anchor,dict) and set(anchor)=={'prop','offset_xyz'}:
            _need(isinstance(anchor['prop'],str) and anchor['prop'] in w['props'] and
                  anchor['prop']!=identity,'prop.anchor','unknown or self-supported prop')
            offset=_xyz(anchor['offset_xyz'],'prop.anchor.offset_xyz')
            _need(math.hypot(*offset[:2])<=.3 and 0<=offset[2]<=.3,
                  'prop.anchor.offset_xyz','supported prop needs a bounded surface offset')
        elif isinstance(anchor, dict) and set(anchor) == {'actor', 'worn'}:
            _need(anchor['actor'] in w['actors'] and anchor['worn'] in {'head', 'torso'}, 'prop.anchor', 'unknown worn actor/attachment')
            key=(anchor['actor'],'worn:'+anchor['worn'])
            _need(key not in occupied, 'prop.anchor', 'two props claim one worn attachment')
            occupied.add(key)
        elif isinstance(anchor,dict) and set(anchor)=={'actor','binding'}:
            _need(isinstance(anchor['actor'],str) and anchor['actor'] in w['actors'] and anchor['binding']=='wrists','prop.anchor','unsupported binding')
            key=(anchor['actor'],'binding:wrists')
            _need(key not in occupied,'prop.anchor','two bindings claim same wrists');occupied.add(key)
        else:
            _obj(anchor, {"actor", "hand"}, "prop.anchor")
            _need(anchor["actor"] in w["actors"] and anchor["hand"] in {"left", "right"}, "prop.anchor", "unknown holding actor/hand")
            key = (anchor["actor"], anchor["hand"])
            _need(key not in occupied, "prop.anchor", "two props claim one hand")
            occupied.add(key)
        if prop["written_ref"] is not None:
            if check_source_refs:
                paper = _paper(sources, prop["written_ref"], "prop.written_ref", current=True)
                _need(paper["carrier_id"] == identity, "prop.written_ref", "paper carrier identity mismatch")
            else:
                _text(prop["written_ref"], "prop.written_ref")
    extended_actions.defaults(w)
    extended_actions.validate_world(w)
    for prop in w['props'].values(): _anchor(w,prop)
    for identity, actor in w["actors"].items():
        if actor["gaze"] is not None:
            ref = actor["gaze"]; _, pos = _target(w, ref, "actor.gaze")
            actor["gaze"] = {"ref": copy.deepcopy(ref), "position_xyz": list(pos)}
    _check_gazes(w, "initial_world.gazes")
    w["completed"] = []
    return w


def _state(world):
    positions, facing, gaze = {}, {}, {}
    for identity, actor in world["actors"].items():
        positions[identity] = {"scene_id": actor["scene_id"], "position_xyz": actor["position_xyz"], "posture": actor["posture"]}
        facing[identity] = {"body_yaw": actor["body_yaw"], "head_yaw": actor["head_yaw"], "hands": actor["hands"],
                            "reach_origin_xyz": actor["reach_origin_xyz"]}
        if actor["gaze"] is None:
            gaze[identity] = {"kind": "point", "status": "unresolved", "name": identity + "无当前注视", "reason": "明确没有当前视觉注视"}
        else:
            ref = actor["gaze"]["ref"]; scene, position = _target(world, ref, "state.gaze")
            gaze[identity] = {"kind": "point" if ref["kind"] == "point" else "entity", "status": "resolved",
                              "scene_id": scene, "position_xyz": position}
            if ref["kind"] == "point": gaze[identity]["name"] = ref["id"]
            else:
                gaze[identity].update(target_type=ref["kind"], target_id=ref["id"])
                if ref["kind"] == "actor":
                    # The legacy state contract binds an entity's base position.
                    # Preserve the separately derived anatomical aim explicitly.
                    gaze[identity]["position_xyz"] = world["actors"][ref["id"]]["position_xyz"]
                    gaze[identity]["aim_part"] = "eyes"
                    gaze[identity]["aim_position_xyz"] = position
    props, held = {}, []
    for identity, prop in world["props"].items():
        scene, position = _anchor(world, prop)
        props[identity] = {"scene_id": scene, "position_xyz": position, "anchor": prop["anchor"],
                           "written_ref": prop["written_ref"] or "无当前文字"}
        props[identity]['physical'] = copy.deepcopy(prop['physical'])
        if "hand" in prop["anchor"]:
            held.append({"prop_id": identity, "holder": prop["anchor"]["actor"],
                         "state": ("左手" if prop["anchor"]["hand"] == "left" else "右手") + "持有"})
    return copy.deepcopy({"人物位置": positions or "无人物", "身体朝向": facing or "无人物", "视线落点": gaze or "无人物",
                          "手持道具": held, "门窗状态": {sid: s["door_window_state"] for sid, s in world["scenes"].items()},
                          "动作进度": {"物件位置与状态": props or "无物件", "已完成事件": world["completed"] or "尚无已完成动作",
                                   "眼态": {i: a["eye_state"] for i, a in world["actors"].items()} or "无人物",
                                   "表演状态": {i: a["performance_state"] for i, a in world["actors"].items()} or "无人物",
                                   "动作状态": {i: {**a['action_state'], 'contacts': a['action_state']['contacts'] or '无持续身体接触',
                                       'wet_parts': a['action_state']['wet_parts'] or '无水渍',
                                       'clean_parts': a['action_state']['clean_parts'] or '尚无擦拭动作'} for i,a in world['actors'].items()} or '无人物'},
                          "光源方向": {sid: s["light"] for sid, s in world["scenes"].items()}})


def _free_hand(world, actor, hand, path):
    _need(hand in {"left", "right"}, path, "hand must be left or right")
    _need(not any(p["anchor"] == {"actor": actor, "hand": hand} for p in world["props"].values()), path, "hand is occupied")
    _need(hand not in world['actors'][actor]['action_state']['contacts'] and
          not extended_actions._contacted(world, actor, hand), path, 'hand remains in an actual grasp/support contact')


def _reach(world, actor_id, target_scene, destination, path):
    actor = world["actors"][actor_id]
    _need(actor["scene_id"] == target_scene, path, "reach crosses physical scenes")
    shoulder = actor["reach_origin_xyz"]
    _need(_distance(shoulder, destination) <= actor["reach_m"] + EPS, path, "destination exceeds declared reach")
    _need(not _blocked(world, target_scene, shoulder, destination), path, "known opaque barrier blocks reach")


def _channels(action):
    actor, kind = action["actor"], action["kind"]
    if kind in extended_actions.FIELDS: return extended_actions.channels(action)
    if kind == "move_actor": return {(actor, "body"), (actor, "position"), (actor, "left"), (actor, "right")}
    if kind == "turn": return {(actor, "body"), (actor, "head"), (actor, "left"), (actor, "right")}
    if kind == "look": return {(actor, "gaze")} | ({(actor, "head")} if action["head_mode"] == "follow" else set())
    if kind in {"blink", "blink_restraint"}: return {(actor, "eyelid_closure")}
    if kind == "eyelid": return {(actor, "eyelid_tension")}
    if kind == "focus_adjust": return {(actor, "focus")}
    if kind == "breath": return {(actor, "breath")} | ({(actor, "voice")} if action["phase"] == "inhale" else set())
    if kind == "inner_voice": return {("audio_bus", "primary")}
    if kind in {"speak", "narrate"}: return {(actor, "voice"), ("audio_bus", "primary")}
    if kind == "hold": return {(actor, "position"), (actor, "body"), (actor, "left"), (actor, "right")}
    if kind == "transfer": return {("prop", action["prop"]), (actor, "left"), (actor, "right"), (action["to_actor"], action["to_hand"])}
    if kind == "move_prop": return {("prop", action["prop"]), (actor, action["hand"])}
    if kind == "write": return {("paper", action["paper_ref"]), (actor, action["hand"])}
    return {(actor, action["hand"])}


def _prepare(world, action, sources, path, *, bridge=False):
    kind = action["kind"]
    use_id = action["source_use_id"]
    if use_id is not None:
        _need(use_id in sources["uses_by_id"], path + ".source_use_id", "unknown compiled source use")
        use = sources["uses_by_id"][use_id]
        expected_mode = "spoken" if kind == "speak" else "objective_narration" if kind == "narrate" else kind if kind in {"write","inner_voice"} else "physical_action"
        _need(use["mode"] == expected_mode and use["actor"] == action["actor"], path + ".source_use_id", "source use actor/channel does not match actual action")
    else:
        _need(kind not in {"speak", "narrate", "inner_voice", "write"}, path + ".source_use_id", "actual audio/writing requires compiled source use")
        use = None
    if kind in {"narrate", "inner_voice"}: return {"use": use, "before_world_sha256": _hash(world)}
    a = world["actors"][action["actor"]]
    if any(prop['anchor']=={'actor':action['actor'],'binding':'wrists'} for prop in world['props'].values()):
        _need(kind in {'hold','speak','look','eye_state','eyelid','breath','focus_adjust','blink','blink_restraint','expression','head_gesture'},
              path,'release actual wrist binding before body or hand movement')
    info = {"before": copy.deepcopy(a), "before_world_sha256": _hash(world)}
    if kind in extended_actions.FIELDS:
        return extended_actions.prepare(world, action, info, path, bridge=bridge)
    if kind in {'move_actor', 'turn'}:
        contacts = list(a['action_state']['contacts'].values()) + [
            contact for _, _, contact in extended_actions._contacted(world, action['actor'])]
        _need(not any(contact['mode'] == 'grasp' and contact['part'] in extended_actions.HAND_PARTS for contact in contacts),
              path, 'release actual hand grasp before relocating or turning')
    if kind == "move_actor":
        scene, target = _target(world, {"kind": "point", "id": action["to"]}, path)
        _need(scene == a["scene_id"], path, "cross-room movement requires a separately modelled passage; cut is not relocation")
        _need(not _blocked(world, scene, a["position_xyz"], target), path, "known opaque barrier blocks movement")
        _need(_distance(a["position_xyz"], target) > EPS, path, "move has identical endpoints; use hold")
        info["destination"] = list(target)
    elif kind == "turn":
        for key in ("body_yaw", "head_yaw"): _yaw(action[key], path + "." + key)
        _need(abs(_delta(action["head_yaw"], action["body_yaw"])) <= 105, path, "unsupported head/body twist")
        _need(any(abs(_delta(action[k], a[k])) > EPS for k in ("body_yaw", "head_yaw")), path, "turn has no change")
    elif kind == "look":
        _need(type(action["tracking"]) is bool and action["head_mode"] in {"still", "follow"}, path, "explicit tracking and head_mode required")
        if action["target"] is not None:
            _target(world, action["target"], path)
            _need(a["eye_state"] == "natural_open", path, "look requires open eyes")
        else: _need(not action["tracking"] and action["head_mode"] == "still", path, "clear gaze cannot track or turn head")
    elif kind in {"point", "move_prop", "write"}:
        if kind == "write":
            _need(action["tool"] in world["props"] and world["props"][action["tool"]]["anchor"] == {"actor": action["actor"], "hand": action["hand"]},
                  path, "writing requires its actual held tool; no pen is invented")
        else:
            _free_hand(world, action["actor"], action["hand"], path)
        if kind == "point":
            scene, target = _target(world, action["target"], path)
            fs, finger = _target(world, {"kind": "point", "id": action["finger_to"]}, path)
            _reach(world, action["actor"], fs, finger, path)
            _need(scene == fs, path, "point target is in another room")
            _need(not _blocked(world, scene, finger, target), path, "known barrier blocks pointing target")
            shoulder = a["reach_origin_xyz"]
            _need(_dot(_unit(_sub(finger, shoulder), path), _unit(_sub(target, shoulder), path)) >= math.cos(math.radians(25)),
                  path, "finger endpoint does not point toward target")
            info["destination"] = list(finger)
        elif kind == "move_prop":
            _need(action["prop"] in world["props"], path, "unknown prop")
            prop = world["props"][action["prop"]]
            _need("point" in prop["anchor"], path, "held prop requires transfer, not free movement")
            scene, position = _anchor(world, prop)
            _reach(world, action["actor"], scene, position, path)
            _need(_distance(_hand(a, action["hand"]), position) <= 0.2, path, "hand must already reach the paper before moving it")
            dest_scene, destination = _target(world, {"kind": "point", "id": action["to"]}, path)
            _reach(world, action["actor"], dest_scene, destination, path)
            _need(not _blocked(world, scene, position, destination), path, "known barrier blocks prop path")
            _need(_distance(position, destination) > EPS, path, "prop movement has no change")
            info.update(destination=list(destination), prop_start=list(position), original_anchor=copy.deepcopy(prop["anchor"]))
        else:
            paper = _paper(sources, action["paper_ref"], path, current=False)
            _need(paper["use_id"] == use_id, path, "write action and paper must consume the same source use")
            _need(paper["carrier_id"] in world["props"], path, "write carrier does not exist")
            scene, position = _anchor(world, world["props"][paper["carrier_id"]])
            _reach(world, action["actor"], scene, position, path)
            _need(_distance(_hand(a, action["hand"]), position) <= 0.2, path, "writing hand is not at the carrier")
            info["paper"] = paper
    elif kind == "transfer":
        _need(action["prop"] in world["props"] and action["to_actor"] in world["actors"], path, "unknown transfer entity")
        anchor = world["props"][action["prop"]]["anchor"]
        _need(anchor.get("actor") == action["actor"], path, "actor is not the real holder")
        _free_hand(world, action["to_actor"], action["to_hand"], path)
        other = world["actors"][action["to_actor"]]
        _need(a["scene_id"] == other["scene_id"], path, "transfer crosses physical scenes")
        start, end = _hand(a, anchor["hand"]), _hand(other, action["to_hand"])
        _need(_distance(start, end) <= 0.2, path, "hands must meet before a transfer")
        _need(not _blocked(world, a["scene_id"], start, end), path, "barrier blocks handover")
    elif kind == "blink": _need(a["eye_state"] == "natural_open", path, "closed eyes cannot perform a natural blink")
    elif kind == "eyelid":
        _need(action["to"] in {"relaxed", "slightly_tense"}, path + ".to", "explicit eyelid effect required")
        _need(a["performance_state"]["eyelid"] != action["to"], path, "eyelid effect has no change")
    elif kind == "breath":
        _need(action["phase"] in {"inhale", "exhale"}, path + ".phase", "explicit breath phase required")
    elif kind == "focus_adjust":
        _need(a["eye_state"] == "natural_open" and a["gaze"] is not None, path, "focus adjustment requires open eyes and a resolved current target")
        _check_gazes(world, path + ".focus_target")
    elif kind == "blink_restraint":
        _need(a["eye_state"] == "natural_open", path, "closed-eye state is not an authored blink restraint")
        info["restraint_binding"] = copy.deepcopy(action["_restraint_binding"])
    elif kind == "speak":
        info["use"] = use
    return info


def _restraint_binding(action, actions, sources, ticks, profile, path):
    reason = action["reason"]
    _need(reason in RESTRAINT_REASONS, path + ".reason", "unsupported explicit blink-restraint reason")
    if reason not in {"emphasis", "decisive_tail"}:
        _need(action["utterance_id"] is None and action["char_range"] is None and action["source_use_id"] is not None,
              path, "plot reason needs a real source use and no invented utterance/range")
        return {"reason": reason, "source_use_id": action["source_use_id"], "semantic_review": "pending_independent"}
    utterance = next((x for x in actions if x["id"] == action["utterance_id"]), None)
    _need(utterance is not None and utterance["kind"] == "speak" and utterance["actor"] == action["actor"],
          path + ".utterance_id", "restraint must bind this actor's actual on-scene utterance")
    use = sources["uses_by_id"].get(utterance["source_use_id"])
    _need(isinstance(use, dict), path, "actual utterance source is unavailable")
    text = use["text"]; span = action["char_range"]
    _need(isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span) and 0 <= span[0] < span[1] <= len(text),
          path + ".char_range", "requires actual nonempty utterance character range")
    if reason == "emphasis":
        from performance_checks import emphasis_bindings
        _need(isinstance(utterance["voice"], dict), path, "actual voice specification is unavailable")
        bindings, faults = emphasis_bindings(utterance["voice"].get("emphasis"), [(action["actor"], text)])
        _need(not faults and any(span in x["character_ranges"] for x in bindings), path + ".char_range", "range is not an actual declared emphasis span")
    else:
        _need(span[1] == len(text) and bool(text[span[0]:].strip("。！？!?；;，, .…")), path + ".char_range", "decisive tail must bind the actual utterance ending, not punctuation alone")
    pieces = [(i, max(a, utterance["start"]), min(b, utterance["end"])) for i, (a, b) in enumerate(zip(ticks, ticks[1:]))
              if max(a, utterance["start"]) < min(b, utterance["end"])]
    pauses = utterance.get("pauses", [])
    segments = _audio_segments(text, pieces, "bound-source", profile, path + ".audio_alignment", pauses)
    bounds = []
    for segment in segments:
        lo, hi = segment["char_range"]; left, right = max(lo, span[0]), min(hi, span[1])
        if left >= right: continue
        bounds.append(predicted_character_interval(text, profile, pauses, segment, [left, right]))
    expected_start, expected_end = min(x[0] for x in bounds), max(x[1] for x in bounds)
    _need(utterance["start"] <= action["start"] < action["end"] <= utterance["end"] and
          action["start"] / 10 <= expected_start + EPS and action["end"] / 10 >= expected_end - EPS,
          path, "restraint interval must cover its predicted character span within the real utterance")
    return {"reason": reason, "utterance_id": utterance["id"], "char_range": list(span), "text": text[span[0]:span[1]],
            "source_use_id": utterance["source_use_id"], "source_text_sha256": _hash(text),
            "predicted_interval": [expected_start, expected_end], "profile_id": profile["id"], "is_measured": False}


def _apply(world, action, info, tick, path):
    kind = action["kind"]
    a = world["actors"].get(action["actor"])
    fraction = (tick - action["start"]) / (action["end"] - action["start"])
    end = tick == action["end"]
    if kind in extended_actions.FIELDS:
        extended_actions.apply(world, action, info, tick)
    elif kind == "move_actor":
        a["position_xyz"] = _lerp(info["before"]["position_xyz"], info["destination"], fraction)
        a["reach_origin_xyz"] = _add(info["before"]["reach_origin_xyz"], _sub(a["position_xyz"], info["before"]["position_xyz"]))
    elif kind == "turn":
        for key in ("body_yaw", "head_yaw"):
            a[key] = (info["before"][key] + _delta(action[key], info["before"][key]) * fraction) % 360
        a["reach_origin_xyz"] = _rotated_origin(info["before"], a["body_yaw"])
    elif kind == "look":
        a["gaze"] = None
        held = info["before"]["gaze"] if action["target"] is not None and info["before"]["gaze"] and info["before"]["gaze"]["ref"] == action["target"] else None
        if action["target"] is not None and (action["tracking"] or end or held):
            if held and not action["tracking"]: pos = held["position_xyz"]
            else: _, pos = _target(world, action["target"], path)
            a["gaze"] = {"ref": copy.deepcopy(action["target"]), "position_xyz": list(pos)}
            if action["head_mode"] == "follow": a["head_yaw"] = _bearing(_eye(a), pos)
    elif kind == "point": _set_hand(a, action["hand"], _lerp(_hand(info["before"], action["hand"]), info["destination"], fraction))
    elif kind == "move_prop":
        _set_hand(a, action["hand"], _lerp(info["prop_start"], info["destination"], fraction))
        world["props"][action["prop"]]["anchor"] = ({"point": action["to"]} if end else {"actor": action["actor"], "hand": action["hand"]})
    elif kind == "transfer" and end:
        world["props"][action["prop"]]["anchor"] = {"actor": action["to_actor"], "hand": action["to_hand"]}
    elif kind == "write" and end:
        world["props"][info["paper"]["carrier_id"]]["written_ref"] = action["paper_ref"]
    elif kind == "blink":
        a["eye_state"] = "natural_open" if end or tick == action["start"] else "blinking"
    elif kind == "eyelid":
        if end: a["performance_state"]["eyelid"] = action["to"]
    elif kind == "breath":
        a["performance_state"]["breath"] = "resting" if end else action["phase"]
    elif kind == "focus_adjust":
        _need(a["gaze"] is not None and a["gaze"]["ref"] == info["before"]["gaze"]["ref"], path, "focus target changed during adjustment")
    elif kind == "blink_restraint":
        if end: a["performance_state"].pop("blink_restraint", None)
        else: a["performance_state"]["blink_restraint"] = copy.deepcopy(info["restraint_binding"])
    if end: world["completed"].append(action["id"])


def _action_sentence(action, info, world, camera):
    actor, kind = action["actor"], action["kind"]
    if kind in extended_actions.FIELDS: return extended_actions.sentence(_display_action(action, world, camera), info)
    before = info.get("before", {})
    if kind == 'hold':
        return '保持', (f"{actor}保持{before['posture']}，身体朝{screen_direction(before['body_yaw'], camera['forward'])}。"
                        f"{actor}左手停在{_screen_place(_hand(before, 'left'), camera)}，右手停在{_screen_place(_hand(before, 'right'), camera)}。")
    if kind == "eyelid":
        verb = "放松" if action["to"] == "relaxed" else "轻收"
        return verb, f"{actor}上眼睑{verb}。"
    if kind == "breath":
        verb = "吸气" if action["phase"] == "inhale" else "呼气"
        return verb, f"{actor}自然{verb}。"
    if kind == "focus_adjust":
        target = _reference(world, before["gaze"]["ref"], camera)
        return "焦点调整", f"{actor}眼球轻微进行焦点调整。{actor}视线持续停在{target}。"
    if kind == "blink_restraint":
        binding = info["restraint_binding"]
        reason = RESTRAINT_REASONS[action["reason"]]
        if "text" in binding: reason = "本句" + reason + "“" + binding["text"] + "”"
        return "保持", f"{actor}因{reason}在该窗口不眨眼。{actor}保持双眼自然睁开。"
    if kind == "turn":
        body_changed = abs(_delta(action["body_yaw"], before["body_yaw"])) > EPS
        head_changed = abs(_delta(action["head_yaw"], before["head_yaw"])) > EPS
        body = f"{actor}转身至身体朝{screen_direction(action['body_yaw'], camera['forward'])}。" if body_changed else ""
        head = (f"{actor}转头至头部朝{screen_direction(action['head_yaw'], camera['forward'])}。" if head_changed else
                f"{actor}头部保持不动。")
        return "转身" if body_changed else "转头", body + head
    if kind == "look" and action["target"] is not None:
        origin = _reference(world, before["gaze"]["ref"], camera) if before["gaze"] else None
        target = _reference(world, action["target"], camera)
        if origin == target:
            text = f"{actor}视线持续停在{target}。"
            verb = "停在"
        elif origin is not None:
            text = f"{actor}视线从{origin}移向{target}。"
            verb = "移向"
        else:
            text = f"{actor}将视线落到{target}。"; verb = "视线落到"
        text += (f"{actor}头部保持不动。" if action["head_mode"] == "still" else
                 f"{actor}头部随目标方向转动。{actor}眼球随头部角度调整继续对准{target}。")
        return verb, text
    hand = "左手" if action.get("hand") == "left" else "右手"
    display = _display_action(action, world, camera)
    verbs = {"move_actor": ("移动", f"移动至{display.get('to', '')}"),
             "point": ("指向", f"{hand}指向{(_reference(world, action['target'], camera) if action.get('target') else '')}"),
             "move_prop": ("移动", f"{hand}移动{display.get('prop', '')}至{display.get('to', '')}"),
             "blink": ("自然眨眼", "自然眨眼一次"),
             "transfer": ("递交", f"递交{display.get('prop', '')}至{action.get('to_actor', '')}的{'左手' if action.get('to_hand') == 'left' else '右手'}"),
             "write": ("书写", f"{hand}在{_reference(world, {'kind': 'prop', 'id': info['paper']['carrier_id']}, camera) if info.get('paper') else ''}书写"),
             "look": ("注视", "保持无当前注视" if action.get("target") is None else
                      ("持续跟随注视" if action.get("tracking") else "将视线落到") + (action.get("target") or {}).get("id", ""))}
    if kind == "speak": return None, f'{actor}说道：“{info["use"]["text"]}”'
    if kind in {"narrate", "inner_voice"}: return None, ""  # Audible source belongs to 心理音轨, without lip sync.
    verb, content = verbs[kind]
    if kind == "look": verb = "保持" if action["target"] is None else "注视" if action["tracking"] else "视线落到"
    # Actor starts the physical clause; the exact same sentence is timing basis.
    sentence = f"{actor}{content}。"
    if kind in {"move_actor", "move_prop", "point"}:
        sentence += f"目标落点位于{_screen_place(info['destination'], camera)}。"
    if kind == "write": sentence += f'本次书写内容为“{info["paper"]["text"]}”。'
    return verb, sentence


def _audio_segments(text, pieces, shot_id, profile, path, pauses=()):
    """Predict a contiguous character partition using the registered capacity.

    This is planned alignment, not measured audio. Integer characters that
    cannot be partitioned at a very short macro cut are an explicit limitation.
    """
    count = len(text); total_ticks = sum(hi - lo for _, lo, hi in pieces)
    pauses = normalize_pauses(text, pauses)
    unit_seconds = estimate_utterance("甲", profile)["seconds"]
    estimates = [0]
    for index, character in enumerate(text):
        estimate = estimate_utterance(character, profile, segment_pauses(text, pauses, index, index + 1))
        estimates.append(estimates[-1] + estimate["units"] + estimate["extra_pause_seconds"] / unit_seconds)
    prefix = [0]
    for _, lo, hi in pieces: prefix.append(prefix[-1] + hi - lo)

    capacities = [((hi - lo) / 10 + EPS) / unit_seconds for _, lo, hi in pieces]
    possible = [[False] * (count + 1) for _ in range(len(pieces) + 1)]
    possible[-1][count] = True
    for index in range(len(pieces) - 1, -1, -1):
        next_count = [0]
        for feasible in possible[index + 1]: next_count.append(next_count[-1] + int(feasible))
        for start in range(count):
            maximum = bisect_right(estimates, estimates[start] + capacities[index]) - 1
            possible[index][start] = next_count[maximum + 1] > next_count[start + 1]
    _need(possible[0][0], path,
          "not-supported audio alignment: complete integer characters cannot fit the declared macro intersections at this profile; no measured alignment or fractional character is invented")
    segments = []; cursor = 0
    for index, (_, lo, hi) in enumerate(pieces):
        maximum = bisect_right(estimates, estimates[cursor] + capacities[index]) - 1
        goal = estimates[-1] * prefix[index + 1] / total_ticks
        options = [stop for stop in range(cursor + 1, maximum + 1) if possible[index + 1][stop]]
        stop = min(options, key=lambda value: abs(estimates[value] - goal) + 0.001 * abs(value - count * prefix[index + 1] / total_ticks))
        segments.append({"shot_id": shot_id, "start": lo / 10, "end": hi / 10, "char_range": [cursor, stop]})
        cursor = stop
    return segments


def _voice_with_pauses(voice, text, pauses, path):
    """Use the same declared extra waits in the existing voice pause field."""
    if not pauses:
        return voice
    _need(isinstance(voice, dict), path, 'requires explicit voice decisions')
    authored = str(voice.get('pauses') or '')
    # Do not silently replace an already authored numerical performance. Only
    # exact source-prefix/boundary anchors can be reconciled without guessing.
    numeric = list(re.finditer(r'(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百半点]+)\s*(?:秒|[sS])', authored))
    explicit = list(re.finditer(
        r'(?P<anchor>开口前|本句结束后|句末|(?:说至本句)?“(?P<prefix>[^”]+)”后)'
        r'(?P<extra>额外)?(?:停顿|短停)(?P<seconds>\d+(?:\.\d+)?)秒', authored))
    for match in numeric:
        bound = next((x for x in explicit if x.start() <= match.start() and match.end() <= x.end()), None)
        _need(bound is not None, path, 'explicit numeric voice pause needs an exact structured source-prefix or utterance-boundary binding')
        prefix = bound['prefix']
        _need(prefix is None or text.startswith(prefix), path, 'numeric voice pause anchor is not this exact source prefix')
        offset = len(prefix) if prefix is not None else 0 if bound['anchor'] == '开口前' else len(text)
        _need(any(p['offset'] == offset and abs(p['seconds'] - float(bound['seconds'])) <= EPS
                  and (not bound['extra'] or p['extra']) for p in pauses), path,
              'numeric voice pause conflicts with the same structured utterance position')
    extra = [p for p in pauses if p['extra'] and p['seconds'] > 0]
    if not extra:
        return voice
    internal = any(0 < p['offset'] < len(text) for p in extra)
    _need(not re.search(r'(?:全句|全程|始终|一直)(?:都)?不停顿|(?:无|没有)(?:任何|额外)?停顿', authored)
          and not (internal and re.search(r'句中不停顿', authored)), path,
          'voice pause instruction conflicts with the declared extra pause')
    labels = []
    for pause in extra:
        offset = pause['offset']
        anchor = '开口前' if offset == 0 else '本句结束后' if offset == len(text) else f'说至本句“{text[:offset]}”后'
        labels.append(f"{anchor}额外停顿{pause['seconds']:g}秒")
    return {**voice, 'pauses': '；'.join(labels) + '；其余按语义自然短停。'}


def _screen_place(position, camera):
    vector = _sub(position, camera['origin']); depth = _dot(vector, camera['forward'])
    if depth <= EPS:
        return '画外摄影机后方'
    x = _dot(vector, camera['right']) / depth / camera['hx']
    if abs(x) > 1 + EPS:
        return '画外左侧' if x < 0 else '画外右侧'
    return '画面中部' if abs(x) <= 1 / 3 else '画面左侧' if x < 0 else '画面右侧'


def _display_name(value, path):
    _name(value, path)
    _need(not list(world_position_matches(value)), path,
          'display name still encodes world placement; supply an explicit object name, keep the stable ID')
    return value


def _reference(world, ref, camera):
    """Present an explicit reference, retaining identity and camera-relative place."""
    identity, kind = ref['id'], ref['kind']
    if kind == 'actor': return identity  # personal names are not placement labels
    registry = world['points'] if kind == 'point' else world['props']
    item = registry[identity]
    label = _display_name(item.get('display_name', identity), kind + '.' + identity + '.display_name')
    if kind == 'prop':
        scene_id, _ = _anchor(world, item)
        _need(not any(other != identity and row.get('display_name', other) == label
                      and _anchor(world, row)[0] == scene_id for other, row in registry.items()),
              'prop.' + identity + '.display_name',
              'multiple scene props share a display name; provide a grounded distinguishing label')
        return label
    place = _screen_place(item['position_xyz'], camera)
    # Two identical labels in the same region do not identify a usable anchor.
    # Require an authored distinguishing name; never fabricate numbering/props.
    ambiguous = [other for other, row in registry.items() if other != identity
                 and row['scene_id'] == item['scene_id']
                 and row.get('display_name', other) == label
                 and _screen_place(row['position_xyz'], camera) == place]
    _need(not ambiguous, 'point.' + identity + '.display_name',
          'display name and screen region do not distinguish multiple references; provide a grounded distinguishing label')
    return place + '的' + label


def _opening_label(world, scene, identity):
    item = world['points'].get(identity, world['props'].get(identity, {}))
    return _display_name(scene.get('opening_labels', {}).get(identity, item.get('display_name', identity)),
                         'scene.opening_labels.' + identity)


def _opening_description(world, scene_id, camera):
    scene = world['scenes'][scene_id]
    if scene['door_window_state'] == '无门窗。': return '无门窗。'
    openings = opening_states(scene['door_window_state'])
    labels = scene.get('opening_labels', {})
    _need(set(labels) <= set(openings), 'scene.opening_labels', 'display name refers to an undeclared opening')
    displays, rows = set(), []
    for identity, state in openings.items():
        label = _opening_label(world, scene, identity)
        if identity in world['points']:
            point = world['points'][identity]
            _need(point['scene_id'] == scene_id, 'scene.openings', 'opening point belongs to another scene')
            position = point['position_xyz']
        elif identity in world['props']:
            prop_scene, position = _anchor(world, world['props'][identity])
            _need(prop_scene == scene_id, 'scene.openings', 'opening prop belongs to another scene')
        elif identity == scene['light']['name'] and 'position_xyz' in scene['light']:
            position = scene['light']['position_xyz']
        else:
            position = None
        _need(position is not None or not list(world_position_matches(identity)), 'scene.openings.' + identity,
              'world-position opening requires an explicit point/prop or the same named light geometry; no left/right is guessed')
        display = (_screen_place(position, camera) + '的' if position is not None else '') + label
        _need(display not in displays, 'scene.opening_labels',
              'multiple openings have indistinguishable display labels; provide grounded distinguishing names')
        displays.add(display)
        rows.append(display + ('开启。' if state == 'open' else '关闭。'))
    return ''.join(rows)


def _display_action(action, world, camera):
    """Adapt only typed reference slots, never substitute words in source prose."""
    shown = dict(action)
    for key in ('prop', 'tool'):
        if action.get(key) in world['props']:
            shown[key] = _reference(world, {'kind': 'prop', 'id': action[key]}, camera)
            if key == 'prop': shown['_prop_id'] = action[key]
    for key in ('point', 'to'):
        if key == 'to' and action['kind'] not in {'move_actor', 'move_prop', 'hand_move', 'put_down', 'drop_prop'}: continue
        if isinstance(action.get(key), str) and action[key] in world['points']:
            shown[key] = _reference(world, {'kind': 'point', 'id': action[key]}, camera)
    if action.get('opening'):
        scene = world['scenes'][world['actors'][action['actor']]['scene_id']]
        name = action['opening']
        shown['opening'] = _opening_label(world, scene, name)
    return shown


def _spatial_description(world, row, camera, view, sources):
    """Camera-relative layout from the same world; never invent scene objects."""
    plan = sources.get('production_plan')
    if plan is None:
        return None
    spec = next(s for s in plan['shots'] if s['shot_id'] == row['shot_id'])
    anchors = spec['design']['spatial_anchors']; descriptions = []
    subjects = {name: world['actors'][name]['position_xyz'] for name in view['actors']}
    subjects.update({name: _anchor(world, world['props'][name])[1] for name in view['props']})
    _need(set(anchors) == set(subjects), 'shot.design.spatial_anchors', 'anchors must exactly cover initial visible subjects')
    for name, position in subjects.items():
        anchor = anchors[name]
        _need(anchor != name and anchor in world['points'] and world['points'][anchor]['scene_id'] == row['scene_id'],
              'shot.design.spatial_anchors', 'requires a different registered point in this scene')
        reference = world['points'][anchor]['position_xyz']; delta = _sub(position, reference)
        # Ground-layout depth excludes landmark height; camera tilt cannot turn
        # an actor at the same floor location into a person behind a table.
        lateral = _dot(delta, camera['right'])
        depth = _dot(delta[:2], camera['forward'][:2])
        sides = []
        if abs(lateral) > .1: sides.append('左' if lateral < 0 else '右')
        if abs(depth) > .1: sides.append('后' if depth > 0 else '前')
        relative = '画面' + ''.join(sides) + '侧' if sides else '近旁'
        reference_label = _reference(world, {'kind': 'point', 'id': anchor}, camera)
        label = name if name in world['actors'] else _reference(world, {'kind': 'prop', 'id': name}, camera)
        descriptions.append(f"{label}位于{_screen_place(position, camera)}；相对{reference_label}，处于{relative}。")
    return ''.join(descriptions)


def _visible_parts(shown):
    parts = (["面部" if shown["front"] else "后侧头部"] if shown["head"] else [])
    parts += ["左手" if hand == "left" else "右手" for hand in shown["hands"]]
    if shown["feet"]: parts.append("足部")
    return parts


def _shot_visible_subjects(first_view, samples, initial_text):
    """Register actual shot subjects without claiming a late part in frame zero."""
    parts = {}
    for sample in samples:
        for identity, shown in sample['view']['actors'].items():
            parts.setdefault(identity, [])
            for part in _visible_parts(shown):
                if part not in parts[identity]: parts[identity].append(part)
    if not parts: return initial_text
    rows = []
    for identity, whole in parts.items():
        first = _visible_parts(first_view['actors'][identity]) if identity in first_view['actors'] else []
        note = '' if set(first) == set(whole) else '；首帧：' + ('、'.join(first) if first else '不入画')
        rows.append(f"{identity}（{'、'.join(whole)}{note}）")
    return '；'.join(rows)


def _initial(world, view, sources, camera):
    rows, limits, names = [], [], []
    for identity, shown in view["actors"].items():
        actor = world["actors"][identity]
        parts = _visible_parts(shown)
        names.append(f"{identity}（{'、'.join(parts)}）")
        place = _screen_place(_eye(actor) if shown['head'] else actor['position_xyz'], camera)
        rows.append(f"{identity}位于{place}，{actor['posture']}，身体朝{screen_direction(actor['body_yaw'], camera['forward'])}，头部朝{screen_direction(actor['head_yaw'], camera['forward'])}。")
        rows.append(extended_actions.static_actor(actor, identity, face_visible=shown['eye_visible']))
        for hand in shown["hands"]:
            place = _screen_place(_hand(actor, hand), camera)
            rows.append(f"{identity}{'左手' if hand == 'left' else '右手'}位于{place}。")
        eyes = eye_blocks(subject=identity, visibility="visible" if shown["eye_visible"] else "hidden",
                          state=("source_closed" if actor["eye_state"] == "closed" else "natural_open") if shown["eye_visible"] else None,
                          source_basis="初态明确闭眼决定" if shown["eye_visible"] and actor["eye_state"] == "closed" else None,
                          planned_events=sources.get('production_plan') is not None)
        rows.append(eyes["initial_state_append"]); limits.append(eyes["generation_limits_append"])
        if shown["eye_visible"] and actor["gaze"] is not None:
            rows.append(f"{identity}视线落在{_reference(world, actor['gaze']['ref'], camera)}。")
    for identity in view["props"]:
        prop = world["props"][identity]; _, position = _anchor(world, prop)
        label = _reference(world, {"kind": "prop", "id": identity}, camera)
        rows.append(f"{label}位于{_screen_place(position, camera)}。")
        if prop['physical']['integrity']=='broken': rows.append(f'{label}已碎裂。')
        if 'worn' in prop['anchor']: rows.append(f"{label}穿戴在{prop['anchor']['actor']}{extended_actions.PART_TEXT[prop['anchor']['worn']]}。")
        if 'binding' in prop['anchor']: rows.append(f"{label}捆住{prop['anchor']['actor']}双腕。")
        if 'prop' in prop['anchor']: rows.append(f"{label}承放在{_reference(world, {'kind': 'prop', 'id': prop['anchor']['prop']}, camera)}上。")
        if prop["written_ref"] is not None:
            paper = sources["written_objects_by_id"][prop["written_ref"]]
            rows.append(f'{label}当前纸面可读文字为“{paper["text"]}”。')
    _need(bool(rows), "camera.frame0", "no modelled subject is visible in the initial frame")
    return "".join(rows), "；".join(names) if names else "无人（物件：" + "、".join(_reference(world, {"kind": "prop", "id": name}, camera) for name in view["props"]) + "）", "".join(limits)


def _fields(world, row, cam, view, sources):
    planned = sources.get('production_plan') is not None
    static, visible, eye_limits = _initial(world, view, sources, cam)
    scene = world["scenes"][row["scene_id"]]; light = scene["light"]
    light_source, vector = _light_source(light)
    outdoor = light['kind'] == 'outdoor_daylight'
    source_vec = vector if outdoor else _sub(vector, cam["target"])
    screen = _dot(source_vec, cam["right"])
    receivers = [i + ("面部" if v["eye_visible"] else "后脑" if v["head"] else "手部") for i, v in view["actors"].items()]
    receivers = receivers or [_reference(world, {"kind": "prop", "id": name}, cam) for name in view["props"]]
    blocks = scene_lighting_blocks(source=light_source,
                                     screen_side="正前" if abs(screen) <= EPS else "左" if screen < 0 else "右",
                                     receiving_surfaces=receivers, weak_reflector=None, main_tone=light["main_tone"],
                                     shadow_tone=light["shadow_tone"], materials=light["materials"], air="clear", exposure="normal")
    geometry_actors = []
    for identity, shown in view["actors"].items():
        actor = world["actors"][identity]
        # A geometry placeholder is a head-direction point, never a fabricated gaze.
        target = (actor["gaze"]["position_xyz"] if actor["gaze"] is not None else
                  _add(actor["position_xyz"], [math.sin(math.radians(actor["head_yaw"])), math.cos(math.radians(actor["head_yaw"])), 0]))
        if math.hypot(target[0] - actor["position_xyz"][0], target[1] - actor["position_xyz"][1]) <= EPS:
            target = _add(actor["position_xyz"], [math.sin(math.radians(actor["head_yaw"])), math.cos(math.radians(actor["head_yaw"])), 0])
        geometry_actors.append({"actor_id": identity, "actor_position": actor["position_xyz"][:2],
                               "body_yaw": actor["body_yaw"], "head_yaw": actor["head_yaw"],
                               "gaze_target": target[:2], "clear_face": shown["eye_visible"]})
    # Screen relations are tokens, so the production v2 binder consumes the same camera.
    names = list(view["actors"]); bindings = [{"id": "camera", "kind": "camera_pose"}]
    relation_tokens = []
    for i in range(1, len(names)):
        token = "relation" + str(i)
        bindings.append({"id": token, "kind": "screen_relation", "actor": names[i], "relative_to": names[0]})
        relation_tokens.append("[[staging:" + token + "]]")
    from production_plan import shot_policy
    framing = shot_policy()['framing'][row['camera']['frame']]
    if row["camera"]["frame"] in {"medium_close", "face", "face_detail"}:
        _need(any(v["eye_visible"] for v in view["actors"].values()) and
              not any(v["feet"] for v in view["actors"].values()), "camera.frame",
              "face close-up requires a visible front-facing head and excludes foot landmarks; facial area still requires independent review")
    if row["camera"]["frame"] in {"hands", "object"}:
        _need(not any(v["head"] or v["feet"] for v in view["actors"].values()), "camera.frame", "declared close-up also contains head/feet landmarks")
    if row["camera"]["frame"] == "object": _need(not view["actors"], "camera.frame", "object-only frame contains actor landmarks")
    tilt = math.degrees(math.asin(cam["forward"][2]))
    fields = {**{key: row["editorial"][key] for key in METADATA_FIELDS}, "场景连续性ID": row["continuity_id"], "镜头类型": framing,
              "可见主体": visible, "空间站位描述": scene["label"] + "。" +
                  (("室外日间。" if outdoor else "日间。" if light['period'] == 'day' else "夜间。") + _opening_description(world, row["scene_id"], cam)) +
                  (_spatial_description(world, row, cam, view, sources) if planned else
                   "".join(f"{_reference(world, {'kind': 'point', 'id': name}, cam)}。" for name, point in world["points"].items() if point["scene_id"] == row["scene_id"])),
              "起始状态": "\n".join(relation_tokens + [static]),
              "机位": "[[staging:camera]]" + f"摄影机离地{cam['origin'][2]:g}米，{'仰角' if tilt >= 0 else '俯角'}{abs(tilt):.1f}度；水平视角{row['camera']['hfov_deg']:g}度，垂直视角{row['camera']['vfov_deg']:g}度。",
              "运镜方式": "固定镜头", "音色": voice_block(mode="silent", dialogue_rows=[]), "心理音轨": "无。",
              "生成限制": "9:16竖屏；禁止额外配乐。" + eye_limits + blocks["generation_limits_append"],
              **blocks["lighting_fields"], **transition_block(method="hard_cut")}
    staging = {"contract": "initial-staging-text-v1", "geometry": {"contract": "staging-geometry-v1", "camera": cam["origin"][:2],
                "camera_target": cam["target"][:2], "actors": geometry_actors}, "target_label": row["camera"]["target"], "bindings": bindings}
    return fields, staging


def lower_production_decisions(payload: dict, *, source_compilation=None) -> dict:
    """Pure deterministic lowering. Caller cannot supply redundant result fields."""
    _obj(payload, {"contract", "project", "initial_world", "shots"}, "$")
    _need(payload["contract"] == CONTRACT, "contract", "unsupported production decision contract")
    try:
        raw_input_sha256 = _hash(payload)
    except (TypeError, ValueError) as exc:
        raise ProductionDecisionError("$", "requires finite JSON production decisions: " + str(exc)) from exc
    sources = _source(source_compilation)
    from production_plan import ProductionPlanGuard
    plan_guard = _production_plan_call(ProductionPlanGuard, payload, sources)
    world = _world(payload["initial_world"], sources)
    project = copy.deepcopy(payload["project"])
    _need(isinstance(project, dict) and isinstance(project.get("prevention_evidence"), dict), "project", "explicit project evidence required")
    evidence = project["prevention_evidence"]
    _need("scene_state_ledger" not in evidence, "project", "second endpoint-state copy forbidden")
    timing = evidence.get("audio_workflow", {}).get("timing")
    _need(isinstance(timing, dict) and not timing.get("utterances") and "events" not in timing, "project.audio_workflow.timing", "utterances/events are derived from actions")
    timing["utterances"] = []
    try:
        default_audio_profile = timing_profile(timing_rules(), timing.get("default_profile_id"), default=True)
    except ValueError as exc:
        raise ProductionDecisionError("project.audio_workflow.timing.default_profile_id", str(exc)) from exc
    audio_profiles = {}
    authored = evidence.setdefault("authoring_evidence", {})
    _need(isinstance(authored, dict), "project.authoring_evidence", "requires object")
    authored["production_state_contract"] = STATE_CONTRACT
    _need(isinstance(payload["shots"], list) and bool(payload["shots"]), "shots", "requires nonempty complete shots")
    from source_action_plans import expand_source_actions
    payload, source_action_execution = _source_action_call(
        expand_source_actions, payload, sources, action_fields=ACTION_FIELDS)
    chain = ProductionStateChain(_state(world), label="initial", reason="single initial production world")
    rows, records, used_events, used_shots, used_uids = [], [], {}, set(), set()
    consumed_uses, consumed_papers, projection_dependencies, rendered_event_ids = set(), set(), {}, set()
    reviewed_speech = []
    world_events = []
    elapsed = 0; previous = None
    for index, raw in enumerate(payload["shots"]):
        path = f"shots[{index}]"
        _obj(raw, {"shot_id", "shot_uid", "episode_id", "scene_id", "continuity_id", "ticks", "camera", "actions", "editorial", "source_unit_ids", "transition"}, path)
        row = copy.deepcopy(raw)
        for key in ("shot_id", "shot_uid", "episode_id", "scene_id", "continuity_id"): _name(row[key], path + "." + key)
        sid = row["shot_id"]
        _need(sid not in used_shots and row["shot_uid"] not in used_uids, path, "duplicate shot identity")
        used_shots.add(sid); used_uids.add(row["shot_uid"])
        _need(row["scene_id"] in world["scenes"], path, "unknown physical scene")
        ticks = row["ticks"]
        _need(isinstance(ticks, list) and len(ticks) == 4, path + ".ticks", "requires four shared macro ticks")
        for t in ticks: _tick(t, path + ".ticks")
        _need(ticks[0] == 0 and all(a < b for a, b in zip(ticks, ticks[1:])), path, "three positive contiguous phases required")
        _editorial(row["editorial"], path + ".editorial")
        _need(isinstance(row["source_unit_ids"], list) and bool(row["source_unit_ids"]), path, "actual source references required")
        _obj(row["transition"], {"relation", "basis"} | ({'bridge'} if 'bridge' in row['transition'] else set()), path + ".transition")
        _text(row["transition"]["basis"], path + ".transition.basis")
        if previous is None:
            _need(row["transition"]["relation"] == "initial", path, "first shot relation must be initial")
            transition = "本集起始。" + row["transition"]["basis"]
        elif previous["continuity_id"] == row["continuity_id"]:
            _need(row["transition"]["relation"] == "continuous" and previous["scene_id"] == row["scene_id"], path, "same continuity ID cannot waive physical scene change")
            transition = "同场连续切换。" + row["transition"]["basis"]
        else:
            try:
                transition = BoundaryDecision(previous["continuity_id"], row["continuity_id"], row["transition"]["relation"], row["transition"]["basis"]).explanation()
            except ValueError as exc:
                raise ProductionDecisionError(path + ".transition", str(exc)) from exc
        bridge_record = None
        if 'bridge' in row['transition']:
            _need(previous is not None, path, 'initial world cannot be replaced by a pre-first-shot bridge')
            from production_bridge import run_bridge
            bridge_record = run_bridge(world, row['transition']['bridge'], transition=row['transition'], sources=sources,
                source_execution=source_action_execution, prior_events=used_events, prior_projection=projection_dependencies, elapsed=elapsed)
            _production_plan_call(plan_guard.check_bridge, row, row['transition']['bridge']['actions'])
            chain.advance(_state(world), label=sid+'-bridge', reason='explicit omitted travel/time; not source delivery')
        start_world = copy.deepcopy(world); start = chain.current
        cam = _camera(world, row["camera"], row["scene_id"], path + ".camera")
        first_view = _view(world, cam, row["scene_id"])
        try:
            fields, staging = _fields(world, row, cam, first_view, sources)
            if sources.get('production_plan') and row['transition']['relation'] != 'continuous':
                # Project only place/visible identity into the editing field;
                # the causal reasoning remains in the internal frozen plan.
                fields.update(transition_block(method='hard_cut',
                    destination_place=world['scenes'][row['scene_id']]['label']))
        except ContractBlockError as exc:
            raise ProductionDecisionError(path + ".initial_contract_blocks", str(exc)) from exc
        visible_current_paper_refs = []
        for identity in first_view["props"]:
            ref = world["props"][identity]["written_ref"]
            if ref is not None:
                visible_current_paper_refs.append(ref)
                consumed_papers.add(ref); consumed_uses.add(sources["written_objects_by_id"][ref]["use_id"])
        actions = row["actions"]
        _need(isinstance(actions, list), path + ".actions", "requires explicit action array")
        intervals = []; prepared = {}; local_ids = set()
        for j, action in enumerate(actions):
            ap = f"{path}.actions[{j}]"
            _need(isinstance(action, dict) and action.get("kind") in ACTION_FIELDS, ap, "unsupported action kind")
            _obj(action, _action_keys(action["kind"], action), ap)
            if action["kind"] in {"speak", "narrate", "inner_voice"}:
                use_id = action["source_use_id"]
                _need(use_id in sources["uses_by_id"], ap + ".source_use_id", "unknown compiled audio use")
                action["actor"] = sources["uses_by_id"][use_id]["actor"]
                choice = action.get("timing")
                if choice is not None:
                    _obj(choice, {"profile_id", "selection_evidence"}, ap + ".timing")
                elif "timing" in action:
                    raise ProductionDecisionError(ap + ".timing", "timing must be an explicit selection object")
                choice = choice or {}
                use = sources["uses_by_id"][use_id]
                try:
                    if 'pauses' in action:
                        action['pauses'] = normalize_pauses(use['text'], action['pauses'])
                        action['voice'] = _voice_with_pauses(action['voice'], use['text'], action['pauses'], ap + '.voice.pauses')
                    audio_profiles[action["id"]] = timing_profile(
                        timing_rules(), choice.get("profile_id", default_audio_profile["id"]),
                        selection=choice.get("selection_evidence"),
                        kind="dialogue" if action["kind"] == "speak" else "psychological",
                        speaker=use["actor"], text=use["text"], uses=sources["uses_by_id"],
                        voice=action.get("voice"))
                except ValueError as exc:
                    raise ProductionDecisionError(ap + ".timing", str(exc)) from exc
            _name(action["id"], ap + ".id")
            if action["kind"] == "narrate": _need(action["actor"] is None, ap, "objective narration has no in-scene speaking actor")
            else: _name(action["actor"], ap + ".actor")
            _need(action["id"] not in used_events and action["id"] not in local_ids, ap, "duplicate stable event identity")
            local_ids.add(action["id"])
            if action["kind"] != "narrate":
                _need(action["actor"] in world["actors"], ap, "unknown action actor")
                _need(world["actors"][action["actor"]]["scene_id"] == row["scene_id"], ap, "offscene physical action is not modelled in this shot")
            lo, hi = _tick(action["start"], ap), _tick(action["end"], ap)
            _need(lo < hi <= ticks[-1], ap, "action outside shot duration")
            parts = [(max(a, lo), min(b, hi)) for a, b in zip(ticks, ticks[1:]) if max(a, lo) < min(b, hi)]
            _need(len(parts) == 1 or action["kind"] in CONTINUOUS | {"speak", "narrate", "inner_voice"}, ap,
                  "atomic blink/transfer/write cross macro phase; this v1 requires their explicit interval inside one macro phase")
            _need(isinstance(action["after"], list) and len(set(action["after"])) == len(action["after"]), ap, "requires unique prerequisite event IDs")
            channels = _channels(action)
            for other, op, used in intervals:
                _need(hi <= other["start"] or lo >= other["end"] or not channels & used, ap, "overlapping writes conflict with " + op)
            intervals.append((action, ap, channels))
        for action, ap, _ in intervals:
            for dependency in action["after"]:
                local = next((x for x in actions if x["id"] == dependency), None)
                end = elapsed + local["end"] if local else used_events.get(dependency)
                _need(end is not None and end <= elapsed + action["start"], ap, "missing or unfinished action prerequisite")
            if action["kind"] == "blink_restraint":
                action["_restraint_binding"] = _restraint_binding(action, actions, sources, ticks,
                    audio_profiles.get(action["utterance_id"], default_audio_profile), ap)
        samples = []
        for tick in range(ticks[-1] + 1):
            # Finish prior actions before starting new actions at this exact tick.
            active = [(a, ap) for a, ap, _ in intervals if a["start"] < tick <= a["end"]]
            for a, ap in sorted(active, key=lambda x: x[0]["kind"] == "look"):
                _apply_source_action(world, a, prepared[a["id"]], tick, ap, source_action_execution)
            for a, ap, _ in intervals:
                if a["start"] == tick:
                    prepared[a["id"]] = _prepare(world, a, sources, ap)
                    _source_action_call(source_action_execution.start, a, world, ap)
                    _apply_source_action(world, a, prepared[a["id"]], tick, ap, source_action_execution)
            _check_gazes(world, f"{path}.tick[{tick}].gaze")
            extended_actions.check_contacts(world, f'{path}.tick[{tick}].contacts')
            for identity, actor in world["actors"].items():
                _need(abs(_delta(actor["head_yaw"], actor["body_yaw"])) <= 105, f"{path}.tick[{tick}].{identity}", "head/body relation exceeds supported model")
            view = _view(world, cam, row["scene_id"])
            samples.append({"tick": tick, "view": view, "world": copy.deepcopy(world)})
        phases = [{"start": a / 10, "end": b / 10, "text": ""} for a, b in zip(ticks, ticks[1:])]
        events, action_records, dialogue_rows = [], [], []
        psychological_text = ""; voice_entries = []
        audio_alignments = []
        for action, ap, _ in sorted(intervals, key=lambda x: (x[0]["start"], x[0]["id"])):
            if action["kind"] == "speak":
                use = sources["uses_by_id"][action["source_use_id"]]
                realization = use.get("spoken_realization") or {}
                if realization.get("transform") == "reviewed_indirect_speech_v1":
                    reviewed_speech.append((realization, use["id"], elapsed + action["start"], elapsed + action["end"]))
            info = prepared[action["id"]]; verb, sentence = _action_sentence(action, info, samples[action["start"]]["world"], cam)
            pieces = [(i, max(a, action["start"]), min(b, action["end"])) for i, (a, b) in enumerate(zip(ticks, ticks[1:]))
                      if max(a, action["start"]) < min(b, action["end"])]
            is_audio = action["kind"] in {"speak", "narrate", "inner_voice"}
            if is_audio:
                _need(action["id"] not in rendered_event_ids, ap, "audio identity collides with a derived event")
                rendered_event_ids.add(action["id"])
            world_pieces = list(pieces)
            inherited = list(dict.fromkeys(item for dependency in action["after"] for item in projection_dependencies[dependency]))
            partial_blink = False
            if action['kind'] in extended_actions.FIELDS:
                shown=[extended_actions.visible(samples[t],action) for t in range(action['start'],action['end'])]
                end_visible=extended_actions.visible(samples[action['end']],action) if action['kind']!='cross_passage' else True
                if action['source_use_id'] is not None:
                    _need(all(shown) and end_visible,ap+'.visible','source action requires its actual action/contact landmarks in frame; use an appropriate camera or separate supported delivery')
                pieces=[(i,lo,hi) for i,lo,hi in pieces if all(shown[t-action['start']] for t in range(lo,hi))]
            elif action["kind"] in EYE_ACTIONS | {"look"}:
                visible_intervals = _visible_intervals(samples, action["actor"], action["start"], action["end"])
                visible_intervals = [(lo, hi) for lo, hi in visible_intervals
                                     if info["before"]["eye_state"] == "natural_open"]
                if action['kind'] == 'look' and action['source_use_id'] is not None:
                    _need(visible_intervals == [(action['start'], action['end'])], ap + '.visible',
                          'source look requires actual eye landmarks in frame; use an appropriate camera')
                partial_blink = visible_intervals != [(action["start"], action["end"])]
                pieces = [(i, max(lo, left), min(hi, right)) for i, lo, hi in pieces for left, right in visible_intervals
                          if max(lo, left) < min(hi, right)]
            elif action["kind"] == "breath":
                # A world breath is retained offscreen; do not create visible
                # body performance where no actor landmark is in the frame.
                visible_intervals = _visible_intervals(samples, action["actor"], action["start"], action["end"], require_face=False)
                pieces = [(i, max(lo, left), min(hi, right)) for i, lo, hi in pieces for left, right in visible_intervals
                          if max(lo, left) < min(hi, right)]
            mouth_intervals = (_visible_intervals(samples, action["actor"], action["start"], action["end"])
                               if action["kind"] == "speak" else [])
            gaze_check = (_speaking_gaze(samples, action, cam, ap + '.gaze_camera',
                                        required=sources.get('production_plan') is not None)
                          if action['kind'] == 'speak' else None)
            if action["source_use_id"] is not None and pieces: consumed_uses.add(action["source_use_id"])
            if action["kind"] == "write": consumed_papers.add(action["paper_ref"])
            fragment_ids = []; rendered_sentences = []
            for part_index, (phase_index, lo, hi) in enumerate(pieces):
                fragment_id = action["id"] if len(pieces) == 1 else action["id"] + "-part" + str(part_index + 1)
                if not (is_audio and fragment_id == action["id"]):
                    _need(fragment_id not in rendered_event_ids, ap, "derived event identity collides with another event")
                    rendered_event_ids.add(fragment_id)
                fragment_ids.append(fragment_id)
                fragment = sentence
                if is_audio:
                    if part_index:
                        fragment = ''
                    if action["kind"] == "speak":
                        if fragment and not fragment.endswith(("。", "；")): fragment += "；"
                        for left, right in mouth_intervals:
                            left, right = max(lo, left), min(hi, right)
                            if left < right:
                                fragment += f"{action['actor']}口部入画时口型与对白一致。"
                        if not mouth_intervals and part_index == 0:
                            fragment += f"{action['actor']}口部不入画。"
                elif action["kind"] == "blink":
                    # Keep the clock and actual actor in the same consumer
                    # clause; a comma before the actor loses this binding.
                    prior_speech = [item for item in world_events if item["id"] in action["after"]
                                    and item["shot_id"] == sid and item["kind"] == "speak" and item["actor"] != action["actor"]]
                    after_text = f"于{prior_speech[-1]['actor']}本句发言结束后" if prior_speech else ""
                    fragment = f"{action['actor']}在{lo / 10:.1f}秒{after_text}"
                    fragment += "呈现一次眨眼动作的局部。" if partial_blink else "自然眨眼一次。"
                    if partial_blink: verb = "眨眼"
                elif action['kind'] in extended_actions.FIELDS:
                    verb, fragment = extended_actions.fragment_sentence(_display_action(action, samples[lo]["world"], cam), info, lo, hi)
                elif len(pieces) > 1 and action['kind'] != 'hold' and verb not in {'停在', '保持', '焦点调整'}:
                    prefix = "开始" if part_index == 0 else "继续"
                    fragment = str(action["actor"]) + prefix + sentence[len(action["actor"]):]
                phase_hold = (action['kind'] == 'look' and verb in {'停在', '保持'}
                              and (lo, hi) == (ticks[phase_index], ticks[phase_index + 1]))
                if not is_audio and action["kind"] != "blink" and not phase_hold:
                    prior_speech = [item for item in world_events if item["id"] in inherited
                                    and item["shot_id"] == sid and item["kind"] == "speak" and item["actor"] != action["actor"]]
                    after_text = f"于{prior_speech[-1]['actor']}本句发言结束后" if prior_speech else ""
                    fragment = re.sub(r"(^|[，,；;。])" + re.escape(action["actor"]),
                                      lambda m: m[1] + action["actor"] + f"在{lo / 10:.1f}秒" + after_text, fragment)
                prefix = f"{lo / 10:.1f}秒，" if is_audio else ""
                if fragment:
                    phases[phase_index]["text"] += f"{prefix}{fragment}"
                rendered_sentences.append(fragment)
                if not is_audio:
                    event = {"id": fragment_id, "kind": "hold" if action["kind"] == "hold" else "action", "actor": action["actor"],
                             "start": lo / 10, "end": hi / 10, "action": verb, "basis": fragment}
                    after = ([fragment_ids[-2]] if part_index else inherited)
                    if after: event["after"] = after
                    events.append(event)
            if is_audio:
                use = info["use"]
                if action["kind"] == "speak":
                    dialogue_rows.append((action["actor"], use["text"]))
                    _need(isinstance(action["voice"], dict), ap + ".voice", "requires explicit voice decisions")
                    voice_entries.append((action['actor'], use['text'], action['voice']))
                    try:
                        from voice_sequence import build_voice_sequence
                        fields["音色"] = build_voice_sequence(voice_entries)
                    except (ContractBlockError, TypeError) as exc:
                        raise ProductionDecisionError(ap + ".voice", str(exc)) from exc
                    source = {"shot_id": sid, "dialogue_index": len(dialogue_rows) - 1}; audio_kind = "dialogue"
                else:
                    prefix = f"{action['actor']}内心独白：“" if action['kind']=='inner_voice' else "客观叙述：“"
                    char_start = len(psychological_text) + len(prefix)
                    psychological_text += prefix + use["text"] + '”；'
                    fields["心理音轨"] = psychological_text + psychological_safety_suffix()
                    source = {"shot_id": sid, "char_range": [char_start, char_start + len(use["text"])]}; audio_kind = "psychological"
                profile = audio_profiles[action["id"]]
                pauses = action.get('pauses', [])
                segments = _audio_segments(use["text"], world_pieces, sid, profile, ap + ".audio_alignment", pauses)
                timing["utterances"].append({"id": action["id"], "kind": audio_kind, "speaker": action["actor"] or "心理音轨",
                    "source": source, "segments": segments, "profile_id": profile["id"]})
                if action.get("timing", {}).get("selection_evidence") is not None:
                    timing["utterances"][-1]["selection_evidence"] = copy.deepcopy(action["timing"]["selection_evidence"])
                if pauses:
                    timing['utterances'][-1]['pauses'] = copy.deepcopy(pauses)
                audio_alignments.append({"id": action["id"], "profile_id": profile["id"], "method": "predicted_integer_character_partition",
                                         "is_measured": False, "source_text_sha256": _hash(use["text"]), "segments": segments,
                                         **({'pauses': copy.deepcopy(pauses), 'source_use_id': action['source_use_id'],
                                             'pause_semantic_status': 'pending_independent_review'} if pauses else {})})
            projection_dependencies[action["id"]] = [action["id"]] if is_audio else fragment_ids[-1:] or inherited
            world_events.append({"id": action["id"], "shot_id": sid, "actor": action["actor"], "kind": action["kind"],
                                 "start": action["start"] / 10, "end": action["end"] / 10, "after": action["after"],
                                 "effect_occurrences": world["completed"].count(action["id"]), "visible_projection_ids": fragment_ids if not is_audio else [],
                                 "audio_id": action["id"] if is_audio else None,
                                 "projected_after": inherited, "projection_terminal_dependencies": projection_dependencies[action["id"]]})
            action_records.append({"id": action["id"], "kind": action["kind"], "before_sha256": info["before_world_sha256"],
                                   "after_sha256": _hash(samples[action["end"]]["world"]), "world_action_description": sentence,
                                   "rendered_sentences": rendered_sentences,
                                   "mouth_sync_intervals": [[a / 10, b / 10] for a, b in mouth_intervals],
                                   "speaking_gaze_check": gaze_check,
                                   "performance_binding": info.get("restraint_binding"),
                                   "phase_fragments": fragment_ids, "reads_after": action["after"], "projected_after": inherited,
                                   "projection_status": "audio" if is_audio else "hidden" if not pieces else "partial" if partial_blink else "visible",
                                   "source_use_id": action["source_use_id"]})
            used_events[action["id"]] = elapsed + action["end"]
        _production_plan_call(plan_guard.check_shot, row, samples, actions, action_records)
        fields['可见主体'] = _shot_visible_subjects(first_view, samples, fields['可见主体'])
        visibility = {}
        for identity in world["actors"]:
            windows = []; opened = None
            for t in range(ticks[-1]):
                yes = samples[t]["view"]["actors"].get(identity, {}).get("eye_visible", False)
                if yes and opened is None: opened = t
                if not yes and opened is not None: windows.append([opened, t]); opened = None
            if opened is not None: windows.append([opened, ticks[-1]])
            visibility[identity] = windows
        for i, (a, b) in enumerate(zip(ticks, ticks[1:])):
            for identity, windows in visibility.items():
                pieces = [(max(a, lo), min(b, hi)) for lo, hi in windows if max(a, lo) < min(b, hi)]
                if pieces == [(a, b)]: phases[i]["text"] += f"{identity}眼睛清楚可见。"
                elif pieces:
                    for lo, hi in pieces: phases[i]["text"] += f"{identity}眼睛仅在{lo / 10:.1f}—{hi / 10:.1f}秒清楚可见。"
                elif identity in samples[a]["view"]["actors"]: phases[i]["text"] += f"{identity}眼睛不入画。"
                if pieces:
                    # Stable actually held gaze is a state projection, not a
                    # newly invented refocus action or a copied endpoint.
                    states = [samples[t]["world"]["actors"][identity] for lo, hi in pieces for t in range(lo, hi)]
                    gazes = [state["gaze"] for state in states]
                    if gazes and all(g is not None and g["ref"] == gazes[0]["ref"] and g["position_xyz"] == gazes[0]["position_xyz"] for g in gazes):
                        target = _reference(samples[a]['world'], gazes[0]['ref'], cam)
                        gaze_text = f"{identity}视线持续停在{target}。"
                        if gaze_text not in phases[i]['text']: phases[i]['text'] += gaze_text
                        head_text = f"{identity}头部保持不动。"
                        if all(state["head_yaw"] == states[0]["head_yaw"] for state in states) and head_text not in phases[i]['text']:
                            phases[i]["text"] += head_text
                        if not any(lo >= b or hi > b for lo, hi in windows):
                            phases[i]["text"] += f"{identity}最终视线停在{target}。"
            if not phases[i]["text"]:
                sample = samples[a]
                phases[i]['text'] = ''.join(f"{name}保持{sample['world']['actors'][name]['posture']}。"
                                           for name in sample['view']['actors'])
                phases[i]['text'] += ''.join(f"{_reference(sample['world'], {'kind': 'prop', 'id': name}, cam)}位于{_screen_place(_anchor(sample['world'], sample['world']['props'][name])[1], cam)}。"
                                            for name in sample['view']['props'])
                if not phases[i]['text']:
                    phases[i]['text'] = f"{start_world['scenes'][row['scene_id']]['label']}空景。"
        try:
            environment_fields, environment_binding = render_environment(
                row["editorial"], initial_world=start_world, final_world=world,
                scene_id=row["scene_id"], actions=actions,
                action_start_worlds={a['id']: samples[a['start']]['world'] for a in actions},
                display_names={a['prop']: _reference(samples[a['start']]['world'], {'kind': 'prop', 'id': a['prop']}, cam)
                               for a in actions if 'prop' in a})
        except EnvironmentDecisionError as exc:
            raise ProductionDecisionError(path + "." + exc.path, exc.reason) from exc
        fields.update(environment_fields)
        current_deliveries = []
        for ref in sorted(set(visible_current_paper_refs)):
            paper = sources['written_objects_by_id'][ref]
            last = 0
            for sample in samples[:-1]:
                prop = sample['world']['props'][paper['carrier_id']]
                if paper['carrier_id'] not in sample['view']['props'] or prop['written_ref'] != ref:
                    break
                last = sample['tick'] + 1
            if last:
                current_deliveries.append({'id': f"state:{sid}:{ref}", 'field': '起始状态',
                    'source_use_id': paper['use_id'], 'text': paper['text'], 'start': 0, 'end': last / 10,
                    'scope': 'current carrier visibility; legibility still requires independent review'})
        end = chain.advance(_state(world), label=sid + "-end", reason="effects of the modelled shot actions")
        rows.append({"shot_id": sid, "shot_uid": row["shot_uid"], "episode_id": row["episode_id"], "duration": ticks[-1] / 10,
                     "source_unit_ids": row["source_unit_ids"], "fields": fields, "staging": staging,
                     "phases": phases, "events": events, "start_state": start.to_dict(), "end_state": end.to_dict(),
                     "transition_explanation": transition})
        records.append({"shot_id": sid, "initial_world_sha256": _hash(start_world), "final_world_sha256": _hash(world),
                        "start_state_sha256": start.sha256, "end_state_sha256": end.sha256,
                        "eye_visibility_ticks": visibility, "actions": action_records, "audio_alignments": audio_alignments, "camera": cam,
                        "visible_current_paper_refs": sorted(set(visible_current_paper_refs)),
                        "environment_binding": environment_binding,
                        "current_written_deliveries": current_deliveries,
                        "physical_scene": row["scene_id"], "continuity_id": row["continuity_id"],
                        **({'inter_shot_bridge': bridge_record} if bridge_record else {})})
        elapsed += ticks[-1]; previous = row
    if source_compilation is not None:
        from source_decisions import validate_source_consumption
        consumption_issues = validate_source_consumption(source_compilation, use_ids=consumed_uses, written_object_ids=consumed_papers)
        _need(not consumption_issues, "source_consumption", "; ".join(x.message for x in consumption_issues))
    for proposal_id in {r[0]["proposal_id"] for r in reviewed_speech}:
        spoken = [r for r in reviewed_speech if r[0]["proposal_id"] == proposal_id]
        _need([r[1] for r in spoken] == spoken[0][0]["ordered_use_ids"]
              and all(a[3] <= b[2] for a, b in zip(spoken, spoken[1:])),
              "source_realization_order", "reviewed utterances must be spoken once in their approved order without overlap")
    source_action_report = _source_action_call(source_action_execution.finish)
    production_plan_report = _production_plan_call(plan_guard.finish)
    return copy.deepcopy({"typed_decisions": {"contract": "typed-authoring-decisions-v2", "project": project, "shots": rows},
                          "derivation": {"contract": CONTRACT, "input_sha256": raw_input_sha256, "scope": SCOPE,
                                         "expanded_decisions_sha256": _hash(payload), "source_action_execution": source_action_report,
                                         "production_prevention": production_plan_report,
                                         "consumed_source_use_ids": sorted(consumed_uses), "consumed_written_object_ids": sorted(consumed_papers),
                                         "shots": records, "world_events": world_events, "final_world": world, "independent_audit_status": "not_performed",
                                         "commit_eligible": False, "semantic_accuracy_percent": None}})


def inspect_production_decisions(payload: dict, *, source_compilation=None, source_unavailable_reason=None) -> dict:
    """Collect independent shapes/cameras, then report effect dependencies honestly.

    A failed earlier action prevents simulation of dependent later states, not
    the independent structural checks of all later shots. This is not preflight.
    """
    findings, checked, not_checked = [], [], []

    def check(path, function):
        try:
            function()
            checked.append(path)
            return True
        except ProductionDecisionError as exc:
            findings.append({"path": exc.path, "message": exc.reason, "stage": "independent_structure"})
            return False

    if not check("root.shape", lambda: _obj(payload, {"contract", "project", "initial_world", "shots"}, "$")):
        return {"findings": findings, "checked": checked, "not_checked": [{"stage": "all_dependents", "reason": "invalid root shape"}]}
    source_unavailable = source_unavailable_reason is not None
    if source_unavailable:
        if not check("source_unavailable_reason", lambda: _text(source_unavailable_reason, "source_unavailable_reason")):
            return {"findings": findings, "checked": checked,
                    "not_checked": [{"stage": "all_dependents", "reason": "invalid source unavailability declaration"}]}
        # Do not substitute an empty successful compilation. Source-dependent
        # checks and rendering remain explicitly unavailable on this branch.
        sources = None
        not_checked.append({"stage": "source_references", "reason": source_unavailable_reason})
    else:
        sources = _source(source_compilation)
    initial = None
    try:
        initial = _world(payload["initial_world"], sources, check_source_refs=not source_unavailable)
        checked.append("initial_world.structure" if source_unavailable else "initial_world")
    except ProductionDecisionError as exc:
        findings.append({"path": exc.path, "message": exc.reason, "stage": "initial_world"})
    rows = payload.get("shots")
    if not isinstance(rows, list):
        findings.append({"path": "shots", "message": "requires array", "stage": "independent_structure"})
        rows = []
    for index, row in enumerate(rows):
        path = f"shots[{index}]"
        if not check(path + ".shape", lambda row=row, path=path: _obj(row, {"shot_id", "shot_uid", "episode_id", "scene_id", "continuity_id", "ticks", "camera", "actions", "editorial", "source_unit_ids", "transition"}, path)):
            continue
        check(path + ".editorial", lambda row=row, path=path: _editorial(row["editorial"], path + ".editorial"))
        if initial is not None and row["scene_id"] in initial["scenes"]:
            check(path + ".camera", lambda row=row, path=path: _camera(initial, row["camera"], row["scene_id"], path + ".camera"))
        else:
            not_checked.append({"stage": path + ".camera", "reason": "physical scene unavailable"})
        if not isinstance(row["actions"], list):
            findings.append({"path": path + ".actions", "message": "requires array", "stage": "independent_structure"})
            continue
        for j, action in enumerate(row["actions"]):
            ap = f"{path}.actions[{j}]"
            def shape(action=action, ap=ap):
                if isinstance(action, dict) and action.get("kind") == "source_step":
                    from source_action_plans import validate_source_step_reference
                    _source_action_call(validate_source_step_reference, action, ap)
                    return
                _need(isinstance(action, dict) and action.get("kind") in ACTION_FIELDS, ap, "unsupported action kind")
                _obj(action, _action_keys(action["kind"], action), ap)
                _tick(action["start"], ap + ".start"); _tick(action["end"], ap + ".end")
            check(ap, shape)
    if source_unavailable:
        not_checked.append({"stage": "state_effect_simulation_and_lowering", "reason": source_unavailable_reason})
    elif findings:
        not_checked.append({"stage": "state_effect_simulation_and_lowering", "reason": "input structural dependencies failed"})
    else:
        try:
            result = lower_production_decisions(payload, source_compilation=source_compilation)
            return {"findings": [], "checked": checked + ["state_effect_simulation_and_lowering"],
                    "not_checked": [{"stage": "full_preflight_and_independent_semantics", "reason": "outside this compiler"}],
                    "lowered": result}
        except ProductionDecisionError as exc:
            findings.append({"path": exc.path, "message": exc.reason, "stage": "state_effect_simulation",
                             **({'status': exc.status, 'code': 'E-PRODUCTION-PLAN-001'} if hasattr(exc, 'status') else {})})
            not_checked.append({"stage": "dependent_state_suffix_and_rendering", "reason": "blocked by " + exc.path})
    return {"findings": findings, "checked": checked, "not_checked": not_checked}
