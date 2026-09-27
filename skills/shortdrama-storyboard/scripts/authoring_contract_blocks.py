"""Small explicit author fragments, not a storyboard or evidence generator.

Returned append fragments never replace an authored pose, action or source text.
Names/surfaces are caller assertions, not entity lookup or semantic proof.
"""
from __future__ import annotations

import math
import re
from typing import Any


class ContractBlockError(ValueError):
    """An explicit input is absent, unsupported, or structurally inconsistent."""


def dynamic_eye_blocks(decision: dict[str, Any], *, picture_body: str) -> dict[str, Any]:
    """Serialize only explicit video eye choices; existing frame-zero API is unchanged."""
    from dynamic_eye_decisions import compile_dynamic_eye_decision, DynamicEyeDecisionError
    try:
        return compile_dynamic_eye_decision(decision, picture_body=picture_body)
    except DynamicEyeDecisionError as exc:
        raise ContractBlockError(str(exc)) from exc


def voice_block(*, mode: str, dialogue_rows: list[tuple[str, str]],
                baseline: str | None = None, speech_act: str | None = None,
                current_expression: str | None = None, volume: str | None = None,
                rhythm: str | None = None, breath: str | None = None,
                pauses: str | None = None, emphasis: str | None = None,
                tail_direction: str | None = None, tail_execution: str | None = None,
                prohibited_tones: list[str] | None = None) -> str:
    """Serialize explicit voice choices; never infer identity, intent or delivery.

    The caller still authors the real utterance phases. This fragment is not a
    complete-input validation route and cannot certify the source assertions.
    """
    from validate_storyboard import VOICE_ALLOWED_TAILS, VOICE_TEMPLATE_LABELS, _voice_template_errors

    _choice(mode, {"spoken", "silent"}, "voice.mode")
    values = [baseline, speech_act, current_expression, volume, rhythm, breath,
              pauses, emphasis, tail_direction, tail_execution, prohibited_tones]
    if not isinstance(dialogue_rows, list):
        raise ContractBlockError("voice.dialogue_rows: requires an explicit list")
    if mode == "silent":
        if dialogue_rows or any(value is not None for value in values):
            raise ContractBlockError("voice.silent: requires no dialogue rows or spoken specification")
        return "无对白，不适用。"

    def authored(value: Any, path: str) -> str:
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(ord(char) < 32 or char in "\u0085\u2028\u2029" for char in value)
                or any(re.search(re.escape(label) + r"\s*[:：]", value) for label in VOICE_TEMPLATE_LABELS)):
            raise ContractBlockError(f"{path}: requires explicit single-line content without fixed-label injection")
        return value

    if not dialogue_rows:
        raise ContractBlockError("voice.spoken: requires actual speaker/text rows")
    for index, row in enumerate(dialogue_rows):
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ContractBlockError(f"voice.dialogue_rows[{index}]: requires speaker/text pair")
        authored(row[0], f"voice.dialogue_rows[{index}].speaker")
        authored(row[1], f"voice.dialogue_rows[{index}].text")
    direction = _choice(tail_direction, set(VOICE_ALLOWED_TAILS), "voice.tail_direction")
    if not isinstance(prohibited_tones, list) or not 2 <= len(prohibited_tones) <= 4:
        raise ContractBlockError("voice.prohibited_tones: requires 2–4 explicit items")
    forbidden = []
    for index, item in enumerate(prohibited_tones):
        item = authored(item, f"voice.prohibited_tones[{index}]")
        if re.search(r"[、，,；;/／]", item):
            raise ContractBlockError("voice.prohibited_tones: one tone per item")
        forbidden.append(item)
    tail_execution = authored(tail_execution, "voice.tail_execution")
    # Bind a positional tail instruction to the supplied utterance without
    # inventing intent, changing a word, or replacing the registered voice.
    from projection_semantics import QUOTES, tail_binding_faults
    if not QUOTES.search(tail_execution) and len(dialogue_rows) == 1:
        ending = dialogue_rows[0][1].rstrip("。！？!?；;，, .…～~")
        if ending:
            tail_execution = "末字“" + ending[-1] + "”；" + tail_execution
    tail_faults = tail_binding_faults(tail_execution, dialogue_rows)
    if tail_faults:
        raise ContractBlockError("voice.tail_execution: " + "; ".join(tail_faults))
    bodies = [baseline, speech_act, current_expression, "", volume, rhythm, breath,
              pauses, emphasis,
              direction + "；" + authored(tail_execution, "voice.tail_execution"), "、".join(forbidden)]
    lines = [label + "：" + ("" if label == "发声方式" else authored(body, "voice." + label))
             for label, body in zip(VOICE_TEMPLATE_LABELS, bodies)]
    result = "\n".join(lines)
    faults = _voice_template_errors(result, "".join(row[1] for row in dialogue_rows), dialogue_rows)
    if faults:
        raise ContractBlockError("voice: " + "; ".join(faults))
    return result


