"""Read-only, evidence-aware views over immutable audit observations.

These classifications do not change audit results, admission gates or ledger
history. A diagnostic is not a measured rendering failure. Legacy missing
provenance is left missing, rather than reconstructed from filenames/statuses.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Any, Iterable


METRICS_CONTRACT = "storyboard-issue-metrics-v1"
CATEGORY_LABELS = {
    "confirmed_content": "已复核的内容问题",
    "content_candidate": "内容风险待复核",
    "format": "文字与格式规范",
    "missing_evidence": "资料或判断依据待补",
    "suspected_false_positive": "疑似误报待复核",
}
_WORKFLOW_CODES = {
    "E-VERSION-001", "E-SOURCE-001", "E-GATE-001",
    "E-AUDIT-COVERAGE-UNKNOWN",
}
_WORKFLOW_FIELDS = {
    "project_version", "source_lock", "workflow_profile/prevention_evidence",
    "audit_coverage", "workflow_profile", "prevention_evidence",
}
_WORKFLOW_PREFIXES = (
    "E-BOOTSTRAP-", "E-ARCHIVE-", "E-HANDOFF-", "E-MIGRATION-",
    "E-INTEGRATION-", "E-DRAFT-SCENE-PREFLIGHT-",
)
_FORMAT_PREFIXES = ("E-FIELDORDER-", "E-DEPRECATED-FIELD-", "E-PUNCT-", "E-TEMPLATE-")
_CONTENT_PREFIXES = (
    "E-VIS-", "E-DIALOGUE-", "E-PSYCH-", "E-TIMELINE-", "E-LIGHT-",
    "E-EXPRESSION-", "E-SPATIAL-", "E-FIELD-CONTENT-", "E-AUDIO-",
    "E-ADAPTED-DIALOGUE-", "E-CONTINUITY-", "E-SOURCE-",
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _strings(value: Any) -> list[str]:
    return sorted({_text(item) for item in value if _text(item)}) if isinstance(value, list) else []


def _shots(issue: dict[str, Any]) -> list[str]:
    return _strings(issue.get("stable_shot_ids")) or _strings(issue.get("shot_ids"))


def _file_identity(event: dict[str, Any]) -> tuple[dict[str, str], str]:
    for key in ("file_fingerprint", "artifact_semantic_fingerprint", "canonical_fingerprint"):
        if _text(event.get(key)):
            return {"fingerprint_kind": key, "fingerprint": _text(event[key]),
                    "source_version": _text(event.get("source_version"))}, "bound"
    # No content/file identity means no safe cross-event deduplication. A name
    # is not a version and a timestamp is not evidence of content equivalence.
    return {"event_fallback": _text(event.get("event_id")) or _digest(event),
            "source_version": _text(event.get("source_version"))}, "unbound"


def _is_workflow_gap(issue: dict[str, Any]) -> bool:
    code, field = _text(issue.get("error_code")), _text(issue.get("field"))
    return ((code in _WORKFLOW_CODES and field in _WORKFLOW_FIELDS)
            or code.startswith(_WORKFLOW_PREFIXES))


def _is_bound_semantic_finding(issue: dict[str, Any], event: dict[str, Any]) -> bool:
    # Inputs are server-validated reports/events. A generic fail/severity label
    # or complete shot coverage does not independently confirm each finding.
    return bool(event.get("audit_class") == "independent_audit"
                and _text(event.get("semantic_audit_sha256"))
                and issue.get("issue_source") == "independent_semantic_audit"
                and _text(issue.get("semantic_issue_id"))
                and _text(issue.get("semantic_layer"))
                and _strings(issue.get("evidence_ids")) and _shots(issue)
                and _text(issue.get("evidence")))


def classify_issue(issue: dict[str, Any], *, event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify supplied validated diagnostics without granting PASS authority.

    Narrow code+evidence matches identify presentation or missing-proof records.
    Unknown codes and incomplete evidence remain unknown; historical snippets
    are never treated as a universal semantic blacklist.
    """
    event = event or {}
    code = _text(issue.get("error_code"))
    field = _text(issue.get("field"))
    evidence = _text(issue.get("evidence"))
    if _is_workflow_gap(issue):
        category, reason = "missing_evidence", "file_or_version_evidence_gap"
    elif (code == "E-VIS-001" and "input_mapping" in evidence
          and ("未登记" in evidence or "未明确" in evidence)):
        category, reason = "missing_evidence", "subject_identity_mapping_unverified"
    elif (code == "E-ADAPTED-DIALOGUE-001" and field == "source_communication"
          and "缺" in evidence and "映射" in evidence):
        category, reason = "missing_evidence", "source_communication_mapping_unverified"
    elif (code.startswith(_FORMAT_PREFIXES)
          or (code == "E-AUDIO-STAGE-001" and field == "生成限制"
              and "统一逐字" in evidence and "禁止额外配乐" in evidence)):
        category, reason = "format", "explicit_format_or_literal_requirement"
    elif (code == "E-FIELD-CONTENT-001" and field == "saved_keyframe_static"
          and re.fullmatch(r"检查实际F的静态指令、当前时刻和独立定位:\s*\['eye_process=不定格于半眨眼状态'\]", evidence)):
        # Precisely one negated fragment was reported by the legacy checker.
        # Mixed findings (including affirmative motion) are not downgraded.
        category, reason = "suspected_false_positive", "negated_constraint_requires_review"
    elif _is_bound_semantic_finding(issue, event):
        category, reason = "confirmed_content", "bound_independent_semantic_finding"
    elif not evidence or any(token in evidence for token in (
            "依据不足", "尚未核验", "须核验", "无法判断", "requires independent review")):
        category, reason = "missing_evidence", "insufficient_finding_evidence"
    elif code.startswith(_CONTENT_PREFIXES):
        category, reason = "content_candidate", "diagnostic_risk_not_independently_confirmed"
    else:
        category, reason = "missing_evidence", "unclassified_diagnostic_requires_review"
    return {
        "category": category, "label": CATEGORY_LABELS[category], "reason": reason,
        "content_confirmed": category == "confirmed_content",
        "false_positive_confirmed": False,
        "generation_effect": "not_measured",
        "changes_audit_verdict": False,
    }


