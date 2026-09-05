"""Person-bound THG-13 eye performance and THG-15 emphasis checks.

These checks consume authored canonical fields; they do not generate a second
performance track or add time for movements that happen alongside dialogue.
Unresolved identity/visibility is reported, not silently borrowed from another
person. Semantic source/visibility review remains an independent audit duty.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Sequence


STAGE_RE = re.compile(r"【(?P<start>\d+\.\d)—(?P<end>\d+\.\d)秒】(?P<body>.*?)(?=【\d+\.\d—\d+\.\d秒】|\Z)", re.S)
QUOTE_RE = re.compile(r'“([^”\n]+)”|‘([^’\n]+)’|"([^"\n]+)"|「([^」\n]+)」|『([^』\n]+)』')
DIALOGUE_RE = re.compile(r'(?:^|[；;。！？!?，,、】])\s*(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})说道[：:]?\s*[“"](?P<line>[^”"\n]+)[”"]')
FACE_SCALE_RE = re.compile(r"大特写|特写|近景|中近景|胸部以上|腰部以上")
FACE_RE = re.compile(r"面部|眼睛|眼神|眼睑|眼球|眼眸|双眼|侧脸|人物反应")
NON_PERSON_RE = re.compile(r"^(?:无|无人|无人物|不适用|书页|书上内容|桌面|账本|杯盏|茶杯|茶盏|信件|信封|木盒|礼匣|门窗|道具|场景|环境|远处行人|背景人群|普通人群|背景生态)$")
HIDDEN_RE = re.compile(r"(?:面部|眼睛|双眼|眼部)(?:全程|始终)?(?:不可见|不在画面|被遮挡|完全遮挡)|(?:仅|只)(?:露出|拍到|可见)?(?:背影|后脑|双手|手部)|背向机位|(?:全程|始终)(?:背影|背对镜头)")
VISIBLE_RE = re.compile(r"(?:面部|眼睛|双眼|眼部|侧脸)(?:清楚|清晰|完整)?可见")
FOCUS_RE = re.compile(r"(?:眼球|视线|目光|焦点).{0,14}(?:换焦|位移|焦点调整|微调)|(?:轻微|细微)(?:调整|转动)眼球")
EYELID_RE = re.compile(r"(?:上|下)?眼睑|眼皮|眼睫")
EYELID_CHANGE_RE = re.compile(r"(?:眼睑|眼皮|眼睫).{0,18}(?:略压|轻压|下压|压低|收紧|放松|舒展|微抬|抬起|低垂|松紧|恢复|变化|松开)|(?:略压|轻压|下压|压低|收紧|放松|微抬).{0,8}(?:眼睑|眼皮)")
GAZE_RE = re.compile(r"(?:眼球|视线|目光|眼神).{0,32}(?:从|由).{1,28}(?:移向|转向|落向|扫向|回到|看向|落在).+")
HEAD_RE = re.compile(r"(?:眼球|视线|目光).{0,20}先.{0,35}头部|头部.{0,20}(?:随后|再|轻微跟|保持不动|不跟随|不转|不动|不跟)")
END_GAZE_RE = re.compile(r"(?:视线|目光|眼神|眼球).{0,20}(?:落在|落向|停在|停留在|回到|看向|移向|转向|对准|聚焦于).+")
GAZE_HOLD_RE = re.compile(r"保持(?:原有|当前|既定|此时的)?(?:视线|目光|注视方向)|(?:视线|目光)(?:仍|继续)?保持(?:不变|稳定)")
GAZE_TARGET_RE = re.compile(r"(?:视线|目光|眼神|眼球)[^，,；;。！？!?\n]{0,36}?(?:落在|落向|停在|停留在|回到|看向|移向|转向|对准|聚焦于)(?P<target>[^，,；;。！？!?\n]+)")
GAZE_CHANGE_RE = re.compile(r"(?:视线|目光|眼神|眼球).{0,16}(?:移开|游移|扫动|偏移|转开|转动|改变|离开|转向|移向|换向|看向)|(?:转动|转移|移开|改变|转开|别开)(?:视线|目光|眼神)")
NO_BLINK_RE = re.compile(r"(?:本镜|全镜|全程|本段|台词期间|重音期间|关键词期间|可见期间|可见区间).{0,32}不眨眼|(?:因|由于).{1,20}不眨眼")
NO_BLINK_REASON_RE = re.compile(r"高压|压迫|震惊|认出|警觉|决定性|关键词|重音|尾音|情绪峰值")
EMPHASIS_NO_BLINK_RE = re.compile(r"(?:重音|关键词|尾音|[‘“][^’”]{1,24}[’”]).{0,24}不眨眼|不眨眼.{0,24}(?:重音|关键词|尾音|[‘“][^’”]{1,24}[’”])")
BLINK_RE = re.compile(r"(?:自然)?(?:短|轻)?眨(?:眼)?")
BLINK_COUNT_RE = re.compile(r"(?:(?P<before>[0-9零一二两三四五]+)\s*次(?:自然)?(?:短|轻)?眨(?:眼)?|(?:自然)?(?:短|轻)?眨(?:眼)?\s*(?P<after>[0-9零一二两三四五]+)\s*次)")
BLINK_RANGE_RE = re.compile(r"(?:眨眼\s*)?(\d+)\s*[-—–~～至到]\s*(\d+)\s*次(?:眨眼)?")
BLINK_TIME_RE = re.compile(r"(?:第|在|于)?(?P<time>\d+(?:\.\d+)?)\s*(?:秒|s)(?![—–-])")
BLINK_TRIGGER_RE = re.compile(r"(?:开口|说话|对白|视线落稳|换焦|听完|台词结束|反应|情绪回收|换气|句间|镜尾|呼吸).{0,18}(?:前|后|时|期间|阶段|反应段)")
STATIC_OPEN_RE = re.compile(r"双眼自然睁开.{0,32}(?:不处于|不生成|避开|禁止|无)(?:半眨眼|半闭眼)|(?:不处于|不生成|避开|禁止)(?:半眨眼|半闭眼).{0,32}双眼自然睁开")
PLOT_CLOSED_RE = re.compile(r"(?:原文|剧情|表演)(?:明确|要求|需要).{0,20}(?:闭眼|合眼|睡着|昏迷)")
NEGATIVE_RE = re.compile(r"(?:禁止|不得|避免|不能|不可|不要|无需|没有|不再|并未|未曾|不|无|未)(?:让|安排|发生|出现|进行|做|有)?[^，,；;。！？!?、\n]{0,7}$")
COLLECTIVE_RE = re.compile(r"^(?:镜尾|此时|随后|接着|同时|最后)?(?:三人|两人|二人|多人|众人|所有人|双方|各人|每人)")
BODY_PART_PATTERN = r"面部|眼睛|双眼|眼部|侧脸|上半身|下半身|全身|双手|左手|右手|手部|手腕|手指|食指|肩缘|肩颈|肩背|肩部|脖颈|腰部|胸部|后脑|背影|轮廓"


@dataclass(frozen=True)
class Clause:
    text: str
    start: float
    end: float
    owner: str | None


def _positive(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Negation is local: it never swallows a later Chinese clause."""
    return [match for match in pattern.finditer(text) if not NEGATIVE_RE.search(text[max(0, match.start() - 18):match.start()])]


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _rows(value: Sequence[tuple[str, str] | dict[str, Any]]) -> list[tuple[str, str]]:
    result = []
    for row in value:
        if isinstance(row, dict):
            result.append((str(row.get("speaker") or row.get("说话人") or ""), str(row.get("text") or row.get("line") or row.get("对白") or "")))
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            result.append((str(row[0]), str(row[1])))
    return [(speaker.strip(), line) for speaker, line in result if speaker.strip() and line]


