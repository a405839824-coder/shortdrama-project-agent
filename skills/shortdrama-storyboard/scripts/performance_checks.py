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
from picture_presentation import timing_view, PictureTimingError
from visible_entities import resolve_visible_subjects, visible_subject_tokens as visible_subject_tokens


STAGE_RE = re.compile(r"【(?P<start>\d+\.\d)—(?P<end>\d+\.\d)秒】(?P<body>.*?)(?=【\d+\.\d—\d+\.\d秒】|\Z)", re.S)
QUOTE_RE = re.compile(r'“([^”\n]+)”|‘([^’\n]+)’|"([^"\n]+)"|「([^」\n]+)」|『([^』\n]+)』')
from voice_sequence import INTEGRATED_DIALOGUE_RE as DIALOGUE_RE
FACE_SCALE_RE = re.compile(r"大特写|特写|近景|中近景|胸部以上|腰部以上")
FACE_RE = re.compile(r"面部|眼睛|眼神|眼睑|眼球|眼眸|双眼|侧脸|人物反应")
HIDDEN_RE = re.compile(r"(?:面部|眼睛|双眼|眼部)(?:(?:和|与)(?:面部|眼睛|双眼|眼部))?(?:全程|始终|均|都)?(?:不可见|不入画|不在画面|被遮挡|完全遮挡)|(?:仅|只)(?:露出|拍到|可见)?(?:背影|侧背|后脑|双手|手部|胸腹衣襟)|背向机位|(?:全程|始终)(?:背影|背对镜头)")
VISIBLE_RE = re.compile(r"(?:面部|眼睛|双眼|眼部|侧脸)(?:清楚|清晰|完整)?可见")
FOCUS_RE = re.compile(r"(?:眼球|视线|目光|焦点).{0,14}(?:换焦|位移|焦点调整|微调)|(?:轻微|细微)(?:调整|转动)眼球")
EYELID_RE = re.compile(r"(?:上|下)?眼睑|眼皮|眼睫")
EYELID_CHANGE_RE = re.compile(r"(?:眼睑|眼皮|眼睫)[^，,；;。！？!?\n]{0,18}?(?P<verb>略压|轻压|下压|压低|收紧|放松|舒展|微抬|抬起|低垂|松紧|恢复|变化|松开|由紧转松|由松转紧|放平|微提|轻提|轻收|略收)|(?P<preverb>略压|轻压|下压|压低|收紧|放松|微抬)[^，,；;。！？!?\n]{0,8}(?:眼睑|眼皮)")
GAZE_RE = re.compile(r"(?:眼球|视线|目光|眼神).{0,32}(?:从|由).{1,28}(?:移向|转向|落向|扫向|回到|看向|落在).+")
HEAD_RE = re.compile(r"(?:眼球|视线|目光).{0,20}先.{0,35}头部|头部.{0,20}(?:随后|再|轻微跟|保持不动|保持当前方向|不跟随|不转|不动|不跟)")
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
BODY_PART_PATTERN = r"面部|眼睛|双眼|眼部|侧脸|上半身|下半身|全身|双手|左手|右手|手部|手腕|手指|食指|肩缘|肩颈|肩背|肩部|脖颈|腰部|胸部|胸腹|后脑|背影|侧背|轮廓"
FOCUS_ACTION_RE = re.compile(r"(?:眼球|视线|目光|焦点)[^，,；;。！？!?\n]{0,48}?(?P<verb>换焦|位移|焦点调整|微调)|(?P<preverb>(?:轻微|细微)(?:调整|转动))眼球")
GAZE_PATH_RE = re.compile(r"(?:眼球|视线|目光|眼神)(?P<lead>[^，,；;。！？!?\n]{0,16}?)(?:从|由)(?P<origin>[^，,；;。！？!?\n]{1,36}?)(?P<verb>移向|转向|落向|扫向|回到|看向|落在|换焦至|换焦到|移焦至|移焦到)(?P<target>[^，,；;。！？!?\n]+)")
ACTION_NEGATION_RE = re.compile(r"(?:禁止|不得|避免|不能|不可|不要|无需|没有|不再|并未|未曾|并非|并不|不是|不曾|未能|尚未|从未)(?:[^，,；;。！？!?\n]{0,16})$|(?:不|未)(?:再|曾|会|要|做|进行|发生|出现|轻微|微微|稍|稍稍|略微|自然|缓慢|逐渐|随后|轻)*$")
TIME_SUBJECT_PREFIX = r"(?:(?:第|在|于)?\d+(?:\.\d+)?\s*(?:秒|s)(?:时|后|起)?[，,：:\s]*)"


