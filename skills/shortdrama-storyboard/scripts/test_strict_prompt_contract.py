#!/usr/bin/env python3
"""Regression tests for the R10-SPC-1 formal-master prompt contract."""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from run_golden_tests import inject_pg1_fixture_evidence, upgrade_fixture_to_compact
from validate_storyboard import (
    COMPACT_MOTHER_COLUMNS,
    project_export_row,
    validate_project,
)


ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "references" / "storyboard_runtime_rules.json"
GOLDEN = ROOT / "references" / "golden_tests.json"


def _replace_id(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_id(child, old, new) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_id(child, old, new) for child in value]
    return new if value == old else value


def strict_fixture() -> dict[str, Any]:
    suite = json.loads(GOLDEN.read_text(encoding="utf-8"))
    obj = copy.deepcopy(suite["fixtures"]["valid_compact_master"])
    inject_pg1_fixture_evidence(obj)
    upgrade_fixture_to_compact(obj)
    obj["source_lock"].update({
        "output_contract": "R10-SPC-1",
        "target_model": "Seedance 2.5",
        "aspect_ratio": "9:16",
    })
    obj["total_duration"] = 3.0
    shot = obj["shots"][0]
    old_id = str(shot["镜头编号"])
    shot.update({
        "镜头编号": "EP05-001",
        "时长": 3.0,
        "累计时长": 3.0,
        "功能类型": "对白推进",
        "场景连续性ID": "EP05-SC01",
        "镜头类型": "三人中近景，腰部以上",
        "可见主体": "江氏、邹氏、许氏",
        "空间站位描述": "香雅院正厅为同一清晨的私宅礼仪空间；邹氏坐画面中右主位，江氏坐左侧下首椅，许氏坐右侧后位；闭合厅门、木格窗和三席位置关系清楚，仅出现三名有源人物。",
        "起始状态": "院外回廊脚步由远及近；三人先看向厅门，江氏正准备将视线转回邹氏；邹氏肩背未松，许氏安静旁观。",
        "机位": "正厅门内侧平视50mm，f/3.2，三席同框，焦点优先落在江氏。",
        "运镜方式": "固定镜头，轻微呼吸感，不推不拉；以江氏收回视线并开口为本镜唯一核心动作。",
        "画面内容": "【0.0—0.7秒】院外脚步继续接近，邹氏眼睫轻抬；江氏从厅门方向收回视线转向邹氏，许氏晚约0.5秒转头，三人反应错开。【0.7—2.6秒】江氏保持坐姿，嘴角浮起礼貌浅笑，轻吸气后看着邹氏说道：“大嫂，没来晚吧？”逗号处短停，“没来晚”轻微加重，问句尾音克制上扬；口型与对白一致，自然眨眼1次，不看镜头。【2.6—3.0秒】江氏保持浅笑观察邹氏；邹氏只回看她，肩背仍稳；许氏不抢反应，镜尾保留院外脚步接近状态。",
        "音色": "江氏——温和亲近、暗含试探，声线平稳，音量适中。",
        "心理音轨": "无。",
        "环境音效": "江氏对白最清晰；近处衣料、呼吸与木椅细响，远处回廊脚步持续接近；开口时环境底声自然避让，不生成额外配乐。",
        "环境动态": "晨风使窗纸轻动，厅门始终闭合；三人转头错开，室内不增加无来源人物。",
        "光影": "人物面部保持自然明暗层次，江氏受光略亮；高光不过曝，暗部保留木构、皮肤和衣料质感。",
        "光源来源": "右侧闭合木格窗透入的自然晨光，辅以左前浅色墙面的弱反射光。",
        "光源方向": "主光由画面右上向左下斜落，左前反射光弱补面部暗侧，向厅内纵深逐级衰减。",
        "色温倾向": "中性微暖晨光；木色与紫褐、绛红衣料保持低饱和本色。",
        "生成限制": "9:16真人古装短剧；单镜单一主机位、单一核心动作阶段；禁止字幕、水印、额外配乐、现代物件、人物看镜头、同步转头、复制脸、上下黑边、无来源光、重HDR、塑料皮肤与海报摆拍。",
        "转场类型": "视线与对白落点切。",
        "切镜/转场提示词": "江氏问句落下后继续侧向邹氏，邹氏目光停在她脸上，院外脚步仍在接近；动作与呼吸稳定后切入江氏近景。",
        "参考图锚定": "江氏、邹氏、许氏定妆照锁脸与服装；香雅院正厅场景图锁三席和门窗关系。",
        "覆盖原文": "江氏向邹氏问候自己是否来晚。",
        "原文依据类型": "原台词＋必要导演推断",
        "原文支撑句": "江氏道：‘大嫂，没来晚吧？’",
    })
    obj["prevention_evidence"] = _replace_id(obj["prevention_evidence"], old_id, "EP05-001")
    return obj


