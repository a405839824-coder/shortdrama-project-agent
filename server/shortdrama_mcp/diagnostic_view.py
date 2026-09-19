"""Bounded, snapshot-bound presentation of read-only machine findings.

This module never changes a validator verdict or persists audit conclusions.
It separates input/evidence gaps from candidate content findings and makes a
large finding retrievable in full without dumping the whole report into a chat.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

from .canonical import stable_json_hash


CONTRACT = "paged-storyboard-diagnostics-v1"
GROUPS = ("issues", "unscoped_or_dependency_issues", "outside_scope_issues")


def category(issue, group):
    if issue.get("classification") == "input_mapping":
        return "input_mapping"
    if (str(issue.get("error_code", "")).startswith("E-TEMPLATE") or
            "canonical_projection" in str(issue.get("location", ""))):
        return "input_mapping"
    if (group == "unscoped_or_dependency_issues" or
            str(issue.get("location", "")).startswith("prevention_evidence")):
        return "evidence_or_dependency"
    return "candidate_content_finding"


def diagnostic_page(response, *, section=None, offset=0, limit=50, max_chars=12000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("diagnostic_pagination_invalid: offset>=0 and limit>=1 required")
    if type(max_chars) is not int or max_chars < 512:
        raise ValueError("diagnostic_budget_invalid: max_chars must be at least 512")
    budget = min(max_chars, 16000)
    data = response.data
    rows = [(group, issue) for group in GROUPS for issue in data.get(group, [])]
    identity = {k: data.get(k) for k in ("artifact_fingerprint", "ruleset_version",
        "validator_version", "requested_episode_ids")}
    snapshot = stable_json_hash({"identity": identity, "findings": rows})
    expected, detail_index = None, None
    if section:
        match = re.fullmatch(r"diagnostic:([0-9a-f]{64})(?::([0-9]+))?", section)
        if not match:
            raise ValueError("diagnostic_section_invalid: use the returned continuation section")
        expected, detail = match.groups()
        detail_index = int(detail) if detail is not None else None
    if expected and expected != snapshot:
        raise ValueError("diagnostic_snapshot_changed: restart at offset=0 without a section")
    result = {k: copy.deepcopy(v) for k, v in data.items() if k not in GROUPS}
    counts = {group: len(data.get(group, [])) for group in GROUPS}
    result.update(diagnostic_contract=CONTRACT, diagnostic_snapshot_sha256=snapshot,
        finding_counts=counts,
        classification_counts=dict(Counter(category(issue, group) for group, issue in rows)),
        finding_meaning="Machine findings, not confirmed content errors or an accuracy denominator.")
    common = {"project_id": response.project_id, "artifact_id": response.artifact_id,
        "diagnose_storyboard": True, "diagnostic_episode_ids": data.get("requested_episode_ids", []),
        "max_chars": budget}
    if detail_index is not None:
        if detail_index >= len(rows):
            raise ValueError("diagnostic_detail_missing")
        group, issue = rows[detail_index]
        full = json.dumps(issue, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if offset > len(full):
            raise ValueError("diagnostic_detail_offset_out_of_range")
        chunk = full[offset:offset + budget]
        end = offset + len(chunk)
        result.update(detail={"issue_index": detail_index, "group": group,
            "classification": category(issue, group), "encoding": "JSON text chunks; concatenate before parsing",
            "text": chunk, "offset": offset, "end_offset": end, "total_chars": len(full)},
            page={"has_more": end < len(full), "next_call":
                {**common, "section": section, "offset": end} if end < len(full) else None})
    else:
        if offset > len(rows):
            raise ValueError("diagnostic_offset_out_of_range")
        result.update({group: [] for group in GROUPS})
        page_rows = []
        end = offset
        for index in range(offset, min(len(rows), offset + min(limit, 50))):
            group, original = rows[index]
            row = copy.deepcopy(original)
            row.update(issue_index=index, classification=category(original, group),
                detail_section=f"diagnostic:{snapshot}:{index}")
            size = len(json.dumps(page_rows + [row], ensure_ascii=False, separators=(",", ":")))
            if size > budget:
                if page_rows:
                    break
                # Never drop or pretend to have delivered the rest of a large
                # issue. Its exact JSON is available through the detail route.
                row = {"issue_index": index, "error_code": str(original.get("error_code", ""))[:64],
                    "classification": category(original, group), "detail_required": True,
                    "detail_section": f"diagnostic:{snapshot}:{index}"}
            page_rows.append(row)
            result[group].append(row)
            end = index + 1
        result["page"] = {"offset": offset, "end_offset": end, "total_findings": len(rows),
            "returned_count": len(page_rows), "has_more": end < len(rows),
            "max_finding_chars": budget, "budget_excludes_fixed_metadata": True,
            "next_call": {**common, "section": f"diagnostic:{snapshot}",
                "offset": end, "limit": min(limit, 50)} if end < len(rows) else None}
    return response.model_copy(update={"data": result})
