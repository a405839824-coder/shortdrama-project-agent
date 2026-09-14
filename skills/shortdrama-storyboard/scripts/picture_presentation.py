"""Untimed picture prose with lossless, content-bound internal clock annotations.

Only production clock notation is separated. Quoted dialogue/written content is
left byte-faithful. The annotation store cannot insert actions or other prose;
each token is revalidated and its replacement is derived, never caller-chosen.
"""
from __future__ import annotations

import copy
import hashlib
import re

FIELD = "picture_timing"
CONTRACT = "picture-clock-annotations-v1"
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
    rf"(?:于|在)?(?:第|约|t\s*=\s*)?(?:{_RANGE}|{_POINT})(?:左右)?(?:之后|之前|后|前|时|处|内)?)"
)
_LITERAL_RE = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"(?:[^"\\]|\\.)*"')


class PictureTimingError(ValueError):
    pass


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replacement(token):
    if token.startswith(("仅在", "仅于")):
        return "一度"
    if token.endswith(("之后", "后")):
        return "随后"
    if token.endswith(("之前", "前")):
        return "此前"
    return ""


def clock_annotations(text):
    """Find clock spans outside quoted content; no source text is executed."""
    text = str(text or "")
    literals = [(match.start(), match.end()) for match in _LITERAL_RE.finditer(text)]
    return [match for match in CLOCK_RE.finditer(text)
            if not any(left < match.end() and match.start() < right for left, right in literals)]


def separate_picture_clock(text):
    """Return natural prose and a reversible clock-only record, not approval."""
    text = str(text or "").strip()
    pieces, annotations, cursor, length = [], [], 0, 0
    for match in clock_annotations(text):
        between = text[cursor:match.start()]
        pieces.append(between)
        length += len(between)
        token = match.group(0)
        annotations.append({"offset": length, "token": token})
        replacement = _replacement(token)
        pieces.append(replacement)
        length += len(replacement)
        cursor = match.end()
    pieces.append(text[cursor:])
    prose = "".join(pieces)
    if not prose.strip():
        raise PictureTimingError("picture_timing_empty: 画面内容仅有计时标记，缺少实际动作或对白")
    return prose, {"contract": CONTRACT, "text_sha256": _digest(prose),
                   "annotations": annotations}


def timed_picture(shot):
    """Reconstruct the internal validation view; reject stale or forged clocks."""
    text = str(shot.get("画面内容") or "")
    if FIELD not in shot:
        return text  # legacy raw canonicals retain their original interpretation
    record = shot[FIELD]
    if (not isinstance(record, dict) or set(record) != {"contract", "text_sha256", "annotations"}
            or record.get("contract") != CONTRACT or record.get("text_sha256") != _digest(text)):
        raise PictureTimingError("picture_timing_binding: 正文已改变或内部计时绑定无效，请更新对应计时依据")
    annotations = record["annotations"]
    if not isinstance(annotations, list):
        raise PictureTimingError("picture_timing_shape: annotations必须为数组")
    pieces, cursor = [], 0
    for item in annotations:
        if not isinstance(item, dict) or set(item) != {"offset", "token"}:
            raise PictureTimingError("picture_timing_shape: 计时记录只允许offset和token")
        offset, token = item["offset"], item["token"]
        if (type(offset) is not int or not cursor <= offset <= len(text)
                or not isinstance(token, str) or not CLOCK_RE.fullmatch(token)):
            raise PictureTimingError("picture_timing_token: 非法计时位置或夹入了动作文字")
        replacement = _replacement(token)
        if text[offset:offset + len(replacement)] != replacement:
            raise PictureTimingError("picture_timing_binding: 计时对应的先后关系已改变")
        pieces.extend((text[cursor:offset], token))
        cursor = offset + len(replacement)
    pieces.append(text[cursor:])
    restored = "".join(pieces)
    rebuilt_text, rebuilt_record = separate_picture_clock(restored)
    if rebuilt_text != text or rebuilt_record != record:
        raise PictureTimingError("picture_timing_roundtrip: 计时记录与正文不构成唯一的可回读关系")
    return restored


def display_picture(shot):
    """One projection for formal cells and reading sheets, without numeric clocks."""
    if FIELD in shot:
        timed_picture(shot)
        text = str(shot.get("画面内容") or "")
        if clock_annotations(text):
            raise PictureTimingError("picture_timing_display: 正文仍残留制作时间标记")
        return text
    return separate_picture_clock(shot.get("画面内容"))[0]


def separate_shot_clock(shot):
    result = copy.deepcopy(shot)
    original = timed_picture(result)
    result["画面内容"], result[FIELD] = separate_picture_clock(original)
    if timed_picture(result) != original.strip():
        raise PictureTimingError("picture_timing_roundtrip: 计时分离改变了内部执行内容")
    return result


def timing_view(shot):
    result = dict(shot)
    result["画面内容"] = timed_picture(shot)
    result.pop(FIELD, None)
    return result
