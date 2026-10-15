"""Frozen source-event steps -> actual action parameters -> observed effects.

The source graph owns the plan. This module proves conformance to that declared
plan, not that the plan faithfully interprets prose. All semantic claims remain
pending independent review. It never infers a missing object, result or action.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

CONTRACT = "source-action-plans-v1"
REFERENCE_KEYS = {"id", "kind", "source_use_id", "step_id", "start", "end", "after"}
EXCLUDED_KINDS = {"speak", "narrate", "inner_voice", "write", "scene_light"}
SEMANTIC_STATUS = "pending_independent_review"


class SourceActionPlanError(ValueError):
    def __init__(self, path: str, reason: str):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _need(ok, path, reason):
    if not ok:
        raise SourceActionPlanError(path, reason)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise SourceActionPlanError("source_action_plans", "requires finite JSON: " + str(exc)) from exc


def world_sha256(initial_world: dict) -> str:
    return hashlib.sha256(_json(initial_world)).hexdigest()


def _obj(value, keys, path):
    _need(type(value) is dict and set(value) == set(keys), path, "requires exactly " + ", ".join(sorted(keys)))


def _id(value, path):
    _need(type(value) is str and bool(re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9_-]{1,64}", value)),
          path, "requires a stable identity")
    return value


def _ids(value, path):
    _need(type(value) is list, path, "requires an explicit array")
    for item in value:
        _id(item, path)
    _need(len(set(value)) == len(value), path, "duplicate identity")


def _fields(action_fields=None):
    if action_fields is None:
        # One authoritative action schema; no parallel list of argument fields.
        from production_decisions import ACTION_FIELDS
        action_fields = ACTION_FIELDS
    return {kind: keys for kind, keys in action_fields.items() if kind not in EXCLUDED_KINDS}


def validate_source_step_reference(action: dict, path="source_step") -> None:
    _obj(action, REFERENCE_KEYS, path)
    _need(action["kind"] == "source_step", path + ".kind", "requires source_step reference")
    for key in ("id", "source_use_id", "step_id"):
        _id(action[key], path + "." + key)
    for key in ("start", "end"):
        _need(type(action[key]) is int and 0 <= action[key] <= 36000, path + "." + key, "requires integer tenths")
    _need(action["start"] < action["end"], path, "step must have a positive interval")
    _ids(action["after"], path + ".after")


def source_step(*, action_id, source_use_id, step_id, start, end, after=()) -> dict:
    value = {"id": action_id, "kind": "source_step", "source_use_id": source_use_id,
             "step_id": step_id, "start": start, "end": end, "after": list(after)}
    validate_source_step_reference(value)
    return value


def _validate_plan(plan, *, action_fields=None):
    _obj(plan, {"contract", "initial_world_sha256", "events"}, "source_action_plans")
    _need(plan["contract"] == CONTRACT, "source_action_plans.contract", "unsupported action-plan contract")
    _need(type(plan["initial_world_sha256"]) is str and bool(re.fullmatch(r"[0-9a-f]{64}", plan["initial_world_sha256"])),
          "source_action_plans.initial_world_sha256", "requires the frozen initial-world digest")
    _need(type(plan["events"]) is list and bool(plan["events"]), "source_action_plans.events", "requires source event plans")
    kinds, uses = _fields(action_fields), set()
    for index, event in enumerate(plan["events"]):
        path = f"source_action_plans.events[{index}]"
        _obj(event, {"source_use_id", "steps"}, path)
        uid = _id(event["source_use_id"], path + ".source_use_id")
        _need(uid not in uses, path, "one source use cannot declare a second event plan")
        uses.add(uid)
        _need(type(event["steps"]) is list and bool(event["steps"]), path + ".steps", "requires actual steps")
        prior, effects, transfers = set(), set(), set()
        for number, step in enumerate(event["steps"]):
            sp = f"{path}.steps[{number}]"
            _obj(step, {"step_id", "kind", "parameters", "after"}, sp)
            sid = _id(step["step_id"], sp + ".step_id")
            _need(sid not in prior, sp, "duplicate source step")
            kind = step["kind"]
            _need(type(kind) is str and kind in kinds, sp + ".kind", "source action kind is not supported")
            _obj(step["parameters"], kinds[kind], sp + ".parameters")
            _ids(step["after"], sp + ".after")
            _need(set(step["after"]).issubset(prior), sp + ".after", "plan dependencies must name earlier steps in this event")
            signature = _json({"kind": kind, "parameters": step["parameters"]})
            _need(signature not in effects, sp, "duplicate declared effect in one source event; separate genuine events need separate source facts")
            if kind == "transfer":
                prop = _id(step["parameters"]["prop"], sp + ".parameters.prop")
                _need(prop not in transfers, sp, "one source event cannot hand over the same object twice")
                transfers.add(prop)
            effects.add(signature)
            prior.add(sid)
    _json(plan)


def construct_source_action_plans(*, initial_world: dict, events: list[dict], action_fields=None) -> dict:
    """Build the graph's plan before its existing external graph freeze.

    Steps carry kind and parameters once; actor comes from the source use.
    This constructor does not infer that a plan is entailed by the prose.
    """
    result = {"contract": CONTRACT, "initial_world_sha256": world_sha256(initial_world),
              "events": copy.deepcopy(events)}
    _validate_plan(result, action_fields=action_fields)
    return result


def migrate_source_actions(payload: dict, source_compilation) -> dict:
    """Explicit legacy migration, to be reviewed and frozen in the source graph.

    Returns a fresh production payload and proposed graph.action_plans. It never
    mutates a frozen graph, adds an approval, or silently executes the migration.
    Existing duplicate-effect plans are rejected, rather than relabelled valid.
    """
    from source_decisions import SourceCompilation
    _need(isinstance(source_compilation, SourceCompilation), "source_compilation", "requires real SourceCompilation")
    sources = source_compilation.to_dict()
    result = copy.deepcopy(payload)
    grouped, originals = {}, {}
    for row in result["shots"]:
        for action in row["actions"]:
            use = sources["uses_by_id"].get(action.get("source_use_id"))
            if use and use["mode"] == "physical_action":
                _need(action.get("kind") != "source_step", "migration", "input already contains a source_step")
                grouped.setdefault(use["id"], []).append(action)
                originals[action["id"]] = use["id"]
    events, migrations = [], []
    for uid, actions in grouped.items():
        steps = []
        actor = sources["uses_by_id"][uid]["actor"]
        for action in actions:
            kind = action["kind"]
            _need(kind in _fields(), "migration", "unsupported physical source action")
            _need(action["actor"] == actor, "migration", "legacy actor differs from the compiled source actor")
            parameters = {key: copy.deepcopy(action[key]) for key in _fields()[kind]}
            steps.append({"step_id": action["id"], "kind": kind, "parameters": parameters,
                          "after": [aid for aid in action["after"] if originals.get(aid) == uid]})
        events.append({"source_use_id": uid, "steps": steps})
    _need(bool(events), "migration", "no physical source actions to migrate")
    plan = construct_source_action_plans(initial_world=result["initial_world"], events=events)
    for row in result["shots"]:
        for index, action in enumerate(row["actions"]):
            uid = originals.get(action["id"])
            if uid is None:
                continue
            row["actions"][index] = source_step(action_id=action["id"], source_use_id=uid, step_id=action["id"],
                                               start=action["start"], end=action["end"],
                                               after=[aid for aid in action["after"] if originals.get(aid) != uid])
            migrations.append({"action_id": action["id"], "source_use_id": uid, "step_id": action["id"]})
    return {"payload": result, "action_plans": plan, "migration": migrations,
            "semantic_status": SEMANTIC_STATUS, "requires_new_frozen_source_graph": True}


def _protected_props(plans, sources, world):
    """Only declared source objects, never names guessed from narrative prose."""
    result = {paper["carrier_id"] for paper in sources["written_objects_by_id"].values()}
    result.update(sources['uses_by_id'][uid]['actor'] for (uid, _), step in plans.items()
                  if step['kind'] == 'surface_effect')
    for step in plans.values():
        parameters = step["parameters"]
        for key in ("prop", "tool"):
            value = parameters.get(key)
            if type(value) is str:
                result.add(value)
        target = parameters.get("target")
        if type(target) is dict and target.get("kind") == "prop" and type(target.get("id")) is str:
            result.add(target["id"])
    pending=list(result)
    while pending:
        anchor=world['props'].get(pending.pop(),{}).get('anchor',{})
        parent=anchor.get('prop', anchor.get('container'))
        if isinstance(parent,str) and parent not in result:
            result.add(parent);pending.append(parent)
    return result


def expand_source_actions(payload: dict, sources: dict, *, action_fields) -> tuple[dict, "SourceActionExecution"]:
    """Generate action bodies from the actual SourceCompilation's frozen plan."""
    raw = copy.deepcopy(payload)
    plan = sources.get("action_plans")
    uses = sources["uses_by_id"]
    physical = {uid for uid, use in uses.items() if use["mode"] == "physical_action"}
    if plan is None:
        _need(not physical, "source_action_plans", "physical source actions require a frozen plan; migrate_source_actions builds an explicit migration")
        plans = {}
    else:
        _validate_plan(plan, action_fields=action_fields)
        _need(plan["initial_world_sha256"] == world_sha256(payload["initial_world"]), "source_action_plans.initial_world_sha256",
              "frozen plan does not match actual initial object/point/world declarations")
        plans, event_ids = {}, set()
        for event in plan["events"]:
            uid = event["source_use_id"]
            _need(uid in physical, "source_action_plans", "plan must bind an actual compiled physical source use")
            use = uses[uid]
            event_id = use["event_ids"][0]
            _need(event_id not in event_ids, "source_action_plans", "source event cannot be enacted by a second plan")
            event_ids.add(event_id)
            for step in event["steps"]:
                plans[(uid, step["step_id"])] = copy.deepcopy(step)
        _need({uid for uid, _ in plans} == physical, "source_action_plans", "every physical source use needs exactly one complete plan")
    protected_props = _protected_props(plans, sources, payload['initial_world'])
    references, action_ids, writes, unbound_prop_edits = {}, set(), set(), []
    for si, row in enumerate(raw["shots"]):
        for ai, action in enumerate(row["actions"]):
            path = f"shots[{si}].actions[{ai}]"
            _need(type(action) is dict, path, "requires action object")
            aid = _id(action.get("id"), path + ".id")
            _need(aid not in action_ids, path, "duplicate stable event identity")
            action_ids.add(aid)
            if action.get("kind") == "source_step":
                validate_source_step_reference(action, path)
                key = (action["source_use_id"], action["step_id"])
                _need(key in plans, path, "unknown frozen source step")
                _need(key not in references, path, "source step already scheduled; new action IDs cannot repeat its occurrence")
                references[key] = (action, path)
            elif action.get("source_use_id") in physical:
                raise SourceActionPlanError(path, "bare physical source action is unbound; use migrate_source_actions then freeze graph.action_plans")
            if action.get("source_use_id") is None:
                affected = action.get("prop") if action.get("kind") in {"move_prop", "transfer"} else None
                if action.get("kind") == "write":
                    paper = sources["written_objects_by_id"].get(action.get("paper_ref"))
                    affected = paper["carrier_id"] if paper else None
                if affected in protected_props:
                    unbound_prop_edits.append(path)
            if action.get("kind") == "write":
                uid = action.get("source_use_id")
                _need(uid not in writes, path, "one declared writing event cannot execute twice")
                writes.add(uid)
    _need(set(references) == set(plans), "source_action_plans", "declared source steps are missing from the actual action schedule")
    _need(not unbound_prop_edits, unbound_prop_edits[0] if unbound_prop_edits else "source_action_plans",
          "null staging action cannot relocate, transfer or rewrite a source-protected prop; declare its source event")
    bindings = {}
    for row in raw["shots"]:
        for index, reference in enumerate(row["actions"]):
            if reference.get("kind") != "source_step":
                continue
            key = (reference["source_use_id"], reference["step_id"])
            step, use = plans[key], uses[key[0]]
            prerequisites = [references[(key[0], sid)][0]["id"] for sid in step["after"]]
            action = {"id": reference["id"], "kind": step["kind"], "actor": use["actor"],
                      "source_use_id": key[0], "start": reference["start"], "end": reference["end"],
                      "after": list(dict.fromkeys(prerequisites + reference["after"])), **copy.deepcopy(step["parameters"])}
            bindings[action["id"]] = {"source_use_id": key[0], "source_event_id": use["event_ids"][0],
                                      "step_id": key[1], "source_use": copy.deepcopy(use), "plan_step": copy.deepcopy(step),
                                      "action": copy.deepcopy(action)}
            row["actions"][index] = action
    return raw, SourceActionExecution(payload["initial_world"], sources.get("graph_sha256"), plan, bindings, protected_props)


