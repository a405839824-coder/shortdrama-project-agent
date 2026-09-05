from __future__ import annotations

import re
from typing import Any

from shortdrama_mcp.auth import request_namespace
from shortdrama_mcp.canonical import stable_json_hash
from shortdrama_mcp.models import SEMANTIC_AUDIT_LAYER_NAMES
from shortdrama_mcp.service import canonical_storyboard_sha256


def semantic_audit_for_artifact(
    service: Any,
    settings: Any,
    project_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Build an explicit clean review with the artifact's current evidence refs.

    This only supplies the test's independent review.  The production pair
    proof, authored shot fields, and validator findings remain unchanged.
    """

    storyboard, _record = service._storyboard_from_artifact(
        request_namespace(settings), project_id, artifact_id
    )
    canonical_sha256 = canonical_storyboard_sha256(storyboard)
    shots = [row for row in storyboard.get("shots", []) if isinstance(row, dict)]
    batches = [row for row in storyboard.get("batches", []) if isinstance(row, dict)]
    shot_ids = [
        str(row.get("镜头编号") or row.get("shot_uid") or "").strip()
        for row in shots
    ]
    pair_ids = [f"{left}->{right}" for left, right in zip(shot_ids, shot_ids[1:])]
    evidence_id = f"shot:{shot_ids[0]}" if shot_ids else f"canonical:{canonical_sha256}"
    layer_evidence = {layer: [evidence_id] for layer in SEMANTIC_AUDIT_LAYER_NAMES}
    layer_evidence["空间连续"].extend(f"pair:{pair_id}" for pair_id in pair_ids)
    layer_evidence["机位可见性"].extend(
        f"field:{shot_id}.{field}"
        for shot_id in shot_ids
        for field in ("光影", "光源来源", "光源方向", "色温倾向")
    )
    layer_evidence["禁项与声音"].extend(
        f"field:{shot_id}.音色"
        for shot_id, shot in zip(shot_ids, shots)
        if re.search(r'说道\s*[：:]?\s*[“"]', str(shot.get("画面内容") or ""))
    )
    prevention = storyboard.get("prevention_evidence")
    adaptation = (
        prevention.get("dialogue_adaptation_audit")
        if isinstance(prevention, dict)
        else None
    )
    entries = adaptation.get("entries") if isinstance(adaptation, dict) else None
    adapted_targets: set[str] = set()
    source_unit_ids: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        source_unit_id = str(entry.get("source_unit_id") or "").strip()
        if source_unit_id:
            source_unit_ids.append(source_unit_id)
        targets = entry.get("target_shots")
        if entry.get("treatment") == "原文事实转化对白" and isinstance(targets, list):
            adapted_targets.update(str(target).strip() for target in targets)
    layer_evidence["源文对白"].extend(
        f"field:{shot_id}.画面内容" for shot_id in shot_ids if shot_id in adapted_targets
    )
    layer_evidence["源文对白"].extend(
        f"source-unit:{source_unit_id}" for source_unit_id in source_unit_ids
    )
    return {
        "canonical_sha256": canonical_sha256,
        "reviewed_shot_count": len(shots),
        "reviewed_batch_count": len(batches),
        "reviewed_adjacent_pair_count": len(pair_ids),
        "adjacent_pair_order_sha256": stable_json_hash(pair_ids),
        "layers": [
            {
                "layer": layer,
                "status": "pass",
                "summary": f"{layer}已独立复核并绑定具体实体证据。",
                "evidence_ids": layer_evidence[layer],
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
    shots = [row for row in storyboard.get("shots", []) if isinstance(row, dict)]
    actual_shot_ids = [str(row.get("镜头编号") or row.get("shot_uid")) for row in shots]
    shots_by_ref = {
        str(reference): row
        for row in shots
        for reference in (row.get("镜头编号"), row.get("shot_uid"))
        if reference
    }
    if shot_id not in shots_by_ref:
        legacy_match = re.fullmatch(r"S(?P<index>\d{3})", shot_id)
        legacy_index = int(legacy_match.group("index")) - 1 if legacy_match else -1
        if 0 <= legacy_index < len(actual_shot_ids):
            shot_id = actual_shot_ids[legacy_index]
    target = shots_by_ref.get(shot_id)
    if target is None or field not in target:
        raise ValueError(f"semantic fixture issue must target an existing shot field: {shot_id}.{field}")
    shot_id = str(target.get("镜头编号") or target.get("shot_uid"))

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
        }
    )
    issue_evidence_ids = [evidence_id]
    if evidence_id not in failed_layer["evidence_ids"]:
        if layer == "禁项与声音" and field == "音色":
            # A silent shot's voice defect must not invent an extra dialogue
            # voice-review entry. Keep the exact field on the issue and bind
            # it to the layer through the same real shot instead.
            shared_evidence_id = f"shot:{shot_id}"
            issue_evidence_ids.append(shared_evidence_id)
        else:
            shared_evidence_id = evidence_id
        if shared_evidence_id not in failed_layer["evidence_ids"]:
            failed_layer["evidence_ids"].append(shared_evidence_id)
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
            "evidence_ids": issue_evidence_ids,
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
