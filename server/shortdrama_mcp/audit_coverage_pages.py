"""Immutable signed review pages; staging is never a completed audit.

The service invokes this only after runtime, artifact, independent actor,
session and exact handoff/fingerprint validation. Finalize reconstructs the
declared review, then the normal full audit and certificate path still runs.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .audit_coverage import CONTRACT, MAX_ENVELOPE_BYTES, build_obligations, coverage_context, validate_coverage, verify_certificate_coverage
from .canonical import stable_json_hash, canonical_sha256
from .models import SemanticAuditCoveragePageInput, ToolResponse

PAGE_CONTRACT = "semantic-obligation-pages-v1"
COLLECTION_KIND = "audit_coverage_collections"
PAGE_KIND = "audit_coverage_pages"


def coverage_progress(service: Any, namespace: str, project_id: str,
                      artifact_id: str | None = None) -> dict[str, Any]:
    pending, stale, completed_count = [], [], 0
    collections = service.store.list_project_records_strict(namespace, project_id, kind=COLLECTION_KIND)
    pages = service.store.list_project_records_strict(namespace, project_id, kind=PAGE_KIND)
    for collection in collections:
        binding = collection.get("binding") or {}
        if artifact_id is not None and binding.get("artifact_id") != artifact_id:
            continue
        try:
            storyboard, record = service._storyboard_from_artifact(namespace, project_id,
                binding["artifact_id"], persist_recovered_canonical=False, require_bound_projection=True)
            fingerprints = service._fingerprint_bundle(storyboard, record,
                canonical_hash=canonical_sha256(storyboard),
                source_fingerprint=service._source_fingerprint(namespace, project_id, storyboard, record.get("source_artifact_id"), record),
                boundary_hash=service._boundary_hash(storyboard))
            if binding.get("fingerprints") != fingerprints:
                stale.append({"collection_id": collection["collection_id"], "artifact_id": binding.get("artifact_id"),
                              "status": "stale_review_evidence", "counts_as_current_review": False})
                continue
            completed = collection_has_signed_audit(service, namespace, project_id, storyboard, collection)
        except (ValueError, KeyError):
            completed = False
        if not completed:
            accepted = [page for page in pages if page.get("collection_id") == collection["collection_id"]]
            pending.append({"collection_id": collection["collection_id"], "artifact_id": binding.get("artifact_id"),
                            "work_order_id": binding.get("work_order_id"), "page_count": len(accepted),
                            "accepted_count": sum(page.get("count", 0) for page in accepted),
                            "expected_count": collection.get("expected_count"),
                            "status": "coverage_pending"})
        else:
            completed_count += 1
    return {"pending_count": len(pending), "pending_collections": pending,
            "stale_count": len(stale), "stale_collections": stale, "completed_current_count": completed_count,
            "counts_as_complete": False, "meaning": "Staged reviews are not completed independent audits."}


def collection_has_signed_audit(service: Any, namespace: str, project_id: str,
                                storyboard: dict[str, Any], collection: dict[str, Any]) -> bool:
    """An immutable collection is complete only through its exact certificate."""
    store, binding = service.store, collection["binding"]
    for cert in store.list_project_records_strict(namespace, project_id, kind="certificates"):
        proof = cert.get("audit_coverage_page_receipt")
        if (not store.project_record_is_signed(cert) or not isinstance(proof, dict) or
                cert.get("certificate_type") != "independent_audit" or
                cert.get("work_order_id") != binding["work_order_id"] or
                cert.get("artifact_id") != binding["artifact_id"] or
                proof.get("collection_id") != collection["collection_id"] or
                proof.get("binding") != binding or
                proof != (cert.get("report") or {}).get("audit_coverage_page_receipt") or
                proof.get("manifest_sha256") != binding["manifest_sha256"] or
                proof.get("checks_sha256") != (cert.get("semantic_obligation_coverage") or {}).get("checks_sha256")):
            continue
        try:
            verify_certificate_coverage(service, storyboard, cert)
        except (ValueError, KeyError, TypeError):
            continue
        return True
    return False


def assert_no_pending_coverage(service: Any, namespace: str, project_id: str,
                               storyboard: dict[str, Any], artifact_id: str,
                               fingerprints: dict[str, str]) -> None:
    for collection in service.store.list_project_records_strict(namespace, project_id, kind=COLLECTION_KIND):
        binding = collection.get("binding") or {}
        if (binding.get("artifact_id") == artifact_id and binding.get("fingerprints") == fingerprints and
                not collection_has_signed_audit(service, namespace, project_id, storyboard, collection)):
            raise ValueError("audit_coverage_pending: current independent review pages have not completed formal audit")


def process_coverage_page(service: Any, namespace: str, project_id: str, *,
                          storyboard: dict[str, Any], artifact: dict[str, Any],
                          audit_work_order: dict[str, Any], handoff: dict[str, Any],
                          fingerprints: dict[str, str], submission: dict[str, Any]) -> ToolResponse | dict[str, Any]:
    page = SemanticAuditCoveragePageInput.model_validate(submission["coverage_page"]).model_dump()
    if submission.get("coverage") is not None:
        raise ValueError("audit_coverage_conflicting_transports")
    if len(json.dumps(page, ensure_ascii=False).encode("utf-8")) > 512*1024:
        raise ValueError("audit_coverage_page_too_large")
    context = coverage_context(service)
    manifest = build_obligations(storyboard, context)
    if (page["manifest_sha256"] != manifest["manifest_sha256"] or
            submission.get("canonical_sha256") != manifest["canonical_sha256"]):
        raise ValueError("audit_coverage_manifest_stale: fetch current audit_obligations")
    authority = service._active_authority_binding(namespace, project_id, audit_work_order)
    binding = {
        "namespace_sha256": stable_json_hash(namespace), "project_id": project_id,
        "artifact_id": artifact["artifact_id"], "artifact_sha256": artifact["fingerprint"],
        "work_order_id": audit_work_order["work_order_id"], "handoff_id": handoff["handoff_id"],
        "authority": authority, "fingerprints": fingerprints,
        "manifest_sha256": manifest["manifest_sha256"], "contract": PAGE_CONTRACT,
    }
    collection_id = page.get("collection_id")
    if page.get("collection_key"):
        collection_id = "ACOL-" + stable_json_hash({"binding": binding, "key": page["collection_key"]})[:40]
    store = service.store

    def current_collection():
        nonlocal binding
        value = store.get_project_record(namespace, project_id, kind=COLLECTION_KIND, record_id=collection_id)
        recorded = value.get("binding") or {}
        if not store.project_record_is_signed(value):
            raise ValueError("audit_coverage_collection_binding_mismatch")
        if recorded != binding:
            stable_keys = lambda data: {k: v for k, v in data.items() if k != "authority"}
            old_authority = recorded.get("authority") or {}
            if (stable_keys(recorded) != stable_keys(binding) or
                    old_authority.get("authority_root_id") != authority.get("authority_root_id") or
                    service._verified_authority_binding_at_generation(
                        namespace, project_id, audit_work_order, old_authority.get("generation")) != old_authority):
                raise ValueError("audit_coverage_collection_binding_mismatch")
            # A signed continuation keeps the immutable collection and older
            # evidence; it does not rewrite its original actor or manifest.
            binding = copy.deepcopy(recorded)
        return value

    def collection_pages():
        values = [value for value in store.list_project_records_strict(namespace, project_id, kind=PAGE_KIND)
                  if value.get("collection_id") == collection_id]
        for value in values:
            if (value.get("binding") != binding or
                    value.get("checks_sha256") != stable_json_hash(value.get("checks")) or
                    not store.project_record_is_signed(value)):
                raise ValueError("audit_coverage_page_binding_mismatch")
        return sorted(values, key=lambda value: value["offset"])

    if page["action"] == "append":
        rows = manifest["obligations"][page["offset"]:page["offset"]+len(page["checks"])]
        expected = [row["obligation_id"] for row in rows]
        actual = [row["obligation_id"] for row in page["checks"]]
        if not expected or actual != expected:
            raise ValueError("audit_coverage_page_range_mismatch")
        partial = {**submission, "coverage": {"contract": CONTRACT,
                    "manifest_sha256": manifest["manifest_sha256"], "checks": page["checks"]}}
        validate_coverage(storyboard, partial, context, _page_ids=expected)
        # Preserve every declared finding, including one whose check is on a
        # later page. A caller must not drop an inconvenient declared issue by
        # omitting its issue_id from this page's checks.
        bound_issues = copy.deepcopy(submission.get("issues", []))
        page_id = "ACP-" + stable_json_hash({"collection_id": collection_id, "offset": page["offset"]})[:40]
        value = {"page_id": page_id, "collection_id": collection_id, "binding": binding,
                 "offset": page["offset"], "count": len(actual), "checks": page["checks"],
                 "checks_sha256": stable_json_hash(page["checks"]), "issues": bound_issues,
                 "status": "coverage_pending"}
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 512*1024:
            raise ValueError("audit_coverage_page_too_large")
        with store.project_write_lock(namespace, project_id):
            if page.get("collection_key"):
                try:
                    collection = current_collection()
                except ValueError as exc:
                    if not str(exc).startswith("unknown audit_coverage_collections record_id"):
                        raise
                    for previous in store.list_project_records_strict(namespace, project_id, kind=COLLECTION_KIND):
                        stable_scope = lambda data: {k: v for k, v in data.items() if k != "authority"}
                        if stable_scope(previous.get("binding") or {}) == stable_scope(binding) and not collection_has_signed_audit(service, namespace, project_id, storyboard, previous):
                            raise ValueError("audit_coverage_collection_already_pending: a new key cannot discard staged findings")
                    collection = store.save_project_record(namespace, project_id, kind=COLLECTION_KIND,
                        record_id=collection_id, value={"collection_id": collection_id, "binding": binding,
                            "collection_key_sha256": stable_json_hash(page["collection_key"]),
                            "status": "coverage_pending", "expected_count": manifest["obligation_count"]})
            else:
                collection = current_collection()
            value["binding"] = binding
            accepted = collection_pages()
            existing = next((item for item in accepted if item["page_id"] == page_id), None)
            if existing is not None:
                if any(existing.get(key) != item for key, item in value.items()):
                    raise ValueError("audit_coverage_page_immutable_conflict")
                persisted = existing
            else:
                present = {row["obligation_id"] for item in accepted for row in item["checks"]}
                if present & set(actual):
                    raise ValueError("audit_coverage_page_overlap")
                staged_bytes = sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in accepted)
                if len(accepted) >= 512 or staged_bytes + len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_ENVELOPE_BYTES:
                    raise ValueError("audit_coverage_collection_limit_exceeded")
                persisted = store.save_project_record(namespace, project_id, kind=PAGE_KIND,
                                                       record_id=page_id, value={**value, "accepting_authority": authority})
                accepted.append(persisted)
            accepted.sort(key=lambda item: item["offset"])
            total = sum(item["count"] for item in accepted)
            contiguous_end = 0
            for item in accepted:
                if item["offset"] != contiguous_end:
                    break
                contiguous_end += item["count"]
        return ToolResponse(status="coverage_pending", summary="审计证据页已暂存｜尚未完成全量审计，不可交付",
            project_id=project_id, work_order_id=audit_work_order["work_order_id"], artifact_id=artifact["artifact_id"],
            data={"contract": PAGE_CONTRACT, "collection_id": collection_id, "page_id": page_id,
                  "page_integrity_hmac": persisted["integrity_hmac"], "offset": page["offset"],
                  "count": len(actual), "accepted_count": total, "expected_count": manifest["obligation_count"],
                  "manifest_sha256": manifest["manifest_sha256"],
                  "accepted_page_ids": [item["page_id"] for item in accepted],
                  "next_offset": contiguous_end if contiguous_end < manifest["obligation_count"] else None,
                  "counts_as_complete": False, "formal_delivery_ready": False,
                  "audit_pass": False, "idempotent_replay": existing is not None})

    # Explicit IDs are required. Never select an implicit latest collection or
    # let the client hide a previously staged failing page from final review.
    with store.project_write_lock(namespace, project_id):
        current_collection()
        accepted = collection_pages()
        if page["page_ids"] != [item["page_id"] for item in accepted]:
            raise ValueError("audit_coverage_finalize_page_set_mismatch")
        checks, offset, expected_issues = [], 0, {}
        for item in accepted:
            if item["offset"] != offset or item["count"] != len(item["checks"]):
                raise ValueError("audit_coverage_finalize_range_incomplete")
            checks.extend(item["checks"])
            offset += item["count"]
            for issue in item.get("issues", []):
                if issue["issue_id"] in expected_issues and expected_issues[issue["issue_id"]] != issue:
                    raise ValueError("audit_coverage_page_issue_conflict")
                expected_issues[issue["issue_id"]] = issue
        final_issues = {row["issue_id"]: row for row in submission.get("issues", [])}
        if any(final_issues.get(key) != value for key, value in expected_issues.items()):
            raise ValueError("audit_coverage_finalize_issue_mismatch")
        result = copy.deepcopy(submission)
        result["coverage_page"] = None
        result["coverage"] = {"contract": CONTRACT, "manifest_sha256": manifest["manifest_sha256"], "checks": checks}
        validate_coverage(storyboard, result, context)
        result["_server_coverage_page_receipt"] = {
            "collection_id": collection_id, "page_ids": list(page["page_ids"]),
            "binding": binding, "manifest_sha256": manifest["manifest_sha256"],
            "checks_sha256": stable_json_hash(checks),
            "finalizing_authority": authority,
        }
    return result