def _same(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= 1e-7
    if type(left) is list and type(right) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if type(left) is dict and type(right) is dict:
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    return type(left) is type(right) and left == right


def _hand(actor, hand):
    angle = math.radians(actor["body_yaw"])
    x, y, z = actor["hands"][hand]
    p = actor["position_xyz"]
    return [p[0] + x * math.cos(angle) + y * math.sin(angle),
            p[1] - x * math.sin(angle) + y * math.cos(angle), p[2] + z]


def _target(world, ref):
    if ref["kind"] == "point":
        return world["points"][ref["id"]]["position_xyz"]
    if ref["kind"] == "actor":
        actor = world["actors"][ref["id"]]
        return [*actor["position_xyz"][:2], actor["position_xyz"][2] + actor["eye_height"]]
    anchor = world["props"][ref["id"]]["anchor"]
    if any(k in anchor for k in ('free','worn','binding','prop','container','mount')):
        from production_decisions import _anchor
        return _anchor(world, world['props'][ref['id']])[1]
    return (world["points"][anchor["point"]]["position_xyz"] if "point" in anchor
            else _hand(world["actors"][anchor["actor"]], anchor["hand"]))


def _get(value, keys):
    for key in keys:
        value = value[key]
    return value


def _erase(value, paths):
    out = copy.deepcopy(value)
    for keys in paths:
        parent = out
        for key in keys[:-1]:
            parent = parent[key]
        parent.pop(keys[-1], None)
    return out


class SourceActionExecution:
    """Actual simulator observations; never an independent source certificate."""
    def __init__(self, initial_world, graph_sha256, plan, bindings, protected_props=()):
        self._initial = copy.deepcopy(initial_world)
        self._graph_sha256 = graph_sha256
        self._plan_json = _json(plan)
        self._bindings_json = _json(bindings)
        self._bound_ids = frozenset(bindings)
        self._protected_props = frozenset(protected_props)
        self._starts, self._completed = {}, {}

    def is_bound(self, action_id):
        return action_id in self._bound_ids

    def staging_snapshot(self, action, world):
        if action.get("source_use_id") is not None:
            return None
        return {identity: copy.deepcopy(world["props"].get(identity)) for identity in self._protected_props}

    def observe_staging(self, action, before, world, path):
        if before is None:
            return
        # Actor movement/turn can carry a held object, preserving its actual
        # anchor and inscription. It cannot silently hand it over or rewrite it.
        current = {identity: world["props"].get(identity) for identity in self._protected_props}
        _need(_same(before, current), path,
              "null staging changed a source-protected prop anchor, ownership or written state")

    def _binding(self, action):
        if not self.is_bound(action["id"]):
            return None
        return json.loads(self._bindings_json).get(action["id"])

    def start(self, action, world, path):
        binding = self._binding(action)
        if binding is None:
            return
        clean = {key: value for key, value in action.items() if not key.startswith("_")}
        _need(clean == binding["action"], path, "expanded action drifted from frozen source kind/object/parameters")
        _need(action["id"] not in self._starts, path, "source step has already started")
        self._starts[action["id"]] = copy.deepcopy(world)

    def observe(self, action, before_apply, world, tick, path):
        binding = self._binding(action)
        if binding is None:
            return
        aid, kind, actor_id = action["id"], action["kind"], action["actor"]
        _need(aid in self._starts, path, "source effect has no actual start")
        original = self._starts[aid]
        current = world['actors'].get(actor_id)
        before = original['actors'].get(actor_id)
        end = tick == action["end"]
        fraction = (tick - action["start"]) / (action["end"] - action["start"])
        base = ("actors", actor_id)
        owned, expected, actual = [], {}, {}

        def state(key, suffix, value):
            keys = base + suffix
            owned.append(keys)
            expected[key], actual[key] = value, copy.deepcopy(_get(world, keys))

        from production_actions import FIELDS as EXTENDED_FIELDS, effects as extended_effects
        if kind == 'surface_effect':
            keys = ('props', actor_id, 'surface')
            owned.append(keys)
            expected['surface'] = copy.deepcopy(original['props'][actor_id]['surface'] if tick == action['start'] else action['to'])
            actual['surface'] = copy.deepcopy(_get(world, keys))
        elif kind in EXTENDED_FIELDS:
            for keys, value in extended_effects(original, action, tick).items():
                owned.append(keys)
                label = '/'.join(keys)
                expected[label], actual[label] = copy.deepcopy(value), copy.deepcopy(_get(world, keys))
        elif kind == "move_actor":
            target = self._initial["points"][action["to"]]["position_xyz"]
            destination = [a + (b - a) * fraction for a, b in zip(before["position_xyz"], target)]
            state("position_xyz", ("position_xyz",), destination)
            state("reach_origin_xyz", ("reach_origin_xyz",),
                  [a + b - c for a, b, c in zip(before["reach_origin_xyz"], destination, before["position_xyz"])])
        elif kind == "turn":
            for key in ("body_yaw", "head_yaw"):
                delta = (action[key] - before[key] + 180) % 360 - 180
                state(key, (key,), (before[key] + delta * fraction) % 360)
            # The reach origin is a rigid body-dependent landmark.
            angle = math.radians((expected["body_yaw"] - before["body_yaw"] + 180) % 360 - 180)
            x, y, z = [a - b for a, b in zip(before["reach_origin_xyz"], before["position_xyz"])]
            p = before["position_xyz"]
            state("reach_origin_xyz", ("reach_origin_xyz",), [p[0] + x * math.cos(angle) + y * math.sin(angle),
                                                            p[1] - x * math.sin(angle) + y * math.cos(angle), p[2] + z])
        elif kind in {"point", "move_prop"}:
            target = self._initial["points"][action["finger_to"] if kind == "point" else action["to"]]["position_xyz"]
            start = _hand(before, action["hand"]) if kind == "point" else _target(original, {"kind": "prop", "id": action["prop"]})
            expected["hand_world_xyz"] = [a + (b - a) * fraction for a, b in zip(start, target)]
            actual["hand_world_xyz"] = _hand(current, action["hand"])
            owned.append(base + ("hands", action["hand"]))
            if kind == "move_prop":
                keys = ("props", action["prop"], "anchor")
                owned.append(keys)
                expected["prop_anchor"] = ({"point": action["to"]} if end else {"actor": actor_id, "hand": action["hand"]})
                actual["prop_anchor"] = copy.deepcopy(_get(world, keys))
        elif kind == "transfer":
            keys = ("props", action["prop"], "anchor")
            owned.append(keys)
            expected["prop_anchor"] = ({"actor": action["to_actor"], "hand": action["to_hand"]} if end
                                       else original["props"][action["prop"]]["anchor"])
            actual["prop_anchor"] = copy.deepcopy(_get(world, keys))
        elif kind == "look":
            target = action["target"]
            from production_decisions import EPS
            # Rebinding another identity at the same actual aim point does not
            # create an intervening no-gaze state. Compare the independently
            # recorded start world, never a producer-supplied motion flag.
            held = before["gaze"] if target is not None and before["gaze"] and (
                before["gaze"]["ref"] == target or
                math.dist(before["gaze"]["position_xyz"], _target(original, target)) <= EPS) else None
            gaze = None
            if target is not None and (action["tracking"] or end or held):
                position = held["position_xyz"] if held and not action["tracking"] else _target(before_apply, target)
                gaze = {"ref": target, "position_xyz": position}
            state("gaze", ("gaze",), gaze)
            if action["head_mode"] == "follow" and gaze is not None:
                eye = [*current["position_xyz"][:2], current["position_xyz"][2] + current["eye_height"]]
                position = gaze["position_xyz"]
                state("head_yaw", ("head_yaw",), math.degrees(math.atan2(position[0] - eye[0], position[1] - eye[1])) % 360)
        elif kind == "blink":
            state("eye_state", ("eye_state",), "natural_open" if end or tick == action["start"] else "blinking")
        elif kind == "eyelid":
            state("eyelid", ("performance_state", "eyelid"),
                  action["to"] if end else before["performance_state"]["eyelid"])
        elif kind == "breath":
            state("breath", ("performance_state", "breath"), "resting" if end else action["phase"])
        elif kind == "blink_restraint":
            owned.append(base + ("performance_state", "blink_restraint"))
            actual["restraint_present"] = "blink_restraint" in current["performance_state"]
            expected["restraint_present"] = not end
            if not end:
                actual["reason"] = current["performance_state"].get("blink_restraint", {}).get("reason")
                expected["reason"] = action["reason"]
        elif kind == "focus_adjust":
            expected["gaze"] = before["gaze"]
            actual["gaze"] = current["gaze"]
        elif kind == "hold":
            for key in ("position_xyz", "body_yaw", "hands"):
                expected[key], actual[key] = before[key], current[key]
        else:
            raise SourceActionPlanError(path, "effect verifier does not support this source action kind")
        # Before/after of this actual single _apply call catches extra mutations
        # as well as missing/wrong intended results; concurrent actions are not
        # incorrectly attributed to this event.
        owned.append(("completed",))
        _need(_same(_erase(before_apply, owned), _erase(world, owned)), path,
              "source action modified state outside its declared effect footprint")
        expected_completed = before_apply["completed"] + ([aid] if end else [])
        _need(world["completed"] == expected_completed, path, "source step effect occurrence is not exactly once")
        _need(_same(expected, actual), path, "actual action/object/result differs from the frozen source-step effect")
        if end:
            _need(aid not in self._completed, path, "source step completed more than once")
            self._completed[aid] = {**binding, "graph_sha256": self._graph_sha256,
                                   "expected_effect": copy.deepcopy(expected), "observed_effect": copy.deepcopy(actual),
                                   "effect_occurrences": 1, "semantic_status": SEMANTIC_STATUS,
                                   "actual_start_world_sha256": world_sha256(original),
                                   "actual_end_world_sha256": world_sha256(world)}

    def finish(self) -> dict:
        bindings = json.loads(self._bindings_json)
        _need(set(self._completed) == set(bindings), "source_action_execution", "not all planned effects actually completed")
        return {"contract": CONTRACT, "graph_sha256": self._graph_sha256,
                "plan": json.loads(self._plan_json), "completed_steps": copy.deepcopy(list(self._completed.values())),
                "protected_prop_ids": sorted(self._protected_props),
                "semantic_status": SEMANTIC_STATUS, "source_entailment_proved": False}
