"""Derive current paper payloads from text; never trust caller metadata.

This deliberately small grammar is an additional, typed recognition route.
It does not infer missing words or certify that a statement is source-faithful.
Only exact quoted bytes are protected; every surrounding instruction remains
available to the public static gates. Unknown syntax remains unbound.
"""
from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    literal: str


@dataclass(frozen=True)
class CurrentWrittenPayload:
    field_name: str
    field_sha256: str
    surface: TextSpan
    assertion: TextSpan
    quote: TextSpan
    kind: str = "current_written_v1"


_QUOTE = re.compile(r'“[^”\n]*”|"[^"\n]*"|「[^」\n]*」|『[^』\n]*』')
_SURFACE = re.compile(r"纸面|纸上|纸签|记录纸|账册|册页|书页|(?:同一张|这张|该张|一张)纸(?:的(?:上部|下部|左侧|右侧|中部))?")
_BREAK = re.compile(
    r"旁白|内声|音轨|台词|对白|转述|口述|朗读|朗诵|默读|念出|读出|"
    r"说道|说着|说话|问道|答道|喊道|[说问答喊](?=\s*[:：，,]|$)|"
    r"将来|将要|即将|随后|稍后|待写|计划|打算|预备|"
    r"正在|尚未|未曾|从未|没有|并非|不是|不再|不存在|"
    r"昨日|昨天|从前|曾经|回忆|原文|源文|"
    r"未知|未定|不清楚|不确定|无法确认|待确认|是否|能否|"
    r"如果|假如|倘若|可能|也许"
)
_UNCERTAIN_SUFFIX = re.compile(r"尚不清楚|不确定|无法确认|未能确认|尚未确认|待确认|仍未确定|是否")
# Role labels are opaque noun text, not a whitelist of private story labels.
# The affirmative current predicate and colon are required independently.
_ASSERTION = re.compile(
    r"\s*(?:(?:纸面|纸上|纸签|记录纸|账册|册页|书页)\s*)?"
    r"(?:现在|当前|此刻|眼下)?"
    r"(?:(?:现有|已有)(?P<role>[^，,。；;：:\n“”\"「」『』!?！？]{1,64})"
    r"|全文只有)\s*[:：]\s*$"
)


def discover_current_written_payloads(text: str, field_name: str = "") -> tuple[CurrentWrittenPayload, ...]:
    """Return immutable, exact field/span bindings derived from *this* text.

    No submitted binding, boolean or hash can authorize a quote. A physical
    paper reference may continue across sentences within one static field;
    explicit speech, future, history or uncertainty ends that scope. Newlines
    reset it, so a projected field cannot borrow another field's paper.
    """
    if not isinstance(text, str):
        raise TypeError("current_written_payload requires an actual string")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result = []
    previous_end = 0
    surface = None
    blocked = False
    for quote in _QUOTE.finditer(text):
        gap_start = previous_end
        gap = text[gap_start:quote.start()]
        previous_end = quote.end()
        if "\n" in gap:
            gap_start += gap.rfind("\n") + 1
            gap = text[gap_start:quote.start()]
            surface = None
            blocked = False
        if _BREAK.search(gap):
            surface = None
            blocked = True
        if not blocked:
            for match in _SURFACE.finditer(gap):
                surface = TextSpan(gap_start + match.start(), gap_start + match.end(), match.group())
        # The assertion is its own punctuation-delimited clause. A character's
        # face, speech or another unbound domain cannot borrow the paper noun.
        local_start = max((gap.rfind(char) for char in "，,。；;！？!?"), default=-1) + 1
        local = gap[local_start:]
        assertion = _ASSERTION.fullmatch(local)
        suffix = re.split(r'[。；;！？!?\n“"「『]', text[quote.end():], maxsplit=1)[0]
        if (blocked or surface is None or assertion is None
                or _BREAK.search(local) or _UNCERTAIN_SUFFIX.search(suffix)):
            continue
        absolute_start = gap_start + local_start
        result.append(CurrentWrittenPayload(
            field_name=field_name,
            field_sha256=digest,
            surface=surface,
            assertion=TextSpan(absolute_start, quote.start(), text[absolute_start:quote.start()]),
            quote=TextSpan(quote.start(), quote.end(), quote.group()),
        ))
    return tuple(result)


_SPOKEN_QUOTE_INTRO = re.compile(r"(?:说道|问道|答道|喊道|内声|旁白|心理音轨)\s*[:：]\s*$")


