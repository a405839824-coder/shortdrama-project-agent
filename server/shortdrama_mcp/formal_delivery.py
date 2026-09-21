"""Read-only, server-owned eligibility for the one formal storyboard template.

Files created by a host or another tool remain outside this service's control.
Their names, public hashes, and caller-provided PASS flags confer no eligibility.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from .canonical import canonical_sha256 as canonical_storyboard_sha256
from .file_io import extract_xlsx
from .materializer import MOTHER_TEMPLATE_COLUMNS
from .models import ToolResponse
from .production_plan import model_production_contract
from .audit_coverage import verify_certificate_coverage
from .r5_workbook import workbook_audit_scope


CONTRACT = "formal-storyboard-delivery-v1"


def delivery_contract():
    context = model_production_contract()["context"]
    return {
        "contract": CONTRACT,
        "output_contract": "R10-SPC-1",
        "template_contract_sha256": context["template_contract_sha256"],
        "rule_registry_sha256": context["rule_registry_sha256"],
        "columns": list(MOTHER_TEMPLATE_COLUMNS),
        "canonical_field_count": 27,
        "workbook_audit_scope": workbook_audit_scope(),
        "renderer": "server_generated_master_and_same_source_R5",
        "verification": "read_project_artifact(section='formal_delivery')",
        "formal_delivery_requires": ["registered_server_storyboard", "exact_file_bytes",
            "current_template", "signed_production_preflight", "current_independent_audit_pass",
            "signed_current_semantic_obligation_coverage"],
        "client_must_match_download_sha256": True,
        "fallback_export_allowed": False,
        "unverified_counts_as_complete": False,
        "unverified_allows_next_batch": False,
        "continuation_precondition_only": True,
        "user_stop_and_quota_override": True,
        "batch_request": {"artifact_type": "production_batch", "output_format": "json",
            "structured_data": {"storyboard_artifact_ids": ["exact server artifact ID"]}},
        "scope": "All authenticated accounts, including new ChatGPT and Codex installations.",
        "host_boundary": "The service cannot prevent other tools from writing local files; hosts must verify this receipt before formal delivery, completion counting, or automatic continuation.",
    }


def inspect_formal_delivery(service, namespace, project_id, artifact_id):
    # Resolve namespace and bytes before returning any artifact-specific data.
    record, path = service.store.get_artifact(namespace, project_id, artifact_id)
    contract = delivery_contract()
    receipt = {
        "contract": CONTRACT, "project_id": project_id, "artifact_id": artifact_id,
        "namespace_sha256": hashlib.sha256(namespace.encode()).hexdigest(),
        "file_name": record["file_name"], "file_sha256": record["fingerprint"],
        "template_contract_sha256": contract["template_contract_sha256"],
        "rule_registry_sha256": contract["rule_registry_sha256"],
        "template_verified": False, "production_preflight_verified": False,
        "independent_audit_verified": False, "formal_delivery_ready": False,
        "semantic_obligation_coverage_verified": False,
        "counts_as_complete": False, "allows_next_batch": False,
        "issues": [],
    }
    try:
        if record.get("artifact_type") != "storyboard" or path.suffix.lower() != ".xlsx":
            raise ValueError("formal_delivery_server_storyboard_required")
        metadata = record.get("metadata") or {}
        if metadata.get("formal_delivery_contract") != CONTRACT:
            raise ValueError("formal_delivery_current_publication_binding_required")
        if any(metadata.get(k) != contract[k] for k in ("template_contract_sha256", "rule_registry_sha256")):
            raise ValueError("formal_delivery_template_version_stale")
        storyboard, _ = service._storyboard_from_artifact(
            namespace, project_id, artifact_id,
            persist_recovered_canonical=False, require_bound_projection=True)
        canonical_hash = canonical_storyboard_sha256(storyboard)
        # Always reparse actual visible cells. A cached extraction or a linked
        # JSON is insufficient to attest the workbook being delivered.
        extracted = extract_xlsx(path.read_bytes())
        if (not extracted.get("canonical_sidecar_present") or
                canonical_storyboard_sha256(extracted.get("canonical_storyboard") or {}) != canonical_hash):
            raise ValueError("formal_delivery_visible_workbook_mismatch")
        receipt["canonical_sha256"] = canonical_hash
        receipt["template_verified"] = True
        certificate_id = str(metadata.get("production_certificate_id") or "")
        certificate = service.store.get_project_record(
            namespace, project_id, kind="certificates", record_id=certificate_id)
        if (not service.store.project_record_is_signed(certificate)
                or certificate.get("certificate_type") != "production_preflight"
                or certificate.get("artifact_id") != artifact_id
                or certificate.get("canonical_sha256") != canonical_hash
                or certificate.get("work_order_id") != record.get("work_order_id")
                or certificate.get("preflight_status") != "production_preflight_pass"
                or certificate.get("preflight_issue_count") != 0
                or certificate.get("formal_delivery_context") != contract
                or certificate.get("cache_components", {}).get("artifact") != record["fingerprint"]):
            raise ValueError("formal_delivery_production_certificate_invalid")
        hub = service.store.load_hub(namespace, project_id)
        work_order = next((w for w in hub.get("work_orders", [])
                           if w.get("work_order_id") == record.get("work_order_id")), None)
        if work_order is None:
            raise ValueError("formal_delivery_production_work_order_missing")
        if certificate.get("ruleset_version") != service._ruleset_version:
            raise ValueError("formal_delivery_production_rules_stale")
        receipt["production_preflight_verified"] = True
        receipt["production_certificate_id"] = certificate_id
        # This existing verifier checks exact authority, current fingerprints,
        # clean signed audit, and the latest completed handoff/reaudit lineage.
        audit = service._clean_independent_certificate_for_work_order(
            namespace, project_id, hub, str(record["work_order_id"]))
        if audit.get("artifact_id") != artifact_id or audit.get("canonical_sha256") != canonical_hash:
            raise ValueError("formal_delivery_independent_audit_wrong_artifact")
        receipt["semantic_obligation_coverage"] = verify_certificate_coverage(service, storyboard, audit)
        receipt["semantic_obligation_coverage_verified"] = True
        receipt["independent_audit_verified"] = True
        receipt["independent_audit_certificate_id"] = audit["certificate_id"]
        receipt["formal_delivery_ready"] = True
        receipt["counts_as_complete"] = True
        receipt["allows_next_batch"] = True
    except (ValueError, KeyError, TypeError) as exc:
        receipt["issues"].append(str(exc))
    now = datetime.now(timezone.utc)
    receipt["verified_at"] = now.isoformat()
    receipt["expires_at"] = (now + timedelta(minutes=5)).isoformat()
    receipt["integrity_hmac"] = service.store._record_integrity_hmac(receipt)
    ready = receipt["formal_delivery_ready"]
    return ToolResponse(
        status="formal_delivery_ready" if ready else "formal_delivery_blocked",
        summary="唯一模板与当前独审已核验｜可正式交付" if ready else "正式交付未通过｜不得计入完成或启动下一批",
        project_id=project_id, artifact_id=artifact_id,
        download_url=service.store.signed_download_url(namespace, project_id, artifact_id, record["file_name"]) if ready else None,
        data={"formal_delivery": receipt, "delivery_contract": contract})


def verified_batch(service, namespace, project_id, request):
    """Build completion data from server proofs, never caller-written scripts."""
    if not isinstance(request, dict) or set(request) != {"storyboard_artifact_ids"}:
        raise ValueError("formal_batch_requires_storyboard_artifact_ids_only")
    ids = request["storyboard_artifact_ids"]
    if (not isinstance(ids, list) or not ids or len(ids) > 100
            or any(not isinstance(x, str) or not x for x in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("formal_batch_requires_unique_registered_storyboards")
    verified = []
    for artifact_id in ids:
        result = inspect_formal_delivery(service, namespace, project_id, artifact_id)
        receipt = result.data["formal_delivery"]
        if receipt["formal_delivery_ready"] is not True:
            raise ValueError("formal_batch_unverified_storyboard: " + artifact_id + ": " + "; ".join(receipt["issues"]))
        verified.append(receipt)
    return {"contract": "formal-storyboard-batch-v1", "storyboard_artifact_ids": ids,
            "formal_delivery_receipts": verified, "counts_as_complete": True}
