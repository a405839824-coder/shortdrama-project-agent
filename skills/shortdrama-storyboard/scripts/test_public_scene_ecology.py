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
    obj['character_anchors'] = ['女主']
    obj['scene_anchors'] = ['街道出口可见', '宵禁空街出口可见']
    shot = obj["shots"][0]
    shot.update({
        "镜头类型": "全景建立镜",
        "可见主体": "女主、古代市集前中后景普通人物与街道出口可见",
        "人物调度": "女主沿中轴前行；前景两名行人错身避让，中景摊贩各自招呼顾客并整理货物，背景脚夫推车穿过街口",
        "画面内容": "古代市集，中等人流；女主穿过摊位，普通人物动作彼此错开且不看镜头",
        "环境音效": "近处脚步与衣料声，中层低声交谈和货物碰触，远处错落叫卖与木轮声",
        "环境动态": "前景两名行人错身避让，中景摊贩分别叫卖和整理货物，背景脚夫推车穿过街口",
        "生成限制": "9:16竖屏；禁止额外配乐；禁止字幕、现代物件、复制人群、同步动作与看镜头；保持人物脸和服装一致",
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
        "声音": "近处脚步与衣料声，中层低声交谈和货物碰触，远处错落叫卖与木轮声；禁止额外配乐",
        "环境动态": "前景两名行人错身避让，中景摊贩分别叫卖和整理货物，背景脚夫推车穿过街口",
        "光影": "侧后方自然日光，暖中性且高光不过曝",
        "独立执行提示词": "9:16竖屏；古代市集中等人流，从明确全景起始状态开始；女主前行，行人、摊贩、顾客和脚夫按各自节奏行动；近中远声源明确，禁止额外配乐、字幕、现代物件和复制人群。",
    })
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    run_golden_tests.upgrade_fixture_to_compact(obj)
    obj.setdefault("source_lock", {}).update({
        "output_contract": "R10-SPC-1",
        "target_model": "Seedance 2.5",
        "aspect_ratio": "9:16",
    })
    shot = obj["shots"][0]
    shot.update({
        "镜头编号": "EP01-001",
        "episode_id": "EP01",
        "场景连续性ID": "EP01-SC01",
        "累计时长": 4.0,
        "空间站位描述": "室外古代市集，中等人流；女主位于中轴前景，前景两名行人分居左右，中景摊贩与顾客围绕摊位，背景脚夫和木轮车朝街口移动，仅出现有源人物。",
        "起始状态": "女主面向街道前方，双脚停稳；前景行人尚未错身，中景摊贩各自照看货物，背景脚夫双手扶车，所有人物视线均有明确落点。",
        "画面内容": "【0.0—0.8秒】女主确认前方人流后迈出第一步，前景两名行人开始错身；【0.8—3.3秒】女主沿中轴穿过摊位，中景摊贩分别招呼顾客和整理货物，背景脚夫推车穿过街口，人物动作彼此错开且不看镜头；【3.3—4.0秒】女主停在摊位外并保持前方视线，行人完成错身，木轮车继续向背景移动。",
        "音色": "无对白，不适用。",
        "心理音轨": "不适用：本镜无心理音轨；不张嘴、不驱动口型、不生成字幕、不计入对白时长。",
        "光影": "女主、摊位与前中后景人物保持自然明暗层次；高光不过曝，暗部保留皮肤、衣料、木构和货物质感。",
        "光源来源": "画面左侧街巷上方透入的自然日光，辅以右前浅色墙面与地面的弱反射光。",
        "光源方向": "主光由画面左上向右下斜落，右前反射光弱补人物暗侧，并沿街道纵深逐级衰减。",
        "色温倾向": "中性微暖日光；肤色自然，衣料、木构、货物与街面保持低饱和本色。",
        "生成限制": "9:16竖屏；禁止额外配乐；单镜单一主机位、单一核心动作阶段；禁止字幕、水印、现代物件、人物看镜头、同步动作、复制脸、复制人群、上下黑边、无来源光、重HDR、塑料皮肤与海报摆拍。",
    })
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    obj["prevention_evidence"]["audio_workflow"]["seedance_bgm_policy"] = "禁止额外配乐"
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
    second["空间站位描述"] = second["空间站位描述"].replace("中等人流", "密集人流")
    second["画面内容"] = second["画面内容"].replace("中等人流", "密集人流")
    obj["shots"].append(second)
    obj["total_duration"] = 8.0
    run_golden_tests.inject_pg1_fixture_evidence(obj)
    return obj