def context_instruction_text(text: str, field_name: str) -> str:
    """Mask typed literal payloads only for instruction cross-reference checks.

    A quote alone is not an exemption: quoted staging instructions still get
    checked. Speech is recognized only in the two fields allowed to carry it;
    source truth, speaker identity and dialogue fidelity remain separate gates.
    """
    text = str(text or "")
    # Export and field ownership share exact physical-text bindings. Restore
    # marker-only fragments; only recognized quoted payload bytes are masked.
    protected_text, protected = protect_visual_text_fragments(text, field_name=field_name)
    for token, fragment in protected.items():
        protected_text = protected_text.replace(token, " " * len(fragment)
                                               if _QUOTE.fullmatch(fragment) else fragment)
    spans = []
    if field_name in {"画面内容", "心理音轨"}:
        for quote in _QUOTE.finditer(text):
            if _SPOKEN_QUOTE_INTRO.search(text[:quote.start()]):
                spans.append((quote.start(), quote.end()))
    masked = list(protected_text)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    return "".join(masked)


KEYFRAME_PROTECTED_VISUAL_LABEL_PATTERN = (
    r"(?:参考图锚点|可见文字|画内文字|道具文字|"
    r"匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)"
)
KEYFRAME_PROTECTED_VISUAL_FRAGMENT_RE = re.compile(
    rf"(?:【{KEYFRAME_PROTECTED_VISUAL_LABEL_PATTERN}】[^；;\n，,]*|"
    rf"(?:画内)?(?:匾额|牌匾|招牌|门牌|题字|"
    rf"可见文字|画内文字|道具文字)"
    rf"(?=[^；;\n，,]{{0,32}}(?:[“\"「『]|写着|写有|刻着|刻有|"
    rf"题着|题有|印着|印有|可见文字|字样|清晰可见))"
    rf"[^；;\n，,]*)"
)
KEYFRAME_QUOTED_DIALOGUE_RE = re.compile(r'[“\"「『][^”\"」』\n]*[”\"」』]')


