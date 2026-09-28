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
REVISION_KIND = "audit_coverage_revisions"


def collection_snapshot(service: Any, namespace: str, project_id: str,
                        collection_id: str) -> dict[str, Any]:
    """Verify and replay append-only pages and revisions, including old rules.

    A revision binds every page visible at its CAS boundary. Later pages name
    that revision's head, so their new findings cannot resurrect a retraction or
    silently replace earlier findings. Nothing is inferred from timestamps.
    """
    store = service.store
    collection = store.get_project_record(namespace, project_id, kind=COLLECTION_KIND, record_id=collection_id)
    binding = collection.get("binding") or {}
    if not store.project_record_is_signed(collection):
        raise ValueError("audit_coverage_collection_signature_required")
    pages = sorted((r for r in store.list_project_records_strict(namespace, project_id, kind=PAGE_KIND)
                    if r.get("collection_id") == collection_id), key=lambda r: r["offset"])
    revisions = sorted((r for r in store.list_project_records_strict(namespace, project_id, kind=REVISION_KIND)
                        if r.get("collection_id") == collection_id), key=lambda r: r["revision_index"])
    for row in pages + revisions:
        if row.get("binding") != binding or not store.project_record_is_signed(row):
            raise ValueError("audit_coverage_record_binding_mismatch")
    for row in pages:
        if row.get("checks_sha256") != stable_json_hash(row.get("checks")) or row.get("count") != len(row.get("checks", [])):
            raise ValueError("audit_coverage_page_binding_mismatch")
    page_map = {row["page_id"]: row for row in pages}
    checks, issues, consumed, applied, retracted, conflicts = {}, {}, set(), [], set(), {}

    def digest(selected: list[dict[str, Any]]) -> str:
        return stable_json_hash({"collection_sha256": stable_json_hash(collection),
            "pages": [{"page_id": r["page_id"], "sha256": stable_json_hash(r)} for r in selected],
            "revisions": [{"revision_id": r["revision_id"], "sha256": stable_json_hash(r)} for r in applied],
            "checks": list(checks.values()), "issues": sorted(issues.values(), key=lambda r: r["issue_id"])})

    def ingest(selected: list[dict[str, Any]]) -> None:
        head = applied[-1]["revision_id"] if applied else None
        for row in selected:
            if row["page_id"] in consumed:
                continue
            if row.get("revision_head") != head:
                raise ValueError("audit_coverage_page_revision_head_mismatch")
            for check in row["checks"]:
                if check["obligation_id"] in checks:
                    raise ValueError("audit_coverage_page_overlap")
                checks[check["obligation_id"]] = copy.deepcopy(check)
            for issue in row.get("issues", []):
                identity = issue["issue_id"]
                if identity in retracted:
                    raise ValueError("audit_coverage_page_issue_conflict")
                if identity in issues and issues[identity] != issue:
                    # Legacy append accepted conflicting issue versions. Keep
                    # them readable, but never choose one as final truth.
                    conflicts.setdefault(identity, [copy.deepcopy(issues[identity])]).append(copy.deepcopy(issue))
                    continue
                issues[identity] = copy.deepcopy(issue)
            consumed.add(row["page_id"])

    for index, revision in enumerate(revisions, 1):
        if (revision.get("revision_index") != index or
                revision.get("previous_revision_id") != (applied[-1]["revision_id"] if applied else None)):
            raise ValueError("audit_coverage_revision_chain_invalid")
        ids = revision.get("page_ids", [])
        if (len(ids) != len(set(ids)) or not consumed.issubset(ids) or any(pid not in page_map for pid in ids)):
            raise ValueError("audit_coverage_revision_page_set_mismatch")
        selected = [r for r in pages if r["page_id"] in ids]
        if ids != [r["page_id"] for r in selected]:
            raise ValueError("audit_coverage_revision_page_set_mismatch")
        ingest(selected)
        if revision.get("expected_snapshot_sha256") != digest(selected):
            raise ValueError("audit_coverage_revision_snapshot_mismatch")
        for check in revision.get("checks", []):
            if check["obligation_id"] not in checks:
                raise ValueError("audit_coverage_revision_check_not_staged")
            checks[check["obligation_id"]] = copy.deepcopy(check)
        for identity in revision.get("retract_issue_ids", []):
            if identity not in issues:
                raise ValueError("audit_coverage_revision_issue_not_staged")
            del issues[identity]
            retracted.add(identity)
            conflicts.pop(identity, None)
        for issue in revision.get("issue_upserts", []):
            issues[issue["issue_id"]] = copy.deepcopy(issue)
            retracted.discard(issue["issue_id"])
            conflicts.pop(issue["issue_id"], None)
        applied.append(revision)
    ingest(pages)
    # Preserve the manifest/page order even after corrections and sparse pages.
    ordered_checks = [checks[c["obligation_id"]] for p in pages for c in p["checks"]]
    return {"collection": collection, "pages": pages, "revisions": revisions,
            "checks": ordered_checks, "issues": sorted(issues.values(), key=lambda r: r["issue_id"]),
            "snapshot_sha256": digest(pages), "revision_ids": [r["revision_id"] for r in revisions],
            "retracted_issue_ids": sorted(retracted), "issue_conflicts": conflicts}


