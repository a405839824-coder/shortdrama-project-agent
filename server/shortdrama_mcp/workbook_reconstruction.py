"""Derive a revision link for explicitly authorized, source-backed legacy rebuilds.

The old workbook is evidence, never a source of production authority. A pure
gate follows current production replay and signed full preflight; a separate
atomic commit reservation binds the source before any generated artifact write.
Neither grants an alternate way to repair an existing generated artifact.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .canonical import stable_json_hash
from .storage import sha256_bytes, utc_stamp
from .workbook_migration import recover_legacy_workbook

CONTRACT = "source-backed-workbook-reconstruction-v1"
_GENERATED_TYPES = {"storyboard", "storyboard_json", "storyboard_json_recovered", "storyboard_audit_overlay"}


def _descendants(store, namespace, project_id, source_artifact_id):
    return [row for row in store.list_artifacts(namespace, project_id, include_uncommitted=True)
            if row.get("artifact_type") in _GENERATED_TYPES
            and row.get("source_artifact_id") == source_artifact_id]


def _check_descendants(store, namespace, project_id, source_artifact_id, full_preflight):
    progress = full_preflight.get("commit_progress") or {}
    resumable_ids = {progress.get("artifact_id"), progress.get("canonical_json_artifact_id")} - {None}
    if any(not (row.get("artifact_id") in resumable_ids
                and row.get("work_order_id") == full_preflight.get("work_order_id")
                and row.get("metadata", {}).get("draft_preflight_id") == full_preflight["draft_preflight_id"]
                and row.get("metadata", {}).get("audit_status") == "PENDING_AUDIT")
           for row in _descendants(store, namespace, project_id, source_artifact_id)):
        raise ValueError("workbook_reconstruction_generated_descendant_requires_repair")


def claim_reconstruction(store, namespace, project_id, proof, *, claim_token):
    """Reserve one source across work orders using the existing signed journal.

    Ownership has no time expiry: after a crash only the exact original full
    preflight may resume. Its ordinary commit claim still fences concurrent
    retries. A handled error releases this reservation only before any child
    artifact exists; even an unpublished child keeps the source bound.
    """
    identity = {key: proof[key] for key in (
        "source_artifact_id", "source_raw_sha256", "work_order_id", "draft_preflight_id", "candidate_sha256")}
    with store.project_write_lock(namespace, project_id):
        preflight = store.get_project_record(namespace, project_id, kind="draft_preflights",
                                             record_id=identity["draft_preflight_id"])
        if (preflight.get("work_order_id") != identity["work_order_id"]
                or preflight.get("candidate_sha256") != identity["candidate_sha256"]
                or preflight.get("scope_kind") != "full" or preflight.get("commit_ready") is not True):
            raise ValueError("workbook_reconstruction_claim_preflight_mismatch")
        draft_claim = preflight.get("commit_claim") or {}
        if (not claim_token or draft_claim.get("claim_token") != claim_token
                or draft_claim.get("state") != "claimed"):
            raise ValueError("workbook_reconstruction_draft_claim_required")
        _check_descendants(store, namespace, project_id, identity["source_artifact_id"], preflight)
        # ponytail: scan the existing signed preflight journal; add an index
        # only if project journal size makes this short CAS measurable.
        for record in store.list_project_records_strict(namespace, project_id, kind="draft_preflights"):
            claim = record.get("workbook_reconstruction_claim")
            if (claim and claim.get("source_artifact_id") == identity["source_artifact_id"]
                    and claim.get("state") != "released"):
                if any(claim.get(key) != value for key, value in identity.items()):
                    raise ValueError("workbook_reconstruction_source_claim_conflict")
        identity["claim_token"] = claim_token
        preflight["workbook_reconstruction_claim"] = {**identity, "state":"reserved", "claimed_at":utc_stamp()}
        store.save_project_record(namespace, project_id, kind="draft_preflights",
                                  record_id=identity["draft_preflight_id"], value=preflight)
    return identity


def release_reconstruction(store, namespace, project_id, identity):
    """Release a failed preparation, never a source with generated history."""
    with store.project_write_lock(namespace, project_id):
        record = store.get_project_record(namespace, project_id, kind="draft_preflights",
                                          record_id=identity["draft_preflight_id"])
        claim = record.get("workbook_reconstruction_claim") or {}
        if (any(claim.get(key) != value for key, value in identity.items())
                or (record.get("commit_claim") or {}).get("claim_token") != identity["claim_token"]):
            return  # A late failed owner cannot release a newer retry's reservation.
        if _descendants(store, namespace, project_id, identity["source_artifact_id"]):
            return
        record["workbook_reconstruction_claim"] = {**claim, "state":"released", "released_at":utc_stamp()}
        store.save_project_record(namespace, project_id, kind="draft_preflights",
                                  record_id=identity["draft_preflight_id"], value=record)


def source_backed_reconstruction(service, namespace: str, project_id: str, *,
                                hub: dict[str, Any], work_order: dict[str, Any],
                                source_artifact_id: str | None, candidate: dict[str, Any],
                                production_route: str, full_preflight: dict[str, Any] | None):
    """Return server-derived lineage, or None for the unchanged normal route."""
    if not source_artifact_id or work_order.get("operation") != "storyboard_regenerate":
        return None
    source, path = service.store.get_artifact(namespace, project_id, source_artifact_id)
    if source.get("artifact_type") != "source:storyboard":
        return None

    def require(condition: bool, message: str):
        if not condition:
            raise ValueError("workbook_reconstruction_" + message)

    from .service import SOURCE_INTAKE_OPERATIONS, canonical_storyboard_sha256
    require(work_order.get("mode") == "execute"
            and work_order.get("authorization") == "explicit_execute"
            and work_order.get("server_actor_role") == "production"
            and not work_order.get("workbook_migration_id"), "production_authority_required")
    require(production_route == "replayed_current_production", "current_production_required")
    require(isinstance(full_preflight, dict)
            and service.store.project_record_is_signed(full_preflight)
            and full_preflight.get("project_id") == project_id
            and full_preflight.get("work_order_id") == work_order.get("work_order_id")
            and full_preflight.get("scope_kind") == "full"
            and full_preflight.get("commit_ready") is True
            and full_preflight.get("candidate_sha256") == canonical_storyboard_sha256(candidate),
            "signed_full_preflight_required")

    metadata = source.get("metadata", {})
    entries = hub.get("project_state", {}).get("authoritative_sources", [])

    def source_entry(record, role):
        matches = [entry for entry in entries if entry.get("artifact_id") == record["artifact_id"]]
        intake = next((order for order in hub.get("work_orders", [])
                       if order.get("work_order_id") == record.get("work_order_id")), {})
        require(len(matches) == 1 and matches[0].get("source_role") == role
                and matches[0].get("fingerprint") == record.get("fingerprint")
                and matches[0].get("work_order_id") == record.get("work_order_id")
                and record.get("metadata", {}).get("source_role") == role
                and intake.get("operation") in SOURCE_INTAKE_OPERATIONS
                and intake.get("mode") == "execute"
                and intake.get("authorization") == "explicit_execute"
                and intake.get("server_actor_role") == "production", "source_authority_mismatch")
        return matches[0]

    old_entry = source_entry(source, "storyboard")
    require(Path(source.get("file_name", "")).suffix.lower() == ".xlsx", "legacy_xlsx_required")
    require(not any(metadata.get(key) for key in (
                "canonical_json_artifact_id", "production_certificate_id", "handoff_id",
                "canonical_sha256", "recovered_without_canonical_sidecar")), "source_already_bound")
    require(not any(source_artifact_id in {row.get("storyboard_artifact_id"), row.get("canonical_artifact_id")}
                    for row in service.store.list_project_records(namespace, project_id, kind="handoffs")),
            "source_already_bound")
    # Once a generated descendant exists, subsequent changes must repair that
    # artifact. Reusing the import cannot discard its failed audit history.
    _check_descendants(service.store, namespace, project_id, source_artifact_id, full_preflight)
    raw = path.read_bytes()
    require(sha256_bytes(raw) == source.get("fingerprint"), "source_bytes_changed")
    _old_candidate, old_proof = recover_legacy_workbook(raw)
    targets = work_order.get("targets")
    required_targets = {source_artifact_id, "source_sha256:" + source["fingerprint"], *old_proof["episode_ids"]}
    require(isinstance(targets, list) and all(isinstance(item, str) for item in targets)
            and len(targets) == len(required_targets) and set(targets) == required_targets,
            "exact_source_hash_and_episode_targets_required")
    shots = candidate.get("shots", [])
    require(bool(shots) and all(isinstance(row, dict) for row in shots), "candidate_episode_scope_mismatch")
    episodes = set()
    for shot in shots:
        match = re.fullmatch(r"(EP\d{2})-\d{3}", str(shot.get("镜头编号") or ""))
        require(match is not None, "candidate_episode_scope_mismatch")
        episode = match[1]
        require(shot.get("episode_id", episode) == episode, "candidate_episode_scope_mismatch")
        episodes.add(episode)
    require(episodes == set(old_proof["episode_ids"]), "candidate_episode_scope_mismatch")

    selected = service._selected_dialogue_source_entry(hub) or {}
    authority_id = selected.get("artifact_id")
    require(bool(authority_id) and work_order.get("source_lock", {}).get("authoritative_source") == authority_id,
            "locked_novel_or_script_required")
    authority, authority_path = service.store.get_artifact(namespace, project_id, authority_id)
    role = str(authority.get("artifact_type") or "").removeprefix("source:")
    require(authority.get("artifact_type") in {"source:novel", "source:script"}, "locked_novel_or_script_required")
    authority_entry = source_entry(authority, role)
    require(selected.get("fingerprint") == authority.get("fingerprint")
            and sha256_bytes(authority_path.read_bytes()) == authority.get("fingerprint"),
            "source_authority_mismatch")
    return {"contract":CONTRACT, "work_order_id":work_order["work_order_id"],
            "authorizing_request_sha256":stable_json_hash(work_order.get("request_text")),
            "source_artifact_id":source_artifact_id, "source_raw_sha256":source["fingerprint"],
            "source_entry_sha256":stable_json_hash(old_entry), "source_proof":old_proof,
            "dialogue_authority_artifact_id":authority_id, "dialogue_authority_role":role,
            "dialogue_authority_sha256":authority["fingerprint"],
            "dialogue_authority_entry_sha256":stable_json_hash(authority_entry),
            "candidate_sha256":canonical_storyboard_sha256(candidate),
            "draft_preflight_id":full_preflight["draft_preflight_id"],
            "draft_preflight_record_sha256":stable_json_hash(full_preflight),
            "candidate_classification":"new_generation_from_authoritative_source",
            "legacy_first_draft_correctness_percent":None, "original_preserved":True,
            "independent_audit_status":"PENDING_AUDIT"}