def emphasis_bindings(
    emphasis: Any,
    dialogue_rows: Sequence[tuple[str, str] | dict[str, Any]],
    *,
    speaker: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bind every quoted emphasis word to one real speaker/utterance/offset.

    A one-speaker shot can omit the speaker label. A multi-speaker field uses
    e.g. ``甲：“回来”；乙：“别走”``. An optional ``第2句`` disambiguates repeated
    utterances. Matching never joins adjacent utterances into an invented word.
    Returned offsets are half-open Python Unicode character offsets.
    """
    text = str(emphasis or "").strip()
    rows = _rows(dialogue_rows)
    speakers = _dedupe(name for name, _ in rows)
    if not text:
        return [], ["重音不能为空"]
    quotes = list(QUOTE_RE.finditer(text))
    if not quotes:
        return [], ["重音必须用引号明确标出本镜实际对白中的字或词"]
    faults: list[str] = []
    bindings: list[dict[str, Any]] = []
    owner = speaker or (speakers[0] if len(speakers) == 1 else None)
    if speaker is not None and speaker not in speakers:
        faults.append(f"重音说话人{speaker}在本镜没有实际发言")
    utterance_selector: int | None = None
    cursor = 0
    name_re = re.compile("|".join(re.escape(name) for name in sorted(speakers, key=len, reverse=True))) if speakers else None
    for quote in quotes:
        lead = text[cursor:quote.start()]
        cursor = quote.end()
        # An explicit name must be a subject/label, never a dialogue object.
        named = [] if name_re is None else [m for m in name_re.finditer(lead) if re.search(r"(?:^|[；;，,、\n])\s*$", lead[:m.start()]) and re.match(r"(?:的)?(?:重音|第\d+句|\s*[：:]|\s*$)", lead[m.end():])]
        explicit_label = re.search(r"(?:^|[；;，,、\n])\s*([\u3400-\u9fffA-Za-z·]{1,12}?)(?:的重音|第\d+句)?\s*[：:]\s*$", lead)
        if explicit_label and explicit_label.group(1) not in speakers and explicit_label.group(1) not in {"重音", "明确落在", "落在"}:
            owner = explicit_label.group(1)
            utterance_selector = None
        if named:
            owner = named[-1].group()
            utterance_selector = None
        selector = re.search(r"第(\d+)句", lead)
        if selector:
            utterance_selector = int(selector.group(1))
        fragment = next(group for group in quote.groups() if group is not None)
        if not owner:
            faults.append(f"重音词“{fragment}”未绑定对应说话人；多人发言不能共用未署名重音")
            continue
        own_rows = [(index, line) for index, (name, line) in enumerate(rows) if name == owner]
        if utterance_selector is not None:
            own_rows = own_rows[utterance_selector - 1:utterance_selector] if utterance_selector > 0 else []
        occurrences = []
        for row_index, line in own_rows:
            position = line.find(fragment)
            while position >= 0:
                occurrences.append((row_index, position, position + len(fragment)))
                position = line.find(fragment, position + 1)
        if not occurrences:
            faults.append(f"{owner}的重音词“{fragment}”未逐字出现在其本镜对应实际发言中")
            continue
        if len({row_index for row_index, _, _ in occurrences}) > 1:
            faults.append(f"{owner}的重音词“{fragment}”对应多次发言，需标明第几句")
            continue
        bindings.append({"speaker": owner, "word": fragment, "utterance_index": occurrences[0][0], "character_ranges": [[start, end] for _, start, end in occurrences]})
    remainder = QUOTE_RE.sub("", text)
    if any(char in remainder for char in '“”‘’"「」『』'):
        faults.append("重音引号必须成对匹配，不能留下未绑定字词")
    return bindings, _dedupe(faults)


def emphasis_binding_faults(emphasis: Any, dialogue_rows: Sequence[tuple[str, str] | dict[str, Any]], *, speaker: str | None = None) -> list[str]:
    return emphasis_bindings(emphasis, dialogue_rows, speaker=speaker)[1]


def _people(visible: Any, explicit: Sequence[str] | None) -> tuple[list[str], dict[str, str]]:
    raw = str(visible or "")
    tokens = [part.strip() for part in re.split(r"[、，,；;和与及+＋]", raw) if part.strip()]
    names = _dedupe(str(name).strip() for name in explicit if str(name).strip()) if explicit else []
    descriptions: dict[str, str] = {}
    owner: str | None = None
    for token in tokens:
        name = re.sub(r"^[\s]*(?:画面|前景|中景|后景|左侧|右侧|中央|左边|右边)[:：\s]*", "", token)
        if re.match(r"^(?:" + BODY_PART_PATTERN + r")(?:清楚|清晰|完整|自然|可见|不可见|被遮挡|全程|始终|仅在|食指|与|和|及|、|$)", name):
            # A body-part enumeration continues only the immediately preceding
            # resolved person, never all named people in the shot.
            if owner is not None:
                descriptions[owner] = descriptions.get(owner, owner) + "、" + token
            continue
        name = re.split(r"[（(]|(?:的)?(?:" + BODY_PART_PATTERN + r"|仅|只|背向|背对)", name, maxsplit=1)[0].strip()
        name = re.sub(r"(?:等|共)?[一二两三四五六七八九十\d]+(?:名|位)?(?:人物|人)$", "", name).strip()
        if not name or NON_PERSON_RE.fullmatch(name) or COLLECTIVE_RE.match(name):
            owner = None
            continue
        if re.fullmatch(r"[\u3400-\u9fffA-Za-z·]{1,12}", name) and (not explicit or name in names):
            names.append(name)
            owner = name
            descriptions[name] = (descriptions[name] + "、" + token) if name in descriptions else token
        else:
            owner = None
    for name in names:
        descriptions.setdefault(name, name)
    return _dedupe(names), descriptions


def _subject(text: str, names: Sequence[str]) -> str | None:
    prefix = r"^(?:(?:镜尾|此时|随后|接着|同时|最后|最终|开口前|听完后|换焦后|镜头开始时|起始时|前景|后景|中景)[，,：:\s]*)*"
    for name in sorted(names, key=len, reverse=True):
        if re.match(prefix + re.escape(name), text):
            # "甲观察乙眼球换焦" describes 乙's eyes, not 甲's.
            # The first possessive eye component also keeps an ordinary gaze
            # path (甲视线从袖口移向乙眼睛) assigned to its real owner 甲.
            holders = [(match.start(), person) for person in names for match in re.finditer(re.escape(person) + r"(?:的)?(?:眼球|视线|目光|眼神|双眼|眼睛|上眼睑|下眼睑|眼睑)", text)]
            if holders:
                return min(holders)[1]
            return name
    return None


def _clauses(text: Any, names: Sequence[str], duration: float) -> list[Clause]:
    value = str(text or "")
    # Speech can mention eyes, negation or another actor. It is never a visual instruction.
    value = re.sub(r'(说道[：:]?\s*)[“"][^”"\n]*[”"]', r"\1〈对白〉", value)
    stages = [(float(match.group("start")), float(match.group("end")), match.group("body")) for match in STAGE_RE.finditer(value)]
    if not stages:
        stages = [(0.0, duration, value)]
    result = []
    for start, end, body in stages:
        owner: str | None = names[0] if len(names) == 1 else None
        for part in re.split(r"[，,；;。！？!?\n]+", body):
            part = part.strip()
            if not part:
                continue
            subject = _subject(part, names)
            if subject is not None:
                owner = subject
            elif COLLECTIVE_RE.match(part):
                owner = "__all__"
            result.append(Clause(part, start, end, owner))
    return result


def _merge(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for start, end in sorted(set(intervals)):
        if result and start <= result[-1][1] + 1e-9:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _visibility(name: str, descriptor: str, shot: dict[str, Any], clauses: list[Clause], duration: float) -> tuple[list[tuple[float, float]], list[str]]:
    shot_type = str(shot.get("镜头类型") or "")
    own = [clause for clause in clauses if clause.owner == name]
    own_text = descriptor + "；" + "；".join(clause.text for clause in own)
    window_re = re.compile(r"(?:^|[；;，,。（(\n])(?:" + re.escape(name) + r"(?:的)?)?(?:面部|眼睛|双眼|眼部)(?:清楚|清晰)?仅在\s*(\d+(?:\.\d+)?)\s*[—–-]\s*(\d+(?:\.\d+)?)\s*秒(?:清楚|清晰|完整)?(?:可见|露出)")
    windows = list(window_re.finditer(own_text))
    faults: list[str] = []
    if windows:
        intervals = []
        for window in windows:
            start, end = float(window.group(1)), float(window.group(2))
            if not (0 <= start < end <= duration):
                faults.append(f"{name}眼部可见区间{start:g}—{end:g}秒越界或为空")
            else:
                intervals.append((start, end))
        return _merge(intervals), faults
    default_visible = bool(FACE_SCALE_RE.search(shot_type) or FACE_RE.search(descriptor))
    if re.search(r"手部特写|书页|POV书|第一视角看书|远景", shot_type) and not VISIBLE_RE.search(descriptor):
        default_visible = False
    if (re.search(r"背影|后脑|仅手|只拍手|肩缘|肩背|肩部", descriptor) and not FACE_RE.search(descriptor)) or _positive(HIDDEN_RE, descriptor):
        default_visible = False
    intervals = []
    stages = sorted({(clause.start, clause.end) for clause in clauses}) or [(0.0, duration)]
    state = default_visible
    for start, end in stages:
        body = "；".join(clause.text for clause in own if (clause.start, clause.end) == (start, end))
        hidden = bool(_positive(HIDDEN_RE, body))
        visible = bool(_positive(VISIBLE_RE, body))
        if hidden and visible:
            faults.append(f"{name}在{start:g}—{end:g}秒同时声明眼部可见与不可见，需给出确切可见区间")
        elif hidden:
            state = False
        elif visible:
            state = True
        if state:
            intervals.append((start, end))
    return _merge(intervals), faults


def _count(token: str) -> int:
    values = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    return int(token) if token.isdigit() else values.get(token, -1)


def _blink_events(clauses: Sequence[Clause]) -> tuple[list[dict[str, Any]], list[str]]:
    events = []
    faults = []
    for clause in clauses:
        matches = _positive(BLINK_RE, clause.text)
        # "不眨眼" and "禁止无依据全程不眨眼" are decisions/limits, not blinks.
        if not matches:
            continue
        counts = list(BLINK_COUNT_RE.finditer(clause.text))
        ranges = list(BLINK_RANGE_RE.finditer(clause.text))
        if not counts and not ranges:
            faults.append("眨眼未写明确次数")
            count = 0
        elif ranges:
            faults.append("眨眼次数必须确定，不得填写候选范围")
            count = max(int(match.group(2)) for match in ranges)
        else:
            count = sum(_count(match.group("before") or match.group("after")) for match in counts)
        times = [float(match.group("time")) for match in BLINK_TIME_RE.finditer(clause.text)]
        if not times and not BLINK_TRIGGER_RE.search(clause.text):
            faults.append("眨眼未绑定数值时点或开口前、换焦后、听后反应等具体时机")
        if any(not clause.start <= moment <= clause.end for moment in times):
            faults.append("眨眼数值时点不在其所属时间阶段内")
        if count > 0 and times and count != len(times):
            faults.append("眨眼次数与明确数值时点数量不一致")
        events.append({"count": count, "times": times, "start": clause.start, "end": clause.end, "text": clause.text})
    return events, _dedupe(faults)


def _identity_faults(visible: str, names: Sequence[str]) -> list[str]:
    """A cast-count phrase is not itself a resolved character identity."""
    values = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    declarations = []
    for clause in re.split(r"[；;，,。\n]", visible):
        if re.search(r"背景|远处|群演|普通人群", clause):
            continue
        for match in re.finditer(r"([一二两三四五六七八九十]|\d+)(?:名|位)?(?:人物|人)", clause):
            token = match.group(1)
            declarations.append(int(token) if token.isdigit() else values[token])
    if declarations and any(count != len(names) for count in declarations):
        return [f"可见主体声明{max(declarations)}人，但仅绑定{len(names)}个具体人物身份；不得用‘等几人’代替逐人身份"]
    if re.search(r"等人|等多人|若干人|多名人物", visible):
        return ["可见主体包含未逐一绑定身份的人物集合，不能完整执行逐人眼部检查"]
    return []


def _no_blink_conflicts(name: str, clauses: Sequence[Clause], events: Sequence[dict[str, Any]]) -> list[str]:
    positive = [event for event in events if event["count"] > 0]
    if not positive:
        return []
    faults = []
    for clause in clauses:
        if re.search(r"禁止|不得|避免|不要求", clause.text):
            continue
        match = re.search(r"(?:本镜|全镜|全程)([^，,；;。]{0,32})不眨眼", clause.text)
        if match and not re.search(r"重音|关键词|尾音|期间|阶段|本段", match.group(1)):
            faults.append(f"{name}同时声明本镜/全程不眨眼与实际眨眼事件，决策相互矛盾")
        if re.search(r"本段[^，,；;。]{0,24}不眨眼", clause.text):
            overlapping = [event for event in positive if any(clause.start <= moment < clause.end for moment in event["times"]) or (not event["times"] and event["start"] < clause.end and event["end"] > clause.start)]
            if overlapping:
                faults.append(f"{name}在{clause.start:g}—{clause.end:g}秒声明本段不眨眼却安排同段眨眼")
    return _dedupe(faults)


def _stagger_faults(all_events: dict[str, list[dict[str, Any]]], clauses: Sequence[Clause], picture: str) -> list[str]:
    """Actual blink synchronization applies even to sub-two-second exposure."""
    faults = []
    if any(_positive(re.compile(r"(?:同步|同时|同频)眨眼"), clause.text) for clause in clauses):
        faults.append("多人出现同步、同时或同频眨眼")
    blinking = {name: events for name, events in all_events.items() if any(event["count"] for event in events)}
    names = list(blinking)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            left_times = [moment for event in blinking[left] for moment in event["times"]]
            right_times = [moment for event in blinking[right] for moment in event["times"]]
            if any(abs(a - b) < 1e-9 for a in left_times for b in right_times):
                faults.append(f"{left}与{right}眨眼时点相同，‘错开’文字不能覆盖实际同步")
            elif not (left_times and right_times) and not re.search(r"眨眼.{0,24}(?:错开|不同时)|(?:错开|不同时).{0,24}眨眼|晚半拍.{0,24}眨", picture):
                faults.append(f"{left}与{right}未给出眨眼错峰顺序或不同数值时点")
    return faults


def _unambiguous_gaze_hold(clauses: Sequence[Clause], *, name: str, names: Sequence[str], last_stage_start: float) -> bool:
    """Resolve a same-shot hold without re-authoring the same target each phase.

    This narrow inference is only for one resolved person. A concrete latest
    target must precede the final hold, and an unresolved subsequent gaze change
    invalidates it. It never borrows another shot, another person or a choice of
    targets. Micro focus/eyelid movement alone does not change a gaze endpoint.
    """
    if len(names) != 1:
        return False
    target: str | None = None
    final_hold = False
    for clause in clauses:
        matches = _positive(GAZE_TARGET_RE, clause.text)
        changed = bool(_positive(GAZE_CHANGE_RE, clause.text))
        if matches:
            for match in matches:
                prefix = clause.text[:match.start()].strip()
                prefix = re.sub(r"^(?:镜尾|此时|随后|接着|最后|最终)", "", prefix)
                own_prefix = re.fullmatch(r"(?:(?:" + re.escape(name) + r"|她|他|该人物|该角色)(?:的)?)?(?:(?:先|随后|此时|轻微|微微|缓慢|自然|转动|移动|调整|再次|重新|短暂))*", prefix)
                value = match.group("target").strip()
                uncertain = bool(re.search(r"或|二选一|不确定|不明确|某处|某个|别处|原处|原目标|既定目标|另一处|游移", value))
                multiple = bool(re.search(r"和|与|及|、", value)) and not re.search(r"之间|交界处|交点", value)
                target = value if own_prefix and value and not uncertain and not multiple else None
            final_hold = False
        elif changed:
            target = None
            final_hold = False
        if clause.start >= last_stage_start and _positive(GAZE_HOLD_RE, clause.text):
            final_hold = target is not None
    return final_hold and target is not None


def eye_contract_faults(
    shot: dict[str, Any],
    *,
    duration: float,
    strict: bool = True,
    visible_names: Sequence[str] | None = None,
    dialogue_rows: Sequence[tuple[str, str] | dict[str, Any]] | None = None,
) -> list[str]:
    """Validate each identified person's actual continuous visible intervals.

    ``visible_names`` may supply already-authoritatively-resolved identities;
    otherwise identities are read from 可见主体. A bounded visibility exception
    can be authored as ``甲眼睛仅在0.0—1.5秒清楚可见``. An unbounded claim of
    "可见1秒" cannot waive a four-second visible-face contract.
    """
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return ["眼部检查需要有效正数镜头时长"]
    visible = str(shot.get("可见主体") or "")
    names, descriptions = _people(visible, visible_names)
    identity_faults = _identity_faults(visible, names)
    if not names:
        if duration >= 2 and FACE_SCALE_RE.search(str(shot.get("镜头类型") or "")) and _positive(re.compile(r"人|面部|眼睛|侧脸"), visible):
            return _dedupe(identity_faults + ["可见人脸未绑定具体人物身份，不能执行逐人眼部检查"])
        return identity_faults
    picture = str(shot.get("画面内容") or "")
    clauses = _clauses(picture, names, duration)
    start_clauses = _clauses(shot.get("起始状态"), names, duration)
    raw_rows = dialogue_rows if dialogue_rows is not None else [(match.group("speaker"), match.group("line")) for match in DIALOGUE_RE.finditer(picture)]
    speakers = {name for name, _ in _rows(raw_rows)}
    limits = str(shot.get("生成限制") or "")
    faults = list(identity_faults)
    all_events: dict[str, list[dict[str, Any]]] = {}
    eligible_people = []
    visible_people = []
    for name in names:
        intervals, visibility_faults = _visibility(name, descriptions.get(name, name), shot, clauses, duration)
        faults.extend(visibility_faults)
        own_all = [clause for clause in clauses if clause.owner == name]
        if not intervals:
            if any(_positive(FOCUS_RE, clause.text) or _positive(EYELID_CHANGE_RE, clause.text) or _positive(BLINK_RE, clause.text) for clause in own_all):
                faults.append(f"{name}眼部不可见，不能安排可见眼球、眼睑或眨眼表演")
            continue
        visible_people.append(name)
        all_events[name], _ = _blink_events(own_all)
        faults.extend(_no_blink_conflicts(name, own_all, all_events[name]))
        for clause in own_all:
            if not any(clause.start < stop and clause.end > start for start, stop in intervals):
                if _positive(FOCUS_RE, clause.text) or _positive(EYELID_CHANGE_RE, clause.text) or _positive(BLINK_RE, clause.text):
                    faults.append(f"{name}在{clause.start:g}—{clause.end:g}秒眼部不可见，却安排了可见眼部表演")
            if _positive(BLINK_RE, clause.text):
                times = [float(match.group("time")) for match in BLINK_TIME_RE.finditer(clause.text)]
                if any(not any(start <= moment <= stop for start, stop in intervals) for moment in times):
                    faults.append(f"{name}眨眼时点不在本人物眼部可见区间内")
        for begin, end in intervals:
            if end - begin < 2.0 - 1e-9:
                short_own = [clause for clause in own_all if clause.start < end and clause.end > begin]
                events, event_faults = _blink_events(short_own)
                faults.extend(f"{name}[{begin:g}—{end:g}秒]{fault}" for fault in event_faults)
                if sum(event["count"] for event in events) > 1:
                    faults.append(f"{name}[{begin:g}—{end:g}秒]短可见区间不应机械安排多次眨眼")
                continue
            eligible_people.append(name)
            label = f"{name}[{begin:g}—{end:g}秒]"
            own = [clause for clause in own_all if clause.start < end and clause.end > begin]
            body = "；".join(clause.text for clause in own)
            # Join only this actor's clauses. Other people and spoken words cannot
            # contribute any eye-chain component or no-blink exemption.
            focus_ok = bool(_positive(FOCUS_RE, body))
            eyelid_ok = bool(_positive(EYELID_CHANGE_RE, body))
            if not _positive(GAZE_RE, body):
                faults.append(f"{label}缺视线从起点移向具体目标的换焦路径")
            if not _positive(HEAD_RE, body):
                faults.append(f"{label}缺眼球先动、头部随后轻跟或明确不跟的关系")
            if not focus_ok:
                faults.append(f"{label}缺轻微眼球换焦、位移或焦点调整")
            if not eyelid_ok:
                faults.append(f"{label}缺上/下眼睑松紧变化")
            last_stage_start = max((clause.start for clause in clauses if clause.start < end and clause.end > begin), default=begin)
            tail = "；".join(clause.text for clause in own if clause.start >= last_stage_start)
            if not _positive(END_GAZE_RE, tail) and not _unambiguous_gaze_hold(own, name=name, names=names, last_stage_start=last_stage_start):
                faults.append(f"{label}最后可见阶段未写最终视线落点")
            events, event_faults = _blink_events(own)
            faults.extend(f"{label}{fault}" for fault in event_faults)
            no_blink_clauses = [clause for clause in own if NO_BLINK_RE.search(clause.text) and not re.search(r"禁止|不得|避免|不要求", clause.text)]
            intentional = bool(no_blink_clauses)
            if not events and not intentional:
                faults.append(f"{label}缺逐人眨眼或有理由不眨眼决策")
            if intentional:
                reason_parts = []
                for decision in no_blink_clauses:
                    position = own.index(decision)
                    reason_parts.append(decision.text)
                    if position > 0 and (own[position - 1].start, own[position - 1].end) == (decision.start, decision.end):
                        previous = own[position - 1]
                        if re.search(r"(?:因|由于|因而|因此|为了|为保持).{0,32}(?:认出|高压|震惊|警觉|重音|关键词|压迫)", previous.text):
                            reason_parts.append(previous.text)
                reasons = "；".join(reason_parts)
                if not NO_BLINK_REASON_RE.search(reasons):
                    faults.append(f"{label}不眨眼缺本人物高压、震惊、认出、警觉或重音等剧情理由")
                if not (focus_ok and eyelid_ok and any(_positive(re.compile(r"呼吸|气息|吸气|呼气"), clause.text) for clause in own)):
                    faults.append(f"{label}不眨眼缺焦点调整、眼睑变化和自然呼吸替代动态")
            if name in speakers and not any(EMPHASIS_NO_BLINK_RE.search(clause.text) for clause in own):
                faults.append(f"{label}说话者关键词、重音或决定性尾音期间未明确不眨眼")
            high = sum(event["count"] for event in events)
            maximum = 1 if end - begin <= 5 else (2 if end - begin <= 10 else None)
            if maximum is not None and high > maximum:
                faults.append(f"{label}可见区间眨眼上限应为{maximum}次，当前逐事件累计{high}次")
            if strict and begin == 0:
                own_start = "；".join(clause.text for clause in start_clauses if clause.owner == name)
                collective_start = "；".join(clause.text for clause in start_clauses if clause.owner == "__all__")
                # Static collective open-eye declarations are safe; dynamic
                # collective clauses never satisfy individual performance.
                static = own_start + "；" + collective_start
                if not (STATIC_OPEN_RE.search(static) or PLOT_CLOSED_RE.search(static)):
                    faults.append(f"{label}起始状态未声明自然睁眼且避开半眨，亦无源文明确闭眼依据")
    if len(set(visible_people)) > 1:
        faults.extend(_stagger_faults(all_events, clauses, picture))
    if not eligible_people:
        return _dedupe(faults)
    for pattern, message in [
        (r"眼神自然聚焦", "生成限制缺‘眼神自然聚焦’"),
        (r"眼球.{0,12}(?:换焦|位移|焦点调整)", "生成限制缺眼球换焦/位移约束"),
        (r"眼睑.{0,12}(?:松紧|变化)", "生成限制缺眼睑松紧变化约束"),
        (r"禁止无依据全程不眨眼", "生成限制缺无依据全程不眨眼禁项"),
        (r"禁止.{0,12}(?:僵直瞪视|目光僵死)", "生成限制缺僵直瞪视禁项"),
        (r"禁止.{0,12}高频机械眨眼", "生成限制缺高频机械眨眼禁项"),
    ]:
        if not re.search(pattern, limits):
            faults.append(message)
    if len(set(visible_people)) > 1:
        if not re.search(r"禁止.{0,16}(?:同步|同时|同频)眨眼", limits):
            faults.append("生成限制缺多人同步眨眼禁项")
    return _dedupe(faults)
