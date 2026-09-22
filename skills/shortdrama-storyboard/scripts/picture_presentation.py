"""Untimed picture prose with lossless, content-bound internal clock annotations.

Production clocks and repeated static phase assertions have a reversible internal
view. Quoted content and real event repetitions remain byte-faithful. The store
cannot insert actions; every token and fold is recomputed, never caller-chosen.
"""
from __future__ import annotations

import copy
import hashlib
import re

FIELD = "picture_timing"
CONTRACT = "picture-clock-annotations-v1"
COMPACT_CONTRACT = "picture-clock-annotations-v2"
FORMAT = "natural-actions-v1"
_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+(?:点[零〇一二两三四五六七八九]+)?|半)"
_UNIT = r"(?:秒|[sS](?![A-Za-z]))"
_RANGE = rf"{_NUMBER}\s*(?:{_UNIT})?\s*[-—–~至]\s*{_NUMBER}\s*{_UNIT}"
_POINT = rf"{_NUMBER}\s*{_UNIT}"
# Keep story dates and relative narrative time (明日/三日后) untouched. Arabic
# seconds outside literal payloads are production clock annotations in this
# explicit output format. A literal countdown belongs in a quoted payload.
CLOCK_RE = re.compile(
    rf"(?<![A-Za-z0-9_.])(?:仅(?:在|于){_RANGE}|"
    rf"[【\[]\s*{_RANGE}\s*[】\]][ \t]*(?:[:：][ \t]*)?|"
    rf"(?:{_RANGE}|{_POINT})[ \t]*[:：][ \t]*|"
    rf"(?:于|在)?(?:第|约|t\s*=\s*)?(?:{_RANGE}|{_POINT})(?:左右)?(?:之后|之前|后|前|时|处|内)?)(?:[，,][ \t]*)?"
)
LEGACY_CLOCK_RE = re.compile(CLOCK_RE.pattern.removesuffix(r'(?:[，,][ \t]*)?'))
_LITERAL_RE = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"(?:[^"\\]|\\.)*"')

_PHASE_RE = re.compile(r"(?m)^【[0-9.]+[—–-][0-9.]+秒】")
_STATE_RE = re.compile(
    r"(?P<subject>[\u3400-\u9fffA-Za-z_][\u3400-\u9fffA-Za-z0-9_-]{0,63}?)"
    r"(?:眼睛清楚可见|眼睛不入画|口部入画时口型与对白一致|头部保持不动|"
    r"(?:视线持续停在|最终视线停在)[^。；;，,\n“”]+)。")


def _static_subjects(shot):
    # Generated visible-subject identities are explicit, not inferred from prose.
    # Old free-form lists remain readable but cannot grant a state-fold exemption.
    return set(re.findall(r'(?:^|[；;])\s*([\u3400-\u9fffA-Za-z_][\u3400-\u9fffA-Za-z0-9_-]{0,63})（[^）]+）',
                          str(shot.get('可见主体') or '')))


def _state_repetitions(text, subjects):
    """Fold only the same non-event assertion made in every timed phase.

    No general sentence dedupe: two blinks, two pushes or two spoken repetitions
    remain two events. Original phase evidence is retained for all consumers.
    """
    phases = list(_PHASE_RE.finditer(text))
    if len(phases) < 2 or not subjects: return []
    literals = [(m.start(), m.end()) for m in _LITERAL_RE.finditer(text)]
    rows = []
    for index, phase in enumerate(phases):
        stop = phases[index + 1].start() if index + 1 < len(phases) else len(text)
        matches = {}
        for match in _STATE_RE.finditer(text, phase.end(), stop):
            if match.group('subject') not in subjects: continue
            if (match.start() > phase.end() and text[match.start()-1] not in '。；;\n'
                    and not CLOCK_RE.fullmatch(text[phase.end():match.start()].strip())): continue
            if any(a < match.end() and match.start() < b for a, b in literals): continue
            matches.setdefault(match.group(), []).append(match)
        rows.append(matches)
    common = set.intersection(*(set(row) for row in rows))
    result = []
    for token in common:
        if any(len(row[token]) != 1 for row in rows): continue
        matches = [row[token][0] for row in rows]
        result.extend(matches[:-1] if '最终视线停在' in token else matches[1:])
    return result



class PictureTimingError(ValueError):
    pass


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replacement(token):
    punctuation = re.search(r'[，,][ \t]*$', token)
    suffix = punctuation.group() if punctuation else ''
    token = token[:-len(suffix)] if suffix else token
    if token.startswith(("仅在", "仅于")):
        return "一度" + suffix
    if token.endswith(("之后", "后")):
        return "随后" + suffix
    if token.endswith(("之前", "前")):
        return "此前" + suffix
    return ""


def clock_annotations(text, *, legacy=False):
    """Find clock spans outside quoted content; no source text is executed."""
    text = str(text or "")
    literals = [(match.start(), match.end()) for match in _LITERAL_RE.finditer(text)]
    return [match for match in (LEGACY_CLOCK_RE if legacy else CLOCK_RE).finditer(text)
            if not any(left < match.end() and match.start() < right for left, right in literals)]


