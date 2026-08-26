#!/usr/bin/env python3
"""R10 functional validator for Chinese short-drama storyboard JSON.

The validator is intentionally deterministic.  It validates the canonical R10
field contract and the four learned method modules instead of merely checking
that a heading or keyword exists.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION = "V16.3-148-R10-StableRuntime"
SCHEMA_VERSION = "R10.0"
WORKFLOW_PROFILE = "R10-PG1"

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ABSTRACT_EMOTIONS = {
    "开心", "委屈", "紧张", "生气", "惊讶", "害怕", "悲伤", "厌恶", "惊喜",
    "困惑", "自豪", "羞涩", "惆怅", "平静", "调皮", "冷漠", "焦虑", "惊恐",
    "欣慰", "期待", "伤心", "感动", "爱慕", "尴尬", "放松",
}
DETAIL_PATTERNS = {
    "mouth": re.compile(r"嘴角|嘴唇|上唇|下唇|唇线|嘴巴|下颌|下巴"),
    "eyes": re.compile(r"眼神|目光|眼睛|眼眶|瞳孔|睫毛|眼角"),
    "face": re.compile(r"眉|额头|脸颊|面部|鼻梁|面颊|下颚"),
    "hands": re.compile(r"手|指|拳|掌|袖口"),
    "body": re.compile(r"肩|颈|身体|上身|胸口|背部|姿态|前倾|后仰|蜷缩|僵住"),
    "gaze": re.compile(r"视线|看向|注视|凝视|低垂|移开|锁定|闪躲|游移"),
    "breath": re.compile(r"呼吸|气息|吸气|吞咽"),
    "head": re.compile(r"低头|抬头|侧头|头部|歪头|后仰"),
}
INVISIBLE_SHOT_TERMS = ["书页", "书上内容", "POV书", "第一视角看书", "手部特写", "背影", "远景"]
FACE_TERMS = ["眼睛", "眼神", "目光", "眉", "嘴唇", "嘴角", "瞳孔", "脸颊", "眼眶", "睫毛", "面部", "眨眼"]
FACE_VISIBLE_TERMS = ["面部可见", "眼睛可见", "侧脸可见", "人物反应", "过肩同框", "OTS同框"]
VAGUE_TERMS = ["电影感", "高级感", "氛围感", "自然移动", "高级运镜", "电影感运镜"]
STATIC_MOTIONS = ["无运镜", "固定镜头", "静态镜头", "固定机位"]
HARD_TIMECODE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:-|–|—|~|～|至|到)\s*\d+(?:\.\d+)?\s*秒|第\s*\d+(?:\.\d+)?\s*秒|\d+(?:\.\d+)?\s*秒(?:时|后|内|左右))"
)

VOICE_DIMENSION_PATTERNS = {
    "声音强弱": re.compile(r"声音|音量|放轻|压低|变亮|变稳|抬高"),
    "语速节奏": re.compile(r"语速|一字一句|断断续续|咬字"),
    "呼吸状态": re.compile(r"呼吸|气息|吸气"),
    "停顿位置": re.compile(r"停顿|停一下|开口前|句中|关键词前|卡住"),
    "重音位置": re.compile(r"重音|加重|强调|咬重"),
    "尾音变化": re.compile(r"尾音|上扬|下压|发虚|放柔|拉长|落下|收住"),
}

PUBLIC_SCENE_RE = re.compile(
    r"街道|街市|闹市|市集|集市|市场|商街|商业街|长街|巷口|巷道|城门|码头|渡口|"
    r"车站|驿站|驿道|茶馆|酒楼|饭馆|餐馆|客栈大堂|客栈前厅|庙会|寺院前庭|山门|"
    r"店铺|药铺|医馆|衙门前|衙门外|宫门|宫道|广场"
)
PUBLIC_EMPTY_RE = re.compile(r"空街|空巷|无人街|街上无人|人迹罕至|闭市|清场|人群已撤离|店门紧闭")
PUBLIC_EXEMPTION_CAUSE_RE = re.compile(
    r"宵禁|闭市|封锁|疏散|清场|戒严|灾后|深夜|黎明前|停业|剧情要求|危险预警|追捕"
)
PUBLIC_DENSITY_RE = re.compile(
    r"稀疏|低密度|少量|两三|三五|零散|中等密度|中等人流|适中人流|密集|高密度|拥挤|人潮|"
    r"(?:约)?[一二三四五六七八九十百\d]+(?:至|到|-|—)?[一二三四五六七八九十百\d]*名(?:行人|路人|百姓|顾客|摊贩|脚夫|守卫|香客|食客|茶客)"
)
PUBLIC_ROLE_RE = re.compile(
    r"行人|路人|百姓|摊贩|商贩|小贩|顾客|食客|茶客|脚夫|挑夫|车夫|骑手|伙计|小二|"
    r"掌柜|守卫|兵丁|差役|僧人|香客|船工|纤夫|搬运工|店家|货郎|孩童|老人"
)
PUBLIC_ACTION_RE = re.compile(
    r"行走|穿行|挑选|交谈|驻足|避让|叫卖|吆喝|搬运|装卸|推车|赶车|牵马|端菜|收拾|"
    r"排队|巡逻|查验|上香|进出|开门|收摊|交易|付钱|张望|擦肩|落座|饮茶|吃饭|挑担|招呼"
)
PUBLIC_VISUAL_LAYER_PATTERNS = {
    "near": re.compile(r"前景|近处|身侧|画面左侧|画面右侧"),
    "mid": re.compile(r"中景|中层|街面|周围"),
    "far": re.compile(r"远景|远处|背景|后景|街口|巷口|城门方向"),
}
PUBLIC_AUDIO_LAYER_PATTERNS = {
    "near": re.compile(r"近处|脚边|身侧|贴近|前景"),
    "mid": re.compile(r"中层|中景|街面|周围|身后"),
    "far": re.compile(r"远处|远景|画外|街口|巷口|城门方向|门外"),
}
PUBLIC_AUDIO_FAMILY_PATTERNS = {
    "movement": re.compile(r"脚步|鞋底|踩水|踩雪|衣料|衣摆|木屐|马蹄|车轮|车辙|轿杆|推车|担子|扁担"),
    "crowd": re.compile(r"人声底噪|低声交谈|交谈声|含混人声|人群低语|模糊人声|远处人声|客声|食客低语"),
    "commerce": re.compile(r"叫卖|吆喝|铜钱|算盘|货物|瓷碗|碗筷|杯盏|盘碟|柜台|案板|秤砣"),
    "weather_nature": re.compile(r"风声|雨声|雨滴|滴水|溅水|踏雪|雪地|浪声|水声|橹声|钟声|鸟鸣|虫鸣|檐铃|门轴|木门"),
    "work": re.compile(r"搬运|装卸|锤打|切菜|炉火|油锅|劈柴|扫地"),
    "official": re.compile(r"守卫口令|查验声|城门|木栓|甲片|兵器|号角"),
}
PUBLIC_DIALOGUE_DUCK_RE = re.compile(r"退至底层|压至底层|压低|不遮盖(?:对白|台词)|对白清晰|台词清晰|对白优先|为对白让位")
PUBLIC_MODERN_AUDIO_RE = re.compile(r"汽车喇叭|手机铃声|电子提示音|广播|扩音器|警笛|发动机|地铁|电车")
PUBLIC_ANONYMOUS_CLEAR_SPEECH_RE = re.compile(
    r"(?:路人|人群|百姓|食客|茶客|摊贩|商贩|小贩|顾客|客人)(?:甲|乙|丙)?"
    r"(?:说|喊|道|台词)[：:]?\s*[“‘「『\"]"
)
PUBLIC_FORBIDDEN_EXTRAS_RE = re.compile(
    r"禁止(?:出现|添加|生成|加入)?(?:任何|所有|全部)?(?:额外角色|路人|人群|群众|行人)(?:出现|入镜|进入)?"
)
PUBLIC_TRANSITION_RE = re.compile(r"涌入|散开|疏散|离开|聚集|人流变化|逐渐(?:变得)?(?:稀疏|密集)|由(?:稀疏|密集)变")
PUBLIC_WIDE_RE = re.compile(r"大全景|全景|远景|建立")
PUBLIC_SOURCE_PAIRS = [
    (re.compile(r"马蹄|马嘶"), re.compile(r"马|马车|骑手|车夫")),
    (re.compile(r"车轮|木轮|车辙"), re.compile(r"车|马车|板车|推车|轿")),
    (re.compile(r"碗筷|杯盏|盘碟"), re.compile(r"食客|茶客|客人|伙计|小二|饭桌|茶桌|摊位")),
    (re.compile(r"叫卖|吆喝"), re.compile(r"摊贩|商贩|小贩|货郎|摊位|店家")),
    (re.compile(r"钟声|木鱼"), re.compile(r"寺|庙|钟楼|僧人|香客")),
]


def _candidate_rule_paths(explicit: str | None = None) -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        here.parent / "rules" / "storyboard_runtime_rules.json",
        here.parent / "references" / "storyboard_runtime_rules.json",
        here / "storyboard_runtime_rules.json",
        Path.cwd() / "rules" / "storyboard_runtime_rules.json",
        Path.cwd() / "storyboard_runtime_rules.json",
    ])
    return candidates


def load_runtime_rules(explicit: str | None = None) -> dict[str, Any]:
    for path in _candidate_rule_paths(explicit):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("storyboard_runtime_rules.json not found")


def _catalog_map(rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x["error_code"]: x for x in rules.get("error_catalog", [])}


def cjk_len(value: Any) -> int:
    return len(CJK_RE.findall(str(value or "")))


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def is_none_value(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text in {"无", "不适用", "N/A", "none", "None"} or text.startswith("不适用：")


def _issue(catalog: dict[str, dict[str, Any]], code: str, location: str, evidence: str, repair: str | None = None, severity: str | None = None) -> dict[str, Any]:
    spec = catalog.get(code, {})
    sev = severity or spec.get("severity", "fail")
    return {
        "error_code": code,
        "severity": sev,
        "blocking": bool(spec.get("blocking", sev == "fail")),
        "location": location,
        "evidence": evidence,
        "repair_action": repair or spec.get("repair_action", "按R10规则修复后重跑。"),
    }


def normalize_shot(raw: dict[str, Any], aliases: dict[str, str]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    shot = copy.deepcopy(raw)
    used = []
    for old, new in aliases.items():
        if old in shot and new not in shot:
            shot[new] = shot[old]
            used.append((old, new))
    return shot, used


def detail_categories(text: str) -> list[str]:
    return [name for name, pattern in DETAIL_PATTERNS.items() if pattern.search(text)]


def match_motion(value: str, motions: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = value.strip().casefold()
    for motion in motions:
        names = [motion["name"], *motion.get("aliases", [])]
        if any(str(name).casefold() in normalized or normalized in str(name).casefold() for name in names):
            return motion
    return None


def parse_blink_range(text: str) -> tuple[int, int] | None:
    patterns = [
        r"(\d+)\s*(?:-|–|—|~|～|至|到)\s*(\d+)\s*次(?:自然)?眨眼",
        r"(?:自然)?眨眼\s*(\d+)\s*(?:-|–|—|~|～|至|到)\s*(\d+)\s*次",
        r"(\d+)\s*次(?:自然)?眨眼",
        r"(?:自然)?眨眼\s*(\d+)\s*次",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            if len(m.groups()) == 2:
                return int(m.group(1)), int(m.group(2))
            value = int(m.group(1))
            return value, value
    return None


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _validate_punctuation(dialogue: str, delivery: str) -> list[str]:
    missing = []
    checks = [
        (("……" in dialogue or "..." in dialogue), ["停顿", "卡住", "断续"], "省略号停顿/卡住"),
        (("——" in dialogue or "—" in dialogue), ["转折", "打断", "强调", "断裂"], "破折号转折/打断"),
        (("？！" in dialogue or "!?" in dialogue or "?!" in dialogue), ["质问", "爆发", "加重", "重音"], "问叹号质问/爆发"),
        (("？" in dialogue or "?" in dialogue), ["上扬", "试探", "质问", "疑问", "收住"], "问号尾音/意图"),
        (("！" in dialogue or "!" in dialogue), ["重音", "加强", "爆发", "警告", "兴奋"], "感叹号情绪/重音"),
        (("～" in dialogue or "~" in dialogue), ["拉长", "轻快", "柔软", "放柔"], "波浪号尾音"),
    ]
    for triggered, expected, label in checks:
        if triggered and not _has_any(delivery, expected):
            missing.append(label)
    return missing


def _pause_budget(dialogue: str) -> float:
    ellipses = dialogue.count("……") + dialogue.count("...")
    dashes = dialogue.count("——") + dialogue.count("—")
    explosive = dialogue.count("？！") + dialogue.count("?!") + dialogue.count("!?")
    return 0.3 * (ellipses + dashes) + 0.2 * explosive


def _missing_fields(obj: Any, fields: list[str]) -> list[str]:
    if not isinstance(obj, dict):
        return list(fields)
    return [field for field in fields if field not in obj or is_empty(obj.get(field))]


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matched_pattern_names(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    return {name for name, pattern in patterns.items() if pattern.search(text)}


def _public_density_class(text: str) -> str | None:
    if re.search(r"稀疏|低密度|少量|两三|三五|零散", text):
        return "low"
    if re.search(r"中等密度|中等人流|适中人流", text):
        return "medium"
    if re.search(r"密集|高密度|拥挤|人潮", text):
        return "high"
    return None


def _validate_public_scene_ecology(
    shots: list[dict[str, Any]],
    batch_by_id: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate ordinary-person and ambience ecology in public locations.

    The checks intentionally operate on concrete source, layer, action and
    continuity evidence.  A generic ``人群`` or ``环境声`` placeholder cannot
    pass, while an explicitly motivated empty public space can be exempted.
    """
    issues: list[dict[str, Any]] = []
    metrics = {
        "public_scene_shot_count": 0,
        "public_scene_exemption_count": 0,
        "public_scene_visual_pass_count": 0,
        "public_scene_audio_pass_count": 0,
        "public_scene_batch_pass_count": 0,
    }
    scene_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for index, shot in enumerate(shots, 1):
        location = f"shots[{index}]"
        batch_id = str(shot.get("生成批次", ""))
        batch = batch_by_id.get(batch_id, {})
        visual_text = " ".join(str(shot.get(field, "")) for field in (
            "可见主体", "人物调度", "画面内容", "环境动态", "生成限制"
        ))
        scene_text = f"{visual_text} {batch.get('场景', '')} {batch.get('起始画面', '')}"
        if not PUBLIC_SCENE_RE.search(scene_text):
            continue

        metrics["public_scene_shot_count"] += 1
        empty_declared = bool(PUBLIC_EMPTY_RE.search(scene_text))
        has_empty_cause = bool(PUBLIC_EXEMPTION_CAUSE_RE.search(scene_text))
        exempt = empty_declared and has_empty_cause
        if exempt:
            metrics["public_scene_exemption_count"] += 1
            continue
        if empty_declared and not has_empty_cause:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-ECOLOGY-001",
                f"{location}.画面内容/环境动态",
                "公共场景声明为空场，但没有宵禁、闭市、封锁、疏散、剧情要求等可核验原因。",
            ))

        density_ok = bool(PUBLIC_DENSITY_RE.search(visual_text))
        role_ok = bool(PUBLIC_ROLE_RE.search(visual_text))
        action_ok = bool(PUBLIC_ACTION_RE.search(visual_text))
        visual_layers = _matched_pattern_names(visual_text, PUBLIC_VISUAL_LAYER_PATTERNS)
        wide = bool(PUBLIC_WIDE_RE.search(f"{shot.get('镜头类型', '')} {shot.get('功能类型', '')}"))
        required_visual_layers = 2 if wide else 1
        forbidden_extras = PUBLIC_FORBIDDEN_EXTRAS_RE.search(str(shot.get("生成限制", "")))

        if not role_ok or forbidden_extras:
            evidence = []
            if not role_ok:
                evidence.append("没有与地点/时代匹配的普通人物角色")
            if forbidden_extras:
                evidence.append(f"生成限制排除了公共人物：{forbidden_extras.group(0)!r}")
            issues.append(_issue(
                catalog,
                "E-PUBLIC-ECOLOGY-001",
                f"{location}.画面内容/环境动态/生成限制",
                "；".join(evidence),
            ))

        missing_visual = []
        if not density_ok:
            missing_visual.append("人流密度")
        if not action_ok:
            missing_visual.append("错峰生活动作")
        if len(visual_layers) < required_visual_layers:
            missing_visual.append(f"空间层次（需{required_visual_layers}层，现有{sorted(visual_layers)}）")
        if missing_visual:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-CROWD-001",
                f"{location}.人物调度/画面内容/环境动态",
                f"公共人物生态缺少：{missing_visual}；当前={visual_text!r}",
            ))
        elif role_ok and not forbidden_extras:
            metrics["public_scene_visual_pass_count"] += 1

        audio_text = str(shot.get("环境音效", ""))
        audio_families = _matched_pattern_names(audio_text, PUBLIC_AUDIO_FAMILY_PATTERNS)
        audio_layers = _matched_pattern_names(audio_text, PUBLIC_AUDIO_LAYER_PATTERNS)
        required_audio_count = 3 if wide else 2
        dialogue = str(shot.get("台词", ""))
        audio_missing = []
        if is_none_value(audio_text):
            audio_missing.append("可听环境声")
        if len(audio_families) < required_audio_count:
            audio_missing.append(f"声源族（需{required_audio_count}类，现有{sorted(audio_families)}）")
        if len(audio_layers) < required_audio_count:
            audio_missing.append(f"声场层次（需{required_audio_count}层，现有{sorted(audio_layers)}）")
        if not is_none_value(dialogue) and not PUBLIC_DIALOGUE_DUCK_RE.search(f"{audio_text} {shot.get('生成限制', '')}"):
            audio_missing.append("对白避让/ducking")
        if audio_missing:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-AUDIO-001",
                f"{location}.环境音效",
                f"公共场景环境音缺少：{audio_missing}；当前={audio_text!r}",
            ))
        else:
            metrics["public_scene_audio_pass_count"] += 1

        av_faults = []
        modern = PUBLIC_MODERN_AUDIO_RE.search(audio_text)
        if modern:
            av_faults.append(f"古装/非现代公共场景出现现代声源{modern.group(0)!r}")
        anonymous_line = PUBLIC_ANONYMOUS_CLEAR_SPEECH_RE.search(audio_text)
        if anonymous_line:
            av_faults.append(f"匿名群演被分配清晰完整台词{anonymous_line.group(0)!r}")
        offscreen_cue = bool(re.search(r"画外|远处|门外|街另一端|巷口|城门方向", audio_text))
        for sound_pattern, source_pattern in PUBLIC_SOURCE_PAIRS:
            sound_hit = sound_pattern.search(audio_text)
            if sound_hit and not source_pattern.search(visual_text) and not offscreen_cue:
                av_faults.append(f"声源{sound_hit.group(0)!r}在画内不可见，也未标为可信画外声")

        batch_sound = str(batch.get("声音", ""))
        batch_environment = " ".join(str(batch.get(field, "")) for field in (
            "起始画面", "动作过程", "结束状态", "环境动态", "独立执行提示词"
        ))
        batch_families = _matched_pattern_names(batch_sound, PUBLIC_AUDIO_FAMILY_PATTERNS)
        batch_layers = _matched_pattern_names(batch_sound, PUBLIC_AUDIO_LAYER_PATTERNS)
        batch_visual_ok = bool(PUBLIC_ROLE_RE.search(batch_environment) and PUBLIC_ACTION_RE.search(batch_environment))
        batch_audio_ok = (
            len(batch_families) >= required_audio_count
            and len(batch_layers) >= required_audio_count
            and bool(audio_families & batch_families)
        )
        if not batch_visual_ok:
            av_faults.append("Seedance批次环境动态未独立写明公共人物角色及动作")
        if not batch_audio_ok:
            av_faults.append(
                f"Seedance批次声音未复现镜头声源/层次：镜头={sorted(audio_families)}，批次={sorted(batch_families)}，批次层次={sorted(batch_layers)}"
            )
        if av_faults:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-AVSYNC-001",
                f"{location}/batch:{batch_id}",
                "；".join(av_faults),
            ))
        else:
            metrics["public_scene_batch_pass_count"] += 1

        scene_id = str(shot.get("场景连续性ID", ""))
        if scene_id:
            scene_rows[scene_id].append({
                "index": index,
                "shot_id": str(shot.get("镜头编号", "")),
                "density": _public_density_class(visual_text),
                "text": f"{visual_text} {audio_text}",
            })

    for scene_id, rows in scene_rows.items():
        for previous, current in zip(rows, rows[1:]):
            if (
                previous["density"]
                and current["density"]
                and previous["density"] != current["density"]
                and not PUBLIC_TRANSITION_RE.search(f"{previous['text']} {current['text']}")
            ):
                issues.append(_issue(
                    catalog,
                    "E-PUBLIC-CONT-001",
                    f"scene:{scene_id}:{previous['shot_id']}->{current['shot_id']}",
                    f"同场连续镜人流密度从{previous['density']}跳到{current['density']}，无涌入、散开、疏散或离开等画面过渡。",
                ))

    return issues, metrics


