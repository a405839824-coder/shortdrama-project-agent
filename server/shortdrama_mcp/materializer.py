from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable


FORMAL_MERGED_FIELD = "正式分镜母版"
LEGACY_FORMAL_MERGED_FIELD = "正式分镜母版·字段合并"
GPT_KEYFRAME_FIELD = "GPT关键帧生成指令"

# Kept as an import-compatible legacy symbol. Compact-master canonicals never
# persist or project this field.
SEEDANCE_EXECUTION_FIELD = "Seedance 2.5独立正式执行分镜"

COMPACT_SCHEMA_VERSION = "R10.1"

COMPACT_SHOT_FIELDS = [
    "镜头编号",
    "时长",
    "累计时长",
    "功能类型",
    "场景连续性ID",
    "镜头类型",
    "可见主体",
    "空间站位描述",
    "起始状态",
    "机位",
    "运镜方式",
    "画面内容",
    "音色",
    "心理音轨",
    "环境音效",
    "环境动态",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
    "生成限制",
    "转场类型",
    "切镜/转场提示词",
    "参考图锚定",
    "覆盖原文",
    "原文依据类型",
    "原文支撑句",
]

MOTHER_TEMPLATE_COLUMNS = [
    "分镜序号",
    "时长",
    "累计时长",
    "功能类型/场景连续性ID",
    FORMAL_MERGED_FIELD,
    GPT_KEYFRAME_FIELD,
    "参考图锚定",
    "覆盖原文",
    "原文依据类型",
    "原文支撑句",
]

# Narrative execution fields are listed here; the shot/timing/function header
# is added explicitly above them to match the approved formal-cell template.
FORMAL_MERGED_ATOMIC_FIELDS = (
    "镜头类型",
    "可见主体",
    "空间站位描述",
    "起始状态",
    "机位",
    "运镜方式",
    "画面内容",
    "音色",
    "心理音轨",
    "环境音效",
    "环境动态",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
    "生成限制",
    "转场类型",
    "切镜/转场提示词",
)

GPT_KEYFRAME_ATOMIC_FIELDS = (
    "镜头类型",
    "可见主体",
    "空间站位描述",
    "起始状态",
    "机位",
    "运镜方式",
    "画面内容",
    "环境动态",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
    "参考图锚定",
    "生成限制",
)

# Legacy v0.4 public constants remain available to read old workbooks and
# fixtures. No compact-master code path calls ``compile_seedance_execution``.
SEEDANCE_ATOMIC_FIELDS = (
    ("起始画面", "起始画面"),
    ("起始表演状态", "起始表演状态"),
    ("动作过程", "动作过程"),
    ("听话反应", "听话反应"),
    ("结束状态", "结束状态"),
    ("摄影机", "摄影机"),
    ("声音", "声音"),
    ("环境动态", "环境动态"),
    ("光影", "光影"),
    ("生成限制", "生成限制"),
)

INTERNAL_SHOT_FIELDS = ("shot_uid", "episode_id", "scene_uid")

LEGACY_STANDALONE_FIELDS = {
    "运镜主体",
    "运镜功能",
    "叙事功能",
    "人物调度",
    "动作与表演",
    "台词",
    "发声方式",
    "标点语气",
    "心理音轨属性",
    "生成批次",
}

PERSISTED_DERIVED_FIELDS = {
    FORMAL_MERGED_FIELD,
    LEGACY_FORMAL_MERGED_FIELD,
    GPT_KEYFRAME_FIELD,
    SEEDANCE_EXECUTION_FIELD,
    "Seedance 2.5独立执行分镜",
    "Seedance 2.5独立执行提示词",
    "独立执行提示词",
}

LEGACY_SHOT_INPUT_FIELDS = (
    set(COMPACT_SHOT_FIELDS)
    | set(INTERNAL_SHOT_FIELDS)
    | LEGACY_STANDALONE_FIELDS
    | PERSISTED_DERIVED_FIELDS
    | {
        "切镜转场提示词",
        "光源",
        "光线方向",
        "色温",
    }
)

DEPRECATED_DISPLAY_LABELS = (
    "运镜主体：",
    "运镜功能：",
    "叙事功能：",
    "人物调度：",
    "动作与表演：",
    "台词：",
    "发声方式：",
    "标点语气：",
    "心理音轨属性：",
)