def issue_metric_key(project_id: str, event: dict[str, Any], issue: dict[str, Any]) -> str:
    """One exact problem in one file/version; different roots stay separate."""
    file_identity, _ = _file_identity(event)
    shots = _shots(issue)
    location = _text(issue.get("location") or issue.get("field"))
    if shots:
        location = re.sub(r"shots\[\d+\]", "shots[*]", location)
        if location.startswith("semantic_audit."):
            location = "semantic_audit"  # Q001/Q099 are report row numbers.
    root = {key: _text(issue.get(key)) for key in (
        "root_cause", "normalized_root_cause", "problem_key", "requirement_id",
        "subject_id", "source_occurrence_id") if _text(issue.get(key))}
    source_refs = [ref for ref in _strings(issue.get("evidence_ids")) if ref.startswith("source-unit:")]
    workflow_gap = _is_workflow_gap(issue)
    # File-level evidence omissions count once per file/version, not once for
    # every affected shot or every coverage-list change. Distinct requirements
    # and explicit root causes continue to split them.
    if not workflow_gap and not root.get("requirement_id"):
        root["evidence"] = _text(issue.get("evidence"))
        root["source_sentence"] = _text(issue.get("source_sentence"))
    identity = {
        "project_id": project_id, "file": file_identity,
        "episode_id": "" if workflow_gap else _text(event.get("episode_id")),
        "error_code": _text(issue.get("error_code")),
        "shots": [] if workflow_gap else shots,
        "field": _text(issue.get("field")),
        "location": _text(issue.get("field")) if workflow_gap else location,
        "root": root, "source_refs": source_refs,
    }
    return "METRIC-" + _digest(identity)[:32]


def _episode_matches(event: dict[str, Any], issue: dict[str, Any], selected: set[str]) -> bool:
    if not selected or _text(event.get("episode_id")) in selected:
        return True
    found = {match.group(1) for shot in _shots(issue)
             if (match := re.match(r"^(EP\d+)-", shot))}
    return bool(found & selected)


def _metric(numerator: int | None, denominator: int | None, *, basis: str,
            unavailable_reason: str | None = None) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "percent": round(100 * numerator / denominator, 2)
            if numerator is not None and denominator else None,
            "denominator_definition": basis,
            "unavailable_reason": unavailable_reason}


