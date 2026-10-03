"""Recover an external intake with a new event, never fabricate a lost one."""
from __future__ import annotations

import copy
import uuid
from typing import Any

from .audit_coverage import validate_coverage
from .audit_coverage_pages import collection_snapshot
from .canonical import stable_json_hash
from .models import ToolResponse

PHASE = "audit_intake_recovery"
ENVIRONMENT_PHASE = "audit_environment_recovery"
CONFIRMATION = "REVALIDATE_EXTERNAL_INTAKE_PRESERVE_HISTORY"
ENVIRONMENT_CONFIRMATION = "REVALIDATE_STALE_ENVIRONMENT_PRESERVE_HISTORY"
CONTRACT = "external-intake-recovery-v1"
CONTENT_KEYS = ("source_bytes", "normalized_source", "episode_boundary", "artifact_raw",
                "artifact_semantic", "canonical", "project_anchors")


def _signed(service: Any, namespace: str, project: str, kind: str, identity: str) -> dict:
    value = service.store.get_project_record(namespace, project, kind=kind, record_id=identity)
    if not service.store.project_record_is_signed(value):
        raise ValueError("audit_intake_recovery_signature_required")
    return value


def _historical_lineage(service: Any, namespace: str, project: str, root: dict,
                        latest: dict, artifact: dict, audit_order: dict, ledger: dict, hub: dict) -> set[str]:
    """Verify signed history without treating an old runtime as current."""
    routes = [row for row in service.store.list_project_records_strict(namespace, project, kind="handoffs")
              if row.get("reaudit_root_handoff_id") == root["handoff_id"]]
    routes.sort(key=lambda row: int(row.get("reaudit_sequence") or 0))
    ordered = [root, *routes]
    if ordered[-1]["handoff_id"] != latest["handoff_id"]:
        raise ValueError("audit_intake_recovery_current_route_not_latest")
    previous_cert = previous_event = None
    for sequence, row in enumerate(ordered):
        if (not service.store.project_record_is_signed(row) or row.get("status") != "consumed"
                or row.get("fingerprints") != root.get("fingerprints")
                or service._reaudit_immutable_identity(row) != service._reaudit_immutable_identity(root)):
            raise ValueError("audit_intake_recovery_historical_lineage_mismatch")
        cert = service._load_verified_independent_audit_certificate(namespace, project,
            certificate_id=str(row.get("audit_certificate_id") or ""), artifact_id=artifact["artifact_id"],
            canonical_sha256=root["canonical_sha256"], source_artifact_raw_sha256=artifact["fingerprint"])
        events = [event for event in ledger.get("events", []) if event.get("event_id") == cert.get("event_id")]
        if len(events) != 1:
            raise ValueError("audit_intake_recovery_historical_event_required")
        event = events[0]
        if (cert.get("handoff_id") != row["handoff_id"] or cert.get("work_order_id") != audit_order["work_order_id"]
                or cert.get("production_stage_trace_id") != root["stage_trace_id"]
                or cert.get("production_trace_id") != row["stage_trace_id"]
                or event.get("handoff_id") != row["handoff_id"]
                or event.get("stage_trace_id") != cert.get("stage_trace_id")
                or event.get("window_key") != audit_order["work_order_id"]
                or event.get("canonical_fingerprint") != root["canonical_sha256"]
                or event.get("semantic_audit_sha256") != cert["report"].get("semantic_audit_sha256")
                or ("event_sha256" in cert and cert["event_sha256"] != stable_json_hash(event))):
            raise ValueError("audit_intake_recovery_historical_certificate_event_mismatch")
        if sequence:
            parent = ordered[sequence-1]
            service._verified_reaudit_route_authority(namespace, project, route=row, audit_work_order=audit_order, hub=hub)
            if (row.get("reaudit_kind") != "independent_audit_successor" or row.get("server_minted_lineage") is not True
                    or row.get("reaudit_sequence") != sequence or row.get("reaudit_parent_handoff_id") != parent["handoff_id"]
                    or row.get("previous_audit_certificate_id") != previous_cert["certificate_id"]
                    or row.get("previous_audit_certificate_hmac") != previous_cert["integrity_hmac"]
                    or row.get("previous_audit_event_sha256") != stable_json_hash(previous_event)
                    or row.get("reaudit_lineage_sha256") != stable_json_hash(
                        service._reaudit_lineage_payload(root=root, parent=parent, successor=row))
                    or row.get("reaudit_event_trace_id") != cert.get("stage_trace_id")
                    or row.get("reaudit_route_trace_id") != row["stage_trace_id"]
                    or [h for h in hub.get("handoffs", []) if h.get("handoff_id") == row["handoff_id"]]
                        != [service._handoff_record_projection(row)]):
                raise ValueError("audit_intake_recovery_historical_parent_bridge_mismatch")
            trace = _signed(service, namespace, project, "telemetry", row["stage_trace_id"])
            if (trace.get("operation") != "audit_storyboard_reaudit_route"
                    or trace.get("artifact_id") != artifact["artifact_id"]
                    or trace.get("input_fingerprints") != row["fingerprints"]):
                raise ValueError("audit_intake_recovery_historical_route_trace_mismatch")
        previous_cert, previous_event = cert, event
    return {row["handoff_id"] for row in ordered}


