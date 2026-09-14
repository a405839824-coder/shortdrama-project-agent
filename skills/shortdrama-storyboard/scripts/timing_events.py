"""Validate explicit action dependencies without charging fixed action fees."""
from __future__ import annotations

import math
import re
from typing import Any


STAGES = re.compile(r"【(\d+\.\d)—(\d+\.\d)秒】(.*?)(?=【\d+\.\d—\d+\.\d秒】|\Z)", re.S)
NEGATION = re.compile(r"(?:禁止|不得|避免|不能|不要|不必|无需|没有|不再|并未|未曾|不|无|未)[^，,；;。！？!?、\n]{0,6}$")
MOUTH_ACTIONS = r"喝水|饮水|喝茶|饮茶|浅啜|啜饮|吞咽|咽下"
CLOSED_MOUTH_ACTIONS = r"闭唇|合唇|闭嘴|(?:双唇|嘴唇)(?:保持)?(?:闭合|紧闭)"
ACTION_GROUPS = (
    r"喝水|饮水", r"喝茶|饮茶|浅啜|啜饮", r"吞咽|咽下",
    r"点头|颔首", r"行礼|作揖|鞠躬", r"起身|站起", r"落座|坐下",
    r"抬手|收手", r"递交|递出|递给", r"转头|转身", r"停住|停留|保持|稳定|等待",
)
SERIAL = re.compile(r"先\s*(?:轻轻|微微)?(?P<action>点头|颔首|喝水|饮水|喝茶|浅啜|吞咽|行礼|作揖|鞠躬|起身|落座|抬手|递交|转身)[^。；;！？!?]{0,30}?(?:随后|然后|后|再)[^。；;！？!?]{0,12}?(?:说道|说话|说|开口)")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _fault(text: str, shot_id: str | None = None, *, code: str = "E-TIMELINE-001") -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": text, "evidence": text}
    if shot_id:
        result["shot_id"] = shot_id
    return result


def _without_speech(picture: str) -> str:
    return re.sub(r'(说道|说)[：:]?\s*[“"][^”"\n]*[”"]', r"\1〈发言〉", picture)


def _negated_before(text: str, position: int, actors: tuple[str, ...] = ()) -> bool:
    prefix = text[:position]
    # A declared actor's exact identity is a noun, not an action modifier.
    # Keep offsets unchanged and retain every negation outside those names.
    # Subject ownership is still checked separately against the original prose.
    for actor in sorted(set(actors), key=len, reverse=True):
        if actor:
            name_at_subject = re.compile(
                r"(?P<lead>(?:^|[，,；;。！？!?】\n]|〈发言〉)\s*(?:(?:在|于)?\d+(?:\.\d+)?秒)?\s*|"
                r"(?:看着|观察|望着|看见))" + re.escape(actor)
            )
            prefix = name_at_subject.sub(lambda m: m.group("lead") + "\ufffc" * len(actor), prefix)
    return bool(NEGATION.search(prefix[-16:]))


def _positive(pattern: str, text: str, actors: tuple[str, ...] = ()) -> bool:
    return any(not _negated_before(text, match.start(), actors) for match in re.finditer(pattern, text))


def _closed_mouth_action(action: str) -> bool:
    """An affirmative event action, not a quoted/conditional mention of it.

    Only the explicit action owns the supplied interval. A basis paragraph can
    describe closing then speaking without declaring that whole paragraph a
    closed-mouth state; conversely it cannot shorten a declared action span.
    """
    text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"\n]*"|\'[^\'\n]*\'',
                  lambda match: " " * len(match.group()), action)
    conditional = False
    for clause in re.split(r"([，,；;。！？!?\n])", text):
        if re.fullmatch(r"[；;。！？!?\n]", clause):
            conditional = False
            continue
        if re.match(r"\s*(?:但|然而|不过|实际|当前|现在)", clause):
            conditional = False
        for match in re.finditer(CLOSED_MOUTH_ACTIONS, clause):
            prefix, suffix = clause[:match.start()], clause[match.end():]
            if re.search(r"如果|假如|倘若|若", prefix):
                conditional = True
            if conditional or _negated_before(clause, match.start()):
                continue
            if re.match(r"\s*(?:动作|状态)?\s*(?:并)?(?:不发生|未发生|不存在|不成立|取消)", suffix):
                continue
            return True
        if re.search(r"如果|假如|倘若|若", clause):
            conditional = True
    return False


