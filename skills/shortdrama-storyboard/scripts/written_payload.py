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
    spans = [(item.quote.start, item.quote.end)
             for item in discover_current_written_payloads(text, field_name)]
    if field_name in {"画面内容", "心理音轨"}:
        for quote in _QUOTE.finditer(text):
            if _SPOKEN_QUOTE_INTRO.search(text[:quote.start()]):
                spans.append((quote.start(), quote.end()))
    masked = list(text)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    return "".join(masked)