def _confirmed_shot_rate(events: list[dict[str, Any]], *, filtered: bool) -> dict[str, Any]:
    basis = "fully independently reviewed shots in the latest audit of each exact file/version"
    reason = "requires_bound_shot_review_coverage_and_per_finding_adjudication"
    if filtered:
        return _metric(None, None, basis=basis, unavailable_reason="filtered_findings_do_not_establish_full_shot_denominator")
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        file_identity, bound = _file_identity(event)
        if event.get("audit_class") != "independent_audit":
            continue
        latest[_digest(file_identity)] = event if bound == "bound" else {"identity_unbound": True}
    numerator = denominator = 0
    for event in latest.values():
        # The service deliberately carries first_delivery_quality forward from
        # round 1. It is not the later round's current review coverage, even if
        # the counts happen to match. Never reuse it to certify that later row.
        if event.get("audit_round") != 1:
            return _metric(None, None, basis=basis,
                           unavailable_reason="latest_round_has_no_current_bound_review_quality")
        quality = event.get("first_delivery_quality") or event.get("first_draft_quality") or {}
        if not isinstance(quality, dict):
            return _metric(None, None, basis=basis, unavailable_reason=reason)
        total = quality.get("total_shot_count")
        if not (event.get("semantic_audit_sha256")
                and quality.get("independent_content_coverage_verified") is True
                and isinstance(total, int) and not isinstance(total, bool) and total > 0
                and quality.get("completed_shot_count") == total):
            return _metric(None, None, basis=basis, unavailable_reason=reason)
        confirmed = set()
        for issue in event.get("issues", []):
            if not isinstance(issue, dict):
                return _metric(None, None, basis=basis, unavailable_reason=reason)
            category = classify_issue(issue, event=event)["category"]
            if category not in {"confirmed_content", "format"}:
                return _metric(None, None, basis=basis, unavailable_reason=reason)
            if category == "confirmed_content":
                confirmed.update(_shots(issue))
        # The coverage record and per-finding evidence must agree. A legacy
        # aggregate problem count cannot replace missing per-finding provenance.
        coverage_problem_ids = set(_strings(quality.get("confirmed_problem_shot_ids")))
        if (confirmed != coverage_problem_ids or len(confirmed) > total
                or quality.get("confirmed_problem_shot_count") != len(confirmed)):
            return _metric(None, None, basis=basis, unavailable_reason=reason)
        numerator += len(confirmed)
        denominator += total
    result = _metric(numerator if denominator else None, denominator or None, basis=basis,
                     unavailable_reason=None if denominator else reason)
    result["evaluated_file_version_count"] = len(latest)
    result["counts_render_failures"] = False
    return result