def _action_pattern(action: str) -> str:
    if _closed_mouth_action(action):
        # Generic 保持 must not lend an unrelated hand/body hold to this state.
        return CLOSED_MOUTH_ACTIONS
    for group in ACTION_GROUPS:
        if re.search(group, action):
            return group
    return re.escape(action)


def _same_subject(picture: str, action_position: int, actor: str) -> bool:
    """Bind a physical action to its own Chinese clause, with local ellipsis."""
    clauses = re.split(r"[，,；;。！？!?】\n]", picture[:action_position])
    inherited = False
    for prefix in reversed(clauses):
        prefix = re.sub(r"^\s*(?:(?:在|于)?\d+(?:\.\d+)?秒)?\s*", "", prefix).strip()
        if not prefix or re.fullmatch(r"(?:(?:轻轻|微微|自然|略微|稍微|随后|接着|再|仍|继续|慢慢|片刻后|听完后)(?:地)?)+", prefix):
            inherited = True
            continue
        observer = re.search(r"(?:看着|观察|望着|看见)([\u3400-\u9fffA-Za-z·]{1,12})$", prefix)
        if observer and not inherited:
            return observer.group(1) == actor
        return prefix.startswith(actor)
    return False


def _basis_positions(event: dict[str, Any], shot: dict[str, Any]) -> list[int]:
    picture = _without_speech(str(shot.get("画面内容") or ""))
    basis = event["basis"]
    action = event["action"]
    actor = event["actor"]
    if basis not in picture or not _positive(_action_pattern(action), basis, (actor,)):
        return []
    # A source quote mentioning an action is not an event, and a label cannot
    # lend another person's physical action to this actor.
    position = picture.find(basis)
    positions = []
    while position >= 0:
        for match in re.finditer(_action_pattern(action), basis):
            absolute = position + match.start()
            if _same_subject(picture, absolute, actor) and not _negated_before(picture, absolute, (actor,)):
                positions.append(absolute)
        position = picture.find(basis, position + 1)
    return positions


def _shot_index(shots: Any) -> tuple[dict[str, dict[str, Any]], dict[str, float], list[dict[str, Any]]]:
    from picture_presentation import timing_view, PictureTimingError
    rows = list(shots.values()) if isinstance(shots, dict) else shots
    index: dict[str, dict[str, Any]] = {}
    offsets: dict[str, float] = {}
    faults = []
    elapsed = 0.0
    for ordinal, shot in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(shot, dict):
            faults.append(_fault(f"第{ordinal + 1}镜不是有效镜头记录"))
            continue
        shot_id = str(shot.get("镜头编号") or shot.get("shot_id") or "")
        duration = _number(shot.get("时长", shot.get("duration")))
        if not shot_id or shot_id in index or duration is None or duration <= 0:
            faults.append(_fault("动作事件引用的镜头身份重复/缺失，或镜长不是有限正数", shot_id))
            continue
        try:
            index[shot_id] = timing_view(shot)
        except PictureTimingError as exc:
            faults.append(_fault(str(exc), shot_id))
            continue
        offsets[shot_id] = elapsed
        elapsed += duration
    return index, offsets, faults


def _span_bounds(span: dict[str, Any], offsets: dict[str, float]) -> tuple[float, float] | None:
    start, end = _number(span.get("global_start")), _number(span.get("global_end"))
    if start is None or end is None:
        local_start, local_end = _number(span.get("start")), _number(span.get("end"))
        offset = offsets.get(str(span.get("shot_id") or ""))
        if offset is None or local_start is None or local_end is None:
            return None
        start, end = offset + local_start, offset + local_end
    return (start, end) if 0 <= start < end else None


def _char_bounds(value: Any) -> tuple[int, int] | None:
    if isinstance(value, dict):
        value = [value.get("start"), value.get("end")]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    return tuple(value) if 0 <= value[0] < value[1] else None


