"""Bounded prose checks shared by saved-field audit and both renderers.

These checks certify explicit bindings, not unrestricted source understanding.
An unparsed relation is unknown, never proof that the scene is correct.
"""
from __future__ import annotations

import hashlib
import re

QUOTES = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"')
MUSIC = re.compile(r'BGM|背景音乐|额外配乐|配乐|音乐', re.I)
NEGATIVE = re.compile(r'^(?:(?:此镜|本镜头|本镜)\s*)?(?:禁止|不要|不得|不使用|不生成|没有|不加|无)(?:任何)?')
UNCERTAIN = re.compile(r'是否|可能|酌情|视情况|待定|考虑|不一定|如果|倘若|假如|若')
# Cancelling a ban grants no proof that music actually exists. Cancelling
# absence, or requiring its opposite, does assert its presence. Keep these
# operator relationships separate from an ordinary first-order prohibition.
RELAXED_BAN = re.compile(r'(?:没有|并未|未曾|未|不再|不|无须|无需|不必|不需要)(?:再)?(?:禁止|禁用|没有|无)')
REQUIRED_PRESENCE = re.compile(r'(?:不是|并非|不能|不可|不得)(?:完全)?(?:没有|无)')
ACTION = re.compile(r'加入|加上|播放|配上|使用|采用|允许|生成|插入|铺设|搭配|切入|响起|渐入|铺开|伴随|保留|开启|恢复|保持|衬托|烘托|铺底|渐强|渐弱|持续')
# These are predicate forms, not an inventory of permitted visual objects.
# A conjoined assertion must never inherit a prior music item's negation.
ASSERTION = re.compile(ACTION.pattern + r'|清澈|清晰|明亮|柔和|洁净|整洁|稳定|可见|可辨|可读|呈现|处于|位于|仍|已经|不再|没有|禁止|不得|不要|关闭|打开')
BOUNDARY = re.compile(r'([；;。！？!?，,\n]+|但是|然而|不过|而且|并且|随后|然后|而后|接着|同时|但|却|且|又|[并而](?=' + ACTION.pattern + r'))')
ENUM = re.compile(r'(?:以及|或者|[、与和及或])')
MUSIC_ITEM = re.compile(r'(?:任何)?(?:让)?(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)?\s*(?:BGM|背景音乐|额外配乐|配乐|音乐)(?:响起|播放|进入|渐入|切入|铺开|持续|开始)?', re.I)
MUSIC_NEGATIVE_PREDICATE = re.compile(
    r'(?:BGM|背景音乐|额外配乐|配乐|音乐)\s*'
    r'(?:禁止|不得|不要|不应|不能|不会|不)(?:出现|响起|播放|加入|使用|生成)', re.I)


def _complete_negative_music_clause(clause):
    """Prove a complete first-order ban, not merely a nearby negative word.

    Bare coordinated objects share the operator. A second operator, relative
    exception or predicate is not a noun atom and must not inherit that scope.
    This intentionally leaves unsupported prose unresolved for explicit review.
    """
    body = clause.strip()
    if MUSIC_NEGATIVE_PREDICATE.fullmatch(body):
        return True
    prefix = NEGATIVE.match(body)
    if prefix is None:
        return False
    items = ENUM.split(body[prefix.end():])
    if not items or any(not item.strip() for item in items):
        return False
    for item in items:
        item = item.strip()
        if MUSIC.search(item):
            if not MUSIC_ITEM.fullmatch(item):
                return False
        elif (not re.fullmatch(r'[\w\u3400-\u9fff· -]+', item)
              or re.search(r'不|未|无|禁止|不得|不要|要求|允许|取消|撤销|除|之外|以外|'
                           r'仅|只|的|之后|以后|停止|保持|执行|若|如果', item)
              or ASSERTION.search(item)):
            return False
    return True


def _masked_quotes(text):
    return QUOTES.sub(lambda m: ' ' * len(m.group()), text)


def mask_static_eye_exclusions(value):
    """Mask only explicit excluded still states, keeping every other action.

    A negated half-blink freeze frame is not an eyelid animation. Conditional
    or cancelled prohibitions do not establish this exception. Same-length
    masking preserves evidence offsets and does not alter stored prose.
    """
    text = str(value or '')
    pattern = re.compile(
        r'(?:避开|避免|排除|不处于|不是|不生成|不定格[于在]|禁止定格[于在])'
        r'(?:半眨眼?|半闭眼|闭眼)(?:瞬间|状态|中途|截帧)?')
    result = list(text)
    for clause in re.finditer(r'[^，,。；;\n]+', text):
        body = clause.group()
        if re.search(r'如果|倘若|假如|若|是否|可能|不一定', body):
            continue
        for match in pattern.finditer(body):
            lead = body[:match.start()]
            if re.search(r'(?:并非|不是|不能|不可|不得|没有|无需|无须|不必|取消|撤销|未|不)'
                         r'(?:再|继续|执行|要求|采用|需要|去|要|来|予以|进行|\s)*$', lead):
                continue
            start, end = clause.start() + match.start(), clause.start() + match.end()
            result[start:end] = ' ' * (end - start)
    return ''.join(result)