def separate_picture_clock(text, *, compact=True, subjects=()):
    """Return natural prose with lossless clocks and narrowly repeated states."""
    text = str(text or "").strip()
    clocks = [(m, 'clock') for m in clock_annotations(text, legacy=not compact)]
    repeats = [(m, 'repeated_state') for m in _state_repetitions(text, subjects)] if compact else []
    spans = sorted(clocks + repeats, key=lambda item: item[0].start())
    # State clauses exclude clock notation, so their spans must be disjoint.
    pieces, annotations, cursor, length = [], [], 0, 0
    contract = COMPACT_CONTRACT if compact else CONTRACT
    for match, kind in spans:
        if match.start() < cursor:
            raise PictureTimingError('picture_timing_overlap: internal presentation spans overlap')
        between = text[cursor:match.start()]
        pieces.append(between); length += len(between)
        token = match.group(0)
        annotation = {"offset": length, "token": token}
        if contract == COMPACT_CONTRACT: annotation['kind'] = kind
        annotations.append(annotation)
        replacement = _replacement(token) if kind == 'clock' else ''
        pieces.append(replacement); length += len(replacement)
        cursor = match.end()
    pieces.append(text[cursor:])
    prose = "".join(pieces)
    if not prose.strip():
        raise PictureTimingError("picture_timing_empty: 画面内容仅有计时标记，缺少实际动作或对白")
    return prose, {"contract": contract, "text_sha256": _digest(prose),
                   "annotations": annotations}


def timed_picture(shot):
    """Reconstruct the internal validation view; reject stale or forged clocks."""
    text = str(shot.get("画面内容") or "")
    if FIELD not in shot:
        return text  # legacy raw canonicals retain their original interpretation
    record = shot[FIELD]
    if (not isinstance(record, dict) or set(record) != {"contract", "text_sha256", "annotations"}
            or record.get("contract") not in {CONTRACT, COMPACT_CONTRACT} or record.get("text_sha256") != _digest(text)):
        raise PictureTimingError("picture_timing_binding: 正文已改变或内部计时绑定无效，请更新对应计时依据")
    compact = record["contract"] == COMPACT_CONTRACT
    annotations = record["annotations"]
    if not isinstance(annotations, list):
        raise PictureTimingError("picture_timing_shape: annotations必须为数组")
    pieces, cursor = [], 0
    for item in annotations:
        if not isinstance(item, dict) or set(item) != ({"offset", "token", "kind"} if compact else {"offset", "token"}):
            raise PictureTimingError("picture_timing_shape: 计时记录字段与当前呈现合同不符")
        offset, token = item["offset"], item["token"]
        kind = item.get("kind", "clock")
        if (type(offset) is not int or not cursor <= offset <= len(text)
                or not isinstance(token, str) or kind not in {"clock", "repeated_state"}
                or not (CLOCK_RE if kind == "clock" else _STATE_RE).fullmatch(token)):
            raise PictureTimingError("picture_timing_token: 非法计时位置或夹入了动作文字")
        replacement = _replacement(token) if kind == "clock" else ""
        if text[offset:offset + len(replacement)] != replacement:
            raise PictureTimingError("picture_timing_binding: 计时对应的先后关系已改变")
        pieces.extend((text[cursor:offset], token))
        cursor = offset + len(replacement)
    pieces.append(text[cursor:])
    restored = "".join(pieces)
    rebuilt_text, rebuilt_record = separate_picture_clock(restored, compact=compact, subjects=_static_subjects(shot))
    if rebuilt_text != text or rebuilt_record != record:
        raise PictureTimingError("picture_timing_roundtrip: 计时记录与正文不构成唯一的可回读关系")
    return restored


def display_picture(shot):
    """One projection for formal cells and reading sheets, without numeric clocks."""
    if FIELD in shot:
        original = timed_picture(shot)
        # Historical bindings remain byte-faithful internally; their current
        # exported view still uses current punctuation/state presentation.
        if shot[FIELD]['contract'] == CONTRACT:
            return separate_picture_clock(original, subjects=_static_subjects(shot))[0]
        text = str(shot.get("画面内容") or "")
        if clock_annotations(text):
            raise PictureTimingError("picture_timing_display: 正文仍残留制作时间标记")
        return text
    return separate_picture_clock(shot.get("画面内容"), subjects=_static_subjects(shot))[0]


def separate_shot_clock(shot):
    result = copy.deepcopy(shot)
    original = timed_picture(result)
    result["画面内容"], result[FIELD] = separate_picture_clock(original, subjects=_static_subjects(result))
    if timed_picture(result) != original.strip():
        raise PictureTimingError("picture_timing_roundtrip: 计时分离改变了内部执行内容")
    return result


def timing_view(shot):
    result = dict(shot)
    result["画面内容"] = timed_picture(shot)
    result.pop(FIELD, None)
    return result
