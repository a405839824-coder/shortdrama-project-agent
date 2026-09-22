"""Explicitly authorized workbook migration; normal production gates stay intact."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .canonical import COMPACT_SHOT_FIELDS, stable_json_hash
from .field_content import field_content_findings
from .materializer import context_instruction_text
from .models import ToolResponse
from .storage import utc_stamp
from .workbook_migration import (
    WORKBOOK_MIGRATION_PHASE, WORKBOOK_MIGRATION_TARGET,
    recover_legacy_workbook, shot_atoms,
)


def _migration_field_precheck(candidate):
    findings = [{"shot_id":shot["镜头编号"], **finding}
                for shot in candidate["shots"]
                for finding in field_content_findings(shot, mask=context_instruction_text)]
    return {"status":"blocked" if findings else "pass",
            "scope":"current_field_content_contract_only", "findings":findings,
            "affected_shot_ids":list(dict.fromkeys(row["shot_id"] for row in findings)),
            "content_accuracy_verified":False}


class WorkbookMigrationMixin:
    def _workbook_migration_plan(self, namespace: str, project_id: str,
                                 work_order_id: str, request_session_id: str | None):
        from . import service as runtime
        hub, ledger = self._require_current_project_state(namespace, project_id)
        order = runtime._find_work_order(hub, work_order_id)
        self._assert_handoff_recovery_work_order(namespace, order, request_session_id=request_session_id)
        targets = order.get("targets", [])
        if not isinstance(targets, list) or len(targets) != len(set(targets)):
            raise ValueError("workbook_migration_scope_invalid")
        source_ids = [item for item in targets if item.startswith("ART-")]
        gap_ids = [item for item in targets if item.startswith("GAP-")]
        hashes = [item.removeprefix("source_sha256:") for item in targets if item.startswith("source_sha256:")]
        if len(source_ids) != 1 or len(gap_ids) != 1 or len(hashes) != 1:
            raise ValueError("workbook_migration_scope_invalid: require one source artifact, gap and SHA")
        gap = self._require_capability_gap(hub, gap_ids[0])
        gap_order = runtime._find_work_order(hub, str(gap.get("work_order_id") or ""))
        if gap_order.get("operation") != "capability_gap" or source_ids[0] not in gap_order.get("targets", []):
            raise ValueError("workbook_migration_gap_scope_mismatch")
        source, path = self.store.get_artifact(namespace, project_id, source_ids[0])
        metadata = source.get("metadata", {})
        entries = [entry for entry in hub.get("project_state", {}).get("authoritative_sources", [])
                   if entry.get("artifact_id") == source_ids[0]]
        intake = runtime._find_work_order(hub, str(source.get("work_order_id") or ""))
        if (source.get("artifact_type") != "source:storyboard" or metadata.get("source_role") != "storyboard"
                or Path(source.get("file_name", "")).suffix.lower() != ".xlsx"
                or source.get("fingerprint") != hashes[0]
                or len(entries) != 1 or entries[0].get("fingerprint") != hashes[0]
                or entries[0].get("source_role") != "storyboard"
                or entries[0].get("work_order_id") != source.get("work_order_id")
                or intake.get("operation") not in runtime.SOURCE_INTAKE_OPERATIONS
                or intake.get("mode") != "execute" or intake.get("authorization") != "explicit_execute"
                or intake.get("server_actor_role") != "production"):
            raise ValueError("workbook_migration_source_authority_mismatch")
        if (metadata.get("production_certificate_id") or metadata.get("handoff_id")
                or (metadata.get("canonical_json_artifact_id") and not metadata.get("recovered_without_canonical_sidecar"))
                or any(row.get("storyboard_artifact_id") == source_ids[0]
                       for row in self.store.list_project_records(namespace, project_id, kind="handoffs"))):
            raise ValueError("workbook_migration_source_already_bound")
        canonical, proof = recover_legacy_workbook(path.read_bytes())
        required = {source_ids[0], gap_ids[0], "source_sha256:" + hashes[0],
                    "controlled_phase:" + WORKBOOK_MIGRATION_PHASE, *proof["episode_ids"]}
        if set(targets) != required:
            raise ValueError("workbook_migration_scope_mismatch: exact source, gap, hash, phase and episodes required")
        prepared, _identity = runtime._prepare_compact_storyboard_for_commit(canonical)
        transformations = []
        for original, projected in zip(shot_atoms(canonical), shot_atoms(prepared)):
            for field in COMPACT_SHOT_FIELDS:
                if original[field] != projected[field]:
                    transformations.append({"shot_id":original["镜头编号"], "field":field,
                                            "before":original[field], "after":projected[field]})
        # Lossless migration cannot repair content while retaining its atom
        # hash. Detect this before minting a work order that can never pass.
        field_precheck = _migration_field_precheck(prepared)
        certificate = self._require_runtime_test_certificate(namespace, project_id)
        plan = {"contract":WORKBOOK_MIGRATION_TARGET, "project_id":project_id,
                "authorizing_work_order_id":work_order_id, "authorizing_request_sha256":stable_json_hash(order["request_text"]),
                "gap_id":gap_ids[0], "gap_sha256":stable_json_hash(gap),
                "source_artifact_id":source_ids[0], "source_raw_sha256":hashes[0],
                "source_entry_sha256":stable_json_hash(entries[0]), "proof":proof,
                "prepared_atom_sha256":stable_json_hash(shot_atoms(prepared)),
                "transformations":transformations, "policy_sha256":stable_json_hash(hub["policy"]),
                "field_content_precheck":field_precheck,
                "runtime_certificate_key":self._runtime_certificate_key(),
                "runtime_certificate_id":certificate["certificate_id"],
                "ruleset_version":self._ruleset_version, "validator_version":self._validator_version(),
                "parser_implementation_sha256":stable_json_hash(
                    Path(__file__).with_name("workbook_migration.py").read_text()),
                "ordinary_production_pending_audit_gate_unchanged":True,
                "independent_audit_required":True}
        return plan, canonical, hub, ledger

    def migrate_workbook_canonical(self, namespace: str, *, project_id: str, work_order_id: str,
                                   action: str, expected_plan_sha256: str | None,
                                   migration_id: str | None, request_session_id: str | None) -> ToolResponse:
        from . import service as runtime
        if action not in {"plan", "execute"}:
            raise ValueError("workbook_migration_action_invalid: plan or execute only; source is immutable")
        # Serialize this route separately: normal record/Hub writers take the
        # non-reentrant project lock themselves. Recheck the plan under that
        # lock immediately before creating the production authority.
        with self.store.lock(f"{namespace}-{project_id}-workbook-migration"):
            plan, _canonical, hub, ledger = self._workbook_migration_plan(
                namespace, project_id, work_order_id, request_session_id)
            plan_sha = stable_json_hash(plan)
            identifier = "WMG-" + plan_sha[:20]
            if plan["field_content_precheck"]["status"] == "blocked":
                source = runtime.StoryboardService._selected_dialogue_source_entry(hub)
                return ToolResponse(status="workbook_migration_requires_reconstruction",
                    summary="原表已保留｜字段内容不支持保真迁移｜需有来源的重建，未创建生产工单",
                    project_id=project_id, work_order_id=work_order_id,
                    data={"migration_plan":plan, "plan_sha256":plan_sha,
                          "artifact_created":False, "certificate_created":False,
                          "production_work_order_created":False, "original_preserved":True,
                          "reconstruction_guidance":{
                              "status":"required_inputs_pending", "authorization_granted":False,
                              "source_artifact_id":plan["source_artifact_id"],
                              "source_raw_sha256":plan["source_raw_sha256"],
                              "dialogue_authority_artifact_id":(source or {}).get("artifact_id"),
                              "required_inputs":[
                                  "当前会话对受影响字段重建的明确授权；仅保真迁移授权不代表可改内容",
                                  "服务端已导入的权威小说/剧本及逐字对白；旧分镜文件本身不自动成为对白权威",
                                  "受影响镜头可核实的场景、参照物显示名、人物/物体状态及机位；未知位置不得猜成左中右",
                                  "与原文和本句匹配的字段决定、当前production-decisions和冻结source graph/production plan"],
                              "next_action":"prepare_source_backed_storyboard_regenerate_after_required_inputs",
                              "begin_project_task_template":{
                                  "project_id":project_id,
                                  "operation":"storyboard_regenerate", "mode":"execute",
                                  "authorization":"explicit_execute",
                                  "request_text":"<填写当前已获用户授权的重建范围，不得沿用仅保真迁移授权>",
                                  "targets":[plan["source_artifact_id"], "source_sha256:" + plan["source_raw_sha256"],
                                             *plan["proof"]["episode_ids"]],
                                  "authoritative_source":(source or {}).get("artifact_id") or "<先导入并锁定原小说/剧本>",
                                  "latest_version":plan["source_raw_sha256"],
                                  "target_boundary":"、".join(plan["proof"]["episode_ids"]),
                                  "must_keep":["原工作簿及其哈希不变", "原文对白与未授权内容"],
                                  "must_remove":[], "protected_scope":["未经本次授权的原内容"],
                                  "expected_outputs":["有来源的新分镜候选", "全量预检及独立审计"]},
                              "commit_storyboard_template":{
                                  "project_id":project_id, "work_order_id":"<新重建工单ID>",
                                  "source_artifact_id":plan["source_artifact_id"],
                                  "draft_preflight_id":"<同候选逐场景覆盖后签发的full预检ID>",
                                  "storyboard":"<当前production-decisions及冻结plan实际编译的完整候选>"},
                              "route_limits":[
                                  "此建议未签发新工单或修正权限，不能用删掉对白保护字段来绕过来源检查",
                                  "普通重建仍须当前生产证明与原表完整分集；服务端核实工单、原表哈希及full预检后推导新修订，客户端不得自行声明revision",
                                  "已绑定canonical、正式证书、handoff或正式后代的原表不得借此绕过既有修复链"]}})
            if action == "plan":
                return ToolResponse(status="workbook_migration_plan_ready", summary="母本迁移预演完成｜原件未改｜待执行计划",
                                    project_id=project_id, work_order_id=work_order_id,
                                    data={"migration_id":identifier, "plan_sha256":plan_sha, "migration_plan":plan,
                                          "artifact_created":False, "certificate_created":False})
            if expected_plan_sha256 != plan_sha or migration_id != identifier:
                raise ValueError("workbook_migration_plan_cas_mismatch: re-plan against current source and runtime")
            records = [row for row in self.store.list_project_records(namespace, project_id, kind="workbook_migrations")
                       if row.get("workbook_migration_id") == identifier]
            if len(records) > 1:
                raise ValueError("workbook_migration_record_conflict")
            if records:
                record = records[0]
                if (not self.store.project_record_is_signed(record) or record.get("plan_sha256") != plan_sha
                        or record.get("plan") != plan or record.get("status") not in {"prepared", "authorized"}):
                    raise ValueError("workbook_migration_record_conflict")
                request = record["production_request"]
            else:
                if self._draft_preflight_failure_state(ledger)["blocked"]:
                    raise ValueError("draft_preflight_repair_pending: existing failed draft must be resolved")
                block = hub.get("project_state", {}).get("production_quality_block")
                if isinstance(block, dict) and block.get("active"):
                    raise ValueError("first_draft_quality_blocked")
                order = runtime._find_work_order(hub, work_order_id)
                request = {"work_order_id":"WO-" + plan_sha[:16], "timestamp":utc_stamp(),
                           "window_key":"controlled-workbook-migration",
                           "request_text":"Execute approved lossless workbook migration " + identifier,
                           "operation":"storyboard_regenerate", "mode":"execute", "authorization":"explicit_execute",
                           "targets":list(plan["proof"]["episode_ids"]), "source_lock":copy.deepcopy(order["source_lock"]),
                           "protected_scope":copy.deepcopy(order["protected_scope"]),
                           "expected_outputs":["bound canonical", "PENDING_AUDIT workbook and handoff"],
                           "server_window_binding_id":runtime._new_id("WIN"), "server_actor_binding_id":runtime._new_id("ACT"),
                           "server_actor_role":"production", "transport_session_fingerprint":self._session_fingerprint(namespace, request_session_id),
                           "window_access_contract":runtime.WINDOW_NEUTRAL_ACCESS_CONTRACT,
                           "window_access_scope":"authenticated_namespace", "workbook_migration_id":identifier,
                           "workbook_migration_plan_sha256":plan_sha}
                if any(row.get("work_order_id") == request["work_order_id"] for row in hub["work_orders"]):
                    raise ValueError("workbook_migration_work_order_collision")
                record = self.store.save_project_record(namespace, project_id, kind="workbook_migrations", record_id=identifier,
                         value={"workbook_migration_id":identifier, "plan_sha256":plan_sha, "plan":plan,
                                "status":"prepared", "production_request":request})
            with self.store.mutate_project(namespace, project_id) as (live_hub, live_ledger):
                live_plan, _parsed, _hub, _ledger = self._workbook_migration_plan(
                    namespace, project_id, work_order_id, request_session_id)
                if live_plan != plan:
                    raise ValueError("workbook_migration_plan_cas_mismatch: source or authorization changed")
                existing = next((row for row in live_hub["work_orders"] if row["work_order_id"] == request["work_order_id"]), None)
                if existing is not None:
                    if any(existing.get(key) != value for key, value in request.items()):
                        raise ValueError("workbook_migration_work_order_conflict")
                else:
                    if record["status"] != "prepared":
                        raise ValueError("workbook_migration_authorized_work_order_missing")
                    if self._draft_preflight_failure_state(live_ledger)["blocked"]:
                        raise ValueError("draft_preflight_repair_pending: existing failed draft must be resolved")
                    block = live_hub.get("project_state", {}).get("production_quality_block")
                    if isinstance(block, dict) and block.get("active"):
                        raise ValueError("first_draft_quality_blocked")
                    runtime.project_agent.create_work_order(live_hub, request)
                    live_hub["project_state"]["current_stage"] = runtime._find_work_order(live_hub, request["work_order_id"])["status"]
            if record["status"] == "prepared":
                record = self.store.save_project_record(namespace, project_id, kind="workbook_migrations", record_id=identifier,
                         value={**record, "status":"authorized", "authorized_at":utc_stamp()})
            return ToolResponse(status="workbook_migration_authorized", summary="母本迁移身份已建立｜待逐场景与全量预检｜未签发审计结论",
                                project_id=project_id, work_order_id=request["work_order_id"],
                                data={"migration_id":identifier, "plan_sha256":plan_sha,
                                      "source_artifact_id":plan["source_artifact_id"], "proof":plan["proof"],
                                      "artifact_created":False, "certificate_created":False,
                                      "next_action":"scene_preflights_then_full_preflight_then_full_commit"})

    def _validated_workbook_migration(self, namespace: str, project_id: str,
                                      work_order: dict[str, Any], request_session_id: str | None):
        identifier = work_order.get("workbook_migration_id")
        if not identifier:
            if work_order.get("workbook_migration_plan_sha256"):
                raise ValueError("workbook_migration_binding_incomplete")
            return None
        record = self.store.get_project_record(namespace, project_id, kind="workbook_migrations", record_id=str(identifier))
        if not self.store.project_record_is_signed(record) or record.get("status") != "authorized":
            raise ValueError("workbook_migration_not_authorized")
        request = record["production_request"]
        if any(work_order.get(key) != value for key, value in request.items()):
            raise ValueError("workbook_migration_work_order_conflict")
        plan, _canonical, _hub, _ledger = self._workbook_migration_plan(
            namespace, project_id, record["plan"]["authorizing_work_order_id"], request_session_id)
        if plan != record["plan"] or stable_json_hash(plan) != record["plan_sha256"]:
            raise ValueError("workbook_migration_binding_stale")
        return record

    @staticmethod
    def _assert_workbook_migration_atoms(record: dict[str, Any] | None, candidate: dict[str, Any]) -> None:
        if record is None:
            return
        plan = record["plan"]
        if stable_json_hash(shot_atoms(candidate)) not in {plan["proof"]["atom_sha256"], plan["prepared_atom_sha256"]}:
            raise ValueError("workbook_migration_semantic_change_forbidden: preserve all source shots and 27 fields")
