"""Source-only grammar for explicitly declared pure-dialogue plays and act ranges.

No candidate inventory teaches this parser a speaker. Other prose keeps the
existing closed parser; this profile requires its declaration, cast and acts.
"""
from __future__ import annotations

import re

CONTRACT = 'pure-dialogue-play-v1'
PARTITION_CONTRACT = 'script-episode-partition-v1'
ACT = re.compile(r'^第[一二三四五六七八九十百零\d]+幕(?:\s|$)')
ROLE = re.compile(r'^([A-Za-z\u3400-\u9fff·]{1,16})[：:](.+)$')


def source_paragraphs(text):
    rows=[]
    offset=0
    for number,line in enumerate(text.splitlines(keepends=True),1):
        raw=line.rstrip('\n')
        if raw.strip():
            rows.append({'number':len(rows)+1,'line':number,'start':offset,
                         'end':offset+len(raw),'text':raw})
        offset+=len(line)
    return rows


def profile_header(text):
    rows=source_paragraphs(text)
    if not rows or not re.fullmatch(r'.{1,180}[（(][^\n（）()]*纯台词剧本[^\n（）()]*[）)]',rows[0]['text'].strip()):
        return None
    from .source_dialogue import SourceDialogueAuthorityError
    def reject():
        raise SourceDialogueAuthorityError('pure_dialogue_play_invalid: requires title, 人物 cast roster and numbered acts')
    if len(rows)<5 or rows[1]['text'].strip()!='人物':reject()
    first=next((i for i,r in enumerate(rows) if ACT.match(r['text'].strip())),None)
    if first is None or first<3:reject()
    cast=[]
    for row in rows[2:first]:
        match=ROLE.fullmatch(row['text'].strip())
        if not match or match[1] in cast:reject()
        cast.append(match[1])
    return rows,first,cast


def parse_pure_play(text):
    header=profile_header(text)
    if header is None:return None
    from .source_dialogue import (
        DialogueOccurrence, SourceDialogueAuthorityError, _speaker,
        _CUE_RE, _QUOTE_LIKE_RE, _PRONOUN_SPEAKERS, _script_declared_speakers, _validate_quote_integrity,
    )
    rows,first,cast=header
    known=set(cast)|_script_declared_speakers(text)
    result=[]
    active=None
    staging=''
    previous_line=0
    for row in rows[first:]:
        value=row['text'].strip()
        if previous_line and row['line']>previous_line+1:active=None
        previous_line=row['line']
        if ACT.match(value):
            active=None
            staging=''
            continue
        if value.startswith(('（','【')):
            closing='）' if value.startswith('（') else '】'
            if not value.endswith(closing) or (value.startswith('【') and not value.startswith('【场景：')):
                raise SourceDialogueAuthorityError(f'pure_dialogue_play_invalid: malformed stage at line {row["line"]}')
            # Parentheses explicitly mark staging, but cannot hide speech.
            body=value[1:-1]
            audible_cue=any(m.group('separator') or m.group('open')
                or (m.group('speaker') in known|_PRONOUN_SPEAKERS and body[m.end():].strip('，。、；！？ '))
                for m in _CUE_RE.finditer(body))
            if _QUOTE_LIKE_RE.search(body) or audible_cue or any(re.search(re.escape(who)+r'[：:]',body) for who in known):
                raise SourceDialogueAuthorityError(f'pure_dialogue_play_ambiguous: speech within stage at line {row["line"]}')
            if value.startswith('【') or not active or not body.startswith(active):
                active=None
            staging+=body+'\n'
            continue
        match=ROLE.fullmatch(value)
        if match:
            active,spoken=match.groups()
            prefix=len(value)-len(spoken)
            voice=re.fullmatch(r'(.{1,12})(残音|回声|画外音)',active)
            if voice and voice[1] in staging and re.search(r'无实体|回声|声音',staging):
                known.add(active)
        elif active:
            spoken=value
            prefix=0
        else:
            raise SourceDialogueAuthorityError(f'pure_dialogue_play_ambiguous: missing speaker at line {row["line"]}')
        # A punctuation-only ellipsis is an explicit silent beat, not words.
        if re.fullmatch(r'(?:…{1,}|\.{3,})',spoken):
            active=None
            continue
        start=row['start']+len(row['text'])-len(row['text'].lstrip())+prefix
        who=_speaker(active,line_number=row['line'],context=CONTRACT,known_speakers=known)
        _validate_quote_integrity(spoken,source_text=text,offset=start,context=CONTRACT)
        result.append(DialogueOccurrence(who,spoken,row['line'],start,'pure_dialogue_play'))
    return result


def select_partition(plan,text,occurrences,episode_ids,source_id,source_sha):
    """Validate the whole saved partition before selecting any episode.

    Service callers must also authenticate the plan artifact and passed work
    order. Boundaries can group whole acts; they cannot carve out dialogue.
    """
    if not isinstance(plan,dict) or set(plan)!={'contract','source_artifact_id','source_sha256','episodes'}:
        raise ValueError('source_partition_shape')
    if (plan['contract']!=PARTITION_CONTRACT or plan['source_artifact_id']!=source_id
            or plan['source_sha256']!=source_sha):raise ValueError('source_partition_source_binding')
    header=profile_header(text)
    if header is None:raise ValueError('source_partition_requires_pure_dialogue_play')
    rows,first,_cast=header
    cursor=first+1
    seen=set();ranges={}
    if not isinstance(plan['episodes'],list) or not plan['episodes']:raise ValueError('source_partition_empty')
    for item in plan['episodes']:
        if not isinstance(item,dict) or set(item)!={'episode_id','paragraph_range'}:raise ValueError('source_partition_episode_shape')
        ep=item['episode_id'];span=item['paragraph_range']
        if not isinstance(ep,str) or not re.fullmatch(r'EP\d{2,}',ep) or ep in seen:raise ValueError('source_partition_episode_identity')
        if (not isinstance(span,list) or len(span)!=2 or any(type(x)is not int for x in span)
                or span[0]!=cursor or not cursor<=span[1]<=len(rows)):raise ValueError('source_partition_gap_overlap_or_range')
        lo,hi=span
        if not ACT.match(rows[lo-1]['text'].strip()):raise ValueError('source_partition_start_not_act')
        if hi<len(rows) and not ACT.match(rows[hi]['text'].strip()):raise ValueError('source_partition_end_not_act')
        ranges[ep]=(rows[lo-1]['start'],rows[hi-1]['end']);seen.add(ep);cursor=hi+1
    if cursor!=len(rows)+1:raise ValueError('source_partition_incomplete')
    if not episode_ids or len(set(episode_ids))!=len(episode_ids) or any(ep not in seen for ep in episode_ids):raise ValueError('source_partition_unknown_episode')
    ordered=[x['episode_id'] for x in plan['episodes'] if x['episode_id'] in episode_ids]
    if ordered!=episode_ids:raise ValueError('source_partition_episode_order')
    selected=[r for r in occurrences if any(ranges[ep][0]<=r.char_offset<ranges[ep][1] for ep in episode_ids)]
    return selected,{'contract':PARTITION_CONTRACT,'episode_ids':episode_ids,
                     'source_occurrence_count':len(occurrences),'selected_occurrence_count':len(selected),
                     'paragraph_ranges':[x for x in plan['episodes'] if x['episode_id'] in episode_ids]}
