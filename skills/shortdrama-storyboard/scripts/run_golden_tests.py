#!/usr/bin/env python3
"""Execute R10.1 compact-master Golden Tests against validate_storyboard.py."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


COMPACT_SHOT_FIELDS = [
    "镜头编号", "时长", "累计时长", "功能类型", "场景连续性ID", "镜头类型", "可见主体",
    "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
    "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
    "转场类型", "切镜/转场提示词", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
]
COMPACT_MOTHER_COLUMNS = [
    "分镜序号", "时长", "累计时长", "功能类型/场景连续性ID", "正式分镜母版",
    "GPT关键帧生成指令", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
]
DEPRECATED_SHOT_FIELDS = [
    "运镜主体", "运镜功能", "人物调度", "动作与表演", "台词", "发声方式", "标点语气",
    "心理音轨属性", "生成批次",
]
STATE_VECTOR_FIELDS = [
    "人物位置", "身体朝向", "视线落点", "手持道具", "门窗状态", "动作进度", "光源方向",
]
BOUNDARY_EXPLANATION_RE = re.compile(
    r"切|转入|转到|换场|场景|地点|时空|翌日|次日|稍后|片刻|另一|新空间|跨集|集间"
)


def _stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_adjacent_continuity_audit(
    shots: list[dict[str, Any]],
    scene_states: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the exact N-1 pair proof used by otherwise-valid fixtures."""

    by_shot = {str(row.get("shot_id")): row for row in scene_states}
    pairs: list[dict[str, Any]] = []
    for ordinal in range(max(len(shots) - 1, 0)):
        left = shots[ordinal]
        right = shots[ordinal + 1]
        left_id = str(left.get("镜头编号"))
        right_id = str(right.get("镜头编号"))
        left_state = by_shot[left_id]
        right_state = by_shot[right_id]
        left_episode = left_id[2:4] if re.fullmatch(r"EP\d{2}-\d{3}", left_id) else "LEGACY"
        right_episode = right_id[2:4] if re.fullmatch(r"EP\d{2}-\d{3}", right_id) else "LEGACY"
        left_scene = str(left_state.get("scene_id", left.get("场景连续性ID", "")))
        right_scene = str(right_state.get("scene_id", right.get("场景连续性ID", "")))
        boundary_type = (
            "episode_cut"
            if left_episode != right_episode
            else "scene_cut"
            if left_scene != right_scene
            else "same_scene"
        )
        prior_end = left_state["end_state"]
        current_start = right_state["start_state"]
        closed = prior_end == current_start
        transition_explanation = right_state.get("transition_explanation")
        if boundary_type == "same_scene":
            continuity_status = "closed" if closed else "fail_current"
        else:
            continuity_status = (
                "explained_transition"
                if BOUNDARY_EXPLANATION_RE.search(
                    str(transition_explanation or "")
                )
                else "fail_current"
            )
        pairs.append({
            "pair_id": f"{left_id}->{right_id}",
            "left_shot_id": left_id,
            "right_shot_id": right_id,
            "boundary_type": boundary_type,
            "left_end_state_sha256": _stable_sha256(prior_end),
            "right_start_state_sha256": _stable_sha256(current_start),
            "reviewed_fields": list(STATE_VECTOR_FIELDS),
            "continuity_status": continuity_status,
            "transition_explanation": transition_explanation,
        })
    pair_ids = [row["pair_id"] for row in pairs]
    return {
        "contract": "adjacent-pair-continuity-v1",
        "expected_pair_count": len(pairs),
        "reviewed_pair_count": len(pairs),
        "coverage_percent": 100,
        "ordered_pair_sha256": _stable_sha256(pair_ids),
        "pairs": pairs,
    }


def normalize_fixture_generation_limit(value: str) -> str:
    """Upgrade a proven legacy negative BGM list without changing intent."""

    text = value.replace("BGM", "额外配乐")
    if "额外配乐" not in text or "禁止额外配乐" in text:
        return text
    text = text.replace("额外配乐", "")
    text = re.sub(r"[、，,]{2,}", "、", text)
    text = re.sub(r"禁止[、，,与和]+", "禁止", text)
    text = re.sub(r"[、，,与和]+(?=[；;。]|$)", "", text)
    text = text.strip(" ；;，,。")
    return f"{text}；禁止额外配乐" if text else "禁止额外配乐"


