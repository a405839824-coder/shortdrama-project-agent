"""Several actual utterances in the unchanged eleven-label voice field."""
import re

MARKER = re.compile(r'〔([1-9][0-9]*):([^〔〕\r\n]+)〕')


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
    from validate_storyboard import VOICE_TEMPLATE_LABELS
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