@dataclass(frozen=True)
class Clause:
    text: str
    start: float
    end: float
    owner: str | None


def _positive(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Negation is local: it never swallows a later Chinese clause."""
    return [match for match in pattern.finditer(text) if not NEGATIVE_RE.search(text[max(0, match.start() - 18):match.start()])]


def _action_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Check the actual predicate as well as a negation before its body part.

    This is deliberately separate from state matching: ``眼睛不可见`` and
    ``头部不跟`` are positive descriptions of legitimate states. Negated
    movement cannot provide positive action evidence. Punctuation bounds scope.
    """
    result = []
    for match in _positive(pattern, text):
        verb = match.start('verb') if match.groupdict().get('verb') else match.start('preverb')
        if not ACTION_NEGATION_RE.search(text[max(0, verb - 40):verb]):
            result.append(match)
    return result


def _gaze_paths(text: str) -> list[re.Match[str]]:
    result = []
    for match in _action_matches(GAZE_PATH_RE, text):
        # Negation before 从 can otherwise be arbitrarily far from 移向.
        if ACTION_NEGATION_RE.search(match.group('lead')):
            continue
        origin = re.sub(r"(?:轻微|微微|缓慢|自然|逐渐|先|再|稍稍|稍)+$", "", match.group('origin')).strip()
        target = match.group('target').strip()
        # A route from a point to that same point is not a movement. Do not
        # invent a different point, or treat a missing endpoint as resolved.
        if not origin or not target or origin == target:
            continue
        if re.search(r"^(?:原处|原位置|同一位置|同一点|原目标)$", target):
            continue
        result.append(match)
    return result


def _eye_clauses(text: Any, names: Sequence[str], duration: float) -> list[Clause]:
    """Quoted speech, thoughts and reported instructions are not eye actions."""
    value = QUOTE_RE.sub(lambda m: '〈引文〉' + (m.group()[-2:-1] if m.group()[-2:-1] in '。！？!?' else ''), str(text or ''))
    # A numeric location is an explicit target qualifier, not a second subject.
    # Remove only the conventional coordinate prefix before clause splitting.
    value = re.sub(r'位于x=[+-]?\d+(?:\.\d+)?[、，,\s]*y=[+-]?\d+(?:\.\d+)?米的', '', value)
    return _clauses(value, names, duration)


def _gaze_target_label(value: str) -> str | None:
    value = re.sub(r'方向$', '', value.strip())
    if (not value or re.search(r'或|某处|某个|原处|原目标|同一点|未知|待定|〈|〉|“|”', value)
            or not re.fullmatch(r'[\u3400-\u9fffA-Za-z0-9·_-]{1,48}', value)):
        return None
    return value


def _sequential_eye_path(clauses: Sequence[Clause]) -> tuple[bool, bool]:
    """Recognize two explicit endpoints in a same-actor ordered action.

    'Eyes first to the seal; then refocus on the list' and a head-led down/up
    movement are complete paths without a literal 从. A single generic 微小换焦
    or a final target alone is not. Callers pass only this actor's clauses.
    """
    move = re.compile(r'(?:眼球|视线|目光|眼神)[^，,；;。！？!?\n]{0,24}?(?P<verb>移向|落向|换焦到|换焦至|移焦到|移焦至)(?P<target>[^，,；;。！？!?\n]+)')
    return_head = re.compile(r'^(?:抬头|低头|转头)后(?:重新|再次)?(?P<verb>落在|落向|回到)(?P<target>[^，,；;。！？!?\n]+)')
    first = None
    first_stage = None
    head_led = False
    for clause in clauses:
        if first_stage != (clause.start, clause.end):
            first = None
            head_led = False
        matches = _action_matches(move, clause.text)
        returning = _action_matches(return_head, clause.text) if first and head_led else []
        for match in matches + returning:
            target = _gaze_target_label(match.group('target'))
            if target is None:
                continue
            if first and target != first and (returning or re.search(r'随后|然后|再|接着', clause.text[:match.start()] + match.group())):
                return True, head_led
            leading = bool(re.search(r'先', clause.text[:match.end()]))
            follows = bool(_action_matches(re.compile(r'眼球(?P<verb>随头|随头部)'), clause.text))
            if leading or follows:
                first, first_stage, head_led = target, (clause.start, clause.end), follows
    return False, False


def _same_target_eye_hold(clauses: Sequence[Clause], initial: Sequence[Clause]) -> bool:
    """A named maintained target need not be rewritten as a fictitious shift.

    The narrow head-tracking form also needs the same explicit initial, action
    and final target. Fine focal adjustment on an explicitly maintained target
    is not a target change; an unresolved shift still blocks this route.
    """
    body = '；'.join(c.text for c in clauses)
    if _gaze_paths(body):
        return False
    hold = re.compile(r'(?:视线|目光|眼神)(?:持续|始终|继续)(?P<verb>落在|停在|聚焦于)(?P<target>[^，,；;。！？!?\n]+)')
    held = [_gaze_target_label(m.group('target')) for c in clauses for m in _action_matches(hold, c.text)]
    held = [target for target in held if target]
    motion = re.compile(r'(?:眼球|视线|目光)(?P<verb>微移向|轻微移向)(?P<target>[^，,；;。！？!?\n]+)')
    moves = [_gaze_target_label(m.group('target')) for c in clauses for m in _action_matches(motion, c.text)]
    targets = [_gaze_target_label(m.group('target')) for c in clauses for m in _positive(GAZE_TARGET_RE, c.text)]
    focus_clauses = [c for c in clauses if _action_matches(FOCUS_ACTION_RE, c.text)]
    if focus_clauses:
        # Only the bounded fine-adjustment predicate may coexist with a hold.
        # A hold in another time window, another person, an unnamed target or
        # an added destination cannot resolve an otherwise incomplete shift.
        fine = r'(?:眼球|视线|目光|焦点)(?:轻微|细微)?(?:进行)?焦点调整'
        for clause in focus_clauses:
            owner = re.escape(clause.owner or '')
            if not re.fullmatch(r'(?:' + owner + r')?(?:的)?' + fine, clause.text.strip()):
                return False
            local_holds = [m for other in clauses
                           if (other.owner, other.start, other.end) == (clause.owner, clause.start, clause.end)
                           for m in _action_matches(hold, other.text)]
            if not local_holds or any(_gaze_target_label(m.group('target')) is None for m in local_holds):
                return False
        if any(target is None for target in targets):
            return False
        start_targets = [_gaze_target_label(m.group('target')) for c in initial
                         for m in _positive(GAZE_TARGET_RE, c.text)]
        if start_targets and (None in start_targets or len(set(start_targets + held + targets)) != 1):
            return False
    targets = [target for target in targets if target]
    # General movement, a target departure or an unresolved change cannot be
    # concealed by one hold elsewhere in the visible interval.
    for clause in clauses:
        if _positive(GAZE_CHANGE_RE, clause.text) and not _action_matches(motion, clause.text):
            return False
    if held and len(set(held + targets + moves)) == 1:
        return True
    start = [_gaze_target_label(m.group('target')) for c in initial for m in _positive(GAZE_TARGET_RE, c.text)]
    start = [target for target in start if target]
    tracking = any(_positive(re.compile(r'头部(?:只作)?(?:小幅|轻微|微小)(?:偏转|转动)'), c.text) for c in clauses)
    return bool(start and targets and moves and all(moves) and tracking and
                len(set([start[-1]] + targets + moves)) == 1)


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
    """Compatibility adapter for the shared evidence-based entity resolver.

    Unresolved labels are deliberately absent here, not interpreted as props.
    Executable callers must inspect resolution.faults to keep them blocking.
    """
    resolved = resolve_visible_subjects(visible, people=explicit)
    return list(resolved.people), resolved.descriptions


def visible_subject_annotation_faults(value: Any) -> list[str]:
    """Reject the proven hidden-person annotation form without guessing names."""
    faults = []
    pattern = re.compile(r"[（(]\s*(?:与|和|以及|及)([^（）()]+?)(?:同框|一同入画|均可见)")
    body_item = re.compile(r"(?:左侧|右侧|近侧|远侧)?(?:" + BODY_PART_PATTERN + r"|坐姿|站姿)(?:清楚|清晰|可见|可辨)?")
    for match in pattern.finditer(str(value or "")):
        items = [item.strip() for item in re.split(r"以及|[、，,与和及]", match.group(1)) if item.strip()]
        if items and not all(body_item.fullmatch(item) for item in items):
            faults.append("可见主体注释含额外共同入画者，须在顶层逐位列明后再判人数；不能把括号内名单视为当前人物的姿态注释")
    return _dedupe(faults)


def _subject(text: str, names: Sequence[str]) -> str | None:
    prefix = r"^(?:(?:镜尾|此时|随后|接着|同时|最后|最终|开口前|听完后|换焦后|镜头开始时|起始时|前景|后景|中景|但是|然而|但)[，,：:\s]*|" + TIME_SUBJECT_PREFIX + r")*"
    for name in sorted(names, key=len, reverse=True):
        if re.match(prefix + re.escape(name), text):
            clock = r"(?:(?:在|于|第)?\d+(?:\.\d+)?\s*(?:秒|s)(?:时)?)?"
            # An observation is not the observer performing the observed
            # action. Require an actual action/body predicate after the named
            # observed person, so simply '甲观察乙' does not transfer ownership.
            for observed in sorted(names, key=len, reverse=True):
                if re.search(r"(?:观察|看着|看见|望着|注视)" + re.escape(observed) + r"(?:的)?" + clock +
                             r"(?:眼球|视线|目光|眼神|双眼|眼睛|上眼睑|下眼睑|眼睑|双手|左手|右手|头部|轻点头|微点头|点头|摇头|颔首|抬眼|垂眼|转头|侧头|吸气|呼气|眨眼)", text):
                    return observed
            # "甲观察乙眼球换焦" describes 乙's eyes, not 甲's.
            # The first possessive eye component also keeps an ordinary gaze
            # path (甲视线从袖口移向乙眼睛) assigned to its real owner 甲.
            holders = [(match.start(), person) for person in names for match in re.finditer(re.escape(person) + r"(?:的)?" + clock + r"(?:眼球|视线|目光|眼神|双眼|眼睛|上眼睑|下眼睑|眼睑)", text)]
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


def listener_reaction_faults(
    shot: dict[str, Any], *, duration: float,
    dialogue_rows: Sequence[tuple[str, str] | dict[str, Any]],
    visible_names: Sequence[str] | None = None,
    non_person_names: Sequence[str] | None = None,
) -> list[str]:
    """Bind a visible listener's real response to the earlier speech.

    A global '随后' (for example on window paper) is not listener evidence.
    A numerical response time is supported without requiring a magic '错峰'
    word, but must follow another person's actual speech start. This check
    does not require eyes when the listener is only visible by hands/body.
    """
    try:
        shot = timing_view(shot)
    except PictureTimingError as exc:
        return [str(exc)]
    rows = _rows(dialogue_rows)
    entities = resolve_visible_subjects(shot.get('可见主体'), people=visible_names,
        non_people=non_person_names, speakers=[name for name, _ in rows],
        evidence_texts=[shot.get('画面内容'), shot.get('起始状态')])
    if entities.faults:
        return entities.faults
    names = list(entities.people)
    if len(names) < 2 or not rows:
        return []
    clauses = _clauses(shot.get('画面内容'), names, duration)
    speakers = {speaker for speaker, _ in rows}
    speech_starts: dict[str, float] = {}
    for clause in clauses:
        if '说道' not in clause.text or '〈对白〉' not in clause.text or clause.owner not in speakers:
            continue
        times = [float(m.group('time')) for m in BLINK_TIME_RE.finditer(clause.text.split('说道', 1)[0])]
        moment = times[0] if times else clause.start
        if clause.start <= moment <= clause.end:
            speech_starts.setdefault(clause.owner, moment)
    reaction = re.compile(r"(?P<verb>(?:轻|微)?点头|摇头|颔首|抬眼|垂眼|转头|侧头|吸气|呼气|眨眼|收紧|松开|放松|轻收|微收|略收|微抬|略抬)")
    stagger = re.compile(r"晚半拍|晚约\d+(?:\.\d+)?秒|随后|听(?:清|完|到)[^，,；;。！？!?\n]{0,16}后|尾音[^，,；;。！？!?\n]{0,8}后|(?:错开|延迟)[^，,；;。！？!?\n]{0,8}(?:反应|点头|抬眼)")
    reacted: set[str] = set()
    for index, clause in enumerate(clauses):
        name = clause.owner
        if name not in names or not _action_matches(reaction, clause.text):
            continue
        # Require the actor at this subject, or a directly adjacent body/action
        # continuation of that actor. An inherited environment clause is not it.
        subject = _subject(clause.text, names)
        previous = clauses[index - 1] if index else None
        continuation = bool(previous and previous.owner == name and
                            (previous.start, previous.end) == (clause.start, clause.end) and
                            _subject(previous.text, names) == name and
                            re.match(r"^(?:其|他|她)?(?:(?:的)?(?:双手|左手|右手|手指|上眼睑|下眼睑|眼睑|视线|目光|头部)|轻点头|微点头|点头|摇头|颔首|轻吸气)", clause.text))
        if subject != name and not continuation:
            continue
        earlier = [start for speaker, start in speech_starts.items() if speaker != name]
        if not earlier:
            continue
        text = clause.text
        if continuation:
            text = previous.text + '；' + text
        moments = [float(m.group('time')) for m in BLINK_TIME_RE.finditer(text)]
        timed = bool(moments) and all(clause.start <= moment <= clause.end and moment > min(earlier) for moment in moments)
        causal = bool(_positive(stagger, text)) and clause.end > min(earlier)
        # An authored clock is stronger evidence than '随后'. A contradictory
        # clock cannot fall back to untimed causality, including simultaneous
        # onset, a response before speech, or an out-of-phase response.
        response_valid = timed if moments else causal
        if response_valid:
            reacted.add(name)
    listeners = [name for name in names if name not in speakers]
    missing = [name for name in listeners if name not in reacted]
    if listeners:
        return [f"{name}未绑定实际听后动作与错峰时机" for name in missing]
    return [] if reacted else ["可见发言者之间未绑定实际听后动作与错峰时机"]


def _source_asserts_actor_closed(name: str, source: Any) -> bool:
    def mask_quote(match: re.Match[str]) -> str:
        payload = next(group for group in match.groups() if group is not None)
        # Retain only the terminal sentence boundary, never quoted facts.
        # Otherwise '乙说："...。"甲合眼' loses the new narrative subject.
        boundary = payload[-1:] if payload[-1:] in '。！？!?' else ''
        return '〈引文〉' + boundary

    source = QUOTE_RE.sub(mask_quote, str(source or ''))
    modifiers = r"(?:(?:已经|已|正|正在|缓缓|慢慢|轻轻|终于|仍|还|也|重新|自然|不由|疲倦地|疲惫地|因疲倦|因困倦))*"
    location = r"(?:(?:在|于)[^，,；;。！？!?\n〈〉]{1,16}?(?:上|下|内|里|旁|边))?"
    # Only a direct subject assertion can supply this narrow source exception.
    # A location/adverb may intervene, but another actor's observed action or
    # quoted statement cannot. Less explicit source syntax remains reviewable,
    # rather than being author-certified through a 16-character wildcard.
    source_closed = re.compile(r"(?:^|[，,；;。！？!?\n])\s*" + re.escape(name) + modifiers + location + modifiers + r"(?P<verb>合眼|闭眼|睡着|昏迷)")
    return bool(_action_matches(source_closed, source))


def _source_closed_throughout(name: str, shot: dict[str, Any], own: Sequence[Clause], start: Sequence[Clause]) -> bool:
    """Narrow full-shot closed-eye applicability; never invent a reopening.

    The source line and static field must both support closure, the actual
    performance must explicitly keep it throughout, and a real reopening
    invalidates the exemption. Source entailment remains independently audited.
    """
    static = '；'.join(c.text for c in start if c.owner == name)
    body = '；'.join(c.text for c in own)
    closed = re.compile(r"(?:全程|始终)(?P<verb>闭眼|合眼|合拢双眼)|(?:双眼|眼睛)(?:全程|始终)(?P<preverb>合拢|闭合|闭着)")
    reopening = re.compile(r"(?:睁开|睁眼|张开双眼)")
    return bool(_positive(PLOT_CLOSED_RE, static) and _source_asserts_actor_closed(name, shot.get('原文支撑句'))
                and _action_matches(closed, body) and not _positive(reopening, body))


def _merge(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for start, end in sorted(set(intervals)):
        if result and start <= result[-1][1] + 1e-9:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def eye_visibility_window_matches(text: str, subject: str, *, require_subject: bool = False) -> list[re.Match[str]]:
    """The registered eye-window grammar, shared with macro-stage scanning.

    Returned matches are assertions, never quoted words. The eye consumer may
    inherit a known clause owner; the timeline caller must require that owner
    explicitly rather than exempt an arbitrary bare numeric range.
    """
    actor = re.escape(subject) + r'(?:的)?'
    if not require_subject:
        actor = '(?:' + actor + ')?'
    pattern = re.compile(r'(?:^|[；;，,。（(\n】])' + actor +
                         r'(?:面部|眼睛|双眼|眼部)(?:清楚|清晰)?仅在\s*'
                         r'(?P<start>\d+(?:\.\d+)?)\s*[—–-]\s*(?P<end>\d+(?:\.\d+)?)'
                         r'\s*秒(?:清楚|清晰|完整)?(?:可见|露出)')
    quoted = [(match.start(), match.end()) for match in QUOTE_RE.finditer(text)]
    return [match for match in pattern.finditer(text)
            if not any(begin < match.end() and end > match.start('start') for begin, end in quoted)]


def eye_visibility_timeline_scan(text: str, *, duration: float, visible_subjects: str | None) -> str:
    """Mask only recognized, bounded actor windows in a temporary scan copy.

    The source body is unchanged. Real 【...】 stages are never masked, and an
    invalid/unowned/quoted/negated range remains for the strict stage guard.
    """
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return text
    names = list(resolve_visible_subjects(visible_subjects, evidence_texts=[text]).people)
    ranges = []
    for name in names:
        for match in eye_visibility_window_matches(text, name, require_subject=True):
            begin, end = float(match.group('start')), float(match.group('end'))
            if 0 <= begin < end <= duration:
                ranges.append((match.start('start'), match.end('end')))
    result = list(text)
    for begin, end in ranges:
        result[begin:end] = ' ' * (end - begin)
    return ''.join(result)


def _visibility(name: str, descriptor: str, shot: dict[str, Any], clauses: list[Clause], duration: float) -> tuple[list[tuple[float, float]], list[str]]:
    shot_type = str(shot.get("镜头类型") or "")
    own = [clause for clause in clauses if clause.owner == name]
    own_text = descriptor + "；" + "；".join(clause.text for clause in own)
    windows = eye_visibility_window_matches(own_text, name)
    faults: list[str] = []
    if windows:
        intervals = []
        for window in windows:
            start, end = float(window.group('start')), float(window.group('end'))
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
    non_person_names: Sequence[str] | None = None,
    dialogue_rows: Sequence[tuple[str, str] | dict[str, Any]] | None = None,
    legacy_components: bool = False,
) -> list[str]:
    """Validate each identified person's actual continuous visible intervals.

    ``visible_names`` may supply already-authoritatively-resolved identities;
    otherwise identities are read from 可见主体. A bounded visibility exception
    can be authored as ``甲眼睛仅在0.0—1.5秒清楚可见``. An unbounded claim of
    "可见1秒" cannot waive a four-second visible-face contract. Current callers
    inspect actual events. ``legacy_components=True`` is only for explicitly
    selected historical replay, never the default production obligation.
    """
    try:
        shot = timing_view(shot)
    except PictureTimingError as exc:
        return [str(exc)]
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return ["眼部检查需要有效正数镜头时长"]
    visible = str(shot.get("可见主体") or "")
    picture = str(shot.get("画面内容") or "")
    raw_rows = dialogue_rows if dialogue_rows is not None else [(match.group("speaker"), match.group("line")) for match in DIALOGUE_RE.finditer(picture)]
    entities = resolve_visible_subjects(visible, people=visible_names, non_people=non_person_names,
        speakers=[name for name, _ in _rows(raw_rows)],
        evidence_texts=[picture, shot.get('起始状态')])
    names, descriptions = list(entities.people), entities.descriptions
    identity_faults = _identity_faults(visible, names)
    identity_faults.extend(entities.faults)
    if not names:
        if duration >= 2 and FACE_SCALE_RE.search(str(shot.get("镜头类型") or "")) and _positive(re.compile(r"人|面部|眼睛|侧脸"), visible):
            return _dedupe(identity_faults + ["可见人脸未绑定具体人物身份，不能执行逐人眼部检查"])
        return identity_faults
    clauses = _eye_clauses(picture, names, duration)
    start_clauses = _eye_clauses(shot.get("起始状态"), names, duration)
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
            if any(_action_matches(FOCUS_ACTION_RE, clause.text) or _action_matches(EYELID_CHANGE_RE, clause.text) or _positive(BLINK_RE, clause.text) for clause in own_all):
                faults.append(f"{name}眼部不可见，不能安排可见眼球、眼睑或眨眼表演")
            continue
        if _source_closed_throughout(name, shot, own_all, start_clauses):
            if any(_action_matches(FOCUS_ACTION_RE, c.text) or _gaze_paths(c.text) or _positive(BLINK_RE, c.text) for c in own_all):
                faults.append(f"{name}源文明确全程闭眼，却安排可见眼球换焦或眨眼表演")
            # Closed eyelids can move, but visible iris paths, gaze targets and
            # open-eye blinking are inapplicable throughout this narrow case.
            continue
        own_static = '；'.join(c.text for c in start_clauses if c.owner == name)
        if PLOT_CLOSED_RE.search(own_static) and any(re.search(r"全程闭眼|全程合眼|双眼始终合拢", c.text) for c in own_all) and not _source_asserts_actor_closed(name, shot.get('原文支撑句')):
            faults.append(f"{name}全程闭眼的原文主体或引用层次尚未明确绑定，待独立源文复核；本检查不要求擅自补写睁眼")
        visible_people.append(name)
        all_events[name], _ = _blink_events(own_all)
        faults.extend(_no_blink_conflicts(name, own_all, all_events[name]))
        for clause in own_all:
            if not any(clause.start < stop and clause.end > start for start, stop in intervals):
                if _action_matches(FOCUS_ACTION_RE, clause.text) or _action_matches(EYELID_CHANGE_RE, clause.text) or _positive(BLINK_RE, clause.text):
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
            if not legacy_components:
                # Current prose inspection checks authored events, not an
                # invented eye-action quota. Missing structured production
                # evidence is handled by its own mapping/derivation gate.
                events, event_faults = _blink_events(own)
                faults.extend(f"{label}{fault}" for fault in event_faults)
                maximum = 1 if end - begin <= 5 else (2 if end - begin <= 10 else None)
                count = sum(event['count'] for event in events)
                if maximum is not None and count > maximum:
                    faults.append(f"{label}可见区间眨眼上限应为{maximum}次，当前逐事件累计{count}次")
                held = [m.group('target') for c in own for m in _positive(
                    re.compile(r'(?:视线|目光|眼神)(?:持续|始终|继续)(?:落在|停在|聚焦于)(?P<target>[^，,；;。！？!?\n]*)'), c.text)]
                initial = [m.group('target') for c in start_clauses if c.owner == name
                           for m in _positive(GAZE_TARGET_RE, c.text)]
                if held and (any(_gaze_target_label(target) is None for target in held)
                             or initial and len(set(initial + held)) != 1 and not _gaze_paths(body)):
                    faults.append(f"{label}持续注视声明与起始视线目标冲突或目标未明确，须核验视线承接")
                for clause in own:
                    # Check asserted movement separately from a legal hold.
                    # A rejected/negated movement is not a demand to add one.
                    declared_paths = _action_matches(GAZE_PATH_RE, clause.text)
                    if declared_paths and len(_gaze_paths(clause.text)) < len(declared_paths):
                        faults.append(f"{label}已写视线移动的起点与目标相同或目标未解析，须核验实际视线事件")
                    incomplete_move = re.compile(r'(?:眼球|视线|目光|眼神)[^，,；;。！？!?\n]{0,18}?(?P<verb>移向|转向|换焦至|换焦到|移焦至|移焦到)(?P<target>[^，,；;。！？!?\n]*)')
                    for move in _action_matches(incomplete_move, clause.text):
                        if _gaze_target_label(move.group('target')) is None:
                            faults.append(f"{label}已写视线移动但目标未明确，须核验实际目标")
                continue
            # Join only this actor's clauses. Other people and spoken words cannot
            # contribute any eye-chain component or no-blink exemption.
            sequential_path, head_led = _sequential_eye_path(own)
            path_ok = bool(_gaze_paths(body)) or sequential_path
            hold_ok = _same_target_eye_hold(own, [c for c in start_clauses if c.owner == name])
            explicit_head_led = bool(_action_matches(re.compile(r'眼球(?P<verb>随头部动作|随头向)'), body))
            focus_ok = bool(_action_matches(FOCUS_ACTION_RE, body)) or sequential_path or (path_ok and explicit_head_led)
            eyelid_ok = bool(_action_matches(EYELID_CHANGE_RE, body))
            if not (path_ok or hold_ok):
                faults.append(f"{label}缺视线从起点移向具体目标的换焦路径")
            hold_head = hold_ok and (bool(_positive(re.compile(r'头部(?:只作)?(?:小幅|轻微|微小)(?:偏转|转动)'), body)) or
                                    bool(_action_matches(re.compile(r'眼球(?P<verb>随头部角度调整)继续对准'), body)))
            if not (_positive(HEAD_RE, body) or head_led or (path_ok and explicit_head_led) or hold_head):
                faults.append(f"{label}缺眼球先动、头部随后轻跟或明确不跟的关系")
            if not (focus_ok or hold_ok):
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
                if not ((focus_ok or hold_ok) and eyelid_ok and any(_positive(re.compile(r"呼吸|气息|吸气|呼气"), clause.text) for clause in own)):
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
    if not eligible_people or not legacy_components:
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
