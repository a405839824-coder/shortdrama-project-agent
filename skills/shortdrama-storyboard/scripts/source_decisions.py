"""Pure source-decision bindings. Structural success always remains semantic_pending.

The caller freezes the graph independently of the production input. Hash identity
is not source authority or an entailment signature. No renderer output is read.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

GRAPH_CONTRACT = "source-facts-v1"
DECISIONS_CONTRACT = "source-decisions-v1"
SEMANTIC_STATUS = "pending_independent_review"
MODES = {"physical_action", "spoken", "inner_voice", "objective_narration", "current_written", "write"}
FACT_KEYS = {"id", "subject", "event_id", "polarity", "conditions", "modality", "occurrence", "expression_occurrence", "expression_status", "channel", "content_availability", "content_policy", "content_text", "supports", "depends_on", "required"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def graph_sha256(graph: Mapping[str, Any] | bytes) -> str:
    """Bytes bind exact file bytes; mappings bind canonical JSON, never repr()."""
    return hashlib.sha256(graph if isinstance(graph, bytes) else _json_bytes(graph)).hexdigest()


@dataclass(frozen=True)
class SourceIssue:
    code: str
    path: str
    message: str
    fact_ids: tuple[str, ...] = ()
    global_issue: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": self.message,
                "fact_ids": list(self.fact_ids), "global_issue": self.global_issue}


class SourceDecisionError(ValueError):
    def __init__(self, issues: tuple[SourceIssue, ...]):
        self.issues = issues
        self.global_missing_fact_ids = tuple(dict.fromkeys(
            fid for issue in issues if issue.code == "required_fact_unrepresented" for fid in issue.fact_ids))
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues))


@dataclass(frozen=True)
class SourceUse:
    id: str
    fact_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    mode: str
    wording: str
    actor: str | None
    text: str
    constraints_json: bytes
    spoken_realization_json: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.id, "fact_ids": list(self.fact_ids), "event_ids": list(self.event_ids),
                "mode": self.mode, "wording": self.wording, "actor": self.actor, "text": self.text,
                "source_constraints": json.loads(self.constraints_json), "semantic_status": SEMANTIC_STATUS}
        if self.spoken_realization_json is not None:
            result["spoken_realization"] = json.loads(self.spoken_realization_json)
        return result


@dataclass(frozen=True)
class WrittenObject:
    id: str
    use_id: str
    carrier_id: str
    at: str
    state: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "use_id": self.use_id, "carrier_id": self.carrier_id,
                "at": self.at, "state": self.state, "text": self.text,
                "text_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
                "semantic_status": SEMANTIC_STATUS}

    def render_static(self) -> str:
        if self.state != "current" or self.at != "frame0":
            raise SourceDecisionError((SourceIssue("paper_not_current", self.id, "planned writing cannot render as frame0 paper"),))
        return f"载体{self.carrier_id}当前已有完整文字：“{self.text}”。"


@dataclass(frozen=True)
class SourceCompilation:
    graph_sha256: str
    uses: tuple[SourceUse, ...]
    written_objects: tuple[WrittenObject, ...]
    required_fact_ids: tuple[str, ...]
    represented_fact_ids: tuple[str, ...]
    action_plans_json: bytes | None = None
    production_plan_json: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        # Fresh dictionaries cannot mutate the immutable compilation.
        return {"contract": DECISIONS_CONTRACT, "graph_sha256": self.graph_sha256,
                "semantic_status": SEMANTIC_STATUS,
                "uses_by_id": {u.id: u.to_dict() for u in self.uses},
                "written_objects_by_id": {w.id: w.to_dict() for w in self.written_objects},
                "action_plans": json.loads(self.action_plans_json) if self.action_plans_json is not None else None,
                "production_plan": json.loads(self.production_plan_json) if self.production_plan_json is not None else None,
                "required_fact_ids": list(self.required_fact_ids),
                "structurally_referenced_fact_ids": list(self.represented_fact_ids),
                "global_missing_fact_ids": [], "semantic_accuracy_percent": None,
                "release_eligible": False,
                "review_obligations": [self._obligation(u) for u in self.uses]}

    def _obligation(self, use: SourceUse) -> dict[str, Any]:
        payload = {"graph_sha256": self.graph_sha256, "actual_source_use": use.to_dict(),
                   "written_objects": [paper.to_dict() for paper in self.written_objects if paper.use_id == use.id]}
        if self.production_plan_json is not None:
            from production_plan import digest as plan_digest
            plan = json.loads(self.production_plan_json)
            payload['production_plan_review'] = {
                'plan_sha256': plan_digest(plan),
                'source_fact_ids': list(use.fact_ids),
                'required_questions': ['does the source support the frozen premise and setting',
                    'do initial knowledge and belief/pretence have actual source support',
                    'do director choices preserve the fact and make the whole shot deliverable'],
                'related_shots': [shot['shot_id'] for shot in plan['shots'] if set(shot['purpose_fact_ids']) & set(use.fact_ids)],
                'semantic_status': SEMANTIC_STATUS,
            }
        return {"id": "source-decision:" + hashlib.sha256(_json_bytes(payload)).hexdigest(),
                "kind": "adapted_dialogue" if use.mode == "spoken" and use.wording == "adapted" else "source_semantics",
                "use_id": use.id, "fact_ids": list(use.fact_ids), "status": SEMANTIC_STATUS,
                "binding": payload, "actual_text_sha256": hashlib.sha256(use.text.encode("utf-8")).hexdigest()}


def _decode(graph: Mapping[str, Any] | bytes) -> dict[str, Any]:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate graph JSON key: " + key)
            result[key] = value
        return result
    if isinstance(graph, bytes):
        value = json.loads(graph.decode("utf-8"), object_pairs_hook=no_duplicates)
        if not isinstance(value, dict):
            raise ValueError("graph must be an object")
        return value
    if not isinstance(graph, Mapping):
        raise ValueError("graph must be an object or frozen UTF-8 bytes")
    return json.loads(_json_bytes(dict(graph)))


def _spoken_format_key(text: str) -> str:
    """Only boundary whitespace and equivalent CJK punctuation width.

    Never remove/reorder words or punctuation, merge internal whitespace, change
    sentence mood, or rewrite quoted literal contents. This is not a synonym or
    semantic equivalence classifier.
    """
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"', "'": "'"}
    width = {",": "，", "?": "？", "!": "！"}
    text = text.strip(" \t\r\n")
    quoted, result = [], []
    for index, char in enumerate(text):
        if quoted and char == quoted[-1]:
            quoted.pop()
        elif char in pairs:
            quoted.append(pairs[char])
        if not quoted and char in width:
            neighbors = text[max(0, index - 1):index] + text[index + 1:index + 2]
            if any("\u3400" <= item <= "\u9fff" for item in neighbors):
                char = width[char]
        result.append(char)
    return "".join(result)


def _spoken_realization(projected, selected, facts, text, wording, source_texts, bad, path, *, channel="oral"):
    """Bind given oral units to exact source characters and preserve their body.

    Qualifier interpretation is still the frozen graph author's decision. This
    hook prevents a producer from changing those decided words or retaining only
    the ID of a separately anchored condition while omitting its source span.
    """
    oral = [f for f in projected if f["channel"] == channel and f["content_availability"] == "given"]
    units = []
    for fact in oral:
        content = fact["content_text"]
        matches = {}
        for support in fact["supports"] if isinstance(fact["supports"], list) else []:
            if not isinstance(support, dict):
                continue
            sid, left, right = support.get("source_id"), support.get("start"), support.get("end")
            actual = source_texts.get(sid) if isinstance(sid, str) else None
            if (not isinstance(actual, str) or type(left) is not int or type(right) is not int
                    or not 0 <= left < right <= len(actual) or actual[left:right] != support.get("text")
                    or not isinstance(content, str) or not content):
                continue
            offset = actual.find(content, left, right)
            while offset >= 0:
                span = {"source_id": sid, "start": offset, "end": offset + len(content), "text": content}
                matches[(sid, offset, offset + len(content))] = span
                offset = actual.find(content, offset + 1, right)
        if not matches:
            bad("spoken_content_not_source_literal", path, "given oral content must be exact characters inside its actual frozen support", [fact["id"]])
            continue
        if len(matches) != 1:
            bad("spoken_source_span_ambiguous", path, "narrow the frozen oral support to one actual occurrence; the compiler does not choose a speaker/event occurrence", [fact["id"]])
            continue
        units.append({"fact_id": fact["id"], "source_span": next(iter(matches.values()))})
    previous_by_source = {}
    for unit in units:
        span = unit["source_span"]
        if span["start"] < previous_by_source.get(span["source_id"], -1):
            bad("spoken_unit_order", path, "spoken source units cannot overlap, duplicate or reverse their source order", [unit["fact_id"]])
        previous_by_source[span["source_id"]] = span["end"]
    # Only qualifiers of the spoken proposition must occur in these words.
    # A prior event's conditions reached through depends_on are background,
    # not an instruction to make the current speaker repeat another utterance.
    condition_ids = []
    pending_conditions = [cid for fact in oral if isinstance(fact["conditions"], list)
                          for cid in fact["conditions"] if isinstance(cid, str)]
    while pending_conditions:
        cid = pending_conditions.pop(0)
        if cid in condition_ids:
            continue
        condition_ids.append(cid)
        condition = facts.get(cid)
        if condition and isinstance(condition["conditions"], list):
            pending_conditions.extend(ref for ref in condition["conditions"] if isinstance(ref, str))
    condition_spans = []
    for cid in condition_ids:
        condition = facts.get(cid)
        retained = []
        for support in condition["supports"] if condition and isinstance(condition["supports"], list) else []:
            if not isinstance(support, dict):
                continue
            sid, left, right = support.get("source_id"), support.get("start"), support.get("end")
            actual = source_texts.get(sid) if isinstance(sid, str) else None
            if (not isinstance(actual, str) or type(left) is not int or type(right) is not int
                    or not 0 <= left < right <= len(actual) or actual[left:right] != support.get("text")):
                continue
            if any(unit["source_span"]["source_id"] == sid and unit["source_span"]["start"] <= left
                   and right <= unit["source_span"]["end"] for unit in units):
                retained.append({"fact_id": cid, "source_span": dict(support)})
        if not retained:
            bad("spoken_condition_unrealized", path, "retained condition needs a precise source span inside the spoken source units; an ID or broad parent paragraph is not a realization", [cid])
        condition_spans.extend(retained)
    expected = "".join(f["content_text"] for f in oral if isinstance(f["content_text"], str))
    if wording == "adapted" and _spoken_format_key(text) != _spoken_format_key(expected):
        bad("spoken_realization_requires_review", path, "unverified semantic paraphrase cannot be produced; preserve the complete source units or obtain a separately implemented genuine pre-production semantic review", [f["id"] for f in selected])
    return {"contract": "source-spoken-realization-v1", "source_units": units,
            "condition_spans": condition_spans, "transform": "exact" if text == expected else "spoken_typography_v1",
            "source_text_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
            "actual_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "semantic_status": SEMANTIC_STATUS}


def construct_source_spoken_use(graph, *, source_texts, expected_graph_sha256,
                                use_id, fact_ids, wording="verbatim", typography="source") -> dict[str, Any]:
    """Construct a single use from frozen source units; no actor/text overrides.

    This partial constructor checks the whole graph's structure but permits
    other required facts to await other uses. Full compilation still checks all
    required facts. No independent review or semantic approval is fabricated.
    """
    g = _decode(graph)
    if not isinstance(fact_ids, (list, tuple)) or not fact_ids or not all(isinstance(fid, str) for fid in fact_ids):
        raise SourceDecisionError((SourceIssue("use_unbound", "fact_ids", "requires explicit ordered source fact IDs"),))
    facts = {f.get("id"): f for f in g.get("facts", []) if isinstance(f, dict) and isinstance(f.get("id"), str)}
    first = facts.get(fact_ids[0], {})
    oral = [facts[fid] for fid in fact_ids if fid in facts and facts[fid].get("event_id") == first.get("event_id")
            and facts[fid].get("channel") == "oral" and facts[fid].get("content_availability") == "given"]
    text = "".join(f.get("content_text", "") for f in oral)
    if typography not in {"source", "chinese_width"} or (typography != "source" and wording != "adapted"):
        raise SourceDecisionError((SourceIssue("spoken_typography", "typography", "only adapted speech may request chinese_width; protected/verbatim output stays exact"),))
    if typography == "chinese_width":
        text = _spoken_format_key(text)
    use = {"id": use_id, "fact_ids": list(fact_ids), "mode": "spoken", "wording": wording,
           "actor": first.get("subject"), "text": text}
    issues, _ = _inspect(graph, {"contract": DECISIONS_CONTRACT, "uses": [use], "written_objects": []},
                         source_texts, expected_graph_sha256)
    blocking = tuple(issue for issue in issues if issue.code != "required_fact_unrepresented")
    if blocking:
        raise SourceDecisionError(blocking)
    return json.loads(_json_bytes(use))


def _inspect(graph, decisions, source_texts, expected_graph_sha256):
    issues: list[SourceIssue] = []
    def bad(code, path, message, ids=(), global_issue=False):
        issues.append(SourceIssue(code, path, message, tuple(ids), global_issue))
    def obj(value, keys, path):
        if not isinstance(value, dict) or set(value) != keys:
            bad("shape", path, "requires exact keys: " + ", ".join(sorted(keys)))
            return False
        return True
    def token(value):
        return isinstance(value, str) and bool(value.strip()) and not re.search(r"[\n\r]", value)
    def ids(value, path):
        valid = isinstance(value, list) and all(token(x) for x in value)
        if not valid or len(set(value)) != len(value):
            bad("references", path, "requires unique nonempty string references")
            return []
        return value
    try:
        digest = graph_sha256(graph)
        g = _decode(graph)
    except (TypeError, ValueError, UnicodeError) as exc:
        bad("graph_decode", "$graph", str(exc), global_issue=True)
        return tuple(issues), None
    if not isinstance(expected_graph_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_graph_sha256) or digest != expected_graph_sha256:
        bad("graph_identity", "$graph", "does not match caller-frozen graph identity", global_issue=True)
    graph_keys = {"contract", "sources", "facts"}
    if "action_plans" in g:
        graph_keys.add("action_plans")
    if "production_plan" in g:
        graph_keys.add("production_plan")
    if not obj(g, graph_keys, "$graph"):
        return tuple(issues), None
    action_plans = g.get("action_plans")
    if "action_plans" in g and (not isinstance(action_plans, dict)
            or action_plans.get("contract") != "source-action-plans-v1"
            or not isinstance(action_plans.get("events"), list)):
        bad("action_plans_shape", "$graph.action_plans", "requires source-action-plans-v1 mapping and events array; action compiler validates its complete plan")
    if g["contract"] != GRAPH_CONTRACT:
        bad("contract", "$graph.contract", "requires " + GRAPH_CONTRACT)
    if not isinstance(source_texts, Mapping) or not isinstance(g["sources"], dict) or not g["sources"]:
        bad("source_identity", "$graph.sources", "requires frozen source IDs and actual text", global_issue=True)
        return tuple(issues), None
    for source_id, expected in g["sources"].items():
        actual = source_texts.get(source_id)
        if not token(source_id) or not isinstance(actual, str) or not isinstance(expected, str) or hashlib.sha256(actual.encode("utf-8")).hexdigest() != expected:
            bad("source_identity", "$graph.sources." + str(source_id), "actual source text does not match frozen SHA256", global_issue=True)
    rows = g["facts"]
    if not isinstance(rows, list) or not rows:
        bad("facts_missing", "$graph.facts", "requires the frozen fact inventory", global_issue=True)
        return tuple(issues), None
    facts = {}
    for index, fact in enumerate(rows):
        path = f"$graph.facts[{index}]"
        if not obj(fact, FACT_KEYS, path):
            continue
        fid = fact["id"]
        if not token(fid) or fid in facts:
            bad("fact_identity", path, "fact ID missing or duplicated", global_issue=True)
            continue
        facts[fid] = fact
        if (fact["subject"] is not None and not token(fact["subject"])) or not token(fact["event_id"]) or type(fact["required"]) is not bool:
            bad("fact_identity", path, "requires subject, stable event ID and explicit required boolean", [fid])
        choices = {"polarity": {"affirmative", "negative"}, "modality": {"asserted", "possible"},
                   "occurrence": {"occurred", "ongoing", "future", "conditional"},
                   "expression_occurrence": {"occurred", "current", "future", "conditional", "not_applicable"},
                   "expression_status": {"asserted", "negated", "uncertain", "not_applicable"},
                   "channel": {"oral", "thought", "report", "written", "action", "narration"},
                   "content_availability": {"given", "unstated", "not_applicable"},
                   "content_policy": {"verbatim", "adaptable", "event_only", "not_applicable"}}
        for key, allowed in choices.items():
            if not isinstance(fact[key], str) or fact[key] not in allowed:
                bad("fact_enum", path + "." + key, "invalid explicit source decision", [fid])
        if not isinstance(fact["content_text"], str) or (fact["content_availability"] == "given" and not fact["content_text"].strip()) or (fact["content_availability"] != "given" and fact["content_text"] != ""):
            bad("content_availability", path, "given content requires text; unstated/not_applicable cannot supply invented words", [fid])
        compatible_policy = {"given": {"verbatim", "adaptable"}, "unstated": {"event_only"}, "not_applicable": {"not_applicable"}}
        if fact["content_policy"] not in compatible_policy.get(fact["content_availability"], set()):
            bad("content_policy", path, "content availability and protection/adaptation policy disagree", [fid])
        ids(fact["conditions"], path + ".conditions"); ids(fact["depends_on"], path + ".depends_on")
        supports = fact["supports"]
        if not isinstance(supports, list) or not supports:
            bad("source_span", path + ".supports", "every fact needs exact source spans", [fid])
            continue
        for si, span in enumerate(supports):
            sp = f"{path}.supports[{si}]"
            if not obj(span, {"source_id", "start", "end", "text"}, sp):
                continue
            actual = source_texts.get(span["source_id"]) if isinstance(span["source_id"], str) else None
            a, b = span["start"], span["end"]
            if span["source_id"] not in g["sources"] or not isinstance(actual, str) or type(a) is not int or type(b) is not int or not 0 <= a < b <= len(actual) or not isinstance(span["text"], str) or actual[a:b] != span["text"]:
                bad("source_span", sp, "span must match exact locked source characters", [fid])
    edges = {}
    for fid, f in facts.items():
        refs = []
        for key in ("conditions", "depends_on"):
            if isinstance(f[key], list): refs.extend(x for x in f[key] if isinstance(x, str))
        edges[fid] = refs
        missing = [x for x in refs if x not in facts]
        if missing: bad("dependency_missing", "$graph.facts." + fid, "missing source dependency: " + ", ".join(missing), [fid, *missing], True)
    visiting, done = set(), set()
    def visit(fid):
        if fid in visiting:
            bad("dependency_cycle", "$graph.facts." + fid, "cyclic source dependencies cannot establish support", [fid], True)
            return
        if fid in done or fid not in facts: return
        visiting.add(fid)
        for dep in edges[fid]: visit(dep)
        visiting.remove(fid); done.add(fid)
    for fid in facts: visit(fid)
    if not obj(decisions, {"contract", "uses", "written_objects"}, "$decisions"):
        return tuple(issues), None
    if decisions["contract"] != DECISIONS_CONTRACT:
        bad("contract", "$decisions.contract", "requires " + DECISIONS_CONTRACT)
    if not isinstance(decisions["uses"], list) or not isinstance(decisions["written_objects"], list):
        bad("shape", "$decisions", "uses and written_objects must be lists")
        return tuple(issues), None
    uses, represented = {}, set()
    enactments = {}
    for index, use in enumerate(decisions["uses"]):
        path = f"$decisions.uses[{index}]"
        if not obj(use, {"id", "fact_ids", "mode", "wording", "actor", "text"}, path): continue
        uid = use["id"]
        if not token(uid) or uid in uses:
            bad("use_identity", path, "use ID missing or duplicated"); continue
        refs = ids(use["fact_ids"], path + ".fact_ids")
        if not refs: bad("use_unbound", path, "a use cannot omit source facts")
        for fid in refs:
            if fid not in facts: bad("fact_missing", path, "unknown source fact " + fid, [fid], True)
        selected = [facts[f] for f in refs if f in facts]
        represented.update(f for f in refs if f in facts)
        # The first fact names the presented event; other event facts may be
        # retained causal/condition context, not a second actor being enacted.
        primary_event = selected[0]["event_id"] if selected else None
        projected = [f for f in selected if f["event_id"] == primary_event]
        for fid in refs:
            for dep in edges.get(fid, []):
                if dep not in refs: bad("use_dependency_missing", path, "use must retain dependency " + dep, [fid, dep], True)
        mode, actor, text = use["mode"], use["actor"], use["text"]
        wording = use["wording"]
        allowed_wording = {"spoken": {"verbatim", "adapted"}, "inner_voice": {"verbatim"}, "current_written": {"verbatim"}, "write": {"verbatim"}, "objective_narration": {"authored"}, "physical_action": {"authored"}}
        if not isinstance(wording, str) or wording not in allowed_wording.get(mode, set()):
            bad("wording_mode", path, "wording must explicitly match presentation mode")
        if not isinstance(mode, str) or mode not in MODES or not isinstance(text, str) or not text.strip():
            bad("use_shape", path, "requires a supported mode and complete decided text")
            continue
        if actor is not None and not token(actor): bad("actor", path, "actor must be a named string or null")
        if mode == "physical_action":
            actions = [f for f in projected if f["channel"] == "action"]
            if not actions or not token(actor) or any(f["subject"] != actor or f["polarity"] != "affirmative" or f["modality"] != "asserted" or f["occurrence"] not in {"occurred", "ongoing"} or f["conditions"] for f in actions):
                bad("nonactual_action", path, "negative/possible/future/conditional source cannot be enacted as an unconditional occurred action", refs)
            for f in actions:
                eid = f["event_id"]
                if eid in enactments and enactments[eid] != uid: bad("event_reenacted", path, "same source event must be merged before physical enactment", refs)
                enactments[eid] = uid
        spoken_realization = None
        if mode == "spoken":
            oral = [f for f in projected if f["channel"] == "oral"]
            if not oral or not token(actor) or any(f["subject"] != actor or f["content_availability"] != "given" or f["expression_occurrence"] not in {"occurred", "current"} or f["expression_status"] != "asserted" for f in oral):
                bad("spoken_unprovided", path, "spoken use needs actual oral content and its source speaker; unstated words cannot be invented", refs)
        if mode == "inner_voice":
            thoughts = [f for f in projected if f["channel"] == "thought"]
            if not thoughts or not token(actor) or any(f["subject"] != actor or f["content_availability"] != "given" or
                    f["expression_occurrence"] not in {"occurred", "current"} or f["expression_status"] != "asserted" for f in thoughts):
                bad("inner_voice_unprovided", path, "inner voice needs actual source thoughts and their thinker; dialogue cannot silently become thought", refs)
        if mode in {"current_written", "write"}:
            written = [f for f in projected if f["channel"] == "written"]
            allowed = {"current"} if mode == "current_written" else {"current", "occurred", "future"}
            if not written or any(f["content_availability"] != "given" or f["expression_occurrence"] not in allowed or f["expression_status"] != "asserted" for f in written):
                bad("written_scope", path, "paper needs supplied written content and the correct inscription occurrence; proposition futurity alone does not forbid current paper", refs)
        if mode == "spoken" and wording == "adapted" and any(f["channel"] == "oral" and f["content_policy"] != "adaptable" for f in projected):
            bad("protected_content_adapted", path, "protected or unstated source cannot opt into adapted speech", refs)
        if mode in {"current_written", "write", "inner_voice"} or (mode == "spoken" and wording == "verbatim"):
            channel = "oral" if mode == "spoken" else "thought" if mode == "inner_voice" else "written"
            expected_text = "".join(f["content_text"] for f in projected if f["channel"] == channel and f["content_availability"] == "given")
            if text != expected_text:
                bad("decided_content_changed", path, "rendered words must equal the ordered locked content; use adapted wording for unprotected oral content; paper/protected text cannot drift", refs)
        if mode == "objective_narration" and any(f["content_availability"] == "unstated" for f in selected) and re.search(r'[“”「」『』"]', text):
            bad("unstated_quoted_words", path, "event-only narration cannot add quoted unknown communication content", refs)
        if mode in {"spoken", "inner_voice"}:
            spoken_realization = _spoken_realization(projected, selected, facts, text, wording, source_texts, bad, path,
                                                       channel="thought" if mode == "inner_voice" else "oral")
        uses[uid] = SourceUse(uid, tuple(refs), tuple(dict.fromkeys(f["event_id"] for f in selected)), mode, wording, actor, text, _json_bytes(selected),
                             _json_bytes(spoken_realization) if spoken_realization is not None else None)
    missing = [fid for fid, f in facts.items() if f["required"] is True and fid not in represented]
    for fid in missing: bad("required_fact_unrepresented", "$global.required_facts", "required fact has no actual source use", [fid], True)
    papers = {}
    carrier_instants = set()
    for index, paper in enumerate(decisions["written_objects"]):
        path = f"$decisions.written_objects[{index}]"
        if not obj(paper, {"id", "use_id", "carrier_id", "at", "state"}, path): continue
        pid, uid = paper["id"], paper["use_id"]
        if not token(pid) or pid in papers or not token(paper["carrier_id"]) or not token(uid):
            bad("paper_identity", path, "paper/carrier/use identities must be explicit and unique"); continue
        use = uses.get(uid)
        if use is None:
            bad("paper_use_missing", path, "paper source use does not exist"); continue
        state, at = paper["state"], paper["at"]
        if not ((state == "current" and at == "frame0" and use.mode == "current_written") or (state == "planned" and at == "after:" + uid and use.mode == "write")):
            bad("paper_time", path, "current frame0 paper and planned post-write paper are distinct states", use.fact_ids)
        carrier_instant = (paper["carrier_id"], at)
        if carrier_instant in carrier_instants:
            bad("paper_state_duplicated", path, "one carrier at one instant requires one complete written object", use.fact_ids)
        carrier_instants.add(carrier_instant)
        papers[pid] = WrittenObject(pid, uid, paper["carrier_id"], at, state, use.text)
    for uid, use in uses.items():
        if use.mode in {"current_written", "write"} and not any(p.use_id == uid for p in papers.values()):
            bad("paper_missing", "$decisions.uses." + uid, "written use requires a carrier/time-bound object", use.fact_ids, True)
    production_plan = None
    if 'production_plan' in g:
        from production_plan import validate_source_plan, ProductionPlanError
        try:
            production_plan = validate_source_plan(g['production_plan'], facts, source_texts, g['sources'])
        except ProductionPlanError as exc:
            bad('production_plan_' + exc.status, exc.path, exc.reason, global_issue=True)
    result = SourceCompilation(digest, tuple(uses.values()), tuple(papers.values()), tuple(fid for fid, f in facts.items() if f["required"] is True), tuple(fid for fid in facts if fid in represented),
                               _json_bytes(action_plans) if action_plans is not None else None,
                               _json_bytes(production_plan) if production_plan is not None else None)
    return tuple(issues), result


def validate_source_decisions(graph, decisions, *, source_texts, expected_graph_sha256) -> tuple[SourceIssue, ...]:
    """Collect structural/global issues; this does not run semantic entailment."""
    try:
        return _inspect(graph, decisions, source_texts, expected_graph_sha256)[0]
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        return (SourceIssue("malformed_source_decisions", "$", str(exc), (), True),)


def compile_source_decisions(graph, decisions, *, source_texts, expected_graph_sha256) -> SourceCompilation:
    """Compile immutable decisions or reject with all known global obligations."""
    try:
        issues, result = _inspect(graph, decisions, source_texts, expected_graph_sha256)
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise SourceDecisionError((SourceIssue("malformed_source_decisions", "$", str(exc), (), True),)) from exc
    if issues:
        raise SourceDecisionError(issues)
    return result


def validate_source_consumption(compilation: SourceCompilation, *, use_ids, written_object_ids) -> tuple[SourceIssue, ...]:
    """Check IDs collected by the real lowerer, not an author's coverage claim.

    This cannot prove that callers actually inspected output; the production
    entry must collect these IDs while rendering its real actions/objects.
    Repeated references for the same event's static/video projections are legal.
    """
    issues = []
    for name, actual, expected in (
        ("uses", use_ids, {u.id for u in compilation.uses}),
        ("written_objects", written_object_ids, {p.id for p in compilation.written_objects}),
    ):
        if not isinstance(actual, (list, tuple, set, frozenset)) or not all(isinstance(x, str) for x in actual):
            issues.append(SourceIssue("consumption_shape", "$consumed." + name, "requires IDs collected during actual lowering", (), True))
            continue
        missing, extra = expected - set(actual), set(actual) - expected
        if missing or extra:
            facts = tuple(dict.fromkeys(fid for u in compilation.uses if u.id in missing for fid in u.fact_ids)) if name == "uses" else ()
            issues.append(SourceIssue("source_use_unconsumed", "$consumed." + name, "missing=" + repr(sorted(missing)) + "; unknown=" + repr(sorted(extra)), facts, True))
    return tuple(issues)
