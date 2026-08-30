from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from .materializer import (
    COMPACT_SCHEMA_VERSION,
    COMPACT_SHOT_FIELDS as MATERIALIZER_COMPACT_SHOT_FIELDS,
)


COMPACT_SCHEMA_PROFILE = "compact_master_v1"
COMPACT_MATERIALIZER_CONTRACT = "compact-master-v1:no-persisted-derived:no-seedance-batches"

# Exactly these 27 caller-authored fields form one R10.1 compact shot.  The
# three stable identity fields below are server-owned CAS coordinates rather
# than generation fields and may coexist with this public contract.
COMPACT_SHOT_FIELDS = tuple(MATERIALIZER_COMPACT_SHOT_FIELDS)
COMPACT_INTERNAL_SHOT_FIELDS = ("shot_uid", "episode_id", "scene_uid")

# R10.1 is an explicit persistence contract, not an open-ended transport
# envelope.  These are the semantic root blocks used by the schema, validator,
# service and lossless XLSX sidecar tests.  Adding a new root block therefore
# requires an intentional schema/runtime migration instead of being silently
# copied into a committed canonical.
COMPACT_TOP_LEVEL_FIELDS = frozenset(
    {
        # Schema, project and deterministic ordering metadata.
        "project_version",
        "schema_version",
        "schema_profile",
        "workflow_profile",
        "field_order",
        # Optional single-episode identity used by legacy and delta readers.
        "episode",
        "episode_id",
        "分集",
        # Canonical content and source/boundary evidence.
        "source_lock",
        "source_ingestion",
        "chapter_intake",
        "chapter_map",
        "source_mapping",
        "protected_dialogue",
        "cognition_matrix",
        "scene_state",
        "coverage",
        "shots",
        "total_duration",
        # R10-PG1 prevention, archive and human-review evidence.
        "prevention_evidence",
        "stage_evidence",
        "human_review",
        # Approved visual/project anchors are semantic facts, not projections.
        "anchors",
        "character_anchors",
        "scene_anchors",
        "prop_anchors",
        "costume_anchors",
        "project_anchors",
        # Registered lossless semantic extension block.  Its descendants are
        # still scanned for forbidden generated caches and batch payloads.
        "canonical_extension",
    }
)

# Known historical projections may be consumed only by the R10.0 migration
# boundary.  They are never copied to an R10.1 successor.  Pattern detection
# below also catches namespaced spellings such as ``vendor_seedance_prompt_cache``.
LEGACY_DERIVED_TOP_LEVEL_FIELDS = frozenset(
    {
        "batches",
        "i2v_batches",
        "seedance_batches",
        "生成批次",
        "mother_template_columns",
        "formal_mother_template_rows",
        "正式分镜母版",
        "正式分镜母版·字段合并",
        "GPT关键帧生成指令",
        "Seedance 2.5独立执行提示词",
        "Seedance 2.5独立正式执行分镜",
        "seedance_prompt_cache",
        "i2v_prompt_cache",
        "derived",
        "derived_cache",
        "derived_outputs",
    }
)

# These zero-valued counters are observability evidence rather than generated
# prose.  They can legally occur inside an evidence block without being
# mistaken for a cached Seedance payload.
_COMPACT_ZERO_METRIC_KEYS = frozenset(
    {
        "legacy_generation_calls",
        "seedance_batches_generated",
        "model_authored_derived_chars",
        "duplicate_generated_bytes",
    }
)
LEGACY_REMOVED_SHOT_FIELDS = frozenset(
    {
        "运镜主体",
        "运镜功能",
        "人物调度",
        "动作与表演",
        "台词",
        "发声方式",
        "标点语气",
        "心理音轨属性",
        "生成批次",
    }
)
LEGACY_DERIVED_SHOT_FIELDS = frozenset(
    {
        "正式分镜母版",
        "正式分镜母版·字段合并",
        "GPT关键帧生成指令",
        "Seedance 2.5独立执行提示词",
    }
)
NO_DUPLICATE_GENERATION_EVIDENCE = {
    "model_authored_derived_chars": 0,
    "legacy_generation_calls": 0,
    "seedance_batches_generated": 0,
    "duplicate_generated_bytes": 0,
}


