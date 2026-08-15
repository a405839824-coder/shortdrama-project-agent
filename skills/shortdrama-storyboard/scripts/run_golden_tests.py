#!/usr/bin/env python3
"""Execute R10 functional Golden Tests against validate_storyboard.py."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any


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
            "camera_position": str(shot.get("机位") or "平视正侧45度"),
            "main_action_stage": str(shot.get("画面内容") or "人物完成当前动作阶段"),
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
        "shot_plan": shot_plan,
        "performance_chains": performance_chains,
        "audio_workflow": {
            "seedance_bgm_policy": "禁止BGM",
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
            "Seedance独立执行",
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
        if suite.get("inject_pg1_fixture_evidence"):
            inject_pg1_fixture_evidence(obj)
        for mutation in test.get("mutations", []):
            apply_mutation(obj, mutation)
        report = validator.validate_project(obj, mode=test.get("mode", "audit"), rules_path=args.rules)
        codes = [issue["error_code"] for issue in report["issues"]]
        expected_codes = test.get("expected_error_codes", [])
        forbidden_codes = test.get("forbidden_error_codes", [])
        missing = [code for code in expected_codes if code not in codes]
        forbidden_present = [code for code in forbidden_codes if code in codes]
        status_match = report["status"] == test["expected_status"]
        passed = status_match and not missing and not forbidden_present
        results.append({
            "test_id": test["test_id"],
            "passed": passed,
            "expected_status": test["expected_status"],
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
