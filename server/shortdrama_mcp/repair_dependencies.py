"""Read-only repair dependencies derived from two actual candidate snapshots.

This is a recheck plan, never evidence that the listed checks have passed.
Unchanged shots remain untouched. A dependency may require rechecking a shot
without granting permission to regenerate it.
"""
from __future__ import annotations

from typing import Any

from .canonical import stable_json_hash


def _identity(shot: dict[str, Any]) -> str:
    return str(shot.get("shot_uid") or shot.get("镜头编号") or "")


def _timing_links(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = storyboard.get("prevention_evidence")
    audio = evidence.get("audio_workflow") if isinstance(evidence, dict) else None
    timing = audio.get("timing") if isinstance(audio, dict) else None
    utterances = timing.get("utterances") if isinstance(timing, dict) else None
    return [row for row in utterances if isinstance(row, dict)] if isinstance(utterances, list) else []


def repair_dependency_plan(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Describe downstream checks; do not edit candidates or certify closure.

Existing delta field/signature policy is shared lazily to avoid maintaining a
second, potentially weaker speech-change detector. Structural/ambiguous shot
identity expands to the complete candidate rather than guessing alignment.
"""
    from .delta import changed_shot_fields, _dialogue_signature

    old = [row for row in before.get("shots", []) if isinstance(row, dict)]
    new = [row for row in after.get("shots", []) if isinstance(row, dict)]
    old_ids, new_ids = [_identity(row) for row in old], [_identity(row) for row in new]
    unique = all(old_ids + new_ids) and len(set(old_ids)) == len(old_ids) and len(set(new_ids)) == len(new_ids)
    structural = not unique or old_ids != new_ids
    old_map, new_map = {_identity(row): row for row in old}, {_identity(row): row for row in new}
    changed, preserved, derived_only = [], [], []
    all_checks: set[str] = set()
    linked_shots: set[str] = set()
    audio_links: list[tuple[str, set[str]]] = []
    for storyboard, shot_rows in ((before, old), (after, new)):
        display_to_uid = {str(row.get("镜头编号") or ""): _identity(row) for row in shot_rows}
        for utterance in _timing_links(storyboard):
            source = utterance.get("source")
            source_id = source.get("shot_id") if isinstance(source, dict) else None
            segments = utterance.get("segments")
            segment_ids = [row.get("shot_id") for row in segments if isinstance(row, dict)] if isinstance(segments, list) else []
            uids = {display_to_uid.get(str(value), "") for value in [source_id, *segment_ids]}
            audio_links.append((str(utterance.get("id") or ""), uids - {""}))
    for uid in dict.fromkeys(old_ids + new_ids):
        left, right = old_map.get(uid, {}), new_map.get(uid, {})
        fields = changed_shot_fields(left, right)
        if not fields and uid in old_map and uid in new_map:
            preserved.append(uid)
            if left != right:
                derived_only.append(uid)
            continue
        checks: set[str] = set()
        reasons: list[str] = []
        if fields & {"镜头编号", "shot_uid", "episode_id", "scene_uid", "场景连续性ID"}:
            structural = True
        if _dialogue_signature(left.get("画面内容")) != _dialogue_signature(right.get("画面内容")):
            reasons.append("dialogue_or_speaker_changed")
            checks.update(("source_dialogue_and_knowledge", "voice_delivery", "speaker_lip_sync",
                           "listener_reaction", "speech_timeline_and_capacity"))
        if "画面内容" in fields:
            reasons.append("picture_or_performance_changed")
            checks.update(("visible_action_and_performance", "speaker_lip_sync", "listener_reaction",
                           "speech_timeline_and_capacity", "static_keyframe", "adjacent_continuity"))
        if "心理音轨" in fields:
            reasons.append("psychological_audio_changed")
            checks.update(("source_dialogue_and_knowledge", "psychological_audio_no_lip_sync",
                           "speech_timeline_and_capacity", "listener_knowledge_boundary"))
        if "音色" in fields:
            reasons.append("voice_delivery_changed")
            checks.update(("voice_delivery", "speech_timeline_and_capacity", "speaker_lip_sync", "listener_reaction"))
        if fields & {"空间站位描述", "起始状态", "可见主体", "镜头类型", "机位", "运镜方式", "参考图锚定"}:
            reasons.append("spatial_or_camera_state_changed")
            checks.update(("camera_and_visibility", "static_keyframe", "actor_and_prop_state",
                           "adjacent_continuity", "speaker_lip_sync", "listener_reaction"))
        if fields & {"时长", "转场类型", "切镜/转场提示词"}:
            reasons.append("duration_or_cut_changed")
            checks.update(("speech_timeline_and_capacity", "cross_shot_audio", "adjacent_continuity"))
        if fields & {"环境动态", "环境音效", "生成限制"}:
            checks.update(("audio_visual_constraints", "speech_timeline_and_capacity"))
        if fields & {"光影", "光源来源", "光源方向", "色温倾向"}:
            checks.add("scene_lighting_continuity")
        related: set[str] = {uid}
        pairs: set[tuple[str, str]] = set()
        if "adjacent_continuity" in checks or structural:
            for order in (old_ids, new_ids):
                if uid in order:
                    index = order.index(uid)
                    if index:
                        pairs.add((order[index - 1], uid))
                    if index + 1 < len(order):
                        pairs.add((uid, order[index + 1]))
            related.update(value for pair in pairs for value in pair)
        if "时长" in fields:
            # Changing an early duration shifts every later global timestamp.
            for order in (old_ids, new_ids):
                if uid in order:
                    related.update(order[order.index(uid):])
            checks.add("downstream_global_timeline")
        linked_utterances: set[str] = set()
        if checks & {"speech_timeline_and_capacity", "speaker_lip_sync", "downstream_global_timeline"}:
            # Fixed point makes the dependency closure independent of input
            # utterance order, including a continuation feeding another shot.
            previous_related: set[str] = set()
            while related != previous_related:
                previous_related = set(related)
                for identity, uids in audio_links:
                    if uids & related:
                        linked_utterances.add(identity)
                        related.update(uids)
                        checks.add("cross_shot_audio")
        all_checks.update(checks)
        linked_shots.update(related)
        changed.append({"shot_uid": uid, "shot_id": right.get("镜头编号", left.get("镜头编号")),
                        "changed_fields": sorted(fields), "reasons": reasons,
                        "required_rechecks": sorted(checks), "related_shot_uids": sorted(related),
                        "adjacent_pairs": [list(pair) for pair in sorted(pairs)],
                        "linked_utterance_ids": sorted(linked_utterances - {""}),
                        "status": "pending_recheck"})
    if structural:
        all_checks.add("complete_source_identity_mapping_and_continuity")
        linked_shots.update(old_ids + new_ids)
    def context(storyboard):
        evidence = storyboard.get("prevention_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        return {"source_lock": storyboard.get("source_lock"),
                **{key: evidence.get(key) for key in ("audio_workflow", "source_ledger", "source_decisions",
                                                     "cognition_matrix", "scene_state_ledger", "production_derivation")}}
    old_context, new_context = context(before), context(after)
    changed_context = sorted(key for key in old_context if stable_json_hash(old_context[key]) != stable_json_hash(new_context[key]))
    if changed_context:
        linked_shots.update(old_ids + new_ids)
        all_checks.add("changed_evidence_source_and_content_binding")
    if "audio_workflow" in changed_context:
        all_checks.update(("speech_timeline_and_capacity", "cross_shot_audio", "speaker_lip_sync"))
    return {
        "contract": "repair-dependency-plan-v1",
        "before_sha256": stable_json_hash(before), "after_sha256": stable_json_hash(after),
        "proof_source": "server_computed_snapshot_diff", "structural_change": structural,
        "changed_shots": changed, "preserved_shot_uids": preserved,
        "changed_context_blocks": changed_context,
        "derived_only_shot_uids": derived_only,
        "required_rechecks": sorted(all_checks), "recheck_shot_uids": sorted(linked_shots - {""}),
        "full_candidate_regression_required": True, "independent_audit_required": True,
        "required_audit_layer_count": 8, "grant_approval": False,
        "dependent_evidence_status": "requires_fresh_validation",
        "unchanged_content_policy": "recheck_dependencies_without_regenerating_correct_content",
        "prohibited_shortcuts": ["delete_or_rewrite_protected_dialogue", "speed_up_to_fit",
                                 "extend_shot_beyond_current_duration_contract", "reuse_stale_evidence_as_pass"],
    }