def music_directives(value):
    """Classify each music occurrence without treating a residual noun as an act.

    Explicit negative noun enumerations inherit scope without a vocabulary of
    permitted visual objects. A new predicate or contrast resets that scope.
    Negation of absence and negation of a ban have different meanings;
    uncertain permissions and modal/conditional instructions stay unresolved.
    """
    original = str(value or '')
    text = _masked_quotes(original)
    records = []
    offset = 0
    for clause in BOUNDARY.split(text):
        for match in MUSIC.finditer(clause):
            prefix = clause[:match.start()]
            body = clause.strip()
            own = re.split(r'[、与和及或]', prefix)[-1].lstrip()
            neg = NEGATIVE.match(body)
            restart = ACTION.search(own)
            # A direct ban may contain its own verb; a later predicate may not
            # borrow a ban from the beginning of a different enumeration item.
            scoped = bool(neg and (not restart or match.start() < neg.end() + len(own)))
            if neg and restart and MUSIC.search(prefix[neg.end():]):
                # A later predicate after an already complete music object
                # starts a new instruction, even without a punctuation mark.
                scoped = False
            if neg and ENUM.search(prefix[neg.end():]):
                scoped = (not restart or restart.start() == 0) and not re.search(r'则|时|之后|以后|即可', prefix)
            local_negative = re.search(r'(?:禁止|不要|不得|不使用|不生成|没有|不加|无)(?:任何)?'
                                       r'(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)?\s*$', prefix)
            negative_predicate = re.match(r'\s*(?:禁止|不得|不要|不应|不能|不会|不)(?:出现|响起|播放|加入|使用|生成)',
                                          clause[match.end():])
            scoped = scoped or bool(local_negative or negative_predicate)
            if UNCERTAIN.search(body) or RELAXED_BAN.search(body) or re.search(r'(?:禁止|不加).{0,20}(?:时|后|则)', body):
                status = 'unknown'
            elif REQUIRED_PRESENCE.search(body):
                status = 'affirmative'
            elif scoped:
                status = 'negative' if _complete_negative_music_clause(body) else 'unknown'
            elif ACTION.search(body):
                status = 'affirmative'
            else:
                status = 'unknown'
            records.append({'status': status, 'text': original[offset:offset + len(clause)],
                            'span': [offset + match.start(), offset + match.end()],
                            'term': match.group()})
        offset += len(clause)
    return records


def strip_negative_music(value):
    """Remove only proved music-ban items, preserving arbitrary visual nouns."""
    text = str(value or '')
    protected = {}
    def protect(match):
        token = '\ue010Q' + str(len(protected)) + '\ue011'
        protected[token] = match.group()
        return token
    text = QUOTES.sub(protect, text)
    mentioned = False
    parts = BOUNDARY.split(text)
    removed = set()
    for index, clause in enumerate(parts):
        records = music_directives(clause)
        if not records or any(row['status'] != 'negative' for row in records):
            continue
        prefix = NEGATIVE.match(clause.strip())
        if not prefix:
            if MUSIC_NEGATIVE_PREDICATE.fullmatch(clause.strip()):
                parts[index] = ''
                removed.add(index)
                mentioned = True
            continue
        items = ENUM.split(clause.strip()[prefix.end():])
        kept = [item for item in items if not MUSIC_ITEM.fullmatch(item.strip())]
        if len(kept) == len(items):
            continue
        mentioned = True
        parts[index] = prefix.group() + '、'.join(kept) if kept else ''
        if not kept:
            removed.add(index)
            # Coordinated bare objects share the original negative operator;
            # a full assertion or its own operator starts a separate clause.
            if index + 2 < len(parts) and parts[index + 1] in {'且', '而且', '并且'}:
                following = parts[index + 2].strip()
                if (following and not NEGATIVE.match(following) and not ASSERTION.search(following)
                        and not UNCERTAIN.search(following) and not REQUIRED_PRESENCE.search(following)
                        and not RELAXED_BAN.search(following)):
                    parts[index + 2] = prefix.group() + following
    # Only connectors adjacent to an actually removed music atom disappear.
    # Preserve every unrelated conjunction and all original punctuation.
    for index in removed:
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(parts) and parts[neighbor] and not re.fullmatch(r'[；;。！？!?，,\n]+', parts[neighbor]):
                parts[neighbor] = ''
    result = ''.join(parts)
    for token, quote in protected.items():
        result = result.replace(token, quote)
    return result, mentioned


