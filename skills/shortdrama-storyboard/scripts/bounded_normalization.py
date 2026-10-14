"""Lossless, recorded formatting normalization before content hashes bind.

Only explicit equivalent directives and provable quote punctuation change.
No dialogue, actor action, source text, emotion or timing is inferred. Callers
must retain returned changes and hash the returned candidate, not the input.
Read-only audit should not overwrite an existing candidate with this result.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from projection_semantics import music_directives, strip_negative_music
from voice_sequence import extract_dialogues

CONTRACT = 'bounded-literal-normalization-v1'
NO_DIALOGUE = '无对白，不适用。'
NO_DIALOGUE_ALIASES = re.compile(
    r'^(?:无对白|无人物(?:对白|发言)|本镜无(?:人物)?(?:对白|发言))'
    r'(?:[，,；;。\s]*(?:本镜)?不适用)?[。.]?$'
    r'|^不适用[：:]?\s*(?:本镜)?无对白[。.]?$')
SPEECH_CUE = re.compile(r'说道|说[：:]|问[：:]|答[：:]|喊[：:]|对白[：:]|台词[：:]|【[^】]+】\s*[“\"]')
EMPHASIS_LINE = re.compile(r'(?m)^(\s*重音[：:])([^\r\n]*)')
QUOTE = re.compile(r'“([^”\n]*)”|「([^」\n]*)」|"([^"\n]*)"')


def normalize_music_prohibition(value: Any) -> str | Any:
    """Canonicalize only bans whose entire music scope is established."""
    if not isinstance(value, str):
        return value
    records = music_directives(value)
    if not records or any(row['status'] != 'negative' for row in records):
        return value
    if all(value[max(0, row['span'][0] - 2):row['span'][1]] == '禁止额外配乐'
           for row in records):
        return value  # Do not reorder/punctuate an already fixed, bound field.
    remainder, removed = strip_negative_music(value)
    if not removed:
        return value
    remainder = re.sub(r'\s*[；;]\s*', '；', remainder)
    remainder = re.sub(r'；{2,}', '；', remainder).strip(' ；;，,。\n')
    return (remainder + '；' if remainder else '') + '禁止额外配乐'


def normalize_emphasis_quotes(value: Any, dialogue_rows) -> str | Any:
    """Split a quoted list only when each word binds one actual utterance.

    A literal dialogue fragment containing its own enumeration punctuation
    stays intact. Multi-speaker ownership and repeated lines must still pass
    the authoritative binder; ambiguities are retained for explicit repair.
    """
    if not isinstance(value, str) or not dialogue_rows:
        return value
    from performance_checks import emphasis_binding_faults
    def line(match):
        prefix, body = match.groups()
        if '〔' in body or '〕' in body:
            return match.group()  # The sequence binder owns per-utterance scope.
        changed = False
        def quote(item):
            nonlocal changed
            literal = next(group for group in item.groups() if group is not None)
            if '、' not in literal or any(literal in row[1] for row in dialogue_rows):
                return item.group()
            words = [word.strip() for word in literal.split('、')]
            if len(words) < 2 or not all(words):
                return item.group()
            if not all(any(word in row[1] for row in dialogue_rows) for word in words):
                return item.group()
            changed = True
            return '、'.join('“' + word + '”' for word in words)
        replacement = QUOTE.sub(quote, body)
        if changed and not emphasis_binding_faults(replacement, dialogue_rows):
            return prefix + replacement
        return match.group()
    return EMPHASIS_LINE.sub(line, value)


def normalize_shot_literals(shot: Any, *, path: str = 'shot'):
    """Return an independent candidate and exact, bounded change records."""
    result = copy.deepcopy(shot)
    changes = []
    if not isinstance(result, dict):
        return result, changes
    def replace(field, after, kind):
        before = result.get(field)
        if before != after:
            result[field] = after
            changes.append({'contract': CONTRACT, 'path': path + '.' + field,
                            'kind': kind, 'before': before, 'after': after})
    if '生成限制' in result:
        replace('生成限制', normalize_music_prohibition(result['生成限制']), 'equivalent_music_prohibition')
    picture = result.get('画面内容')
    rows = extract_dialogues(picture)
    voice = result.get('音色')
    if isinstance(voice, str):
        if (not rows and not SPEECH_CUE.search(str(picture or ''))
                and NO_DIALOGUE_ALIASES.fullmatch(voice.strip())):
            replace('音色', NO_DIALOGUE, 'no_dialogue_literal')
        elif rows:
            replace('音色', normalize_emphasis_quotes(voice, rows), 'emphasis_quote_punctuation')
    return result, changes


def normalize_project_literals(project: Any):
    result = copy.deepcopy(project)
    changes = []
    if isinstance(result, dict) and isinstance(result.get('shots'), list):
        for index, shot in enumerate(result['shots']):
            result['shots'][index], records = normalize_shot_literals(shot, path=f'shots[{index}]')
            changes.extend(records)
    return result, changes


def normalize_production_decision_literals(decisions: Any):
    """Normalize only source-linked voice punctuation before compilation.

    Production already emits fixed music/no-dialogue literals. Its remaining
    authored formatting is shots[i].actions[j].voice.emphasis. Source uses
    here provide candidate text bindings only; the compiler must still verify
    the separately frozen graph and all source-realization/plan evidence.
    Nothing in project, initial_world, source uses or source hashes is changed.
    """
    result = copy.deepcopy(decisions)
    changes = []
    if not isinstance(result, dict) or result.get('contract') != 'production-decisions-v1':
        return result, changes
    project = result.get('project')
    evidence = project.get('prevention_evidence') if isinstance(project, dict) else None
    source = evidence.get('source_decisions') if isinstance(evidence, dict) else None
    uses = source.get('uses') if isinstance(source, dict) else None
    if not isinstance(uses, list) or not isinstance(result.get('shots'), list):
        return result, changes
    by_id = {}
    for use in uses:
        if not isinstance(use, dict) or not isinstance(use.get('id'), str) or use['id'] in by_id:
            return result, changes  # Duplicate/malformed bindings remain compiler failures.
        by_id[use['id']] = use
    for i, shot in enumerate(result['shots']):
        actions = shot.get('actions') if isinstance(shot, dict) else None
        if not isinstance(actions, list):
            continue
        for j, action in enumerate(actions):
            if not isinstance(action, dict) or action.get('kind') != 'speak':
                continue
            voice = action.get('voice')
            use_id = action.get('source_use_id')
            use = by_id.get(use_id) if isinstance(use_id, str) else None
            if (not isinstance(voice, dict) or not isinstance(voice.get('emphasis'), str)
                    or not use or use.get('mode') != 'spoken'
                    or not isinstance(use.get('actor'), str) or not use['actor']
                    or not isinstance(use.get('text'), str) or not use['text']):
                continue
            before = voice['emphasis']
            after = normalize_emphasis_quotes('重音：' + before, [(use['actor'], use['text'])])[3:]
            if before != after:
                voice['emphasis'] = after
                changes.append({'contract': CONTRACT,
                                'path': f'shots[{i}].actions[{j}].voice.emphasis',
                                'kind': 'source_bound_emphasis_quote_punctuation',
                                'before': before, 'after': after, 'source_use_id': use_id})
    return result, changes


def normalize_authoring_request(request: Any):
    """Convenience envelope adapter; callers persist originals before use."""
    result = copy.deepcopy(request)
    if not isinstance(result, dict) or result.get('contract') != 'production-authoring-request-v1':
        return result, []
    normalized, changes = normalize_production_decision_literals(result.get('decisions'))
    result['decisions'] = normalized
    return result, [dict(row, path='decisions.' + row['path']) for row in changes]