# Retired labels are structural only inside the fields that formerly absorbed
# those split columns. Evidence and reference fields must remain byte-faithful
# even when the quoted source itself contains words such as “台词：”.
DEPRECATED_LABEL_CONTAINER_FIELDS = (
    "运镜方式",
    "空间站位描述",
    "起始状态",
    "画面内容",
    "心理音轨",
)

# Retired structural headings must never reappear in a newly written formal
# master.  Source/evidence cells are deliberately excluded: quoted original
# prose must remain byte-faithful even when it literally contains “台词：”.
DEPRECATED_LABEL_REJECTION_FIELDS = tuple(
    field
    for field in COMPACT_SHOT_FIELDS
    if field not in {"参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句"}
)

_TIMECODE_RE = re.compile(
    r"(?:\[|【)?\s*\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*"
    r"\d+(?:\.\d+)?\s*(?:秒|s)\s*(?:\]|】|[:：])?",
    flags=re.IGNORECASE,
)
_BRACKETED_DIALOGUE_RE = re.compile(
    r"【[^】\n]{1,40}】\s*[^。！？!?；;\n]*(?:[。！？!?；;]|$)"
)
_QUOTED_DIALOGUE_RE = re.compile(r"[“\"「『][^”\"」』\n]*[”\"」』]")
_AUDIO_ONLY_CLAUSE_MARKERS = (
    "口型",
    "说话时",
    "开口",
    "对白",
    "动作声",
    "环境声",
    "脚步",
    "问句",
    "陈述句",
    "感叹句",
    "对白节奏",
    "声音强弱",
    "语速节奏",
    "呼吸状态",
    "停顿位置",
    "重音位置",
    "尾音变化",
    "声量",
    "语速",
    "停顿",
    "重音",
    "尾音",
    "发声",
    "对白节奏",
    "环境音",
    "音效",
    "配乐",
    "音乐",
    "BGM",
    "声音",
    "鸟声",
    "脚步声",
    "呼吸声",
    "衣料声",
    "器物声",
    "叶声",
    "鸟鸣",
    "虫鸣",
    "风声",
    "声线",
    "音轨",
    "音频",
)

_LIGHT_SUBFIELD_LABELS = (
    "光源来源：",
    "光源方向：",
    "色温倾向：",
    "光源：",
    "光线方向：",
    "色温：",
)

_LIGHT_EFFECT_MARKERS = (
    "明暗",
    "曝光",
    "高光",
    "阴影",
    "轮廓",
    "层次",
    "质感",
    "反光",
    "不过曝",
)


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return str(value).strip()


def _labeled_lines(
    record: dict[str, Any],
    fields: Iterable[str],
    *,
    heading: str = "",
    transforms: dict[str, Any] | None = None,
) -> str:
    lines = [heading] if heading else []
    transforms = transforms or {}
    for field in fields:
        value = _text(record.get(field))
        transform = transforms.get(field)
        if transform and value:
            value = _text(transform(value))
        if value:
            lines.append(f"{field}：{value}")
    return "\n".join(lines)


def _deduplicated_join(*values: Any) -> str:
    parts: list[str] = []
    normalized: list[str] = []
    for value in values:
        item_text = _text(value)
        if not item_text:
            continue
        key = re.sub(r"[\s，。；：、,.!?！？;:\-—–]", "", item_text).casefold()
        if not key:
            continue
        # Do not append a legacy atom already expressed by a richer field.
        if any(key == item or key in item for item in normalized):
            continue
        # If a new richer atom subsumes a preceding short one, keep only the
        # richer expression.
        remove = [index for index, item in enumerate(normalized) if item in key]
        for index in reversed(remove):
            normalized.pop(index)
            parts.pop(index)
        normalized.append(key)
        parts.append(item_text)
    return "；".join(parts)


def _normalized_prose(value: Any) -> str:
    return re.sub(
        r"[\s，。；：、,.!?！？;:\-—–【】\[\]()（）“”\"']",
        "",
        _text(value),
    ).casefold()


def _without_deprecated_labels(value: Any) -> str:
    result = _text(value)
    for label in DEPRECATED_DISPLAY_LABELS:
        result = result.replace(label, "")
    return result.strip()