def main() -> int:
    cases: list[tuple[str, dict[str, Any], str | None, str]] = []

    cases.append(("valid_public_scene", public_fixture(), None, "pass"))

    generic = public_fixture()
    generic["shots"][0]["空间站位描述"] = "女主走过闹市，路人来往"
    generic["shots"][0]["画面内容"] = "古代闹市，女主从人群中经过"
    generic["shots"][0]["环境动态"] = "路人自然移动"
    cases.append(("generic_crowd_fails", generic, "E-PUBLIC-CROWD-001", "fail"))

    generic_audio = public_fixture()
    generic_audio["shots"][0]["环境音效"] = "街道嘈杂声"
    cases.append(("generic_audio_fails", generic_audio, "E-PUBLIC-AUDIO-001", "fail"))

    no_ducking = public_fixture()
    no_ducking["shots"][0]["画面内容"] += "；【女主】让开。；声音压低，语速短促，呼吸收紧，开口前停顿，重音落在让字，尾音下压收住；句号收束，命令语气"
    no_ducking["shots"][0]["音色"] = "年轻女声，清晰克制"
    cases.append(("dialogue_without_ducking_fails", no_ducking, "E-PUBLIC-AUDIO-001", "fail"))

    missing_people = public_fixture()
    missing_people["shots"][0]["空间站位描述"] = "女主独自走过街道"
    missing_people["shots"][0]["画面内容"] = "古代街道，女主独自前行"
    missing_people["shots"][0]["环境动态"] = "风吹幌子"
    missing_people["shots"][0]["生成限制"] = "9:16竖屏；禁止额外配乐；禁止额外角色、字幕和现代物件"
    cases.append(("missing_people_fails", missing_people, "E-PUBLIC-ECOLOGY-001", "fail"))

    anonymous_line = public_fixture()
    anonymous_line["shots"][0]["环境音效"] += "，路人喊：“快跑！”"
    cases.append(("anonymous_extra_line_fails", anonymous_line, "E-PUBLIC-AVSYNC-001", "fail"))

    modern_sound = public_fixture()
    modern_sound["shots"][0]["环境音效"] += "，远处汽车喇叭"
    cases.append(("modern_sound_fails", modern_sound, "E-PUBLIC-AVSYNC-001", "fail"))

    duplicate_batch = public_fixture()
    duplicate_batch["batches"] = [{"批次编号": "B01"}]
    cases.append(("duplicate_batch_fails", duplicate_batch, "E-DUPLICATE-GENERATION-001", "fail"))

    valid_empty = public_fixture()
    valid_empty["shots"][0]["可见主体"] = "全景中的女主与宵禁空街出口可见"
    valid_empty["shots"][0]["空间站位描述"] = "室外月夜宵禁后，女主独自穿过已清场的空街；两侧店门紧闭，街口保持可见。"
    valid_empty["shots"][0]["起始状态"] = "女主站在空街中轴，面向街口；两侧摊位已收摊，店门紧闭，街上没有行人、摊贩或车辆。"
    valid_empty["shots"][0]["画面内容"] = (
        "【0.0—0.8秒】女主确认宵禁后的街道已清场，店门紧闭；"
        "【0.8—3.3秒】她沿空街中轴独自前行，衣摆与幌子受风轻动，街上无人；"
        "【3.3—4.0秒】女主在街口停步并保持前方视线，空街状态不变。"
    )
    valid_empty["shots"][0]["环境动态"] = "宵禁后的空街只有风吹幌子"
    valid_empty["shots"][0]["环境音效"] = "近处女主脚步与衣料声，中层风吹幌子和木门轻响，远处画外更鼓声；街上无人声。"
    valid_empty["shots"][0]["光影"] = "女主与空街保持夜间自然明暗，空气清澈，街墙与店门材质可辨；高光不过曝，阴影不抬亮成白昼。"
    valid_empty["shots"][0]["光源来源"] = "高处月光与夜空漫射光，街墙提供极弱反射补光。"
    valid_empty["shots"][0]["光源方向"] = "月光从画面左上方斜落，街墙反射弱补女主暗侧，亮度沿街道纵深逐级衰减。"
    valid_empty["shots"][0]["色温倾向"] = "清冷月光与夜空环境光覆盖街面，女主肤色和木门保留低饱和本色，不把整幅画面统一染蓝。"
    valid_empty["shots"][0]["生成限制"] = "9:16竖屏；禁止额外配乐；禁止字幕、现代物件和无依据新增路人"
    cases.append(("motivated_empty_scene_passes", valid_empty, None, "pass"))

    unexplained_empty = public_fixture()
    unexplained_empty["shots"][0]["空间站位描述"] = "女主独自穿过空街"
    unexplained_empty["shots"][0]["画面内容"] = "古代空街，店门紧闭，街上无人"
    unexplained_empty["shots"][0]["环境动态"] = "空街只有风吹幌子"
    unexplained_empty["shots"][0]["生成限制"] = "9:16竖屏；禁止额外配乐；禁止字幕和现代物件"
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
