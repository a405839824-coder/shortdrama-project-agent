from __future__ import annotations

import re
from typing import Any

from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES
from shortdrama_mcp.service import canonical_storyboard_sha256


def semantic_audit_for_artifact(
    service: Any,
    settings: Any,
    project_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Build an explicit clean semantic-review fixture for existing tests."""

    storyboard, _record = service._storyboard_from_artifact(
        request_namespace(settings), project_id, artifact_id
    )
    canonical_sha256 = canonical_storyboard_sha256(storyboard)
    shots = [row for row in storyboard.get("shots", []) if isinstance(row, dict)]
    batches = [row for row in storyboard.get("batches", []) if isinstance(row, dict)]
    if shots:
        shot_id = str(shots[0].get("镜头编号") or shots[0].get("shot_uid"))
        evidence_id = f"shot:{shot_id}"
    elif batches:
        batch_id = str(
            batches[0].get("批次编号") or batches[0].get("batch_uid")
        )
        evidence_id = f"batch:{batch_id}"
    else:
        evidence_id = f"canonical:{canonical_sha256}"
    return {
        "canonical_sha256": canonical_sha256,
        "reviewed_shot_count": len(shots),
        "reviewed_batch_count": len(batches),
        "layers": [
            {
                "layer": layer,
                "status": "pass",
                "summary": f"{layer}已独立复核并绑定具体实体证据。",
                "evidence_ids": [evidence_id],
            }
            for layer in SEMANTIC_AUDIT_LAYER_NAMES
        ],
        "issues": [],
        "conclusion": {"status": "pass", "summary": "八层语义复核完整且未发现问题。"},
    }


def failing_semantic_audit_for_artifact(
    service: Any,
    settings: Any,
    project_id: str,
    artifact_id: str,
    *,
    shot_id: str,
    field: str,
    error_code: str,
    layer: str = "正式母版单镜执行完整性",
    issue_id: str = "shots[1].semantic_contract_mismatch",
    evidence: str = "独立语义审计确认该字段与已锁定的场景事实不一致。",
    repair_action: str = "仅修正该镜该字段，并执行完整生产回归后返回原审计窗口。",
) -> dict[str, Any]:
    """Return one fingerprint-bound semantic FAIL over a machine-clean artifact.

    Repair tests must not rely on a production-preflight-invalid workbook: the
    production boundary intentionally creates no artifact in that case.  This
    helper starts from the same complete eight-layer review as the clean
    fixture, then records one independently found, exact shot-field issue.

    The default issue id retains ``shots[1]`` so the legacy repair-package
    location adapter can bind the certificate finding to the first shot while
    the explicit ``shot_ids`` and ``field`` remain the semantic authority.
    """

    storyboard, _record = service._storyboard_from_artifact(
        request_namespace(settings), project_id, artifact_id
    )
    actual_shot_ids = [
        str(row.get("镜头编号") or row.get("shot_uid"))
        for row in storyboard.get("shots", [])
        if isinstance(row, dict)
    ]
    if shot_id not in actual_shot_ids:
        legacy_match = re.fullmatch(r"S(?P<index>\d{3})", shot_id)
        legacy_index = int(legacy_match.group("index")) - 1 if legacy_match else -1
        if 0 <= legacy_index < len(actual_shot_ids):
            shot_id = actual_shot_ids[legacy_index]

    submission = semantic_audit_for_artifact(
        service,
        settings,
        project_id,
        artifact_id,
    )
    evidence_id = f"field:{shot_id}.{field}"
    failed_layer = next(
        row for row in submission["layers"] if row["layer"] == layer
    )
    failed_layer.update(
        {
            "status": "fail_current",
            "summary": evidence,
            "evidence_ids": [evidence_id],
        }
    )
    submission["issues"] = [
        {
            "issue_id": issue_id,
            "layer": layer,
            "error_code": error_code,
            "severity": "fail",
            "blocking": True,
            "shot_ids": [shot_id],
            "batch_ids": [],
            "field": field,
            "evidence_ids": [evidence_id],
            "evidence": evidence,
            "repair_action": repair_action,
        }
    ]
    submission["conclusion"] = {
        "status": "fail_current",
        "summary": "八层独立语义复核完成，发现一项需修复问题。",
    }
    return submission


def inject_semantic_audit(
    service: Any,
    settings: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name != "audit_storyboard" or "semantic_audit" in arguments:
        return arguments
    enriched = dict(arguments)
    try:
        enriched["semantic_audit"] = semantic_audit_for_artifact(
            service,
            settings,
            str(arguments["project_id"]),
            str(arguments["artifact_id"]),
        )
    except (ValueError, KeyError, FileNotFoundError):
        # Negative tests intentionally pass an unknown, cross-project, pending,
        # or aborted artifact. Let the tool under test reject that artifact;
        # the fixture helper must not mask the production error first.
        return arguments
    return enriched
