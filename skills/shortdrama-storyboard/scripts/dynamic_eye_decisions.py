"""Serialize explicit, stage-bound eye choices without inventing choreography.

This is an authoring component, not source/geometry approval. Visibility bases
must already occur as this actor's affirmative assertions in the actual body.
The component does not infer whether an authored camera really sees an eye.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import math
import re
from typing import Any


class DynamicEyeDecisionError(ValueError):
    pass


def _object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DynamicEyeDecisionError(f"{path}: requires exactly {sorted(keys)}")
    return value


def _label(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9·_-]{1,48}", value):
        raise DynamicEyeDecisionError(f"{path}: requires a concrete single label")
    if re.search(r"未知|待定|某处|原目标|同一点|另一处|同上|下一镜|或者|二选一", value):
        raise DynamicEyeDecisionError(f"{path}: unresolved label")
    return value


def _time(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DynamicEyeDecisionError(f"{path}: requires a finite numeric tenth-second value")
    try:
        number = float(value)
        precise = Decimal(str(value))
        valid = math.isfinite(number) and number >= 0 and precise * 10 == (precise * 10).to_integral_value()
    except (ValueError, OverflowError):
        valid = False
    if not valid:
        raise DynamicEyeDecisionError(f"{path}: requires a finite nonnegative tenth-second value")
    return number


def _target(value: Any, path: str) -> dict[str, str]:
    value = _object(value, {'id', 'label'}, path)
    return {key: _label(value[key], f'{path}.{key}') for key in ['id', 'label']}


def compile_dynamic_eye_decision(decision: dict[str, Any], *, picture_body: str) -> dict[str, Any]:
    """Return a new body, per-stage append fragments and real timing events.

    ``phases`` covers the exact authored body stages, including hidden ones.
    Targets are chosen by the caller. Distinct shift endpoints must be supplied;
    a hold has exactly one target and never emits a fictitious focus shift.
    Existing eye choreography for this subject is rejected rather than merged
    with a competing track. Other subjects and actual dialogue remain intact.
    """
    from performance_checks import (
        STAGE_RE, QUOTE_RE, HIDDEN_RE, VISIBLE_RE, FOCUS_ACTION_RE,
        EYELID_CHANGE_RE, BLINK_RE, _clauses, _positive, _action_matches,
        _gaze_paths, _subject,
    )
    decision = _object(decision, {'version', 'subject', 'shot_id', 'duration', 'phases'}, 'eye')
    if type(decision['version']) is not int or decision['version'] != 1:
        raise DynamicEyeDecisionError('eye.version: expected integer 1')
    subject = _label(decision['subject'], 'eye.subject')
    shot_id = _label(decision['shot_id'], 'eye.shot_id')
    duration = _time(decision['duration'], 'eye.duration')
    if duration <= 0 or not isinstance(picture_body, str):
        raise DynamicEyeDecisionError('eye: requires positive duration and actual picture_body')
    stages = list(STAGE_RE.finditer(picture_body))
    phases = decision['phases']
    if not stages or not isinstance(phases, list) or len(phases) != len(stages):
        raise DynamicEyeDecisionError('eye.phases: must match every actual body stage')
    if picture_body[:stages[0].start()].strip():
        raise DynamicEyeDecisionError('eye.picture_body: unbound text before first stage')
    append_rows, events, chunks = [], [], []
    cursor = 0.0
    previous_target = None
    first_target = None
    target_labels: dict[str, str] = {}
    for index, (phase, stage) in enumerate(zip(phases, stages)):
        path = f'eye.phases[{index}]'
        phase = _object(phase, {'start', 'end', 'visibility', 'visibility_basis', 'mode',
                                'targets', 'head_relation', 'head_basis', 'eyelid', 'blinks'}, path)
        begin, end = _time(phase['start'], path+'.start'), _time(phase['end'], path+'.end')
        if not (begin == cursor < end <= duration and
                begin == float(stage.group('start')) and end == float(stage.group('end'))):
            raise DynamicEyeDecisionError(path+': times must partition the actual authored stages')
        cursor = end
        body = stage.group('body')
        unquoted = QUOTE_RE.sub('〈引文〉', body)
        clauses = _clauses(unquoted, [subject], duration)
        own = [c.text for c in clauses if _subject(c.text, [subject]) == subject]
        basis = phase['visibility_basis']
        if not isinstance(basis, str) or basis not in own:
            raise DynamicEyeDecisionError(path+'.visibility_basis: requires an exact actor-owned unquoted body clause')
        visibility = phase['visibility']
        if visibility not in {'visible', 'hidden'}:
            raise DynamicEyeDecisionError(path+'.visibility: expected visible or hidden')
        if not _positive(VISIBLE_RE if visibility == 'visible' else HIDDEN_RE, basis):
            raise DynamicEyeDecisionError(path+'.visibility_basis: does not affirm the selected visibility')
        # Contradictory visibility cannot be resolved by choosing one statement.
        if any(_positive(HIDDEN_RE if visibility == 'visible' else VISIBLE_RE, text) for text in own):
            raise DynamicEyeDecisionError(path+': conflicting eye visibility in actual body')
        all_owned = [c.text for c in clauses if c.owner == subject]
        if any(_action_matches(FOCUS_ACTION_RE, t) or _action_matches(EYELID_CHANGE_RE, t)
               or _positive(BLINK_RE, t) or _gaze_paths(t) for t in all_owned):
            raise DynamicEyeDecisionError(path+': actual body already contains eye choreography; do not overlay a second track')
        targets = phase['targets']
        if not isinstance(targets, list):
            raise DynamicEyeDecisionError(path+'.targets: requires an array')
        targets = [_target(value, path+'.targets') for value in targets]
        for target in targets:
            if target['id'] in target_labels and target_labels[target['id']] != target['label']:
                raise DynamicEyeDecisionError(path+': one target id has competing labels')
            target_labels[target['id']] = target['label']
        blinks = phase['blinks']
        if not isinstance(blinks, list):
            raise DynamicEyeDecisionError(path+'.blinks: requires an explicit array')
        if visibility == 'hidden':
            if (phase['mode'] != 'none' or targets or phase['head_relation'] is not None or
                    phase['head_basis'] is not None or phase['eyelid'] is not None or blinks):
                raise DynamicEyeDecisionError(path+': hidden eyes cannot carry visible eye choreography')
            # Hidden motion is not inferred; a later visible segment establishes
            # its own target explicitly rather than bridging an unseen movement.
            previous_target = None
            append_rows.append({'start': begin, 'end': end, 'text': ''})
            chunks.append(stage.group(0).rstrip())
            continue
        mode = phase['mode']
        relation = phase['head_relation']
        if mode not in {'hold', 'shift', 'head_led'}:
            raise DynamicEyeDecisionError(path+'.mode: expected hold, shift or head_led')
        if len(targets) != (1 if mode == 'hold' else 2):
            raise DynamicEyeDecisionError(path+'.targets: hold needs one target; movement needs two endpoints')
        if mode != 'hold' and (targets[0]['id'] == targets[1]['id'] or targets[0]['label'] == targets[1]['label']):
            raise DynamicEyeDecisionError(path+': a shift needs distinct, explicit endpoints')
        if previous_target is not None and targets[0] != previous_target:
            raise DynamicEyeDecisionError(path+': initial target contradicts the preceding visible phase; no silent state repair')
        if first_target is None:
            first_target = targets[0]
        previous_target = targets[-1]
        allowed = {'hold': {'still', 'tracks_target'}, 'shift': {'still', 'eyes_first'}, 'head_led': {'follows_head'}}
        if relation not in allowed[mode]:
            raise DynamicEyeDecisionError(path+'.head_relation: incompatible with selected mode')
        head_basis = phase['head_basis']
        if not isinstance(head_basis, str) or head_basis not in own:
            raise DynamicEyeDecisionError(path+'.head_basis: requires an exact actor-owned unquoted head-action clause')
        head_patterns = {
            'still': r'头部(?:保持(?:当前方向|不动)|不跟随|不转|不动)',
            'eyes_first': r'头部(?:随后|再)[^，,；;。]{0,16}?(?P<verb>跟|转|偏|低|抬)',
            'follows_head': r'(?P<preverb>低头|抬头)|头部[^，,；;。]{0,16}?(?P<verb>低|抬|转|偏)',
            'tracks_target': r'头部[^，,；;。]{0,16}?(?P<verb>转|偏|低|抬)',
        }
        match_head = _positive if relation == 'still' else _action_matches
        if not match_head(re.compile(head_patterns[relation]), head_basis):
            raise DynamicEyeDecisionError(path+'.head_basis: does not affirm the selected head relation')
        if phase['eyelid'] not in {'relax', 'slightly_tighten'}:
            raise DynamicEyeDecisionError(path+'.eyelid: requires relax or slightly_tighten')
        origin, target = targets[0]['label'], targets[-1]['label']
        if mode == 'hold':
            action = f'视线持续落在{target}'
            gaze = f'{subject}{action}，保持对同一目标的自然注视'
            if relation == 'tracks_target':
                gaze += f'，眼球随头部角度调整继续对准{target}'
        elif mode == 'shift':
            action = f'眼球从{origin}轻微换焦至{target}'
            gaze = f'{subject}{action}'
            gaze += '，头部保持当前方向' if relation == 'still' else '，眼球先动，头部随后轻跟'
        else:
            action = f'眼球随头部动作从{origin}移向{target}'
            gaze = f'{subject}{action}，头部带动视线'
        gaze += '。'
        lid = '自然放松' if phase['eyelid'] == 'relax' else '轻微收紧'
        text = gaze + f'{subject}眼睑{lid}。'
        event_id = f'{shot_id}-EYE-{subject}-{index+1}'
        events.append({'id': event_id, 'shot_id': shot_id, 'kind': 'hold' if mode == 'hold' else 'action',
                       'actor': subject, 'action': action, 'basis': gaze,
                       'start': begin, 'end': end, 'after': []})
        blink_end = begin
        for ordinal, blink in enumerate(blinks):
            blink = _object(blink, {'start', 'end'}, path+'.blinks')
            left, right = _time(blink['start'], path+'.blinks.start'), _time(blink['end'], path+'.blinks.end')
            if not begin <= left < right <= end or left < blink_end:
                raise DynamicEyeDecisionError(path+'.blinks: requires ordered, non-overlapping spans inside the visible phase')
            blink_end = right
            basis = f'{subject}在{left:.1f}秒自然眨眼一次'
            text += basis + f'，{subject}在{right:.1f}秒双眼恢复自然睁开。'
            events.append({'id': event_id+f'-BLINK-{ordinal+1}', 'shot_id': shot_id, 'kind': 'action',
                           'actor': subject, 'action': '眨眼', 'basis': basis,
                           'start': left, 'end': right, 'after': []})
        text += f'{subject}最终视线落在{target}。'
        append_rows.append({'start': begin, 'end': end, 'text': text})
        chunks.append(stage.group(0).rstrip() + text)
    if cursor != duration:
        raise DynamicEyeDecisionError('eye.phases: final stage must end at shot duration')
    encoded = json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return {'picture': '\n'.join(chunks), 'phase_appends': append_rows, 'events': events,
            'initial_target': first_target, 'final_target': previous_target,
            'decision_sha256': hashlib.sha256(encoded).hexdigest(),
            'review_obligations': ['Actual camera/occlusion visibility, source support and head/gaze feasibility require independent review.']}