def summarize_issue_metrics(
    ledger: dict[str, Any], *, episode_ids: Iterable[str] | None = None,
    error_codes: Iterable[str] | None = None, include_history: bool = False,
    limit: int = 100, cursor: int = 0,
) -> dict[str, Any]:
    """Project historical findings without mutating the original ledger.

    The scope is all historical observations matching episode/code filters,
    deliberately independent of legacy issue-row status/recurrence filters.
    No closed/resolved state is inferred from absence in a later audit.
    """
    selected_episodes = {_text(value) for value in episode_ids or [] if _text(value)}
    selected_codes = {_text(value) for value in error_codes or [] if _text(value)}
    project_id = _text(ledger.get("project_id"))
    grouped: dict[str, dict[str, Any]] = {}
    categories: Counter[str] = Counter()
    event_ids: set[str] = set()
    observation_count = 0
    incomplete_identity_observations = 0
    unsupported_introduced_observations = 0
    unscoped_observations_excluded = 0
    events = [row for row in ledger.get("events", []) if isinstance(row, dict)]
    events = sorted(events, key=lambda row: (_text(row.get("timestamp")), _text(row.get("event_id"))))
    for event_index, event in enumerate(events):
        event_id = _text(event.get("event_id")) or f"unidentified-event:{event_index}"
        _, identity_state = _file_identity({**event, "event_id": event_id})
        for issue_index, issue in enumerate(event.get("issues", [])):
            if not isinstance(issue, dict):
                continue
            if selected_codes and _text(issue.get("error_code")) not in selected_codes:
                continue
            if not _episode_matches(event, issue, selected_episodes):
                if selected_episodes and not _shots(issue):
                    unscoped_observations_excluded += 1
                continue
            classification = classify_issue(issue, event=event)
            category = classification["category"]
            key = issue_metric_key(project_id, {**event, "event_id": event_id}, issue)
            observation_count += 1
            categories[category] += 1
            event_ids.add(event_id)
            incomplete_identity_observations += int(identity_state != "bound")
            unsupported_introduced_observations += int(issue.get("status") == "introduced")
            if key not in grouped:
                grouped[key] = {
                    "problem_id": key, "error_code": _text(issue.get("error_code")),
                    "field": _text(issue.get("field")), "location": _text(issue.get("location")),
                    "shot_ids": _shots(issue), "episode_id": _text(event.get("episode_id")),
                    "file_identity": _file_identity(event)[0], "identity_state": identity_state,
                    "counting_unit": "file_version" if _is_workflow_gap(issue) else "file_version_location_finding",
                    "first_seen_at": event.get("timestamp"), "last_seen_at": event.get("timestamp"),
                    "observation_count": 0, "event_ids": set(), "category_observations": Counter(),
                    "latest_classification": classification, "example_evidence": _text(issue.get("evidence")),
                    "review_state": "not_independently_reviewed", "repair_attribution": "not_established",
                }
                if include_history:
                    grouped[key]["observation_references"] = []
            row = grouped[key]
            row["observation_count"] += 1
            row["event_ids"].add(event_id)
            row["category_observations"][category] += 1
            row["last_seen_at"] = event.get("timestamp")
            row["latest_classification"] = classification
            row["latest_observation"] = {"event_id": event_id, "issue_index": issue_index,
                                         "recorded_status": issue.get("status")}
            row["review_state"] = "bound_content_finding" if classification["content_confirmed"] else "requires_review"
            if include_history:
                row["observation_references"].append({
                    "event_id": event_id, "issue_index": issue_index, "timestamp": event.get("timestamp"),
                    "recorded_status": issue.get("status"), "category": category,
                })
    rows = []
    for row in grouped.values():
        row["distinct_audit_count"] = len(row.pop("event_ids"))
        row["category_observations"] = dict(row["category_observations"])
        row["display_summary"] = f"1个问题，记录{row['observation_count']}次（{row['distinct_audit_count']}次审计）"
        row["discovery_state"] = "reobserved" if row["distinct_audit_count"] > 1 else "newly_discovered"
        rows.append(row)
    rows.sort(key=lambda row: (_text(row["last_seen_at"]), row["problem_id"]), reverse=True)
    unique_categories = Counter(row["latest_classification"]["category"] for row in rows)
    # A repeat count, a warning label, or a historical 'introduced' flag is not
    # proof of an error rate, a false positive, or damage caused by a repair.
    return {
        "contract": METRICS_CONTRACT, "read_only": True,
        "scope": {"basis": "historical_audit_observations", "episode_ids": sorted(selected_episodes),
                  "error_codes": sorted(selected_codes), "legacy_status_filter_applied": False,
                  "legacy_recurrence_filter_applied": False},
        "history_preserved": True, "changes_audit_verdict": False,
        "event_count_with_findings": len(event_ids),
        "historical_observation_count": observation_count,
        "unique_file_version_problem_count": len(rows),
        "repeat_observation_count": observation_count - len(rows),
        "file_version_evidence_gap_count": sum(row["counting_unit"] == "file_version" for row in rows),
        "unbound_identity_observation_count": incomplete_identity_observations,
        "unscoped_observations_excluded_by_episode_filter": unscoped_observations_excluded,
        "categories": [{"category": category, "label": label,
                        "observation_count": categories[category],
                        "observation_share": _metric(categories[category], observation_count,
                            basis="all matching historical issue observations; repeats included"),
                        "unique_problem_count": unique_categories[category],
                        "unique_problem_share": _metric(unique_categories[category], len(rows),
                            basis="distinct file/version/location/finding problems; latest classification")}
                       for category, label in CATEGORY_LABELS.items()],
        "quality_metrics": {
            "confirmed_content_shot_rate": _confirmed_shot_rate(
                events, filtered=bool(selected_episodes or selected_codes)),
            "false_positive_rate": _metric(None, None,
                basis="independently adjudicated findings with retained evidence",
                unavailable_reason="suspected_false_positives_are_not_adjudicated_false_positives"),
            "repair_recurrence_rate": _metric(None, None,
                basis="confirmed repaired findings receiving an equivalent-scope follow-up review",
                unavailable_reason="requires_bound_repair_and_followup_evidence"),
            "raw_first_draft_content_error_rate": _metric(None, None,
                basis="verified unmodified first generated candidates",
                unavailable_reason="earliest_registered_audit_is_not_first_generation_proof"),
            "image_or_video_generation_failure_rate": None,
        },
        "repair_attribution": {"confirmed_introduced_count": None,
                               "legacy_introduced_observations_requiring_proof": unsupported_introduced_observations,
                               "rule": "newly discovered does not establish introduced by repair"},
        "problems": rows[max(0, cursor):max(0, cursor) + max(0, limit)],
        "cursor": max(0, cursor),
        "next_cursor": max(0, cursor) + max(0, limit) if limit > 0 and max(0, cursor) + limit < len(rows) else None,
        "limitations": [
            "Classification describes retained evidence, not observed image/video quality.",
            "Format and suspected false-positive labels do not waive existing audit gates.",
            "Exact evidence fallback may split paraphrases; uncertain roots are not merged.",
            "Original audit events and their recorded statuses remain unchanged.",
        ],
    }


def classify_report_issues(report: dict[str, Any], *, event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact classification for one diagnostics/audit response, without history."""
    classifications = [{"issue_index": index, **classify_issue(issue, event=event)}
                       for index, issue in enumerate(report.get("issues", [])) if isinstance(issue, dict)]
    counts = Counter(row["category"] for row in classifications)
    return {"contract": METRICS_CONTRACT, "changes_audit_verdict": False,
            "observation_count": len(classifications), "classification": classifications,
            "categories": [{"category": category, "label": label, "count": counts[category],
                            "share": _metric(counts[category], len(classifications),
                                             basis="issue observations in this report")}
                           for category, label in CATEGORY_LABELS.items()],
            "generation_failure_rate": None}