def _legacy_movement_description(source: dict[str, Any]) -> str:
    raw = _text(source.get("运镜方式"))
    base_parts: list[str] = []
    inline_subject = ""
    inline_function = ""
    for part in re.split(r"[|｜\n]", raw):
        candidate = part.strip(" ；;，,")
        if not candidate:
            continue
        if re.match(r"^运镜主体\s*[:：]", candidate):
            inline_subject = re.sub(r"^运镜主体\s*[:：]\s*", "", candidate)
        elif re.match(r"^(?:运镜功能|叙事功能)\s*[:：]", candidate):
            inline_function = re.sub(
                r"^(?:运镜功能|叙事功能)\s*[:：]\s*",
                "",
                candidate,
            )
        else:
            base_parts.append(candidate)

    base = _deduplicated_join(*base_parts).strip(" ；;，,。")
    subject = _without_deprecated_labels(
        source.get("运镜主体") or inline_subject
    ).strip(" ；;，,。")
    function = _without_deprecated_labels(
        source.get("运镜功能") or source.get("叙事功能") or inline_function
    ).strip(" ；;，,。")
    clauses = [base] if base else []
    joined = _normalized_prose(base)
    if subject and _normalized_prose(subject) not in joined:
        clauses.append(f"画面围绕{subject}展开")
        joined += _normalized_prose(subject)
    if function and _normalized_prose(function) not in joined:
        clauses.append(f"用于{function}")
    if not clauses:
        return ""
    return "，".join(clauses).rstrip("，。") + "。"


def _legacy_light_effect(source: dict[str, Any]) -> str:
    raw = _text(source.get("光影"))
    retained: list[str] = []
    for part in re.split(r"[；;\n|｜]", raw):
        candidate = part.strip()
        if not candidate:
            continue
        if any(candidate.startswith(label) for label in _LIGHT_SUBFIELD_LABELS):
            continue
        retained.append(candidate)
    if retained:
        return _deduplicated_join(*retained)

    direction = _text(source.get("光源方向") or source.get("光线方向"))
    effects = [
        clause.strip()
        for clause in re.split(r"[，,；;\n]", direction)
        if clause.strip()
        and any(marker in clause for marker in _LIGHT_EFFECT_MARKERS)
    ]
    if effects:
        return _deduplicated_join(*effects)
    return ""


def _legacy_light_atoms(source: dict[str, Any]) -> tuple[str, str, str]:
    raw = _text(source.get("光影"))

    def embedded(*labels: str) -> str:
        label_pattern = "|".join(re.escape(label) for label in labels)
        match = re.search(
            rf"(?:^|[；;\n|｜])\s*(?:{label_pattern})\s*[:：]\s*"
            rf"(.*?)(?=[；;\n|｜]\s*(?:光源来源|光源方向|色温倾向|"
            rf"光源|光线方向|色温)\s*[:：]|$)",
            raw,
        )
        return match.group(1).strip() if match else ""

    source_value = _text(source.get("光源来源") or source.get("光源"))
    direction = _text(source.get("光源方向") or source.get("光线方向"))
    temperature = _text(source.get("色温倾向") or source.get("色温"))
    return (
        source_value or embedded("光源来源", "光源"),
        direction or embedded("光源方向", "光线方向"),
        temperature or embedded("色温倾向", "色温"),
    )


