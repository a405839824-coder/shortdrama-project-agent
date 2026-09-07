"""Non-blocking, model-independent advice attached after an audit completes.

This module never changes a report, verdict, certificate, work order or ledger.
The policy and effort labels live in the authoritative rule registry.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_policy() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parent.parent / "references" / "rule_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    return registry["runtime"]["audit_repair_mode_advice"], registry["plugin_version"]


def supplement_required(reason: str) -> dict[str, Any]:
    return {
        "title": "本批修正模式建议",
        "status": "supplement_required",
        "recommended_effort": None,
        "recommendation": "待补充建议",
        "reasons": [reason],
        "scope": {"shot_ids": [], "locations": []},
        "groups": [],
        "escalation_conditions": ["依据现有报告补齐问题类型、修复依据和影响范围后再建议。"],
        "advisory_only": True,
        "affects_audit_verdict": False,
        "next_action": "只补充报告建议；不因建议缺失重审、改判或要求重复授权。",
    }


def build_repair_mode_advice(
    report: dict[str, Any],
    *,
    audit_status: str,
    repair_package: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    if policy is None:
        policy, policy_version = load_policy()
    policy_hash = hashlib.sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = supplement_required("审计尚未形成可判定的完整问题清单。")
    result.update({
        "policy_contract": policy["contract"],
        "policy_version": policy_version,
        "policy_sha256": policy_hash,
        "model_context": policy["model_context"],
        "selection_note": policy["selection_note"],
        "basis": "按本批审计证据作出的工作量建议，未经模式准确率对比实测。",
    })
    issues = report.get("issues")
    if not isinstance(issues, list) or any(not isinstance(row, dict) for row in issues):
        return result
    # Informational calibration notices do not create content repairs.
    active = [row for row in issues if row.get("blocking") is True or
              str(row.get("severity") or "fail").lower() not in {"info", "warn", "warning"}]
    if not active:
        if audit_status != "pass":
            return result
        result.update({
            "status": "no_repair",
            "recommendation": "无需修正",
            "reasons": ["本次审计通过，未列出待修内容问题；非阻塞提示仍按原报告保留。"],
            "escalation_conditions": [],
            "next_action": "按已授权流程继续；无需为修正选择思考模式。",
        })
        return result

    targets = (repair_package or {}).get("issue_targets") or []
    tiers = policy["tiers"]
    criteria = policy["criteria"]
    grouped: dict[str, dict[str, Any]] = {}
    for issue in active:
        code = str(issue.get("error_code") or "")
        identity = str(issue.get("semantic_issue_id") or issue.get("issue_id") or code)
        location = str(issue.get("location") or "")
        shot_ids = {str(s) for s in issue.get("shot_ids", [])}
        for target in targets:
            if target.get("issue_id") == identity and target.get("location") == location:
                shot_ids.add(str(target["shot_id"]))
        action = str(issue.get("repair_action") or "")
        issue_type = str(issue.get("issue_type") or "")
        if issue_type in criteria["deep_issue_types"] or any(
            term in action for term in criteria["deep_repair_terms"]
        ):
            tier = "deep"
            reason = "需裁决规则冲突或定位反复修复的根因，先统一依据和修复方案。"
        elif len(shot_ids) > 1 or any(code.startswith(p) for p in criteria["coupled_code_prefixes"]) or any(
            term in action for term in criteria["coupled_repair_terms"]
        ):
            tier = "coupled"
            reason = "问题涉及跨镜依赖、源文覆盖或结构调整，需要联合核对影响范围。"
        elif code in criteria["mechanical_codes"] and action and (shot_ids or location):
            tier = "mechanical"
            reason = "问题位置和修复动作明确，可按现有权威字段或原文逐项修正。"
        else:
            tier = "local"
            reason = "需结合原文及本镜语义判断具体改法，现有证据未指明结构性联动。"
        group = grouped.setdefault(tier, {
            "tier": tier,
            "recommended_effort": tiers[tier]["effort"],
            "recommendation": tiers[tier]["label"],
            "alternative_efforts": tiers[tier]["alternatives"],
            "reasons": [reason],
            "shot_ids": [], "locations": [], "issue_ids": [],
            "escalation_conditions": tiers[tier]["escalation_conditions"],
        })
        group["shot_ids"] = sorted(set(group["shot_ids"]) | shot_ids)
        group["locations"] = sorted(set(group["locations"]) | ({location} if location else set()))
        group["issue_ids"] = sorted(set(group["issue_ids"]) | {identity})
    groups = sorted(grouped.values(), key=lambda g: tiers[g["tier"]]["rank"])
    overall = groups[-1]
    result.update({
        "status": "recommended",
        "recommended_effort": overall["recommended_effort"],
        "recommendation": overall["recommendation"],
        "alternative_efforts": overall["alternative_efforts"],
        "reasons": overall["reasons"] + ["整批建议取依赖最复杂的问题组；独立的简单问题可使用较低模式。"],
        "scope": {
            "shot_ids": sorted({s for g in groups for s in g["shot_ids"]}),
            "locations": sorted({s for g in groups for s in g["locations"]}),
        },
        "groups": groups,
        "escalation_conditions": overall["escalation_conditions"],
        "next_action": "按用户已授权范围修正并执行原有复核；用户可自行选择其他可用模式。",
    })
    return result