def project_root() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent


def load_validator():
    path = Path(__file__).resolve().parent / "validate_storyboard.py"
    spec = importlib.util.spec_from_file_location("r10_validate_storyboard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_parent(obj: Any, parts: list[str]) -> tuple[Any, str]:
    current = obj
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current, parts[-1]


def apply_mutation(obj: Any, mutation: dict[str, Any]) -> None:
    parts = mutation["path"].split(".")
    parent, key = resolve_parent(obj, parts)
    op = mutation["op"]
    if op == "set":
        if isinstance(parent, list):
            parent[int(key)] = copy.deepcopy(mutation.get("value"))
        else:
            parent[key] = copy.deepcopy(mutation.get("value"))
    elif op == "delete":
        if isinstance(parent, list):
            del parent[int(key)]
        else:
            parent.pop(key, None)
    elif op == "append":
        target = parent[int(key)] if isinstance(parent, list) else parent[key]
        target.append(copy.deepcopy(mutation.get("value")))
    else:
        raise ValueError(f"unsupported mutation op: {op}")


def upgrade_fixture_to_compact(obj: dict[str, Any]) -> None:
    """Migrate legacy Golden source fixtures to the R10.1 validator input.

    Mutations are intentionally applied before this adapter, so every existing
    regression test still changes the same semantic fact while the validator
    only ever receives the compact 27-field shape.
    """
    legacy_batches = obj.get("batches") if isinstance(obj.get("batches"), list) else []
    first_batch = legacy_batches[0] if legacy_batches and isinstance(legacy_batches[0], dict) else {}
    legacy_prompt = str(first_batch.get("独立执行提示词", ""))
    missing_legacy_end = bool(first_batch) and "结束状态" not in first_batch

    obj["schema_version"] = "R10.1"
    declared_order = obj.get("field_order")
    if not isinstance(declared_order, list) or not declared_order or declared_order[0] == "镜头编号":
        obj["field_order"] = copy.deepcopy(COMPACT_SHOT_FIELDS)
    columns = obj.get("mother_template_columns")
    if isinstance(columns, list):
        if any("Seedance 2.0" in str(value) for value in columns):
            # Keep the obsolete declaration so the new duplicate-generation
            # gate proves it is rejected.
            pass
        else:
            obj["mother_template_columns"] = copy.deepcopy(COMPACT_MOTHER_COLUMNS)

    for shot in obj.get("shots", []):
        if not isinstance(shot, dict):
            continue
        scheduling = str(shot.get("人物调度") or shot.get("空间站位描述") or "人物与道具保持既定空间关系")
        action = str(shot.get("动作与表演") or "")
        picture = str(shot.get("画面内容") or "")
        dialogue = str(shot.get("台词") or "")
        delivery = str(shot.get("发声方式") or "")
        punctuation = str(shot.get("标点语气") or "")
        visible = str(shot.get("可见主体") or "角色")
        speaker = "女主" if "女主" in visible else re.split(r"[、，,；;\s]", visible, maxsplit=1)[0] or "角色"

        sections = [picture, action]
        if dialogue and not dialogue.startswith(("无", "不适用")):
            sections.append(f"【{speaker}】{dialogue}")
            sections.append(f"发声说明：{delivery}")
            sections.append(f"标点按停顿、上扬、重落或收住表现：{punctuation}")
        if legacy_prompt and any(term in legacy_prompt for term in ("上一镜", "关键帧", "继续原动作", "加入BGM", "背景音乐")):
            sections.append(legacy_prompt)
        shot["画面内容"] = "；".join(section.strip("； ") for section in sections if section.strip("； "))
        shot["空间站位描述"] = scheduling
        shot["起始状态"] = str(shot.get("起始状态") or ("人物位置、朝向、视线、姿态与道具起始状态明确；" + scheduling))
        if missing_legacy_end:
            shot.pop("起始状态", None)

        motion = str(shot.get("运镜方式") or "")
        if not any(term in motion for term in ("无运镜", "固定镜头", "静态镜头", "固定机位")):
            subject = str(shot.get("运镜主体") or "无")
            function = str(shot.get("运镜功能") or "无")
            shot["运镜方式"] = f"{motion}，摄影机围绕{subject}运动，用于{function}"

        psych = str(shot.get("心理音轨") or "")
        psych_attr = str(shot.get("心理音轨属性") or "")
        if psych and not psych.startswith(("无", "不适用")) and psych_attr:
            shot["心理音轨"] = f"{psych}；{psych_attr}"
        if isinstance(shot.get("生成限制"), str):
            # New writes use the unambiguous Chinese phrase.  Keep active BGM
            # requests in other fields untouched so the negative fixture still
            # proves the literal-token hard gate.
            shot["生成限制"] = normalize_fixture_generation_limit(
                shot["生成限制"]
            )
        for field in DEPRECATED_SHOT_FIELDS:
            shot.pop(field, None)

    obj.pop("batches", None)


def inject_pg1_fixture_evidence(obj: dict[str, Any]) -> None:
    """Build valid PG1 evidence so tests can mutate one prevention rule at a time."""
    shots = obj.get("shots", [])
    batches = obj.get("batches", [])
    obj["workflow_profile"] = "R10-PG1"
    source_ledger = []
    scene_states = []
    shot_plan = []
    performance_chains = []
    stable_state = {
        "人物位置": "门边至桌前的既定位置",
        "身体朝向": "面向桌面",
        "视线落点": "桌面信件",
        "手持道具": "无",
        "门窗状态": "门打开、窗关闭",
        "动作进度": "停稳后观察",
        "光源方向": "画面左侧至右侧",
    }
    for index, shot in enumerate(shots, 1):
        if isinstance(shot, dict) and isinstance(shot.get("生成限制"), str):
            shot["生成限制"] = normalize_fixture_generation_limit(
                shot["生成限制"]
            )
        visible_text = f"{shot.get('镜头类型', '')} {shot.get('可见主体', '')}"
        face_visible = (
            float(shot.get("时长") or 0) >= 2.0
            and any(term in visible_text for term in (
                "面部", "眼睛", "眼神", "眼睑", "眼球", "双眼", "侧脸", "人物反应",
                "大特写", "特写", "近景", "中近景", "胸部以上", "腰部以上",
            ))
            and not ("书页" in visible_text and "人物反应" not in visible_text)
        )
        if face_visible:
            # These source fixtures have one face-visible protagonist; a
            # listener's shoulder is not a second visible pair of eyes.  Keep
            # the eye chain owned by that protagonist before defect mutations.
            eye_subject = "女主" if "女主" in visible_text else "男主" if "男主" in visible_text else re.split(
                r"[、，,；;\s]", str(shot.get("可见主体") or "人物"), maxsplit=1
            )[0]
            eye_chain = (
                f"{eye_subject}视线从门框移向桌面信件，眼球先完成轻微焦点调整，头部随后轻微跟转；"
                f"{eye_subject}在0.4秒换焦后自然短眨一次，重新睁眼后上眼睑略微下压；"
                f"镜尾{eye_subject}视线最终落在信件上"
            )
            picture = str(shot.get("画面内容") or "")
            if not all(term in picture for term in (
                eye_subject, "眼球先", "上眼睑", "0.4秒", "视线最终落在"
            )):
                shot["画面内容"] = f"{picture.rstrip('；')}；{eye_chain}"
            dialogue_value = str(shot.get("台词") or "")
            if (
                dialogue_value and not dialogue_value.startswith(("无", "不适用"))
            ) or re.search(r"说道|【[\u3400-\u9fffA-Za-z·]{1,20}】|重音|加重", str(shot.get("画面内容") or "")):
                shot["画面内容"] += f"；{eye_subject}在台词关键词与重音期间不眨眼"
            start = str(shot.get("起始状态") or "人物位置、朝向、视线、姿态与道具起始状态明确")
            if not ("双眼自然睁开" in start and "不处于半眨眼" in start):
                shot["起始状态"] = f"{start}；双眼自然睁开，不处于半眨眼瞬间"
            limits = str(shot.get("生成限制") or "").rstrip("；")
            for requirement in (
                "眼神自然聚焦，保留轻微眼球焦点调整和眼睑松紧变化",
                "禁止无依据全程不眨眼", "禁止僵直瞪视", "禁止高频机械眨眼",
            ):
                if requirement not in limits:
                    limits += f"；{requirement}"
            shot["生成限制"] = limits
            if re.search(r"两人|二人|双人|三人|多人|群像", str(shot.get("可见主体") or "")):
                shot["画面内容"] += "；多人眨眼错开、不同时闭眼"
                shot["生成限制"] += "；禁止多人同步眨眼"
        shot_id = str(shot.get("镜头编号"))
        support = str(shot.get("原文支撑句") or shot.get("覆盖原文") or f"测试源文{index}")
        source_ledger.append({
            "source_id": f"SRC-{index:03d}",
            "source_type": "关键事件",
            "source_text": support,
            "speaker": "叙述或原台词角色",
            "treatment": "保留",
            "target_shots": [shot_id],
            "authorization": "不适用：未删除",
        })
        scene_states.append({
            "scene_id": str(shot.get("场景连续性ID")),
            "shot_id": shot_id,
            "start_state": copy.deepcopy(stable_state),
            "end_state": copy.deepcopy(stable_state),
            "transition_explanation": "不适用：状态连续",
        })
        is_detail = "特写" in str(shot.get("镜头类型", ""))
        shot_plan.append({
            "shot_id": shot_id,
            "core_function": str(shot.get("功能类型") or "动作推进"),
            "viewpoint_count": 1,
            "main_camera_count": 1,
            "camera_position": str(shot.get("机位") or "平视正侧45度"),
            "main_action_stage": str(shot.get("画面内容") or "人物完成当前动作阶段"),
            "main_action_stage_count": 1,
            "transition_function": "动作因果",
            "is_detail_shot": is_detail,
            "detail_shot_function": "展示关键信息" if is_detail else "不适用：非特写",
        })
        performance_chains.append({
            "shot_id": shot_id,
            "stimulus": "看见或听见当前剧情信息",
            "listen_or_observe_reaction": "先停顿并将注意力转向信息来源",
            "gaze_path": "信息来源到目标人物或道具",
            "micro_expression": "嘴角收住，眉眼产生可见变化",
            "ecological_action": "手部随情绪自然停住",
            "body_or_breath": "肩颈与呼吸产生轻微连带",
            "end_state": "动作收束并保留明确视线落点",
        })
    obj["prevention_evidence"] = {
        "scope_lock": {
            "authoritative_source": "Golden Test fixture",
            "task_scope": "fixture全部镜头",
            "protected_scope": "fixture范围外不得修改",
            "active_rule_revision": "R10-PG1",
        },
        "source_ledger": source_ledger,
        "change_lock": {"must_include": [], "must_exclude": []},
        "cognition_matrix": [{
            "character": "测试角色",
            "knows": ["当前可见信息"],
            "does_not_know": ["尚未揭示的信息"],
            "relationship_stage": "测试既定阶段",
            "emotion_stage": "当前节拍情绪",
            "forbidden": ["不得提前泄露后文"],
        }],
        "scene_state_ledger": scene_states,
        "adjacent_continuity_audit": build_adjacent_continuity_audit(shots, scene_states),
        "shot_plan": shot_plan,
        "performance_chains": performance_chains,
        "audio_workflow": {
            "seedance_bgm_policy": "禁止额外配乐",
            "bgm_cue_stage": "画面锁定后独立设计",
            "scene_emotional_key": "服从当前剧情节拍",
            "dialogue_ducking": "对白优先，音乐后期避让",
        },
        "audit_layers": [
            "源文对白",
            "机位可见性",
            "空间连续",
            "表演链",
            "镜头节奏",
            "正式母版单镜执行完整性",
            "禁项与声音",
            "统计闭合",
        ],
        "repair_history": [],
    }
    for batch in batches:
        batch.setdefault("起始画面", "人物、场景、站位、姿态、道具与受光均明确")
        batch.setdefault("起始表演状态", "视线、头部、手部、重心和呼吸均明确")
        batch.setdefault("动作过程", "动作按时间顺序完成并包含身体连带")
        batch.setdefault("听话反应", "听话人先接收信息再产生可见反应")
        batch.setdefault("结束状态", "人物位置、姿态、视线与道具状态明确")
        batch.setdefault("摄影机", "摄影机位置、景别、角度与主运镜明确")
        batch.setdefault("声音", "对白、环境声与衣料声明确，禁止BGM")
        batch.setdefault("环境动态", "场景动态连续且不抢人物表演")
        batch.setdefault("光影", "光源、方向与色温连续")
        batch.setdefault("独立执行提示词", "从明确起始状态开始，按顺序完成动作并停在明确结束状态；禁止BGM与字幕")


def inject_chapter_intake_evidence(obj: dict[str, Any]) -> None:
    """Attach one valid novel-to-storyboard intake package for conditional tests."""
    value_axes = {
        "causal_necessity": 5,
        "information_value": 5,
        "character_or_relationship_change": 4,
        "conflict_reversal_payoff": 3,
        "emotional_setup_or_payoff": 4,
        "hook_value": 3,
        "cinematic_function": 4,
        "redundancy": 1,
        "production_feasibility": 5,
    }
    obj["source_ingestion"] = {
        "mode": "chapter_to_storyboard",
        "chapter_id": "CH-GOLDEN-001",
        "complete_read": True,
        "chunk_count": 2,
        "chunks_read": 2,
        "boundary_context": {
            "previous_end": "上一章女主已察觉异样",
            "current_start": "女主推门进入房间",
            "current_end": "女主确认有人跟踪并决定离开",
            "next_start": "下一章女主开始转移",
        },
        "chapter_goal": "让观众理解女主发现跟踪并决定离开的完整因果。",
        "events": [
            {
                "event_id": "EV-001",
                "source_unit_ids": ["SU-001"],
                "value_grade": "A",
                "causal_role": "原因：发现异常线索",
                "target_beats": ["BEAT-001"],
            },
            {
                "event_id": "EV-002",
                "source_unit_ids": ["SU-002"],
                "value_grade": "S",
                "causal_role": "行动与结果：确认跟踪并决定离开",
                "target_beats": ["BEAT-002"],
            },
        ],
        "causal_links": [
            {"from_event_id": "EV-001", "to_event_id": "EV-002", "relation": "线索导致决定"}
        ],
        "character_state_changes": [
            {
                "character": "女主",
                "start_state": "怀疑但不确定危险来源",
                "trigger_event_ids": ["EV-001", "EV-002"],
                "end_state": "确认被跟踪并决定离开",
                "cognition_before": "不知道跟踪是否真实",
                "cognition_after": "知道有人正在跟踪",
                "forbidden_knowledge": ["不知道跟踪者真实身份"],
            }
        ],
        "source_treatment_ledger": [
            {
                "source_unit_id": "SU-001",
                "source_type": "关键事件",
                "source_text": "她在门缝里发现新鲜泥印。",
                "value_axes": copy.deepcopy(value_axes),
                "value_grade": "A",
                "cinematic_function": "建立异常线索和空间方向",
                "treatment": "保留",
                "target_beats": ["BEAT-001"],
                "reason": "是后续决定的原因",
                "authorization": "不适用：未删除",
                "redundancy_evidence": "不适用：不重复",
                "retained_function_in": "不适用：未删除",
            },
            {
                "source_unit_id": "SU-002",
                "source_type": "关键对白与决定",
                "source_text": "有人跟着我，我得马上离开。",
                "value_axes": copy.deepcopy(value_axes),
                "value_grade": "S",
                "cinematic_function": "完成认知变化并给出决定",
                "treatment": "保留",
                "target_beats": ["BEAT-002"],
                "reason": "保护因果、信息与人物决定",
                "authorization": "不适用：未删除",
                "redundancy_evidence": "不适用：不重复",
                "retained_function_in": "不适用：未删除",
            },
        ],
        "protected_dialogue": [
            {
                "source_unit_id": "SU-002",
                "speaker": "女主",
                "text": "有人跟着我，我得马上离开。",
                "functions": ["信息", "认知变化", "决定", "因果桥"],
                "target_beats": ["BEAT-002"],
            }
        ],
        "adaptation_budget": {
            "target_duration_seconds": 12,
            "dialogue_seconds": 5,
            "action_seconds": 5,
            "transition_seconds": 2,
            "shot_count_min": 2,
            "shot_count_max": 4,
        },
        "beat_plan": [
            {
                "beat_id": "BEAT-001",
                "core_stage": "发现异常线索",
                "event_ids": ["EV-001"],
                "source_unit_ids": ["SU-001"],
                "entry_state": "尚未确认危险",
                "exit_state": "怀疑升级",
                "target_duration_seconds": 5,
                "shot_count_range": [1, 2],
                "function": "原因与悬念",
            },
            {
                "beat_id": "BEAT-002",
                "core_stage": "确认危险并作出决定",
                "event_ids": ["EV-002"],
                "source_unit_ids": ["SU-002"],
                "entry_state": "怀疑升级",
                "exit_state": "决定离开",
                "target_duration_seconds": 7,
                "shot_count_range": [1, 2],
                "function": "认知变化、决定和章节结果",
            },
        ],
        "visualization_license": [
            {
                "description": "女主看到泥印后呼吸短暂停住，手指停在门框",
                "source_unit_ids": ["SU-001"],
                "category": "allowed",
                "plot_change": False,
            }
        ],
        "new_plot_events": [],
        "uncertainty_status": "clear",
        "coverage_metrics": {
            "core_event_coverage_percent": 100,
            "causal_link_coverage_percent": 100,
            "character_state_change_coverage_percent": 100,
            "protected_dialogue_coverage_percent": 100,
            "source_treatment_percent": 100,
        },
    }
    # The chapter's protected utterance must exist in the actual authored shot,
    # not just in a declaration of 100% coverage.  This is a complete positive
    # baseline; callers apply their missing-dialogue/coverage mutations later.
    shot = obj["shots"][0]
    shot_id = str(shot["镜头编号"])
    dialogue = "有人跟着我，我得马上离开。"
    shot["功能类型"] = "对白推进"
    shot["画面内容"] = (
        "【0.0—0.6秒】女主发现门缝泥印，视线从门框移向泥印，眼球先轻微换焦，"
        "头部随后轻跟，上眼睑略压，在0.4秒换焦后自然眨眼一次；"
        f"【0.6—3.6秒】女主肩颈微紧，手指停在门框，呼吸收稳；女主说道：“{dialogue}”"
        "女主口型与对白一致，不看镜头，在‘跟着我’和‘马上离开’重音期间不眨眼；"
        "【3.6—4.0秒】女主视线最终落在门外来路，手仍停在门框，保持稳定呼吸。"
    )
    # The 27-field picture already owns the sole quote.  Do not let the legacy
    # adapter append it again after the final phase.
    shot["台词"] = "无"
    shot["动作与表演"] = ""
    shot["发声方式"] = "不适用：完整发声规格在音色字段"
    shot["标点语气"] = "不适用：精确原句标点已在画面内容"
    shot["音色"] = (
        "基础声线：二十余岁青年女声，中音区，声线偏薄而清亮，长期保持克制质感。\n"
        "言语行为：确认危险并明确离开的决定。\n"
        "当前表达：警觉而利落，保持判断清楚。\n"
        "发声方式：\n"
        "音量：低声但字句完整，仅够近处听清。\n"
        "语速与节奏：连贯利落，不拖长整句。\n"
        "呼吸：随手指停住收稳气息，句中自然衔接。\n"
        "停顿：逗号处自然短停，不添加额外沉默。\n"
        "重音：落在本句的“跟着我”和“马上离开”。\n"
        "尾音：决定句尾短收落稳。\n"
        "禁止语气：拖沓、撒娇、夸张尖叫。"
    )
    shot["环境音效"] = "女主对白最清晰，近处衣料与呼吸声，门外远处风声压低避让对白。"
    support = f"她在门缝里发现新鲜泥印，说道：“{dialogue}”"
    shot["覆盖原文"] = support
    shot["原文支撑句"] = support
    shot["原文依据类型"] = "直接动作+原台词"
    obj["source_ingestion"]["protected_dialogue"][0]["mapped_shot_uids"] = [shot_id]
    evidence = obj.get("prevention_evidence", {})
    for row in evidence.get("source_ledger", []):
        if shot_id in row.get("target_shots", []):
            row["source_text"] = support
            row["source_type"] = "关键对白与决定"
            row["speaker"] = "女主"
    for row in evidence.get("shot_plan", []):
        if row.get("shot_id") == shot_id:
            row["core_function"] = "对白推进"
            row["main_action_stage"] = "发现线索后确认危险并表明离开决定"


def expand_fixture_for_adjacent_audit(obj: dict[str, Any]) -> None:
    """Deterministically create a second contiguous shot before evidence injection."""

    shots = obj.get("shots")
    if not isinstance(shots, list) or len(shots) != 1 or not isinstance(shots[0], dict):
        raise ValueError("adjacent-audit Golden fixture requires exactly one source shot")
    first = shots[0]
    second = copy.deepcopy(first)
    first_duration = float(first.get("时长") or 0)
    first_id = str(first.get("镜头编号") or "S001")
    second["镜头编号"] = "S002" if first_id == "S001" else f"{first_id}-NEXT"
    second["累计时长"] = first_duration * 2
    second["画面内容"] = "女主停在桌前，继续看向桌上的信件"
    second["动作与表演"] = "右手离开门框后自然垂落，肩背与呼吸保持细微自然变化"
    obj["shots"] = [first, second]
    obj["total_duration"] = first_duration * 2


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests", default=str(root / "references" / "golden_tests.json"))
    parser.add_argument("--rules", default=str(root / "references" / "storyboard_runtime_rules.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    suite = json.loads(Path(args.tests).read_text(encoding="utf-8"))
    validator = load_validator()
    fixtures = suite.get("fixtures", {})
    results = []
    for test in suite.get("tests", []):
        fixture_id = test["fixture_id"]
        obj = copy.deepcopy(fixtures[fixture_id])
        if test.get("expand_for_adjacent_audit"):
            expand_fixture_for_adjacent_audit(obj)
        if suite.get("inject_pg1_fixture_evidence"):
            inject_pg1_fixture_evidence(obj)
        if test.get("inject_chapter_intake"):
            inject_chapter_intake_evidence(obj)
        for mutation in test.get("mutations", []):
            apply_mutation(obj, mutation)
        if not test.get("skip_fixture_upgrade"):
            upgrade_fixture_to_compact(obj)
        if test.get("measure_current_repair"):
            obj = validator.bind_current_repair_check(obj, rules_path=args.rules)
        report = validator.validate_project(obj, mode=test.get("mode", "audit"), rules_path=args.rules)
        codes = [issue["error_code"] for issue in report["issues"]]
        expected_status = test["expected_status"]
        expected_codes = test.get("expected_error_codes", [])
        forbidden_codes = test.get("forbidden_error_codes", [])
        missing = [code for code in expected_codes if code not in codes]
        forbidden_present = [code for code in forbidden_codes if code in codes]
        status_match = report["status"] == expected_status
        passed = status_match and not missing and not forbidden_present
        results.append({
            "test_id": test["test_id"],
            "passed": passed,
            "expected_status": expected_status,
            "actual_status": report["status"],
            "expected_error_codes": expected_codes,
            "actual_error_codes": codes,
            "missing_error_codes": missing,
            "forbidden_error_codes_present": forbidden_present,
        })
    passed_count = sum(1 for result in results if result["passed"])
    output = {
        "run_id": f"R10-FUNCTIONAL-{int(time.time())}",
        "suite_version": suite.get("version"),
        "test_count": len(results),
        "test_pass_count": passed_count,
        "test_fail_count": len(results) - passed_count,
        "status": "pass" if passed_count == len(results) and results else "fail",
        "results": results,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if output["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
