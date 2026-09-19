"""Describe observed workflow progress without inventing content acceptance.

Runtime readiness, an empty issue ledger and an approved work order are not
delivery proofs. Only the dedicated, exact-file receipt can authorize delivery.
"""
from __future__ import annotations


CONTRACT = "observed-storyboard-workflow-v1"


def observed_workflow(data):
    latest = data.get("latest_storyboard")
    latest = latest if isinstance(latest, dict) else {}
    artifact = latest.get("artifact_id")
    state = {
        "contract": CONTRACT,
        "scope": "server_observed_artifacts_only",
        "registered_storyboard_artifact_id": artifact,
        "registered_file_sha256": latest.get("fingerprint"),
        "formal_delivery_state": "not_verified",
        "counts_as_complete": False,
        "allows_next_batch": False,
        "runtime_pass_is_content_pass": False,
        "zero_recorded_issues_is_content_pass": False,
        "outside_plugin_output_observed": False,
        "delivery_verification": {
            "tool": "read_project_artifact", "section": "formal_delivery",
            "artifact_id": artifact,
            "requires": "current signed receipt for the exact delivered bytes",
        },
    }
    if not artifact:
        state.update(stage="not_submitted", independent_audit_state="not_started",
            label="尚无正式提交分镜；零问题记录不代表已审通过",
            next_action="submit_a_preflighted_candidate_or_migrate_the_imported_workbook")
    elif data.get("independent_audit_pending") or data.get("audit_coverage_pending"):
        state.update(stage="awaiting_audit", independent_audit_state="pending",
            label="已提交，独立审计尚未闭合", next_action="complete_current_independent_audit")
    elif (str(latest.get("effective_audit_status") or "").upper() == "PASS"
          and latest.get("audit_evidence_state") == "verified_independent_certificate"):
        state.update(stage="audit_recorded", independent_audit_state="verified_record",
            label="独审记录已核对，正式交付仍须核对当前文件回执",
            next_action="read_current_formal_delivery_receipt")
    else:
        state.update(stage="audit_unverified", independent_audit_state="not_verified",
            label="已有分镜，尚无当前有效的独审通过证据",
            next_action="resolve_audit_evidence_and_actual_findings")
    if not data.get("state_valid", True):
        state.update(stage="blocked", label="项目控制状态需恢复，不能认定内容完成",
            next_action="repair_project_control_state")
    elif (data.get("draft_preflight_repair_pending") or
          (isinstance(data.get("production_quality_block"), dict)
           and data["production_quality_block"].get("active"))):
        state.update(stage="blocked", label="当前预检或质量问题待修正，不能认定内容完成",
            next_action="repair_current_causes_and_recheck_dependencies")
    return state