def _is_generated_persistence_key(raw_key: Any) -> bool:
    """Return whether a mapping key denotes a forbidden generated payload."""

    key = _normalize_string(str(raw_key)).strip()
    if key in LEGACY_DERIVED_TOP_LEVEL_FIELDS:
        return True
    folded = key.casefold().replace("-", "_").replace(" ", "_")
    if folded in _COMPACT_ZERO_METRIC_KEYS:
        return False
    if folded == "derived":
        return True
    generated_terms = (
        "prompt",
        "batch",
        "cache",
        "projection",
        "output",
        "提示词",
        "批次",
        "缓存",
        "派生",
        "投影",
        "输出",
    )
    return (
        ("seedance" in folded or "i2v" in folded)
        and any(term in folded for term in generated_terms)
    ) or (
        "derived" in folded
        and any(
            term in folded
            for term in (
                "cache",
                "output",
                "projection",
                "prompt",
                "payload",
                "缓存",
                "输出",
                "投影",
                "提示词",
            )
        )
    )


def _generated_payload_paths(value: Any, *, path: str = "top-level") -> list[str]:
    """Find generated caches/batches even when hidden in a semantic block."""

    hits: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _is_generated_persistence_key(key):
                hits.append(child_path)
                continue
            hits.extend(_generated_payload_paths(child, path=child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            hits.extend(_generated_payload_paths(child, path=f"{path}[{index}]"))
    return hits


def _strip_legacy_generated_payloads(value: Any) -> None:
    """Delete only registered generated payloads from an accepted legacy copy."""

    if isinstance(value, dict):
        for key in list(value):
            if _is_generated_persistence_key(key):
                value.pop(key, None)
            else:
                _strip_legacy_generated_payloads(value[key])
    elif isinstance(value, list):
        for child in value:
            _strip_legacy_generated_payloads(child)


def _assert_top_level_persistence_contract(
    storyboard: dict[str, Any],
    *,
    legacy_input: bool,
) -> None:
    """Enforce the registered root schema before any caller data is copied."""

    unknown = sorted(
        str(field)
        for field in storyboard
        if field not in COMPACT_TOP_LEVEL_FIELDS
        and not _is_generated_persistence_key(field)
    )
    if unknown:
        action = (
            "legacy unknown semantic root fields require an explicit R10.1 mapping"
            if legacy_input
            else "unknown R10.1 root fields require a schema migration"
        )
        raise ValueError(
            "compact_contract_violation: " + action + ": " + ", ".join(unknown)
        )
    if not legacy_input:
        generated = _generated_payload_paths(storyboard)
        if generated:
            raise ValueError(
                "compact_contract_violation: R10.1 cannot persist generated "
                "projections, Seedance/i2v caches, or batches: "
                + ", ".join(generated[:32])
            )


def _normalize_string(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )


def normalize_json(value: Any) -> Any:
    """Return a deterministic, JSON-safe representation without changing array order.

    The helper is deliberately strict: unsupported Python objects fail instead of
    being stringified, because silently stringifying them would make a cache key
    look stable when it is not.
    """

    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not allow NaN or infinity")
        return int(value) if value.is_integer() else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical JSON does not allow NaN or infinity")
        integral = value.to_integral_value()
        return int(integral) if value == integral else float(value)
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _normalize_string(str(raw_key))
            if key in normalized:
                raise ValueError(f"canonical JSON key collision after normalization: {key!r}")
            normalized[key] = normalize_json(raw_value)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_json(item) for item in value]
    raise TypeError(f"value is not JSON serializable for canonical hashing: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    normalized = normalize_json(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Readable alias used by the MCP response and certificate contracts."""

    return stable_json_hash(value)


def compact_contract_evidence() -> dict[str, Any]:
    """Return content-free proof that no duplicate long-form generation ran."""

    return {
        "schema_version": COMPACT_SCHEMA_VERSION,
        "schema_profile": COMPACT_SCHEMA_PROFILE,
        "materializer_contract": COMPACT_MATERIALIZER_CONTRACT,
        "model_authored_shot_field_count": len(COMPACT_SHOT_FIELDS),
        "model_authored_shot_fields_sha256": stable_json_hash(COMPACT_SHOT_FIELDS),
        "persisted_derived_fields": [],
        "persisted_seedance_batches": False,
        "materialization_passes": 1,
        **NO_DUPLICATE_GENERATION_EVIDENCE,
    }


def assert_compact_storyboard_contract(storyboard: dict[str, Any]) -> None:
    """Validate an already-declared R10.1 canonical without rewriting it."""

    if not isinstance(storyboard, dict):
        raise TypeError("compact storyboard must be an object")
    if str(storyboard.get("schema_version") or "").strip() != COMPACT_SCHEMA_VERSION:
        raise ValueError("compact_contract_violation: schema_version must be R10.1")
    _assert_top_level_persistence_contract(storyboard, legacy_input=False)
    violations: list[str] = []
    shots = storyboard.get("shots")
    if not isinstance(shots, list):
        violations.append("top-level:shots")
    else:
        expected = set(COMPACT_SHOT_FIELDS)
        allowed = expected | set(COMPACT_INTERNAL_SHOT_FIELDS)
        for index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                violations.append(f"shots[{index}]:not_object")
                continue
            missing = sorted(expected - set(shot))
            forbidden = sorted(
                (set(shot) & (LEGACY_REMOVED_SHOT_FIELDS | LEGACY_DERIVED_SHOT_FIELDS))
                | (set(shot) - allowed)
            )
            violations.extend(f"shots[{index}].missing:{field}" for field in missing)
            violations.extend(f"shots[{index}].forbidden:{field}" for field in forbidden)
    if violations:
        raise ValueError(
            "compact_contract_violation: R10.1 input must already use exactly the "
            "27-field compact shot contract; " + ", ".join(violations[:32])
        )


def compact_storyboard_canonical(
    storyboard: dict[str, Any],
    *,
    in_place: bool = False,
) -> dict[str, Any]:
    """Normalize any accepted storyboard into the R10.1 compact canonical.

    This is a one-way persistence boundary.  Legacy split presentation fields
    are folded once into the retained semantic fields, server-generated long
    projections are removed, and legacy Seedance batches are deliberately not
    copied.  Exporters may compile the formal-master and GPT columns on demand,
    but those projections never become canonical state.
    """

    if not isinstance(storyboard, dict):
        raise TypeError("compact storyboard must be an object")
    declared_version = str(storyboard.get("schema_version") or "").strip()
    legacy_input = declared_version != COMPACT_SCHEMA_VERSION
    _assert_top_level_persistence_contract(
        storyboard,
        legacy_input=legacy_input,
    )
    if declared_version == COMPACT_SCHEMA_VERSION:
        assert_compact_storyboard_contract(storyboard)

    # Delayed import keeps the hashing module usable while materializer itself
    # imports canonical helpers.  All legacy-field folding has one authority.
    from .materializer import migrate_storyboard_to_compact

    result = migrate_storyboard_to_compact(storyboard, in_place=in_place)
    if legacy_input:
        # Work only on the materializer's copy (or the explicitly requested
        # in-place value).  Registered historical projections are safe to
        # discard; unregistered root facts were blocked before this point.
        _strip_legacy_generated_payloads(result)
    compact_shots = result.get("shots")
    if not isinstance(compact_shots, list):
        raise ValueError("compact storyboard requires a shots array")
    episode_running: dict[str, float] = {}
    total_duration = 0.0
    for index, compact in enumerate(compact_shots):
        if not isinstance(compact, dict):
            raise ValueError(f"compact storyboard shot[{index}] must be an object")
        try:
            duration = float(compact.get("时长") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"compact storyboard shot[{index}].时长 must be numeric"
            ) from exc
        if duration < 0 or not math.isfinite(duration):
            raise ValueError(
                f"compact storyboard shot[{index}].时长 must be finite and non-negative"
            )
        if abs(duration * 10 - round(duration * 10)) > 1e-8:
            raise ValueError(
                f"compact storyboard shot[{index}].时长 must use 0.1-second precision"
            )
        shot_id = str(compact.get("镜头编号") or "").strip()
        display_match = re.match(r"^EP(?P<episode>\d{2})-\d{3}$", shot_id)
        scene_match = re.match(
            r"^(EP\d{2})(?:-|$)",
            str(compact.get("场景连续性ID") or "").strip(),
        )
        episode_key = str(
            (f"EP{display_match.group('episode')}" if display_match else "")
            or (scene_match.group(1) if scene_match else "")
            or compact.get("episode_id")
            or result.get("episode_id")
            or result.get("episode")
            or result.get("分集")
            or "__single_episode__"
        )
        episode_cumulative = round(
            episode_running.get(episode_key, 0.0) + duration,
            1,
        )
        episode_running[episode_key] = episode_cumulative
        compact["累计时长"] = (
            int(episode_cumulative)
            if episode_cumulative.is_integer()
            else episode_cumulative
        )
        total_duration = round(total_duration + duration, 1)

    result["schema_version"] = COMPACT_SCHEMA_VERSION
    result["schema_profile"] = COMPACT_SCHEMA_PROFILE
    result["total_duration"] = (
        int(total_duration) if total_duration.is_integer() else total_duration
    )
    successor = {
        field: result[field]
        for field in result
        if field in COMPACT_TOP_LEVEL_FIELDS
    }
    if in_place:
        result.clear()
        result.update(successor)
        successor = result
    assert_compact_storyboard_contract(successor)
    return successor


def normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:].strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1]
    return candidate or None


def etag_matches(candidate: str | None, current: str | None) -> bool:
    left = normalize_etag(candidate)
    right = normalize_etag(current)
    return left is not None and right is not None and left == right
