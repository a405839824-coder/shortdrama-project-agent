"""One author-input inspection path; never fill missing semantic decisions.

The constructor is pure. Production must record its first complete result before
inspection; the recorder calls the private inspection below after freezing raw
bytes. Nothing here publishes a canonical or grants an independent audit.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references/storyboard_schema.json"


def construct_production_input(*, project, initial_world, shots) -> dict:
    """Copy authored decisions without validation, defaults or hidden retries.

    A complete return value is author input, not an append-only attempt record.
    Persist its first version through record_attempt before using inspection
    findings to revise it. A pure function cannot enforce that local discipline.
    """
    from production_decisions import CONTRACT

    return copy.deepcopy({"contract": CONTRACT, "project": project,
                          "initial_world": initial_world, "shots": shots})


def _author_project_schema(schema: dict) -> dict:
    """Project the existing schema onto caller-owned input fields only.

    Constants, shots, totals, state snapshots and audio events are compiler
    products. Their absence before lowering is not missing author evidence;
    a second authored copy is still forbidden. No enum or required evidence
    list is independently maintained here.
    """
    result = copy.deepcopy(schema)
    properties = result["properties"]
    generated_roots = {name for name, value in properties.items()
                       if isinstance(value, dict) and "const" in value}
    generated_roots.update({"shots", "total_duration"})
    result["required"] = [name for name in result.get("required", [])
                          if name not in generated_roots]
    for name in generated_roots:
        properties[name] = False
    evidence = properties["prevention_evidence"]
    evidence["required"] = [name for name in evidence.get("required", [])
                            if name != "scene_state_ledger"]
    evidence["properties"]["scene_state_ledger"] = False
    timing = result["$defs"]["shot_timing"]
    timing["required"] = [name for name in timing.get("required", [])
                          if name not in {"events", "utterances"}]
    timing["properties"]["events"] = False
    # The core accepts an omitted or empty utterance list, then derives it.
    # Keep its real item schema/type and only forbid prewritten utterances.
    timing["properties"]["utterances"]["maxItems"] = 0
    return result


class _Collection:
    def __init__(self):
        self.checks: list[dict] = []
        self.issues: list[dict] = []

    def add(self, stage: str, findings=(), *, reason=None, **details):
        rows = []
        for item in findings:
            record = {**item, "stage": stage, "check": stage}
            record.setdefault("code", "production_input_requirement")
            record.setdefault("path", "$")
            rows.append(record)
            if record not in self.issues:
                self.issues.append(record)
        # A dependency note cannot erase an observed failure. Only checks with
        # no result of their own are deferred.
        check = {"check": stage, "status": "fail" if rows else "not_checked" if reason else "pass",
                 **details}
        if rows:
            check["findings"] = rows
        if reason:
            check["reason"] = reason
        self.checks.append(check)

    def exception(self, stage: str, exc: Exception, *, path="$"):
        found = getattr(exc, "findings", None)
        if not found:
            found = [{"path": getattr(exc, "path", path),
                      "message": getattr(exc, "reason", f"{type(exc).__name__}: {exc}")}]
        self.add(stage, found)

    def absorb(self, report: dict):
        self.checks.extend(copy.deepcopy(report.get("checks", [])))
        for item in report.get("findings", []):
            normalized = {**item, "stage": item.get("stage", item.get("check", "authoring")),
                          "code": item.get("code", "production_input_requirement")}
            if normalized not in self.issues:
                self.issues.append(normalized)


def _inspect_with_intermediates(raw, *, graph_bytes, expected_graph_sha256, require_production_plan=False):
    """Private recorder hook: return lowered/source objects plus public report.

    Only the serializable third element is the public inspection result. The
    intermediate is not a canonical or a substitute for record_attempt.
    """
    import author_storyboard as author
    from authoring_preflight import inspect_production_editorial, inspect_typed_decisions
    from production_decisions import CONTRACT, inspect_production_decisions
    from source_decisions import compile_source_decisions

    c = _Collection()
    schema_bytes = SCHEMA_PATH.read_bytes()
    schema = json.loads(schema_bytes)
    project = raw.get("project") if isinstance(raw, dict) else None
    evidence = project.get("prevention_evidence") if isinstance(project, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}

    # This stage is independent of source interpretation and action simulation.
    project_errors = []
    for error in Draft202012Validator(_author_project_schema(schema)).iter_errors(project):
        path = ".".join(map(str, error.absolute_path))
        project_errors.append({"code": "project_schema", "path": "project" + ("." + path if path else ""),
                               "message": error.message})
    c.add("project_schema", project_errors, schema_sha256=hashlib.sha256(schema_bytes).hexdigest())
    if not isinstance(raw, dict) or raw.get("contract") != CONTRACT:
        c.add("production_contract", [{"code": "production_contract", "path": "contract",
                                       "message": "requires the existing production decision contract"}])
    else:
        c.add("production_contract")

    # Sum the authored shot clock before rendering any prose. Source-grounded
    # semantics and actual voice occupation are checked after compilation.
    from episode_rules import inspect_planned_budget
    c.add("episode_planned_budget", inspect_planned_budget(raw))

    try:
        c.absorb(inspect_production_editorial(raw, renderer=author))
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        c.exception("editorial_format", exc)

    source_errors, source_texts = [], {}
    source_rows = evidence.get("source_ledger")
    if not isinstance(source_rows, list) or not source_rows:
        source_errors.append({"code": "source_rows", "path": "project.prevention_evidence.source_ledger",
                              "message": "requires frozen source text rows"})
    else:
        for index, row in enumerate(source_rows):
            if (not isinstance(row, dict) or not isinstance(row.get("source_id"), str)
                    or not isinstance(row.get("source_text"), str) or row["source_id"] in source_texts):
                source_errors.append({"code": "source_rows", "path": f"project.prevention_evidence.source_ledger[{index}]",
                                      "message": "requires unique source IDs and actual source_text"})
            else:
                source_texts[row["source_id"]] = row["source_text"]
    if not isinstance(graph_bytes, bytes):
        source_errors.append({"code": "source_graph_unavailable", "path": "source_graph_path",
                              "message": "requires separately frozen source graph bytes"})
    compilation = None
    if not source_errors:
        try:
            compilation = compile_source_decisions(
                graph_bytes, evidence.get("source_decisions"), source_texts=source_texts,
                expected_graph_sha256=expected_graph_sha256)
        except (TypeError, ValueError, KeyError) as exc:
            source_errors.extend(issue.to_dict() for issue in getattr(exc, "issues", []))
            if not source_errors:
                source_errors.append({"code": "source_compilation", "path": "source_graph",
                                      "message": f"{type(exc).__name__}: {exc}"})
    c.add("source_compilation", source_errors, semantic_status="pending_independent_review")

    # Uncompiled plan bytes may expose missing declarations, but never prove a
    # source binding. This shape-only diagnostic remains independent of the
    # authoritative source compiler above.
    from performance_evidence import inspect_performance_input
    raw_plan = None
    if isinstance(graph_bytes, bytes):
        try:
            graph_document = json.loads(graph_bytes)
            raw_plan = graph_document.get('production_plan') if isinstance(graph_document, dict) else None
        except (ValueError, UnicodeError):
            pass  # The source compiler owns the actual parsing failure.
    try:
        performance = inspect_performance_input(raw, sources=compilation, raw_plan=raw_plan)
        c.add('performance_input_declarations', performance['findings'],
              reason='source plan or verified legacy applicability is unavailable' if performance['basis'] == 'unknown' else None,
              basis=performance['basis'], semantic_status=performance['semantic_status'])
        for index, reason in enumerate(performance['deferred_checks']):
            c.add(f'performance_input_declarations.deferred[{index}]', reason=reason)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        c.exception('performance_input_declarations', exc)

    if require_production_plan:
        from production_plan import CONTRACT as PLAN_CONTRACT
        if compilation is None:
            c.add('frozen_production_plan',
                  reason='source compilation failed; a validated frozen production plan is unavailable',
                  depends_on=['source_compilation'], blocked_by=['source_compilation'],
                  upstream_findings=[{'code': item['code'], 'path': item['path']}
                                     for item in c.issues if item['stage'] == 'source_compilation'])
        else:
            plan = compilation.to_dict().get('production_plan')
            c.add('frozen_production_plan', [] if plan is not None else [{
                'code': 'E-PRODUCTION-PLAN-001', 'path': '$graph.production_plan',
                'status': 'insufficient_evidence',
                'message': 'new production requires a separately frozen ' + PLAN_CONTRACT +
                           '; identity checks cannot substitute for source/knowledge/whole-shot planning'}],
                  depends_on=['source_compilation'])

    lowered = None
    geometry_observations = []
    try:
        inspected = inspect_production_decisions(
            raw, source_compilation=compilation,
            source_unavailable_reason="source compilation failed; source references and dependent state are unavailable"
            if source_errors else None)
        for name in inspected.get("checked", []):
            c.add(name)
        for item in inspected.get("findings", []):
            c.add(item.get("stage", "production_lowering"), [item])
        for item in inspected.get("not_checked", []):
            c.add(item["stage"], reason=item["reason"])
        geometry_observations = copy.deepcopy(inspected.get('geometry_observations', []))
        lowered = inspected.get("lowered")
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        c.exception("production_lowering", exc)
        c.add("dependent_state_suffix_and_rendering", reason="production inspection could not establish its prerequisites")

    if lowered is None:
        c.add("typed_input_contracts", reason="actual lowering is unavailable")
        c.add("required_performance", reason="actual state and visible windows are unavailable")
    else:
        typed = lowered["typed_decisions"]
        try:
            c.absorb(inspect_typed_decisions(typed, renderer=author))
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            c.exception("typed_input_contracts", exc)
        from performance_checks import eye_contract_faults, listener_reaction_faults
        world = raw.get('initial_world', {})
        cast = list(world.get('actors', {}))
        props = list(world.get('props', {})) + list(world.get('points', {}))

        # These are real privately lowered shot fields, not authored PASS tags
        # or a fabricated schema-positive candidate. No whole canonical is made.
        traces = {shot["shot_id"]: shot for shot in lowered["derivation"]["shots"]}
        planned_performance = lowered['derivation'].get('production_prevention', {}).get('status') == 'pass'
        for index, row in enumerate(typed["shots"]):
            stage = f"shots[{index}].required_performance"
            try:
                fields, _ = author._bind_staging_fields(row["fields"], row["staging"])
                shot = copy.deepcopy(fields)
                shot.update({"镜头编号": row["shot_id"], "时长": row["duration"],
                             "画面内容": "\n".join(
                                 f"【{phase['start']:.1f}—{phase['end']:.1f}秒】{phase['text']}"
                                 for phase in row["phases"])})
                dialogue_rows = author.extract_dialogues(shot["画面内容"])
                # A verified frozen plan uses the real event/state/visibility
                # compiler. The legacy prose grammar requires an invented eye
                # chain and a reaction from every listener; it is not an extra
                # source of obligations for deterministic planned output.
                faults = [] if planned_performance else eye_contract_faults(
                    shot, duration=row['duration'], strict=True, dialogue_rows=dialogue_rows,
                    visible_names=cast, non_person_names=props)
                reactions = [] if planned_performance else listener_reaction_faults(
                    shot, duration=row['duration'], dialogue_rows=dialogue_rows,
                    visible_names=cast, non_person_names=props)
                findings = [{"code": "E-VIS-001" if message.startswith('input_mapping') else "E-EXPRESSION-003", "path": f"shots[{index}].actions",
                             "message": message} for message in faults]
                findings.extend({"code": "E-VIS-001" if message.startswith('input_mapping') else "E-PERFORMANCE-001", "path": f"shots[{index}].actions",
                                 "message": message} for message in reactions)
                windows = copy.deepcopy(traces[row["shot_id"]].get("eye_visibility_ticks", {}))
                c.add(stage, findings, shot_id=row["shot_id"], visibility=windows,
                      eye_applicability={actor: "visible_window" if spans else "not_applicable"
                                         for actor, spans in windows.items()},
                      applicability_basis=('verified frozen plan and actual event/state/visibility compiler; naturalness is independently reviewed'
                                           if planned_performance else 'actual lowered fields and current shared visible-window consumers'))
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                c.exception(stage, exc, path=f"shots[{index}]")
                c.add(stage + ".visibility", reason="actual shot projection was not available")

    c.add("independent_source_semantics_and_formal_release", reason="requires independent review of actual output and existing service authority")
    ready = lowered is not None and not c.issues
    report = {"contract": "production-input-inspection-v1", "checks": c.checks,
              "issues": c.issues, "findings": copy.deepcopy(c.issues),
              "not_checked": [row for row in c.checks if row["status"] == "not_checked"],
              "ready_for_canonical": ready, "canonical_created": False,
              "geometry_observations": geometry_observations,
              "semantic_status": "pending_independent_review", "semantic_accuracy_percent": None,
              "commit_eligible": False, "schema_sha256": hashlib.sha256(schema_bytes).hexdigest()}
    return lowered, compilation, report


def inspect_production_input(raw, *, graph_bytes, expected_graph_sha256, require_production_plan=False) -> dict[str, Any]:
    """Read-only complete-input diagnostics; never a production attempt store.

    Missing graph/source dependencies do not turn referenced paper into an
    invalid object. Independent project, camera and action-shape checks still
    run; dependent simulation/visible-performance checks are not_checked.
    """
    return _inspect_with_intermediates(raw, graph_bytes=graph_bytes,
                                       expected_graph_sha256=expected_graph_sha256,
                                       require_production_plan=require_production_plan)[2]