def _same_review_authority(service: Any, namespace: str, project_id: str,
                           audit_work_order: dict[str, Any], old: dict[str, Any], current: dict[str, Any]) -> bool:
    return old == current or (old.get("authority_root_id") == current.get("authority_root_id") and
        service._verified_authority_binding_at_generation(namespace, project_id, audit_work_order,
                                                          old.get("generation")) == old)


def validate_rebind_snapshot(service: Any, namespace: str, project_id: str, *,
                             storyboard: dict[str, Any], artifact: dict[str, Any],
                             audit_work_order: dict[str, Any], handoff: dict[str, Any],
                             fingerprints: dict[str, str], submission: dict[str, Any]) -> dict[str, Any]:
    """Read-only preflight shared by intake minting and collection rebinding."""
    page = SemanticAuditCoveragePageInput.model_validate(submission["coverage_page"]).model_dump()
    if page["action"] != "rebind" or submission.get("coverage") is not None:
        raise ValueError("audit_coverage_rebind_request_required")
    manifest = build_obligations(storyboard, coverage_context(service))
    if (page["manifest_sha256"] != manifest["manifest_sha256"] or
            submission.get("canonical_sha256") != manifest["canonical_sha256"]):
        raise ValueError("audit_coverage_manifest_stale: fetch current audit_obligations")
    authority = service._active_authority_binding(namespace, project_id, audit_work_order)
    binding = {"namespace_sha256": stable_json_hash(namespace), "project_id": project_id,
        "artifact_id": artifact["artifact_id"], "artifact_sha256": artifact["fingerprint"],
        "work_order_id": audit_work_order["work_order_id"], "handoff_id": handoff["handoff_id"],
        "authority": authority, "fingerprints": fingerprints,
        "manifest_sha256": manifest["manifest_sha256"], "contract": PAGE_CONTRACT}
    snapshot = collection_snapshot(service, namespace, project_id, page["collection_id"])
    old = snapshot["collection"]["binding"]
    stable_scope = lambda b: {k: v for k, v in b.items() if k not in {
        "authority", "handoff_id", "fingerprints", "manifest_sha256"}}
    runtime_keys = {"parser", "schema", "runtime", "rules", "validator", "scripts",
                    "dependencies_environment", "target_model_contract"}
    content = lambda b: {k: v for k, v in b.get("fingerprints", {}).items() if k not in runtime_keys}
    if (stable_scope(old) != stable_scope(binding) or content(old) != content(binding) or
            not _same_review_authority(service, namespace, project_id, audit_work_order,
                                       old.get("authority") or {}, authority)):
        raise ValueError("audit_coverage_rebind_content_or_authority_changed")
    if old.get("fingerprints") == fingerprints and old.get("manifest_sha256") == manifest["manifest_sha256"]:
        raise ValueError("audit_coverage_rebind_requires_changed_rules: use revise for current evidence")
    if (page["expected_snapshot_sha256"] != snapshot["snapshot_sha256"] or
            page["page_ids"] != [r["page_id"] for r in snapshot["pages"]]):
        raise ValueError("audit_coverage_snapshot_conflict: reread audit_coverage before rebinding")
    return {"snapshot": snapshot, "manifest": manifest, "binding": binding}


