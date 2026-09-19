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
    _lighting_context_faults,
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
    obj['character_anchors'] = ['江氏', '邹氏', '许氏']
    obj["source_lock"].update({
        "output_contract": "R10-SPC-1",
        "target_model": "Seedance 2.5",
        "aspect_ratio": "9:16",
    })
    obj["total_duration"] = 4.0
    shot = obj["shots"][0]
    old_id = str(shot["镜头编号"])
    shot.update({
        "镜头编号": "EP05-001",
        "时长": 4.0,
        "累计时长": 4.0,
        "功能类型": "对白推进",
        "场景连续性ID": "EP05-SC01",
        "镜头类型": "三人中近景，腰部以上",
        "可见主体": "江氏、邹氏、许氏",
        "空间站位描述": "香雅院正厅为同一清晨的私宅礼仪空间；邹氏坐画面中右主位，江氏坐左侧下首椅，许氏坐右侧后位；闭合厅门、木格窗和三席位置关系清楚，仅出现三名有源人物。",
        "起始状态": "江氏坐左侧下首椅，头部侧向邹氏，目光停在膝上袖口，双手自然叠放膝上；邹氏坐中右主位，肩背挺直；许氏坐右侧后位，视线落在江氏身上；三人双眼自然睁开，不处于半眨眼瞬间；厅门闭合。",
        "机位": "正厅门内侧平视50mm，f/3.2，三席同框，焦点优先落在江氏。",
        "运镜方式": "固定镜头，轻微呼吸感，不推不拉。",
        "画面内容": (
            "【0.0—0.8秒】江氏视线从膝上袖口移向邹氏面部，眼球先轻微换焦，头部随后轻跟，"
            "上眼睑略压，0.3秒在换焦后自然眨眼一次；"
            "许氏视线从江氏衣袖移向江氏面部，眼球先轻微换焦，头部随后轻跟，"
            "上眼睑略微放松，0.6秒在换焦后自然眨眼一次；三人反应错开。"
            "【0.8—3.3秒】江氏保持坐姿，嘴角浮起礼貌浅笑，轻吸气后看着邹氏；"
            "江氏说道：“大嫂，没来晚吧？”"
            "江氏在“没来晚”重音期间不眨眼，口型与对白一致，不看镜头；"
            "邹氏听到问候后视线从江氏肩部移向江氏面部，眼球先轻微换焦，头部保持不动，"
            "上眼睑由微紧转为放松。"
            "【3.3—4.0秒】邹氏听完后在3.5秒自然眨眼一次，视线最终落在江氏面部；"
            "许氏听完问候后在3.7秒轻点头，视线最终落在江氏面部；"
            "江氏视线最终落在邹氏面部；三人眨眼错开、不同时闭眼，三人位置清楚。"
        ),
        "音色": (
            "基础声线：二十余岁青年女声，中音区，声线偏薄而清亮，长期保持温润平稳的质感。\n"
            "言语行为：带试探意味的问候。\n"
            "当前表达：表面温和亲近，暗含确认自己是否迟到的试探。\n"
            "发声方式：\n"
            "音量：比日常交谈低一档，仅够近处对象听清。\n"
            "语速与节奏：前半句自然，问句略放慢，不拖长字音。\n"
            "呼吸：开口前轻吸气，句中不额外抢气。\n"
            "停顿：在“大嫂”后的逗号处短停。\n"
            "重音：明确落在原文中的“没来晚”。\n"
            "尾音：克制上抬。\n"
            "禁止语气：撒娇、讨好、夸张惊慌。"
        ),
        "心理音轨": "无。",
        "环境音效": "江氏对白最清晰；近处衣料、呼吸与木椅细响，远处回廊脚步持续接近；开口时环境底声自然避让。",
        "环境动态": "窗纸在开场瞬间轻微向内偏斜，厅门闭合；三人位置与前中后层次清楚，室内不增加无来源人物。",
        "光影": "室内空气清澈，人物面部保持自然明暗层次，江氏受光略亮；高光不过曝，背景木构、门窗与衣料质感清晰可辨，不泛白、不雾化。",
        "光源来源": "自然晨光经右侧闭合木格窗的窗纸漫射透入，左前浅色墙面提供弱反射光。",
        "光源方向": "大面积漫射主光由画面右前上方形成柔和明暗，左前反射光弱补面部暗侧，无可见光束，亮度向厅内纵深平缓衰减。",
        "色温倾向": "中性微暖晨光；木色与紫褐、绛红衣料保持低饱和本色。",
        "生成限制": "9:16竖屏；单镜单一主机位、单一核心动作阶段；人物眼神自然聚焦，保留轻微眼球焦点调整和眼睑松紧变化；禁止无依据全程不眨眼；禁止僵直瞪视；禁止高频机械眨眼；禁止多人同步眨眼；门窗保持闭合；室内空气清澈，无薄雾、无烟尘、无丁达尔光、无体积光、无可见光束、无全画面柔焦或镜头扩散，背景不泛白；禁止字幕、水印、现代物件、人物看镜头、同步转头、复制脸、上下黑边、无来源光、重HDR、塑料皮肤与海报摆拍；禁止额外配乐。",
        "转场类型": "视线与对白落点切。",
        "切镜/转场提示词": "江氏问句落下后继续侧向邹氏，邹氏目光停在她脸上，院外脚步仍在接近；动作与呼吸稳定后切入江氏近景。",
        "参考图锚定": "江氏、邹氏、许氏定妆照锁脸与服装；香雅院正厅场景图锁三席和门窗关系。",
        "覆盖原文": "江氏向邹氏问候自己是否来晚。",
        "原文依据类型": "原台词＋必要导演推断",
        "原文支撑句": "江氏道：‘大嫂，没来晚吧？’",
    })
    obj["prevention_evidence"] = _replace_id(obj["prevention_evidence"], old_id, "EP05-001")
    obj["protected_dialogue"] = [{
        "dialogue_id": "D05-001",
        "speaker": "江氏",
        "text": "大嫂，没来晚吧？",
        "mapped_shot_uids": ["EP05-001"],
    }]
    return obj