# These exact independent clauses already belong to the registered projection
# vocabulary in _keyframe_generation_limits. Do not broaden its strip grammar.
VIDEO_EYE_POLICY = (
    "视频中的眼球换焦、眼球位移、眼睑松紧变化、眨眼按画面内容所列时点执行；"
    "禁止无依据全程不眨眼；禁止僵直瞪视；禁止高频机械眨眼；禁止多人同步眨眼。"
)
CURRENT_VIDEO_EYE_POLICY = (
    "眼部动作按本镜已列事件执行；未列出眼部动作时保持已声明的自然状态，不额外添加模板动作。"
)
PSYCHOLOGICAL_SAFETY = (
    "人物不张嘴，不驱动口型，不生成字幕；不计入角色对白时长，"
    "完整计入实际音轨占时。"
)
CLEAR_INTERIOR_LIMITS = (
    "室内空气清澈，无薄雾、无烟尘、无可见光束；"
    "背景结构清晰可辨，不泛白、不雾化。"
)
_LABEL = re.compile(r"[\u3400-\u9fffA-Za-z0-9·_-]{1,48}\Z")
_UNRESOLVED = re.compile(r"前镜|前一镜|上一镜|下一镜|下一个镜|同上|未知|待定|待补|未提供|按需|视情况|二选一|或")
_WORLD = {"北", "东北", "东", "东南", "南", "西南", "西", "西北"}
_TONE = {"neutral": "中性", "warm": "偏暖", "cool": "偏冷", "slightly_warm": "中性微暖", "slightly_cool": "中性微冷"}


def _label(value: Any, path: str) -> str:
    # Lazy import avoids an import-time cycle if an author entry loads both
    # modules, and shares the registered cross-context vocabulary verbatim.
    from validate_storyboard import CROSS_SHOT_TERMS
    if (not isinstance(value, str) or not _LABEL.fullmatch(value)
            or _UNRESOLVED.search(value) or any(term in value for term in CROSS_SHOT_TERMS)):
        raise ContractBlockError(f"{path}: requires a concrete plain label, not prose, an alternative or a cross-shot reference")
    return value


