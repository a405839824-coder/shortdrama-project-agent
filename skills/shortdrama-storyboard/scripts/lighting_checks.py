"""Scene-grounded supplemental lighting checks for the 0.5.24 rule upgrade.

Keep the established validator's source, aperture-identity and beam checks by
passing it as ``legacy_checker``.  This module adds the missing scene resolution,
independent clarity predicates and semantic adjacent-light comparison.  It never
mutates a shot or treats candidate anchor claims as imported source authority.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any


PHYSICAL_FIELDS = ("空间站位描述", "起始状态", "画面内容", "环境动态", "参考图锚定")
LIGHT_FIELDS = ("光影", "光源来源", "光源方向", "色温倾向", "生成限制")
TYPE_ALIASES = {
    "indoor": "indoor", "interior": "indoor", "室内": "indoor", "内景": "indoor",
    "outdoor": "outdoor", "exterior": "outdoor", "室外": "outdoor", "外景": "outdoor",
    "transition": "transition", "transitional": "transition", "过渡": "transition",
    "过渡空间": "transition", "半开放": "transition",
}
ROOM = r"室内|内景|屋内|内室|闺阁|厨房|灶房|灶间|伙房|浴室|茅房|卧室|卧房|书房|书斋|账房|寝宫|寝殿|大殿|偏殿|厢房|厅堂|正厅|花厅|侧厅|祠堂|库房|仓房|客房|房内|厅内|殿内|堂内|阁内"
OUTDOOR = r"室外|户外|外景|门外|窗外|外廊|露天|街市|街道|庭院|院中|野外|林中|河边|山坡|城外"
TRANSITION = r"过渡空间|半开放|门槛处|门洞内|廊檐下|回廊|廊下"
NEGATION = re.compile(r"(?:禁止|不得|不要|避免|不使用|不生成|不出现|不存在|没有|尚未|并未|未见|无|不)(?:任何|明显|可见|强烈|额外|出现|产生|形成|使用|生成|造成)?[^；。！？!?，,、\n]{0,10}$")
CLAUSE_SPLIT = re.compile(r"[；。！？!?，,\n]|但是|然而|反而|但|却")
EFFECT_RE = re.compile(r"halation|高光晕染|镜头光晕|全画面光晕", re.I)
SMOKE_RE = re.compile(r"薄雾|浓雾|雾气|烟雾|细烟|薄烟|轻烟|浓烟|烟气|烟尘|炊烟|空气颗粒|水汽|蒸汽|茶汽")
FLAME_RE = re.compile(r"烛火|烛光|红烛|烛台|灯芯|油灯|火焰|火场|灯火")


def _blob(shot: Mapping[str, Any], fields: Sequence[str]) -> str:
    return "。".join(str(shot.get(key) or "") for key in fields)


def affirmative_matches(pattern: re.Pattern[str], value: Any) -> list[re.Match[str]]:
    """A prohibition ends at punctuation or an explicit affirmative restart.

    Bare enumeration inherits negation (``无烟雾、细烟``); ``但背景泛白`` and
    ``无字幕、镜头使用柔焦`` start new assertions.  Matches refer to original
    offsets, so one negated occurrence cannot hide a later affirmative one.
    """
    text = str(value or "")
    result = []
    for match in pattern.finditer(text):
        prefix = CLAUSE_SPLIT.split(text[:match.start()])[-1]
        item_prefix = prefix.rsplit("、", 1)[-1]
        restart = re.search(r"使用|采用|允许|出现|形成|变得|变成|呈现|产生|笼罩|弥漫", item_prefix)
        if NEGATION.search(item_prefix):
            continue
        if "、" in prefix and not restart and re.match(r"\s*(?:禁止|不得|不要|避免|没有|无|不使用|不生成)", prefix):
            continue
        result.append(match)
    return result


def _affirmative(pattern: str, value: Any, flags: int = 0) -> bool:
    return bool(affirmative_matches(re.compile(pattern, flags), value))


def resolve_scene_anchor(shot: Mapping[str, Any], scene_anchors: Any = None) -> tuple[dict[str, Any], list[str]]:
    """Resolve only the current scene ID, rejecting duplicate/ambiguous records."""
    scene_id = str(shot.get("场景连续性ID") or "")
    if not scene_id or not scene_anchors:
        return {}, []
    if isinstance(scene_anchors, Mapping):
        candidate = scene_anchors.get(scene_id)
        if isinstance(candidate, Mapping):
            return dict(candidate), []
        entries = list(scene_anchors.values())
    elif isinstance(scene_anchors, list):
        entries = scene_anchors
    else:
        return {}, ["scene_anchors必须为场景ID映射或带scene_id的记录列表"]
    matches = [dict(row) for row in entries if isinstance(row, Mapping) and str(row.get("scene_id") or row.get("场景连续性ID") or row.get("id") or "") == scene_id]
    if len(matches) > 1:
        return {}, [f"场景{scene_id}存在重复锚点，无法唯一判定空间和光影状态"]
    return (matches[0] if matches else {}), []


def _location_type(value: Any) -> str:
    for clause in re.split(r"[；;。，,\n]", str(value or "")):
        # A room name used as the reference building does not relocate a person.
        clause = re.sub(rf"(?:{ROOM})(?:的)?(?=门外|窗外|外廊|外侧|外面)", "", clause)
        matches = []
        for kind, pattern in (("indoor", ROOM), ("outdoor", OUTDOOR), ("transition", TRANSITION)):
            found = re.search(pattern, clause)
            if found:
                matches.append((found.start(), kind))
        if matches:
            return min(matches, key=lambda item: item[0])[1]
    return "unknown"


def classify_scene(shot: Mapping[str, Any], scene_anchors: Any = None) -> dict[str, Any]:
    """Use the scene anchor first; aliases are a documented fallback, not authority.

    Transition anchors use ``shot_space_types[shot_id]`` to bind the active side
    of an opening.  A clear authored location can resolve that side if absent.
    No fields are added to the 27-field shot itself.
    """
    anchor, faults = resolve_scene_anchor(shot, scene_anchors)
    authored = _location_type(shot.get("空间站位描述"))
    raw = anchor.get("space_type", anchor.get("空间类型", ""))
    anchored = TYPE_ALIASES.get(str(raw).strip().lower(), "")
    if raw and not anchored:
        faults.append(f"场景锚点空间类型{raw!r}无效；须为indoor/outdoor/transition")
    resolved = anchored or authored
    if resolved == "transition":
        locations = anchor.get("shot_space_types", {})
        active = locations.get(str(shot.get("镜头编号") or ""), "") if isinstance(locations, Mapping) else ""
        authored_side = authored
        if authored_side not in ("indoor", "outdoor"):
            sides = {TYPE_ALIASES[match.group(1)] for match in affirmative_matches(
                re.compile(r"(室内|室外)侧"), shot.get("空间站位描述"),
            )}
            authored_side = next(iter(sides)) if len(sides) == 1 else "transition"
        resolved = TYPE_ALIASES.get(str(active).strip().lower(), "") or authored_side
        if resolved not in ("indoor", "outdoor"):
            resolved = "transition"
            faults.append("过渡空间缺本镜所在室内侧/室外侧：补场景锚点shot_space_types或明确空间站位")
        elif active and authored_side in ("indoor", "outdoor") and resolved != authored_side:
            faults.append(f"过渡空间本镜锚定侧{resolved}与空间站位{authored_side}冲突，须回查所在侧")
    elif anchored and authored in ("indoor", "outdoor") and anchored != authored:
        faults.append(f"场景锚点{anchored}与本镜空间站位{authored}冲突，不能用场景别名绕过")
    if resolved == "unknown":
        faults.append("缺室内/室外/过渡空间事实：补对应场景锚点space_type或具体空间站位")
    return {"space_type": resolved, "provenance": "scene_anchor" if anchored else "location_alias", "anchor": anchor, "faults": faults}


def _clarity_faults(shot: Mapping[str, Any]) -> list[str]:
    light = _blob(shot, LIGHT_FIELDS)
    physical = _blob(shot, PHYSICAL_FIELDS)
    all_text = light + "。" + physical
    faults = []
    if not _affirmative(r"空气(?:保持|仍然|仍|持续|始终)?(?:清澈|清洁|清透|清晰|透明)|清澈(?:的)?空气", light):
        faults.append("室内清透缺空气状态：须明确空气清澈；局部烟气时须明确其他区域空气清澈")
    if not _affirmative(r"(?:背景|木构|门窗|陈设|空间结构)[^不无未；。！？!?，,、\n]{0,18}(?:清晰|清楚|可辨|能辨认)", light):
        faults.append("室内清透缺背景结构可辨证据，不能仅用空气清澈替代")
    exposure = _affirmative(r"曝光(?:保持|仍然|仍)?(?:正常|克制|适当)|(?:高光|背景|整体|画面)[^；。！？!?，,、\n]{0,10}(?:不过曝|无过曝)|正常(?:的)?曝光", light)
    if not exposure:
        faults.append("室内清透缺曝光依据：须明确曝光正常或高光/背景不过曝")
    # Evaluate each adverse predicate separately, including negation within the
    # subject-predicate span (背景不泛白 is not background whitening).
    for label, pattern in (
        ("背景或画面泛白", r"(?:背景|全画面|整个房间|整屋|室内|画面)[^；。！？!?，,、\n]{0,12}?(?P<adverse>泛白|蒙白|雾白|奶白|白茫茫|发白)"),
        ("背景结构不可辨", r"(?:背景|木构|门窗|陈设|空间结构)[^；。！？!?，,、\n]{0,12}?(?P<adverse>不可辨|无法辨认|不清楚|不清晰|看不清|模糊不清|完全消失)"),
        ("曝光过度", r"(?:背景|高光|画面|窗纸|木墙)[^；。！？!?，,、\n]{0,10}?(?P<adverse>过曝|曝光过度)"),
        ("空气浑浊", r"空气[^；。！？!?，,、\n]{0,8}?(?P<adverse>浑浊|混浊|雾蒙蒙|奶雾化)"),
    ):
        for match in affirmative_matches(re.compile(pattern), all_text):
            # The negation may occur after the subject (空气并不浑浊).
            # Check only the prefix of this adverse predicate: the 不 in
            # 背景不清楚 belongs to the adverse predicate itself, not a waiver.
            predicate_prefix = all_text[match.start():match.start("adverse")]
            if NEGATION.search(predicate_prefix) or re.search(r"(?:并非|不是|不再)[^；。！？!?，,、\n]{0,6}$", predicate_prefix):
                continue
            faults.append(f"室内相反肯定事实：{label}（{match.group()}）；清澈口号不能抵消")
            break
    return faults


def lighting_context_faults(
    shot: Mapping[str, Any],
    scene_anchors: Any = None,
    legacy_checker: Callable[[dict[str, Any]], list[str]] | None = None,
) -> list[str]:
    resolution = classify_scene(shot, scene_anchors)
    faults = list(resolution["faults"])
    kind = resolution["space_type"]
    adapted = dict(shot)
    if kind in ("indoor", "outdoor"):
        adapted["空间站位描述"] = ("室内；" if kind == "indoor" else "室外；") + str(shot.get("空间站位描述") or "")
    if legacy_checker is not None:
        faults.extend(legacy_checker(adapted))
    physical = _blob(shot, PHYSICAL_FIELDS)
    lights = _blob(shot, LIGHT_FIELDS)
    source = str(shot.get("光源来源") or "")
    effects = str(shot.get("光影") or "") + "。" + str(shot.get("色温倾向") or "") + "。" + str(shot.get("生成限制") or "")
    if kind == "indoor":
        faults.extend(_clarity_faults(shot))
        # Mere presence of a candle must never authorize invented smoke.
        if affirmative_matches(SMOKE_RE, lights) and not _affirmative(r"香炉|药炉|热茶|热水|沸水|蒸锅|灶火|灶台|火盆|火场|失火|扬尘|尘土飞扬|雨雾|蒸汽|冒烟|燃烧产生[^；。]{0,6}烟", physical):
            faults.append("室内烟气缺已建立的具体烟汽来源；烛光/红烛本身不自动授权烟雾")
    # Visible rays need a separately established medium, outdoors as well as in.
    if _affirmative(r"丁达尔|体积光|可见光束|光柱|耶稣光", lights) and not _affirmative(r"薄雾|浓雾|雾气|烟雾|烟尘|扬尘|水汽|蒸汽|雨雾|空气颗粒", physical):
        faults.append("可见光束缺场景已建立的散射介质；有源逆光不等于可见光柱")
    if kind == "outdoor" and _affirmative(r"逆光|轮廓光|背光(?!一侧|侧面|面)", effects + "。" + str(shot.get("光源方向") or "")) and not _affirmative(r"太阳|阳光|日光|晨光|天光|月光|月色|烛|油灯|灯火|火焰|灯笼", source):
        faults.append("室外逆光缺真实太阳、月光或场内灯火来源；无需附加雾烟但必须有源")
    halations = affirmative_matches(EFFECT_RE, effects)
    if halations:
        real_flame = bool(affirmative_matches(FLAME_RE, physical) and affirmative_matches(FLAME_RE, source))
        for match in halations:
            clause = re.split(r"[；。！？!?，,\n]", effects[:match.start()])[-1] + match.group() + re.split(r"[；。！？!?，,\n]", effects[match.end():])[0]
            if re.search(r"金属|铜镜|湿地|湿石|釉面|反光边缘", clause):
                faults.append("金属、湿地与釉面使用局部反射高光，不把材质高光写成halation/晕染")
            if kind == "indoor" and not (real_flame and re.search(r"火焰|烛火|灯芯", clause) and re.search(r"边缘|附近", clause) and re.search(r"局部|仅|只", clause) and re.search(r"极轻微|极微弱|极淡", clause)):
                faults.append("室内halation仅限已建立真实火焰/烛火/灯芯边缘的极轻微局部微晕")
    return list(dict.fromkeys(faults))


SOURCE_CLASSES = {
    "daylight": r"太阳|阳光|日光|晨光|午后光",
    "sky": r"天光|天空|漫射天光",
    "moon": r"月光|月色|夜空",
    "candle": r"红烛|烛火|烛光|烛台",
    "oil_lamp": r"油灯|灯芯",
    "lantern": r"灯笼",
    "fire": r"火盆|火焰|火场",
    "window": r"窗",
    "door": r"门",
    "wall_reflection": r"(?:浅色|浅)?墙[^；。]{0,8}反射|反射[^；。]{0,8}(?:浅色|浅)?墙",
}


def _world_location(value: Any) -> tuple[str, ...]:
    # 东侧窗 is the same opening as 东窗, not the coarser coordinate 东侧.
    # Preserve both the direction and the kind of opening; east/west and
    # door/window identities must never collapse during normalization.
    pattern = re.compile(
        r"(?:场景)?(?P<direction>东南|东北|西南|西北|东|西|南|北|左|右|前|后)"
        r"(?:(?:侧|面)(?:的)?(?P<opening>[门窗])(?:户|扇)?|(?P<feature>墙|角|窗|门|侧|面))"
    )
    return tuple(sorted({match.group("direction") + (match.group("opening") or match.group("feature")) for match in pattern.finditer(str(value or ""))}))


def _aperture_events(value: Any) -> list[tuple[str, str, bool]]:
    """Ordered visible opening events and final states, bound to exact openings.

    A maintained state alone cannot authorize a transition. A real operation
    followed by a contrary terminal state keeps that terminal state, so an
    earlier opening cannot hide a later closure. Speech and negated operations
    are not physical events.
    """
    text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', "", str(value or ""))
    noun = r"(?:(?:左|右|东|西|南|北)(?:侧)?)?(?:木格|木|厅)?[门窗](?:户|扇)?(?!闩|锁|帘)"
    verbs = r"打开|推开|拉开|开启|半开|关上|关闭|合上|闭合|紧闭|敞开|关着"
    gap = r"[^门窗；。！？!?，,、\n]{0,6}?"
    patterns = (
        re.compile(rf"(?P<verb>{verbs}){gap}(?P<opening>{noun})"),
        re.compile(rf"(?P<opening>{noun}){gap}(?P<verb>{verbs})"),
    )
    found = []
    for pattern in patterns:
        for match in affirmative_matches(pattern, text):
            if match.start("opening") < match.start("verb") and re.match(
                rf"(?:了|那扇|这扇|那道|这道|一扇|一道)?{noun}", text[match.end("verb"):],
            ):
                # In 打开左窗后又关闭右窗, the second operation belongs to
                # 右窗; it must not also be parsed as 左窗后又关闭.
                continue
            prefix = CLAUSE_SPLIT.split(text[:match.start("verb")])[-1]
            if NEGATION.search(prefix) or re.search(r"(?:并非|不是|准备|打算|试图)[^；。！？!?，,、\n]{0,6}$", prefix):
                continue
            key = re.sub(r"侧|木格|木|厅|户|扇", "", match.group("opening"))
            verb = match.group("verb")
            state = "half_open" if verb == "半开" else "open" if verb in ("打开", "推开", "拉开", "开启", "敞开") else "closed"
            maintained = re.search(r"(?:保持|仍然|仍旧|始终|依然|维持)[^门窗；。！？!?，,、\n]{0,4}$", prefix)
            operation = verb in ("打开", "推开", "拉开", "开启", "半开", "关上", "关闭", "合上", "闭合") and not maintained
            found.append((match.start(), match.end(), key, state, operation))
    # The two grammars may recognize the same operation around a following
    # state clause. Order by source position and deduplicate identical events.
    return [(key, state, operation) for _, _, key, state, operation in sorted(set(found))]


def _aperture_end_states(value: Any) -> dict[str, str]:
    states = {}
    operated = set()
    for opening, state, operation in _aperture_events(value):
        if operation:
            operated.add(opening)
        if opening in operated:
            states[opening] = state
    return states


def _aperture_signature(shot: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    # Scope to start/spatial state: a later authored opening is a transition,
    # not a contradictory starting state. Established per-opening legacy checks
    # remain authoritative for actual path legality.
    text = _blob(shot, ("空间站位描述", "起始状态"))
    pattern = re.compile(r"(?P<key>(?:(?:左|右|东|西|南|北)(?:侧)?)?(?:木格|木|厅)?[门窗](?:户|扇)?)[^门窗；。！？!?，,、\n]{0,6}(?P<state>打开|开启|敞开|半开|关闭|闭合|紧闭|关着)")
    rows = []
    for match in affirmative_matches(pattern, text):
        if re.search(r"未|没有|不", text[match.start():match.start("state")]):
            continue
        key = re.sub(r"侧|木格|木|厅|户|扇", "", match.group("key"))
        state = "half_open" if match.group("state") == "半开" else "open" if match.group("state") in ("打开", "开启", "敞开") else "closed"
        rows.append((key, state))
    return tuple(sorted(set(rows)))


def _color_signature(value: Any) -> tuple[tuple[str, str], ...]:
    pairs = []
    for clause in re.split(r"[；。！？!?，,、\n]", str(value or "")):
        subject = re.search(r"受光面|主光|补光|面部|人物|肤色|背景|暗部|前景|中景|高光|木墙|木质", clause)
        tone = re.search(r"中性微暖|暖|冷|中性|琥珀|青灰|灰青", clause)
        if tone:
            tone_name = "warm" if tone.group() in ("中性微暖", "暖", "琥珀") else "cool" if tone.group() in ("冷", "青灰", "灰青") else "neutral"
            pairs.append((subject.group() if subject else "overall", tone_name))
    return tuple(sorted(set(pairs)))


def _screen_light_paths(value: Any) -> tuple[tuple[str, str], ...]:
    """Preserve source-to-destination order, not just the mentioned sides."""
    side = r"左前(?:侧)?|右前(?:侧)?|左后(?:侧)?|右后(?:侧)?|左侧|右侧|前侧|后侧|上方|下方|顶部"
    pattern = re.compile(rf"(?P<origin>{side})\s*(?:至|到|向|朝|射向|照向)\s*(?:画面|人物)?(?P<target>{side})")
    def normalize(token: str) -> str:
        return re.sub(r"^(左前|右前|左后|右后)侧$", r"\1", token)
    return tuple(sorted({(normalize(match.group("origin")), normalize(match.group("target"))) for match in affirmative_matches(pattern, value)}))


def lighting_signature(shot: Mapping[str, Any], scene_anchors: Any = None) -> dict[str, Any]:
    anchor, _ = resolve_scene_anchor(shot, scene_anchors)
    states = anchor.get("lighting_states", {})
    state = states.get(str(shot.get("镜头编号") or ""), {}) if isinstance(states, Mapping) else {}
    common = anchor.get("lighting_state", {})
    explicit = {**(dict(common) if isinstance(common, Mapping) else {}), **(dict(state) if isinstance(state, Mapping) else {})}
    source = str(shot.get("光源来源") or "")
    direction = str(shot.get("光源方向") or "")
    context = _blob(shot, ("空间站位描述", "起始状态"))
    time_match = re.search(r"清晨|晨间|早晨|上午|正午|午后|夜晚|夜间|深夜|黄昏后|黎明前|月夜", context)
    time_name = time_match.group() if time_match else ""
    time_name = {"晨间": "清晨", "早晨": "清晨", "夜间": "夜晚", "月夜": "夜晚"}.get(time_name, time_name)
    return {
        "source_types": tuple(sorted(name for name, pattern in SOURCE_CLASSES.items() if _affirmative(pattern, source))),
        "source_id": str(explicit.get("source_id") or ""),
        "source_position": str(explicit.get("world_position") or "") or _world_location(source),
        "authored_source_position": _world_location(source),
        "camera_position": str(explicit.get("camera_world_position") or "") or _world_location(shot.get("机位")),
        "screen_direction": tuple(sorted(set(re.findall(r"左前|右前|左后|右后|左侧|右侧|前侧|后侧|侧后|斜后|上方|下方|顶部", direction)))),
        "screen_paths": _screen_light_paths(direction),
        "apertures": _aperture_signature(shot),
        "time_of_day": str(explicit.get("time_of_day") or time_name),
        "authored_time_of_day": time_name,
        "color": _color_signature(shot.get("色温倾向")),
        "declared_color": str(explicit.get("temperature") or ""),
    }


def scene_lighting_continuity_faults(items: Sequence[Any], scene_anchors: Any = None) -> list[str]:
    """Return physical differences for adjacent same-scene shots, not prose drift.

    Accept either shots or the validator's ``(index, shot)`` rows.  Reverse-shot
    direction changes need the same established world-space source and distinct
    camera world positions; the word “反打” alone is insufficient evidence.
    This supplements rather than replaces the N−1 seven-field hashed audit.
    """
    shots = [item[1] if isinstance(item, tuple) else item for item in items]
    faults = []
    for pair_index, (left, right) in enumerate(zip(shots, shots[1:])):
        if isinstance(items[pair_index], tuple) and isinstance(items[pair_index + 1], tuple):
            left_index, right_index = items[pair_index][0], items[pair_index + 1][0]
            if isinstance(left_index, int) and isinstance(right_index, int) and right_index != left_index + 1:
                # Scene-group rows can omit intervening scene cuts. The global
                # N−1 audit owns those boundaries; do not invent a neighbor.
                continue
        if str(left.get("场景连续性ID") or "") != str(right.get("场景连续性ID") or ""):
            continue
        before, after = lighting_signature(left, scene_anchors), lighting_signature(right, scene_anchors)
        event = str(right.get("画面内容") or "")
        light_event = _affirmative(r"(?:移动|挪动|移至|移到|点燃|熄灭|点亮|吹灭)[^；。]{0,12}(?:烛|灯|火)|(?:烛|灯|火)[^；。]{0,12}(?:移动|移至|移到|熄灭|点燃)", event)
        for key, label in (("source_types", "光源类型"), ("source_id", "光源身份"), ("source_position", "光源场景位置"), ("authored_source_position", "明示光源场景位置")):
            explained = light_event
            if key == "source_types":
                changed_types = set(before[key]) ^ set(after[key])
                named_types = {name for name, pattern in SOURCE_CLASSES.items() if _affirmative(pattern, event)}
                explained = bool(light_event and changed_types and changed_types <= named_types)
            elif key == "source_id":
                # Changing identity is not the same operation as moving the
                # same lamp. Require both identities in the concrete event.
                explained = bool(light_event and before[key] and after[key] and before[key] in event and after[key] in event)
            if before[key] != after[key] and not explained:
                faults.append(f"{left.get('镜头编号')}->{right.get('镜头编号')}：{label}变化缺场内来源/移灯事件：{before[key]!r}→{after[key]!r}")
        old, new = dict(before["apertures"]), dict(after["apertures"])
        old.update(_aperture_end_states(left.get("画面内容")))
        right_end = _aperture_end_states(event)
        if old != new:
            # A transition must name the changed opening, not any other door.
            for opening in sorted(set(old) | set(new)):
                if old.get(opening) == new.get(opening):
                    continue
                if opening not in new or opening not in right_end or right_end[opening] != new[opening]:
                    faults.append(f"{left.get('镜头编号')}->{right.get('镜头编号')}：{opening}状态{old.get(opening)!r}→{new.get(opening)!r}缺对应开合事件")
        time_changed = any(before[key] and after[key] and before[key] != after[key] for key in ("time_of_day", "authored_time_of_day"))
        if time_changed and not _affirmative(r"时间推移|天色[^；。]{0,8}(?:变暗|转暗)|从[^；。]{0,8}到[^；。]{0,8}(?:夜|午|晨)|入夜", event):
            faults.append(f"{left.get('镜头编号')}->{right.get('镜头编号')}：时段变化缺时间推进依据")
        color_before, color_after = dict(before["color"]), dict(after["color"])
        common_subjects = set(color_before) & set(color_after)
        if (any(color_before[key] != color_after[key] for key in common_subjects) or (before["declared_color"] and after["declared_color"] and before["declared_color"] != after["declared_color"])) and not light_event:
            faults.append(f"{left.get('镜头编号')}->{right.get('镜头编号')}：同一受光区域暖冷关系变化缺真实光源变化依据")
        reversed_path = before["screen_paths"] != after["screen_paths"] and any(
            (target, origin) in after["screen_paths"] for origin, target in before["screen_paths"] if origin != target
        )
        if before["screen_direction"] != after["screen_direction"] or reversed_path:
            world_same = bool(before["source_position"] and before["source_position"] == after["source_position"] and before["source_id"] == after["source_id"])
            camera_changed = bool(before["camera_position"] and after["camera_position"] and before["camera_position"] != after["camera_position"])
            if not (light_event or (world_same and camera_changed)):
                faults.append(f"{left.get('镜头编号')}->{right.get('镜头编号')}：画面光向变化缺同一场景光源坐标与反打机位依据")
    return list(dict.fromkeys(faults))
