"""Bounded public views of signed audit evidence; never rewrite signed reports."""
from __future__ import annotations

import copy
import json

from .canonical import stable_json_hash
from .models import ToolResponse


CONTRACT = "paged-signed-audit-evidence-v1"


def _read_call(project_id, artifact_id, certificate_id, **kwargs):
    return {"project_id": project_id, "artifact_id": artifact_id,
            "section": f"audit_evidence:{certificate_id}", **kwargs}


def compact_audit_response(response: ToolResponse) -> ToolResponse:
    """Project the public response only, retaining issues and all other fields.

    The checks list is replaced by an explicitly marked summary, never an empty
    list that could be mistaken for the signed submission. Without a persisted
    certificate identity no evidence is omitted: there is no exact read route.
    """
    result = response.model_copy(deep=True)
    data = result.data
    report = data.get("report")
    semantic = report.get("semantic_audit") if isinstance(report, dict) else None
    coverage = semantic.get("coverage") if isinstance(semantic, dict) else None
    certificate = data.get("certificate")
    certificate_id = certificate.get("certificate_id") if isinstance(certificate, dict) else None
    if not (isinstance(coverage, dict) and isinstance(coverage.get("checks"), list)
            and isinstance(certificate_id, str) and certificate_id and response.project_id
            and response.artifact_id and certificate.get("artifact_id") == response.artifact_id):
        return result
    checks = coverage["checks"]
    coverage["checks"] = {
        "projection": "omitted_from_public_response",
        "omitted": True, "count": len(checks), "sha256": stable_json_hash(checks),
        "read_tool": "read_project_artifact",
        "read_call": _read_call(response.project_id, response.artifact_id, certificate_id,
                                offset=0, limit=20, max_chars=12000),
        "meaning": "Read the signed checks through this exact certificate; this summary is not audit input.",
    }
    data["audit_evidence_projection"] = {
        "contract": CONTRACT, "signed_report_unchanged": True,
        "response_report_is_projection": True,
        "signed_report_sha256": certificate.get("report_sha256"),
        "meaning": "The signed report hash applies to the stored report, not this compact response.",
    }
    return result


def read_audit_evidence(service, namespace, project_id, artifact_id, certificate_id,
                        offset=0, limit=20, max_chars=12000, detail_index=None):
    """Read one exact signed certificate; this never verifies release eligibility."""
    if (type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1
            or (detail_index is not None and (type(detail_index) is not int or detail_index < 0))):
        raise ValueError("audit_evidence_pagination_invalid")
    if type(max_chars) is not int or max_chars < 512:
        raise ValueError("audit_evidence_budget_invalid: max_chars must be at least 512")
    budget, page_limit = min(max_chars, 16000), min(limit, 50)
    certificate = service.store.get_project_record(namespace, project_id,
        kind="certificates", record_id=certificate_id)
    if not service.store.project_record_is_signed(certificate):
        raise ValueError("audit_evidence_signature_required")
    if (certificate.get("certificate_id") != certificate_id
            or certificate.get("project_id") != project_id
            or certificate.get("artifact_id") != artifact_id):
        raise ValueError("audit_evidence_artifact_binding_mismatch")
    report = certificate.get("report")
    if not isinstance(report, dict):
        raise ValueError("audit_evidence_report_missing")
    actual_report_hash = stable_json_hash(report)
    if certificate.get("report_sha256") and certificate["report_sha256"] != actual_report_hash:
        raise ValueError("audit_evidence_report_hash_mismatch")
    semantic = report.get("semantic_audit")
    coverage = semantic.get("coverage") if isinstance(semantic, dict) else None
    checks = coverage.get("checks") if isinstance(coverage, dict) else None
    if checks is not None and not isinstance(checks, list):
        raise ValueError("audit_evidence_checks_invalid")
    available = checks is not None
    checks = checks if available else []
    data = {"contract": CONTRACT, "read_only": True, "certificate_id": certificate_id,
        "certificate_signature_verified": True, "report_sha256": actual_report_hash,
        "stored_semantic_contract": certificate.get("semantic_audit_contract"),
        "coverage_available": available, "check_count": len(checks) if available else None,
        "checks_sha256": stable_json_hash(checks) if available else None,
        "current_release_eligibility": "not_verified", "counts_as_complete": False,
        "certificate_created": False, "historical_evidence_repaired": False,
        "meaning": "Read-only signed historical/current evidence; not current audit, sign-off or delivery approval."}
    common = _read_call(project_id, artifact_id, certificate_id, max_chars=budget)
    if detail_index is not None:
        if detail_index >= len(checks):
            raise ValueError("audit_evidence_detail_missing")
        text = json.dumps(checks[detail_index], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if offset > len(text):
            raise ValueError("audit_evidence_detail_offset_out_of_range")
        end = min(len(text), offset + budget)
        data["detail"] = {"check_index": detail_index, "text": text[offset:end],
            "offset": offset, "end_offset": end, "total_chars": len(text),
            "encoding": "JSON text chunks; concatenate all chunks before parsing"}
        data["page"] = {"has_more": end < len(text), "next_call": {
            **common, "section": f"audit_evidence:{certificate_id}:{detail_index}",
            "offset": end} if end < len(text) else None}
    else:
        if offset > len(checks):
            raise ValueError("audit_evidence_offset_out_of_range")
        rows, end = [], offset
        for index in range(offset, min(len(checks), offset + page_limit)):
            row = {"check_index": index, "check": copy.deepcopy(checks[index])}
            if len(json.dumps(rows + [row], ensure_ascii=False, separators=(",", ":"))) > budget:
                if rows:
                    break
                row = {"check_index": index, "detail_required": True,
                    "detail_call": {**common, "section": f"audit_evidence:{certificate_id}:{index}", "offset": 0}}
                if len(json.dumps([row], ensure_ascii=False, separators=(",", ":"))) > budget:
                    raise ValueError("audit_evidence_budget_too_small_for_reference")
            rows.append(row)
            end = index + 1
        data["checks"] = rows
        data["page"] = {"offset": offset, "end_offset": end, "total_checks": len(checks) if available else None,
            "returned_count": len(rows), "has_more": end < len(checks), "max_check_chars": budget,
            "budget_excludes_fixed_metadata": True, "next_call":
            {**common, "offset": end, "limit": page_limit} if end < len(checks) else None}
    return ToolResponse(status="audit_evidence", summary="签名审计证据只读分页｜不签发当前通过结论",
                        project_id=project_id, artifact_id=artifact_id, data=data)
