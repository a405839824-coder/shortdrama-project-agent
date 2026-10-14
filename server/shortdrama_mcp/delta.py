from __future__ import annotations

import copy
import json
import re
import time
from typing import Any

from .canonical import (
    COMPACT_MATERIALIZER_CONTRACT,
    COMPACT_SCHEMA_PROFILE,
    COMPACT_SCHEMA_VERSION,
    LEGACY_DERIVED_SHOT_FIELDS,
    NO_DUPLICATE_GENERATION_EVIDENCE,
    assert_compact_storyboard_contract,
    compact_storyboard_canonical,
    stable_json_hash,
)
from .materializer import migrate_legacy_shot
from .continuity import state_equal


# The delta route is intentionally execution-only. These fields bind source,
# story meaning, character knowledge, or structural identity and therefore
# require the complete canonical route when they change.
DELTA_FULL_ROUTE_FIELDS = {
    "镜头编号",
    "shot_uid",
    "episode_id",
    "scene_uid",
    "场景连续性ID",
    "功能类型",
    "可见主体",
    "心理音轨",
    "覆盖原文",
    "原文依据类型",
    "原文支撑句",
}
# Psychological audio can disclose or alter character knowledge.  The bounded
# delta evidence schema has shot-keyed performance rows but no shot-keyed
# cognition proof, so these two fields intentionally remain on the full route.
DELTA_EXECUTION_FIELDS = {
    "时长",
    "累计时长",
    "镜头类型",
    "空间站位描述",
    "起始状态",
    "机位",
    "运镜方式",
    "画面内容",
    "音色",
    "环境音效",
    "环境动态",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
    "生成限制",
    "转场类型",
    "切镜/转场提示词",
    "参考图锚定",
}
SERVER_DERIVED_SHOT_FIELDS = {
    "累计时长",
    *LEGACY_DERIVED_SHOT_FIELDS,
}
SHOT_PLAN_TRIGGER_FIELDS = {
    "时长",
    "镜头类型",
    "机位",
    "运镜方式",
    "空间站位描述",
    "起始状态",
    "画面内容",
    "转场类型",
    "切镜/转场提示词",
    "参考图锚定",
}
PERFORMANCE_TRIGGER_FIELDS = {
    "画面内容",
    "音色",
}
SCENE_STATE_TRIGGER_FIELDS = {
    "空间站位描述",
    "起始状态",
    "环境动态",
    "光源方向",
    "参考图锚定",
}
# Authored timing contains utterance/phase ranges, pause/action dependencies
# and possibly audio spanning multiple shots. The bounded delta schema cannot
# replace or independently revalidate that global dependency graph. A change
# to any execution fact that may alter it therefore needs the full route.
# Static lighting edits retain their existing scene-state/evidence checks.
TIMING_TRIGGER_FIELDS = DELTA_EXECUTION_FIELDS - {
    "累计时长",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
}
EVIDENCE_BLOCKS = {
    "shot_plan",
    "performance_chains",
    "scene_state_ledger",
}
DERIVED_GLOBAL_EVIDENCE_BLOCKS = {"adjacent_continuity_audit"}
STATE_VECTOR_FIELDS = [
    "人物位置",
    "身体朝向",
    "视线落点",
    "手持道具",
    "门窗状态",
    "动作进度",
    "光源方向",
]
EVIDENCE_BINDING_FIELDS = {
    "shot_uid",
    "shot_semantic_sha256",
    "delta_evidence_revision",
}

DIALOGUE_TAG_RE = re.compile(
    r"【(?!\d+\.\d—\d+\.\d秒】)(?P<speaker>[^】\n]{1,30})】"
    r"\s*(?P<line>[^【\n｜|；;]+)"
)
QUOTED_TEXT_RE = re.compile(
    r"“(?P<cn_double>[^”\n]+)”|"
    r"‘(?P<cn_single>[^’\n]+)’|"
    r"「(?P<corner>[^」\n]+)」|"
    r"『(?P<double_corner>[^』\n]+)』|"
    r'"(?P<ascii_double>[^"\n]+)"'
)
ATTRIBUTED_QUOTE_RE = re.compile(
    r"(?P<speaker>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,20}?)"
    r"(?:平静地|克制地|轻声|低声|高声|冷声|沉声|缓缓|忽然|随即|再)?"
    r"(?:说|问|答|喊|道|开口|回应|解释|致歉)\s*[：:]?\s*"
    r"(?P<quote>“[^”\n]+”|‘[^’\n]+’|「[^」\n]+」|『[^』\n]+』|\"[^\"\n]+\")"
)
BOUNDARY_EXPLANATION_RE = re.compile(
    r"切|转入|转到|换场|场景|地点|时空|翌日|次日|稍后|片刻|另一|新空间|跨集|集间"
)


