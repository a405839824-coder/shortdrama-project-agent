"""Freeze an author decision attempt, then render a structurally checked draft.

This is not source verification, semantic audit, publication, or a repair path.
Rejected decisions remain in the attempt store and never produce canonical.json.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, TypedDict
from uuid import uuid4

from jsonschema import Draft202012Validator

from timing_model import validate_timing
from validate_storyboard import (
    VOICE_NO_DIALOGUE_RE, _prop_state_faults, _validate_timeline,
    _voice_template_errors, _validate_punctuation, extract_dialogues,
)
from staging_text_bindings import bind_fields, GeometryInputError
from production_state import (
    CONTRACT as PRODUCTION_STATE_CONTRACT, ProductionStateError,
    validate_state_sequence,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references/storyboard_schema.json"
SCHEMA_BYTES = SCHEMA_PATH.read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)
FIELDS = SCHEMA["properties"]["field_order"]["const"]
COMPUTED = {"镜头编号", "时长", "累计时长", "画面内容", "覆盖原文"}
STATE_FIELDS = SCHEMA["properties"]["prevention_evidence"]["properties"]["adjacent_continuity_audit"]["properties"]["pairs"]["items"]["properties"]["reviewed_fields"]["items"]["enum"]
CONTRACT = "typed-authoring-decisions-v1"
BOUND_CONTRACT = "typed-authoring-decisions-v2"
PRODUCTION_CONTRACT = "production-decisions-v1"


class Phase(TypedDict):
    start: float
    end: float
    text: str


class PropState(TypedDict):
    prop_id: str
    holder: str
    state: str


class ShotDecision(TypedDict):
    shot_id: str
    shot_uid: str
    episode_id: str
    duration: float
    source_unit_ids: list[str]
    fields: dict[str, Any]
    phases: list[Phase]
    events: list[dict[str, Any]]
    start_state: dict[str, Any]
    end_state: dict[str, Any]
    transition_explanation: str


class RejectedDecisions(ValueError):
    def __init__(self, findings: list[dict[str, str]]):
        self.findings = findings
        super().__init__("; ".join(f"{x['path']}: {x['message']}" for x in findings))


class _JSONFloat(float):
    """Retain the numeric token for exact authored time checks before rounding."""
    def __new__(cls, value, literal=None):
        instance = super().__new__(cls, value)
        if not math.isfinite(instance):
            raise ValueError("nonfinite JSON number")
        instance.literal = literal if literal is not None else str(value)
        return instance


def _bind_staging_fields(fields: dict[str, Any], staging: dict) -> tuple[dict, dict]:
    """Adapt only our JSON numeric wrappers at the geometry boundary.

    Keep the parsed decision and its exact time tokens intact. The geometry
    API still rejects external numeric subclasses, booleans and nonnumbers.
    Both canonical rendering and its recorded diagnostics use this adapter.
    """
    def native_numbers(value: Any) -> Any:
        if type(value) is _JSONFloat:
            return float(value)
        if isinstance(value, dict):
            return {key: native_numbers(child) for key, child in value.items()}
        if isinstance(value, list):
            return [native_numbers(child) for child in value]
        return value

    return bind_fields(fields, native_numbers(staging))


def _check(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise RejectedDecisions([{"path": path, "message": message}])


def _text(value: Any, path: str) -> str:
    _check(isinstance(value, str) and bool(value.strip()), path, "requires an explicit nonempty string")
    return value


def _identity(value: Any, path: str) -> str:
    value = _text(value, path)
    _check(value == value.strip() and not re.search(r"[\x00-\x1f\x7f]", value), path,
           "identity must not contain surrounding whitespace or control characters")
    return value


def _ticks(value: Any, path: str, *, positive: bool = False) -> int:
    _check(type(value) in (int, float, _JSONFloat), path, "requires a JSON number, not a string or boolean")
    number = Decimal(value.literal if isinstance(value, _JSONFloat) else str(value))
    _check(number.is_finite() and number >= 0, path, "requires a finite nonnegative time")
    _check(number == number.quantize(Decimal("0.1")), path, "requires exact 0.1-second precision; no rounding")
    _check(not positive or number > 0, path, "requires positive duration")
    return int(number * 10)


def _state_content(value: Any, path: str) -> None:
    """Reject empty placeholders, while preserving actual numeric coordinates."""
    if isinstance(value, str):
        _text(value, path)
    elif isinstance(value, dict):
        _check(bool(value), path, "state object cannot be empty; write an explicit unknown instead")
        for key, child in value.items():
            _identity(key, path + ".key")
            _state_content(child, f"{path}.{key}")
    elif isinstance(value, list):
        _check(bool(value), path, "state array cannot be empty; write an explicit unknown instead")
        for index, child in enumerate(value):
            _state_content(child, f"{path}[{index}]")
    else:
        _check(isinstance(value, (int, float)) and math.isfinite(value), path,
               "state values must be explicit text, objects, arrays, finite coordinates or booleans; null is not an authored unknown")


def _state(value: Any, path: str) -> None:
    _check(isinstance(value, dict), path, "requires a seven-field state object")
    _check(set(value) == set(STATE_FIELDS), path, "requires exactly the current seven continuity fields")
    for field in STATE_FIELDS:
        if field != "手持道具":
            _check(isinstance(value[field], (str, dict)) and bool(value[field]), f"{path}.{field}",
                   "requires an authored nonempty string or object, including explicit unknowns where applicable")
            _state_content(value[field], f"{path}.{field}")
    props = value["手持道具"]
    _check(isinstance(props, list), f"{path}.手持道具", "requires an explicit array of prop_id/holder/state; [] declares no held props")
    ids: set[str] = set()
    for index, prop in enumerate(props):
        where = f"{path}.手持道具[{index}]"
        _check(isinstance(prop, dict) and set(prop) == {"prop_id", "holder", "state"}, where,
               "requires exactly prop_id, holder and state")
        for key in ("prop_id", "holder", "state"):
            _text(prop[key], f"{where}.{key}")
        _identity(prop["prop_id"], f"{where}.prop_id")
        _identity(prop["holder"], f"{where}.holder")
        _check(prop["prop_id"] not in ids, where, "duplicate prop identity in one state")
        ids.add(prop["prop_id"])
        faults = _prop_state_faults({prop["prop_id"]: {"holder": prop["holder"], "state": prop["state"]}}, strict=True)
        _check(not faults, where, "; ".join(faults))


def _check_staging_marker_locations(value: Any, path: tuple = ()) -> None:
    """v2 reserves its marker syntax; source collisions are rejected, not edited."""
    if isinstance(value, str):
        designated = (len(path) == 4 and path[0] == "shots" and type(path[1]) is int
                      and path[2] == "fields" and path[3] in {"机位", "起始状态"})
        _check(designated or "[[staging:" not in value, ".".join(map(str, path)) or "$",
               "reserved staging marker outside initial binding fields; preserve literal source using v1 instead of altering it")
    elif isinstance(value, dict):
        for key, child in value.items():
            _check(not isinstance(key, str) or "[[staging:" not in key,
                   ".".join(map(str, path)) or "$", "reserved staging marker in an object key")
            _check_staging_marker_locations(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_staging_marker_locations(child, (*path, index))


def _state_dependency_diagnostics(decisions: dict) -> list[dict]:
    evidence = decisions["project"]["prevention_evidence"]
    authored = evidence.get("authoring_evidence", {})
    declared = authored.get("production_state_contract") if isinstance(authored, dict) else None
    _check(declared in (None, PRODUCTION_STATE_CONTRACT),
           "project.prevention_evidence.authoring_evidence.production_state_contract",
           "unknown production state contract")
    _check(declared is None or decisions["contract"] == BOUND_CONTRACT,
           "contract", "explicit production state contract requires typed v2")
    if decisions["contract"] != BOUND_CONTRACT:
        return []
    try:
        return validate_state_sequence(decisions["shots"], strict=declared == PRODUCTION_STATE_CONTRACT)
    except ProductionStateError as exc:
        raise RejectedDecisions([{"path": exc.path, "message": exc.reason}]) from exc


def _render_decisions(decisions: dict[str, Any]) -> dict[str, Any]:
    """Internal pure renderer; record_attempt is the production library entry.

    No input mutation, default actor, missing prop state, voice/eye prose, source
    entailment or adjacent continuity conclusion is created here.
    """
    _check(isinstance(decisions, dict) and set(decisions) == {"contract", "project", "shots"}, "$",
           "requires exactly contract, project and shots")
    _check(decisions["contract"] in (CONTRACT, BOUND_CONTRACT), "contract",
           f"requires {CONTRACT} or {BOUND_CONTRACT}")
    bound_staging = decisions["contract"] == BOUND_CONTRACT
    if bound_staging:
        _check_staging_marker_locations(decisions)
    project = copy.deepcopy(decisions["project"])
    _check(isinstance(project, dict), "project", "requires an object")
    fixed = {key: SCHEMA["properties"][key]["const"] for key in
             ("project_version", "schema_version", "schema_profile", "workflow_profile", "field_order")}
    _check(not (set(project) & (set(fixed) | {"shots", "total_duration"})), "project",
           "computed root metadata must not have a second authored copy")
    evidence = project.get("prevention_evidence")
    _check(isinstance(evidence, dict), "project.prevention_evidence", "requires caller-authored evidence")
    _check("scene_state_ledger" not in evidence, "project.prevention_evidence.scene_state_ledger",
           "states belong to shot decisions; conflicting evidence copies are refused")
    audio = evidence.get("audio_workflow")
    _check(isinstance(audio, dict) and isinstance(audio.get("timing"), dict), "project.prevention_evidence.audio_workflow.timing",
           "requires explicit timing metadata and utterances; no evidence is invented")
    timing = audio["timing"]
    _check("events" not in timing, "project.prevention_evidence.audio_workflow.timing.events",
           "events belong to shot decisions; conflicting copies are refused")
    source_ledger = evidence.get("source_ledger")
    _check(isinstance(source_ledger, list) and bool(source_ledger), "project.prevention_evidence.source_ledger",
           "requires explicit source rows before source_unit_ids can be bound")
    source_ids = []
    for index, unit in enumerate(source_ledger):
        _check(isinstance(unit, dict), f"source_ledger[{index}]", "requires an object")
        source_id = _identity(unit.get("source_id"), f"source_ledger[{index}].source_id")
        _check(not re.search(r"[；;]", source_id), f"source_ledger[{index}].source_id",
               "source identity must not contain the output delimiter")
        source_ids.append(source_id)
    _check(len(set(source_ids)) == len(source_ids), "source_ledger", "duplicate source IDs are ambiguous")
    rows = decisions["shots"]
    _check(isinstance(rows, list) and bool(rows), "shots", "requires at least one complete shot decision")
    shots = []; states = []; events = []; cumulative: dict[str, int] = {}; total = 0
    seen_ids: set[str] = set(); seen_uids: set[str] = set()
    closed_episodes: set[str] = set(); active_episode = None
    required = set(ShotDecision.__annotations__) | ({"staging"} if bound_staging else set())
    for index, row in enumerate(rows):
        where = f"shots[{index}]"
        _check(isinstance(row, dict) and set(row) == required, where,
               "requires exactly the documented ShotDecision fields; no silent field removal")
        sid = _identity(row["shot_id"], where + ".shot_id")
        uid = _identity(row["shot_uid"], where + ".shot_uid")
        episode = _identity(row["episode_id"], where + ".episode_id")
        _check(sid not in seen_ids and uid not in seen_uids, where, "duplicate shot ID or stable UID")
        _check(sid.rsplit("-", 1)[0] == episode, where + ".episode_id", "episode does not match the shot ID")
        _check(episode not in closed_episodes, where + ".episode_id", "episode order cannot reopen a completed episode")
        if active_episode is not None and active_episode != episode:
            closed_episodes.add(active_episode)
        active_episode = episode; seen_ids.add(sid); seen_uids.add(uid)
        duration = _ticks(row["duration"], where + ".duration", positive=True)
        units = row["source_unit_ids"]
        _check(isinstance(units, list) and bool(units), where + ".source_unit_ids", "requires an array of source IDs")
        for unit in units:
            _text(unit, where + ".source_unit_ids")
            _check(unit == unit.strip() and not re.search(r"[；;\r\n]", unit), where + ".source_unit_ids",
                   "source IDs must not contain the output delimiter or surrounding whitespace")
            _check(unit in source_ids, where + ".source_unit_ids", "source ID is absent from the authored source ledger")
        _check(len(set(units)) == len(units), where + ".source_unit_ids", "duplicate source IDs must be resolved by the author")
        fields = row["fields"]
        _check(isinstance(fields, dict) and set(fields) == set(FIELDS) - COMPUTED, where + ".fields",
               "requires exactly the noncomputed canonical fields; 覆盖原文 comes only from source_unit_ids")
        if bound_staging:
            try:
                fields, _ = _bind_staging_fields(fields, row["staging"])
            except GeometryInputError as exc:
                raise RejectedDecisions([{"path": where + "." + exc.path, "message": exc.reason}]) from exc
        phases = row["phases"]
        _check(isinstance(phases, list) and len(phases) == 3, where + ".phases", "requires exactly three phases")
        cursor = 0; rendered = []
        for phase_index, phase in enumerate(phases):
            pp = f"{where}.phases[{phase_index}]"
            _check(isinstance(phase, dict) and set(phase) == {"start", "end", "text"}, pp,
                   "requires exactly start, end and text")
            start = _ticks(phase["start"], pp + ".start")
            end = _ticks(phase["end"], pp + ".end")
            _check(start == cursor and end > start and end <= duration, pp,
                   "phase must be positive, contiguous and inside the shot")
            text = _text(phase["text"], pp + ".text")
            _check(not bound_staging or "[[staging:" not in text, pp + ".text",
                   "initial staging tokens cannot be used in dynamic phases")
            rendered.append(f"【{start / 10:.1f}—{end / 10:.1f}秒】{text}")
            cursor = end
        _check(cursor == duration, where + ".phases", "phases must close exactly on duration")
        picture = "\n".join(rendered)
        faults = _validate_timeline(picture, duration / 10, strict=True,
                                    visible_subjects=fields.get("可见主体"))
        _check(not faults, where + ".phases", "; ".join(faults))
        if bound_staging:
            # Recheck actual assembled fields, including edits after a helper
            # call. The recorded raw input already exists; never repair it here.
            dialogue_rows = extract_dialogues(picture)
            dialogue = "".join(text for _, text in dialogue_rows)
            if dialogue_rows:
                voice_faults = _voice_template_errors(fields["音色"], dialogue, dialogue_rows)
                _check(not voice_faults, where + ".fields.音色", "; ".join(voice_faults))
                punctuation_faults = _validate_punctuation(dialogue, picture)
                _check(not punctuation_faults, where + ".phases",
                       "PUNCT: " + "、".join(punctuation_faults))
            else:
                _check(bool(VOICE_NO_DIALOGUE_RE.fullmatch(str(fields["音色"]).strip())),
                       where + ".fields.音色", "无对白镜必须填写无对白，不适用。")
        for state_name in ("start_state", "end_state"):
            _state(row[state_name], where + "." + state_name)
        _text(row["transition_explanation"], where + ".transition_explanation")
        _check(isinstance(row["events"], list), where + ".events", "requires explicit events array")
        for event_index, event in enumerate(row["events"]):
            ep = f"{where}.events[{event_index}]"
            _check(isinstance(event, dict) and "shot_id" not in event, ep, "shot_id is bound by the owning shot, not a second copy")
            for key in ("id", "actor", "action", "basis"):
                _text(event.get(key), ep + "." + key)
            _identity(event["id"], ep + ".id")
            _identity(event["actor"], ep + ".actor")
            left = _ticks(event.get("start"), ep + ".start")
            right = _ticks(event.get("end"), ep + ".end")
            _check(0 <= left < right <= duration, ep, "event bounds must fit its shot")
            events.append({**copy.deepcopy(event), "shot_id": sid})
        cumulative[episode] = cumulative.get(episode, 0) + duration; total += duration
        authored = {**copy.deepcopy(fields), "镜头编号": sid, "时长": duration / 10,
                    "累计时长": cumulative[episode] / 10, "画面内容": picture, "覆盖原文": "；".join(units)}
        shot = {key: authored[key] for key in FIELDS}
        shot.update(shot_uid=uid, episode_id=episode); shots.append(shot)
        states.append({"shot_id": sid, "scene_id": fields["场景连续性ID"],
                       "start_state": copy.deepcopy(row["start_state"]), "end_state": copy.deepcopy(row["end_state"]),
                       "transition_explanation": row["transition_explanation"]})
    # Check actual endpoints after assembly. Legacy prose is not guessed; the
    # explicit contract checks every actor's resolved or pending dependency.
    _state_dependency_diagnostics(decisions)
    evidence["scene_state_ledger"] = states; timing["events"] = events
    candidate = {**fixed, **project, "shots": shots, "total_duration": total / 10}
    errors = list(Draft202012Validator(SCHEMA).iter_errors(candidate))
    if errors:
        raise RejectedDecisions([{"path": ".".join(map(str, error.absolute_path)), "message": error.message} for error in errors])
    # The shared timeline resolves the real speech sources before checking actor
    # bindings, after/trigger references and mouth-action overlap. Empty speech
    # spans would reject valid dependencies while letting bad references pass.
    timing_faults = validate_timing(candidate, shots)["faults"]
    if timing_faults:
        raise RejectedDecisions([{"path": f"timing:{fault.get('shot_id', '')}", "message": fault["message"]} for fault in timing_faults])
    # Numerical planning remains available to the internal validators. The
    # authored picture field itself is plain action/dialogue prose.
    from picture_presentation import separate_shot_clock
    candidate["shots"] = [separate_shot_clock(shot) for shot in candidate["shots"]]
    return candidate


def _strict_json(raw: bytes) -> dict[str, Any]:
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate JSON key: {key}")
            obj[key] = value
        return obj
    def reject_constant(value):
        raise ValueError(f"nonfinite JSON constant: {value}")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant,
                      parse_float=lambda token: _JSONFloat(token, token))


def _write_once_bytes(path: Path, payload: bytes) -> None:
    """Publish complete bytes exclusively; a failed write never exposes a prefix."""
    pending = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    try:
        with pending.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is atomic and refuses an existing destination on Windows and
        # POSIX; replace()/POSIX rename() would overwrite a frozen artifact.
        os.link(pending, path)
    finally:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass  # an interrupted staging file is not a published canonical


def _write_json(path: Path, value: Any) -> None:
    # Metadata may describe even malformed Unicode arguments without preventing
    # the rejected invocation from being recorded.
    payload = (json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("utf-8")
    _write_once_bytes(path, payload)


def _lower_production_input(decisions, graph_bytes, graph_sha256):
    """Inspect one complete input after record_attempt froze its original bytes."""
    from production_input import _inspect_with_intermediates

    return _inspect_with_intermediates(
        decisions, graph_bytes=graph_bytes, expected_graph_sha256=graph_sha256)


def record_attempt(input_path: Path, attempts_root: Path, *, attempt_id: str | None = None,
                   source_graph_path: Path | None = None, source_graph_sha256: str | None = None) -> dict[str, Any]:
    """Persist every invocation, including invalid JSON and output collisions.

    The attempt root is an append-only set of immutable attempt directories.
    started.json also preserves interrupted attempts without a terminal report.
    """
    attempts_root = Path(attempts_root); attempts_root.mkdir(parents=True, exist_ok=True)
    requested = uuid4().hex if attempt_id is None else attempt_id
    valid_name = (isinstance(requested, str)
                  and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", requested))
                  and not re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", requested))
    collision = not valid_name
    attempt = attempts_root / requested if valid_name else attempts_root / ("rejected-" + uuid4().hex)
    coverage = None
    production = None
    source_compilation = None
    production_input = None
    try:
        attempt.mkdir(exist_ok=False)
    except FileExistsError:
        collision = True
        attempt = attempts_root / ("rejected-" + uuid4().hex)
        attempt.mkdir(exist_ok=False)
    started = {"contract": CONTRACT, "attempt_id": attempt.name,
               "requested_attempt_id": requested if isinstance(requested, str) else repr(requested),
               "input_path": str(Path(input_path).absolute()), "status": "started",
               "created_at": datetime.now(timezone.utc).isoformat(), "include_in_attempt_accounting": True}
    _write_json(attempt / "started.json", started)
    report = {**started, "status": "rejected", "input_sha256": None, "canonical_sha256": None,
              "canonical_path": None, "semantic_accuracy_percent": None, "commit_eligible": False,
              "checked_scope": "schema, source IDs, phase/time structure, speech/event references and actors, registered text timing budget, prop types",
              "not_checked": ["source truth or full coverage", "independent semantic audit", "all-pair continuity closure",
                              "audio/video measurement", "publication gates"],
              "schema_sha256": hashlib.sha256(SCHEMA_BYTES).hexdigest(),
              "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "production_state_validator_sha256": hashlib.sha256(
                  Path(__file__).with_name("production_state.py").read_bytes()).hexdigest()}
    try:
        raw = Path(input_path).read_bytes()
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        _write_once_bytes(attempt / "input.original.json", raw)
        _check(not collision, "attempt_id", "invalid or existing attempt ID; prior files remain unchanged")
        decisions = _strict_json(raw)
        report["input_contract"] = decisions.get("contract") if isinstance(decisions, dict) else None
        if report['input_contract'] == PRODUCTION_CONTRACT:
            # Raw bytes are already frozen. Production coordinates use the
            # ordinary JSON number contract; typed time-token precision remains
            # untouched on the legacy paths.
            decisions = json.loads(json.dumps(decisions, allow_nan=False))
            production_input = copy.deepcopy(decisions)
            graph_bytes = None
            graph_digest = source_graph_sha256
            if source_graph_path is not None:
                graph_bytes = Path(source_graph_path).read_bytes()
                _write_once_bytes(attempt / 'source_graph.original.json', graph_bytes)
                actual_graph_digest = hashlib.sha256(graph_bytes).hexdigest()
                report['source_graph_sha256'] = actual_graph_digest
                report['expected_source_graph_sha256'] = graph_digest
                # If no separate caller digest was supplied, this pins the file
                # read at this invocation. It does not authenticate its author.
                if graph_digest is None:
                    graph_digest = actual_graph_digest
                report['source_graph_identity_basis'] = ('caller_digest' if source_graph_sha256 is not None
                                                          else 'file_bytes_at_attempt_start')
            production, source_compilation, coverage = _lower_production_input(decisions, graph_bytes, graph_digest)
            if coverage['findings']:
                raise RejectedDecisions(coverage['findings'])
            _check(coverage['ready_for_canonical'], 'production_input',
                   'required author-input checks are incomplete; inspect check_coverage')
            _check(production is not None, 'production', 'lowering did not produce typed decisions')
            _write_json(attempt / 'production_derivation.json', production['derivation'])
            _write_json(attempt / 'source_compilation.json', source_compilation.to_dict())
            decisions = production['typed_decisions']
            _write_json(attempt / 'typed_decisions.derived.json', decisions)
            report['checked_scope'] += '; source declarations and actual use consumption; modelled event effects and derived state/time/camera'
        if production is None:
            # Production input already ran the same typed checks before its
            # required-performance inspection. Legacy typed entry stays intact.
            from authoring_preflight import inspect_typed_decisions
            coverage = inspect_typed_decisions(decisions, renderer=sys.modules[__name__])
            if coverage['findings']:
                raise RejectedDecisions(coverage['findings'])
        if decisions["contract"] == BOUND_CONTRACT:
            report["checked_scope"] += "; actual assembled v2 VOICE/PUNCT lexical contracts; declared/recognized named-target state dependencies"
        candidate = _render_decisions(decisions)
        coverage['checks'].append({'check': 'canonical_assembly', 'status': 'pass'})
        if production is not None:
            from production_derivation import attach_derivation
            candidate = attach_derivation(candidate, production_input, graph_bytes,
                                          production=production, source_compilation=source_compilation)
            report['production_binding_sha256'] = candidate['prevention_evidence']['production_derivation']['binding_sha256']
        if decisions["contract"] == BOUND_CONTRACT:
            state_diagnostics = _state_dependency_diagnostics(decisions)
            _write_json(attempt / "production_state_dependencies.json", state_diagnostics)
            report["production_state_dependencies_path"] = str((attempt / "production_state_dependencies.json").resolve())
            report["checked_scope"] += " (unexamined legacy state text reported)"
            binding_diagnostics = [{"shot_id": row["shot_id"],
                                    **_bind_staging_fields(row["fields"], row["staging"])[1]}
                                   for row in decisions["shots"]]
            _write_json(attempt / "initial_staging_bindings.json", binding_diagnostics)
            report["initial_staging_bindings_path"] = str((attempt / "initial_staging_bindings.json").resolve())
            report["checked_scope"] += "; explicitly bound initial planar camera/actor text"
        payload = (json.dumps(candidate, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        _write_once_bytes(attempt / "canonical.json", payload)
        report.update(status="rendered_pending_preflight", canonical_sha256=hashlib.sha256(payload).hexdigest(),
                      canonical_path=str((attempt / "canonical.json").resolve()), findings=[])
    except RejectedDecisions as exc:
        report["findings"] = exc.findings
    except Exception as exc:
        # Ordinary parser/validator/I/O exceptions are failed attempts. Process
        # interruption (KeyboardInterrupt/SystemExit) still leaves started.json.
        report["findings"] = [{"path": "$", "message": f"{type(exc).__name__}: {exc}"}]
    if coverage is not None:
        # The fixed prose checked_scope is a capability summary; this trace is
        # the authority for which checks actually ran on this exact attempt.
        if report['status'] == 'rejected':
            coverage['checks'].append({'check': 'canonical_assembly', 'status': 'not_checked'
                                       if coverage['findings'] else 'fail',
                                       'reason': 'input/check dependencies failed' if coverage['findings'] else 'assembly failed'})
            coverage['findings'] = report.get('findings', coverage['findings'])
        coverage['not_checked'] = [x for x in coverage['checks'] if x['status'] == 'not_checked']
        _write_json(attempt / 'check_coverage.json', coverage)
        report['check_coverage_path'] = str((attempt / 'check_coverage.json').resolve())
        report['checks_completed'] = sum(x['status'] in ('pass', 'fail') for x in coverage['checks'])
        report['checks_not_run'] = len(coverage['not_checked'])
        report['check_coverage_sha256'] = hashlib.sha256((attempt / 'check_coverage.json').read_bytes()).hexdigest()
    _write_json(attempt / "report.json", report)
    report["report_path"] = str((attempt / "report.json").resolve())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--attempts-root", type=Path, required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--source-graph", type=Path)
    parser.add_argument("--source-graph-sha256")
    args = parser.parse_args()
    report = record_attempt(args.input, args.attempts_root, attempt_id=args.attempt_id,
                            source_graph_path=args.source_graph, source_graph_sha256=args.source_graph_sha256)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "rendered_pending_preflight" else 2


if __name__ == "__main__":
    raise SystemExit(main())
