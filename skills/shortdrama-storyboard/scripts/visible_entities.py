"""Resolve visible entity types from declarations, never from name length.

This is input mapping, not semantic certification. An untyped label stays
unknown; it must not acquire eyes merely because it is a short Chinese word.
Silent actors remain actors through registered identities or owned anatomy.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence

BODY_PART_PATTERN = r"面部|眼睛|双眼|眼部|侧脸|上半身|下半身|全身|双手|左手|右手|手部|手腕|手指|食指|肩缘|肩颈|肩背|肩部|脖颈|腰部|胸部|胸腹|后脑|背影|侧背|轮廓"
ANATOMY = r"面部|眼睛|双眼|眼部|侧脸|上半身|下半身|双手|左手|右手|手部|手腕|手指|肩缘|肩颈|肩背|肩部|脖颈|胸腹|后脑|背影|侧背|眼球|眼睑|视线|目光"
EMPTY = re.compile(r"^(?:无|无人|无人物|(?:无人物|无人|没有人物|没有人)入画|不适用)$")
COLLECTIVE = re.compile(r"^(?:三人|两人|二人|多人|众人|所有人|双方|各人|每人|背景人群|普通人群|远处行人|背景生态)$")
QUOTE = re.compile(r'“[^”\n]*”|‘[^’\n]*’|"[^"\n]*"|「[^」\n]*」|『[^』\n]*』')


@dataclass(frozen=True)
class VisibleEntities:
    people: tuple[str, ...]
    non_people: tuple[str, ...]
    unknown: tuple[str, ...]
    descriptions: dict[str, str]
    conflicts: tuple[str, ...] = ()
    background_people: tuple[str, ...] = ()

    @property
    def faults(self) -> list[str]:
        result = [f"input_mapping：可见主体“{name}”身份类型未登记或未明确，须核验人物/道具/环境映射；不能按人物补写眼神或眨眼" for name in self.unknown]
        result.extend(f"input_mapping：可见主体“{name}”的人物与非人物类型证据冲突，须核验原始身份映射" for name in self.conflicts)
        return result


def visible_subject_tokens(value: Any) -> list[str]:
    text = str(value or ""); stack = []; spans = []
    for index, char in enumerate(text):
        if char in "(（": stack.append((char, index))
        elif char in ")）":
            if stack and stack[-1][0] == {")": "(", "）": "（"}[char]:
                _, start = stack.pop(); spans.append((start, index))
            else: stack.clear()
    protected = {i for a, b in spans for i in range(a, b + 1)}
    result = []; start = index = 0
    while index < len(text):
        char = text[index]; connector = "以及" if text.startswith("以及", index) else char
        if index not in protected and (char in "、，,；;+＋" or connector in {"以及", "和", "与", "及"} and text[start:index].strip()):
            if text[start:index].strip(): result.append(text[start:index].strip())
            index += len(connector); start = index; continue
        index += 1
    if text[start:].strip(): result.append(text[start:].strip())
    return result


def _names(values: Iterable[Any] | None) -> set[str]:
    return {v.strip() for v in (values or ()) if isinstance(v, str) and v.strip()}


def resolve_visible_subjects(value: Any, *, people: Sequence[str] | None = None,
                             non_people: Sequence[str] | None = None,
                             speakers: Sequence[str] | None = None,
                             evidence_texts: Sequence[Any] = ()) -> VisibleEntities:
    registered = _names(people); objects = _names(non_people); spoken = _names(speakers)
    texts = [QUOTE.sub("〈引文〉", str(t or "")) for t in evidence_texts]
    tokens = visible_subject_tokens(value)
    declared_no_people = any(re.fullmatch(r"(?:无人|无人物|(?:无人物|无人|没有人物|没有人)入画)", token) for token in tokens)
    found = []; props = []; unknown = []; conflicts = []; backgrounds = []; descriptions = {}; owner = None
    for token in tokens:
        label = re.sub(r"^(?:画面|前景|中景|后景|左侧|右侧|中央|左边|右边)[:：\s]*", "", token.strip())
        label = re.sub(r'^(?:全景|近景|中景|远景)(?:中|内|里)的?', '', label)
        if re.match(r"^(?:" + BODY_PART_PATTERN + r")(?:清楚|清晰|完整|自然|可见|不可见|被遮挡|全程|始终|仅在|食指|与|和|及|、|$)", label):
            if owner: descriptions[owner] += "、" + token
            else: unknown.append(token)
            continue
        declared = re.match(r"^(人物|角色|道具|物件|环境|场景)[：:]\s*(.+)$", label)
        kind = declared.group(1) if declared else None
        if declared: label = declared.group(2)
        name = re.split(r"[（(]|(?:的)?(?:" + BODY_PART_PATTERN + r"|仅|只|背向|背对)", label, maxsplit=1)[0].strip()
        if re.fullmatch(r'[一二两三四五六七八九十\d]+(?:名|位)?(?:人物|人)', name):
            unknown.append(name)
            owner = None
            continue
        name = re.sub(r"(?:等|共)?[一二两三四五六七八九十\d]+(?:名|位)?(?:人物|人)$", "", name).strip()
        owner = None
        if not name or EMPTY.fullmatch(name): continue
        if re.search(r'背景(?:人群|人物)|普通(?:人群|人物)|远处行人|背景生态', name):
            backgrounds.append(name)
            continue
        if COLLECTIVE.fullmatch(name):
            unknown.append(name)
            continue
        annotation = label[len(name):]
        # A person owns anatomy; a gaze target such as “许氏目光落在杯盖”
        # supplies no type evidence for 杯盖. Quoted speech is also excluded.
        own_annotation = re.sub(r'^[（(\s]*(?:的)?', '', annotation)
        own_anatomy = bool(re.match(r'(?:仅|只|近侧|远侧|左侧|右侧|后侧|前侧|清楚|清晰)*(?:' + ANATOMY + r')', own_annotation)) or any(
            re.search(r"(?:^|[；;，,。！？!?\n】])\s*" + re.escape(name) + r"(?:随后|此时|同时|先|再)?(?:的)?(?:" + ANATOMY + r")", t) for t in texts)
        human = name in registered or name in spoken or kind in {"人物", "角色"} or own_anatomy
        nonhuman = name in objects or kind in {"道具", "物件", "环境", "场景"} or declared_no_people
        if human and nonhuman: conflicts.append(name)
        elif human:
            found.append(name); owner = name
            descriptions[name] = descriptions.get(name, "") + ("、" if name in descriptions else "") + token
        elif nonhuman: props.append(name)
        else: unknown.append(name)
    unique = lambda rows: tuple(dict.fromkeys(rows))
    counts = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
              '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    for match in re.finditer(r'(?:等|共)([一二两三四五六七八九十]|\d+)(?:名|位)?(?:人物|人)', str(value or '')):
        token = match.group(1)
        count = int(token) if token.isdigit() else counts[token]
        if count > len(unique(found)):
            unknown.append(f'声明{count}人但仅绑定{len(unique(found))}位人物')
    if re.search(r'等人|等多人|若干人|多名人物', str(value or '')):
        unknown.append('未逐位绑定的人物集合')
    return VisibleEntities(unique(found), unique(props), unique(unknown), descriptions, unique(conflicts), unique(backgrounds))


def project_entity_registry(project: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Read only existing typed containers; never mine arbitrary prose names."""
    people = set(); props = set()
    def anchors(value, target, keys):
        if isinstance(value, dict): target.update(_names(value))
        elif isinstance(value, list):
            for row in value:
                if isinstance(row, str): target.add(row)
                elif isinstance(row, dict):
                    for key in keys:
                        if isinstance(row.get(key), str): target.add(row[key]); break
    anchors(project.get('character_anchors'), people, ('character', 'name', 'character_id', 'actor_id', 'id'))
    anchors(project.get('prop_anchors'), props, ('prop', 'name', 'prop_id', 'id'))
    anchors(project.get('scene_anchors'), props, ('scene', 'name', 'scene_id', 'id'))
    evidence = project.get('prevention_evidence') or {}
    if isinstance(evidence, dict):
        anchors(evidence.get('cognition_matrix'), people, ('character',))
        derivation = evidence.get('production_derivation') or {}
        data = derivation.get('input') if isinstance(derivation, dict) else None
        if isinstance(data, dict):
            world = data.get('initial_world') or {}
            if isinstance(world, dict):
                anchors(world.get('actors'), people, ('actor_id',))
                anchors(world.get('props'), props, ('prop_id',))
                anchors(world.get('points'), props, ('point_id',))
    intake = project.get('source_ingestion') or {}
    if isinstance(intake, dict): anchors(intake.get('character_state_changes'), people, ('character',))
    return sorted(people), sorted(props)