def _prepare(service: Any, namespace: str, project: str, handoff_id: str, *, phase: str = PHASE) -> dict:
    from .service import canonical_storyboard_sha256, _find_work_order, sha256_bytes

    old = _signed(service, namespace, project, "handoffs", handoff_id)
    if (old.get("handoff_type") != "audit_intake" or old.get("audit_class") != "audit_intake"
            or old.get("from_window_role") != "external_input" or old.get("status") != "consumed"
            or old.get("reaudit_kind") or (old.get("audit_intake_recovery_id") and phase == PHASE)):
        raise ValueError("audit_intake_recovery_external_consumed_root_required")
    if old.get("audit_intake_recovery_id"):
        assert_recovery_published(service, namespace, project, old)
    missing_id = str(old.get("stage_trace_id") or "")
    if not missing_id:
        raise ValueError("audit_intake_recovery_missing_reference_required")
    if phase == ENVIRONMENT_PHASE:
        service._assert_audit_trace_references(namespace, project, handoff=old,
            artifact_id=str(old.get("storyboard_artifact_id") or ""))
    else:
        try:
            service.store.get_project_record(namespace, project, kind="telemetry", record_id=missing_id)
        except ValueError as exc:
            if str(exc) != f"unknown telemetry record_id: {missing_id}":
                raise
        else:
            raise ValueError("audit_intake_recovery_trace_already_present")
    artifact_id = str(old.get("storyboard_artifact_id") or "")
    storyboard, artifact = service._storyboard_from_artifact(namespace, project, artifact_id,
        persist_recovered_canonical=False, require_bound_projection=True)
    _, path = service.store.get_artifact(namespace, project, artifact_id)
    if sha256_bytes(path.read_bytes()) != artifact.get("fingerprint"):
        raise ValueError("audit_intake_recovery_artifact_bytes_changed")
    canonical = canonical_storyboard_sha256(storyboard)
    source = service._source_fingerprint(namespace, project, storyboard, artifact.get("source_artifact_id"), artifact)
    current = service._fingerprint_bundle(storyboard, artifact, canonical_hash=canonical,
        source_fingerprint=source, boundary_hash=service._boundary_hash(storyboard))
    if canonical != old.get("canonical_sha256") or any(
            current.get(key) != (old.get("fingerprints") or {}).get(key) for key in CONTENT_KEYS):
        raise ValueError("audit_intake_recovery_content_changed")
    # A consumed root retains its first certificate; later audits have their
    # own signed successor. The unchanged artifact names the current authority.
    certificate_id = str(artifact.get("metadata", {}).get("independent_audit_certificate_id") or "")
    if not certificate_id:
        raise ValueError("audit_intake_recovery_latest_certificate_mismatch")
    cert = service._load_verified_independent_audit_certificate(namespace, project,
        certificate_id=certificate_id, artifact_id=artifact_id, canonical_sha256=canonical,
        source_artifact_raw_sha256=current["artifact_raw"])
    runtime_certificate = service._matching_runtime_certificate(namespace, project)
    if (phase == ENVIRONMENT_PHASE and old.get("fingerprints") == current
            and (cert.get("cache_components") or {}).get("runtime_test_certificate_id")
                == (runtime_certificate or {}).get("certificate_id")):
        raise ValueError("audit_environment_recovery_profile_unchanged")
    if cert.get("production_stage_trace_id") != missing_id:
        raise ValueError("audit_intake_recovery_root_trace_mismatch")
    route = _signed(service, namespace, project, "handoffs", str(cert.get("handoff_id") or ""))
    if (route["handoff_id"] != handoff_id and route.get("reaudit_root_handoff_id") != handoff_id):
        raise ValueError("audit_intake_recovery_route_root_mismatch")
    if (route.get("status") != "consumed" or route.get("audit_certificate_id") != certificate_id
            or route.get("latest_audit_certificate_id", certificate_id) != certificate_id
            or route.get("storyboard_artifact_id") != artifact_id
            or route.get("canonical_sha256") != canonical
            or (route.get("production_stage_trace_id") or route.get("stage_trace_id")) != missing_id
            or route.get("stage_trace_id") != cert.get("production_trace_id")):
        raise ValueError("audit_intake_recovery_current_route_mismatch")
    route_fingerprints = route.get("fingerprints") or {}
    if any(route_fingerprints.get(key) != current.get(key) for key in CONTENT_KEYS):
        raise ValueError("audit_intake_recovery_route_content_mismatch")
    certificate_fingerprints = {key: route_fingerprints.get({"source": "source_bytes", "boundary": "episode_boundary"}.get(key, key))
        for key in ("source", "boundary", "artifact_raw", "artifact_semantic", "canonical", "parser", "schema",
                    "runtime", "rules", "validator", "project_anchors", "target_model_contract")}
    if certificate_fingerprints != cert.get("fingerprints"):
        raise ValueError("audit_intake_recovery_route_fingerprint_mismatch")
    if route["handoff_id"] != handoff_id:
        route_trace = _signed(service, namespace, project, "telemetry", route["stage_trace_id"])
        if (route.get("reaudit_kind") != "independent_audit_successor"
                or route.get("reaudit_route_trace_id") != route["stage_trace_id"]
                or route_trace.get("operation") != "audit_storyboard_reaudit_route"
                or route_trace.get("artifact_id") != artifact_id
                or route_trace.get("input_fingerprints") != route_fingerprints):
            raise ValueError("audit_intake_recovery_current_route_trace_mismatch")
    hub = service.store.load_hub(namespace, project)
    audit_order = _find_work_order(hub, str(cert["work_order_id"]))
    packages = [p for p in service.store.list_project_records_strict(namespace, project, kind="repair_packages")
                if p.get("audit_certificate_id") == certificate_id]
    if len(packages) > 1 or (not packages and (phase == PHASE or cert.get("result") != "pass")):
        raise ValueError("audit_intake_recovery_signed_repair_package_required")
    package = packages[0] if packages else None
    if package is not None and not service.store.project_record_is_signed(package):
        raise ValueError("audit_intake_recovery_signed_repair_package_required")
    if package is not None and package.get("status") not in ({"issued", "consumed"} if phase == ENVIRONMENT_PHASE else {"issued"}):
        raise ValueError("audit_intake_recovery_repair_package_not_issued")
    bindings = {"base_artifact_id": artifact_id, "base_canonical_sha256": canonical,
        "source_production_stage_trace_id": missing_id, "source_audit_handoff_id": route["handoff_id"],
        "source_audit_event_trace_id": cert.get("stage_trace_id"),
        "source_audit_route_trace_id": cert.get("production_trace_id"),
        "audit_event_id": cert.get("event_id"), "fingerprints": route_fingerprints}
    if package is not None and any(package.get(key) != value for key, value in bindings.items()):
        raise ValueError("audit_intake_recovery_repair_package_mismatch")
    audit_trace = _signed(service, namespace, project, "telemetry", str(cert.get("stage_trace_id") or ""))
    if (audit_trace.get("artifact_id") != artifact_id or audit_trace.get("operation") != "audit_storyboard"
            or audit_trace.get("input_fingerprints") != route_fingerprints):
        raise ValueError("audit_intake_recovery_audit_event_trace_mismatch")
    ledger = service.store.load_ledger(namespace, project)
    lineage_ids = _historical_lineage(service, namespace, project, old, route, artifact, audit_order, ledger, hub)
    events = [e for e in ledger.get("events", []) if e.get("event_id") == cert.get("event_id")]
    if len(events) != 1:
        raise ValueError("audit_intake_recovery_audit_event_required")
    event = events[0]
    if (event.get("handoff_id") != route["handoff_id"] or event.get("stage_trace_id") != cert.get("stage_trace_id")
            or event.get("window_key") != cert.get("work_order_id")
            or event.get("canonical_fingerprint") != canonical
            or event.get("semantic_audit_sha256") != cert.get("report", {}).get("semantic_audit_sha256")):
        raise ValueError("audit_intake_recovery_audit_event_mismatch")
    # Validate historical coverage using its signed historical context. Unknown
    # results are retained; complete means all obligations supplied, not PASS.
    report = cert["report"]
    receipt = validate_coverage(storyboard, report.get("semantic_audit") or {},
        {key: cert["fingerprints"][key] for key in ("rules", "validator", "runtime", "schema")},
        allow_unresolved=True)
    if (receipt != cert.get("semantic_obligation_coverage") or
            receipt != report.get("independent_evidence_matrix", {}).get("semantic_obligation_coverage")):
        raise ValueError("audit_intake_recovery_coverage_receipt_mismatch")
    proof = cert.get("audit_coverage_page_receipt")
    snapshot_hash = None
    if proof is not None:
        snapshot = collection_snapshot(service, namespace, project, str(proof.get("collection_id") or ""))
        if (snapshot["issue_conflicts"] or proof != report.get("audit_coverage_page_receipt")
                or proof.get("binding") != snapshot["collection"]["binding"]
                or proof.get("binding", {}).get("handoff_id") not in lineage_ids
                or any(proof.get("binding", {}).get(key) != value for key, value in {
                    "namespace_sha256": stable_json_hash(namespace), "project_id": project,
                    "artifact_id": artifact_id, "artifact_sha256": current["artifact_raw"],
                    "work_order_id": cert["work_order_id"], "fingerprints": route_fingerprints,
                    "manifest_sha256": receipt["manifest_sha256"]}.items())
                or proof.get("checks_sha256") != stable_json_hash(snapshot["checks"])
                or proof.get("checks_sha256") != receipt["checks_sha256"]
                or proof.get("page_ids") != [p["page_id"] for p in snapshot["pages"]]
                or proof.get("snapshot_sha256") != snapshot["snapshot_sha256"]
                or proof.get("revision_ids", []) != snapshot["revision_ids"]):
            raise ValueError("audit_intake_recovery_coverage_snapshot_mismatch")
        snapshot_hash = snapshot["snapshot_sha256"]
    public = {"old_handoff_id": handoff_id, "old_handoff_sha256": stable_json_hash(old),
        ("old_trace_id" if phase == ENVIRONMENT_PHASE else "old_missing_trace_id"): missing_id,
        "artifact_id": artifact_id,
        "raw_sha256": current["artifact_raw"], "canonical_sha256": canonical,
        "certificate_id": certificate_id, "certificate_sha256": stable_json_hash(cert),
        "repair_package_id": package["repair_package_id"] if package else None,
        "repair_package_sha256": stable_json_hash(package) if package else None,
        "audit_event_id": event["event_id"], "audit_event_sha256": stable_json_hash(event),
        "audit_trace_sha256": stable_json_hash(audit_trace), "route_sha256": stable_json_hash(route),
        "coverage_snapshot_sha256": snapshot_hash, "coverage_receipt_sha256": stable_json_hash(receipt),
        "historical_result": cert["result"], "current_fingerprints": current,
        "audit_work_order_id": cert["work_order_id"]}
    return {"public": public, "storyboard": storyboard, "artifact": artifact, "audit_order": audit_order}


