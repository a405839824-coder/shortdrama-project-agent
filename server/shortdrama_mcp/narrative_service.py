"""Source-bound prose planning, immutable attempts and independent review.

These receipts establish what was checked and by which server authority. They
do not establish that a model's semantic judgement is infallible.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .canonical import canonical_json_bytes
from .source_dialogue import extracted_source_text
from .acceptance_service import controlled_acceptance_operation
from .models import ToolResponse
from .storage import sha256_bytes, slug_filename, utc_stamp

NARRATIVE_OPERATIONS = frozenset({
    "script_create", "script_rewrite", "script_change", "dialogue_rewrite",
})
NARRATIVE_TYPES = frozenset({"script", "dialogue"})
RECORD_KIND = "narrative_records"


def digest(value: Any) -> str:
    # Source and manuscript character spans are exact: do not NFC-normalize or
    # rewrite line endings as the storyboard canonical serializer does.
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8"))


class NarrativeGuardMixin:
    @staticmethod
    def _invalidate_narrative_current(hub, order, reason):
        current_id = order.get("narrative_current_artifact_id")
        order.pop("verified_narrative_review_id", None)
        for deliverable in hub.get("deliverables", []):
            if current_id and deliverable.get("deliverable_id") == current_id:
                deliverable.setdefault("prior_statuses", []).append({
                    "status": deliverable["status"], "timestamp": utc_stamp(), "reason": reason})
                deliverable["status"] = "invalidated"
                latest = hub.get("project_state", {}).get("latest_artifacts", {})
                pointer = latest.get(deliverable["artifact_type"], {})
                if pointer.get("deliverable_id") == current_id:
                    latest.pop(deliverable["artifact_type"], None)
        if current_id:
            old_status = order["status"]
            order["status"] = "failed_current"
            order["validation"]["status"] = "fail"
            order.setdefault("state_history", []).append({"timestamp": utc_stamp(),
                "from": old_status, "to": "failed_current", "reason": reason})

    def _narrative_records(self, namespace, project_id):
        return sorted(self.store.list_project_records_strict(namespace, project_id, kind=RECORD_KIND),
                      key=lambda record: record["sequence"])

    def _narrative_record(self, namespace, project_id, record_id, record_type=None):
        record = self.store.get_project_record(
            namespace, project_id, kind=RECORD_KIND, record_id=record_id)
        if record_type and record.get("record_type") != record_type:
            raise ValueError("narrative_record_type_mismatch")
        return record

    def _save_narrative_record(self, namespace, project_id, record_type, **values):
        record_id = "NAR-" + uuid4().hex[:20]
        records = self._narrative_records(namespace, project_id)
        value = {**values, "record_type": record_type, "record_id": record_id,
                 "sequence": max((r["sequence"] for r in records), default=0) + 1,
                 "created_at": utc_stamp()}
        return self.store.save_project_record(namespace, project_id, kind=RECORD_KIND,
                                             record_id=record_id, value=value)

    def _narrative_order(self, namespace, project_id, work_order_id, session, *, audit=False):
        from .service import _find_work_order
        hub = self.store.load_hub(namespace, project_id)
        order = _find_work_order(hub, work_order_id)
        expected = {"narrative_audit"} if audit else NARRATIVE_OPERATIONS
        if (order.get("operation") not in expected
                or order.get("server_actor_role") != ("independent_audit" if audit else "production")
                or order.get("mode") != ("audit_only" if audit else "execute")
                or order.get("authorization") != ("read_only" if audit else "explicit_execute")):
            raise ValueError("narrative_authority_required")
        self._assert_work_order_session(namespace, order, session)
        return hub, order

    def _narrative_source(self, namespace, project_id, hub, plan):
        source_id = plan.get("source_artifact_id")
        extracted, record, _path = self._load_extracted_or_payload(namespace, project_id, source_id)
        role = record.get("metadata", {}).get("source_role")
        current = hub.get("project_state", {}).get("latest_artifacts", {}).get("source:" + str(role), {})
        if (role not in {"novel", "script"} or current.get("artifact_id") != source_id
                or record.get("fingerprint") != plan.get("source_sha256")):
            raise ValueError("narrative_source_stale_or_not_authoritative")
        return extracted_source_text(extracted)

    def _current_narrative_plan(self, namespace, project_id, hub, record_id):
        from .service import _find_work_order
        record = self._narrative_record(namespace, project_id, record_id, "plan")
        if record.get("service_code_fingerprint") != self._service_code_fingerprint:
            raise ValueError("narrative_checker_changed: preflight and review under the current rules")
        order = _find_work_order(hub, record["work_order_id"])
        if (order.get("narrative_plan_id") != record_id
                or digest(order.get("request_text")) != record["request_sha256"]):
            raise ValueError("narrative_plan_superseded_or_request_changed")
        latest = hub.get("narrative_plan_heads", {}).get(record["lineage_id"])
        if latest != record_id:
            raise ValueError("narrative_baseline_superseded: recheck dependent plans and prose")
        text = self._narrative_source(namespace, project_id, hub, record["plan"])
        if sha256_bytes(text.encode("utf-8")) != record["source_text_sha256"]:
            raise ValueError("narrative_source_extraction_changed")
        return record

    @controlled_acceptance_operation
    def preflight_narrative_plan(self, namespace, *, project_id, work_order_id,
                                 plan, request_session_id=None):
        from .narrative_contract import validate_plan
        from .service import _find_work_order
        self._require_runtime()
        with self.store.project_write_lock(namespace, project_id):
            hub, order = self._narrative_order(namespace, project_id, work_order_id, request_session_id)
            if order.get("status") not in {"approved", "scope_locked", "executing", "failed_current", "validating"}:
                raise ValueError("narrative_plan_work_order_not_writable")
            try:
                source_text = self._narrative_source(namespace, project_id, hub, plan)
                checked = validate_plan(plan, source_text, order["request_text"])
            except ValueError as exc:
                failed = self._save_narrative_record(namespace, project_id, "plan_failure",
                    work_order_id=work_order_id, input_snapshot=plan, error=str(exc))
                return ToolResponse(status="blocked", summary="剧情计划未通过｜尚未生产正文",
                    project_id=project_id, work_order_id=work_order_id,
                    data={"record_id": failed["record_id"], "error": str(exc), "manuscript_accuracy": None})
            normalized = checked["normalized"]
            lineage_id = digest({"source_artifact_id": normalized["source_artifact_id"],
                                 "source_scope": normalized["source_scope"]})
            record = self._save_narrative_record(namespace, project_id, "plan",
                work_order_id=work_order_id, plan=normalized, check=checked,
                plan_sha256=digest(normalized), lineage_id=lineage_id,
                service_code_fingerprint=self._service_code_fingerprint,
                source_text_sha256=sha256_bytes(source_text.encode("utf-8")),
                request_sha256=digest(order["request_text"]),
                producer_binding=self._active_authority_binding(namespace, project_id, order))
            with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
                previous_plan_id = hub.get("narrative_plan_heads", {}).get(lineage_id)
                for affected in hub.get("work_orders", []):
                    if previous_plan_id and affected.get("narrative_plan_id") == previous_plan_id:
                        self._invalidate_narrative_current(hub, affected, "narrative_baseline_superseded")
                order = _find_work_order(hub, work_order_id)
                order["narrative_plan_id"] = record["record_id"]
                order.pop("verified_narrative_review_id", None)
                hub.setdefault("narrative_plan_heads", {})[lineage_id] = record["record_id"]
            return ToolResponse(status="plan_checked_pending_review",
                summary="剧情计划结构检查通过｜原文映射及语义待独立核对",
                project_id=project_id, work_order_id=work_order_id,
                data={"plan_id": record["record_id"], "plan_sha256": record["plan_sha256"],
                      "check": checked, "manuscript_accuracy": None})

    def _narrative_review_for(self, namespace, project_id, target_id, *, require_clean=True):
        reviews = [r for r in self._narrative_records(namespace, project_id)
                   if r["record_type"] == "review" and r["target_id"] == target_id]
        review = max(reviews, key=lambda r: r["sequence"], default=None)
        if review is None or (require_clean and review.get("clean") is not True):
            raise ValueError("narrative_independent_review_required")
        return review

    @controlled_acceptance_operation
    def record_narrative_attempt(self, namespace, *, project_id, work_order_id,
                                 outcome, reason, request_session_id=None):
        self._require_runtime()
        if outcome not in {"refused", "incomplete", "failed"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError("narrative_attempt_outcome_or_reason_invalid")
        with self.store.project_write_lock(namespace, project_id):
            _hub, order = self._narrative_order(namespace, project_id, work_order_id, request_session_id)
            record = self._save_narrative_record(namespace, project_id, "attempt",
                work_order_id=work_order_id, plan_id=order.get("narrative_plan_id"),
                outcome=outcome, reason=reason, complete_candidate=False,
                candidate_snapshot=None, manuscript_accuracy=None)
        return ToolResponse(status="attempt_recorded", summary="未完成尝试已留档｜不计作合格稿",
            project_id=project_id, work_order_id=work_order_id, data={"record_id": record["record_id"]})

    def _commit_narrative_artifact(self, namespace, *, project_id, work_order_id,
            artifact_type, output_format, file_name, content_text, structured_data,
            source_artifact_id, change_summary, narrative_candidate, request_session_id):
        from .narrative_contract import validate_candidate
        from .service import _find_work_order, _artifact_version, project_agent
        # The caller was already checked by the generic artifact authority gate.
        with self.store.project_write_lock(namespace, project_id):
            hub, order = self._narrative_order(namespace, project_id, work_order_id, request_session_id)
            records = self._narrative_records(namespace, project_id)
            raw = {"narrative_candidate": narrative_candidate, "content_text": content_text,
                   "structured_data": structured_data, "output_format": output_format}
            if len(canonical_json_bytes(raw)) > 10 * 1024 * 1024:
                raise ValueError("artifact payload exceeds 10 MiB")
            plan_record = None
            checked = None
            rendered = None
            error = None
            try:
                if not isinstance(narrative_candidate, dict):
                    raise ValueError("narrative_candidate_required: preflight plan before writing prose")
                plan_record = self._current_narrative_plan(namespace, project_id, hub, order.get("narrative_plan_id", ""))
                if narrative_candidate.get("plan_id") != plan_record["record_id"]:
                    raise ValueError("narrative_candidate_plan_mismatch")
                plan_review = self._narrative_review_for(namespace, project_id, plan_record["record_id"], require_clean=False)
                requested_scenes = set(narrative_candidate["candidate"].get("scope_scene_ids", []))
                if not requested_scenes or not requested_scenes.issubset(set(plan_review.get("clean_scene_ids", []))):
                    raise ValueError("narrative_plan_scope_review_unresolved")
                if source_artifact_id != plan_record["plan"]["source_artifact_id"]:
                    raise ValueError("narrative_candidate_source_mismatch")
                checked = validate_candidate(plan_record["plan"], narrative_candidate["candidate"])
                self._check_narrative_repair(namespace, project_id, plan_record, narrative_candidate, records)
                rendered = self._render_narrative_candidate(checked["normalized"])
                if structured_data is not None or content_text not in {None, "", rendered} or output_format not in {"text", "markdown"}:
                    raise ValueError("narrative_body_mismatch: server renders only checked scene text")
            except (ValueError, KeyError, TypeError) as exc:
                error = str(exc)
            lineage = plan_record["lineage_id"] if plan_record else None
            complete_candidate = bool(content_text or (isinstance(narrative_candidate, dict)
                and isinstance(narrative_candidate.get("candidate"), dict)
                and narrative_candidate["candidate"].get("scenes")))
            prior = [r for r in records if r["record_type"] == "attempt" and r.get("complete_candidate")
                     and ((lineage and r.get("lineage_id") == lineage)
                          or r.get("work_order_id") == work_order_id)]
            record = self._save_narrative_record(namespace, project_id, "attempt",
                work_order_id=work_order_id, plan_id=order.get("narrative_plan_id"),
                lineage_id=lineage, raw_submission=raw, complete_candidate=complete_candidate,
                comparable_candidate=bool(checked),
                candidate_snapshot=checked["normalized"] if checked else (narrative_candidate.get("candidate") if isinstance(narrative_candidate, dict) else None),
                outcome="blocked" if error else "structure_checked_pending_review", error=error,
                first_candidate_id=prior[0].get("first_candidate_id") or prior[0]["record_id"] if prior else None,
                candidate_ordinal=len(prior) + 1, manuscript_accuracy=None,
                producer_binding=self._active_authority_binding(namespace, project_id, order))
            if error:
                return ToolResponse(status="blocked", summary="正文候选已留档但未放行｜待修正依据或内容",
                    project_id=project_id, work_order_id=work_order_id,
                    data={"attempt_id": record["record_id"], "error": error,
                          "candidate_ordinal": record["candidate_ordinal"], "manuscript_accuracy": None})
            version = _artifact_version(self.store.list_artifacts(namespace, project_id), artifact_type)
            extension = ".md" if output_format == "markdown" else ".txt"
            clean_name = slug_filename(file_name or f"{artifact_type}_{version}{extension}")
            if not clean_name.endswith(extension):
                clean_name += extension
            artifact = self.store.save_artifact_bytes(namespace, project_id, rendered.encode("utf-8"),
                file_name=clean_name, artifact_type=artifact_type, version=version,
                work_order_id=work_order_id, source_artifact_id=source_artifact_id,
                metadata={"change_summary": change_summary, "narrative_attempt_id": record["record_id"],
                          "narrative_plan_id": plan_record["record_id"]})
            publication = self._save_narrative_record(namespace, project_id, "publication",
                attempt_id=record["record_id"], work_order_id=work_order_id,
                artifact_id=artifact["artifact_id"], artifact_sha256=artifact["fingerprint"],
                plan_id=plan_record["record_id"])
            with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
                order = _find_work_order(hub, work_order_id)
                if order["status"] != "validating":
                    self._start_execution(hub, order)
                order.pop("verified_narrative_review_id", None)
                order["validation"]["status"] = "pending"
                order["narrative_current_artifact_id"] = artifact["artifact_id"]
                order["narrative_publication_id"] = publication["record_id"]
                project_agent.record_deliverable(hub, {
                    "deliverable_id": artifact["artifact_id"], "work_order_id": work_order_id,
                    "timestamp": utc_stamp(), "artifact_type": artifact_type,
                    "file_name": artifact["file_name"], "version": version,
                    "fingerprint": artifact["fingerprint"], "status": "draft",
                    "source_versions": [source_artifact_id]})
                if order["status"] != "validating":
                    project_agent.transition_work_order(hub, work_order_id, "validating")
            return ToolResponse(status="saved_pending_independent_review", summary="正文已保存｜结构检查通过｜语义待独立审查",
                project_id=project_id, work_order_id=work_order_id, artifact_id=artifact["artifact_id"],
                download_url=self.store.signed_download_url(namespace, project_id, artifact["artifact_id"], artifact["file_name"]),
                data={"artifact": artifact, "attempt_id": record["record_id"],
                      "publication_id": publication["record_id"],
                      "not_included_scene_ids": checked.get("not_included_scene_ids", []),
                      "first_candidate_id": record["first_candidate_id"] or record["record_id"],
                      "candidate_ordinal": record["candidate_ordinal"], "manuscript_accuracy": None})

    @staticmethod
    def _render_narrative_candidate(candidate):
        return "\n\n".join(scene["text"] for scene in candidate["scenes"])

    def _check_narrative_repair(self, namespace, project_id, plan_record, envelope, records):
        # Implemented against the shared dependency closure validator below.
        from .narrative_contract import validate_repair
        prior = [r for r in records if r["record_type"] == "attempt"
                 and r.get("lineage_id") == plan_record["lineage_id"]
                 and r.get("comparable_candidate") is True]
        if not prior:
            if envelope.get("previous_attempt_id") or envelope.get("repair"):
                raise ValueError("narrative_repair_parent_unknown")
            return
        previous = max(prior, key=lambda r: r["sequence"])
        if envelope.get("previous_attempt_id") != previous["record_id"]:
            raise ValueError("narrative_repair_latest_parent_required")
        validate_repair(plan_record["plan"], previous["candidate_snapshot"], envelope["candidate"], envelope.get("repair"))

    @controlled_acceptance_operation
    def review_narrative_artifact(self, namespace, *, project_id, work_order_id,
            target_id, review, request_session_id=None):
        from .narrative_contract import validate_review
        from .service import _find_work_order
        self._require_runtime()
        with self.store.project_write_lock(namespace, project_id):
            hub, audit = self._narrative_order(namespace, project_id, work_order_id, request_session_id, audit=True)
            target = self._narrative_record(namespace, project_id, target_id)
            if target["record_type"] == "plan":
                plan_record = self._current_narrative_plan(namespace, project_id, hub, target_id)
                candidate = None
                producer_id = plan_record["work_order_id"]
                target_sha = plan_record["plan_sha256"]
                binding = plan_record["producer_binding"]
            elif target["record_type"] == "publication":
                plan_record = self._current_narrative_plan(namespace, project_id, hub, target["plan_id"])
                producer_id = target["work_order_id"]
                producer = _find_work_order(hub, producer_id)
                if producer.get("narrative_publication_id") != target_id:
                    raise ValueError("narrative_review_target_superseded")
                artifact, _path = self.store.get_artifact(namespace, project_id, target["artifact_id"])
                if artifact["fingerprint"] != target["artifact_sha256"]:
                    raise ValueError("narrative_artifact_changed")
                attempt = self._narrative_record(namespace, project_id, target["attempt_id"], "attempt")
                candidate = attempt["candidate_snapshot"]
                binding = attempt["producer_binding"]
                target_sha = target["artifact_sha256"]
            else:
                raise ValueError("narrative_review_target_invalid: use plan_id or publication_id")
            producer = _find_work_order(hub, producer_id)
            self._assert_audit_separation(namespace, project_id, audit, {
                "work_order_id": producer_id, "from_window_binding_id": binding.get("window_binding_id"),
                "from_actor_binding_id": binding.get("actor_binding_id"),
                "from_session_fingerprint": binding.get("session_fingerprint")})
            if target_id not in audit.get("targets", []):
                raise ValueError("narrative_review_outside_audit_scope")
            if review.get("target_sha256") != target_sha:
                raise ValueError("narrative_review_exact_version_required")
            previous_reviews = [r for r in self._narrative_records(namespace, project_id)
                                if r["record_type"] == "review" and r["target_id"] == target_id]
            previous_review = max(previous_reviews, key=lambda r: r["sequence"], default=None)
            if previous_review and (review.get("supersedes_review_id") != previous_review["record_id"]
                                    or not str(review.get("change_reason") or "").strip()):
                raise ValueError("narrative_reaudit_history_required: identify the previous review and explain new findings or changed assessment")
            if not previous_review and review.get("supersedes_review_id"):
                raise ValueError("narrative_reaudit_parent_unknown")
            checked = validate_review(plan_record["plan"], candidate, {k: v for k, v in review.items()
                if k not in {"target_sha256", "supersedes_review_id", "change_reason"}})
            clean = checked["clean"]
            record = self._save_narrative_record(namespace, project_id, "review",
                target_id=target_id, target_sha256=target_sha, plan_id=plan_record["record_id"],
                work_order_id=work_order_id, producer_work_order_id=producer_id,
                review=checked["normalized"], clean=clean,
                supersedes_review_id=review.get("supersedes_review_id"), change_reason=review.get("change_reason"),
                reviewer_binding=self._active_authority_binding(namespace, project_id, audit),
                scope=checked.get("covered_scene_ids", []),
                clean_scene_ids=checked.get("clean_scene_ids", []),
                semantic_judgement="independent_reviewer_assessment")
            if candidate is not None or not clean:
                with self.store.mutate_project(namespace, project_id) as (hub, _ledger):
                    producer = _find_work_order(hub, producer_id)
                    producer.pop("verified_narrative_review_id", None)
                    if clean:
                        producer["verified_narrative_review_id"] = record["record_id"]
                    elif not clean:
                        self._invalidate_narrative_current(hub, producer, "narrative_review_reopened:" + record["record_id"])
            return ToolResponse(status="independent_review_pass" if clean else "independent_review_unresolved",
                summary="独立内容审查已记录｜仍须完成验证及用户签核" if clean else "独立内容审查存在未解决项",
                project_id=project_id, work_order_id=work_order_id,
                data={"review_id": record["record_id"], "clean": clean, "coverage": record["scope"],
                      "manuscript_accuracy": None, "semantic_judgement": record["semantic_judgement"]})

    def _verified_narrative_review(self, namespace, project_id, hub, order):
        publication_id = order.get("narrative_publication_id")
        if not publication_id:
            raise ValueError("narrative_independent_review_required: legacy or unbound draft")
        publication = self._narrative_record(namespace, project_id, publication_id, "publication")
        if publication["work_order_id"] != order["work_order_id"]:
            raise ValueError("narrative_review_work_order_mismatch")
        plan = self._current_narrative_plan(namespace, project_id, hub, publication["plan_id"])
        self._narrative_review_for(namespace, project_id, publication["plan_id"])
        attempt = self._narrative_record(namespace, project_id, publication["attempt_id"], "attempt")
        expected_scenes = {scene["id"] for scene in plan["plan"]["scenes"]}
        if set(attempt["candidate_snapshot"]["scope_scene_ids"]) != expected_scenes:
            raise ValueError("narrative_partial_candidate: unchecked scenes cannot count as full-batch pass")
        artifact, _path = self.store.get_artifact(namespace, project_id, publication["artifact_id"])
        review = self._narrative_review_for(namespace, project_id, publication_id)
        if (artifact["fingerprint"] != publication["artifact_sha256"]
                or review["target_sha256"] != artifact["fingerprint"]
                or review["record_id"] != order.get("verified_narrative_review_id")
                or order.get("narrative_current_artifact_id") != artifact["artifact_id"]):
            raise ValueError("narrative_independent_review_stale_or_mismatched")
        return review

    def read_narrative_record(self, namespace, *, project_id, record_id=None, offset=0, limit=20000):
        if offset < 0 or not 1 <= limit <= 20000:
            raise ValueError("narrative_read_range_invalid")
        if record_id == "contract":
            self.store.load_hub(namespace, project_id)
            reference = Path(__file__).resolve().parents[2] / "skills" / "shortdrama-storyboard" / "references" / "narrative-production-constraints.md"
            encoded = reference.read_text(encoding="utf-8")
            data = {"record_id": "contract", "record_type": "workflow_reference", "content": encoded[offset:offset + limit],
                    "total_chars": len(encoded), "reference_sha256": sha256_bytes(encoded.encode("utf-8")),
                    "next_offset": offset + limit if offset + limit < len(encoded) else None}
        elif record_id:
            record = self._narrative_record(namespace, project_id, record_id)
            encoded = json.dumps(record, ensure_ascii=False, indent=2)
            data = {"record_id": record_id, "content": encoded[offset:offset + limit],
                    "total_chars": len(encoded), "next_offset": offset + limit if offset + limit < len(encoded) else None}
        else:
            records = self._narrative_records(namespace, project_id)
            rows = [{k: r[k] for k in ("record_id", "record_type", "sequence", "work_order_id", "outcome",
                     "target_id", "plan_id", "candidate_ordinal", "first_candidate_id") if k in r} for r in records]
            data = {"records": rows[offset:offset + min(limit, 100)], "total_records": len(rows),
                    "next_offset": offset + min(limit, 100) if offset + min(limit, 100) < len(rows) else None,
                    "manuscript_accuracy": None}
        return ToolResponse(status="ok", summary="已读取剧情约束与尝试记录", project_id=project_id, data=data)