def protect_visual_text_fragments(value: str, *, field_name: str = "") -> tuple[str, dict[str, str]]:
    """Protect written payloads, never adjacent action or quoted speech."""
    protected: dict[str, str] = {}
    source_text = str(value or "")
    typed_quotes = {item.quote.start: item.quote.literal
                    for item in discover_current_written_payloads(source_text, field_name)}

    def replace(match: re.Match[str]) -> str:
        token = f"\ue000VISUAL{len(protected)}\ue001"
        protected[token] = match.group(0)
        return token

    surface_re = re.compile(r"纸面|纸上|纸签|记录纸|账册|册页|书页|木牌|布告|标签|标题|题头|栏名|表头|简图|地图|示意图|页签|最后一行|门牌|匾额|牌匾|招牌|题字|可见文字|画内文字|道具文字")
    marking_re = re.compile(r"写着|写有|写到|写好|已写的|刻着|刻有|印着|印有|标注|标记|现有|已有|可读|标签|标题|题头|栏名|表头|字样|文字|题字|清晰可见")
    speaking_re = re.compile(
        r"说道|说着|问道|答道|喊道|念道|朗读|朗诵|念出|念着|读出|读着|默读|"
        r"低声|轻声|高声|开口|转述|口述|心理内声|内声|旁白|对白|台词|音轨|"
        r"(?:说|问|答|喊|念|(?<!可)读)\s*[:：]?\s*$|"
        r"(?:说|问|念|(?<!可)读)(?=纸面|纸上|册页|书页|文字)"
    )
    historical_re = re.compile(
        r"昨日|昨天|前日|前一日|从前|以前|当年|当时|曾经|"
        r"曾(?:写|有|刻|印)|回忆|记得|想起|原文|源文|历史引用"
    )
    enumeration_re = re.compile(r"[\s，,、；;:：]*(?:(?:以及|及|和|与)[\s，,、；;:：]*)?")
    current_re = re.compile(r"现在|当前|此刻|眼下")
    correction_re = re.compile(r"[\s，,]*而是(?:写着|写有|刻着|刻有|印着|印有|标注为)[\s:：]*")
    column_intro_re = re.compile(r"栏|列|字段")
    column_value_re = re.compile(
        r'[\s，,、；;]+(?:(?:以及|及|和|与)\s*)?[^“"「『，,、；;:：=。！？!?\n]+?(?:为|是|[:：=])\s*'
    )
    column_break_re = re.compile(
        r"随后|稍后|正在|将(?:要)?(?:写|改|标|刻|印|为|是)|(?:改|变)(?:成|为)|"
        r"(?:不再|已不|不)(?:存在|可读|显示)|擦除|抹去|收起|移走"
    )

    # Keep the physical paper scope separate from the kind of its first
    # value: a title can precede named columns without itself being a column.
    paper_surface_re = re.compile(
        r"纸面|纸上|纸签|记录纸|账册|册页|书页|"
        r"(?:同一张|这张|该张|一张)纸(?:的(?:上部|下部|左侧|右侧|中部))?|"
        r"纸的(?:上部|下部|左侧|右侧|中部)"
    )
    current_role_re = re.compile(
        r'[\s，,、；;]+(?:(?:以及|及|和|与)\s*)?(?:现有|已有)'
        r'(?:标题|题头|表头|栏名|栏目|字段|[^“"「『，,、；;:：=。！？!?\n]*?(?:栏|列))'
        r'(?:为|是|[:：=])\s*'
    )
    negative_marking_re = re.compile(
        r"(?:没有|未曾|从未|并未|尚未|不曾|不是|不|未)(?:写着|写有|标注|现有|已有)|不存在"
    )

    def protect_written_quotes(match: re.Match[str]) -> str:
        clause = match.group(0)
        end = 0
        written = False
        columns = False
        paper_scope = False

        def choose(quote: re.Match[str]) -> str:
            nonlocal end, written, columns, paper_scope
            prefix = clause[end:quote.start()]
            end = quote.end()
            typed_quote = typed_quotes.get(match.start() + quote.start()) == quote.group(0)
            scope_break = bool(speaking_re.search(prefix) or historical_re.search(prefix)
                               or column_break_re.search(prefix))
            if scope_break:
                paper_scope = False
            if (paper_scope and current_role_re.fullmatch(prefix)
                    and not scope_break and not negative_marking_re.search(prefix)):
                written = True
                columns = True
                return replace(quote)
            if written and correction_re.fullmatch(prefix):
                # Preserve both the negated label and its positive correction;
                # only this same-surface continuation may inherit the binding.
                return replace(quote)
            if (written and columns and column_value_re.fullmatch(prefix)
                    and not speaking_re.search(prefix) and not historical_re.search(prefix)
                    and not column_break_re.search(prefix)):
                # Arbitrary column labels inherit this sentence's explicit
                # current paper declaration. Only the value is masked; speech,
                # state changes and other unquoted instructions remain visible.
                return replace(quote)
            if not enumeration_re.fullmatch(prefix):
                # A new unquoted clause needs its own physical-text binding.
                # In particular 可读 is a visibility property, not 读 speech.
                local_prefix = re.split(r"[，,；;]", prefix)[-1]
                scope_prefix = re.split(r"[；;]", prefix)[-1]
                suffix = re.split(r'[“"「『，,。；;！？!?\n]', clause[end:], maxsplit=1)[0]
                context = local_prefix + suffix
                historical = list(historical_re.finditer(scope_prefix))
                current = list(current_re.finditer(scope_prefix))
                historical_only = bool(historical and not (current and current[-1].start() > historical[-1].start()))
                written = bool(
                    (surface_re.search(context) or paper_surface_re.search(context))
                    and marking_re.search(context)
                    and not speaking_re.search(scope_prefix)
                    and not historical_only
                )
                paper_scope = bool(
                    written and paper_surface_re.search(local_prefix)
                    and not column_break_re.search(scope_prefix)
                    and not negative_marking_re.search(scope_prefix)
                )
                columns = bool(written and column_intro_re.search(local_prefix)
                               and re.search(r"现有|已有|现在|当前|此刻|眼下", scope_prefix)
                               and not column_break_re.search(scope_prefix))
            return replace(quote) if written or typed_quote else quote.group(0)

        return KEYFRAME_QUOTED_DIALOGUE_RE.sub(choose, clause)

    # Sentence boundaries reset the binding; commas/semicolons may enumerate
    # several payloads after one explicit paper-text declaration. Protect only
    # the quotes, so a following actor/camera process still reaches the gate.
    text = re.sub(r'(?:[“"「『][^”"」』\n]*[”"」』]|[^。！？!?\n])+', protect_written_quotes, source_text)

    def protect_explicit(match: re.Match[str]) -> str:
        fragment = match.group(0)
        if "\ue000VISUAL" in fragment or re.search(r'[“"「『]', fragment):
            # Only the explicit marker needs protection from bracketed-dialogue
            # removal. The quoted payload is already protected, and a trailing
            # body/environment action must remain visible to the static guard.
            return re.sub(r"【(?:参考图锚点|可见文字|画内文字|道具文字|匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)】", replace, fragment)
        return replace(match)

    return KEYFRAME_PROTECTED_VISUAL_FRAGMENT_RE.sub(protect_explicit, text), protected