def assert_recovery_published(service: Any, namespace: str, project: str, handoff: dict) -> None:
    recovery_id = handoff.get("audit_intake_recovery_id")
    if not recovery_id:
        return
    journal = _signed(service, namespace, project, "handoff_recoveries", recovery_id)
    mapping = next((m for m in journal.get("mappings", []) if m["new_handoff_id"] == handoff["handoff_id"]), None)
    if (journal.get("contract") != CONTRACT or journal.get("status") != "committed" or not mapping
            or mapping.get("new_stage_trace_id") != handoff.get("stage_trace_id")
            or mapping.get("fingerprints") != handoff.get("fingerprints")):
        raise ValueError("audit_intake_recovery_not_committed")


def recover_intake(service: Any, namespace: str, *, project_id: str, work_order_id: str,
                   gap_id: str, handoff_ids: list[str], action: str,
                   expected_plan_sha256: str | None, confirmation: str | None,
                   request_session_id: str | None, phase: str = PHASE) -> ToolResponse:
    from .service import _find_work_order, utc_stamp

    if phase not in {PHASE, ENVIRONMENT_PHASE}:
        raise ValueError("audit_intake_recovery_phase_invalid")
    expected_confirmation = ENVIRONMENT_CONFIRMATION if phase == ENVIRONMENT_PHASE else CONFIRMATION
    store = service.store
    with store.project_write_lock(namespace, project_id):
        hub = store.load_hub(namespace, project_id)
        order = _find_work_order(hub, work_order_id)
        service._assert_handoff_recovery_authority(namespace, order, gap_id=gap_id,
            handoff_ids=handoff_ids, phase=phase, request_session_id=request_session_id)
        service._require_capability_gap(hub, gap_id)
        runtime = service._require_runtime_test_certificate(namespace, project_id)
        prepared = [_prepare(service, namespace, project_id, h, phase=phase) for h in handoff_ids]
        body = {"contract": CONTRACT, "phase": phase, "project_id": project_id,
            "work_order_id": work_order_id, "gap_id": gap_id, "handoff_ids": handoff_ids,
            "runtime_test_certificate_id": runtime["certificate_id"],
            "items": [p["public"] for p in prepared], "expected_confirmation": expected_confirmation,
            "history_records_deleted": 0, "storyboard_artifacts_modified": 0,
            "independent_audit_verdict_issued": False}
        plan_hash = stable_json_hash(body)
        recovery_id = "ARCV-" + plan_hash[:32]
        plan = {**body, "plan_sha256": plan_hash, "recovery_id": recovery_id}
        journals = store.list_project_records_strict(namespace, project_id, kind="handoff_recoveries")
        for journal in journals:
            if (journal.get("contract") == CONTRACT and journal.get("status") in {"executing", "committed"}
                    and set(journal.get("plan", {}).get("handoff_ids", [])) & set(handoff_ids)
                    and journal.get("recovery_id") != recovery_id):
                raise ValueError("audit_intake_recovery_already_started: resume the existing signed plan")
        prior = next((j for j in journals
                      if j.get("recovery_id") == recovery_id), None)
        if prior is not None and (not store.project_record_is_signed(prior) or prior.get("plan") != plan):
            raise ValueError("audit_intake_recovery_journal_conflict")
        if action == "plan":
            if prior is None:
                prior = store.save_project_record(namespace, project_id, kind="handoff_recoveries",
                    record_id=recovery_id, value={"recovery_id": recovery_id, "contract": CONTRACT,
                        "status": "planned", "created_at": utc_stamp(), "plan": plan})
            return ToolResponse(status="audit_intake_recovery_plan_ready", summary="审计入库恢复方案已核验；历史原文与结论保留，当前仍须完整复审",
                project_id=project_id, work_order_id=work_order_id, data={"recovery_plan": plan,
                    "plan_integrity_hmac": prior["integrity_hmac"]})
        if expected_plan_sha256 != plan_hash:
            raise ValueError("audit_intake_recovery_plan_stale")
        if confirmation != expected_confirmation:
            raise ValueError("audit_intake_recovery_confirmation_required")
        if prior is None:
            raise ValueError("audit_intake_recovery_signed_plan_required")
        journal = copy.deepcopy(prior)
        if journal["status"] == "planned":
            journal.update(status="executing", started_at=utc_stamp(), mappings=[{
                "old_handoff_id": p["public"]["old_handoff_id"], "new_handoff_id": "HND-"+uuid.uuid4().hex[:16],
                "recovery_id": recovery_id, "fingerprints": p["public"]["current_fingerprints"]} for p in prepared])
            journal = store.save_project_record(namespace, project_id, kind="handoff_recoveries", record_id=recovery_id, value=journal)
        for prepared_item, mapping in zip(prepared, journal["mappings"], strict=True):
            existing = next((h for h in store.list_project_records_strict(namespace, project_id, kind="handoffs")
                             if h["handoff_id"] == mapping["new_handoff_id"]), None)
            if existing is None:
                existing = service._persist_audit_intake(namespace, project_id,
                    storyboard=prepared_item["storyboard"], record=prepared_item["artifact"],
                    audit_work_order=prepared_item["audit_order"], stage_trace_id="",
                    handoff_id=mapping["new_handoff_id"], recovery_id=recovery_id)
            if (not store.project_record_is_signed(existing) or existing.get("audit_intake_recovery_id") != recovery_id
                    or existing.get("fingerprints") != mapping["fingerprints"]
                    or existing.get("storyboard_artifact_id") != prepared_item["public"]["artifact_id"]):
                raise ValueError("audit_intake_recovery_successor_conflict")
            trace = _signed(service, namespace, project_id, "telemetry", existing["stage_trace_id"])
            if (not trace.get("intake_binding_only") or trace.get("audit_completed") is not False
                    or trace.get("handoff_id") != existing["handoff_id"]
                    or trace.get("input_fingerprints") != mapping["fingerprints"]):
                raise ValueError("audit_intake_recovery_successor_trace_conflict")
            mapping.update(new_stage_trace_id=existing["stage_trace_id"], status="PENDING_AUDIT")
            # Finish interrupted hub publication using exactly the already signed event.
            with store.mutate_project(namespace, project_id) as (live_hub, ledger):
                if not any(h.get("handoff_id") == existing["handoff_id"] for h in live_hub.setdefault("handoffs", [])):
                    live_hub["handoffs"].append(copy.deepcopy(existing))
                if not any(t.get("trace_id") == trace["trace_id"] for t in ledger.setdefault("performance_traces", [])):
                    ledger["performance_traces"].append(copy.deepcopy(trace))
        if journal["status"] != "committed":
            journal.update(status="committed", committed_at=utc_stamp())
            store.save_project_record(namespace, project_id, kind="handoff_recoveries", record_id=recovery_id, value=journal)
        return ToolResponse(status="audit_intake_recovery_completed", summary="新根事件已真实保存；须重新完整独立审计，未生成修订分镜",
            project_id=project_id, work_order_id=work_order_id,
            data={"mappings": journal["mappings"], "recovery_plan": plan, "audit_pass": False,
                "formal_delivery_ready": False, "storyboard_artifacts_modified": 0,
                "next_action": "audit_storyboard with the new explicit handoff and complete current independent review"})