def _codes(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    report = validate_project(obj, mode="audit", rules_path=str(RULES))
    return report, [issue["error_code"] for issue in report["issues"]]


def main() -> int:
    base = strict_fixture()
    results: list[dict[str, Any]] = []

    def run_code_test(test_id: str, mutate: Callable[[dict[str, Any]], None] | None, expected_code: str | None) -> None:
        obj = copy.deepcopy(base)
        if mutate:
            mutate(obj)
        report, codes = _codes(obj)
        if expected_code is None:
            passed = not any(issue.get("severity") == "fail" for issue in report["issues"])
        else:
            passed = expected_code in codes
        results.append({
            "test_id": test_id,
            "passed": passed,
            "expected_error_code": expected_code,
            "actual_status": report["status"],
            "actual_error_codes": codes,
        })

    run_code_test("SPC-VALID", None, None)
    run_code_test(
        "SPC-THREE-STAGES",
        lambda obj: obj["shots"][0].__setitem__("画面内容", "【0.0—1.0秒】人物转头。【1.0—3.0秒】人物停稳。"),
        "E-TIMELINE-001",
    )
    run_code_test(
        "SPC-NO-KELVIN",
        lambda obj: obj["shots"][0].__setitem__("色温倾向", "主区4200K微暖，背景偏冷。"),
        "E-LIGHT-001",
    )
    run_code_test(
        "SPC-NO-LIGHT-ALTERNATIVE",
        lambda obj: obj["shots"][0].__setitem__("光源来源", "右侧窗光或门外天光。"),
        "E-LIGHT-001",
    )
    run_code_test(
        "SPC-COLOR-RELATION",
        lambda obj: obj["shots"][0].__setitem__("色温倾向", "暖中性"),
        "E-LIGHT-001",
    )
    run_code_test(
        "SPC-NO-OLD-ASPECT-PHRASE",
        lambda obj: obj["shots"][0].__setitem__("生成限制", "9:16竖屏真人古装短剧截图质感；禁止字幕。"),
        "E-DEPRECATED-FIELD-001",
    )
    run_code_test(
        "SPC-EP-ID",
        lambda obj: obj["shots"][0].__setitem__("镜头编号", "S001"),
        "E-TEMPLATE-008",
    )
    run_code_test(
        "SPC-EPISODE-CUMULATIVE",
        lambda obj: obj["shots"][0].__setitem__("累计时长", 6.0),
        "E-STAT-001",
    )
    run_code_test(
        "SPC-NO-LEGACY-DIALOGUE",
        lambda obj: obj["shots"][0].__setitem__("画面内容", "【0.0—0.7秒】江氏转头。【0.7—2.6秒】【江氏】‘大嫂，没来晚吧？’【2.6—3.0秒】江氏停稳。"),
        "E-DEPRECATED-FIELD-001",
    )

    row = project_export_row(base["shots"][0])
    formal = str(row["正式分镜母版"])
    gpt = str(row["GPT关键帧生成指令"])
    expected_first_line = "镜头序号：EP05-001（3.0s；累计3.0s）"
    prohibited_gpt = ("【0.0—", "说道：", "大嫂，没来晚吧", "口型", "音量", "语速", "环境音效", "脚步", "BGM", "9:16竖屏")
    projection_passed = (
        list(row) == COMPACT_MOTHER_COLUMNS
        and formal.startswith(expected_first_line + "\n\n镜头类型：")
        and all(f"\n\n{field}：" in formal for field in (
            "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
            "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
            "转场类型", "切镜/转场提示词",
        ))
        and not any(term in gpt for term in prohibited_gpt)
    )
    results.append({
        "test_id": "SPC-DETERMINISTIC-PROJECTION",
        "passed": projection_passed,
        "expected_columns": COMPACT_MOTHER_COLUMNS,
        "actual_columns": list(row),
        "formal_first_line": formal.splitlines()[0] if formal else "",
        "gpt_prohibited_hits": [term for term in prohibited_gpt if term in gpt],
    })

    passed_count = sum(1 for row in results if row["passed"])
    output = {
        "run_id": f"R10-SPC-1-{int(time.time())}",
        "test_count": len(results),
        "test_pass_count": passed_count,
        "test_fail_count": len(results) - passed_count,
        "status": "pass" if passed_count == len(results) else "fail",
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
