"""Versioned quality measurements and conservative repair provenance.

The caller supplies server-validated diagnostics. Candidate-authored categories
or claimed fixes never authorize a downgrade, a pass, or a historical rewrite.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any, Callable

QUALITY_CONTRACT = "storyboard-quality-v2"
# Existing warning-level E-* diagnostics still describe contract/content faults.
# Never erase lighting discontinuity or missing performance merely for being WARN.
ADVISORY_CODES = frozenset({"N-TIMING-UNCALIBRATED"})
WORKFLOW_PREFIXES = ("E-GATE-", "E-BOOTSTRAP-", "E-VERSION-", "E-ARCHIVE-",
                     "E-AUDIT-", "E-HANDOFF-", "E-MIGRATION-", "E-INTEGRATION-",
                     "E-DRAFT-SCENE-PREFLIGHT-")
CONTRACT_PREFIXES = ("E-FIELDORDER-", "E-DEPRECATED-FIELD-", "E-PUNCT-",
                      "E-TEMPLATE-", "E-I2V-INDEP-", "E-AUDIO-STAGE-", "E-DRAFT-CONTRACT-")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def issue_domain(issue: dict[str, Any]) -> str:
    code = str(issue.get("error_code") or "")
    severity = str(issue.get("severity") or "fail").lower()
    if (code in ADVISORY_CODES and severity in {"info", "warn", "warning"}
            and not issue.get("blocking", False)):
        return "advisory"
    if code.startswith(WORKFLOW_PREFIXES):
        return "workflow"
    if code.startswith(CONTRACT_PREFIXES):
        return "contract"
    return "content"


def measure_quality(storyboard, issues, *, resolve_ids: Callable,
                    pattern_key: Callable, known_pattern_keys=None):
    shots = [s for s in storyboard.get("shots", []) if isinstance(s, dict)]
    identities = [str(s.get("镜头编号") or s.get("shot_uid") or "") for s in shots]
    identity_valid = bool(shots) and all(identities) and len(set(identities)) == len(shots)
    domains = {name: set() for name in ("content", "contract", "workflow", "advisory")}
    counts = Counter()
    unlocated = Counter()
    patterns = Counter()
    known_hits = set()
    hard_count = 0
    actionable = []
    for issue in issues:
        domain = issue_domain(issue)
        counts[domain] += 1
        ids = set(resolve_ids(storyboard, issue))
        valid_ids = ids & set(identities)
        domains[domain].update(valid_ids)
        if domain == "advisory":
            continue
        actionable.append(issue)
        if not valid_ids or ids - set(identities):
            unlocated[domain] += 1
        severity = str(issue.get("severity") or "fail").lower()
        hard_count += int(bool(issue.get("blocking", severity in {"fail", "error", "blocking"}))
                          or severity in {"fail", "error", "blocking"})
        # Process failures remain blockers, but cannot manufacture a recurring
        # *content* defect or lower the content quality of unrelated shots.
        if domain != "workflow":
            key = pattern_key(issue)
            patterns[key] += 1
            if key in (known_pattern_keys or set()):
                known_hits.add(key)
    unusable = domains["content"] | domains["contract"]
    content_unknown = not identity_valid or bool(unlocated["content"])
    usability_unknown = content_unknown or bool(unlocated["contract"])
    def rate(affected, unknown=False):
        return None if unknown else round(100 * (len(shots) - len(affected)) / len(shots), 2)
    correct = rate(domains["content"], content_unknown)
    usable = rate(unusable, usability_unknown)
    episode_sets = {}
    for shot, identity in zip(shots, identities):
        episode_sets.setdefault(str(shot.get("episode_id") or "全项目"), set()).add(identity)
    episode_rates = {ep: None if content_unknown else round(100 * (len(ids) - len(ids & domains["content"])) / len(ids), 2)
                     for ep, ids in episode_sets.items()}
    minimum = min((r for r in episode_rates.values() if r is not None), default=None)
    systemic = sorted(key for key, count in patterns.items() if count >= 2)
    new_rate = None if usability_unknown else round(100 * len(unusable) / len(shots), 2)
    zero_clean = not hard_count and not known_hits and not systemic and not counts["workflow"]
    measured = correct is not None and usable is not None and minimum is not None
    grade = "unavailable" if not measured else "C"
    if measured and zero_clean and correct >= 95 and usable >= 95 and minimum >= 96:
        grade = "A" if correct >= 98 and usable >= 98 and new_rate <= 2 else "B"
    return {
        "quality_contract": QUALITY_CONTRACT, "measurement_state": "measured" if measured else "unavailable",
        "grade": grade, "checked_shot_count": len(shots), "issue_point_count": len(issues),
        "unique_affected_shot_count": len(unusable), "affected_shot_ids": sorted(unusable),
        "content_affected_shot_ids": sorted(domains["content"]),
        "contract_affected_shot_ids": sorted(domains["contract"]),
        "first_draft_correctness_percent": correct, "direct_usability_percent": usable,
        "new_local_issue_shot_rate_percent": new_rate, "episode_correctness_percent": episode_rates,
        "minimum_episode_correctness_percent": minimum, "hard_or_major_issue_count": hard_count,
        "global_blocking_issue_count": sum(unlocated.values()),
        "workflow_issue_count": counts["workflow"], "advisory_count": counts["advisory"],
        "unlocated_content_issue_count": unlocated["content"],
        "known_recurrence_count": len(known_hits), "known_recurrence_pattern_keys": sorted(known_hits),
        "systemic_duplicate_count": len(systemic), "systemic_pattern_keys": systemic,
        "commit_eligible": bool(grade == "A" and not actionable and identity_valid),
        "final_independent_audit_required_percent": 100.0,
    }


def assessment_context(storyboard, *, rules="unknown", validator="unknown", runtime="unknown"):
    content = copy.deepcopy(storyboard)
    evidence = content.get("prevention_evidence")
    if isinstance(evidence, dict):
        for key in ("repair_history", "audit_layers"):
            evidence.pop(key, None)
    return {"contract": QUALITY_CONTRACT, "content_sha256": digest(content),
            "rules_fingerprint": rules, "validator_fingerprint": validator, "runtime_fingerprint": runtime,
            "shot_identities": {str(s.get("镜头编号") or ""): str(s.get("shot_uid") or s.get("镜头编号") or "")
                                for s in storyboard.get("shots", []) if isinstance(s, dict)}}


def bind_issue_identity(issue, context):
    bound = copy.deepcopy(issue)
    mapping = context.get("shot_identities", {})
    bound["stable_shot_ids"] = sorted(mapping.get(str(s), str(s)) for s in issue.get("shot_ids", []))
    return bound


def issue_identity(issue):
    # Use the semantic field, not the report's Q001/Q099 location suffix.
    field = str(issue.get("field") or issue.get("location") or "unknown")
    shots = tuple(sorted(str(s) for s in issue.get("stable_shot_ids", issue.get("shot_ids", []))))
    return str(issue.get("error_code") or ""), field, shots


def finding_origin(issue, prior_events, context):
    if not prior_events:
        return "initial"
    previous = prior_events[-1]
    if any(issue_identity(issue) == issue_identity(old) for old in previous.get("issues", [])):
        return "residual"
    before = previous.get("assessment_context") or {}
    if before.get("contract") != QUALITY_CONTRACT:
        return "attribution_pending"
    if any(before.get(key) != context.get(key) for key in
           ("rules_fingerprint", "validator_fingerprint", "runtime_fingerprint")):
        return "rule_changed"
    if before.get("content_sha256") == context.get("content_sha256"):
        return "missed_in_prior_audit"
    # A different artifact is not proof the repair *caused* this defect.
    return "attribution_pending"


def convergence_evidence(issues, prior_events):
    result = []
    for issue in issues:
        key = issue_identity(issue)
        repeats = 1
        for event in reversed(prior_events):
            if any(issue_identity(old) == key for old in event.get("issues", [])):
                repeats += 1
            else:
                break
        if repeats >= 2:
            result.append({"issue_key": digest(key), "error_code": key[0], "field": key[1],
                           "shot_ids": list(issue.get("shot_ids", [])), "stable_shot_ids": list(key[2]),
                           "consecutive_observations": repeats,
                           "required_action": "rebuild_upstream" if repeats >= 3 else "root_cause_repair",
                           "required_evidence": ["before_after_diff", "affected_scope", "regression_results"],
                           "resolved": False})
    return result


def annotate_attempt_event(event, prior_events, context):
    """Keep failed repair attempts in one source lineage across work orders.

    Exact retries do not create another observation or increase the round.
    Caller must select the signed events for the same source lineage.
    """
    existing = next((row for row in prior_events if row.get("event_id") == event.get("event_id")), None)
    if existing is not None:
        return copy.deepcopy(existing)
    result = copy.deepcopy(event)
    result["assessment_contract"] = QUALITY_CONTRACT
    result["assessment_context"] = copy.deepcopy(context)
    result["audit_round"] = len(prior_events) + 1
    result["issues"] = [bind_issue_identity(issue, context) for issue in event.get("issues", [])]
    for issue in result["issues"]:
        issue["status"] = finding_origin(issue, prior_events, context)
    result["repair_convergence"] = convergence_evidence(result["issues"], prior_events)
    return result


def machine_issue_map(storyboard, issues, resolve_ids):
    context = assessment_context(storyboard)
    result = {}
    for issue in issues:
        location = str(issue.get("location") or "全表")
        if location.startswith(("repair_history[", "prevention_evidence.repair_history")):
            continue
        bound = bind_issue_identity({**issue,
            "field": str(issue.get("field") or location.rsplit(".", 1)[-1]),
            "shot_ids": resolve_ids(storyboard, issue)}, context)
        result[digest(issue_identity(bound))] = bound
    return result


def repair_diff(before, after):
    """Server-computed differences, including dependent evidence and projections."""
    def flatten(value, prefix=""):
        if isinstance(value, dict):
            if not value:
                return {prefix: {}}
            return {p: v for key, child in value.items()
                    if not (prefix == "prevention_evidence" and key in {"repair_history", "audit_layers"})
                    for p, v in flatten(child, f"{prefix}.{key}".strip(".")).items()}
        if isinstance(value, list):
            if not value:
                return {prefix: []}
            return {p: v for i, child in enumerate(value)
                    for p, v in flatten(child, f"{prefix}[{i}]").items()}
        return {prefix: value}
    left, right = flatten(before), flatten(after)
    paths = sorted(p for p in left.keys() | right.keys() if (p in left, left.get(p)) != (p in right, right.get(p)))
    def shot_map(storyboard):
        return {str(s.get("shot_uid") or s.get("镜头编号") or ""): s
                for s in storyboard.get("shots", []) if isinstance(s, dict)}
    old_shots, new_shots = shot_map(before), shot_map(after)
    changed = sorted(uid for uid in old_shots.keys() | new_shots.keys()
                     if old_shots.get(uid) != new_shots.get(uid))
    scenes = sorted({str(row.get("scene_uid") or row.get("场景连续性ID") or "")
                     for uid in changed for row in (old_shots.get(uid, {}), new_shots.get(uid, {}))
                     if row.get("scene_uid") or row.get("场景连续性ID")})
    return {"before_sha256": assessment_context(before)["content_sha256"],
            "after_sha256": assessment_context(after)["content_sha256"],
            "changed_paths": paths, "changed_path_count": len(paths),
            "content_changed": bool(paths), "proof_source": "server_computed",
            "changed_shot_uids": changed, "affected_scene_ids": scenes,
            "full_candidate_regression_required": True,
            "semantic_closure": "requires_independent_audit"}