def _codes(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    report = validate_project(obj, mode="audit", rules_path=str(RULES))
    return report, [issue["error_code"] for issue in report["issues"]]


def _set_start_result_conflict(obj: dict[str, Any]) -> None:
    shot = obj["shots"][0]
    shot["起始状态"] = "江氏已接过礼匣并双手持稳，邹氏仍坐主位；三人双眼自然睁开，不处于半眨眼瞬间。"
    shot["画面内容"] = shot["画面内容"].replace(
        "【0.0—0.8秒】",
        "【0.0—0.8秒】江氏接过礼匣；",
        1,
    )


def _set_prop_dual_holder(obj: dict[str, Any]) -> None:
    state = obj["prevention_evidence"]["scene_state_ledger"][0]["start_state"]
    state["手持道具"] = {
        "礼匣": {"holder": ["江氏", "邹氏"], "state": "关闭"},
    }


def _set_prop_free_text(obj: dict[str, Any]) -> None:
    state = obj["prevention_evidence"]["scene_state_ledger"][0]["start_state"]
    state["手持道具"] = "江氏手持礼匣"


def _set_tea_chain_overload(obj: dict[str, Any]) -> None:
    shot = obj["shots"][0]
    shot["时长"] = 2.7
    shot["累计时长"] = 2.7
    shot["画面内容"] = (
        "【0.0—0.5秒】江氏双手奉茶。"
        "【0.5—2.2秒】邹氏接杯，邹氏揭盖，邹氏浅啜并点头。"
        "【2.2—2.7秒】邹氏收手，杯盏停稳。"
    )
    obj["total_duration"] = 2.7
    obj["protected_dialogue"] = []
    shot["音色"] = "无对白，不适用。"
    # These are this negative example's explicit planned event times, not
    # universal minimum action fees.  The sip cannot finish inside its phase
    # or the shot, and the following hand withdrawal cannot precede that end.
    obj["prevention_evidence"]["audio_workflow"]["timing"] = {
        "contract": "shot-timing-v1",
        "utterances": [],
        "events": [
            {"id": "tea-offer", "shot_id": "EP05-001", "kind": "action", "actor": "江氏",
             "action": "奉茶", "basis": "江氏双手奉茶", "start": 0.0, "end": 0.5, "after": []},
            {"id": "tea-receive", "shot_id": "EP05-001", "kind": "action", "actor": "邹氏",
             "action": "接杯", "basis": "邹氏接杯", "start": 0.5, "end": 1.0, "after": ["tea-offer"]},
            {"id": "tea-open", "shot_id": "EP05-001", "kind": "action", "actor": "邹氏",
             "action": "揭盖", "basis": "邹氏揭盖", "start": 1.0, "end": 1.4, "after": ["tea-receive"]},
            {"id": "tea-sip", "shot_id": "EP05-001", "kind": "action", "actor": "邹氏",
             "action": "浅啜", "basis": "邹氏浅啜", "start": 1.4, "end": 2.9, "after": ["tea-open"]},
            {"id": "tea-hand-withdraw", "shot_id": "EP05-001", "kind": "action", "actor": "邹氏",
             "action": "收手", "basis": "邹氏收手", "start": 2.2, "end": 2.7, "after": ["tea-sip"]},
        ],
    }


def main() -> int:
    base = strict_fixture()
    results: list[dict[str, Any]] = []

    def run_code_test(
        test_id: str,
        mutate: Callable[[dict[str, Any]], None] | None,
        expected_code: str | None,
        expected_gate_id: str | None = None,
        forbidden_codes: tuple[str, ...] = (),
    ) -> None:
        obj = copy.deepcopy(base)
        if mutate:
            mutate(obj)
        report, codes = _codes(obj)
        gate_ids = [str(issue.get("template_gate_id")) for issue in report["issues"] if issue.get("template_gate_id")]
        if expected_code is None:
            passed = not any(issue.get("severity") == "fail" for issue in report["issues"])
        else:
            passed = expected_code in codes and (expected_gate_id is None or expected_gate_id in gate_ids)
        passed = passed and not set(forbidden_codes).intersection(codes)
        results.append({
            "test_id": test_id,
            "passed": passed,
            "expected_error_code": expected_code,
            "expected_template_gate_id": expected_gate_id,
            "actual_status": report["status"],
            "actual_error_codes": codes,
            "actual_template_gate_ids": gate_ids,
            "forbidden_error_codes": list(forbidden_codes),
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
    run_code_test(
        "SPC-THG01-ASPECT-MISSING",
        lambda obj: obj["shots"][0].__setitem__("生成限制", "禁止字幕、水印与现代物件；禁止额外配乐。"),
        "E-I2V-INDEP-002",
        "THG-01",
    )
    run_code_test(
        "SPC-THG02-TRANSITION-EMPTY-AND-DUPLICATE",
        lambda obj: obj["shots"][0].__setitem__("切镜/转场提示词", "动作落稳；；动作落稳；动作落稳。"),
        "E-DUPLICATE-GENERATION-001",
        "THG-02",
    )
    run_code_test(
        "SPC-THG03-KEYFRAME-DYNAMIC",
        lambda obj: obj["shots"][0].__setitem__("起始状态", "几秒后由远及近推镜，江氏准备转头。"),
        "E-JIMENG-TIMING-001",
        "THG-03",
    )
    run_code_test(
        "SPC-THG04-CROSS-SHOT-IN-TRANSITION",
        lambda obj: obj["shots"][0].__setitem__("切镜/转场提示词", "承接上一镜继续刚才动作后切。"),
        "E-I2V-INDEP-001",
        "THG-04",
    )
    run_code_test(
        "SPC-THG05-DIALOGUE-SPEAKER-MISMATCH",
        lambda obj: obj["protected_dialogue"][0].__setitem__("speaker", "邹氏"),
        "E-SOURCE-SELECT-001",
        "THG-05",
    )
    run_code_test(
        "SPC-THG05-DIALOGUE-PUNCTUATION-MISMATCH",
        lambda obj: obj["protected_dialogue"][0].__setitem__("text", "大嫂，没来晚吧！"),
        "E-SOURCE-SELECT-001",
        "THG-05",
    )
    run_code_test(
        "SPC-THG06-VISIBLE-MAIN-CHARACTER-LIMIT",
        lambda obj: obj["shots"][0].__setitem__("可见主体", "江氏、邹氏、许氏、荣婶儿"),
        "E-VIS-001",
        "THG-06",
    )
    run_code_test(
        "SPC-THG07-MULTI-ACTION-STAGE",
        lambda obj: obj["prevention_evidence"]["shot_plan"][0].__setitem__("main_action_stage_count", 2),
        "E-BATCH-004",
        "THG-07",
    )
    run_code_test(
        "SPC-THG08-START-CONTAINS-RESULT",
        _set_start_result_conflict,
        "E-CONTINUITY-001",
        "THG-08",
    )
    run_code_test(
        "SPC-THG09-PROP-DUAL-HOLDER",
        _set_prop_dual_holder,
        "E-CONTINUITY-001",
        "THG-09",
    )
    run_code_test(
        "SPC-THG09-PROP-FREE-TEXT-REJECTED",
        _set_prop_free_text,
        "E-CONTINUITY-001",
        "THG-09",
    )
    run_code_test(
        "SPC-THG10-TEA-CHAIN-OVERLOAD",
        _set_tea_chain_overload,
        "E-BATCH-003",
        "THG-10",
    )
    run_code_test(
        "SPC-THG11-TENTH-SECOND-PROJECTION-PRECISION",
        lambda obj: (
            obj["shots"][0].__setitem__("时长", 3.25),
            obj["shots"][0].__setitem__("累计时长", 3.25),
            obj.__setitem__("total_duration", 3.25),
        ),
        "E-STAT-001",
        "THG-11",
    )
    run_code_test(
        "SPC-THG13-EYE-PERFORMANCE-CONTRACT",
        lambda obj: obj["shots"][0].__setitem__(
            "画面内容",
            "【0.0—0.8秒】江氏目光锁住邹氏并保持凝视。"
            "【0.8—3.3秒】江氏说道：\u201c大嫂，没来晚吧？\u201d"
            "【3.3—4.0秒】镜尾江氏仍看着邹氏。",
        ),
        # The frozen prose holds a real target; missing mandatory eye template
        # components was the <=0.5.43 assertion, not a current content error.
        # It still removes the listener response, so that separate guard stays.
        "E-PERFORMANCE-001",
        forbidden_codes=("E-EXPRESSION-003",),
    )
    run_code_test(
        "SPC-THG13-ACTUAL-SAME-ENDPOINT-SHIFT",
        lambda obj: obj['shots'][0].__setitem__('画面内容',
            obj['shots'][0]['画面内容'].replace('江氏视线从膝上袖口移向邹氏面部',
                '江氏视线从邹氏面部移向邹氏面部')),
        'E-EXPRESSION-003', 'THG-13',
    )
    run_code_test(
        "SPC-THG14-ADJACENT-PAIR-PROOF",
        lambda obj: obj["prevention_evidence"].pop(
            "adjacent_continuity_audit", None
        ),
        "E-CONTINUITY-001",
        "THG-14",
    )
    run_code_test(
        "SPC-THG15-ONE-LINE-VOICE-REJECTED",
        lambda obj: obj["shots"][0].__setitem__(
            "音色", "江氏温和亲近、暗含试探，声线平稳，音量适中。"
        ),
        "E-VOICE-001",
        "THG-15",
    )
    run_code_test(
        "SPC-THG15-MISSING-VOICE-SUBLABEL",
        lambda obj: obj["shots"][0].__setitem__(
            "音色", obj["shots"][0]["音色"].replace("呼吸：开口前轻吸气，句中不额外抢气。\n", "")
        ),
        "E-VOICE-001",
        "THG-15",
    )
    run_code_test(
        "SPC-THG15-EMPHASIS-MUST-MATCH-SOURCE",
        lambda obj: obj["shots"][0].__setitem__(
            "音色", obj["shots"][0]["音色"].replace("“没来晚”", "“马上离开”")
        ),
        "E-VOICE-001",
        "THG-15",
    )
    run_code_test(
        "SPC-THG15-NO-GENERIC-NEUTRAL-SUFFIX",
        lambda obj: obj["shots"][0].__setitem__(
            "音色",
            obj["shots"][0]["音色"]
            + "\n声音中等清晰，语速从容，关键词加重，尾音随句意收住。",
        ),
        "E-VOICE-001",
        "THG-15",
    )
    run_code_test(
        "SPC-THG15-NESTED-LABELS-STAY-OUT-OF-PICTURE",
        lambda obj: obj["shots"][0].__setitem__(
            "画面内容", obj["shots"][0]["画面内容"] + "\n音量：适中。"
        ),
        "E-VOICE-001",
        "THG-15",
    )
    run_code_test(
        "SPC-THG17-UNOBSERVABLE-SYMBOLISM",
        lambda obj: obj["shots"][0].__setitem__(
            "光影",
            obj["shots"][0]["光影"] + "；红色代表爱情、婚姻和希望。",
        ),
        "E-T2I-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-DAY-NIGHT-CONFLICT",
        lambda obj: obj["shots"][0].__setitem__(
            "空间站位描述",
            "深夜的香雅院正厅；邹氏坐画面中右主位，江氏坐左侧下首椅，"
            "许氏坐右侧后位；闭合厅门与木格窗关系清楚。",
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-RIM-LIGHT-WITHOUT-BACKLIGHT",
        lambda obj: obj["shots"][0].__setitem__(
            "光影",
            obj["shots"][0]["光影"] + "；江氏发丝边缘形成暖色轮廓光。",
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-PRACTICAL-SOURCE-NOT-IN-SCENE",
        lambda obj: obj["shots"][0].__setitem__(
            "光源来源", "画面右侧红烛为主光，左前浅色墙面提供弱反射光。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-VOLUMETRIC-LIGHT-WITHOUT-MEDIUM",
        lambda obj: obj["shots"][0].__setitem__(
            "光影", obj["shots"][0]["光影"] + "；窗前出现清晰丁达尔光束。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-INDOOR-HAZE-REJECTED",
        lambda obj: obj["shots"][0].__setitem__(
            "光影", obj["shots"][0]["光影"] + "；室内漂浮薄雾，形成雾蒙蒙的朦胧背景。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-INDOOR-MILKY-OPTICS-REJECTED",
        lambda obj: obj["shots"][0].__setitem__(
            "光影", obj["shots"][0]["光影"] + "；自然镜头扩散与高光晕染覆盖整个室内。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-CLOSED-WINDOW-VISIBLE-BEAM-REJECTED",
        lambda obj: obj["shots"][0].__setitem__(
            "光源方向", "晨光从斜后方穿过闭合木格窗射入室内，在人物背后形成可见光束。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-INDOOR-BACKLIGHT-WITHOUT-OPENING-REJECTED",
        lambda obj: obj["shots"][0].__setitem__(
            "光源方向", "斜后方晨光作为强逆光，向厅内纵深衰减。"
        ),
        "E-LIGHT-001",
        "THG-17",
    )
    run_code_test(
        "SPC-THG17-NEGATIVE-INDOOR-EFFECTS-ARE-NOT-POSITIVE",
        lambda obj: obj["shots"][0].__setitem__(
            "生成限制", obj["shots"][0]["生成限制"] + "；禁止轮廓光、镜头光晕和烟雾。"
        ),
        None,
    )
    # Exercise scene decisions separately from unrelated dialogue/continuity gates.
    clean_room = {
        "空间站位描述": "清晨卧房，左前木格窗关闭，窗纸完整。",
        "起始状态": "女子坐在床边。",
        "光影": "空气清澈，背景木构清晰可辨，曝光正常、高光不过曝，不泛白、不雾化。",
        "光源来源": "闭合窗纸透入天光，浅墙反射弱补光。",
        "光源方向": "左前侧略亮，向背景平缓衰减，无可见光束。",
        "色温倾向": "中性微暖，木墙保持本色。",
    }
    local_smoke = "仅香炉口上方一缕细烟，不扩散至人物和背景，其余室内空气清澈，背景木构清晰可辨，高光不过曝。"
    lighting_cases = [
        ("CLEAN-ROOM", {}, False),
        ("GENERIC-SMOKE", {"光影": "烟雾在人物身后弥漫，背景不泛白。"}, True),
        ("ENGLISH-HALATION", {"光影": clean_room["光影"] + "极轻微halation使画面朦胧。"}, True),
        ("LOCAL-SMOKE-WITHOUT-PROP", {"光影": local_smoke}, True),
        ("SOURCE-AND-LOCAL-SMOKE", {"起始状态": "女子坐在床边，案上香炉已点燃。", "光影": local_smoke}, False),
        ("LOCAL-SMOKE-PLUS-WHOLE-ROOM-HAZE", {"起始状态": "案上香炉已点燃。", "光影": local_smoke + "烟雾充满整个房间。"}, True),
        ("CLEAR-COLOR-MOOD", {"光影": clean_room["光影"] + "如梦似幻由低饱和色彩、浅景深与人物浅笑表现。"}, False),
        ("SMOKE-NEGATIVE-LIST", {"生成限制": "禁止薄雾、烟雾、细烟、镜头扩散、halation。"}, False),
        ("NEGATIVE-THEN-POSITIVE-SMOKE", {"光影": "无烟雾，薄雾笼罩人物。背景可辨。"}, True),
        ("LIGHT-CANNOT-OPEN-WINDOW", {"光源来源": "敞开木格窗的漫射天光。"}, True),
        ("OPEN-DOOR-CANNOT-OPEN-CLOSED-WINDOW", {"空间站位描述": "清晨正厅，门敞开，窗户关闭。", "光源来源": "敞开木格窗的漫射天光。"}, True),
        ("OPEN-DOOR-BACKLIGHT", {"空间站位描述": "清晨正厅，厅门敞开，女子门口现身。", "光源来源": "敞开厅门外的晨光，浅墙反射弱补面部。", "光源方向": "门外侧后方晨光形成侧逆光，亮度向厅内衰减。"}, False),
        ("CLOSED-ROOM-BACKLIGHT", {"光源方向": "侧后方天光作为背光，向背景衰减。"}, True),
        ("EXTERIOR-LEFT-REAR-SYNONYM", {"空间站位描述": "晴日书房门外廊下。", "光影": "发丝和肩部呈克制轮廓光，空气清澈，建筑可辨。", "光源来源": "晨光为主光，天空反射弱补面部。", "光源方向": "晨光从画面左后侧照向人物，廊下亮度衰减。"}, False),
        ("ROOM-MENTION-IN-EXTERIOR", {"空间站位描述": "书房门外廊下，清晨。", "原文支撑句": "她准备进入书房。", "光源来源": "室外晨光和天空弱补光。", "光源方向": "画面左后方侧逆光，向石阶衰减。"}, False),
        ("REAL-FIRE-NO-FULL-FRAME-DIFFUSION", {"起始状态": "屋角失火，局部火焰燃烧。", "光影": "自然镜头扩散覆盖整个室内，背景木构可辨。"}, True),
        ("PRACTICAL-LOCAL-HALATION", {"空间站位描述": "夜晚卧房，窗户关闭。", "起始状态": "案上红烛燃着。", "光源来源": "红烛为主光，浅墙提供极弱反射光。", "光影": "空气清澈，背景木构可辨，曝光正常、高光不过曝。仅烛火边缘出现局部极轻微halation，不扩散至人脸与背景。"}, False),
        ("TIME-PREFIX-DOES-NOT-HIDE-INDOOR", {"空间站位描述": "清晨，女主位于室内卧房，闭合木窗在左后。", "光影": "薄雾笼罩背景，人物面部清晰。"}, True),
        ("OUTSIDE-VIEW-DOES-NOT-MOVE-ROOM", {"空间站位描述": "卧室靠近窗外庭院的一侧，女主坐在床沿。", "光影": "薄雾笼罩背景，人物面部清晰。"}, True),
        ("LIST-NEGATION-STOPS-AT-AFFIRMATIVE-PREDICATE", {"光影": "无字幕、镜头使用柔焦；女主面部自然明暗。"}, True),
        ("LIST-NEGATION-STOPS-AT-CONTRAST", {"光影": "不使用柔焦、但允许薄雾笼罩人物。背景可辨。"}, True),
        ("NEGATED-WINDOW-OPENING-IS-NOT-OPENING", {"空间站位描述": "室内清晨；左窗紧闭。", "光源来源": "左窗没有打开，晨光经窗纸漫射透入。"}, False),
        ("UNRELATED-NO-DIALOGUE-DOES-NOT-CLOSE-WINDOW", {"空间站位描述": "室内清晨；无对白、窗户打开；女主逆光登场。", "光源来源": "敞开窗户外的晨光，浅墙反射弱补光。", "光源方向": "画面左后方晨光形成侧逆光，向室内纵深衰减。"}, False),
        ("OPEN-RIGHT-WINDOW-CANNOT-AUTHORIZE-CLOSED-LEFT", {"空间站位描述": "清晨正厅；左窗紧闭，右窗打开。", "起始状态": "桌上热茶冒出局部水汽。", "光影": "仅茶杯口上方一缕水汽，不扩散、不遮挡人物，背景木构清晰可辨。", "光源来源": "晨光穿过左侧闭合木窗形成可见光束。", "光源方向": "光束从左前方斜落，向室内纵深衰减。"}, True),
        ("OPEN-RIGHT-WINDOW-AUTHORIZES-ITS-OWN-LOCAL-RAY", {"空间站位描述": "清晨正厅；左窗紧闭，右窗打开。", "起始状态": "桌上热茶冒出局部水汽。", "光影": "仅茶杯口上方一缕水汽，不扩散、不遮挡人物，其余空气清澈，背景木构清晰可辨，高光不过曝。", "光源来源": "晨光穿过右侧敞开木窗，仅在茶杯上方水汽内形成局部可见光束。", "光源方向": "局部光束从右前方斜落，向室内纵深衰减。"}, False),
        ("AMBIGUOUS-WINDOW-DOES-NOT-BORROW-OTHER-OPENING", {"空间站位描述": "清晨正厅；左窗紧闭，右窗打开。", "起始状态": "桌上热茶冒出局部水汽。", "光影": "仅茶杯口上方一缕水汽，不扩散、不遮挡人物，背景木构清晰可辨。", "光源来源": "晨光穿过木窗形成可见光束。", "光源方向": "光束从右前方斜落，向室内纵深衰减。"}, True),
        ("SHADOW-SIDE-IS-NOT-BACKLIGHT-SETUP", {"光影": clean_room["光影"] + "女主背光一侧面部保留暗部质感。"}, False),
        ("CANDLE-LIGHT-SYNONYM-LOCAL-HALATION", {"空间站位描述": "夜晚卧房，窗户关闭。", "起始状态": "案上红烛燃着。", "光源来源": "烛光为主光，浅墙提供极弱反射光。", "光影": "空气清澈，背景木构可辨，曝光正常、高光不过曝。仅烛火边缘出现局部极轻微halation，不扩散至人脸与背景。"}, False),
        ("CANDLE-LIGHT-CANNOT-AUTHORIZE-FULL-FRAME-HALATION", {"空间站位描述": "夜晚卧房，窗户关闭。", "起始状态": "案上红烛燃着。", "光源来源": "烛光为主光，浅墙提供极弱反射光。", "光影": "空气清澈，背景木构可辨。仅烛火边缘出现局部极轻微halation，同时镜头扩散覆盖全画面。"}, True),
    ]
    for room_name in ("卧房", "书房", "账房", "厅堂"):
        lighting_cases.append(("ROOM-ALIAS-" + room_name, {"空间站位描述": "清晨" + room_name + "，窗户闭合。", "光影": "薄雾弥漫，背景不泛白。"}, True))
    for test_id, changes, should_fail in lighting_cases:
        faults = _lighting_context_faults({**clean_room, **changes})
        results.append({
            "test_id": "SPC-THG17-" + test_id,
            "passed": bool(faults) == should_fail,
            "actual_faults": faults,
        })
    run_code_test(
        "SPC-BGM-LITERAL-IN-EXECUTION-FIELD",
        lambda obj: obj["shots"][0].__setitem__("生成限制", "9:16竖屏；禁止BGM、字幕与水印。"),
        "E-AUDIO-STAGE-001",
        "THG-AUDIO",
    )

    row = project_export_row(base["shots"][0])
    formal = str(row["正式分镜母版"])
    gpt = str(row["GPT关键帧生成指令"])
    expected_first_line = "镜头序号：EP05-001（4.0s；累计4.0s）"
    prohibited_gpt = (
        "【0.0—", "说道：", "大嫂，没来晚吧", "口型", "音量", "语速", "环境音效",
        "脚步", "BGM", "硬切", "转场", "推镜", "跟镜", "由远及近", "几秒后", "随后",
    )
    projection_passed = (
        list(row) == COMPACT_MOTHER_COLUMNS
        and formal.startswith(expected_first_line + "\n\n镜头类型：")
        and all(f"\n\n{field}：" in formal for field in (
            "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
            "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
            "转场类型", "切镜/转场提示词",
        ))
        and "\n\n音色：\n\n基础声线：" in formal
        and formal.count("9:16竖屏") == 1
        and gpt.count("9:16竖屏") == 1
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

    runtime_rules = json.loads(RULES.read_text(encoding="utf-8"))
    hard_gates = runtime_rules.get("gates", {}).get("template_hard_gates", [])
    audit_types = (
        runtime_rules.get("windowed_execution_policy", {}).get("audit_types", {})
    )
    production_audit = audit_types.get("production_preflight", {})
    independent_audit = audit_types.get("independent_audit", {})
    thg17_passed = (
        [row.get("gate_id") for row in hard_gates]
        == [f"THG-{index:02d}" for index in range(1, 18)]
        and runtime_rules.get("compact_master_contract", {}).get(
            "projection_text_normalization"
        ) == "UTF-8_NFC_LF"
        and production_audit.get("may_issue_release_verdict") is False
        and production_audit.get("audit_overlay_status") == "PENDING_AUDIT"
        and production_audit.get("audit_issue_counts") is None
        and independent_audit.get("may_issue_release_verdict") is True
        and independent_audit.get("must_recompute_all_quality_conclusions") is True
    )
    results.append({
        "test_id": "SPC-THG01-17-AUDIT-AUTHORITY-AND-RULE-CERTIFICATE",
        "passed": thg17_passed,
        "hard_gate_ids": [row.get("gate_id") for row in hard_gates],
        "projection_text_normalization": runtime_rules.get(
            "compact_master_contract", {}
        ).get("projection_text_normalization"),
        "production_preflight": production_audit,
        "independent_audit": independent_audit,
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
