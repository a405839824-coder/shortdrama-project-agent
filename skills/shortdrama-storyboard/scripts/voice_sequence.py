"""Several actual utterances in the unchanged eleven-label voice field."""
import re

VOICE_TEMPLATE_LABELS = (
    "基础声线", "言语行为", "当前表达", "发声方式", "音量", "语速与节奏",
    "呼吸", "停顿", "重音", "尾音", "禁止语气",
)
LEGACY_DIALOGUE_TAG_RE = re.compile(
    r"【(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})】\s*[“\"]?(?P<line>[^【\n｜|；;”\"]+)[”\"]?"
)
# Keep quoted words inside actual speech. Four explicit nested levels cover the
# supported manuscript syntax without treating the first inner closing quote
# as the utterance end. Unbalanced/deeper input is not silently truncated.
_QUOTED_BODY = r'[^“”"\n]'
for _ in range(4):
    _QUOTED_BODY = r'(?:[^“”"\n]|“' + _QUOTED_BODY + r'*”)'
INTEGRATED_DIALOGUE_RE = re.compile(
    r'(?:^|[；;。！？!?，,、】])\s*(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})说道[：:]?\s*[“"](?P<line>'
    + _QUOTED_BODY + r'+)[”"]'
)
MARKER = re.compile(r'〔([1-9][0-9]*):([^〔〕\r\n]+)〕')


def required_voice_clause_findings(label, value, policy):
    """Check required roles against the exact grammar used by field ownership.

    Clause groups are registry indices, not a second emotion/intent word list.
    A supported clause in one group cannot supply a missing group or license an
    unknown extra clause; the field checker reports that extra independently.
    This checks declared expression syntax, not source or acting correctness.
    """
    path = "音色." + label
    groups = policy["voice_subfields"][label].get("required_clause_groups", {})
    patterns = policy.get("owned_clause_patterns", {}).get(path, [])
    clauses = [part.strip() for part in re.split(r"[。；;，,\n]", str(value or "")) if part.strip()]
    findings = []
    for role, indices in groups.items():
        # An invalid registry is an implementation fault, never an empty allowlist.
        if not indices or any(type(i) is not int or not 0 <= i < len(patterns) for i in indices):
            raise ValueError(path + ": invalid required_clause_groups: " + role)
        if not any(re.fullmatch(patterns[i], clause, re.I) for i in indices for clause in clauses):
            findings.append({"code": "E-FIELD-CONTENT-001", "path": path,
                             "role": "missing_owned_clause_group", "status": "fail",
                             "excerpt": str(value or ""), "offset": [0, len(str(value or ""))],
                             "message": path + "缺少必要的" + role,
                             "repair": "按真实对白与人物依据补齐对应表达决定；未知语义保留为待支持，不能自动替换为统一情绪"})
    return findings


def extract_dialogues(picture_content):
    """Shared R10-SPC-1 and legacy read-only syntax, without validator imports."""
    rows = []
    text = str(picture_content or "")
    for match in INTEGRATED_DIALOGUE_RE.finditer(text):
        line = match.group("line").strip()
        if line:
            rows.append((match.start(), match.group("speaker").strip(), line))
    for match in LEGACY_DIALOGUE_TAG_RE.finditer(text):
        line = match.group("line").strip()
        sentence = re.match(r"(.+?[。！？?!](?:[。！？?!…])?)", line)
        if sentence:
            line = sentence.group(1).strip()
        if line:
            rows.append((match.start(), match.group("speaker").strip(), line))
    return [(speaker, line) for _, speaker, line in sorted(rows)]


def has_markers(text):
    return '〔' in text or '〕' in text


def build_voice_sequence(entries):
    from authoring_contract_blocks import voice_block, ContractBlockError
    from validate_storyboard import VOICE_TEMPLATE_LABELS, _parse_voice_template, _voice_template_errors
    blocks=[]; rows=[]
    for entry in entries:
        speaker,text,voice=entry
        if has_markers(speaker) or has_markers(text) or any(has_markers(str(x)) for x in voice.values()):
            raise ContractBlockError('voice sequence marker injection')
        block=voice_block(mode='spoken', dialogue_rows=[(speaker,text)], **voice)
        blocks.append(_parse_voice_template(block)[0]); rows.append((speaker,text))
    if len(blocks)==1: return block
    lines=[]
    for label in VOICE_TEMPLATE_LABELS:
        body='' if label=='发声方式' else ''.join(f'〔{i+1}:{rows[i][0]}〕{b[label]}' for i,b in enumerate(blocks))
        lines.append(label+'：'+body)
    result='\n'.join(lines)
    faults=_voice_template_errors(result,''.join(x[1] for x in rows),rows)
    if faults: raise ContractBlockError('voice sequence: '+'; '.join(faults))
    return result


def split_voice_sequence(parsed, rows):
    if rows is None or len(rows)<2: return [],['多句音色必须绑定实际对白顺序，不能凭标记编造说话人']
    expected=[(str(i+1),row[0]) for i,row in enumerate(rows)]
    blocks=[{} for _ in rows]; errors=[]
    for label in VOICE_TEMPLATE_LABELS:
        if label=='发声方式':
            if parsed.get(label): errors.append('发声方式必须保留空值分组标题')
            for block in blocks: block[label]=''
            continue
        text=parsed.get(label,''); matches=list(MARKER.finditer(text))
        if not matches or matches[0].start()!=0 or [m.groups() for m in matches]!=expected:
            errors.append(label+'的逐句说话人/顺序不匹配实际对白'); continue
        for i,m in enumerate(matches):
            blocks[i][label]=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)]
            if has_markers(blocks[i][label]): errors.append(label+'包含损坏或未登记的逐句标记')
    return blocks,errors


def validate_voice_sequence(parsed, rows):
    from validate_storyboard import VOICE_TEMPLATE_LABELS, _voice_template_errors
    blocks,errors=split_voice_sequence(parsed,rows)
    if errors:return errors
    for i,block in enumerate(blocks):
        text='\n'.join(label+'：'+block[label] for label in VOICE_TEMPLATE_LABELS)
        errors.extend(f'第{i+1}句{rows[i][0]}：'+x for x in _voice_template_errors(text,rows[i][1],[rows[i]]))
    return errors