def _first_action_stage(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    for part in re.split(r"[；;\n]", text):
        candidate = part.strip()
        if candidate:
            return candidate
    return text


def _seconds_label(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    text = re.sub(r"\s*(?:秒|s)$", "", text, flags=re.IGNORECASE).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        text = f"{float(text):.1f}"
    return f"{text}s"


def _visual_only_text(value: str, *, strip_dialogue: bool = False) -> str:
    """Keep only visible still-frame semantics from any projected field."""

    result = _TIMECODE_RE.sub("", value)
    if strip_dialogue:
        result = _QUOTED_DIALOGUE_RE.sub("", result)
        result = _BRACKETED_DIALOGUE_RE.sub("", result)
        result = re.sub(r"【[^】\n]{1,40}】", "", result)
    result = re.sub(
        r"(?:精确)?(?:台词|对白)\s*[:：]\s*[^。！？!?；;\n]*(?:[。！？!?；;]|$)",
        "",
        result,
    )
    visible_major: list[str] = []
    for major in re.split(r"[；;\n]", result):
        visible_minor: list[str] = []
        for minor in re.split(r"[，,、]|并且|并伴随|同时伴随|并有", major):
            clause = minor.strip()
            if not clause:
                continue
            clause = re.sub(
                r"(?:低声|轻声|高声|平静地|克制地)?(?:开口)?"
                r"(?:说|问|答|喊|道)\s*[:：]?\s*$",
                "",
                clause,
            ).strip()
            clause = re.sub(r"(?:开口|发声)\s*$", "", clause).strip()
            if not clause:
                continue
            if any(marker.casefold() in clause.casefold() for marker in _AUDIO_ONLY_CLAUSE_MARKERS):
                continue
            visible_minor.append(clause)
        if visible_minor:
            visible_major.append("，".join(visible_minor))
    result = "；".join(visible_major)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"([；;，,])\s*(?=[；;，,。])", "", result)
    return result.strip(" \n；;，,。:：”\"")


def _visual_only_picture_content(value: str) -> str:
    """Backward-compatible alias for the all-field visual scrubber."""

    return _visual_only_text(value, strip_dialogue=True)


def _visual_only_clauses(value: Any) -> str:
    """Remove timing, dialogue and audio clauses from a static projection."""

    result = _TIMECODE_RE.sub("", _text(value))
    result = re.sub(
        r"[\u3400-\u9fffA-Za-z·]{1,20}说道[：:]?\s*[“\"][^”\"\n]+[”\"]",
        "",
        result,
    )
    result = re.sub(
        r"【(?!\d+\.\d—|参考图锚点)[^】\n]{1,20}】\s*[“\"]?[^。！？!?；;\n]*[”\"]?",
        "",
        result,
    )
    forbidden = re.compile(
        r"对白|台词|说道|口型|音色|音量|语速|重音|尾音|环境音|声源|"
        r"脚步|衣料|呼吸|听见|画外音|配乐|BGM|鸟声|鸟鸣|虫鸣|风声|"
        r"水声|声线|声音|声响|发声|开口|问句|陈述句|感叹句|时间码"
    )
    visible: list[str] = []
    for major in re.split(r"[；;\n]", result):
        minor_visible = []
        for minor in re.split(r"[，,、]|并伴随|同时伴随|伴随", major):
            clause = minor.strip(" 。；;")
            if not clause or forbidden.search(clause):
                continue
            minor_visible.append(clause)
        if minor_visible:
            visible.append("，".join(minor_visible))
    return "；".join(visible).strip("；。 ")


def compile_formal_merged_field(
    shot: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> str:
    """Compile the compact execution cell without adding semantic content."""

    if not isinstance(shot, dict):
        raise ValueError("materialization_shot_invalid: shot must be an object")
    compact = shot if assume_compact else migrate_legacy_shot(shot)
    shot_id = _text(compact.get("镜头编号"))
    duration = _seconds_label(compact.get("时长"))
    cumulative = _seconds_label(compact.get("累计时长"))
    timing = ""
    if duration and cumulative:
        timing = f"（{duration}；累计{cumulative}）"
    elif duration:
        timing = f"（{duration}）"
    paragraphs = [
        f"镜头序号：{shot_id}{timing}" if shot_id else "镜头序号：",
        (
            f"镜头类型：{_text(compact.get('镜头类型'))}"
            f"｜可见主体：{_text(compact.get('可见主体'))}"
        ),
    ]
    paragraphs.extend(
        f"{field}：{_text(compact.get(field))}"
        for field in FORMAL_MERGED_ATOMIC_FIELDS
        if field not in {"镜头类型", "可见主体"}
    )
    return "\n\n".join(paragraphs)


def compile_gpt_keyframe_instruction(
    shot: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> str:
    """Compile a visual-only keyframe instruction without timecodes/dialogue."""

    if not isinstance(shot, dict):
        raise ValueError("materialization_shot_invalid: shot must be an object")
    compact = shot if assume_compact else migrate_legacy_shot(shot)
    start_state = _visual_only_clauses(compact.get("起始状态")) or (
        "人物保持本镜开场的可见站位、朝向、姿态与道具状态"
    )
    environment = _visual_only_clauses(compact.get("环境动态")) or (
        "场景内可见物保持真实、克制的自然状态"
    )
    anchor = _text(compact.get("参考图锚定")) or "无"
    return (
        "真人古装短剧，真实摄影机拍摄质感；"
        f"镜头类型与主体：{_text(compact.get('镜头类型'))}，"
        f"{_text(compact.get('可见主体'))}；"
        f"空间站位：{_visual_only_clauses(compact.get('空间站位描述'))}；"
        f"定格状态：{start_state}；"
        f"摄影机：{_visual_only_clauses(compact.get('机位'))}；"
        f"光影：{_visual_only_clauses(compact.get('光影'))}；"
        f"光源来源：{_visual_only_clauses(compact.get('光源来源'))}；"
        f"光源方向：{_visual_only_clauses(compact.get('光源方向'))}；"
        f"色彩：{_visual_only_clauses(compact.get('色温倾向'))}；"
        f"环境可见状态：{environment}；"
        f"参考图锚定：{_visual_only_clauses(anchor)}；"
        "单一稳定视觉时刻，禁止字幕、水印、现代物件、人物看镜头、"
        "复制脸、上下黑边、无来源光、重HDR、塑料皮肤与海报摆拍。"
    )


def compile_seedance_execution(batch: dict[str, Any]) -> str:
    """Read-only v0.4 compatibility compiler; never used by R10.1."""

    if not isinstance(batch, dict):
        raise ValueError("materialization_batch_invalid: batch must be an object")
    missing = [field for field, _label in SEEDANCE_ATOMIC_FIELDS if not _text(batch.get(field))]
    if missing:
        raise ValueError(
            "materialization_input_missing: Seedance atomic fields missing "
            + ", ".join(missing)
        )
    parts = ["Seedance 2.5空白上下文独立执行。"]
    parts.extend(
        f"{label}：{_text(batch[field])}。"
        for field, label in SEEDANCE_ATOMIC_FIELDS
    )
    prompt = "".join(parts)
    if len(prompt) > 60_000:
        raise ValueError(
            "materialization_too_large: compiled Seedance prompt exceeds 60000 characters"
        )
    return prompt


def migrate_legacy_shot(
    shot: dict[str, Any],
    *,
    legacy: bool | None = None,
) -> dict[str, Any]:
    """Collapse an R10.0 shot into the 27-field compact shot contract."""

    if not isinstance(shot, dict):
        raise ValueError("materialization_shot_invalid: shot must be an object")
    source = copy.deepcopy(shot)
    unknown_fields = sorted(set(source) - LEGACY_SHOT_INPUT_FIELDS)
    if unknown_fields:
        raise ValueError(
            "compact_contract_violation: unknown shot semantic fields require "
            "explicit mapping: " + ",".join(unknown_fields)
        )
    if legacy is None:
        legacy = any(field in source for field in LEGACY_STANDALONE_FIELDS) or any(
            label in _text(source.get("运镜方式"))
            for label in ("运镜主体：", "运镜功能：", "叙事功能：")
        )
    compact: dict[str, Any] = {field: source.get(field) for field in COMPACT_SHOT_FIELDS}

    compact["空间站位描述"] = _deduplicated_join(
        _without_deprecated_labels(source.get("空间站位描述")),
        _without_deprecated_labels(source.get("人物调度")),
    )
    compact["运镜方式"] = (
        _legacy_movement_description(source)
        if legacy
        else source.get("运镜方式")
    )
    picture = _deduplicated_join(
        _without_deprecated_labels(source.get("画面内容")),
        _without_deprecated_labels(source.get("动作与表演")),
        _without_deprecated_labels(source.get("台词")),
    )
    voice = _without_deprecated_labels(source.get("发声方式"))
    if voice:
        picture = _deduplicated_join(picture, f"说话时，{voice}")
    punctuation = _without_deprecated_labels(source.get("标点语气"))
    if punctuation:
        picture = _deduplicated_join(picture, f"对白节奏按{punctuation}")
    compact["画面内容"] = picture
    psychological_attribute = _without_deprecated_labels(
        source.get("心理音轨属性")
    )
    psychological = _without_deprecated_labels(source.get("心理音轨"))
    if (
        psychological_attribute
        and _normalized_prose(psychological_attribute)
        not in _normalized_prose(psychological)
    ):
        psychological = _deduplicated_join(
            psychological,
            f"心理音轨为{psychological_attribute}",
        )
    compact["心理音轨"] = psychological
    compact["生成限制"] = _text(source.get("生成限制")).lstrip("；; ")
    if legacy:
        light_source, light_direction, light_temperature = _legacy_light_atoms(source)
        compact["光源来源"] = light_source
        compact["光源方向"] = light_direction
        compact["色温倾向"] = light_temperature
        source["光源来源"] = light_source
        source["光源方向"] = light_direction
        source["色温倾向"] = light_temperature
    compact["光影"] = (
        _legacy_light_effect(source)
        if legacy
        else source.get("光影")
    )
    compact["起始状态"] = source.get("起始状态")
    if not _text(compact["起始状态"]):
        compact["起始状态"] = _deduplicated_join(
            _without_deprecated_labels(source.get("人物调度")),
            _first_action_stage(
                _without_deprecated_labels(
                    source.get("动作与表演") or source.get("画面内容")
                )
            ),
        )
    compact["切镜/转场提示词"] = _deduplicated_join(
        source.get("切镜/转场提示词"),
        source.get("切镜转场提示词"),
    )
    if (
        not _text(compact["切镜/转场提示词"])
        and legacy
        and _text(source.get("转场类型"))
    ):
        transition = _text(source.get("转场类型")).rstrip("。；; ")
        compact["切镜/转场提示词"] = f"按{transition}执行。"

    # Strip headings only from the structural containers that absorb the
    # retired fields. Evidence/reference cells must remain byte-faithful.
    for field in DEPRECATED_LABEL_CONTAINER_FIELDS:
        if isinstance(compact.get(field), str):
            compact[field] = _without_deprecated_labels(compact[field])

    residual_labels = [
        f"{field}[deprecated-label={label}]"
        for field in DEPRECATED_LABEL_REJECTION_FIELDS
        if isinstance(compact.get(field), str)
        for label in DEPRECATED_DISPLAY_LABELS
        if label in compact[field]
    ]
    if residual_labels:
        raise ValueError(
            "compact_contract_violation: migrated shot retains retired "
            "structural labels: " + ", ".join(residual_labels)
        )

    for field in INTERNAL_SHOT_FIELDS:
        if source.get(field) not in (None, ""):
            compact[field] = source[field]
    return compact


def migrate_storyboard_to_compact(
    storyboard: dict[str, Any],
    *,
    in_place: bool = False,
) -> dict[str, Any]:
    """Return the lean R10.1 canonical used for commits and XLSX sidecars."""

    if not isinstance(storyboard, dict):
        raise ValueError("materialization_storyboard_invalid: root must be an object")
    result = storyboard if in_place else copy.deepcopy(storyboard)
    shots = result.get("shots")
    if not isinstance(shots, list):
        raise ValueError("materialization_storyboard_invalid: shots must be an array")
    declared_version = _text(result.get("schema_version"))
    if declared_version == COMPACT_SCHEMA_VERSION:
        forbidden_root = [
            field
            for field in (
                "batches",
                "i2v_batches",
                "seedance_batches",
                "mother_template_columns",
                "formal_mother_template_rows",
            )
            if field in result
        ]
        if (
            "field_order" in result
            and list(result.get("field_order") or []) != COMPACT_SHOT_FIELDS
        ):
            forbidden_root.append("field_order[not-exact-compact-contract]")
        forbidden_shots: list[str] = []
        allowed_shot_fields = set(COMPACT_SHOT_FIELDS) | set(INTERNAL_SHOT_FIELDS)
        for index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                raise ValueError(
                    "materialization_shot_invalid: every shot must be an object"
                )
            for field in sorted(LEGACY_STANDALONE_FIELDS | PERSISTED_DERIVED_FIELDS):
                if field in shot:
                    forbidden_shots.append(f"shots[{index}].{field}")
            missing_fields = [field for field in COMPACT_SHOT_FIELDS if field not in shot]
            if missing_fields:
                forbidden_shots.append(
                    f"shots[{index}].missing={','.join(missing_fields)}"
                )
            unknown_fields = sorted(set(shot) - allowed_shot_fields)
            if unknown_fields:
                forbidden_shots.append(
                    f"shots[{index}].unknown={','.join(unknown_fields)}"
                )
            for field in DEPRECATED_LABEL_REJECTION_FIELDS:
                value = shot.get(field)
                if not isinstance(value, str):
                    continue
                for label in DEPRECATED_DISPLAY_LABELS:
                    if label in value:
                        forbidden_shots.append(
                            f"shots[{index}].{field}[deprecated-label={label}]"
                        )
            light_effect = _text(shot.get("光影"))
            for label in _LIGHT_SUBFIELD_LABELS:
                if label in light_effect:
                    forbidden_shots.append(
                        f"shots[{index}].光影[nested-light-label={label}]"
                    )
        forbidden = forbidden_root + forbidden_shots
        if forbidden:
            raise ValueError(
                "compact_contract_violation: R10.1 forbids legacy, derived, "
                "and batch fields: " + ", ".join(forbidden)
            )
    elif declared_version not in {"", "R10.0", "legacy", "LEGACY"}:
        raise ValueError(
            "materialization_schema_unsupported: expected R10.0/legacy or R10.1, "
            f"got {declared_version}"
        )
    else:
        for index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                raise ValueError(
                    "materialization_shot_invalid: every shot must be an object"
                )
            unknown_legacy = sorted(set(shot) - LEGACY_SHOT_INPUT_FIELDS)
            if unknown_legacy:
                raise ValueError(
                    "compact_contract_violation: legacy unknown shot semantic "
                    "fields require explicit mapping: "
                    f"shots[{index}].{','.join(unknown_legacy)}"
                )
    compact_shots = [
        migrate_legacy_shot(
            shot,
            legacy=declared_version != COMPACT_SCHEMA_VERSION,
        )
        for shot in shots
    ]
    if declared_version != COMPACT_SCHEMA_VERSION:
        missing_light = [
            str(shot.get("镜头编号") or f"shots[{index}]")
            for index, shot in enumerate(compact_shots)
            if not _text(shot.get("光影"))
        ]
        if missing_light:
            raise ValueError(
                "materialization_input_missing: legacy migration cannot derive "
                "光影 for " + ", ".join(missing_light)
            )
        missing_start = [
            str(shot.get("镜头编号") or f"shots[{index}]")
            for index, shot in enumerate(compact_shots)
            if not _text(shot.get("起始状态"))
        ]
        if missing_start:
            raise ValueError(
                "materialization_input_missing: legacy migration cannot derive "
                "起始状态 for " + ", ".join(missing_start)
            )
    result["shots"] = compact_shots
    result["schema_version"] = COMPACT_SCHEMA_VERSION
    result["field_order"] = list(COMPACT_SHOT_FIELDS)
    for field in (
        "batches",
        "i2v_batches",
        "seedance_batches",
        "mother_template_columns",
        "formal_mother_template_rows",
    ):
        result.pop(field, None)
    return result


def materialize_storyboard(
    storyboard: dict[str, Any],
    *,
    in_place: bool = False,
) -> dict[str, Any]:
    """Normalize to a lean compact canonical without persisted projections.

    R10.1 stores semantic atoms once: no formal/GPT/Seedance long field, no
    batch prompt and no ``batches`` array are written here.
    """

    return migrate_storyboard_to_compact(storyboard, in_place=in_place)


def _function_scene_cell(shot: dict[str, Any]) -> str:
    function = _text(shot.get("功能类型"))
    scene = _text(shot.get("场景连续性ID"))
    if function and scene:
        return f"功能类型：{function}\n场景连续性ID：{scene}"
    if function:
        return f"功能类型：{function}"
    if scene:
        return f"场景连续性ID：{scene}"
    return ""


def formal_mother_template_rows(
    storyboard: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> list[dict[str, Any]]:
    """Build the exact ten-column workbook projection from a lean canonical."""

    compact = storyboard if assume_compact else migrate_storyboard_to_compact(storyboard)
    shots = compact.get("shots")
    if not isinstance(shots, list):
        raise ValueError("materialization_storyboard_invalid: shots must be an array")
    rows: list[dict[str, Any]] = []
    for shot in shots:
        rows.append(
            {
                "分镜序号": shot.get("镜头编号"),
                "时长": shot.get("时长"),
                "累计时长": shot.get("累计时长"),
                "功能类型/场景连续性ID": _function_scene_cell(shot),
                FORMAL_MERGED_FIELD: compile_formal_merged_field(
                    shot,
                    assume_compact=True,
                ),
                GPT_KEYFRAME_FIELD: compile_gpt_keyframe_instruction(
                    shot,
                    assume_compact=True,
                ),
                "参考图锚定": shot.get("参考图锚定"),
                "覆盖原文": shot.get("覆盖原文"),
                "原文依据类型": shot.get("原文依据类型"),
                "原文支撑句": shot.get("原文支撑句"),
            }
        )
    return rows
