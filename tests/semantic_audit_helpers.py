from __future__ import annotations

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
