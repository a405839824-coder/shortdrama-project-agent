"""Pure narrative declarations and review coverage, not a semantic certifier.

The service owns artifact authority, reviewer identity, append-only history and
signatures. This module checks actual source/request spans and declared state
transitions. It never infers knowledge from prose or treats a reviewer's claim
as independent approval. Plans use chronological scenes in this v1.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


CONTRACT = "narrative-contract-v1"
RULE_IDS = ("source_fidelity", "identity_relations", "knowledge", "time_location",
            "authority", "props", "causality", "goals_style")
QUESTION_IDS = ("causal_or_wording", "new_facts_knowledge_authority",
                "affected_dependencies", "same_rule_check")
SEMANTIC_STATUS = "pending_independent_review"
_PLAN_KEYS = {"contract", "source_artifact_id", "source_sha256", "source_text_sha256", "source_scope",
              "source_map", "authorizations", "goals", "characters", "locations", "permissions", "props",
              "facts", "prior_events", "initial_state", "scenes", "future_dependencies"}
_EVENT_DATA = {
    "move": {"from_location_id", "to_location_id"}, "reveal": {"fact_ids"},
    "learn": {"fact_id", "via_event_id"}, "assert_known": {"fact_id"},
    "grant_permission": {"permission_id", "to_actor_id", "required_permission_id"},
    "use_permission": {"permission_id"}, "transfer_prop": {"prop_id", "to_actor_id"},
    "use_prop": {"prop_id"}, "assert_fact": {"fact_id", "value"},
    "change_fact": {"fact_id", "value", "authorization_id"},
}


def _need(ok, path, message):
    if not ok:
        raise ValueError(f"{path}: {message}")


def _obj(value, keys, path):
    _need(type(value) is dict and set(value) == set(keys), path, "object keys must be " + repr(sorted(keys)))


def _text(value, path):
    _need(isinstance(value, str) and bool(value.strip()), path, "nonempty text required")
    return value


def _id(value, path):
    _need(isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)), path, "stable identifier required")
    return value


def _ids(value, path):
    _need(type(value) is list, path, "identifier list required")
    for item in value:
        _id(item, path)
    _need(len(set(value)) == len(value), path, "duplicate identifiers")
    return value


def _integer(value, path):
    _need(type(value) is int and value >= 0, path, "nonnegative integer required")


def _sha(value, path):
    _need(isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value)), path, "lowercase SHA256 required")


def _clone(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("input: finite JSON values required") from exc


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _span(span, text, path, scope=None):
    _obj(span, {"start", "end", "text"}, path)
    _integer(span["start"], path); _integer(span["end"], path)
    lo, hi = (0, len(text)) if scope is None else (scope["start"], scope["end"])
    _need(lo <= span["start"] < span["end"] <= hi, path, "span outside its exact source scope")
    _need(span["text"] == text[span["start"]:span["end"]], path, "span is not the actual source characters")


def _registry(rows, path, keys):
    _need(type(rows) is list, path, "array required")
    result = {}
    for i, row in enumerate(rows):
        p = f"{path}[{i}]"; _obj(row, keys, p); key = _id(row["id"], p + ".id")
        _need(key not in result, p, "duplicate identity")
        result[key] = row
    return result


def _authorization(authorizations, aid, path, fact_id=None):
    _need(aid in authorizations, path, "missing actual request authorization")
    if fact_id is not None:
        _need(fact_id in authorizations[aid]["fact_ids"], path, "authorization does not declare this fact in its scope")


def _base(plan, source_text=None, request_text=None):
    """Recheck shape even when a service passes its already validated plan."""
    p = _clone(plan); _obj(p, _PLAN_KEYS, "plan")
    _need(p["contract"] == CONTRACT, "plan.contract", "unsupported contract")
    _id(p["source_artifact_id"], "source_artifact_id")
    _sha(p["source_sha256"], "source_sha256")  # Raw artifact bytes: service verifies authority/hash.
    _sha(p["source_text_sha256"], "source_text_sha256")
    _obj(p["source_scope"], {"start", "end"}, "source_scope")
    scope = p["source_scope"]
    _integer(scope["start"], "source_scope.start"); _integer(scope["end"], "source_scope.end")
    _need(scope["start"] < scope["end"], "source_scope", "nonempty scope required")
    maps = _registry(p["source_map"], "source_map", {"id", "start", "end", "text", "fact_ids", "treatment", "authorization_id"})
    cursor = scope["start"]
    for row in maps.values():
        _integer(row["start"], "source_map.start"); _integer(row["end"], "source_map.end")
        _need(row["start"] == cursor and row["start"] < row["end"] <= scope["end"], "source_map", "exact contiguous full scope coverage required")
        _need(isinstance(row["text"], str) and len(row["text"]) == row["end"] - row["start"], "source_map", "mapped character length differs")
        cursor = row["end"]
        _ids(row["fact_ids"], "source_map.fact_ids")
        _need(row["treatment"] in {"retained", "authorized_change", "context", "unknown"}, "source_map.treatment", "unsupported treatment")
    _need(cursor == scope["end"], "source_map", "unmapped source suffix")
    # Candidate/review calls can check declared exact spans from the frozen map;
    # only validate_plan additionally compares them with the actual extraction.
    mapped_text = "".join(row["text"] for row in maps.values())
    bound_text = "\0" * scope["start"] + mapped_text
    if source_text is not None:
        _need(isinstance(source_text, str), "source_text", "actual extracted text required")
        _need(hashlib.sha256(source_text.encode("utf-8")).hexdigest() == p["source_text_sha256"], "source_text_sha256", "extracted text identity mismatch")
        _need(scope["end"] <= len(source_text) and source_text[scope["start"]:scope["end"]] == mapped_text,
              "source_map", "mapped source differs from actual artifact extraction")
        bound_text = source_text
    auth = _registry(p["authorizations"], "authorizations", {"id", "request_span", "fact_ids"})
    for row in auth.values():
        _ids(row["fact_ids"], "authorizations.fact_ids")
        _obj(row["request_span"], {"start", "end", "text"}, "request_span")
        _text(row["request_span"]["text"], "request_span.text")
        _integer(row["request_span"]["start"], "request_span.start")
        _integer(row["request_span"]["end"], "request_span.end")
        _need(row["request_span"]["end"] - row["request_span"]["start"] == len(row["request_span"]["text"]), "request_span", "exact request character range required")
        if request_text is not None:
            _need(isinstance(request_text, str), "request_text", "actual work-order request required")
            _span(row["request_span"], request_text, "request_span")
    chars = _registry(p["characters"], "characters", {"id", "name"})
    for char in chars.values(): _text(char["name"], "characters.name")
    _need(bool(chars), "characters", "at least one identity required")
    for key in ("locations", "permissions", "props"): _ids(p[key], key)
    facts = _registry(p["facts"], "facts", {"id", "subject", "predicate", "value", "source_spans", "policy", "authorization_id"})
    for fact in facts.values():
        _need(fact["subject"] in chars or fact["subject"] in p["props"] or fact["subject"] is None, "facts.subject", "unknown identity")
        _text(fact["predicate"], "facts.predicate")
        _need(fact["policy"] in {"locked", "adaptable", "unknown"}, "facts.policy", "unsupported fact policy")
        _need(type(fact["source_spans"]) is list and bool(fact["source_spans"]), "facts.source_spans", "source evidence required")
        for span in fact["source_spans"]: _span(span, bound_text, "facts.source_spans", scope)
        _need(any(fact["id"] in row["fact_ids"] for row in maps.values()), "facts", "fact missing from source-to-fact mapping")
        if fact["policy"] == "adaptable": _authorization(auth, fact["authorization_id"], "facts.authorization_id", fact["id"])
        else: _need(fact["authorization_id"] is None, "facts.authorization_id", "only adaptable facts declare a change authorization")
    locked = {}
    for fact in facts.values():
        if fact["policy"] == "locked":
            key = (fact["subject"], fact["predicate"])
            _need(key not in locked or locked[key] == fact["value"], "facts", "contradictory locked identity/fact declarations")
            locked[key] = fact["value"]
    for row in maps.values():
        _need(set(row["fact_ids"]) <= facts.keys(), "source_map.fact_ids", "unknown fact")
        if row["treatment"] == "authorized_change":
            _authorization(auth, row["authorization_id"], "source_map.authorization_id")
            for fid in row["fact_ids"]: _authorization(auth, row["authorization_id"], "source_map.authorization_id", fid)
        else: _need(row["authorization_id"] is None, "source_map.authorization_id", "unexpected authorization")
    for row in auth.values(): _need(set(row["fact_ids"]) <= facts.keys(), "authorizations.fact_ids", "unknown fact scope")
    goals = _registry(p["goals"], "goals", {"id", "text", "authorization_id"})
    _need(bool(goals), "goals", "explicit authorized production goal required")
    for goal in goals.values():
        _text(goal["text"], "goals.text"); _authorization(auth, goal["authorization_id"], "goals.authorization_id")
    return p, chars, facts, maps, auth


def _snapshot(state):
    return _clone(state)


def _scene_links(p):
    """Declared dataflow only: no inference of dependencies from prose."""
    prior = {e["id"]: e for e in p["prior_events"]}
    event_scene = {eid: None for eid in prior}
    knowledge, permissions, props, locations, fact_values = {}, {}, {}, {}, {}
    for actor, entries in p["initial_state"]["knowledge"].items():
        for entry in entries: knowledge[(actor, entry["fact_id"])] = (None, {entry["fact_id"]})
    for actor, entries in p["initial_state"]["permissions"].items():
        for entry in entries: permissions[(actor, entry["permission_id"])] = (None, set(prior[entry["via_event_id"]]["fact_ids"]))
    for prop, entry in p["initial_state"]["ownership"].items():
        props[prop] = (None, set(prior[entry["via_event_id"]]["fact_ids"]))
    links, facts_used = {}, {}
    for scene in p["scenes"]:
        sid = scene["id"]; deps, used = set(scene["depends_on"]), set()
        def consume(origin):
            if origin is not None:
                other, fids = origin
                if other is not None and other != sid: deps.add(other)
                used.update(fids)
        for actor in scene["present"]: consume(locations.get(actor))
        for e in scene["events"]:
            actor, kind, data = e["actor_id"], e["kind"], e["data"]
            for dep in e["requires"]:
                other = event_scene.get(dep)
                if other is not None and other != sid: deps.add(other)
            used.update(e["uses_facts"])
            for fid in e["uses_facts"]: consume(knowledge.get((actor, fid)))
            if "fact_id" in data:
                used.add(data["fact_id"])
                consume(fact_values.get(data["fact_id"]))
            if kind == "assert_known": consume(knowledge.get((actor, data["fact_id"])))
            if kind == "reveal": used.update(data["fact_ids"])
            if kind == "learn": knowledge[(actor, data["fact_id"])] = (sid, {data["fact_id"]})
            if kind == "move":
                consume(locations.get(actor)); locations[actor] = (sid, set(e["uses_facts"]))
            if kind in {"use_prop", "transfer_prop"}: consume(props.get(data["prop_id"]))
            if kind == "transfer_prop": props[data["prop_id"]] = (sid, set(e["uses_facts"]))
            if kind in {"use_permission", "grant_permission"}:
                consume(permissions.get((actor, data.get("required_permission_id", data.get("permission_id")))))
            if kind == "grant_permission": permissions[(data["to_actor_id"], data["permission_id"])] = (sid, set(e["uses_facts"]))
            if kind == "change_fact": fact_values[data["fact_id"]] = (sid, {data["fact_id"]})
            event_scene[e["id"]] = sid
        links[sid], facts_used[sid] = deps, used
    return links, facts_used


def _downstream(links, initial):
    result = set(initial)
    while True:
        more = {sid for sid, deps in links.items() if deps & result}
        if more <= result: return result
        result.update(more)


def _simulate(p, chars, facts, maps, auth):
    prior = _registry(p["prior_events"], "prior_events", {"id", "fact_ids", "knowledge", "permissions", "ownership"})
    for event in prior.values():
        _ids(event["fact_ids"], "prior_events.fact_ids")
        _need(bool(event["fact_ids"]) and set(event["fact_ids"]) <= facts.keys(), "prior_events.fact_ids", "actual source fact evidence required")
        for key, keys in (("knowledge", {"actor_id", "fact_id"}), ("permissions", {"actor_id", "permission_id"}), ("ownership", {"prop_id", "owner_id"})):
            _need(type(event[key]) is list, "prior_events." + key, "array required")
            for grant in event[key]:
                _obj(grant, keys, "prior_events." + key)
                if key == "knowledge":
                    _need(grant["actor_id"] in chars and grant["fact_id"] in event["fact_ids"], "prior_events.knowledge", "knowledge needs this event's exact fact evidence")
                elif key == "permissions":
                    _need(grant["actor_id"] in chars and grant["permission_id"] in p["permissions"], "prior_events.permissions", "unknown permission grant")
                else: _need(grant["owner_id"] in chars and grant["prop_id"] in p["props"], "prior_events.ownership", "unknown ownership grant")
    initial = p["initial_state"]
    _obj(initial, {"locations", "knowledge", "permissions", "ownership"}, "initial_state")
    for key in ("locations", "knowledge", "permissions"):
        _need(type(initial[key]) is dict and set(initial[key]) == chars.keys(), "initial_state." + key, "state required for every character separately")
    for actor, location in initial["locations"].items(): _need(location in p["locations"], "initial_state.locations", "unknown location")
    for kind, value_key in (("knowledge", "fact_id"), ("permissions", "permission_id")):
        for actor, entries in initial[kind].items():
            _need(type(entries) is list, "initial_state." + kind, "acquisition records required")
            seen = set()
            for entry in entries:
                _obj(entry, {value_key, "via_event_id"}, "initial_state." + kind)
                value, via = entry[value_key], entry["via_event_id"]
                _need(value not in seen, "initial_state." + kind, "duplicate acquisition")
                seen.add(value)
                _need(via in prior and {"actor_id": actor, value_key: value} in prior[via][kind], "initial_state." + kind, "knowledge/authority has not been acquired through the declared prior event")
    _need(type(initial["ownership"]) is dict and set(initial["ownership"]) <= set(p["props"]), "initial_state.ownership", "unknown prop")
    for prop, ownership in initial["ownership"].items():
        _obj(ownership, {"owner_id", "via_event_id"}, "initial_state.ownership")
        via = ownership["via_event_id"]
        _need(via in prior and {"prop_id": prop, "owner_id": ownership["owner_id"]} in prior[via]["ownership"], "initial_state.ownership", "prop has no declared acquisition")
    state = _snapshot(initial)
    state["fact_values"] = {fid: _clone(fact["value"]) for fid, fact in facts.items()}
    state["completed_events"] = list(prior)
    events = {eid: {"fact_ids": row["fact_ids"], "at": -1, "scene_id": None} for eid, row in prior.items()}
    scenes = _registry(p["scenes"], "scenes", {"id", "start", "end", "location_id", "present", "depends_on", "events"})
    _need(bool(scenes), "scenes", "complete scene plan required")
    trace, blocked, reasons, last_end = [], set(), {}, 0
    unknown_locations, unknown_permissions, unknown_props = set(), set(), set()
    unknown_knowledge, unknown_values = set(), set()
    uncertain_facts = {fid for fid, fact in facts.items() if fact["policy"] == "unknown"}
    uncertain_facts.update(fid for m in maps.values() if m["treatment"] == "unknown" for fid in m["fact_ids"])
    for actor, entries in initial["permissions"].items():
        for entry in entries:
            if set(prior[entry["via_event_id"]]["fact_ids"]) & uncertain_facts: unknown_permissions.add((actor, entry["permission_id"]))
    for prop, entry in initial["ownership"].items():
        if set(prior[entry["via_event_id"]]["fact_ids"]) & uncertain_facts: unknown_props.add(prop)
    all_event_ids = set(prior)
    for scene in scenes.values():
        sid = scene["id"]; path = "scenes." + sid
        _integer(scene["start"], path); _integer(scene["end"], path)
        _need(last_end <= scene["start"] < scene["end"], path, "v1 scenes must be in chronological nonoverlapping story time")
        last_end = scene["end"]
        _need(scene["location_id"] in p["locations"], path, "unknown scene location")
        _ids(scene["present"], path + ".present"); _ids(scene["depends_on"], path + ".depends_on")
        _need(set(scene["present"]) <= chars.keys(), path, "unknown present character")
        prior_scene_ids = {item["scene_id"] for item in trace}
        _need(set(scene["depends_on"]) <= prior_scene_ids, path, "scene dependency missing or not completed earlier")
        _need(type(scene["events"]) is list, path, "events array required")
        pending = ["scene:" + dep for dep in scene["depends_on"] if dep in blocked]
        pending += ["location:" + actor for actor in scene["present"] if actor in unknown_locations]
        used_facts = set()
        previous_at = scene["start"]
        for event in scene["events"]:
            ep = path + ".events"
            _obj(event, {"id", "at", "kind", "actor_id", "requires", "uses_facts", "data"}, ep)
            eid = _id(event["id"], ep); _need(eid not in all_event_ids, ep, "duplicate event identity")
            _integer(event["at"], ep); _need(previous_at <= event["at"] <= scene["end"], ep, "event must occur inside its chronological scene")
            previous_at = event["at"]
            _need(event["kind"] in _EVENT_DATA, ep, "unsupported explicit event kind")
            _obj(event["data"], _EVENT_DATA[event["kind"]], ep + ".data")
            _ids(event["requires"], ep + ".requires"); _ids(event["uses_facts"], ep + ".uses_facts")
            _need(set(event["requires"]) <= all_event_ids, ep, "missing or future prerequisite event")
            pending.extend("event:" + dep for dep in event["requires"] if events.get(dep, {}).get("blocked"))
            all_event_ids.add(eid)
            _need(set(event["uses_facts"]) <= facts.keys(), ep, "unknown fact used")
            _need(event["actor_id"] in scene["present"] or (event["actor_id"] is None and event["kind"] in {"reveal", "assert_fact"}), ep, "event actor is absent")
            used_facts.update(event["uses_facts"])
            if "fact_id" in event["data"]:
                _need(event["data"]["fact_id"] in facts, ep, "unknown event fact")
                used_facts.add(event["data"]["fact_id"])
                if event["data"]["fact_id"] in unknown_values: pending.append("value:" + event["data"]["fact_id"])
            for fid in event["uses_facts"] + ([event["data"]["fact_id"]] if event["kind"] == "assert_known" else []):
                if (event["actor_id"], fid) in unknown_knowledge: pending.append("knowledge:" + fid)
            if event["kind"] == "reveal":
                _ids(event["data"]["fact_ids"], ep)
                _need(set(event["data"]["fact_ids"]) <= facts.keys(), ep, "unknown revealed fact")
                used_facts.update(event["data"]["fact_ids"])
            data, kind = event["data"], event["kind"]
            if kind == "move": _need(data["from_location_id"] in p["locations"] and data["to_location_id"] in p["locations"], ep, "unknown move endpoint")
            if kind in {"use_prop", "transfer_prop"}: _need(data["prop_id"] in p["props"], ep, "unknown prop")
            if kind in {"use_permission", "grant_permission"}: _need(data["permission_id"] in p["permissions"], ep, "unknown permission")
            if kind == "grant_permission": _need(data["required_permission_id"] in p["permissions"], ep, "unknown required permission")
            if kind in {"grant_permission", "transfer_prop"}: _need(data["to_actor_id"] in scene["present"], ep, "recipient absent")
            if kind == "learn": _need(data["via_event_id"] in event["requires"], ep, "acquisition event must be a prerequisite")
            if event["kind"] in {"use_prop", "transfer_prop"} and event["data"]["prop_id"] in unknown_props:
                pending.append("ownership:" + event["data"]["prop_id"])
            if event["kind"] in {"grant_permission", "use_permission"}:
                permission = event["data"].get("required_permission_id", event["data"].get("permission_id"))
                if (event["actor_id"], permission) in unknown_permissions: pending.append("permission:" + permission)
        unknown = {fid for fid in used_facts if facts[fid]["policy"] == "unknown"}
        # Unknown mapped content tied to a used fact also remains unresolved.
        unknown |= {fid for row in maps.values() if row["treatment"] == "unknown" for fid in row["fact_ids"] if fid in used_facts}
        pending += ["fact:" + fid for fid in sorted(unknown)]
        if pending:
            blocked.add(sid); reasons[sid] = sorted(set(pending))
            for event in scene["events"]:
                kind, data = event["kind"], event["data"]
                if kind == "move": unknown_locations.add(event["actor_id"])
                if kind == "transfer_prop": unknown_props.add(data["prop_id"])
                if kind == "grant_permission": unknown_permissions.add((data["to_actor_id"], data["permission_id"]))
                if kind == "learn": unknown_knowledge.add((event["actor_id"], data["fact_id"]))
                if kind == "change_fact": unknown_values.add(data["fact_id"])
                events[event["id"]] = {"fact_ids": [], "at": event["at"], "scene_id": sid, "blocked": True}
            trace.append({"scene_id": sid, "status": "blocked", "start_state": _snapshot(state), "end_state": None})
            continue
        for actor in scene["present"]:
            _need(state["locations"][actor] == scene["location_id"], path, "character present at a location without an arrival/return event: " + actor)
        start = _snapshot(state)
        for event in scene["events"]:
            eid, kind, actor, data = event["id"], event["kind"], event["actor_id"], event["data"]
            ep = path + ".events." + eid
            _need(actor is None or state["locations"][actor] == scene["location_id"], ep, "actor has already left this scene location")
            for dep in event["requires"]:
                _need(dep in events and events[dep]["at"] <= event["at"] and dep in state["completed_events"], ep, "missing, blocked or future prerequisite event: " + dep)
            known = {entry["fact_id"] for entry in state["knowledge"].get(actor, [])}
            _need(actor is None or set(event["uses_facts"]) <= known, ep, "character uses knowledge not yet acquired")
            permission_set = {entry["permission_id"] for entry in state["permissions"].get(actor, [])}
            if kind == "move":
                _need(data["from_location_id"] in p["locations"] and data["to_location_id"] in p["locations"], ep, "unknown move endpoint")
                _need(state["locations"][actor] == data["from_location_id"] and data["from_location_id"] != data["to_location_id"], ep, "second departure or movement without its actual starting location")
                state["locations"][actor] = data["to_location_id"]
            elif kind == "learn":
                via = data["via_event_id"]
                _need(via in event["requires"] and via in state["completed_events"] and data["fact_id"] in events[via]["fact_ids"], ep, "knowledge has no preceding acquisition event for this fact")
                _need(data["fact_id"] not in known, ep, "duplicate knowledge acquisition")
                state["knowledge"][actor].append({"fact_id": data["fact_id"], "via_event_id": eid})
            elif kind == "assert_known": _need(data["fact_id"] in known, ep, "character knows a fact without acquiring it")
            elif kind == "grant_permission":
                _need(data["required_permission_id"] in permission_set and data["permission_id"] in p["permissions"], ep, "grantor lacks the required authority")
                _need(data["to_actor_id"] in scene["present"], ep, "permission recipient absent")
                _need(state["locations"][data["to_actor_id"]] == scene["location_id"], ep, "permission recipient already left")
                _need(data["permission_id"] not in {x["permission_id"] for x in state["permissions"][data["to_actor_id"]]}, ep, "duplicate permission grant")
                state["permissions"][data["to_actor_id"]].append({"permission_id": data["permission_id"], "via_event_id": eid})
            elif kind == "use_permission": _need(data["permission_id"] in permission_set, ep, "authority has not been granted")
            elif kind in {"transfer_prop", "use_prop"}:
                _need(data["prop_id"] in p["props"] and state["ownership"].get(data["prop_id"], {}).get("owner_id") == actor,
                      ep, "prop/gift has not been acquired by this character")
                if kind == "transfer_prop":
                    _need(data["to_actor_id"] in scene["present"] and data["to_actor_id"] != actor, ep, "invalid/absent gift recipient")
                    _need(state["locations"][data["to_actor_id"]] == scene["location_id"], ep, "gift recipient already left")
                    state["ownership"][data["prop_id"]] = {"owner_id": data["to_actor_id"], "via_event_id": eid}
            elif kind == "assert_fact": _need(data["value"] == state["fact_values"][data["fact_id"]], ep, "assertion conflicts with the currently locked/adapted fact")
            elif kind == "change_fact":
                fact = facts[data["fact_id"]]
                _need(fact["policy"] == "adaptable", ep, "locked fact cannot be changed")
                _authorization(auth, data["authorization_id"], ep, fact["id"])
                _need(data["authorization_id"] == fact["authorization_id"], ep, "change does not match this fact's authorized scope")
                state["fact_values"][fact["id"]] = _clone(data["value"])
            event_facts = data.get("fact_ids", []) if kind == "reveal" else ([data["fact_id"]] if kind in {"learn", "assert_fact", "change_fact"} else event["uses_facts"])
            events[eid] = {"fact_ids": event_facts, "at": event["at"], "scene_id": sid}
            state["completed_events"].append(eid)
        trace.append({"scene_id": sid, "status": "checked", "start_state": start, "end_state": _snapshot(state)})
    future = _registry(p["future_dependencies"], "future_dependencies", {"id", "required_event_id", "consumer", "mode", "authorization_id", "replacement_event_id"})
    unresolved_future = []
    for dep in future.values():
        _text(dep["consumer"], "future_dependencies.consumer")
        _need(dep["mode"] in {"preserve", "authorized_change"}, "future_dependencies.mode", "invalid mode")
        if dep["mode"] == "preserve":
            _need(dep["authorization_id"] is None and dep["replacement_event_id"] is None, "future_dependencies", "preserved dependency cannot silently change")
            _need(dep["required_event_id"] in events, "future_dependencies", "future use has no preceding gift/knowledge/event source")
            if dep["required_event_id"] not in state["completed_events"]: unresolved_future.append(dep["id"])
        else:
            _authorization(auth, dep["authorization_id"], "future_dependencies.authorization_id")
            if dep["replacement_event_id"] is not None:
                _need(dep["replacement_event_id"] in events, "future_dependencies", "replacement predecessor missing")
                if dep["replacement_event_id"] not in state["completed_events"]: unresolved_future.append(dep["id"])
    return {"state_trace": trace, "blocked_scene_ids": [sid for sid in scenes if sid in blocked],
            "blocking_reasons": reasons, "unresolved_future_dependency_ids": unresolved_future,
            "declaration_checked_scene_ids": [sid for sid in scenes if sid not in blocked]}


def validate_plan(plan: dict, source_text: str, request_text: str) -> dict:
    p, chars, facts, maps, auth = _base(plan, source_text, request_text)
    result = _simulate(p, chars, facts, maps, auth)
    return {"normalized": p, "plan_sha256": canonical_sha256(p), **result,
            "semantic_status": SEMANTIC_STATUS, "release_eligible": False,
            "source_mapping_scope": "exact character coverage; meaning/completeness requires independent bidirectional review"}


def validate_candidate(plan: dict, candidate: dict) -> dict:
    p, chars, facts, maps, auth = _base(plan)
    checked = _simulate(p, chars, facts, maps, auth)
    c = _clone(candidate); _obj(c, {"contract", "plan_sha256", "scope_scene_ids", "scenes"}, "candidate")
    _need(c["contract"] == CONTRACT and c["plan_sha256"] == canonical_sha256(p), "candidate", "candidate belongs to a different exact plan")
    _ids(c["scope_scene_ids"], "candidate.scope_scene_ids")
    scenes = _registry(c["scenes"], "candidate.scenes", {"id", "text", "event_ids", "end_state"})
    _need(bool(scenes) and list(scenes) == c["scope_scene_ids"], "candidate.scenes", "scene text must completely cover the declared candidate scope in order")
    planned = {scene["id"]: scene for scene in p["scenes"]}
    _need(set(scenes) <= planned.keys(), "candidate.scenes", "unplanned scene")
    _need(list(scenes) == [sid for sid in planned if sid in scenes], "candidate.scenes", "candidate scene order differs from plan")
    trace = {row["scene_id"]: row for row in checked["state_trace"]}
    for sid, scene in scenes.items():
        _text(scene["text"], "candidate.scenes." + sid + ".text")  # Preserve all text exactly; do not sanitize it.
        _need(sid not in checked["blocked_scene_ids"], "candidate.scenes." + sid, "scene depends on unresolved facts/state")
        _ids(scene["event_ids"], "candidate.event_ids")
        _need(scene["event_ids"] == [event["id"] for event in planned[sid]["events"]], "candidate.event_ids", "missing, extra or reordered declared event")
        _need(scene["end_state"] == trace[sid]["end_state"], "candidate.end_state", "candidate state contradicts the actual plan transition")
    return {"normalized": c, "candidate_sha256": canonical_sha256(c), "plan_sha256": c["plan_sha256"],
            "covered_scene_ids": list(scenes), "not_included_scene_ids": [sid for sid in planned if sid not in scenes],
            "semantic_status": SEMANTIC_STATUS, "release_eligible": False,
            "checked_scope": "explicit event/state declarations; scene prose has not been semantically certified"}


def _candidate_span(span, scenes, path):
    _obj(span, {"scene_id", "start", "end", "text"}, path)
    _need(span["scene_id"] in scenes, path, "unknown candidate scene")
    _span({key: span[key] for key in ("start", "end", "text")}, scenes[span["scene_id"]]["text"], path)


def validate_review(plan: dict, candidate_or_None: dict | None, review: dict) -> dict:
    p, chars, facts, maps, auth = _base(plan)
    checked_plan = _simulate(p, chars, facts, maps, auth)
    candidate = validate_candidate(p, candidate_or_None)["normalized"] if candidate_or_None is not None else None
    r = _clone(review)
    _obj(r, {"contract", "stage", "plan_sha256", "candidate_sha256", "scene_checks", "source_to_facts", "facts_to_source", "issues"}, "review")
    stage = "candidate" if candidate is not None else "plan"
    _need(r["contract"] == CONTRACT and r["stage"] == stage and r["plan_sha256"] == canonical_sha256(p), "review", "review stage/plan binding differs")
    _need(r["candidate_sha256"] == (canonical_sha256(candidate) if candidate is not None else None), "review.candidate_sha256", "review targets another candidate")
    scene_ids = candidate["scope_scene_ids"] if candidate is not None else [scene["id"] for scene in p["scenes"]]
    candidate_scenes = {scene["id"]: scene for scene in candidate["scenes"]} if candidate else {}
    planned_scenes = {scene["id"]: scene for scene in p["scenes"]}
    issues = _registry(r["issues"], "review.issues", {"id", "kind", "rule_id", "location", "basis", "conflict", "impact", "suggestion"})
    for issue in issues.values():
        _need(issue["kind"] in {"hard", "unknown", "style", "future"} and issue["rule_id"] in RULE_IDS, "review.issues", "unknown issue category/rule")
        for field in ("basis", "conflict", "impact"): _text(issue[field], "review.issues." + field)
        loc = issue["location"]; _obj(loc, {"scene_id", "event_id", "source_map_id", "candidate_span"}, "review.location")
        _need(any(value is not None for value in loc.values()), "review.location", "exact issue location required")
        if loc["scene_id"] is not None: _need(loc["scene_id"] in scene_ids, "review.location", "scene outside reviewed scope")
        if loc["event_id"] is not None:
            _need(loc["scene_id"] is not None and loc["event_id"] in {e["id"] for e in planned_scenes[loc["scene_id"]]["events"]}, "review.location", "event is not in the located scene")
        if loc["source_map_id"] is not None: _need(loc["source_map_id"] in maps, "review.location", "unknown source-map location")
        if loc["candidate_span"] is not None:
            _need(candidate is not None, "review.location", "plan review cannot cite future candidate text")
            _candidate_span(loc["candidate_span"], candidate_scenes, "review.location.candidate_span")
            _need(loc["scene_id"] in {None, loc["candidate_span"]["scene_id"]}, "review.location", "candidate span belongs to another scene")
        suggestion = issue["suggestion"]
        if suggestion is not None:
            _obj(suggestion, {"change", "answers"}, "review.suggestion"); _text(suggestion["change"], "review.suggestion.change")
            _obj(suggestion["answers"], QUESTION_IDS, "review.suggestion.answers")
            for question in QUESTION_IDS: _text(suggestion["answers"][question], "review.suggestion." + question)
    checks = r["scene_checks"]; _need(type(checks) is list, "review.scene_checks", "array required")
    expected = {(sid, rule) for sid in scene_ids for rule in RULE_IDS}; seen, used_issues, blocked = set(), set(), set(checked_plan["blocked_scene_ids"]) & set(scene_ids)
    for row in checks:
        _obj(row, {"scene_id", "rule_id", "status", "basis", "source_map_ids", "fact_ids", "candidate_spans", "issue_ids"}, "review.scene_checks")
        key = (row["scene_id"], row["rule_id"])
        _need(key in expected and key not in seen, "review.scene_checks", "unknown or duplicate scene/rule cell")
        seen.add(key)
        _need(row["status"] in {"consistent", "not_applicable", "unknown", "issue"}, "review.scene_checks.status", "invalid review assessment")
        _text(row["basis"], "review.scene_checks.basis")
        for name, registry in (("source_map_ids", maps), ("fact_ids", facts), ("issue_ids", issues)):
            _ids(row[name], "review.scene_checks." + name); _need(set(row[name]) <= registry.keys(), "review.scene_checks." + name, "unknown reference")
        _need(type(row["candidate_spans"]) is list, "review.scene_checks.candidate_spans", "array required")
        if candidate is not None:
            _need(bool(row["candidate_spans"]), "review.scene_checks.candidate_spans", "every assessment must cite the actual scene text, including not-applicable explanations")
            for span in row["candidate_spans"]:
                _candidate_span(span, candidate_scenes, "review.scene_checks.candidate_spans")
                _need(span["scene_id"] == row["scene_id"], "review.scene_checks", "evidence from another scene")
        else: _need(not row["candidate_spans"], "review.scene_checks", "plan assessment cannot claim candidate evidence")
        if row["status"] in {"unknown", "issue"}: _need(bool(row["issue_ids"]), "review.scene_checks", "unresolved/conflicting assessment requires located issues")
        else: _need(not row["issue_ids"], "review.scene_checks", "consistent/not-applicable assessment cannot hide issue IDs")
        for iid in row["issue_ids"]:
            issue = issues[iid]
            _need(issue["rule_id"] == row["rule_id"] and issue["location"]["scene_id"] in {None, row["scene_id"]}, "review.scene_checks", "issue does not belong to this cell")
            used_issues.add(iid)
            if issue["kind"] in {"hard", "unknown"}: blocked.add(row["scene_id"])
        if row["status"] == "unknown": blocked.add(row["scene_id"])
    _need(seen == expected, "review.scene_checks", "all scenes times the fixed eight rules must be covered")
    uncertain_source_facts, global_unknown = set(), False
    if stage == "plan":
        for name in ("source_to_facts", "facts_to_source"):
            _need(type(r[name]) is list and all(type(row) is dict for row in r[name]), "review." + name, "array of review objects required")
        source_rows = _registry([{**row, "id": row.get("source_map_id")} for row in r["source_to_facts"]], "review.source_to_facts", {"id", "source_map_id", "fact_ids", "status", "basis"})
        fact_rows = _registry([{**row, "id": row.get("fact_id")} for row in r["facts_to_source"]], "review.facts_to_source", {"id", "fact_id", "source_map_ids", "status", "basis"})
        _need(source_rows.keys() == maps.keys() and fact_rows.keys() == facts.keys(), "review.source_mapping", "complete bidirectional source/fact review required")
        for mid, row in source_rows.items():
            _ids(row["fact_ids"], "review.source_to_facts.fact_ids")
            _need(row["fact_ids"] == maps[mid]["fact_ids"], "review.source_to_facts", "review omits/adds declared mapped facts")
        for fid, row in fact_rows.items():
            _ids(row["source_map_ids"], "review.facts_to_source.source_map_ids")
            _need(row["source_map_ids"] == [mid for mid, mapped in maps.items() if fid in mapped["fact_ids"]], "review.facts_to_source", "reverse fact support incomplete")
        for row in [*source_rows.values(), *fact_rows.values()]:
            _need(row["status"] in {"consistent", "unknown", "issue"}, "review.source_mapping", "invalid source assessment")
            _text(row["basis"], "review.source_mapping.basis")
        for mid, row in source_rows.items():
            if row["status"] != "consistent":
                uncertain_source_facts.update(row["fact_ids"])
                if not row["fact_ids"]: global_unknown = True
        uncertain_source_facts.update(fid for fid, row in fact_rows.items() if row["status"] != "consistent")
    else: _need(r["source_to_facts"] == [] and r["facts_to_source"] == [], "review.source_mapping", "candidate review does not replace a prior plan/source review")
    _need(used_issues == issues.keys(), "review.issues", "every issue must be tied to reviewed scene/rule coverage")
    links, facts_used = _scene_links(p)
    blocked.update(sid for sid, used in facts_used.items() if used & uncertain_source_facts)
    if global_unknown: blocked.update(scene_ids)
    blocked = _downstream(links, blocked) & set(scene_ids)
    clean = [sid for sid in scene_ids if sid not in blocked]
    return {"normalized": r, "review_sha256": canonical_sha256(r), "plan_sha256": r["plan_sha256"],
            "candidate_sha256": r["candidate_sha256"], "covered_scene_ids": scene_ids, "clean_scene_ids": clean,
            "blocked_scene_ids": [sid for sid in scene_ids if sid in blocked], "clean": not blocked,
            "issue_counts": {kind: sum(issue["kind"] == kind for issue in issues.values()) for kind in ("hard", "unknown", "style", "future")},
            "unresolved_source_fact_ids": sorted(uncertain_source_facts),
            "global_source_uncertainty": global_unknown,
            "source_uncertainty_reason": "review found source content without any declared fact identity; affected scenes cannot be localized" if global_unknown else None,
            "semantic_status": "review_submission_validated_identity_and_truth_not_certified", "release_eligible": False}


def validate_repair(plan: dict, previous_candidate: dict, candidate: dict, repair: dict) -> dict:
    """Bind the actual old/new candidates and require declared causal rechecks.

    The old candidate may belong to an earlier signed plan; its authenticity is
    supplied by the service, not by an author-selected previous document here.
    A valid repair record is not an independent semantic resolution.
    """
    p, chars, facts, maps, auth = _base(plan)
    current = validate_candidate(p, candidate)["normalized"]
    previous = _clone(previous_candidate)
    _obj(previous, {"contract", "plan_sha256", "scope_scene_ids", "scenes"}, "previous_candidate")
    _need(previous["contract"] == CONTRACT, "previous_candidate", "unsupported contract")
    _sha(previous["plan_sha256"], "previous_candidate.plan_sha256")
    _ids(previous["scope_scene_ids"], "previous_candidate.scope_scene_ids")
    old = _registry(previous["scenes"], "previous_candidate.scenes", {"id", "text", "event_ids", "end_state"})
    _need(bool(old) and list(old) == previous["scope_scene_ids"], "previous_candidate", "actual old scene scope required")
    for row in old.values():
        _text(row["text"], "previous_candidate.text"); _ids(row["event_ids"], "previous_candidate.event_ids")
        _need(type(row["end_state"]) is dict, "previous_candidate.end_state", "state object required")
    r = _clone(repair)
    _obj(r, {"contract", "plan_sha256", "previous_candidate_sha256", "candidate_sha256", "items", "rechecked_scene_ids"}, "repair")
    _need(r["contract"] == CONTRACT and r["plan_sha256"] == canonical_sha256(p), "repair", "repair belongs to another plan")
    _need(r["previous_candidate_sha256"] == canonical_sha256(previous), "repair.previous_candidate_sha256", "repair does not target the actual preceding candidate")
    _need(r["candidate_sha256"] == canonical_sha256(current), "repair.candidate_sha256", "repair does not target this actual candidate")
    new = {s["id"]: s for s in current["scenes"]}
    ordered_ids = list(dict.fromkeys([*old, *new]))
    changed = {sid for sid in ordered_ids if old.get(sid) != new.get(sid)}
    all_scene_ids = {s["id"] for s in p["scenes"]} | set(old)
    _ids(r["rechecked_scene_ids"], "repair.rechecked_scene_ids")
    _need(set(r["rechecked_scene_ids"]) <= all_scene_ids, "repair.rechecked_scene_ids", "unknown scene")
    _need(type(r["items"]) is list, "repair.items", "array required")
    declared_changed, declared_affected, seen = set(), set(), set()
    links, _ = _scene_links(p)
    for item in r["items"]:
        _obj(item, {"issue_id", "root_cause", "changed_scene_ids", "affected_scene_ids", "resolution", "answers"}, "repair.items")
        _id(item["issue_id"], "repair.issue_id"); _need(item["issue_id"] not in seen, "repair.items", "duplicate issue identity")
        seen.add(item["issue_id"])
        _text(item["root_cause"], "repair.root_cause"); _text(item["resolution"], "repair.resolution")
        _obj(item["answers"], QUESTION_IDS, "repair.answers")
        for qid in QUESTION_IDS: _text(item["answers"][qid], "repair.answers." + qid)
        _ids(item["changed_scene_ids"], "repair.changed_scene_ids"); _ids(item["affected_scene_ids"], "repair.affected_scene_ids")
        local_changed, local_affected = set(item["changed_scene_ids"]), set(item["affected_scene_ids"])
        _need(bool(local_changed) and local_changed <= changed, "repair.changed_scene_ids", "claimed changes do not match actual scene differences")
        _need(local_affected <= all_scene_ids and _downstream(links, local_changed) <= local_affected, "repair.affected_scene_ids", "affected scope omits changed scenes or declared downstream dependencies")
        declared_changed.update(local_changed); declared_affected.update(local_affected)
    _need(declared_changed == changed, "repair.items", "every actual changed/added/removed scene needs a cause and resolution record")
    _need(declared_affected <= set(r["rechecked_scene_ids"]), "repair.rechecked_scene_ids", "affected scene has no declared recheck")
    # A removed preceding scene or newly changed plan has semantics outside the
    # new graph. Record the boundary rather than claiming that graph proved it.
    missing_current = sorted(declared_affected - set(new))
    return {"normalized": r, "repair_sha256": canonical_sha256(r),
            "changed_scene_ids": [sid for sid in ordered_ids if sid in changed],
            "affected_scene_ids": sorted(declared_affected), "not_included_scene_ids": missing_current,
            "previous_plan_changed": previous["plan_sha256"] != current["plan_sha256"],
            "semantic_status": SEMANTIC_STATUS, "release_eligible": False,
            "checked_scope": "actual version differences and declared dependency closure; causal adequacy and repair resolution require independent review"}
