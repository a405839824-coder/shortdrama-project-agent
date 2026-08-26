#!/usr/bin/env python3
"""Executable positive, negative and continuity tests for public-scene ecology."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_golden_tests  # noqa: E402
import validate_storyboard  # noqa: E402


def public_fixture() -> dict[str, Any]:
    suite = json.loads((ROOT / "references" / "golden_tests.json").read_text(encoding="utf-8"))
    obj = copy.deepcopy(suite["fixtures"]["valid_base"])
    shot = obj["shots"][0]
    shot.update({
        "镜头类型": "全景建立镜",
        "可见主体": "女主、古代市集前中后景普通人物与街道出口可见",
        "人物调度": "女主沿中轴前行；前景两名行人错身避让，中景摊贩各自招呼顾客并整理货物，背景脚夫推车穿过街口",
        "画面内容": "古代市集，中等人流；女主穿过摊位，普通人物动作彼此错开且不看镜头",
        "环境音效": "近处脚步与衣料声，中层低声交谈和货物碰触，远处错落叫卖与木轮声",
        "环境动态": "前景两名行人错身避让，中景摊贩分别叫卖和整理货物，背景脚夫推车穿过街口",
        "生成限制": "禁止字幕、BGM、现代物件、复制人群、同步动作与看镜头；保持人物脸和服装一致",
    })
    batch = obj["batches"][0]
    batch.update({
        "场景": "古代市集，中等人流",
        "起始画面": "古代市集全景，女主位于中轴；前景行人、中景摊贩、背景脚夫和推车位置明确",
        "起始表演状态": "女主看向前方；普通人物各自看向道路、货物或顾客",
        "动作过程": "女主前行，前景行人错身避让，中景摊贩分别招呼顾客和整理货物，背景脚夫推车穿过街口",
        "听话反应": "不适用：本批无角色对白",
        "结束状态": "女主停在摊位外，行人已错身，脚夫推车仍沿背景街口移动",
        "摄影机": "平视全景建立机位，28–35mm，固定主机位",
        "声音": "近处脚步与衣料声，中层低声交谈和货物碰触，远处错落叫卖与木轮声；禁止BGM",
        "环境动态": "前景两名行人错身避让，中景摊贩分别叫卖和整理货物，背景脚夫推车穿过街口",
        "光影": "侧后方自然日光，暖中性且高光不过曝",
        "独立执行提示词": "古代市集中等人流，从明确全景起始状态开始；女主前行，行人、摊贩、顾客和脚夫按各自节奏行动；近中远声源明确，禁止BGM、字幕、现代物件和复制人群。",
    })
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    return obj


def validate(obj: dict[str, Any]) -> dict[str, Any]:
    return validate_storyboard.validate_project(
        obj,
        mode="audit",
        rules_path=str(ROOT / "references" / "storyboard_runtime_rules.json"),
    )


def has_code(report: dict[str, Any], code: str) -> bool:
    return any(issue.get("error_code") == code for issue in report.get("issues", []))


def build_continuity_fixture() -> dict[str, Any]:
    obj = public_fixture()
    second = copy.deepcopy(obj["shots"][0])
    second["镜头编号"] = "S002"
    second["累计时长"] = 8.0
    second["生成批次"] = "B02"
    second["人物调度"] = second["人物调度"].replace("中等人流", "密集人流")
    second["画面内容"] = second["画面内容"].replace("中等人流", "密集人流")
    obj["shots"].append(second)
    second_batch = copy.deepcopy(obj["batches"][0])
    second_batch["批次编号"] = "B02"
    second_batch["覆盖镜头"] = ["S002"]
    second_batch["场景"] = "古代市集，密集人流"
    obj["batches"].append(second_batch)
    obj["total_duration"] = 8.0
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    return obj


def main() -> int:
    cases: list[tuple[str, dict[str, Any], str | None, str]] = []

    cases.append(("valid_public_scene", public_fixture(), None, "pass"))

    generic = public_fixture()
    generic["shots"][0]["人物调度"] = "女主走过闹市，路人来往"
    generic["shots"][0]["画面内容"] = "古代闹市，女主从人群中经过"
    generic["shots"][0]["环境动态"] = "路人自然移动"
    cases.append(("generic_crowd_fails", generic, "E-PUBLIC-CROWD-001", "fail"))

    generic_audio = public_fixture()
    generic_audio["shots"][0]["环境音效"] = "街道嘈杂声"
    cases.append(("generic_audio_fails", generic_audio, "E-PUBLIC-AUDIO-001", "fail"))

    no_ducking = public_fixture()
    no_ducking["shots"][0]["台词"] = "让开。"
    no_ducking["shots"][0]["音色"] = "年轻女声，清晰克制"
    no_ducking["shots"][0]["发声方式"] = "声音压低，语速短促，呼吸收紧，开口前停顿，重音落在让字，尾音下压收住"
    no_ducking["shots"][0]["标点语气"] = "句号收束，命令语气"
    cases.append(("dialogue_without_ducking_fails", no_ducking, "E-PUBLIC-AUDIO-001", "fail"))

    missing_people = public_fixture()
    missing_people["shots"][0]["人物调度"] = "女主独自走过街道"
    missing_people["shots"][0]["画面内容"] = "古代街道，女主独自前行"
    missing_people["shots"][0]["环境动态"] = "风吹幌子"
    missing_people["shots"][0]["生成限制"] = "禁止额外角色、字幕、BGM和现代物件"
    cases.append(("missing_people_fails", missing_people, "E-PUBLIC-ECOLOGY-001", "fail"))

    anonymous_line = public_fixture()
    anonymous_line["shots"][0]["环境音效"] += "，路人喊：“快跑！”"
    cases.append(("anonymous_extra_line_fails", anonymous_line, "E-PUBLIC-AVSYNC-001", "fail"))

    modern_sound = public_fixture()
    modern_sound["shots"][0]["环境音效"] += "，远处汽车喇叭"
    cases.append(("modern_sound_fails", modern_sound, "E-PUBLIC-AVSYNC-001", "fail"))

    batch_mismatch = public_fixture()
    batch_mismatch["batches"][0]["声音"] = "环境声；禁止BGM"
    batch_mismatch["batches"][0]["环境动态"] = "场景动态连续"
    batch_mismatch["batches"][0]["起始画面"] = "女主站在场景中央"
    batch_mismatch["batches"][0]["动作过程"] = "女主向前行走"
    batch_mismatch["batches"][0]["独立执行提示词"] = "女主向前行走，禁止BGM和字幕"
    cases.append(("seedance_ecology_mismatch_fails", batch_mismatch, "E-PUBLIC-AVSYNC-001", "fail"))

    valid_empty = public_fixture()
    valid_empty["shots"][0]["人物调度"] = "女主独自穿过宵禁后的空街"
    valid_empty["shots"][0]["画面内容"] = "古代街道因宵禁已经清场，店门紧闭，街上无人"
    valid_empty["shots"][0]["环境动态"] = "宵禁后的空街只有风吹幌子"
    valid_empty["shots"][0]["生成限制"] = "禁止字幕、BGM、现代物件和无依据新增路人"
    cases.append(("motivated_empty_scene_passes", valid_empty, None, "pass"))

    unexplained_empty = public_fixture()
    unexplained_empty["shots"][0]["人物调度"] = "女主独自穿过空街"
    unexplained_empty["shots"][0]["画面内容"] = "古代空街，店门紧闭，街上无人"
    unexplained_empty["shots"][0]["环境动态"] = "空街只有风吹幌子"
    unexplained_empty["shots"][0]["生成限制"] = "禁止字幕、BGM和现代物件"
    cases.append(("unexplained_empty_scene_fails", unexplained_empty, "E-PUBLIC-ECOLOGY-001", "fail"))

    cases.append(("density_jump_fails", build_continuity_fixture(), "E-PUBLIC-CONT-001", "fail"))

    results = []
    for case_id, obj, expected_code, expected_status in cases:
        report = validate(obj)
        passed = report.get("status") == expected_status and (expected_code is None or has_code(report, expected_code))
        results.append({
            "test_id": case_id,
            "passed": passed,
            "expected_status": expected_status,
            "actual_status": report.get("status"),
            "expected_error_code": expected_code,
            "actual_error_codes": sorted({issue.get("error_code") for issue in report.get("issues", [])}),
        })

    passed_count = sum(1 for result in results if result["passed"])
    output = {
        "status": "pass" if passed_count == len(results) else "fail",
        "test_count": len(results),
        "test_pass_count": passed_count,
        "test_fail_count": len(results) - passed_count,
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