def _natural_serial_faults(index: dict[str, dict[str, Any]], speech_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    faults = []
    for shot_id, shot in index.items():
        picture = _without_speech(str(shot.get("画面内容") or ""))
        actors = tuple(str(span.get("speaker") or "") for span in speech_spans if str(span.get("shot_id") or "") == shot_id)
        phases = [(float(match.group(1)), float(match.group(2)), match.group(3)) for match in STAGES.finditer(picture)]
        if not phases:
            phases = [(0.0, float(shot.get("时长", shot.get("duration"))), picture)]
        for start, end, body in phases:
            serial = [match for match in SERIAL.finditer(body) if not _negated_before(body, match.start(), actors) and _positive(re.escape(match.group("action")), match.group(), actors)]
            if not serial:
                continue
            spans = []
            for span in speech_spans:
                if str(span.get("shot_id") or "") != shot_id or str(span.get("kind") or "dialogue") in {"psychological", "psych", "psychological_audio"}:
                    continue
                left, right = _number(span.get("start")), _number(span.get("end"))
                if left is not None and right is not None and left < end and right > start:
                    spans.append((max(start, left), min(end, right)))
            merged = []
            for left, right in sorted(spans):
                if merged and left <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
                else:
                    merged.append((left, right))
            if sum(right - left for left, right in merged) >= end - start - 1e-9:
                faults.append(_fault(f"{start:g}—{end:g}秒阶段写‘{serial[0].group()}’，但对应发声已占满阶段，没有正时长容纳在先动作", shot_id, code="E-BATCH-003"))
    return faults


def validate_events(project: dict[str, Any], shots: Any, speech_spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Return faults/advisories and normalized, identity-preserving events."""
    index, offsets, faults = _shot_index(shots)
    result: dict[str, Any] = {"faults": faults, "advisories": [], "events": []}
    prevention = project.get("prevention_evidence") or {}
    nested = prevention.get("audio_workflow") if isinstance(prevention, dict) else None
    legacy = project.get("audio_workflow")
    if nested is not None and legacy is not None and nested != legacy:
        faults.append(_fault("prevention_evidence.audio_workflow与旧顶层副本冲突，不能选择性忽略动作事件"))
    workflow = nested if nested is not None else (legacy or {})
    timing = workflow.get("timing") or {} if isinstance(workflow, dict) else {}
    raw_events = timing.get("events") if isinstance(timing, dict) else None
    faults.extend(_natural_serial_faults(index, speech_spans))
    if raw_events is None or raw_events == []:
        return result
    if not isinstance(raw_events, list):
        faults.append(_fault("audio_workflow.timing.events必须是事件列表"))
        return result
    references: dict[str, list[dict[str, Any]]] = {}
    utterances: dict[str, list[dict[str, Any]]] = {}
    for span in speech_spans:
        if not isinstance(span, dict):
            continue
        span_id = str(span.get("id") or "")
        bounds = _span_bounds(span, offsets)
        if not span_id or bounds is None:
            faults.append(_fault("发言事件缺身份或有效时间，不能作为动作依赖", str(span.get("shot_id") or "")))
            continue
        normalized = dict(span, global_start=bounds[0], global_end=bounds[1])
        references.setdefault(span_id, []).append(normalized)
        utterances.setdefault(str(span.get("utterance_id") or span_id), []).append(normalized)
    seen = set(references)
    for raw in raw_events:
        if not isinstance(raw, dict):
            faults.append(_fault("动作时间事件必须是对象"))
            continue
        shot_id, event_id = str(raw.get("shot_id") or ""), str(raw.get("id") or "")
        shot = index.get(shot_id)
        if not isinstance(raw.get("id"), str) or not event_id or event_id in seen:
            faults.append(_fault(f"动作事件id缺失或重复：{event_id!r}", shot_id))
            continue
        seen.add(event_id)
        if shot is None:
            faults.append(_fault(f"事件{event_id}引用不存在的镜头", shot_id))
            continue
        start, end = _number(raw.get("start")), _number(raw.get("end"))
        duration = float(shot.get("时长", shot.get("duration")))
        if start is None or end is None or not 0 <= start < end:
            faults.append(_fault(f"事件{event_id}起止必须有限、非负、正时长且不超过{duration:g}秒镜长", shot_id))
            continue
        if end > duration:
            faults.append(_fault(f"事件{event_id}结束于{end:g}秒，超过{duration:g}秒镜长", shot_id, code="E-BATCH-003"))
            continue
        if raw.get("kind") not in {"action", "reaction", "hold"} or any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in ("actor", "action", "basis")):
            faults.append(_fault(f"事件{event_id}缺合法kind、actor、action或basis", shot_id))
            continue
        after = raw.get("after", [])
        if not isinstance(after, list) or any(not isinstance(item, str) or not item for item in after) or len(after) != len(set(after)):
            faults.append(_fault(f"事件{event_id}的after必须是唯一事件/发言id列表", shot_id))
            continue
        event = dict(raw, start=start, end=end, after=after, global_start=offsets[shot_id] + start, global_end=offsets[shot_id] + end)
        positions = _basis_positions(event, shot)
        if not positions:
            faults.append(_fault(f"事件{event_id}的动作依据未在对应人物实际画面中成立，否定动作及对白文字不算已发生动作", shot_id))
        else:
            picture = _without_speech(str(shot.get("画面内容") or ""))
            stages = list(STAGES.finditer(picture))
            owned = [(float(stage.group(1)), float(stage.group(2))) for stage in stages if any(stage.start(3) <= position < stage.end(3) for position in positions)]
            if stages and not any(left <= start and end <= right for left, right in owned):
                faults.append(_fault(f"事件{event_id}的{start:g}—{end:g}秒超出其动作依据所属阶段{owned}；跨段动作须分段绑定并保留连续依赖", shot_id, code="E-BATCH-003"))
        result["events"].append(event)
        references[event_id] = [event]
    for event in result["events"]:
        shot_id, event_id = event["shot_id"], event["id"]
        for dependency in event["after"]:
            prior = references.get(dependency)
            if not prior:
                faults.append(_fault(f"事件{event_id}依赖未知事件/发言{dependency}", shot_id))
            elif dependency == event_id or max(item["global_end"] for item in prior) > event["global_start"] + 1e-9:
                faults.append(_fault(f"事件{event_id}在前置{dependency}结束前开始，串行依赖不成立", shot_id))
        closed_mouth = _closed_mouth_action(event["action"])
        if closed_mouth or re.search(MOUTH_ACTIONS, event["action"]):
            for span in speech_spans:
                if span.get("speaker") != event["actor"] or str(span.get("kind") or "dialogue") in {"psychological", "psych", "psychological_audio"}:
                    continue
                bounds = _span_bounds(span, offsets)
                if bounds and event["global_start"] < bounds[1] - 1e-9 and event["global_end"] > bounds[0] + 1e-9:
                    state = "闭唇" if closed_mouth else "喝水/吞咽"
                    faults.append(_fault(f"事件{event_id}的{state}与同人发声{span.get('id')}重叠", shot_id))
        trigger = event.get("trigger")
        if event["kind"] == "reaction" and not isinstance(trigger, dict):
            faults.append(_fault(f"反应事件{event_id}缺发言字符触发点", shot_id))
            continue
        if trigger is not None:
            if not isinstance(trigger, dict) or not isinstance(trigger.get("utterance_id"), str) or isinstance(trigger.get("offset"), bool) or not isinstance(trigger.get("offset"), int):
                faults.append(_fault(f"事件{event_id}触发点须含utterance_id和整数offset", shot_id))
                continue
            candidates = []
            offset = trigger["offset"]
            for span in utterances.get(trigger["utterance_id"], []):
                chars = _char_bounds(span.get("char_range"))
                if chars and chars[0] <= offset <= chars[1]:
                    reached = span["global_start"] + (offset - chars[0]) / (chars[1] - chars[0]) * (span["global_end"] - span["global_start"])
                    candidates.append((reached, chars))
            if not candidates:
                faults.append(_fault(f"事件{event_id}的触发发言/字符偏移无实际发声片段可证明", shot_id))
            elif len([chars for _, chars in candidates if chars[0] < offset < chars[1]]) > 1:
                faults.append(_fault(f"事件{event_id}的触发字符同时属于多个发声片段，依据不唯一", shot_id))
            elif event["global_start"] + 1e-9 < min(reached for reached, _ in candidates):
                faults.append(_fault(f"事件{event_id}反应早于对应发言到达字符{offset}的时间", shot_id))
    return result