def _choice(value: Any, choices: set[str] | dict[str, str], path: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ContractBlockError(f"{path}: unsupported value; choose {', '.join(sorted(choices))}")
    return value


def _labels(value: Any, path: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ContractBlockError(f"{path}: requires a nonempty explicit list of labels")
    result = [_label(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if len(set(result)) != len(result):
        raise ContractBlockError(f"{path}: duplicate labels do not describe extra surfaces")
    return result


def eye_blocks(*, subject: str | None, visibility: str, state: str | None,
               source_basis: str | None = None, planned_events: bool = False,
               legacy_components: bool = False) -> dict[str, str]:
    """Return separate append fragments for the actual initial eye decision.

    source_closed requires an authored source basis but does not verify it or
    put that unobservable explanation into an image instruction.
    Current generation allows natural stillness. The original policy text is
    retained only for explicit legacy replay, not selected by a missing plan.
    """
    visibility = _choice(visibility, {"visible", "hidden", "no_people"}, "visibility")
    if visibility == "no_people":
        if subject is not None or state is not None or source_basis is not None:
            raise ContractBlockError("no_people: subject, state and source_basis must all be None")
        return {"initial_state_append": "", "generation_limits_append": ""}
    name = _label(subject, "subject")
    if visibility == "hidden":
        if state is not None or source_basis is not None:
            raise ContractBlockError("hidden: no visible frame0 eye state or source_basis may be asserted")
        return {"initial_state_append": f"{name}眼睛不入画。", "generation_limits_append": ""}
    _choice(state, {"natural_open", "source_closed"}, "state")
    if state == "source_closed":
        if not isinstance(source_basis, str) or not source_basis.strip():
            raise ContractBlockError("source_basis: source_closed requires an explicit nonempty source basis")
        return {"initial_state_append": f"{name}双眼合拢。", "generation_limits_append": ""}
    if source_basis is not None:
        raise ContractBlockError("source_basis: only source_closed accepts this argument")
    if planned_events or not legacy_components:
        return {'initial_state_append': f'{name}双眼自然睁开，不处于半眨眼瞬间；{name}眼神自然聚焦。',
                'generation_limits_append': CURRENT_VIDEO_EYE_POLICY}
    return {"initial_state_append": f"{name}双眼自然睁开，不处于半眨眼瞬间；{name}眼神自然聚焦。",
            "generation_limits_append": f"{name}眼神自然聚焦；" + VIDEO_EYE_POLICY}


def psychological_safety_suffix() -> str:
    """Append after the caller's unchanged psychological/narration payload.

    This does not allocate timing, turn dialogue into narration, or certify
    source entitlement. Actual registered audio segments remain mandatory.
    """
    return PSYCHOLOGICAL_SAFETY


def transition_block(*, method: str, destination_place: str | None = None,
                     destination_subject: str | None = None) -> dict[str, str]:
    """Describe a cut without borrowing any previous/next shot's state."""
    methods = {"hard_cut": "硬切", "dissolve": "叠化"}
    method = _choice(method, methods, "method")
    place = _label(destination_place, "destination_place") if destination_place is not None else None
    subject = _label(destination_subject, "destination_subject") if destination_subject is not None else None
    label = methods[method]
    prompt = label + "。"
    if place and subject:
        prompt = f"{label}至{place}的{subject}。"
    elif place or subject:
        prompt = f"{label}至{place or subject}。"
    return {"转场类型": label, "切镜/转场提示词": prompt}


def scene_lighting_blocks(*, source: dict[str, Any], screen_side: str,
                             receiving_surfaces: list[str], weak_reflector: str | None,
                             main_tone: str, shadow_tone: str, materials: list[str],
                             air: str, exposure: str) -> dict[str, Any]:
    """Render explicitly supplied ordinary indoor or outdoor daylight decisions.

    Supported narrow cases: day paper/glass-window daylight (open/closed), or a lit
    night oil lamp/candle/lantern, or outdoor daylight. Screen side must come from the
    caller's actual staging; this function does not certify that computation.
    """
    outdoor = isinstance(source, dict) and source.get("kind") == "outdoor_daylight"
    dimension = "elevation_deg" if outdoor else "height_m"
    required = {"period", "kind", "name", "state", "world_side", dimension}
    if not isinstance(source, dict) or set(source) != required:
        raise ContractBlockError("source: requires exactly period, kind, name, state, world_side, " + dimension)
    period = _choice(source["period"], {"day", "night"}, "source.period")
    kind = _choice(source["kind"], {"paper_window", "glass_window", "oil_lamp", "candle", "lantern", "outdoor_daylight"}, "source.kind")
    name = _label(source["name"], "source.name")
    world = _choice(source["world_side"], _WORLD, "source.world_side")
    screen = _choice(screen_side, {"左", "右", "正前"}, "screen_side")
    height = source[dimension]
    try:
        valid_height = (not isinstance(height, bool) and isinstance(height, (int, float))
                        and math.isfinite(height) and height > 0 and (not outdoor or height < 90))
    except OverflowError:
        valid_height = False
    if not valid_height:
        raise ContractBlockError("source." + dimension + ": requires a finite positive number, not a bool" +
                                 ("; elevation must be below 90 degrees" if outdoor else ""))
    receivers = "、".join(_labels(receiving_surfaces, "receiving_surfaces"))
    material = "、".join(_labels(materials, "materials"))
    reflector = None if weak_reflector is None else _label(weak_reflector, "weak_reflector")
    main = _TONE[_choice(main_tone, _TONE, "main_tone")]
    shadow = _TONE[_choice(shadow_tone, _TONE, "shadow_tone")]
    _choice(air, {"clear"}, "air")
    _choice(exposure, {"normal"}, "exposure")
    if period == "day" and outdoor:
        _choice(source["state"], {"present"}, "source.state")
        physical = "室外日间；已建立日间自然天光。"
        origin = f"场景{world}侧上方的日间自然天光（{name}）为本镜主光，来光仰角{height:g}度。"
    elif period == "day" and kind == "paper_window":
        state = _choice(source["state"], {"open", "closed"}, "source.state")
        if state == "closed":
            physical = f"室内日间；{name}关闭，窗纸透入漫射天光。"
            origin = f"场景{world}侧的{name}通过闭合窗纸透入漫射天光，作为本镜主光。"
        else:
            physical = f"室内日间；{name}敞开。"
            origin = f"日间漫射天光经场景{world}侧敞开的{name}进入，作为本镜主光。"
    elif period == "day" and kind == "glass_window":
        state = _choice(source["state"], {"open", "closed"}, "source.state")
        if state == "closed":
            physical = f"室内日间；玻璃窗（{name}）关闭，日间自然光透过玻璃进入室内。"
            origin = f"日间自然光通过场景{world}侧{name}的闭合玻璃进入，作为本镜主光。"
        else:
            physical = f"室内日间；玻璃窗（{name}）敞开。"
            origin = f"日间自然光经场景{world}侧敞开的{name}开口进入，作为本镜主光。"
    elif period == "night" and kind in {"oil_lamp", "candle", "lantern"}:
        _choice(source["state"], {"lit"}, "source.state")
        kinds = {"oil_lamp": "油灯", "candle": "蜡烛", "lantern": "灯笼"}
        placement = "画面中部" if screen == "正前" else f"画面{screen}侧"
        physical = f"室内夜间；{placement}的{kinds[kind]}（{name}）已点亮。"
        origin = f"场景{world}侧已点亮的{kinds[kind]}（{name}）为本镜主光。"
    else:
        raise ContractBlockError("source: unsupported period/kind pairing; do not infer a missing light source")
    fill = f"{reflector}提供较弱反射补光。" if reflector else ""
    dark = f"暗侧由{reflector}弱反射补光。" if reflector else "暗侧保留自然明暗层次。"
    shading = ("可见表面随朝向呈现自然明暗层次。" if outdoor else
               "亮度沿可见表面与空间纵深衰减。")
    fields = {
        "光影": f"{receivers}呈现自然明暗层次。曝光正常，高光不过曝，暗部保留材质灰阶；空气清澈，背景结构清晰可辨，不泛白、不雾化。" + shading + dark,
        "光源来源": origin + ("" if outdoor else f"光源离地{height:g}米。") + fill,
        "光源方向": f"场景{world}侧主光位于画面{screen}侧，落向{receivers}。",
        "色温倾向": f"主光{main}，暗侧{shadow}；可见材质的{material}本色保持自然，不作整幅染色。",
    }
    return {"spatial_state_append": physical, "lighting_fields": fields,
            "generation_limits_append": ("室外" + CLEAR_INTERIOR_LIMITS.removeprefix("室内")) if outdoor else CLEAR_INTERIOR_LIMITS}


def interior_lighting_blocks(**kwargs) -> dict[str, Any]:
    """Compatibility entry for the existing indoor-only fragment contract."""
    source = kwargs.get("source")
    if isinstance(source, dict) and source.get("kind") == "outdoor_daylight":
        raise ContractBlockError("source.kind: outdoor daylight requires scene_lighting_blocks")
    return scene_lighting_blocks(**kwargs)