def _shot_id(value: dict[str, Any]) -> str:
    return str(value.get("镜头编号") or "").strip()


def _shot_uid(value: dict[str, Any]) -> str:
    return str(value.get("shot_uid") or "").strip()


def _batch_id(value: dict[str, Any]) -> str:
    return str(value.get("批次编号") or "").strip()


def _covered_shots(value: dict[str, Any]) -> list[str]:
    covered = value.get("覆盖镜头", [])
    if isinstance(covered, str):
        covered = [item.strip() for item in re.split(r"[,，、]", covered) if item.strip()]
    if not isinstance(covered, list):
        raise ValueError("delta_batch_invalid: 覆盖镜头 must be an array or delimited string")
    return [str(item).strip() for item in covered if str(item).strip()]


def _semantic_shot_hash(shot: dict[str, Any]) -> str:
    value = {
        key: copy.deepcopy(item)
        for key, item in shot.items()
        if key not in SERVER_DERIVED_SHOT_FIELDS
        and key not in {"shot_uid", "scene_uid", "episode_id"}
    }
    return stable_json_hash(value)


def changed_shot_fields(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    """Return actual caller-controlled field changes, excluding derived time."""

    keys = set(before) | set(after)
    return {
        key
        for key in keys
        if key not in SERVER_DERIVED_SHOT_FIELDS
        and stable_json_hash(before.get(key)) != stable_json_hash(after.get(key))
    }


def _has_authored_timing(storyboard: dict[str, Any]) -> bool:
    evidence = storyboard.get("prevention_evidence")
    audio = evidence.get("audio_workflow") if isinstance(evidence, dict) else None
    # Presence, not truthiness: empty or malformed authored timing may not
    # evade invalidation. Its schema/content validity remains a full-preflight
    # responsibility; the delta route never repairs a global timing block.
    return isinstance(audio, dict) and "timing" in audio


def _dialogue_signature(picture_content: Any) -> dict[str, list[tuple[str, str]] | list[str]]:
    """Return deterministic spoken-text evidence embedded in 画面内容.

    R10-SPC-1 uses ``【0.0—x.x秒】`` stage brackets and integrated
    ``角色说道：“对白。”`` speech; the stage brackets are never speakers.
    Quoted legacy prose is included conservatively so a speaker/text change cannot reuse stale
    source-mapping, protected-dialogue, or cognition evidence through delta.
    Ordinary unquoted visible-action edits remain eligible for the bounded
    execution route.
    """

    text = str(picture_content or "")
    tagged: list[tuple[str, str]] = []
    for match in DIALOGUE_TAG_RE.finditer(text):
        line = match.group("line").strip()
        sentence = re.match(r"(.+?[。！？?!](?:[。！？?!…])?)", line)
        if sentence:
            line = sentence.group(1).strip()
        if line:
            tagged.append((match.group("speaker").strip(), line))
    quoted = [
        next(value for value in match.groups() if value is not None).strip()
        for match in QUOTED_TEXT_RE.finditer(text)
    ]
    attributed = [
        (match.group("speaker").strip(), match.group("quote").strip())
        for match in ATTRIBUTED_QUOTE_RE.finditer(text)
    ]
    return {"tagged": tagged, "quoted": quoted, "attributed": attributed}


def _evidence_content_hash(row: dict[str, Any]) -> str:
    return stable_json_hash(
        {key: value for key, value in row.items() if key not in EVIDENCE_BINDING_FIELDS}
    )


def _build_adjacent_continuity_audit(
    shots: list[dict[str, Any]],
    scene_state_rows: Any,
) -> dict[str, Any]:
    """Deterministically rebuild the complete N-1 continuity proof.

    Delta cannot change shot identity, order, episode, scene, or source meaning.
    It may change execution state, so the server refreshes hashes and pair
    bindings from the caller-supplied scene-state rows instead of retaining a
    stale global proof or asking the model to restate unchanged topology.
    """

    by_shot = _evidence_index(scene_state_rows, block="scene_state_ledger")
    pairs: list[dict[str, Any]] = []
    for ordinal in range(max(len(shots) - 1, 0)):
        left = shots[ordinal]
        right = shots[ordinal + 1]
        left_id = _shot_id(left)
        right_id = _shot_id(right)
        left_row = by_shot[left_id]
        right_row = by_shot[right_id]
        left_scene = str(left_row.get("scene_id", left.get("场景连续性ID", "")))
        right_scene = str(right_row.get("scene_id", right.get("场景连续性ID", "")))
        left_episode = _stable_episode_id({}, left)
        right_episode = _stable_episode_id({}, right)
        boundary_type = (
            "episode_cut"
            if left_episode != right_episode
            else "scene_cut"
            if left_scene != right_scene
            else "same_scene"
        )
        left_end = left_row.get("end_state", {})
        right_start = right_row.get("start_state", {})
        transition_explanation = right_row.get("transition_explanation")
        if boundary_type == "same_scene":
            continuity_status = (
                "closed" if state_equal(left_end, right_start) else "fail_current"
            )
        else:
            continuity_status = (
                "explained_transition"
                if BOUNDARY_EXPLANATION_RE.search(
                    str(transition_explanation or "")
                )
                else "fail_current"
            )
        pairs.append(
            {
                "pair_id": f"{left_id}->{right_id}",
                "left_shot_id": left_id,
                "right_shot_id": right_id,
                "boundary_type": boundary_type,
                "left_end_state_sha256": stable_json_hash(left_end),
                "right_start_state_sha256": stable_json_hash(right_start),
                "reviewed_fields": list(STATE_VECTOR_FIELDS),
                "continuity_status": continuity_status,
                "transition_explanation": transition_explanation,
            }
        )
    pair_ids = [row["pair_id"] for row in pairs]
    return {
        "contract": "adjacent-pair-continuity-v1",
        "expected_pair_count": len(pairs),
        "reviewed_pair_count": len(pairs),
        "coverage_percent": 100,
        "ordered_pair_sha256": stable_json_hash(pair_ids),
        "pairs": pairs,
    }


def _stable_episode_id(storyboard: dict[str, Any], shot: dict[str, Any]) -> str:
    explicit = str(shot.get("episode_id") or storyboard.get("episode_id") or "").strip()
    if explicit:
        return explicit
    candidates = (
        str(shot.get("场景连续性ID") or ""),
        _shot_id(shot),
        str(storyboard.get("episode") or storyboard.get("分集") or ""),
    )
    for value in candidates:
        match = re.search(r"(?:第\s*0*(\d+)\s*集|\bEP\s*0*(\d+)\b)", value, re.IGNORECASE)
        if match:
            number = int(match.group(1) or match.group(2))
            return f"EP{number:02d}"
    # If no explicit episode marker exists, the artifact is treated as one
    # episode. Scene labels must not accidentally mint a different episode per
    # scene. Multi-episode artifacts therefore need an explicit marker/ID.
    return "EP-" + stable_json_hash({"structural_label": "single-episode"})[:20]


def _evidence_index(rows: Any, *, block: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"delta_evidence_base_invalid: prevention_evidence.{block} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"delta_evidence_base_invalid: prevention_evidence.{block} rows must be objects")
        shot_id = str(row.get("shot_id") or "").strip()
        if not shot_id:
            raise ValueError(f"delta_evidence_base_invalid: prevention_evidence.{block} row misses shot_id")
        if shot_id in result:
            raise ValueError(f"delta_evidence_base_invalid: duplicate {block} evidence for {shot_id}")
        result[shot_id] = row
    return result


def _compiled_shot_plan(base_row: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    """Refresh directly redundant plan facts without inventing creative facts."""

    row = copy.deepcopy(base_row)
    row.update(
        {
            "shot_id": _shot_id(shot),
            "core_function": shot.get("功能类型"),
            "camera_position": shot.get("机位"),
            "main_action_stage": shot.get("画面内容"),
            "transition_function": shot.get("转场类型"),
        }
    )
    return row


def _apply_evidence_updates(
    merged: dict[str, Any],
    *,
    base: dict[str, Any],
    base_affected: list[dict[str, Any]],
    replacement_shots: list[dict[str, Any]],
    evidence_updates: dict[str, Any] | None,
) -> dict[str, Any]:
    """Replace only affected shot-keyed evidence and prove untouched blocks.

    A changed visual description automatically refreshes its directly
    redundant shot-plan row. Performance and scene-state changes require
    explicit complete rows because the server cannot safely invent those
    semantic chains. Global source/cognition/coverage evidence is immutable on
    this route.
    """

    base_evidence = base.get("prevention_evidence")
    merged_evidence = merged.get("prevention_evidence")
    if not isinstance(base_evidence, dict) or not isinstance(merged_evidence, dict):
        raise ValueError("delta_evidence_base_invalid: prevention_evidence must be an object")
    supplied = evidence_updates or {}
    if not isinstance(supplied, dict) or set(supplied) - EVIDENCE_BLOCKS:
        raise ValueError(
            "delta_evidence_global_requires_full_commit: only shot_plan, performance_chains, "
            "and scene_state_ledger updates are accepted"
        )

    base_by_id = {_shot_id(row): row for row in base_affected}
    replacement_by_id = {_shot_id(row): row for row in replacement_shots}
    affected_ids = set(base_by_id)
    changed_by_id = {
        shot_id: changed_shot_fields(base_by_id[shot_id], replacement_by_id[shot_id])
        for shot_id in affected_ids
    }
    required: dict[str, set[str]] = {
        "shot_plan": {
            shot_id for shot_id, fields in changed_by_id.items() if fields & SHOT_PLAN_TRIGGER_FIELDS
        },
        "performance_chains": {
            shot_id for shot_id, fields in changed_by_id.items() if fields & PERFORMANCE_TRIGGER_FIELDS
        },
        "scene_state_ledger": {
            shot_id for shot_id, fields in changed_by_id.items() if fields & SCENE_STATE_TRIGGER_FIELDS
        },
    }

    untouched_hashes_before = {
        key: stable_json_hash(value)
        for key, value in base_evidence.items()
        if key not in EVIDENCE_BLOCKS | DERIVED_GLOBAL_EVIDENCE_BLOCKS
    }
    updated_counts: dict[str, int] = {}
    for block in sorted(EVIDENCE_BLOCKS):
        base_rows = base_evidence.get(block)
        base_index = _evidence_index(base_rows, block=block)
        supplied_rows = supplied.get(block, [])
        if not isinstance(supplied_rows, list):
            raise ValueError(f"delta_evidence_invalid: {block} must be an array")
        supplied_index = _evidence_index(supplied_rows, block=block) if supplied_rows else {}
        extras = sorted(set(supplied_index) - required[block])
        if extras:
            raise ValueError(
                f"delta_evidence_scope_violation: {block} contains unaffected shots "
                + ", ".join(extras)
            )
        missing = sorted(required[block] - set(supplied_index))
        if block == "shot_plan":
            # 画面/机位等 fields are direct canonical facts, so a missing row
            # can be refreshed deterministically from the replacement shot.
            for shot_id in list(missing):
                if shot_id not in base_index:
                    raise ValueError(f"delta_evidence_missing: base shot_plan missing {shot_id}")
                supplied_index[shot_id] = _compiled_shot_plan(
                    base_index[shot_id], replacement_by_id[shot_id]
                )
                missing.remove(shot_id)
        if missing:
            raise ValueError(
                f"delta_evidence_missing: {block} requires complete replacement rows for "
                + ", ".join(missing)
            )

        updated_rows: list[dict[str, Any]] = []
        for base_row in base_rows:
            shot_id = str(base_row.get("shot_id") or "").strip()
            if shot_id not in supplied_index:
                updated_rows.append(copy.deepcopy(base_row))
                continue
            new_row = copy.deepcopy(supplied_index[shot_id])
            if _evidence_content_hash(new_row) == _evidence_content_hash(base_row):
                raise ValueError(
                    f"delta_evidence_stale: {block} for {shot_id} is unchanged despite a semantic edit"
                )
            shot = replacement_by_id[shot_id]
            new_row["shot_id"] = shot_id
            new_row["shot_uid"] = _shot_uid(shot)
            new_row["shot_semantic_sha256"] = _semantic_shot_hash(shot)
            new_row["delta_evidence_revision"] = "delta-evidence-v1"
            updated_rows.append(new_row)
        merged_evidence[block] = updated_rows
        updated_counts[block] = len(supplied_index)

    merged_evidence["adjacent_continuity_audit"] = (
        _build_adjacent_continuity_audit(
            merged.get("shots", []),
            merged_evidence.get("scene_state_ledger"),
        )
    )

    untouched_hashes_after = {
        key: stable_json_hash(value)
        for key, value in merged_evidence.items()
        if key not in EVIDENCE_BLOCKS | DERIVED_GLOBAL_EVIDENCE_BLOCKS
    }
    if untouched_hashes_before != untouched_hashes_after:
        raise RuntimeError("delta_evidence_global_changed: immutable evidence blocks changed")
    return {
        "updated_evidence_counts": updated_counts,
        "global_evidence_hashes_verified": True,
        "adjacent_continuity_audit_sha256": stable_json_hash(
            merged_evidence["adjacent_continuity_audit"]
        ),
    }


def ensure_shot_uids(
    storyboard: dict[str, Any], *, in_place: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a storyboard with stable internal shot identities.

    Legacy 0.3/0.4 artifacts have only a display shot number.  Their first 0.5
    read or delta operation receives a deterministic UID derived from the
    immutable legacy canonical and ordinal.  Once stored in a successor, the
    UID survives display-number changes and future deltas.  ``in_place`` is
    reserved for an already-copied compact persistence boundary.
    """

    migrated = storyboard if in_place else copy.deepcopy(storyboard)
    shots = migrated.get("shots")
    if not isinstance(shots, list):
        return migrated, {"shot_uids": [], "legacy_uid_count": 0}
    used: set[str] = set()
    legacy_count = 0
    manifest: list[dict[str, Any]] = []
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        episode_id = _stable_episode_id(migrated, shot)
        shot["episode_id"] = episode_id
        scene_label = str(shot.get("场景连续性ID") or "").strip()
        if not scene_label:
            raise ValueError("scene_uid_missing_basis: 场景连续性ID is required")
        if not str(shot.get("scene_uid") or "").strip():
            shot["scene_uid"] = "SCENE-" + stable_json_hash(
                {"episode_id": episode_id, "scene": scene_label}
            )[:20]
        uid = _shot_uid(shot)
        if not uid:
            uid = "SHOT-" + stable_json_hash(
                {
                    "episode_id": episode_id,
                    "scene_uid": shot["scene_uid"],
                    "display_shot_id": _shot_id(shot),
                }
            )[:20]
            shot["shot_uid"] = uid
            legacy_count += 1
        if uid in used:
            raise ValueError(f"shot_uid_collision: duplicate internal shot uid {uid}")
        used.add(uid)
        manifest.append(
            {
                "shot_uid": uid,
                "shot_id": _shot_id(shot),
                "ordinal": index,
                "semantic_sha256": _semantic_shot_hash(shot),
            }
        )
    return migrated, {
        "shot_uids": manifest,
        "batch_uids": [],
        "legacy_uid_count": legacy_count,
        "legacy_batch_uid_count": 0,
    }


def storyboard_identity_manifest(storyboard: dict[str, Any]) -> dict[str, Any]:
    """Describe prepared compact identities without copying or rewriting shots."""

    assert_compact_storyboard_contract(storyboard)
    shots = storyboard.get("shots", [])
    manifest: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, shot in enumerate(shots):
        uid = _shot_uid(shot)
        if not uid or uid in used:
            raise ValueError("shot_uid_missing_or_collision: prepared compact delta identity is invalid")
        used.add(uid)
        manifest.append(
            {
                "shot_uid": uid,
                "shot_id": _shot_id(shot),
                "ordinal": index,
                "semantic_sha256": _semantic_shot_hash(shot),
            }
        )
    return {
        "shot_uids": manifest,
        "batch_uids": [],
        "legacy_uid_count": 0,
        "legacy_batch_uid_count": 0,
    }


def merge_storyboard_delta(
    base: dict[str, Any],
    *,
    affected_shot_uids: list[str],
    replacement_shots: list[dict[str, Any]],
    replacement_batches: list[dict[str, Any]] | None = None,
    evidence_updates: dict[str, Any] | None = None,
    top_level_updates: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge one contiguous affected range into the compact canonical.

    Unaffected semantic shot fields are copied from the exact base canonical.
    Only cumulative time is globally recomputed.  ``replacement_batches`` is
    accepted solely so 0.4 callers do not fail at the transport boundary; its
    contents are counted and discarded without materialization.
    """

    merge_started_ns = time.perf_counter_ns()
    if not isinstance(base, dict):
        raise ValueError("delta_base_invalid: canonical storyboard must be an object")
    if not isinstance(affected_shot_uids, list) or not affected_shot_uids:
        raise ValueError("delta_scope_required: affected_shot_uids must be non-empty")
    affected_uids = [str(item).strip() for item in affected_shot_uids if str(item).strip()]
    if len(affected_uids) != len(affected_shot_uids) or len(set(affected_uids)) != len(affected_uids):
        raise ValueError("delta_scope_invalid: affected_shot_uids must be unique non-empty strings")
    if not isinstance(replacement_shots, list) or not replacement_shots:
        raise ValueError("delta_replacement_required: replacement_shots must be non-empty")
    if replacement_batches is not None and not isinstance(replacement_batches, list):
        raise ValueError("delta_legacy_batches_invalid: replacement_batches must be an array")
    discarded_legacy_batch_count = len(replacement_batches or [])

    # A delta base is already a persisted R10.1 canonical.  Treat it as a
    # read-only CAS input: strict validation plus identity enrichment is not a
    # second semantic materialization pass.
    if str(base.get("schema_version") or "").strip() != COMPACT_SCHEMA_VERSION:
        raise ValueError(
            "delta_base_requires_compact_successor: legacy bases must first be committed "
            "through the full R10.1 production route"
        )
    assert_compact_storyboard_contract(base)
    migrated_base, uid_migration = ensure_shot_uids(base)
    assert_compact_storyboard_contract(migrated_base)
    base_shots = migrated_base.get("shots")
    if not isinstance(base_shots, list) or not base_shots:
        raise ValueError("delta_base_invalid: base canonical has no shots")
    if any(not isinstance(row, dict) for row in base_shots):
        raise ValueError("delta_base_invalid: every base shot must be an object")
    base_ids = [_shot_id(row) for row in base_shots]
    base_uids = [_shot_uid(row) for row in base_shots]
    if any(not value for value in base_ids) or len(set(base_ids)) != len(base_ids):
        raise ValueError("delta_base_invalid: base shot ids must be unique and non-empty")
    missing = sorted(set(affected_uids) - set(base_uids))
    if missing:
        raise ValueError("delta_scope_missing: base does not contain shot_uids " + ", ".join(missing))
    positions = sorted(base_uids.index(value) for value in affected_uids)
    expected_positions = list(range(positions[0], positions[-1] + 1))
    if positions != expected_positions or affected_uids != [base_uids[index] for index in expected_positions]:
        raise ValueError("delta_scope_noncontiguous: affected_shot_uids must follow one exact contiguous base range")
    affected = [base_ids[index] for index in expected_positions]
    base_affected = [base_shots[index] for index in expected_positions]

    if any(not isinstance(row, dict) for row in replacement_shots):
        raise ValueError("delta_replacement_invalid: every replacement shot must be an object")
    if len(replacement_shots) != len(base_affected):
        raise ValueError(
            "delta_structural_change_requires_full_commit: insert/delete/split/merge changes require "
            "the full source, cognition, scene-state, and mapping route"
        )
    # Replacement input is normalized only over the affected range.  Declared
    # compact rows are validated before local copying so retired/unknown fields
    # cannot be hidden by the normalizer.  The sole full-table semantic pass is
    # performed after the protected base and this bounded range are merged.
    assert_compact_storyboard_contract(
        {"schema_version": COMPACT_SCHEMA_VERSION, "shots": replacement_shots}
    )
    replacement_shots = [
        migrate_legacy_shot(shot, legacy=False) for shot in replacement_shots
    ]
    replacement_uids: list[str] = []
    used_replacement_uids: set[str] = set()
    for index, shot in enumerate(replacement_shots):
        base_shot = base_affected[index]
        uid = _shot_uid(shot)
        display_id = _shot_id(shot)
        expected_uid = _shot_uid(base_shot)
        expected_display_id = _shot_id(base_shot)
        if uid and uid != expected_uid:
            raise ValueError(
                f"delta_scope_move_requires_full_commit: replacement {display_id!r} changes shot_uid"
            )
        uid = expected_uid
        if display_id != expected_display_id:
            raise ValueError(
                "delta_structural_change_requires_full_commit: display shot identity changed; "
                "renumbering requires the full route"
            )
        for identity_field in ("episode_id", "scene_uid", "场景连续性ID"):
            base_value = base_shot.get(identity_field)
            supplied_value = shot.get(identity_field)
            if supplied_value in (None, ""):
                shot[identity_field] = copy.deepcopy(base_value)
            elif str(supplied_value).strip().casefold() != str(base_value or "").strip().casefold():
                raise ValueError(
                    f"delta_scope_move_requires_full_commit: {expected_display_id}.{identity_field} "
                    "moves across an authorized episode or scene"
                )
        shot["shot_uid"] = uid
        changed_fields = changed_shot_fields(base_shot, shot)
        if (
            "画面内容" in changed_fields
            and _dialogue_signature(base_shot.get("画面内容"))
            != _dialogue_signature(shot.get("画面内容"))
        ):
            raise ValueError(
                "delta_semantic_requires_full_commit: dialogue text, quoted speech, "
                f"or speaker changed in {expected_display_id}; refresh source mapping, "
                "protected dialogue, and cognition evidence through the full route"
            )
        forbidden = sorted(changed_fields & DELTA_FULL_ROUTE_FIELDS)
        unknown = sorted(
            field
            for field in changed_fields
            if field not in DELTA_EXECUTION_FIELDS
            and field not in DELTA_FULL_ROUTE_FIELDS
            and field not in {"shot_uid", "episode_id", "scene_uid"}
        )
        if forbidden or unknown:
            fields = forbidden + unknown
            raise ValueError(
                "delta_semantic_requires_full_commit: source, cognition, identity, or global mapping "
                "fields changed: " + ", ".join(fields)
            )
        timing_fields = sorted(changed_fields & TIMING_TRIGGER_FIELDS)
        if timing_fields and _has_authored_timing(migrated_base):
            raise ValueError(
                "delta_timing_evidence_requires_full_commit: "
                f"{expected_display_id} changes timing-dependent fields "
                + ", ".join(timing_fields)
                + "; rebuild the affected complete scene/episode with fresh "
                "audio_workflow.timing, cross-shot audio and source-fragment "
                "dependencies, then run full production preflight in the current "
                "window. Bounded delta cannot reuse or replace authored timing."
            )
        if uid in used_replacement_uids:
            raise ValueError(f"delta_replacement_invalid: duplicate replacement shot_uid {uid}")
        replacement_uids.append(uid)
        used_replacement_uids.add(uid)
    replacement_ids = [_shot_id(row) for row in replacement_shots]
    if any(not value for value in replacement_ids) or len(set(replacement_ids)) != len(replacement_ids):
        raise ValueError("delta_replacement_invalid: replacement shot ids must be unique and non-empty")
    untouched_ids = set(base_ids) - set(affected)
    collisions = sorted(set(replacement_ids) & untouched_ids)
    if collisions:
        raise ValueError("delta_shot_id_collision: replacement ids collide with protected base ids " + ", ".join(collisions))

    affected_set = set(affected)

    merged = copy.deepcopy(migrated_base)
    updates = top_level_updates or {}
    if not isinstance(updates, dict):
        raise ValueError("delta_top_level_invalid: top_level_updates must be an object")
    if updates:
        raise ValueError(
            "delta_top_level_requires_full_commit: compact delta preserves every top-level evidence block; "
            "use the full route when source, cognition, coverage, scene-state, or chapter evidence changes"
        )

    before_hashes = {
        shot_id: _semantic_shot_hash(base_shots[index])
        for index, shot_id in enumerate(base_ids)
        if shot_id not in affected_set
    }
    merged_shots = (
        [copy.deepcopy(row) for row in base_shots[: positions[0]]]
        + [copy.deepcopy(row) for row in replacement_shots]
        + [copy.deepcopy(row) for row in base_shots[positions[-1] + 1 :]]
    )
    merged["shots"] = merged_shots
    compact_started_ns = time.perf_counter_ns()
    merged["schema_version"] = COMPACT_SCHEMA_VERSION
    merged["schema_profile"] = COMPACT_SCHEMA_PROFILE
    merged.pop("batches", None)
    merged = compact_storyboard_canonical(merged, in_place=True)
    assert_compact_storyboard_contract(merged)
    merged_shots = merged["shots"]
    compact_ended_ns = time.perf_counter_ns()
    evidence_manifest = _apply_evidence_updates(
        merged,
        base=migrated_base,
        base_affected=base_affected,
        replacement_shots=replacement_shots,
        evidence_updates=evidence_updates,
    )

    after_by_id = {_shot_id(row): row for row in merged_shots}
    changed_untouched = sorted(
        shot_id
        for shot_id, before_hash in before_hashes.items()
        if _semantic_shot_hash(after_by_id[shot_id]) != before_hash
    )
    if changed_untouched:
        raise RuntimeError(
            "delta_protected_semantics_changed: merge altered untouched shots "
            + ", ".join(changed_untouched)
        )

    full_bytes = len(json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    delta_input = {
        "affected_shot_uids": affected_uids,
        "replacement_shots": replacement_shots,
        "discarded_legacy_replacement_batch_count": discarded_legacy_batch_count,
        "evidence_updates": evidence_updates or {},
        "top_level_updates": updates,
    }
    delta_bytes = len(json.dumps(delta_input, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    manifest = {
        "affected_base_shot_ids": affected,
        "affected_base_shot_uids": affected_uids,
        "replacement_shot_ids": replacement_ids,
        "replacement_shot_uids": replacement_uids,
        "discarded_legacy_replacement_batch_count": discarded_legacy_batch_count,
        "untouched_shot_count": len(before_hashes),
        "untouched_semantic_hashes_verified": True,
        **evidence_manifest,
        "derived_fields_recomputed": [
            "累计时长",
            "total_duration",
        ],
        "schema_version": COMPACT_SCHEMA_VERSION,
        "schema_profile": COMPACT_SCHEMA_PROFILE,
        "materializer_contract": COMPACT_MATERIALIZER_CONTRACT,
        "materialization_passes": 1,
        "base_canonicalization_passes": 0,
        "full_table_canonicalization_passes": 1,
        "delta_replacement_normalization_passes": 1,
        "delta_replacement_normalized_shot_count": len(replacement_shots),
        **NO_DUPLICATE_GENERATION_EVIDENCE,
        "materialization_stage": "compact_canonical_only",
        "canonicalization_stage": "complete",
        "legacy_shot_uid_migration_count": uid_migration["legacy_uid_count"],
        "legacy_batch_uid_migration_count": 0,
        "delta_input_bytes": delta_bytes,
        "materialized_canonical_bytes": full_bytes,
        "compact_canonical_bytes": full_bytes,
        "payload_reduction_percent": round(max(0.0, 100.0 * (1.0 - delta_bytes / max(full_bytes, 1))), 2),
        "internal_stage_ms": {
            "materialize": round((compact_ended_ns - compact_started_ns) / 1_000_000, 3),
            "merge_total": round((time.perf_counter_ns() - merge_started_ns) / 1_000_000, 3),
        },
    }
    from .repair_dependencies import repair_dependency_plan
    manifest["dependency_plan"] = repair_dependency_plan(migrated_base, merged)
    return merged, manifest