def coverage_progress(service: Any, namespace: str, project_id: str,
                      artifact_id: str | None = None) -> dict[str, Any]:
    pending, stale, reports, completed_count = [], [], [], 0
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
            report_state = collection_report_state(service, namespace, project_id, storyboard, collection)
            completed = report_state.get("completed_current_review", False)
        except (ValueError, KeyError):
            completed, report_state = False, {}
        if report_state.get("reported"):
            reports.append({"collection_id": collection["collection_id"], "artifact_id": binding.get("artifact_id"), **report_state})
        if not completed:
            if report_state.get("reported"):
                continue
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
            "reported_current_count": len(reports), "reported_collections": reports,
            "reported_unknown_count": sum(r["status"] == "reported_unknown" for r in reports),
            "passed_current_count": sum(r["audit_pass"] for r in reports),
            "counts_as_complete": False, "meaning": "Staged reviews are not completed independent audits."}


def collection_has_signed_audit(service: Any, namespace: str, project_id: str,
                                storyboard: dict[str, Any], collection: dict[str, Any]) -> bool:
    """An immutable collection is complete only through its exact certificate."""
    return bool(collection_report_state(service, namespace, project_id, storyboard, collection).get("completed_current_review"))


def collection_report_state(service: Any, namespace: str, project_id: str,
                             storyboard: dict[str, Any], collection: dict[str, Any]) -> dict[str, Any]:
    store, binding = service.store, collection["binding"]
    snapshot = collection_snapshot(service, namespace, project_id, collection["collection_id"])
    if snapshot["issue_conflicts"]:
        return {"reported": False, "completed_current_review": False, "audit_pass": False}
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
                proof.get("page_ids") != [r["page_id"] for r in snapshot["pages"]] or
                proof.get("checks_sha256") != stable_json_hash(snapshot["checks"]) or
                (proof.get("snapshot_sha256") is not None and proof["snapshot_sha256"] != snapshot["snapshot_sha256"]) or
                (snapshot["revision_ids"] and (proof.get("revision_ids") != snapshot["revision_ids"] or
                                               proof.get("snapshot_sha256") != snapshot["snapshot_sha256"])) or
                proof.get("checks_sha256") != (cert.get("semantic_obligation_coverage") or {}).get("checks_sha256")):
            continue
        try:
            receipt = verify_certificate_coverage(service, storyboard, cert)
        except (ValueError, KeyError, TypeError):
            continue
        metrics = (cert.get("report") or {}).get("quality_metrics") or {}
        unknown_checks = any(row.get("status") == "unknown" for row in metrics.get("semantic_checks", []))
        completion = metrics.get("review_completion") or {}
        complete = bool(receipt["complete"] and not unknown_checks and not completion.get("unknown_shot_count"))
        passed = complete and cert.get("result") == "pass"
        return {"reported": True, "certificate_id": cert.get("certificate_id"),
                "completed_current_review": complete, "audit_pass": passed,
                "status": "reported_pass" if passed else "reported_fail" if complete else "reported_unknown",
                "unresolved_obligation_count": len(receipt.get("unresolved_obligation_ids", [])),
                "formal_delivery_ready": False}
    return {"reported": False, "completed_current_review": False, "audit_pass": False}


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
            if (stable_keys(recorded) != stable_keys(binding) or not _same_review_authority(
                    service, namespace, project_id, audit_work_order, old_authority, authority)):
                raise ValueError("audit_coverage_collection_binding_mismatch")
            # A signed continuation keeps the immutable collection and older
            # evidence; it does not rewrite its original actor or manifest.
            binding = copy.deepcopy(recorded)
        return value

    if page["action"] == "rebind":
        with store.project_write_lock(namespace, project_id):
            snapshot = validate_rebind_snapshot(service, namespace, project_id, storyboard=storyboard,
                artifact=artifact, audit_work_order=audit_work_order, handoff=handoff,
                fingerprints=fingerprints, submission=submission)["snapshot"]
            parent = {"collection_id": collection_id, "snapshot_sha256": snapshot["snapshot_sha256"],
                      "revision_ids": snapshot["revision_ids"], "reason": page["reason"]}
            child_id = "ACOL-" + stable_json_hash({"binding": binding, "parent": parent})[:40]
            existing = None
            for prior in store.list_project_records_strict(namespace, project_id, kind=COLLECTION_KIND):
                if prior["collection_id"] == child_id:
                    existing = prior
                elif (prior.get("binding") == binding and
                      not collection_has_signed_audit(service, namespace, project_id, storyboard, prior)):
                    raise ValueError("audit_coverage_collection_already_pending")
            if existing is None:
                store.save_project_record(namespace, project_id, kind=COLLECTION_KIND, record_id=child_id,
                    value={"collection_id": child_id, "binding": binding, "parent": parent,
                           "status": "coverage_pending", "expected_count": manifest["obligation_count"]})
            child = collection_snapshot(service, namespace, project_id, child_id)
        return ToolResponse(status="coverage_pending", summary="已显式绑定当前规则｜历史证据保留，当前检查须重新复核",
            project_id=project_id, artifact_id=artifact["artifact_id"], work_order_id=audit_work_order["work_order_id"],
            data={"contract": PAGE_CONTRACT, "collection_id": child_id, "parent": parent,
                  "snapshot_sha256": child["snapshot_sha256"], "revision_ids": child["revision_ids"],
                  "accepted_count": len(child["checks"]), "expected_count": manifest["obligation_count"],
                  "accepted_page_ids": [r["page_id"] for r in child["pages"]],
                  "next_offset": len(child["checks"]), "counts_as_complete": False,
                  "audit_pass": False, "formal_delivery_ready": False, "idempotent_replay": existing is not None})

    if page["action"] == "revise":
        with store.project_write_lock(namespace, project_id):
            current_collection()
            snapshot = collection_snapshot(service, namespace, project_id, collection_id)
            request = {k: page[k] for k in ("expected_snapshot_sha256", "page_ids", "reason", "checks",
                                           "issue_upserts", "retract_issue_ids")}
            revision_id = "ACR-" + stable_json_hash({"collection_id": collection_id, **request})[:40]
            existing = next((r for r in snapshot["revisions"] if r["revision_id"] == revision_id), None)
            if existing is None:
                if (page["expected_snapshot_sha256"] != snapshot["snapshot_sha256"] or
                        page["page_ids"] != [r["page_id"] for r in snapshot["pages"]]):
                    raise ValueError("audit_coverage_snapshot_conflict: reread audit_coverage before revising")
                checks = {r["obligation_id"]: r for r in snapshot["checks"]}
                issues = {r["issue_id"]: r for r in snapshot["issues"]}
                if len({r["obligation_id"] for r in page["checks"]}) != len(page["checks"]):
                    raise ValueError("audit_coverage_duplicate_obligation")
                for check in page["checks"]:
                    if check["obligation_id"] not in checks:
                        raise ValueError("audit_coverage_revision_check_not_staged")
                    checks[check["obligation_id"]] = check
                for identity in page["retract_issue_ids"]:
                    if identity not in issues:
                        raise ValueError("audit_coverage_revision_issue_not_staged")
                    del issues[identity]
                issues.update({r["issue_id"]: r for r in page["issue_upserts"]})
                # The outer envelope must explicitly agree with the effective
                # issues. A revision cannot smuggle an unrelated omission.
                if {r["issue_id"]: r for r in submission.get("issues", [])} != issues:
                    raise ValueError("audit_coverage_revision_effective_issues_mismatch")
                revised = {**submission, "coverage": {"contract": CONTRACT,
                    "manifest_sha256": manifest["manifest_sha256"], "checks": list(checks.values())}}
                if checks:
                    validate_coverage(storyboard, revised, context, _page_ids=list(checks), allow_unresolved=True)
                record = {"revision_id": revision_id, "collection_id": collection_id, "binding": binding,
                           "revision_index": len(snapshot["revisions"]) + 1,
                           "previous_revision_id": snapshot["revision_ids"][-1] if snapshot["revision_ids"] else None,
                           **request, "accepting_authority": authority}
                size = sum(len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
                           for r in snapshot["pages"] + snapshot["revisions"] + [record])
                if len(snapshot["revisions"]) >= 512 or size > MAX_ENVELOPE_BYTES:
                    raise ValueError("audit_coverage_collection_limit_exceeded")
                persisted = store.save_project_record(namespace, project_id, kind=REVISION_KIND, record_id=revision_id, value=record)
                snapshot = collection_snapshot(service, namespace, project_id, collection_id)
            else:
                persisted = existing
        return ToolResponse(status="coverage_pending", summary="审计更正已签名追加｜旧页保留，仍须完成正式审计",
            project_id=project_id, artifact_id=artifact["artifact_id"], work_order_id=audit_work_order["work_order_id"],
            data={"contract": PAGE_CONTRACT, "collection_id": collection_id, "revision_id": revision_id,
                  "revision_integrity_hmac": persisted["integrity_hmac"], "snapshot_sha256": snapshot["snapshot_sha256"],
                  "revision_ids": snapshot["revision_ids"], "accepted_count": len(snapshot["checks"]),
                  "expected_count": manifest["obligation_count"], "counts_as_complete": False,
                  "audit_pass": False, "formal_delivery_ready": False, "idempotent_replay": existing is not None})

    if page["action"] == "append":
        rows = manifest["obligations"][page["offset"]:page["offset"]+len(page["checks"])]
        expected = [row["obligation_id"] for row in rows]
        actual = [row["obligation_id"] for row in page["checks"]]
        if not expected or actual != expected:
            raise ValueError("audit_coverage_page_range_mismatch")
        partial = {**submission, "coverage": {"contract": CONTRACT,
                    "manifest_sha256": manifest["manifest_sha256"], "checks": page["checks"]}}
        validate_coverage(storyboard, partial, context, _page_ids=expected, allow_unresolved=True)
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
                    current_collection()
                except ValueError as exc:
                    if not str(exc).startswith("unknown audit_coverage_collections record_id"):
                        raise
                    for previous in store.list_project_records_strict(namespace, project_id, kind=COLLECTION_KIND):
                        stable_scope = lambda data: {k: v for k, v in data.items() if k != "authority"}
                        if stable_scope(previous.get("binding") or {}) == stable_scope(binding) and not collection_has_signed_audit(service, namespace, project_id, storyboard, previous):
                            raise ValueError("audit_coverage_collection_already_pending: a new key cannot discard staged findings")
                    store.save_project_record(namespace, project_id, kind=COLLECTION_KIND,
                        record_id=collection_id, value={"collection_id": collection_id, "binding": binding,
                            "collection_key_sha256": stable_json_hash(page["collection_key"]),
                            "status": "coverage_pending", "expected_count": manifest["obligation_count"]})
            else:
                current_collection()
            value["binding"] = binding
            snapshot = collection_snapshot(service, namespace, project_id, collection_id)
            accepted = snapshot["pages"]
            existing = next((item for item in accepted if item["page_id"] == page_id), None)
            if existing is not None:
                if existing.get("revision_head"):
                    value["revision_head"] = existing["revision_head"]
                if any(existing.get(key) != item for key, item in value.items()):
                    raise ValueError("audit_coverage_page_immutable_conflict")
                persisted = existing
            else:
                if snapshot["issue_conflicts"]:
                    raise ValueError("audit_coverage_page_issue_conflict: resolve signed legacy conflicts with revise")
                effective_issues = {r["issue_id"]: r for r in snapshot["issues"]}
                for issue in bound_issues:
                    identity = issue["issue_id"]
                    if (identity in snapshot["retracted_issue_ids"] or
                            identity in effective_issues and effective_issues[identity] != issue):
                        raise ValueError("audit_coverage_page_issue_conflict: use signed revise")
                if snapshot["revision_ids"]:
                    value["revision_head"] = snapshot["revision_ids"][-1]
                present = {row["obligation_id"] for item in accepted for row in item["checks"]}
                if present & set(actual):
                    raise ValueError("audit_coverage_page_overlap")
                staged_bytes = sum(len(json.dumps(item, ensure_ascii=False).encode("utf-8")) for item in accepted + snapshot["revisions"])
                if len(accepted) >= 512 or staged_bytes + len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_ENVELOPE_BYTES:
                    raise ValueError("audit_coverage_collection_limit_exceeded")
                persisted = store.save_project_record(namespace, project_id, kind=PAGE_KIND,
                                                       record_id=page_id, value={**value, "accepting_authority": authority})
                accepted.append(persisted)
            snapshot = collection_snapshot(service, namespace, project_id, collection_id)
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
                  "snapshot_sha256": snapshot["snapshot_sha256"], "revision_ids": snapshot["revision_ids"],
                  "accepted_page_ids": [item["page_id"] for item in accepted],
                  "next_offset": contiguous_end if contiguous_end < manifest["obligation_count"] else None,
                  "counts_as_complete": False, "formal_delivery_ready": False,
                  "audit_pass": False, "idempotent_replay": existing is not None})

    # Explicit IDs are required. Never select an implicit latest collection or
    # let the client hide a previously staged failing page from final review.
    with store.project_write_lock(namespace, project_id):
        current_collection()
        snapshot = collection_snapshot(service, namespace, project_id, collection_id)
        if snapshot["issue_conflicts"]:
            raise ValueError("audit_coverage_page_issue_conflict: resolve signed legacy conflicts with revise")
        accepted = snapshot["pages"]
        if page["page_ids"] != [item["page_id"] for item in accepted]:
            raise ValueError("audit_coverage_finalize_page_set_mismatch")
        checks, offset = snapshot["checks"], 0
        expected_issues = {r["issue_id"]: r for r in snapshot["issues"]}
        for item in accepted:
            if item["offset"] != offset or item["count"] != len(item["checks"]):
                raise ValueError("audit_coverage_finalize_range_incomplete")
            offset += item["count"]
        final_issues = {row["issue_id"]: row for row in submission.get("issues", [])}
        if (any(final_issues.get(key) != value for key, value in expected_issues.items()) or
                set(final_issues) & set(snapshot["retracted_issue_ids"])):
            raise ValueError("audit_coverage_finalize_issue_mismatch")
        result = copy.deepcopy(submission)
        result["coverage_page"] = None
        result["coverage"] = {"contract": CONTRACT, "manifest_sha256": manifest["manifest_sha256"], "checks": checks}
        validate_coverage(storyboard, result, context, allow_unresolved=True)
        result["_server_coverage_page_receipt"] = {
            "collection_id": collection_id, "page_ids": list(page["page_ids"]),
            "binding": binding, "manifest_sha256": manifest["manifest_sha256"],
            "checks_sha256": stable_json_hash(checks),
            "snapshot_sha256": snapshot["snapshot_sha256"], "revision_ids": snapshot["revision_ids"],
            "finalizing_authority": authority,
        }
    return result
