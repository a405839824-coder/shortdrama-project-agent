"""Signed pre-production review of exact source-grounded indirect dialogue.

Uses the existing narrative tools, work-order authority and signed record store.
The independent assessment is evidence, not a guarantee of semantic accuracy.
"""
from __future__ import annotations

import json

from .models import ToolResponse
from .storage import sha256_bytes


class SourceRealizationMixin:
    def _current_source_realization(self, namespace, project_id, hub, proposal_id):
        from source_realizations import digest
        from .service import _find_work_order
        record = self._narrative_record(namespace, project_id, proposal_id, "source_dialogue_proposal")
        plan = record["plan"]
        order = _find_work_order(hub, record["work_order_id"])
        self._active_authority_binding(namespace, project_id, order)
        heads = hub.get("source_realization_heads", {})
        if (record["service_code_fingerprint"] != self._service_code_fingerprint
                or record["request_sha256"] != digest(order.get("request_text"))
                or any(heads.get(digest([plan["source_artifact_id"], u["id"]])) != proposal_id for u in plan["binding"]["uses"])):
            raise ValueError("source_realization_stale: source, proposal, request or checker changed")
        source = self._narrative_source(namespace, project_id, hub, plan)
        if sha256_bytes(source.encode("utf-8")) != record["source_text_sha256"]:
            raise ValueError("source_realization_source_extraction_changed")
        return record

    def _preflight_source_realization(self, namespace, project_id, work_order_id, plan, session):
        from source_realizations import digest, validate_proposal
        hub, order = self._narrative_order(namespace, project_id, work_order_id, session,
                                          operations={"storyboard_plan"})
        if order.get("status") not in {"approved", "scope_locked", "executing", "failed_current", "validating"}:
            raise ValueError("source_realization_work_order_not_writable")
        try:
            source = self._narrative_source(namespace, project_id, hub, plan)
            validate_proposal(plan, source)
        except (ValueError, TypeError, KeyError) as exc:
            record = self._save_narrative_record(namespace, project_id, "source_dialogue_failure",
                work_order_id=work_order_id, input_snapshot=plan, error=str(exc))
            return ToolResponse(status="blocked", summary="对白转译提案未通过｜未授权生成",
                project_id=project_id, work_order_id=work_order_id,
                data={"record_id": record["record_id"], "error": str(exc), "manuscript_accuracy": None})
        record = self._save_narrative_record(namespace, project_id, "source_dialogue_proposal",
            work_order_id=work_order_id, plan=plan, plan_sha256=digest(plan),
            source_text_sha256=sha256_bytes(source.encode("utf-8")), request_sha256=digest(order.get("request_text")),
            service_code_fingerprint=self._service_code_fingerprint,
            producer_binding=self._active_authority_binding(namespace, project_id, order))
        with self.store.mutate_project(namespace, project_id) as (current, _ledger):
            for use in plan["binding"]["uses"]:
                current.setdefault("source_realization_heads", {})[digest([plan["source_artifact_id"], use["id"]])] = record["record_id"]
        return ToolResponse(status="plan_checked_pending_review", summary="对白转译提案已冻结｜具体措辞与说话人待独立核对",
            project_id=project_id, work_order_id=work_order_id,
            data={"plan_id": record["record_id"], "plan_sha256": record["plan_sha256"], "manuscript_accuracy": None})

    def _review_source_realization(self, namespace, project_id, hub, audit, target_id, review):
        from source_realizations import validate_review
        target = self._current_source_realization(namespace, project_id, hub, target_id)
        binding = target["producer_binding"]
        self._assert_audit_separation(namespace, project_id, audit, {
            "work_order_id": target["work_order_id"], "from_window_binding_id": binding.get("window_binding_id"),
            "from_actor_binding_id": binding.get("actor_binding_id"), "from_session_fingerprint": binding.get("session_fingerprint")})
        if target_id not in audit.get("targets", []) or review.get("target_sha256") != target["plan_sha256"]:
            raise ValueError("source_realization_review_scope_or_version")
        previous = max((r for r in self._narrative_records(namespace, project_id)
                        if r["record_type"] == "review" and r["target_id"] == target_id),
                       key=lambda r: r["sequence"], default=None)
        if previous and (review.get("supersedes_review_id") != previous["record_id"] or not str(review.get("change_reason") or "").strip()):
            raise ValueError("narrative_reaudit_history_required")
        if not previous and review.get("supersedes_review_id"):
            raise ValueError("narrative_reaudit_parent_unknown")
        clean = validate_review(target["plan"], review)
        record = self._save_narrative_record(namespace, project_id, "review", target_id=target_id,
            target_sha256=target["plan_sha256"], plan_id=target_id, work_order_id=audit["work_order_id"],
            producer_work_order_id=target["work_order_id"], review=review, clean=clean,
            reviewer_binding=self._active_authority_binding(namespace, project_id, audit),
            service_code_fingerprint=self._service_code_fingerprint, semantic_judgement="independent_reviewer_assessment")
        receipt = {"proposal_id": target_id, "review_id": record["record_id"], "binding": target["plan"]["binding"]} if clean else None
        return ToolResponse(status="independent_review_pass" if clean else "independent_review_unresolved",
            summary="对白转译独审已记录｜完整分镜仍需独审" if clean else "对白转译仍有未解决项｜禁止据此生成",
            project_id=project_id, work_order_id=audit["work_order_id"],
            data={"review_id": record["record_id"], "clean": clean, "dialogue_realization": receipt, "manuscript_accuracy": None})

    def _verify_source_realization_graph(self, namespace, project_id, graph):
        from source_realizations import bound_realizations
        from .service import _find_work_order
        # No cross-request authority cache: a later failed review revokes a
        # previously usable receipt even when canonical bytes are unchanged.
        if not graph.get("dialogue_realizations"):
            return
        bound_realizations(graph)
        hub = self.store.load_hub(namespace, project_id)
        for receipt in graph["dialogue_realizations"]:
            proposal = self._current_source_realization(namespace, project_id, hub, receipt["proposal_id"])
            review = self._narrative_review_for(namespace, project_id, receipt["proposal_id"])
            if (review["record_id"] != receipt["review_id"] or receipt["binding"] != proposal["plan"]["binding"]
                    or review["target_sha256"] != proposal["plan_sha256"]
                    or review.get("service_code_fingerprint") != self._service_code_fingerprint):
                raise ValueError("source_realization_receipt_mismatch_or_superseded")
            audit = _find_work_order(hub, review["work_order_id"])
            self._assert_audit_separation(namespace, project_id, audit, {
                "work_order_id": proposal["work_order_id"],
                "from_window_binding_id": proposal["producer_binding"].get("window_binding_id"),
                "from_actor_binding_id": proposal["producer_binding"].get("actor_binding_id"),
                "from_session_fingerprint": proposal["producer_binding"].get("session_fingerprint")})

    def _verify_storyboard_source_realizations(self, namespace, project_id, candidate):
        evidence = candidate.get("prevention_evidence") or {}
        binding = evidence.get("production_derivation") or {}
        raw = binding.get("source_graph_json") if isinstance(binding, dict) else None
        if not raw:
            return
        graph = json.loads(raw)
        if graph.get("dialogue_realizations"):
            from production_derivation import inspect_derivation
            if inspect_derivation(candidate).get("valid") is not True:
                raise ValueError("source_realization_derivation_invalid")
            self._verify_source_realization_graph(namespace, project_id, graph)
