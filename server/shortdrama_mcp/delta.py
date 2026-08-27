from __future__ import annotations

import copy
import json
import re
import time
from typing import Any

from .canonical import stable_json_hash


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
    "台词",
    "心理音轨",
    "心理音轨属性",
    "覆盖原文",
    "原文依据类型",
    "原文支撑句",
}
DELTA_EXECUTION_FIELDS = {
    "时长",
    "累计时长",
    "镜头类型",
    "机位",
    "运镜方式",
    "运镜主体",
    "运镜功能",
    "人物调度",
    "画面内容",
    "动作与表演",
    "音色",
    "发声方式",
    "标点语气",
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
    "生成批次",
}
SERVER_DERIVED_SHOT_FIELDS = {"累计时长"}
SHOT_PLAN_TRIGGER_FIELDS = {
    "时长",
    "镜头类型",
    "机位",
    "运镜方式",
    "运镜主体",
    "运镜功能",
    "人物调度",
    "画面内容",
    "动作与表演",
    "转场类型",
    "切镜/转场提示词",
    "参考图锚定",
    "生成批次",
}
PERFORMANCE_TRIGGER_FIELDS = {
    "人物调度",
    "动作与表演",
    "音色",
    "发声方式",
    "标点语气",
}
SCENE_STATE_TRIGGER_FIELDS = {
    "人物调度",
    "动作与表演",
    "环境动态",
    "光源方向",
    "参考图锚定",
}
EVIDENCE_BLOCKS = {
    "shot_plan",
    "performance_chains",
    "scene_state_ledger",
}
EVIDENCE_BINDING_FIELDS = {
    "shot_uid",
    "shot_semantic_sha256",
    "delta_evidence_revision",
}


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


def _number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"delta_numeric_invalid: {field} must be numeric") from exc
    if result < 0:
        raise ValueError(f"delta_numeric_invalid: {field} must be non-negative")
    return result


def _clean_number(value: float) -> int | float:
    rounded = round(value, 3)
    return int(rounded) if rounded.is_integer() else rounded


def _materialize_seedance_prompt(batch: dict[str, Any]) -> str:
    """Compile the redundant final prompt from caller-supplied atomic fields.

    The compiler never invents plot, action, dialogue, sound, lighting, or
    camera facts.  It only labels and joins the independently supplied fields,
    so delaying this expansion reduces model output without weakening the final
    blank-context Seedance contract.
    """

    fields = (
        ("起始画面", "起始画面"),
        ("起始表演状态", "起始表演状态"),
        ("动作过程", "动作过程"),
        ("听话反应", "听话反应"),
        ("结束状态", "结束状态"),
        ("摄影机", "摄影机"),
        ("声音", "声音"),
        ("环境动态", "环境动态"),
        ("光影", "光影"),
        ("生成限制", "生成限制"),
    )
    missing = [field for field, _label in fields if not str(batch.get(field) or "").strip()]
    if missing:
        raise ValueError(
            "delta_materialization_input_missing: Seedance atomic fields missing "
            + ", ".join(missing)
        )
    parts = ["Seedance 2.5空白上下文独立执行。"]
    parts.extend(f"{label}：{str(batch[field]).strip()}。" for field, label in fields)
    prompt = "".join(parts)
    if len(prompt) > 60_000:
        raise ValueError("delta_materialization_too_large: compiled Seedance prompt exceeds 60000 characters")
    return prompt