def field_sha256(value):
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def labelled_sections(value, labels):
    pattern = re.compile(r'(?m)^\s*(' + '|'.join(map(re.escape, labels)) + r')\s*[:：]')
    text = str(value or '')
    matches = list(pattern.finditer(text))
    result = {}
    duplicates = []
    for index, match in enumerate(matches):
        label = match.group(1)
        if label in result:
            duplicates.append(label)
        result[label] = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
    return result, duplicates


def tail_binding_faults(value, dialogue_rows):
    """A quoted tail must be an ending of this utterance, not another line."""
    text = str(value or '')
    quotes = [m.group()[1:-1] for m in QUOTES.finditer(text)]
    if not quotes:
        return ['尾音未绑定本句实际末字或末词；通用收尾方向不能代替本句执行定位']
    endings = [line.rstrip('。！？!?；;，,、 .…～~“”"‘’「」『』') for _, line in dialogue_rows]
    return [f'尾音“{fragment}”不是当前发言的实际句末' for fragment in quotes
            if not fragment or not any(line.endswith(fragment) for line in endings)]


def explicit_state_conflicts(initial, projected, names):
    """Compare explicit per-person first-frame predicates; never infer a pose."""
    states = {'eye': ((r'闭眼|合眼|(?:双眼|眼睛)[^，。；;]{0,6}(?:合拢|闭合)', 'closed'),
                      (r'睁眼|眼睛[^，。；;]{0,6}睁开|双眼[^，。；;]{0,6}睁开', 'open')),
              'posture': ((r'坐着|坐姿|坐在|坐于|落座|坐稳', 'seated'),
                          (r'站着|站姿|站在|站于|站立', 'standing'))}
    def claims(value, person, patterns):
        found = set()
        owner = None
        for clause in re.split(r'[；;。，,\n]', _masked_quotes(str(value or ''))):
            named = [(match.start(), name) for name in names
                     for match in [re.search(r'(?:^|：|:)\s*' + re.escape(name), clause)] if match]
            if named:
                owner = min(named)[1]
            if owner != person:
                continue
            for pattern, state in patterns:
                for match in re.finditer(pattern, clause):
                    if not re.search(r'不|未|禁止|避免|避开', clause[max(0, match.start() - 5):match.start()]):
                        found.add(state)
        return found
    faults = []
    for name in names:
        for kind, patterns in states.items():
            before, after = claims(initial, name, patterns), claims(projected, name, patterns)
            if len(before) == 1 and after and after != before:
                faults.append(f'{name}的{kind}静态起态冲突：E={sorted(before)}，F={sorted(after)}')
    # An unqualified rule applies to all visible eyes, including a closed one.
    if re.search(r'(?:所有|全部|入画|可见)(?:人物)?(?:的)?(?:双眼|眼睛)[^；。\n]{0,12}(?:自然)?睁开', projected):
        for name in names:
            if claims(initial, name, states['eye']) == {'closed'}:
                faults.append(f'{name}明确闭眼起态与F统一睁眼要求冲突')
    return faults


def environment_phase_findings(shot, names):
    """Scene-level prose cannot assert an actor event absent from this shot."""
    environment = _masked_quotes(str(shot.get('环境动态') or ''))
    picture = _masked_quotes(str(shot.get('画面内容') or ''))
    initial = str(shot.get('起始状态') or '')
    groups = ((r'起身|站起', r'坐着|坐姿|坐稳|保持坐'),
              (r'急步|行走|走动|迈步', r'停步|站定|停住|站稳'))
    findings = []
    for name in names:
        for action, stationary in groups:
            trigger = re.search(re.escape(name) + r'[^，；。\n]{0,12}(?:' + action + ')', environment)
            if not trigger:
                continue
            event = re.search(re.escape(name) + r'[^，；。\n]{0,16}(?:' + action + ')', picture)
            if event:
                continue
            stable = re.search(re.escape(name) + r'[^，；。\n]{0,16}(?:' + stationary + ')', initial + '；' + picture)
            findings.append({'status': 'fail' if stable else 'unknown', 'message':
                f'环境动态引用{name}的动作“{trigger.group()}”，本镜画面无对应事件' + ('且已有静止状态' if stable else '，需逐阶段绑定')})
    return findings
