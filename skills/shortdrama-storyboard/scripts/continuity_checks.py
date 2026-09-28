"""Closed state equivalences; never infer unseen people or prop transfers."""
from __future__ import annotations

import re



def state_value(value):
    if isinstance(value, dict):
        return {key: state_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [state_value(item) for item in value]
    if isinstance(value, str):
        # Whole-value aliases only: no fuzzy name/position/holder comparison.
        aliases = {"已关闭": "关闭", "关着": "关闭", "闭合": "关闭",
                   "已打开": "打开", "开着": "打开"}
        return aliases.get(value.strip(), value.strip())
    return value


def state_equal(left, right):
    return state_value(left) == state_value(right)


def audit_adjacent_text(project, shots):
    """Check every actual text boundary when no production state proof exists.

    This is a bounded validator observation, never a fabricated production
    ledger. Only an explicit terminal state can stand for the left cut point;
    earlier actions and the left initial state cannot silently replace it.
    """
    from production_derivation import digest
    from projection_semantics import QUOTES, explicit_state_conflicts, field_sha256
    from visible_entities import project_entity_registry, resolve_visible_subjects

    fields = ("镜头编号", "场景连续性ID", "可见主体", "空间站位描述", "起始状态",
              "画面内容", "环境动态", "切镜/转场提示词", "光影", "光源来源", "光源方向", "色温倾向")
    people, props = project_entity_registry(project)
    pairs, checks = [], []
    for left, right in zip(shots, shots[1:]):
        left_id, right_id = (str(row.get("镜头编号") or "") for row in (left, right))
        pair_id = f"{left_id}->{right_id}"
        actual = {f"field:{sid}.{field}": shot.get(field)
                  for sid, shot in ((left_id, left), (right_id, right)) for field in fields}
        hashes = {key: field_sha256(value) for key, value in actual.items()}
        resolutions = [resolve_visible_subjects(shot.get("可见主体"), people=people,
            non_people=props, evidence_texts=[shot.get(field) for field in fields]) for shot in (left, right)]
        names = list(dict.fromkeys(name for row in resolutions for name in row.people))
        picture = str(left.get("画面内容") or "")
        # Preserve offsets into the actual field while excluding quoted claims.
        unquoted = QUOTES.sub(lambda match: " " * len(match.group()), picture)
        endpoints = list(re.finditer(r"(?:^|[。；;\n】])\s*(?:镜末|末态|结束时|至镜尾)\s*[:：]\s*", unquoted))
        endpoint = unquoted[endpoints[-1].end():].strip() if endpoints else ""
        start = str(right.get("起始状态") or "").strip()
        episodes = [re.match(r"^(EP\d+)-", sid) for sid in (left_id, right_id)]
        same_scene = bool(left.get("场景连续性ID") and left.get("场景连续性ID") == right.get("场景连续性ID")
                          and all(episodes) and episodes[0].group() == episodes[1].group())
        uncertain = re.compile(r"如果|倘若|可能|或许|准备|正要|将要|随后|然后|接着|之后|再")
        findings = []
        status = "unknown"
        reason = "缺可确定的左镜实际末态或右镜起态；不以起态、早先动作或作者元数据补作切点状态"
        if not same_scene:
            reason = "跨场或场景身份未绑定；已读取两侧实际文本与转场说明，时空及因果连续性仍待独立核对"
        elif endpoint and start and not uncertain.search(endpoint + start):
            findings = explicit_state_conflicts(endpoint, start, names)
            findings = [item.replace("静态起态冲突：E=", "相邻末态/起态冲突：左镜=").replace("，F=", "，右镜=") for item in findings]
            if findings:
                status, reason = "fail", "同一场景、同一人物的明确末态与次镜明确起态冲突"
            elif endpoint == start and not any(row.faults for row in resolutions):
                reason = "明示末态与次镜起态逐字相同，但文本一致不能证明状态可解析或完整连续；仍需独立核对"
            else:
                reason = "已比较两侧明示状态但不能完整解析或证明等价；未命中冲突不等于通过"
        result = {"pair_id": pair_id, "left_shot_id": left_id, "right_shot_id": right_id,
                  "continuity_status": status, "review_method": "explicit-adjacent-text-v1",
                  "reason": reason, "field_sha256": hashes, "actual_fields": actual,
                  "left_terminal_excerpt": endpoint, "right_initial_excerpt": start,
                  "left_terminal_offset": endpoints[-1].end() if endpoints else None,
                  "findings": findings, "input_mapping_findings": [fault for row in resolutions for fault in row.faults]}
        pairs.append(result)
        checks.append({"check_id": "adjacent_text_continuity", "layer": "空间连续",
                       "pair_id": pair_id, "shot_ids": [left_id, right_id], "status": status,
                       "reason": reason, "evidence": result, "field_sha256": hashes,
                       "findings": findings})
    pair_ids = [row["pair_id"] for row in pairs]
    return {"contract": "adjacent-pair-continuity-v1", "machine_review_contract": "explicit-adjacent-text-v1",
            "expected_pair_count": len(pairs), "reviewed_pair_count": len(pairs), "coverage_percent": 100.0,
            "pair_ids": pair_ids, "ordered_pair_sha256": digest(pair_ids), "pair_results": pairs,
            "passed_pair_count": sum(row["continuity_status"] == "pass" for row in pairs),
            "failed_pair_count": sum(row["continuity_status"] == "fail" for row in pairs),
            "unknown_pair_count": sum(row["continuity_status"] == "unknown" for row in pairs),
            "machine_review_complete": True, "proof_verified": False}, checks