def _semantic_shot_hash(shot: dict[str, Any]) -> str:
    value = {
        key: copy.deepcopy(item)
        for key, item in shot.items()
        if key not in {"累计时长", "shot_uid", "scene_uid", "episode_id"}
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


def _evidence_content_hash(row: dict[str, Any]) -> str:
    return stable_json_hash(
        {key: value for key, value in row.items() if key not in EVIDENCE_BINDING_FIELDS}
    )


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
        if key not in EVIDENCE_BLOCKS
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

    untouched_hashes_after = {
        key: stable_json_hash(value)
        for key, value in merged_evidence.items()
        if key not in EVIDENCE_BLOCKS
    }
    if untouched_hashes_before != untouched_hashes_after:
        raise RuntimeError("delta_evidence_global_changed: immutable evidence blocks changed")
    return {
        "updated_evidence_counts": updated_counts,
        "global_evidence_hashes_verified": True,
    }


def ensure_shot_uids(storyboard: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copy with stable internal shot identities.

    Legacy 0.3.0 artifacts have only a display shot number.  Their first 0.4.0
    read or delta operation receives a deterministic UID derived from the
    immutable legacy canonical and ordinal.  Once stored in a successor, the
    UID survives display-number changes and future deltas.
    """

    migrated = copy.deepcopy(storyboard)
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
    shot_episode_by_id = {
        _shot_id(shot): str(shot.get("episode_id") or "")
        for shot in shots
        if isinstance(shot, dict)
    }
    batches = migrated.get("batches")
    batch_manifest: list[dict[str, Any]] = []
    legacy_batch_count = 0
    used_batch_uids: set[str] = set()
    if isinstance(batches, list):
        for index, batch in enumerate(batches):
            if not isinstance(batch, dict):
                continue
            covered = _covered_shots(batch)
            episode_ids = {
                shot_episode_by_id[shot_id]
                for shot_id in covered
                if shot_id in shot_episode_by_id and shot_episode_by_id[shot_id]
            }
            explicit_episode = str(batch.get("episode_id") or "").strip()
            if explicit_episode:
                episode_ids.add(explicit_episode)
            if len(episode_ids) != 1:
                raise ValueError(
                    f"batch_episode_ambiguous: {_batch_id(batch) or index + 1} must cover exactly one episode"
                )
            episode_id = next(iter(episode_ids))
            batch["episode_id"] = episode_id
            batch_uid = str(batch.get("batch_uid") or "").strip()
            if not batch_uid:
                batch_uid = "BATCH-" + stable_json_hash(
                    {"episode_id": episode_id, "display_batch_id": _batch_id(batch)}
                )[:20]
                batch["batch_uid"] = batch_uid
                legacy_batch_count += 1
            if batch_uid in used_batch_uids:
                raise ValueError(f"batch_uid_collision: duplicate internal batch uid {batch_uid}")
            used_batch_uids.add(batch_uid)
            batch_manifest.append(
                {
                    "batch_uid": batch_uid,
                    "batch_id": _batch_id(batch),
                    "ordinal": index,
                    "episode_id": episode_id,
                }
            )
    return migrated, {
        "shot_uids": manifest,
        "batch_uids": batch_manifest,
        "legacy_uid_count": legacy_count,
        "legacy_batch_uid_count": legacy_batch_count,
    }


def merge_storyboard_delta(
    base: dict[str, Any],
    *,
    affected_shot_uids: list[str],
    replacement_shots: list[dict[str, Any]],
    replacement_batches: list[dict[str, Any]],
    evidence_updates: dict[str, Any] | None = None,
    top_level_updates: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge one contiguous affected range and materialize the final canonical.

    Unaffected semantic shot fields and unaffected batches are copied from the
    exact base canonical.  Only cumulative time is globally recomputed because
    it is a derived statistic.  A final full-table validator still decides
    whether the resulting artifact is production-valid.
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
    if not isinstance(replacement_batches, list) or not replacement_batches:
        raise ValueError("delta_replacement_required: replacement_batches must be non-empty")

    migrated_base, uid_migration = ensure_shot_uids(base)
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
    replacement_shots = [copy.deepcopy(row) for row in replacement_shots]
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

    base_batches = migrated_base.get("batches")
    if not isinstance(base_batches, list):
        raise ValueError("delta_base_invalid: base batches must be an array")
    retained_batches: list[dict[str, Any]] = []
    removed_batch_ids: set[str] = set()
    batch_insert_index: int | None = None
    affected_set = set(affected)
    for batch_index, row in enumerate(base_batches):
        if not isinstance(row, dict):
            raise ValueError("delta_base_invalid: every base batch must be an object")
        row = copy.deepcopy(row)
        if not str(row.get("batch_uid") or "").strip():
            row["batch_uid"] = "BATCH-" + stable_json_hash(
                {
                    "legacy_canonical": stable_json_hash(base),
                    "ordinal": batch_index,
                    "display_batch_id": _batch_id(row),
                }
            )[:20]
        covered = set(_covered_shots(row))
        hit = covered & affected_set
        if hit and covered - affected_set:
            raise ValueError(
                "delta_scope_must_expand: a base batch crosses affected and protected shots: "
                + _batch_id(row)
            )
        if hit:
            if batch_insert_index is None:
                batch_insert_index = len(retained_batches)
            removed_batch_ids.add(_batch_id(row))
        else:
            retained_batches.append(row)

    if any(not isinstance(row, dict) for row in replacement_batches):
        raise ValueError("delta_replacement_invalid: every replacement batch must be an object")
    replacement_batch_ids = [_batch_id(row) for row in replacement_batches]
    if any(not value for value in replacement_batch_ids) or len(set(replacement_batch_ids)) != len(replacement_batch_ids):
        raise ValueError("delta_replacement_invalid: replacement batch ids must be unique and non-empty")
    retained_batch_ids = {_batch_id(row) for row in retained_batches}
    batch_collisions = sorted(set(replacement_batch_ids) & retained_batch_ids)
    if batch_collisions:
        raise ValueError("delta_batch_id_collision: replacement batch ids collide with retained ids " + ", ".join(batch_collisions))

    coverage_counts = {shot_id: 0 for shot_id in replacement_ids}
    materialized_batches: list[dict[str, Any]] = []
    replacement_by_id = {_shot_id(row): row for row in replacement_shots}
    materialize_started_ns = time.perf_counter_ns()
    for batch_index, raw in enumerate(replacement_batches):
        row = copy.deepcopy(raw)
        if str(row.get("batch_uid") or "").strip():
            raise ValueError("delta_batch_uid_untrusted: replacement batch_uid is server-generated")
        row["batch_uid"] = "BATCH-" + stable_json_hash(
            {
                "affected_uids": affected_uids,
                "ordinal": batch_index,
                "batch": {key: value for key, value in row.items() if key != "batch_uid"},
            }
        )[:20]
        covered = _covered_shots(row)
        if not covered:
            raise ValueError(f"delta_batch_invalid: {_batch_id(row)} covers no shots")
        unknown = sorted(set(covered) - set(replacement_ids))
        if unknown:
            raise ValueError(
                f"delta_batch_scope_violation: {_batch_id(row)} covers non-replacement shots "
                + ", ".join(unknown)
            )
        for shot_id in covered:
            coverage_counts[shot_id] += 1
        row["覆盖镜头"] = covered
        row["时长"] = _clean_number(
            sum(_number(replacement_by_id[shot_id].get("时长"), field=f"{shot_id}.时长") for shot_id in covered)
        )
        episode_ids = {
            str(replacement_by_id[shot_id].get("episode_id") or "").strip()
            for shot_id in covered
            if str(replacement_by_id[shot_id].get("episode_id") or "").strip()
        }
        if len(episode_ids) == 1:
            derived_episode = next(iter(episode_ids))
            supplied_episode = str(row.get("episode_id") or "").strip()
            if supplied_episode and supplied_episode.casefold() != derived_episode.casefold():
                raise ValueError(
                    f"delta_batch_scope_violation: {_batch_id(row)} moves across episode identity"
                )
            row["episode_id"] = derived_episode
        row["独立执行提示词"] = _materialize_seedance_prompt(row)
        materialized_batches.append(row)
    materialize_ended_ns = time.perf_counter_ns()
    invalid_coverage = sorted(shot_id for shot_id, count in coverage_counts.items() if count != 1)
    if invalid_coverage:
        raise ValueError(
            "delta_batch_coverage_invalid: every replacement shot must be covered exactly once: "
            + ", ".join(invalid_coverage)
        )
    replacement_batch_set = set(replacement_batch_ids)
    for shot in replacement_shots:
        ref = str(shot.get("生成批次") or "").strip()
        if ref not in replacement_batch_set:
            raise ValueError(
                f"delta_batch_reference_invalid: {_shot_id(shot)} references {ref!r} outside replacement batches"
            )

    merged = copy.deepcopy(migrated_base)
    updates = top_level_updates or {}
    if not isinstance(updates, dict):
        raise ValueError("delta_top_level_invalid: top_level_updates must be an object")
    if updates:
        raise ValueError(
            "delta_top_level_requires_full_commit: 0.4.0 delta preserves every top-level evidence block; "
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
    cumulative = 0.0
    for shot in merged_shots:
        cumulative += _number(shot.get("时长"), field=f"{_shot_id(shot)}.时长")
        shot["累计时长"] = _clean_number(cumulative)
    merged["shots"] = merged_shots
    insert_at = len(retained_batches) if batch_insert_index is None else batch_insert_index
    merged["batches"] = (
        retained_batches[:insert_at]
        + materialized_batches
        + retained_batches[insert_at:]
    )
    merged["total_duration"] = _clean_number(cumulative)
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
        "replacement_batches": replacement_batches,
        "evidence_updates": evidence_updates or {},
        "top_level_updates": updates,
    }
    delta_bytes = len(json.dumps(delta_input, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    manifest = {
        "affected_base_shot_ids": affected,
        "affected_base_shot_uids": affected_uids,
        "replacement_shot_ids": replacement_ids,
        "replacement_shot_uids": replacement_uids,
        "removed_batch_ids": sorted(removed_batch_ids),
        "replacement_batch_ids": replacement_batch_ids,
        "untouched_shot_count": len(before_hashes),
        "untouched_semantic_hashes_verified": True,
        **evidence_manifest,
        "derived_fields_recomputed": ["累计时长", "batches[].时长", "batches[].独立执行提示词", "total_duration"],
        "materialization_stage": "complete",
        "legacy_shot_uid_migration_count": uid_migration["legacy_uid_count"],
        "legacy_batch_uid_migration_count": uid_migration.get("legacy_batch_uid_count", 0),
        "delta_input_bytes": delta_bytes,
        "materialized_canonical_bytes": full_bytes,
        "payload_reduction_percent": round(max(0.0, 100.0 * (1.0 - delta_bytes / max(full_bytes, 1))), 2),
        "internal_stage_ms": {
            "materialize": round((materialize_ended_ns - materialize_started_ns) / 1_000_000, 3),
            "merge_total": round((time.perf_counter_ns() - merge_started_ns) / 1_000_000, 3),
        },
    }
    return merged, manifest