def _validate_chapter_intake(
    obj: dict[str, Any],
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the conditional novel/chapter-to-storyboard planning gate."""
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "chapter_intake_active": False,
        "chapter_complete_read": None,
        "chapter_core_event_coverage_percent": None,
        "chapter_causal_link_coverage_percent": None,
        "chapter_character_state_change_coverage_percent": None,
        "chapter_protected_dialogue_coverage_percent": None,
        "chapter_source_treatment_percent": None,
    }
    intake = obj.get("source_ingestion")
    if not isinstance(intake, dict) or intake.get("mode") != "chapter_to_storyboard":
        return issues, metrics

    metrics["chapter_intake_active"] = True
    policy = rules.get("modules", {}).get("CHAPTER_INTAKE", {})
    required_fields = policy.get("required_fields", [])
    missing = [field for field in required_fields if field not in intake]
    if missing:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion",
            f"章节前置规划缺字段：{missing}",
        ))

    chunk_count = int(_as_number(intake.get("chunk_count"), 0))
    chunks_read = int(_as_number(intake.get("chunks_read"), 0))
    complete_read = intake.get("complete_read") is True
    metrics["chapter_complete_read"] = complete_read and chunk_count >= 1 and chunks_read == chunk_count
    if not metrics["chapter_complete_read"]:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.complete_read",
            f"complete_read={intake.get('complete_read')!r}; chunk_count={chunk_count}; chunks_read={chunks_read}",
        ))

    boundary = intake.get("boundary_context")
    boundary_missing = _missing_fields(boundary, policy.get("boundary_fields", []))
    if boundary_missing:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.boundary_context",
            f"章节边界缺字段：{boundary_missing}",
        ))
    if is_empty(intake.get("chapter_id")) or is_empty(intake.get("chapter_goal")):
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.chapter_id/chapter_goal",
            "chapter_id或chapter_goal为空。",
        ))
    if intake.get("uncertainty_status") not in {"clear", "resolved"}:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.uncertainty_status",
            f"仍有未解决歧义：{intake.get('uncertainty_status')!r}",
        ))

    events = intake.get("events")
    event_ids: set[str] = set()
    event_source_unit_ids: set[str] = set()
    if not isinstance(events, list) or not events:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.events", "章节核心事件为空。"))
        events = []
    for index, event in enumerate(events, 1):
        location = f"source_ingestion.events[{index}]"
        if not isinstance(event, dict):
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, "事件不是对象。"))
            continue
        event_missing = _missing_fields(event, ["event_id", "source_unit_ids", "value_grade", "causal_role", "target_beats"])
        if event_missing or not isinstance(event.get("source_unit_ids"), list) or not isinstance(event.get("target_beats"), list):
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, f"事件字段缺失或类型错误：{event_missing}"))
            continue
        event_id = str(event.get("event_id"))
        if event_id in event_ids:
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, f"重复event_id：{event_id}"))
        event_ids.add(event_id)
        event_source_unit_ids.update(str(x) for x in event.get("source_unit_ids", []))

    causal_links = intake.get("causal_links")
    if not isinstance(causal_links, list) or not causal_links:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.causal_links", "章节因果链为空。"))
        causal_links = []
    for index, link in enumerate(causal_links, 1):
        missing_link = _missing_fields(link, ["from_event_id", "to_event_id", "relation"])
        unknown = [
            str(link.get(field))
            for field in ("from_event_id", "to_event_id")
            if not is_empty(link.get(field)) and str(link.get(field)) not in event_ids
        ] if isinstance(link, dict) else []
        if missing_link or unknown:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-INTAKE-001",
                f"source_ingestion.causal_links[{index}]",
                f"因果字段缺失={missing_link}; 未知事件={unknown}",
            ))

    state_changes = intake.get("character_state_changes")
    state_required = [
        "character", "start_state", "trigger_event_ids", "end_state",
        "cognition_before", "cognition_after", "forbidden_knowledge",
    ]
    if not isinstance(state_changes, list) or not state_changes:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.character_state_changes", "人物状态变化为空。"))
        state_changes = []
    for index, row in enumerate(state_changes, 1):
        row_missing = _missing_fields(row, state_required)
        triggers = row.get("trigger_event_ids") if isinstance(row, dict) else None
        unknown = [str(x) for x in triggers or [] if str(x) not in event_ids] if isinstance(triggers, list) else []
        if row_missing or not isinstance(triggers, list) or not triggers or unknown:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-INTAKE-001",
                f"source_ingestion.character_state_changes[{index}]",
                f"人物变化缺字段={row_missing}; 未知触发事件={unknown}",
            ))

    beat_plan = intake.get("beat_plan")
    beat_policy = rules.get("modules", {}).get("ADAPTATION_BUDGET", {})
    beat_required = beat_policy.get("beat_required_fields", [])
    beat_ids: set[str] = set()
    covered_events: set[str] = set()
    beat_source_unit_ids: set[str] = set()
    if not isinstance(beat_plan, list) or not beat_plan:
        issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.beat_plan", "节拍计划为空。"))
        beat_plan = []
    for index, beat in enumerate(beat_plan, 1):
        location = f"source_ingestion.beat_plan[{index}]"
        if not isinstance(beat, dict):
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍不是对象。"))
            continue
        beat_missing = _missing_fields(beat, beat_required)
        if beat_missing:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"节拍缺字段：{beat_missing}"))
            continue
        beat_id = str(beat.get("beat_id"))
        beat_ids.add(beat_id)
        beat_events = beat.get("event_ids")
        source_units = beat.get("source_unit_ids")
        shot_range = beat.get("shot_count_range")
        if not isinstance(beat_events, list) or not beat_events or not isinstance(source_units, list) or not source_units:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍必须映射事件和源文单元。"))
        else:
            unknown = [str(x) for x in beat_events if str(x) not in event_ids]
            if unknown:
                issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"节拍引用未知事件：{unknown}"))
            covered_events.update(str(x) for x in beat_events if str(x) in event_ids)
            beat_source_unit_ids.update(str(x) for x in source_units)
        if (
            not isinstance(shot_range, list)
            or len(shot_range) != 2
            or _as_number(shot_range[0], 0) < 1
            or _as_number(shot_range[1], 0) < _as_number(shot_range[0], 0)
        ):
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"shot_count_range非法：{shot_range!r}"))
        if _as_number(beat.get("target_duration_seconds"), 0) <= 0:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍目标时长必须大于0。"))
    missing_event_beats = sorted(event_ids - covered_events)
    if missing_event_beats:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-BUDGET-001",
            "source_ingestion.beat_plan",
            f"核心事件未进入节拍：{missing_event_beats}",
        ))
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            continue
        unknown_beats = [str(x) for x in event.get("target_beats", []) if str(x) not in beat_ids]
        if unknown_beats:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-BUDGET-001",
                f"source_ingestion.events[{index}].target_beats",
                f"事件目标节拍不存在：{unknown_beats}",
            ))

    selection = rules.get("modules", {}).get("SOURCE_SELECTION", {})
    value_axes = selection.get("value_axes", [])
    allowed_grades = set(selection.get("allowed_grades", []))
    allowed_treatments = set(selection.get("allowed_treatments", []))
    treatment_rows = intake.get("source_treatment_ledger")
    treatment_unit_ids: set[str] = set()
    if not isinstance(treatment_rows, list) or not treatment_rows:
        issues.append(_issue(catalog, "E-SOURCE-SELECT-001", "source_ingestion.source_treatment_ledger", "源文取舍台账为空。"))
        treatment_rows = []
    for index, row in enumerate(treatment_rows, 1):
        location = f"source_ingestion.source_treatment_ledger[{index}]"
        if not isinstance(row, dict):
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "取舍台账行不是对象。"))
            continue
        row_missing = _missing_fields(row, [
            "source_unit_id", "source_type", "source_text", "value_axes",
            "value_grade", "cinematic_function", "treatment", "reason",
        ])
        if row_missing:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"取舍台账缺字段：{row_missing}"))
            continue
        unit_id = str(row.get("source_unit_id"))
        if unit_id in treatment_unit_ids:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"重复source_unit_id：{unit_id}"))
        treatment_unit_ids.add(unit_id)
        axes = row.get("value_axes")
        missing_axes = [axis for axis in value_axes if not isinstance(axes, dict) or axis not in axes]
        if missing_axes:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"多轴价值判断缺失：{missing_axes}"))
        grade = str(row.get("value_grade"))
        treatment = str(row.get("treatment"))
        if grade not in allowed_grades or treatment not in allowed_treatments:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"非法等级/处理：{grade!r}/{treatment!r}"))
        targets = row.get("target_beats")
        if treatment == "删除":
            has_authorization = not is_none_value(row.get("authorization"))
            has_redundancy = not is_none_value(row.get("redundancy_evidence"))
            if not (has_authorization or has_redundancy):
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "删除缺授权或冗余证据。"))
            if not is_none_value(row.get("cinematic_function")) and is_none_value(row.get("retained_function_in")):
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "删除内容承担电影功能但未说明由哪个节拍承接。"))
        elif not isinstance(targets, list) or not targets:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"处理方式{treatment!r}缺目标节拍。"))
        else:
            unknown = [str(x) for x in targets if str(x) not in beat_ids]
            if unknown:
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"目标节拍不存在：{unknown}"))
    missing_treatment_units = sorted((event_source_unit_ids | beat_source_unit_ids) - treatment_unit_ids)
    if missing_treatment_units:
        issues.append(_issue(
            catalog,
            "E-SOURCE-SELECT-001",
            "source_ingestion.source_treatment_ledger",
            f"事件或节拍引用的源文单元未进入取舍台账：{missing_treatment_units}",
        ))

    protected = intake.get("protected_dialogue")
    if not isinstance(protected, list):
        issues.append(_issue(catalog, "E-SOURCE-SELECT-001", "source_ingestion.protected_dialogue", "protected_dialogue必须是数组。"))
        protected = []
    protected_required_seconds = 0.0
    for index, row in enumerate(protected, 1):
        location = f"source_ingestion.protected_dialogue[{index}]"
        row_missing = _missing_fields(row, ["source_unit_id", "speaker", "text", "functions", "target_beats"])
        targets = row.get("target_beats") if isinstance(row, dict) else None
        functions = row.get("functions") if isinstance(row, dict) else None
        unknown = [str(x) for x in targets or [] if str(x) not in beat_ids] if isinstance(targets, list) else []
        if row_missing or not isinstance(functions, list) or not functions or not isinstance(targets, list) or not targets or unknown:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"对白保护字段缺失={row_missing}; 未知节拍={unknown}"))
            continue
        if str(row.get("source_unit_id")) not in treatment_unit_ids:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"保护对白源文单元未进入取舍台账：{row.get('source_unit_id')!r}"))
        dialogue_text = str(row.get("text"))
        protected_required_seconds += cjk_len(dialogue_text) / 2.8 + _pause_budget(dialogue_text)

    budget = intake.get("adaptation_budget")
    budget_required = beat_policy.get("required_budget_fields", [])
    budget_missing = _missing_fields(budget, budget_required)
    if budget_missing:
        issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"改编预算缺字段：{budget_missing}"))
    else:
        target = _as_number(budget.get("target_duration_seconds"), 0)
        dialogue_seconds = _as_number(budget.get("dialogue_seconds"), -1)
        action_seconds = _as_number(budget.get("action_seconds"), -1)
        transition_seconds = _as_number(budget.get("transition_seconds"), -1)
        shot_min = int(_as_number(budget.get("shot_count_min"), 0))
        shot_max = int(_as_number(budget.get("shot_count_max"), 0))
        component_sum = dialogue_seconds + action_seconds + transition_seconds
        if target <= 0 or min(dialogue_seconds, action_seconds, transition_seconds) < 0 or component_sum > target + 1e-9:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"目标={target:g}s，最低组成={component_sum:g}s，不可行。"))
        if dialogue_seconds + 1e-9 < protected_required_seconds:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget.dialogue_seconds", f"保护对白至少需{protected_required_seconds:.2f}s，预算仅{dialogue_seconds:.2f}s。"))
        if shot_min < 1 or shot_max < shot_min:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"镜头数范围非法：{shot_min}-{shot_max}"))

    visual_license = intake.get("visualization_license")
    if not isinstance(visual_license, list):
        issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", "source_ingestion.visualization_license", "visualization_license必须是数组。"))
        visual_license = []
    for index, row in enumerate(visual_license, 1):
        location = f"source_ingestion.visualization_license[{index}]"
        if not isinstance(row, dict):
            issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", location, "视觉化许可行不是对象。"))
            continue
        row_missing = _missing_fields(row, ["description", "source_unit_ids", "category", "plot_change"])
        if row_missing or not isinstance(row.get("source_unit_ids"), list) or not row.get("source_unit_ids") or row.get("category") not in {"allowed", "conditional"} or row.get("plot_change") is not False:
            issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", location, f"视觉化许可不合规：missing={row_missing}, row={row!r}"))
    new_plot_events = intake.get("new_plot_events")
    if not isinstance(new_plot_events, list) or new_plot_events:
        issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", "source_ingestion.new_plot_events", f"新增原文外剧情：{new_plot_events!r}"))

    coverage = intake.get("coverage_metrics")
    coverage_fields = policy.get("coverage_fields", [])
    threshold = _as_number(policy.get("coverage_required_percent"), 100)
    for field in coverage_fields:
        value = _as_number(coverage.get(field), -1) if isinstance(coverage, dict) else -1
        metrics[f"chapter_{field}"] = value
        if value != threshold:
            code = "E-SOURCE-SELECT-001" if field in {"protected_dialogue_coverage_percent", "source_treatment_percent"} else "E-CHAPTER-INTAKE-001"
            issues.append(_issue(catalog, code, f"source_ingestion.coverage_metrics.{field}", f"覆盖率必须为{threshold:g}%，当前{value:g}%。"))

    return issues, metrics


def _validate_preventive_gates(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    mode: str,
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "profile": obj.get("workflow_profile"),
        "source_mapping_percent": 0.0,
        "old_content_hit_count": 0,
        "cross_context_term_count": 0,
        "first_repair_effectiveness_percent": None,
        "residual_rate_percent": None,
        "side_effect_rate_percent": None,
    }
    policy = rules.get("preventive_gate_policy", {})
    expected_profile = policy.get("profile", WORKFLOW_PROFILE)
    required = mode in {"audit", "repair", "archive"}
    evidence = obj.get("prevention_evidence")
    if obj.get("workflow_profile") != expected_profile or not isinstance(evidence, dict):
        if required:
            issues.append(_issue(
                catalog,
                "E-GATE-001",
                "workflow_profile/prevention_evidence",
                f"expected profile={expected_profile!r}; got {obj.get('workflow_profile')!r}; evidence_type={type(evidence).__name__}",
            ))
        return issues, metrics

    required_blocks = policy.get("required_evidence_blocks", [])
    missing_blocks = [name for name in required_blocks if name not in evidence]
    if missing_blocks:
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence", f"缺少证据块：{missing_blocks}"))

    scope_lock = evidence.get("scope_lock")
    scope_fields = ["authoritative_source", "task_scope", "protected_scope", "active_rule_revision"]
    scope_missing = _missing_fields(scope_lock, scope_fields)
    if scope_missing or (isinstance(scope_lock, dict) and scope_lock.get("active_rule_revision") != expected_profile):
        issues.append(_issue(
            catalog,
            "E-GATE-001",
            "prevention_evidence.scope_lock",
            f"范围锁缺失={scope_missing}; revision={scope_lock.get('active_rule_revision') if isinstance(scope_lock, dict) else None!r}",
        ))

    shot_ids = {str(shot.get("镜头编号")) for shot in shots}
    story_blob = json.dumps({"shots": shots, "batches": batches}, ensure_ascii=False)
    ledger = evidence.get("source_ledger")
    covered_shots: set[str] = set()
    allowed_treatments = {"保留", "合并", "压缩", "授权删除", "借用"}
    if not isinstance(ledger, list) or not ledger:
        issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.source_ledger", "源文覆盖台账缺失或为空。"))
    else:
        for index, row in enumerate(ledger, 1):
            location = f"prevention_evidence.source_ledger[{index}]"
            missing = _missing_fields(row, ["source_id", "source_type", "source_text", "speaker", "treatment", "target_shots", "authorization"])
            if missing:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"台账缺字段：{missing}"))
                continue
            treatment = str(row.get("treatment"))
            targets = row.get("target_shots")
            if treatment not in allowed_treatments:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"非法处理方式：{treatment!r}"))
            if treatment == "授权删除":
                if is_none_value(row.get("authorization")):
                    issues.append(_issue(catalog, "E-SOURCE-005", location, "授权删除缺用户授权证据。"))
            elif not isinstance(targets, list) or not targets:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"处理方式{treatment!r}缺目标镜头。"))
            else:
                unknown = [str(x) for x in targets if str(x) not in shot_ids]
                if unknown:
                    issues.append(_issue(catalog, "E-SOURCE-005", location, f"目标镜头不存在：{unknown}"))
                covered_shots.update(str(x) for x in targets if str(x) in shot_ids)
        missing_shot_coverage = sorted(shot_ids - covered_shots)
        if missing_shot_coverage:
            issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.source_ledger", f"未进入源文台账的镜头：{missing_shot_coverage}"))
        metrics["source_mapping_percent"] = round(100.0 * len(covered_shots) / max(len(shot_ids), 1), 2)

    change_lock = evidence.get("change_lock")
    if not isinstance(change_lock, dict) or not isinstance(change_lock.get("must_include"), list) or not isinstance(change_lock.get("must_exclude"), list):
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.change_lock", "change_lock必须包含must_include与must_exclude数组。"))
    else:
        missing_required = [str(term) for term in change_lock["must_include"] if str(term).strip() and str(term) not in story_blob]
        if missing_required:
            issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.change_lock.must_include", f"必含内容未进入正式镜头/批次：{missing_required}"))
        residue = [str(term) for term in change_lock["must_exclude"] if str(term).strip() and str(term) in story_blob]
        if residue:
            metrics["old_content_hit_count"] = len(residue)
            issues.append(_issue(catalog, "E-CHANGELOCK-001", "prevention_evidence.change_lock.must_exclude", f"禁止残留内容仍命中：{residue}"))

    cognition = evidence.get("cognition_matrix")
    cognition_fields = ["character", "knows", "does_not_know", "relationship_stage", "emotion_stage", "forbidden"]
    if not isinstance(cognition, list) or not cognition:
        issues.append(_issue(catalog, "E-COGNITION-001", "prevention_evidence.cognition_matrix", "人物认知矩阵缺失或为空。"))
    else:
        for index, row in enumerate(cognition, 1):
            missing = _missing_fields(row, cognition_fields)
            if missing or not isinstance(row.get("knows"), list) or not isinstance(row.get("does_not_know"), list) or not isinstance(row.get("forbidden"), list):
                issues.append(_issue(catalog, "E-COGNITION-001", f"prevention_evidence.cognition_matrix[{index}]", f"人物认知字段缺失或类型错误：{missing}"))

    state_fields = policy.get("state_vector_fields", [])
    state_rows = evidence.get("scene_state_ledger")
    state_by_shot: dict[str, dict[str, Any]] = {}
    if isinstance(state_rows, list):
        state_by_shot = {str(row.get("shot_id")): row for row in state_rows if isinstance(row, dict)}
    missing_state_shots = sorted(shot_ids - set(state_by_shot))
    if missing_state_shots:
        issues.append(_issue(catalog, "E-CONTINUITY-001", "prevention_evidence.scene_state_ledger", f"缺场景状态的镜头：{missing_state_shots}"))
    for shot_id, row in state_by_shot.items():
        for label in ("start_state", "end_state"):
            missing = _missing_fields(row.get(label), state_fields)
            if missing:
                issues.append(_issue(catalog, "E-CONTINUITY-001", f"scene_state:{shot_id}.{label}", f"状态向量缺字段：{missing}"))
    previous: tuple[str, dict[str, Any]] | None = None
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        row = state_by_shot.get(shot_id)
        if row is None:
            previous = None
            continue
        scene_id = str(row.get("scene_id", shot.get("场景连续性ID", "")))
        if previous and previous[0] == scene_id:
            previous_row = previous[1]
            prior_end = previous_row.get("end_state", {})
            current_start = row.get("start_state", {})
            mismatches = {field: [prior_end.get(field), current_start.get(field)] for field in state_fields if prior_end.get(field) != current_start.get(field)}
            if mismatches and is_none_value(row.get("transition_explanation")):
                issues.append(_issue(catalog, "E-CONTINUITY-001", f"scene:{scene_id}:{previous_row.get('shot_id')}->{shot_id}", f"首尾状态不闭合：{mismatches}"))
        previous = (scene_id, row)

    plans = evidence.get("shot_plan")
    plan_by_shot = {str(row.get("shot_id")): row for row in plans if isinstance(row, dict)} if isinstance(plans, list) else {}
    missing_plan_shots = sorted(shot_ids - set(plan_by_shot))
    if missing_plan_shots:
        issues.append(_issue(catalog, "E-SHOTLOAD-001", "prevention_evidence.shot_plan", f"缺镜头功能卡：{missing_plan_shots}"))
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        plan = plan_by_shot.get(shot_id)
        if not plan:
            continue
        missing = _missing_fields(plan, ["core_function", "viewpoint_count", "camera_position", "main_action_stage", "transition_function"])
        if missing or _as_number(plan.get("viewpoint_count"), 99) > 1:
            issues.append(_issue(catalog, "E-SHOTLOAD-001", f"shot_plan:{shot_id}", f"缺字段={missing}; viewpoint_count={plan.get('viewpoint_count')!r}"))
        if "特写" in str(shot.get("镜头类型", "")) or bool(plan.get("is_detail_shot")):
            if is_none_value(plan.get("detail_shot_function")):
                issues.append(_issue(catalog, "E-SHOTFRAG-001", f"shot_plan:{shot_id}", "特写未说明剧情、信息或情绪功能。", severity="warning"))

    chain_fields = policy.get("performance_chain_fields", [])
    chains = evidence.get("performance_chains")
    chain_by_shot = {str(row.get("shot_id")): row for row in chains if isinstance(row, dict)} if isinstance(chains, list) else {}
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        dialogue = str(shot.get("台词", ""))
        core = str(shot.get("功能类型", ""))
        relevant = not is_none_value(dialogue) or _has_any(core, ["情绪", "关系", "冲突", "反应", "内心"])
        if not relevant:
            continue
        chain = chain_by_shot.get(shot_id)
        missing = _missing_fields(chain, chain_fields)
        if missing:
            issues.append(_issue(catalog, "E-PERFORMANCE-001", f"performance_chain:{shot_id}", f"演员表演链缺字段：{missing}"))

    expected_layers = policy.get("audit_layer_order", [])
    if evidence.get("audit_layers") != expected_layers:
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.audit_layers", f"分层审计顺序必须为：{expected_layers}"))

    audio = evidence.get("audio_workflow")
    audio_missing = _missing_fields(audio, ["seedance_bgm_policy", "bgm_cue_stage", "scene_emotional_key", "dialogue_ducking"])
    if audio_missing or (isinstance(audio, dict) and "禁止" not in str(audio.get("seedance_bgm_policy"))) or (isinstance(audio, dict) and not _has_any(str(audio.get("bgm_cue_stage")), ["画面锁定后", "后期", "独立"])):
        issues.append(_issue(catalog, "E-AUDIO-STAGE-001", "prevention_evidence.audio_workflow", f"音频分层缺失或不合规：missing={audio_missing}, audio={audio!r}"))

    required_batch_fields = policy.get("i2v_required_batch_fields", [])
    forbidden_context_terms = policy.get("i2v_forbidden_context_terms", [])
    for index, batch in enumerate(batches, 1):
        location = f"batches[{index}]"
        missing = _missing_fields(batch, required_batch_fields)
        if missing:
            issues.append(_issue(catalog, "E-I2V-INDEP-002", location, f"独立执行字段缺失：{missing}"))
        batch_text = json.dumps(batch, ensure_ascii=False)
        context_hits = [term for term in forbidden_context_terms if term in batch_text]
        if context_hits:
            metrics["cross_context_term_count"] += len(context_hits)
            issues.append(_issue(catalog, "E-I2V-INDEP-001", location, f"跨上下文代指：{context_hits}"))
        prompt = str(batch.get("独立执行提示词", ""))
        prompt_without_bans = re.sub(r"(?:禁止|不要|不使用|无)(?:任何)?(?:BGM|背景音乐)", "", prompt, flags=re.I)
        if re.search(r"加入\s*BGM|播放\s*BGM|配上\s*BGM|背景音乐(?:响起|播放|进入|渐入)", prompt_without_bans, flags=re.I):
            issues.append(_issue(catalog, "E-AUDIO-STAGE-001", f"{location}.独立执行提示词", f"Seedance主提示词要求BGM：{prompt!r}"))

    repair_history = evidence.get("repair_history")
    if mode == "repair" and (not isinstance(repair_history, list) or not repair_history):
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.repair_history", "repair模式必须记录至少一轮修正证据。"))
    if isinstance(repair_history, list):
        for index, row in enumerate(repair_history, 1):
            if not isinstance(row, dict):
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", "修正记录不是对象。"))
                continue
            missing = _missing_fields(row, ["round", "initial_issue_count", "residual_issue_count", "introduced_issue_count", "repeat_rounds", "action"])
            if missing:
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", f"修正记录缺字段：{missing}"))
                continue
            initial = _as_number(row.get("initial_issue_count"))
            residual = _as_number(row.get("residual_issue_count"))
            introduced = _as_number(row.get("introduced_issue_count"))
            resolved = max(initial - residual, 0.0)
            if initial > 0:
                metrics["first_repair_effectiveness_percent"] = round(100.0 * resolved / initial, 2)
                metrics["residual_rate_percent"] = round(100.0 * residual / initial, 2)
            metrics["side_effect_rate_percent"] = round(100.0 * introduced / max(resolved, 1.0), 2)
            if residual > 0:
                issues.append(_issue(catalog, "E-REGRESSION-001", f"repair_history[{index}]", f"初审={initial:g}, 修后残留={residual:g}"))
            if introduced > 0:
                issues.append(_issue(catalog, "E-REGRESSION-002", f"repair_history[{index}]", f"修正新增问题={introduced:g}"))
            repeat_rounds = int(_as_number(row.get("repeat_rounds")))
            action = str(row.get("action"))
            if repeat_rounds == 2 and action not in {"root_cause_repair", "rebuild_upstream"}:
                issues.append(_issue(catalog, "E-SYSTEMIC-001", f"repair_history[{index}]", f"连续2轮仍使用动作 {action!r}，必须执行根因修复。"))
            if repeat_rounds >= 3 and action != "rebuild_upstream":
                issues.append(_issue(catalog, "E-SYSTEMIC-001", f"repair_history[{index}]", f"连续{repeat_rounds}轮仍使用动作 {action!r}，必须回退上游重建。"))

    return issues, metrics


def validate_project(obj: dict[str, Any] | list[Any], mode: str = "audit", rules_path: str | None = None) -> dict[str, Any]:
    rules = load_runtime_rules(rules_path)
    catalog = _catalog_map(rules)
    issues: list[dict[str, Any]] = []
    if isinstance(obj, list):
        obj = {"shots": obj}
    if not isinstance(obj, dict):
        issues.append(_issue(catalog, "E-TEMPLATE-001", "root", "输入必须是JSON对象或镜头数组。"))
        obj = {}

    if obj.get("project_version") != VERSION:
        issues.append(_issue(catalog, "E-VERSION-001", "project_version", f"expected {VERSION}, got {obj.get('project_version')!r}"))
    if obj.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue(catalog, "E-VERSION-001", "schema_version", f"expected {SCHEMA_VERSION}, got {obj.get('schema_version')!r}"))

    required_fields = rules["canonical_shot_fields"]
    declared_order = obj.get("field_order")
    if declared_order != required_fields:
        issues.append(_issue(catalog, "E-FIELDORDER-001", "field_order", "顶层field_order缺失或与R10字段顺序不一致。"))

    mother_columns = obj.get("mother_template_columns")
    if mother_columns is not None:
        formal_execution_column = rules.get("modules", {}).get("JIMENG_TIMING", {}).get(
            "formal_execution_column", "Seedance 2.5独立正式执行分镜"
        )
        if not isinstance(mother_columns, list) or formal_execution_column not in mother_columns:
            issues.append(_issue(
                catalog,
                "E-SEEDANCE-VERSION-001",
                "mother_template_columns",
                f"正式执行列必须包含{formal_execution_column!r}；当前={mother_columns!r}",
            ))

    source_lock = obj.get("source_lock")
    source_lock_fields = ["上集终点", "本集起点", "本集计划终点", "下一集起点", "禁止重复", "禁止提前"]
    if not isinstance(source_lock, dict) or any(is_empty(source_lock.get(f)) for f in source_lock_fields):
        issues.append(_issue(catalog, "E-SOURCE-001", "source_lock", "Source Lock缺失或核心边界字段不完整。"))

    raw_shots = obj.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        issues.append(_issue(catalog, "E-TEMPLATE-001", "shots", "没有可校验的镜头列表。"))
        raw_shots = []

    aliases = rules.get("field_aliases", {})
    motions = rules["modules"]["MOTION"]["catalog"]
    normalized_shots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    scene_groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for index, raw in enumerate(raw_shots, 1):
        location = f"shots[{index}]"
        if not isinstance(raw, dict):
            issues.append(_issue(catalog, "E-TEMPLATE-001", location, "镜头不是对象。"))
            continue
        shot, used_aliases = normalize_shot(raw, aliases)
        normalized_shots.append(shot)
        for old, new in used_aliases:
            issues.append(_issue(catalog, "E-INTEGRATION-003", location, f"使用旧字段 {old!r}，已兼容映射到 {new!r}。", severity="warning"))
        for field in required_fields:
            if field not in shot or is_empty(shot.get(field)):
                issues.append(_issue(catalog, "E-TEMPLATE-008", f"{location}.{field}", f"缺字段或空值：{field}"))

        shot_id = str(shot.get("镜头编号", ""))
        if shot_id in seen_ids:
            issues.append(_issue(catalog, "E-TEMPLATE-008", f"{location}.镜头编号", f"重复镜头编号：{shot_id}"))
        seen_ids.add(shot_id)

        try:
            duration = float(shot.get("时长", 0))
        except (TypeError, ValueError):
            duration = 0.0
            issues.append(_issue(catalog, "E-STAT-001", f"{location}.时长", "时长不是数字。"))
        try:
            float(shot.get("累计时长", 0))
        except (TypeError, ValueError):
            issues.append(_issue(catalog, "E-STAT-001", f"{location}.累计时长", "累计时长不是数字。"))

        shot_type = str(shot.get("镜头类型", ""))
        visible = str(shot.get("可见主体", ""))
        action = str(shot.get("动作与表演", ""))
        image_content = str(shot.get("画面内容", ""))
        combined_performance = f"{action} {image_content}"
        image_timecodes = sorted(set(HARD_TIMECODE_RE.findall(image_content)))
        if image_timecodes:
            issues.append(_issue(
                catalog,
                "E-JIMENG-TIMING-001",
                f"{location}.画面内容",
                f"正式画面描述混入硬时间码：{image_timecodes}",
                severity="warning",
            ))
        invisible_context = _has_any(f"{shot_type} {visible}", INVISIBLE_SHOT_TERMS) and not _has_any(visible, FACE_VISIBLE_TERMS)
        if invisible_context:
            bad = [term for term in FACE_TERMS if term in combined_performance]
            if bad:
                issues.append(_issue(catalog, "E-VIS-005", location, f"不可见镜头写入面部/眨眼细节：{sorted(set(bad))}"))

        categories = detail_categories(action)
        emotion_labels = [x for x in ABSTRACT_EMOTIONS if x in action]
        core_emotion = _has_any(str(shot.get("功能类型", "")), ["情绪", "关系", "冲突", "哭", "内心", "反应"]) or bool(emotion_labels)
        if emotion_labels and not categories:
            issues.append(_issue(catalog, "E-EXPRESSION-001", f"{location}.动作与表演", f"只有抽象情绪标签 {emotion_labels}，没有可观察细节。"))
        elif core_emotion and len(categories) < 4:
            issues.append(_issue(catalog, "E-EXPRESSION-002", f"{location}.动作与表演", f"重要情绪镜只有 {len(categories)} 类细节：{categories}", severity="warning"))

        if "眨眼" in combined_performance:
            blink_range = parse_blink_range(combined_performance)
            if "同步眨眼" in combined_performance or "同时眨眼" in combined_performance or "同频眨眼" in combined_performance:
                issues.append(_issue(catalog, "E-EXPRESSION-003", location, "双人镜头出现同步/同频眨眼。"))
            if blink_range:
                _, high = blink_range
                if 3 <= duration <= 5 and high > 1:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"{duration}s镜头眨眼上限应为1次，当前写到{high}次。"))
                elif 5 < duration <= 10 and high > 2:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"{duration}s镜头眨眼上限应为2次，当前写到{high}次。"))
                elif duration < 3 and high > 1:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"短于3s镜头不应机械安排{high}次眨眼。"))

        camera = str(shot.get("机位", ""))
        if is_none_value(camera) or _has_any(camera, ["随便", "自然机位", "高级机位"]):
            issues.append(_issue(catalog, "E-CAMERA-001", f"{location}.机位", f"机位不可执行：{camera!r}"))

        motion_text = str(shot.get("运镜方式", ""))
        matched_motion = match_motion(motion_text, motions)
        is_static = _has_any(motion_text, STATIC_MOTIONS)
        if _has_any(motion_text, VAGUE_TERMS) or (not matched_motion and not is_static):
            issues.append(_issue(catalog, "E-MOTION-001", f"{location}.运镜方式", f"运镜不属于R10菜单或过于空泛：{motion_text!r}"))
        if matched_motion and not is_static:
            subject = str(shot.get("运镜主体", ""))
            function = str(shot.get("运镜功能", ""))
            if is_none_value(subject):
                issues.append(_issue(catalog, "E-MOTION-002", f"{location}.运镜主体", "运镜有类型但无主体。"))
            allowed_functions = matched_motion.get("functions", [])
            if is_none_value(function):
                issues.append(_issue(catalog, "E-MOTION-003", f"{location}.运镜功能", "运镜有类型但无叙事功能。", severity="warning"))
            elif allowed_functions and not _has_any(function, allowed_functions):
                issues.append(_issue(catalog, "E-MOTION-003", f"{location}.运镜功能", f"功能 {function!r} 与 {matched_motion['name']} 的典型功能弱匹配。", severity="warning"))

        light = str(shot.get("光影", ""))
        light_source = str(shot.get("光源来源", ""))
        light_direction = str(shot.get("光源方向", ""))
        color_temp = str(shot.get("色温倾向", ""))
        if is_none_value(light_source) or is_none_value(light_direction) or is_none_value(color_temp) or (light.strip() in VAGUE_TERMS):
            issues.append(_issue(catalog, "E-LIGHT-001", f"{location}.光影", f"缺具体光源/方向/色温：光影={light!r}, 光源={light_source!r}, 方向={light_direction!r}, 色温={color_temp!r}"))
        if "底光" in f"{light} {light_source}" and _has_any(str(shot.get("功能类型", "")), ["甜宠", "温情", "浪漫", "关系"]):
            issues.append(_issue(catalog, "E-LIGHT-002", f"{location}.光影", "普通关系/浪漫镜使用底光，需改为有源且有剧情必要的光效。", severity="warning"))

        dialogue = str(shot.get("台词", ""))
        if not is_none_value(dialogue):
            if is_none_value(shot.get("音色")) or is_none_value(shot.get("发声方式")):
                issues.append(_issue(catalog, "E-VOICE-001", location, "带台词镜缺音色或发声方式。"))
            delivery = str(shot.get("发声方式", ""))
            dimensions = [name for name, pattern in VOICE_DIMENSION_PATTERNS.items() if pattern.search(delivery)]
            if core_emotion and len(dimensions) < 6:
                issues.append(_issue(catalog, "E-VOICE-001", f"{location}.发声方式", f"核心情绪台词只覆盖 {len(dimensions)}/6 项：{dimensions}"))
            elif len(dimensions) < 4:
                issues.append(_issue(catalog, "E-VOICE-002", f"{location}.发声方式", f"普通台词只覆盖 {len(dimensions)}/6 项：{dimensions}", severity="warning"))
            punctuation_delivery = str(shot.get("标点语气", ""))
            if is_none_value(punctuation_delivery):
                issues.append(_issue(catalog, "E-PUNCT-001", f"{location}.标点语气", "带台词镜未填写标点语气。"))
            else:
                missing_punct = _validate_punctuation(dialogue, punctuation_delivery)
                if missing_punct:
                    issues.append(_issue(catalog, "E-PUNCT-004", f"{location}.标点语气", f"未说明：{missing_punct}"))
            min_duration = cjk_len(dialogue) / 2.8 + _pause_budget(dialogue)
            if duration + 1e-9 < min_duration:
                issues.append(_issue(catalog, "E-DIALOGUE-003", f"{location}.台词", f"{cjk_len(dialogue)}个汉字 + 停顿预算需至少{min_duration:.2f}s，镜头仅{duration:.2f}s。"))

        psych = str(shot.get("心理音轨", ""))
        if not is_none_value(psych):
            safety = f"{shot.get('心理音轨属性', '')} {shot.get('生成限制', '')}"
            required_psych = ["不张嘴", "不驱动口型", "不生成字幕", "不计入"]
            missing = [term for term in required_psych if term not in safety]
            if missing:
                issues.append(_issue(catalog, "E-PSYCH-001", location, f"心理音轨缺安全属性：{missing}"))

        scene_id = str(shot.get("场景连续性ID", ""))
        if scene_id:
            scene_groups[scene_id].append((index, shot))

    for scene_id, items in scene_groups.items():
        if len(items) < 2:
            continue
        directions = {str(shot.get("光源方向")) for _, shot in items if not is_none_value(shot.get("光源方向"))}
        temperatures = {str(shot.get("色温倾向")) for _, shot in items if not is_none_value(shot.get("色温倾向"))}
        sources = {str(shot.get("光源来源")) for _, shot in items if not is_none_value(shot.get("光源来源"))}
        if len(directions) > 1 or len(temperatures) > 1 or len(sources) > 1:
            issues.append(_issue(catalog, "E-LIGHT-002", f"scene:{scene_id}", f"连续镜光线不一致：光源={sorted(sources)}, 方向={sorted(directions)}, 色温={sorted(temperatures)}", severity="warning"))

    batches = obj.get("batches")
    if not isinstance(batches, list):
        batches = []
    batch_by_id = {str(b.get("批次编号")): b for b in batches if isinstance(b, dict)}
    shot_by_id = {str(s.get("镜头编号")): s for s in normalized_shots}
    for index, shot in enumerate(normalized_shots, 1):
        batch_id = str(shot.get("生成批次", ""))
        if not batch_id or batch_id not in batch_by_id:
            issues.append(_issue(catalog, "E-BATCH-001", f"shots[{index}].生成批次", f"批次 {batch_id!r} 不存在。"))

    for index, batch in enumerate(batches, 1):
        location = f"batches[{index}]"
        try:
            duration = float(batch.get("时长", 0))
        except (TypeError, ValueError):
            duration = 0.0
        if duration < 4:
            issues.append(_issue(catalog, "E-BATCH-002", f"{location}.时长", f"批次时长{duration}s，小于4s。"))
        elif duration > 15:
            issues.append(_issue(catalog, "E-BATCH-004", f"{location}.时长", f"批次时长{duration}s，大于15s。"))
        elif duration > 12:
            issues.append(_issue(catalog, "E-BATCH-003", f"{location}.时长", f"批次时长{duration}s，大于12s。", severity="warning"))
        covered = batch.get("覆盖镜头", [])
        if isinstance(covered, str):
            covered = [x.strip() for x in re.split(r"[,，、]", covered) if x.strip()]
        motion_names = set()
        for shot_id in covered or []:
            shot = shot_by_id.get(str(shot_id))
            if not shot:
                continue
            motion_text = str(shot.get("运镜方式", ""))
            motion = match_motion(motion_text, motions)
            if motion and not _has_any(motion_text, STATIC_MOTIONS):
                motion_names.add(motion["name"])
        declared_main = str(batch.get("主运镜", ""))
        declared_multi = len([x for x in re.split(r"[+＋、,，]", declared_main) if x.strip()]) > 1
        if len(motion_names) > 1 or declared_multi:
            issues.append(_issue(catalog, "E-BATCH-004", f"{location}.主运镜", f"单批次包含多个主运镜：镜头内={sorted(motion_names)}, 声明={declared_main!r}"))

    if normalized_shots and obj.get("total_duration") is not None:
        try:
            expected_total = float(obj.get("total_duration"))
            actual_total = float(normalized_shots[-1].get("累计时长", 0))
            if abs(expected_total - actual_total) > 0.01:
                issues.append(_issue(catalog, "E-STAT-001", "total_duration", f"总时长{expected_total}与末镜累计{actual_total}不一致。"))
        except (TypeError, ValueError):
            issues.append(_issue(catalog, "E-STAT-001", "total_duration", "总时长不是数字。"))

    public_issues, public_metrics = _validate_public_scene_ecology(
        normalized_shots,
        batch_by_id,
        catalog,
    )
    issues.extend(public_issues)

    gate_issues, gate_metrics = _validate_preventive_gates(
        obj,
        normalized_shots,
        batches,
        mode,
        rules,
        catalog,
    )
    issues.extend(gate_issues)
    gate_metrics.update(public_metrics)
    chapter_issues, chapter_metrics = _validate_chapter_intake(obj, rules, catalog)
    issues.extend(chapter_issues)
    gate_metrics.update(chapter_metrics)

    if mode == "archive":
        stage_evidence = obj.get("stage_evidence")
        required_stages = ["STAGE-A", "STAGE-B", "STAGE-C", "STAGE-D"]
        if not isinstance(stage_evidence, dict) or any(not stage_evidence.get(stage) for stage in required_stages):
            issues.append(_issue(catalog, "E-BOOTSTRAP-001", "stage_evidence", "归档模式缺阶段A-D证据。"))
        review = obj.get("human_review")
        if not isinstance(review, dict) or review.get("最终归档") != "approved" or is_empty(review.get("签名")):
            issues.append(_issue(catalog, "E-ARCHIVE-001", "human_review", "归档模式缺approved状态或真实签名。"))

    fail_count = sum(1 for issue in issues if issue["severity"] == "fail")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    return {
        "validator_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "workflow_revision": rules.get("workflow_revision", WORKFLOW_PROFILE),
        "workflow_profile": obj.get("workflow_profile"),
        "mode": mode,
        "status": status,
        "shot_count": len(normalized_shots),
        "batch_count": len(batches),
        "error_count": fail_count,
        "warning_count": warning_count,
        "quality_metrics": gate_metrics,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json")
    parser.add_argument("--mode", choices=["draft", "audit", "repair", "archive"], default="audit")
    parser.add_argument("--rules", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    obj = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    report = validate_project(obj, mode=args.mode, rules_path=args.rules)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    return 0 if report["status"] == "pass" else 2 if report["status"] == "warning" else 1


if __name__ == "__main__":
    sys.exit(main())
