"""Immutable author snapshots and explicitly named spatial dependencies.

Coordinates are checked against the actual supplied state, never copied into a
gaze automatically. No source, visibility, head motion or continuity certificate
is generated. Legacy free text is reported as unexamined, not interpreted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable

CONTRACT = "production-state-chain-v1"
STATE_FIELDS = frozenset(("人物位置", "身体朝向", "视线落点", "手持道具",
                          "门窗状态", "动作进度", "光源方向"))
SCOPE = ("declared entity identity, scene and coordinates only; no source truth, "
         "line of sight, wall occlusion, anatomy, phase execution or independent PASS")


class ProductionStateError(ValueError):
    def __init__(self, path: str, reason: str):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _require(test: bool, path: str, reason: str) -> None:
    if not test:
        raise ProductionStateError(path, reason)


def _name(value: Any, path: str) -> str:
    _require(isinstance(value, str) and bool(value) and value == value.strip()
             and not any(ord(c) < 32 for c in value), path, "requires an explicit identity/text")
    return value


def _json(value: Any, path: str = "state") -> None:
    if isinstance(value, dict):
        _require(bool(value), path, "empty object is not an explicit unknown")
        for key, child in value.items():
            _name(key, path + ".key"); _json(child, path + "." + key)
    elif isinstance(value, list):
        _require(bool(value) or path == "state.手持道具", path,
                 "only the held-prop list uses [] to declare none")
        for index, child in enumerate(value):
            _json(child, f"{path}[{index}]")
    elif isinstance(value, str):
        _require(bool(value.strip()), path, "empty state text is not an authored unknown")
    elif isinstance(value, bool):
        pass
    else:
        _require(isinstance(value, (int, float)) and math.isfinite(value), path,
                 "requires finite JSON values; null is not an explicit unknown")


def _vector(value: Any, length: int, path: str) -> list[float]:
    _require(isinstance(value, list) and len(value) == length, path,
             f"requires {length} explicit coordinates")
    _require(all(isinstance(x, (int, float)) and not isinstance(x, bool)
                 and math.isfinite(x) for x in value), path,
             "coordinates must be finite numbers, not booleans or strings")
    return list(value)


def _registries(state: dict) -> dict[str, dict]:
    actors = state.get("人物位置")
    progress = state.get("动作进度")
    props = progress.get("物件位置与状态") if isinstance(progress, dict) else None
    return {"actor": actors if isinstance(actors, dict) else {},
            "prop": props if isinstance(props, dict) else {}}


def _identities(state: dict) -> set[tuple[str, str]]:
    return {(kind, name) for kind, rows in _registries(state).items() for name in rows}


def _position(value: Any, path: str) -> tuple[str, str, list]:
    _require(isinstance(value, dict), path, "resolved entity location is not structured")
    scene = _name(value.get("scene_id"), path + ".scene_id")
    _require(scene not in ("未知", "待定", "unknown", "unresolved"), path,
             "unknown location cannot support a resolved gaze")
    key = "position_xyz" if "position_xyz" in value else "position_xy"
    pos = _vector(value.get(key), 3 if key == "position_xyz" else 2, path + "." + key)
    if key == "position_xyz" and "position_xy" in value:
        _require(_vector(value["position_xy"], 2, path + ".position_xy") == pos[:2],
                 path, "declared xy and xyz positions conflict")
    return scene, key, pos


def validate_named_targets(state: dict, *, strict: bool = False,
                           known_entities: Iterable[tuple[str, str]] = ()) -> dict:
    """Check references without changing either the target or observer.

    Strict entries are entity/point references with a resolved/unresolved status.
    Legacy ``{name, position_xy}`` is checked only when its name identifies an
    actor/prop in this state or the supplied sequence. Free text stays unexamined.
    """
    _require(isinstance(state, dict) and set(state) == STATE_FIELDS, "state",
             "requires exactly the seven current continuity fields")
    for key in STATE_FIELDS - {"手持道具"}:
        _require(isinstance(state[key], (str, dict)) and bool(state[key]), key,
                 "requires explicit nonempty state text or object")
    held = state["手持道具"]
    _require(isinstance(held, list), "手持道具", "requires an explicit array; [] declares no held props")
    held_ids = set()
    for index, item in enumerate(held):
        path = f"手持道具[{index}]"
        _require(isinstance(item, dict) and set(item) == {"prop_id", "holder", "state"}, path,
                 "requires exactly prop_id, holder and state")
        for key in item:
            _name(item[key], path + "." + key)
        _require(item["prop_id"] not in held_ids, path, "duplicate held-prop identity")
        held_ids.add(item["prop_id"])
    _json(state)
    registries = _registries(state); known = set(known_entities) | _identities(state)
    gazes = state["视线落点"]
    report = {"contract": CONTRACT, "mode": "explicit" if strict else "recognized_legacy",
              "dependencies": [], "unresolved": [], "unexamined": [], "scope": SCOPE}
    if strict and state["人物位置"] == "无人物" and gazes == "无人物":
        return report
    if not isinstance(gazes, dict):
        _require(not strict, "视线落点", "strict state requires per-actor references or explicit 无人物")
        report["unexamined"].append({"path": "视线落点", "reason": "legacy free text"})
        return report
    if strict:
        _require(isinstance(state["人物位置"], dict), "人物位置", "requires structured actor identities")
        _require(set(gazes) == set(registries["actor"]), "视线落点",
                 "each actor needs its own explicit target or unresolved reason")
    for observer, gaze in gazes.items():
        path = "视线落点." + observer
        _name(observer, path)
        if not isinstance(gaze, dict):
            _require(not strict, path, "requires an explicit entity or point reference")
            report["unexamined"].append({"path": path, "reason": "legacy free text"})
            continue
        explicit = "kind" in gaze or "target_id" in gaze or "status" in gaze
        if strict or explicit:
            kind = gaze.get("kind"); status = gaze.get("status")
            _require(kind in ("entity", "point"), path + ".kind", "requires entity or point")
            _require(status in ("resolved", "unresolved"), path + ".status",
                     "requires resolved or explicit unresolved")
            target_type = gaze.get("target_type") if kind == "entity" else None
            if kind == "entity":
                _require(target_type in ("actor", "prop"), path + ".target_type", "requires actor or prop")
                target = _name(gaze.get("target_id"), path + ".target_id")
            else:
                target = _name(gaze.get("name"), path + ".name")
                _require(not any(target == name for _, name in known), path + ".name",
                         "a named entity cannot be relabeled as a fixed point")
            _require(observer in registries["actor"], path, "observer is absent from 人物位置")
            if status == "unresolved":
                reason = _name(gaze.get("reason"), path + ".reason")
                _require(not any(key in gaze for key in ("position_xy", "position_xyz", "scene_id")),
                         path, "unresolved target must not retain a purported current position")
                report["unresolved"].append({"observer": observer, "kind": kind,
                                              "target": target, "reason": reason})
                continue
        else:
            target = gaze.get("name")
            matches = [(kind, name) for kind, name in known if name == target]
            if not matches:
                report["unexamined"].append({"path": path, "reason": "unregistered legacy point/name"})
                continue
            _require(len(matches) == 1, path, "ambiguous entity name; declare target_type")
            target_type, target = matches[0]; kind = "entity"
            _require(observer in registries["actor"], path, "observer is absent from 人物位置")

        observer_scene, _, _ = _position(registries["actor"][observer], "人物位置." + observer)
        if kind == "point":
            scene, key, actual = _position(gaze, path)
        else:
            _require(target in registries[target_type], path, "named target is absent in this state")
            scene, key, actual = _position(registries[target_type][target], target_type + "." + target)
            # Existing xy-only references can be checked as planar legacy data.
            # Explicit new refs must bind the complete declared vector, including z.
            ref_key = key if strict or explicit or key in gaze else "position_xy"
            expected = actual if ref_key == key else actual[:2]
            received = _vector(gaze.get(ref_key), len(expected), path + "." + ref_key)
            _require(received == expected, path,
                     f"stale named-target coordinates: supplied {received}, actual {expected}; explicitly update or invalidate")
            if "position_xy" in gaze and "position_xyz" in gaze:
                _require(_vector(gaze["position_xy"], 2, path + ".position_xy") ==
                         _vector(gaze["position_xyz"], 3, path + ".position_xyz")[:2], path,
                         "reference xy and xyz positions conflict")
            if strict or explicit or "scene_id" in gaze:
                _require(gaze.get("scene_id") == scene, path + ".scene_id", "target scene binding is stale or missing")
        _require(observer_scene == scene, path,
                 "cross-scene gaze is unresolved; do not infer sight through walls or copy target coordinates")
        report["dependencies"].append({"observer": observer, "kind": kind, "target": target,
                                       "scene_id": scene, key: actual})
    return report


def validate_state_sequence(rows: list[dict], *, strict: bool = False) -> list[dict]:
    """Check actual submitted endpoints, not an unused plan or claimed hash."""
    known = set()
    for row in rows:
        for name in ("start_state", "end_state"):
            if isinstance(row.get(name), dict):
                known.update(_identities(row[name]))
    result = []
    for index, row in enumerate(rows):
        for name in ("start_state", "end_state"):
            try:
                diagnostics = validate_named_targets(row[name], strict=strict, known_entities=known)
            except ProductionStateError as exc:
                raise ProductionStateError(f"shots[{index}].{name}.{exc.path}", exc.reason) from exc
            result.append({"shot_id": row["shot_id"], "endpoint": name, **diagnostics})
    return result


@dataclass(frozen=True, slots=True, init=False)
class StateSnapshot:
    """No reachable mutable state containers; every export is a fresh copy."""
    label: str
    reason: str
    _payload: bytes

    def __init__(self, state: dict, *, label: str, reason: str,
                 known_entities: Iterable[tuple[str, str]] = ()):
        validate_named_targets(state, strict=True, known_entities=known_entities)
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        object.__setattr__(self, "label", _name(label, "snapshot.label"))
        object.__setattr__(self, "reason", _name(reason, "snapshot.reason"))
        object.__setattr__(self, "_payload", payload)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self._payload).hexdigest()

    def to_dict(self) -> dict:
        return json.loads(self._payload)


class ProductionStateChain:
    """Commit validated snapshots atomically; never update dependent gazes."""
    def __init__(self, initial_state: dict, *, label: str, reason: str):
        first = StateSnapshot(initial_state, label=label, reason=reason)
        self._history = (first,)

    @property
    def current(self) -> StateSnapshot:
        return self._history[-1]

    @property
    def history(self) -> tuple[StateSnapshot, ...]:
        return self._history

    def advance(self, next_state: dict, *, label: str, reason: str) -> StateSnapshot:
        _require(label not in {s.label for s in self._history}, "snapshot.label", "history label already exists")
        known = set().union(*(_identities(s.to_dict()) for s in self._history))
        candidate = StateSnapshot(next_state, label=label, reason=reason, known_entities=known)
        self._history = (*self._history, candidate)
        return candidate


def shot_states(start: StateSnapshot, end: StateSnapshot) -> dict:
    """Fresh endpoint exports: editing a caller's dict cannot rewrite history."""
    _require(isinstance(start, StateSnapshot) and isinstance(end, StateSnapshot),
             "shot_states", "requires actual immutable snapshots")
    return {"start_state": start.to_dict(), "end_state": end.to_dict()}
