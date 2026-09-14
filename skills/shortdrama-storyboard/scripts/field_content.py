"""Shared field ownership checks and fixed R5 keyframe section routing.

The registry defines ownership. These deterministic checks identify supported
role violations; they never certify source interpretation or natural performance.
The production compiler supplies camera/state/light values from typed decisions.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re


REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "references"
CONTRACT = "storyboard-field-content-v1"
ERROR_CODE = "E-FIELD-CONTENT-001"


@lru_cache(maxsize=1)
def field_policy():
    registry = json.loads((REFERENCE_ROOT / "rule_registry.json").read_text(encoding="utf-8"))
    policy = registry["runtime"]["field_content_policy"]
    if policy["contract"] != CONTRACT:
        raise ValueError("field_content_policy: unsupported field ownership contract")
    return policy


class FieldContentError(ValueError):
    def __init__(self, findings):
        self.findings = findings
        super().__init__("field_content_violation: " + "; ".join(
            f"{row['path']}: {row['message']} [{row['excerpt']}]" for row in findings))


def _instruction_text(value, field, mask):
    text = str(value or "")
    # Only the caller's typed written/speech parser can grant literal scope.
    text = mask(text, field) if mask is not None else text
    if field == "画面内容":
        # An eye/mouth action may refer to an actual speech event. This masks
        # only the event label, never the following delivery or action clause.
        text = re.sub(r"(?:重音|尾音)(?=期间|结束后|落下后)",
                      lambda m: " " * len(m.group()), text)
        text = re.sub(r"重音(?=\s*(?:“[^”]*”|‘[^’]*’|字|词)\s*(?:期间|时|上|在该窗口)\s*不眨眼)",
                      lambda m: " " * len(m.group()), text)
    return text


def _findings_for_value(field, value, *, mask, subfield=None, dialogue_rows=(), context=None):
    policy = field_policy()
    spec = policy["voice_subfields"][subfield] if subfield else policy["fields"][field]
    original = str(value or "")
    text = _instruction_text(original, field, mask)
    if subfield in {"重音", "停顿"}:
        # A genuine quoted dialogue span is data, not a camera/voice directive.
        for match in re.finditer(r'“([^”]*)”|「([^」]*)」|"([^"\n]*)"', text):
            literal = next((v for v in match.groups() if v is not None), "")
            if literal and any(literal in utterance for _, utterance in dialogue_rows):
                text = text[:match.start()] + " " * (match.end() - match.start()) + text[match.end():]
    path = field + ("." + subfield if subfield else "")
    found = []
    clause_patterns = policy.get('owned_clause_patterns', {}).get(path)
    if clause_patterns is not None:
        for match in re.finditer(r'[^。；;，,\n]+', text):
            clause = match.group().strip()
            if clause and not any(re.fullmatch(pattern, clause, re.I) for pattern in clause_patterns):
                found.append({'code': ERROR_CODE, 'path': path, 'role': 'unsupported_owned_clause',
                              'status': 'not_supported', 'excerpt': original[match.start():match.end()],
                              'offset': [match.start(), match.end()],
                              'message': path + '子句不属于已登记的完整字段语法；已有正确子句不能为其余正文放行',
                              'repair': '保留输入并回到对应决定；合法但未覆盖的表达须登记语法和正反例，不自动删句或猜测改写'})
    if field == '机位' and subfield is None:
        # Every clause must match supported camera syntax. A known word in one
        # clause does not license unrelated text elsewhere in the field.
        for match in re.finditer(r'[^。；;，,\n]+', text):
            clause=match.group().strip()
            focus = re.fullmatch(r'焦点(?:优先)?(?:落)?(?:在|对准)([\w\u3400-\u9fff·-]{1,12})', clause)
            subjects = re.split(r'[，,；;、。\n]', str((context or {}).get('可见主体') or ''))
            named_focus = focus is not None and any(focus[1] == subject.strip() or re.match(
                re.escape(focus[1]) + r'(?:的)?(?:面部|眼睛|双眼|手部|身体|上半身|下半身|全身|可见|[：:（(])', subject.strip())
                for subject in subjects)
            if clause and not named_focus and not any(re.fullmatch(pattern,clause,re.I)
                                  for pattern in policy['camera_clause_patterns']):
                found.append({'code':ERROR_CODE,'path':path,'role':'unsupported_camera_clause',
                              'excerpt':original[match.start():match.end()], 'offset':[match.start(),match.end()],
                              'message':'机位子句不属于支持的摄影机位置、角度、镜头或对焦语法；不得把未知内容判为所属字段正确',
                              'repair':'使用结构化机位生成；真实但未支持的摄影表达须明确登记，不能自动删句或补写剧情'})
    heading_policy = policy.get("embedded_heading_policy", {})
    if heading_policy.get("forbid_foreign_field_headings"):
        headings = set(policy["fields"]) | set(policy["voice_subfields"]) | {
            row["label"] for row in policy["keyframe_sections"]}
        allowed = {subfield or field} | set(heading_policy.get("exceptions", {}).get(field, []))
        foreign = sorted(headings - allowed, key=len, reverse=True)
        pattern = r"(?:^|[；;。\n])\s*(?P<label>" + "|".join(map(re.escape, foreign)) + r")\s*[:：]"
        for match in re.finditer(pattern, text):
            found.append({"code": ERROR_CODE, "path": path, "role": "foreign_field_heading",
                          "excerpt": original[match.start():match.end()],
                          "offset": [match.start(), match.end()],
                          "message": path + "不能嵌入其他字段或子项标题：" + match.group("label"),
                          "repair": "回到对应字段的来源决定修正；不要将整段标题正文塞入另一个字段"})
    for role in spec.get("forbidden_roles", []):
        for expression in policy["role_patterns"][role]:
            for match in re.finditer(expression, text, flags=re.I):
                found.append({"code": ERROR_CODE, "path": path,
                              "role": role, "excerpt": original[match.start():match.end()],
                              "offset": [match.start(), match.end()],
                              "message": f"{path}只允许{spec['allows']}；此片段属于{policy['role_labels'][role]}",
                              "repair": "回到有依据的所属决定修正并重建相关输出；不要静默删句、搬列或补造事实"})
    return found


def voice_parts(value):
    """Preserve ordered multi-speaker text within the existing R5 labels."""
    labels = list(field_policy()["voice_subfields"])
    pattern = re.compile(r"(?m)^\s*(" + "|".join(map(re.escape, labels)) + r")\s*[:：]")
    text = str(value or "")
    matches = list(pattern.finditer(text))
    return [(m.group(1), text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip())
            for i, m in enumerate(matches)]


def voice_content_findings(value, *, dialogue_rows=(), mask=None):
    findings = []
    parts = voice_parts(value)
    if not parts:
        # The established voice-format validator reports missing/invalid labels.
        return _findings_for_value("音色", value, mask=mask)
    first_label = re.search(r"(?m)^\s*(?:" + "|".join(map(re.escape, field_policy()["voice_subfields"])) + r")\s*[:：]", str(value))
    if first_label and str(value)[:first_label.start()].strip():
        findings.append({"code": ERROR_CODE, "path": "音色", "role": "unlabelled",
                         "excerpt": str(value)[:first_label.start()].strip(), "offset": [0, first_label.start()],
                         "message": "音色不接受固定标签之外的前置正文", "repair": "把发声决定填写在对应子项"})
    for label, body in parts:
        findings.extend(_findings_for_value("音色", body, mask=mask,
                                           subfield=label, dialogue_rows=dialogue_rows))
        if label == "发声方式" and body:
            findings.append({"code": ERROR_CODE, "path": "音色.发声方式", "role": "unlabelled",
                             "excerpt": body, "offset": [0, len(body)],
                             "message": "发声方式只能作为六个发声子项的分组标题，不能追加无归属正文",
                             "repair": "按实际发声决定填写对应子项"})
    return findings


def field_content_findings(shot, *, mask=None, dialogue_rows=()):
    if not isinstance(shot, dict):
        return []  # Structural schema checks own object/type failures.
    policy = field_policy()
    if not dialogue_rows:
        # The full validator supplies its authoritative dialogue parser. Direct
        # render callers retain narrowly marked speech literals for role checks;
        # source/speaker fidelity is still checked by the full validator.
        dialogue_rows = [("", m.group(1)) for m in re.finditer(
            r'(?:说道|问道|答道|喊道|对白|台词|[\u3400-\u9fff]{1,12}\s*[:：])\s*[:：]?\s*“([^”]*)”',
            str(shot.get("画面内容") or ""))]
    findings = []
    for field, spec in policy["fields"].items():
        if field not in shot or spec.get("verbatim_source"):
            continue
        if field not in {"时长", "累计时长"} and not isinstance(shot[field], str):
            findings.append({"code": ERROR_CODE, "path": field, "role": "non_text",
                             "excerpt": repr(shot[field])[:160], "offset": [0, 0],
                             "message": field + "必须是所属字段的文本，不能用数值、数组或对象代替",
                             "repair": "按该字段合同提交实际内容；不要自动转字符串掩盖错误类型"})
            continue
        if field == "音色":
            findings.extend(voice_content_findings(shot[field], mask=mask, dialogue_rows=dialogue_rows))
        else:
            findings.extend(_findings_for_value(field, shot[field], mask=mask, context=shot))
    unique = {}
    for row in findings:
        unique[(row["path"], row["role"], tuple(row["offset"]))] = row
    return list(unique.values())


def assert_field_content(shot, *, mask=None, dialogue_rows=()):
    findings = field_content_findings(shot, mask=mask, dialogue_rows=dialogue_rows)
    if findings:
        raise FieldContentError(findings)


def appearance_anchor_text(value):
    """Only explicitly labelled appearance prose belongs in image instructions.

    Image IDs, attachment status and source citations stay in column G.
    Unlabelled legacy references are retained there, never guessed as appearance.
    """
    text = str(value or "").strip()
    marker = re.search(r"(?m)^文字造型锚\s*[:：]\s*", text)
    if marker is None:
        return ""
    tail = text[marker.end():]
    boundary = re.search(r"(?m)^(?:素材绑定|图片绑定状态|来源依据|绑定状态|参考图ID)\s*[:：]", tail)
    result = (tail[:boundary.start()] if boundary else tail).strip()
    if not result or re.search(r"未附图|未绑定|待绑定|附件状态|(?:图片|文件)ID\s*[:：]", result, re.I):
        raise ValueError("field_content_violation: 参考图锚定.文字造型锚缺失或混入素材绑定状态")
    return result


def assert_static_text_preserved(source, projected, field):
    """Punctuation formatting is allowed; silently dropping a clause is not."""
    normalize = lambda text: re.sub(r"[\s。；;，,：:]", "", str(text or ""))
    if normalize(source) != normalize(projected):
        raise ValueError("field_content_violation: " + field +
                         "含有无法直接投影到起始帧的内容；请修正所属决定，不能静默删句：" + str(source)[:220])


def model_field_contract():
    """Compact current rules for authenticated clients, from the sole registry."""
    policy = field_policy()
    return {"contract": CONTRACT,
            "fields": {name: row["allows"] for name, row in policy["fields"].items()},
            "voice_subfields": {name: row["allows"] for name, row in policy["voice_subfields"].items()},
            "keyframe_sections": policy["keyframe_sections"],
            "embedded_heading_policy": policy["embedded_heading_policy"],
            "owned_clause_patterns": policy.get("owned_clause_patterns", {}),
            "speaking_gaze_policy": policy.get("speaking_gaze_policy"),
            "workbook": {"formal_sheet": "正式分镜母版", "reading_sheets": "每集_逐镜阅读", "source_sheet": "使用与来源", "visible_columns": 10},
            "new_production_entry": policy["new_production_entry"],
            "legacy_canonical": "兼容候选仍执行全部字段检查；不因此视为已验证结构化生成",
            "limits": "已支持的字段结构和角色检查；原文理解、表演选择等开放语义仍须独立审查；不能据此声称98%"}


def r5_keyframe_sections(values, *, aspect, visual_limits, appearance_anchor):
    """Route already validated, static values; never copy a full video field.

    The caller owns initial-state and literal validation. No environment motion,
    sound, voice, transition or audit metadata is an input to this renderer.
    """
    result = []
    for section in field_policy()["keyframe_sections"]:
        label = section["label"]
        if label == "画幅":
            value = aspect
        elif label == "视觉限制":
            value = visual_limits
        elif label == "文字造型锚":
            value = appearance_anchor
        else:
            value = "；".join(str(values.get(key) or "").strip() for key in section["sources"]
                             if str(values.get(key) or "").strip())
        if section.get("optional") and not value:
            continue
        result.append((label, value))
    return result
