from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable


FORMAL_MERGED_FIELD = "正式分镜母版"
LEGACY_FORMAL_MERGED_FIELD = "正式分镜母版·字段合并"
FORMAL_MASTER_FIELD_ALIASES = (
    FORMAL_MERGED_FIELD,
    LEGACY_FORMAL_MERGED_FIELD,
)
GPT_KEYFRAME_FIELD = "GPT关键帧生成指令"

ASPECT_RATIO_TOKEN = "9:16竖屏"

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

# Narrative execution fields are listed here; the shot/timing header and the
# combined shot-type/visible-subject paragraph are added explicitly above them.
FORMAL_MERGED_ATOMIC_FIELDS = (
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
    "环境动态",
    "光影",
    "光源来源",
    "光源方向",
    "色温倾向",
    "参考图锚定",
    "生成限制",
)

GPT_KEYFRAME_SCRUB_FIELDS = {
    "空间站位描述",
    "起始状态",
    "机位",
    "环境动态",
    "参考图锚定",
    "生成限制",
}

# Frozen v0.5.0 projection grammar.  It is read-only compatibility code used
# solely to verify an existing legacy-header workbook against its sidecar; new
# exports always use the stricter v0.5.1 functions below.
LEGACY_V050_FORMAL_FIELDS = (
    "镜头类型",
    "可见主体",
    *FORMAL_MERGED_ATOMIC_FIELDS,
)
LEGACY_V050_GPT_FIELDS = (
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
_PROTECTED_VISUAL_LABEL_PATTERN = (
    r"(?:参考图锚点|可见文字|画内文字|道具文字|"
    r"匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)"
)
_PROTECTED_VISUAL_FRAGMENT_RE = re.compile(
    rf"(?:【{_PROTECTED_VISUAL_LABEL_PATTERN}】[^；;\n，,]*|"
    rf"(?:画内)?(?:匾额|牌匾|招牌|门牌|题字|"
    rf"可见文字|画内文字|道具文字)"
    rf"(?=[^；;\n，,]{{0,32}}(?:[“\"「『]|写着|写有|刻着|刻有|"
    rf"题着|题有|印着|印有|可见文字|字样|清晰可见))"
    rf"[^；;\n，,]*)"
)
_BRACKETED_DIALOGUE_RE = re.compile(
    r"【[^】\n]{1,40}】\s*[^。！？!?；;\n]*(?:[。！？!?；;]|$)"
)
_QUOTED_DIALOGUE_RE = re.compile(r"[“\"「『][^”\"」』\n]*[”\"」』]")
_LABELED_DIALOGUE_RE = re.compile(
    r"(?:精确)?(?:台词|对白)(?:文字|内容)?\s*[:：]\s*"
    r"[^。！？!?；;\n]*(?:[。！？!?；;]|$)"
)
_AUDIO_ONLY_CLAUSE_MARKERS = (
    "口型",
    "说话时",
    "对白",
    "动作声",
    "环境声",
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
_AUDIO_SOURCE_CLAUSE_RE = re.compile(
    r"(?:古琴|琴|雨|水|敲门|雷|钟|婴儿哭|哭|笑|喊|脚步|呼吸|"
    r"衣料|器物|风|鸟|虫|叶)(?:声|鸣)"
)

_ASPECT_RATIO_PHRASE_RE = re.compile(
    r"(?<!\d)9\s*[:：]\s*16(?![\d.])\s*(?:"
    r"竖屏真人古装短剧(?:截图质感|单帧)?|"
    r"真人古装短剧(?:截图质感|单帧)?|"
    r"竖屏"
    r")?",
    flags=re.IGNORECASE,
)

_ANY_ASPECT_TOKEN_RE = re.compile(
    r"(?<!\d)(?P<width>\d+)\s*(?P<separator>[:：/])\s*"
    r"(?P<height>\d+(?:\.\d+)?)(?!\d)"
)
_NEGATIVE_MUSIC_CLAUSE_RE = re.compile(
    r"(?:禁止|不要|不得|不使用|不生成|无)(?:任何)?(?:让)?"
    r"(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)?\s*"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)"
    r"(?:响起|播放|进入|渐入|切入|铺开|持续|开始)?",
    flags=re.IGNORECASE,
)
_ACTIVE_MUSIC_DIRECTIVE_RE = re.compile(
    r"(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)\s*(?:任何)?"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)|"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)\s*"
    r"(?:响起|播放|进入|渐入|切入|铺开|持续|采用|使用|开始|为)",
    flags=re.IGNORECASE,
)
_MUSIC_TERM_RE = re.compile(
    r"(?<![A-Za-z])BGM(?![A-Za-z])|背景音乐|额外配乐|配乐|音乐",
    flags=re.IGNORECASE,
)

KEYFRAME_FORBIDDEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*"
    r"\d+(?:\.\d+)?\s*(?:秒|s)|"
    r"(?:第\s*)?(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
    r"\s*秒(?:后|前|时|内)?|"
    r"(?<![A-Za-z0-9_-])\d+(?:\.\d+)?[sS](?:后|前|时|内)?"
    r"(?![A-Za-z0-9_-])|一瞬后|"
    r"硬切|切镜|转场|切至|切到|切换至|跳切|叠化|溶解|淡入|淡出|"
    r"(?:画面|镜头)(?:转至|转到)|"
    r"推镜|拉镜|跟镜|摇镜|移镜|升镜|降镜|环绕镜|"
    r"由远及近|由近及远|"
    r"(?:镜头|摄影机|摄像机)?(?:缓慢|快速|轻微|持续)?"
    r"(?:前推|后拉|横移|上升|下降|摇摄|跟随|环绕)|"
    r"(?:镜头|摄影机|摄像机|相机|画面)(?:向前|向后|向左|向右|"
    r"朝前|朝后|朝左|朝右|缓缓|缓慢|慢慢|快速|轻微|持续|逐渐)*"
    r"(?:推近|推进|拉远|后退|退开|摇动|摇摄|平移|横移|变焦|"
    r"旋转|放大|缩小|跟随|环绕)|"
    r"从(?:全景|中景|近景|特写)[^；。\n]{0,12}"
    r"(?:移动|切换|变化|过渡)到(?:全景|中景|近景|特写)|"
    r"(?:几秒|数秒|片刻|少顷)(?:后|前|之后|以前)|随后|接着|然后|"
    r"(?:正在|开始|继续|逐渐|将要|即将)(?:缓缓|缓慢|慢慢)?"
    r"(?:起身|转头|行走|抬手|收手|离开|进入|移动|飘动|递交|"
    r"接取|说话|开口|摇晃|落下|抬眼|低头)|"
    r"准备(?:开始|起身|转头|移动|说话|开口|递交|接取|离开|进入)|"
    r"即将(?:开始|起身|转头|移动|说话|开口|递交|接取|离开|进入)|"
    r"(?:嘴型|唇形|口唇)[^；。\n]{0,12}(?:同步|开合|说话)|"
    r"(?:古琴|琴|雨|水|敲门|雷|钟|婴儿哭|哭|笑|喊|脚步|呼吸|"
    r"衣料|器物|风|鸟|虫|叶)(?:声|鸣)|"
    r"说道|对白|台词|口型|音色|语速|重音|尾音|环境音效|"
    r"BGM|配乐|声音|声响|脚步声|呼吸声|衣料声)",
    flags=re.IGNORECASE,
)
# Import-compatible representative literals for callers that enumerate the
# v0.5.1 static gate.  The regex above is the authoritative detector.
KEYFRAME_FORBIDDEN_DYNAMIC_TERMS = (
    "硬切", "切镜", "转场", "推镜", "拉镜", "跟镜", "摇镜", "移镜",
    "升镜", "降镜", "环绕镜", "由远及近", "由近及远", "几秒后", "随后",
    "接着", "然后", "准备开始", "即将开始",
)

BLANK_CONTEXT_FORBIDDEN_TERMS = (
    "承接上一镜",
    "延续之前",
    "保持关键帧",
    "保持上一关键帧",
    "按照上一张",
    "沿用上一张",
    "同上",
    "继续原动作",
    "继续刚才",
    "保持上一镜",
    "承接关键帧",
    "上一关键帧",
    "上一镜头",
    "下一镜",
    "前一镜",
    "前镜",
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


def _protect_visual_text_fragments(value: Any) -> tuple[str, dict[str, str]]:
    """Hide explicit still-visible labels while dialogue/audio text is scrubbed."""

    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"\ue000VISUAL{len(protected)}\ue001"
        protected[token] = match.group(0)
        return token

    return _PROTECTED_VISUAL_FRAGMENT_RE.sub(replace, _text(value)), protected


def _active_music_directive(value: Any) -> str:
    """Return any unambiguously non-negative in-shot music instruction."""

    without_bans, _mentioned = _strip_negative_music_clauses(value)
    instruction_text, _protected = _protect_visual_text_fragments(without_bans)
    instruction_text = _QUOTED_DIALOGUE_RE.sub("", instruction_text)
    instruction_text = _LABELED_DIALOGUE_RE.sub("", instruction_text)
    match = _ACTIVE_MUSIC_DIRECTIVE_RE.search(instruction_text)
    if match:
        return match.group(0)
    # After all recognized negative clauses/list-items are removed, any music
    # term is affirmative or ambiguous and therefore unsafe to reinterpret.
    match = _MUSIC_TERM_RE.search(instruction_text)
    return match.group(0) if match else ""


def _strip_negative_music_clauses(value: Any) -> tuple[str, bool]:
    """Remove only proven bans and preserve all other music semantics."""

    text = _text(value)
    mentioned = False
    parts = re.split(r"([；;。！？!?\n]+)", text)
    for index in range(0, len(parts), 2):
        original = parts[index]
        if not _MUSIC_TERM_RE.search(original):
            continue
        negative_prefix = re.match(
            r"^\s*(?:禁止|不要|不得|不使用|不生成|无)(?:任何)?",
            original,
            flags=re.I,
        )
        clause, direct_count = _NEGATIVE_MUSIC_CLAUSE_RE.subn("", original)
        if direct_count:
            mentioned = True
            remainder = clause.strip(" 、，,与和且及以")
            if remainder and negative_prefix and not re.match(
                r"^\s*(?:禁止|不要|不得|不使用|不生成|无)",
                remainder,
                flags=re.I,
            ):
                clause = negative_prefix.group(0).strip() + remainder
            else:
                clause = remainder
        if not _MUSIC_TERM_RE.search(clause):
            parts[index] = clause
            continue
        if negative_prefix is None:
            parts[index] = clause
            continue
        # A prohibition about another subject followed by an affirmative music
        # command is not a negative music list item.
        if _ACTIVE_MUSIC_DIRECTIVE_RE.search(clause):
            parts[index] = clause
            continue
        clause = _MUSIC_TERM_RE.sub("", clause)
        clause = re.sub(
            r"(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)\s*",
            "",
            clause,
            flags=re.I,
        )
        clause = clause.strip(" 、，,与和且及以")
        if clause and not re.match(
            r"^\s*(?:禁止|不要|不得|不使用|不生成|无)",
            clause,
            flags=re.I,
        ):
            clause = negative_prefix.group(0).strip() + clause
        parts[index] = clause
        mentioned = True
    cleaned = "".join(parts)
    cleaned = re.sub(r"[、，,与和且及以]\s*(?=[；;。！？!?\n]|$)", "", cleaned)
    cleaned = re.sub(r"([、，,])\s*[、，,]+", r"\1", cleaned)
    return cleaned, mentioned


def _assert_generation_aspect_contract(value: Any) -> None:
    text = _text(value)
    malformed = sorted(
        {
            match.group(0)
            for match in _ANY_ASPECT_TOKEN_RE.finditer(text)
            if (
                match.group("width") != "9"
                or match.group("separator") not in {":", "："}
                or match.group("height") != "16"
            )
        }
    )
    remainder = _ASPECT_RATIO_PHRASE_RE.sub("", text)
    residual_screen_terms = sorted(
        {term for term in ("竖屏", "横屏", "方屏") if term in remainder}
    )
    if malformed or residual_screen_terms:
        raise ValueError(
            "aspect_ratio_contract_violation: per-shot execution must use "
            f"9:16竖屏 only; malformed={malformed}, "
            f"residual_screen_terms={residual_screen_terms}"
        )


def _assert_projected_aspect_contract(value: Any, *, projection: str) -> None:
    text = _text(value)
    matches = list(_ANY_ASPECT_TOKEN_RE.finditer(text))
    malformed = [
        match.group(0)
        for match in matches
        if (
            match.group("width") != "9"
            or match.group("separator") != ":"
            or match.group("height") != "16"
        )
    ]
    if (
        text.count(ASPECT_RATIO_TOKEN) != 1
        or len(matches) != 1
        or malformed
        or any(term in text for term in ("横屏", "方屏"))
    ):
        raise ValueError(
            f"aspect_ratio_projection_violation: {projection} must contain "
            "exactly one 9:16竖屏 contract and no competing ratio"
        )


def _normalize_generation_limits(value: Any) -> str:
    """Render the per-shot aspect contract exactly once.

    v0.5.0 accepted several aspect-ratio spellings.  v0.5.1 migrates all of
    them into one self-contained shot constraint instead of copying a verbose
    legacy style phrase into every projection.
    """

    _assert_generation_aspect_contract(value)
    active_music = _active_music_directive(value)
    if active_music:
        raise ValueError(
            "generation_limit_audio_contract_violation: affirmative in-shot "
            f"music directive is forbidden: {active_music!r}"
        )
    remainder = _ASPECT_RATIO_PHRASE_RE.sub("", _text(value))
    # Normalize proven negative spellings to one exact phrase.  Ambiguous or
    # affirmative music wording was rejected above and is never inverted.
    remainder, music_mentioned = _strip_negative_music_clauses(remainder)
    remainder = re.sub(r"\s*[；;]\s*", "；", remainder)
    remainder = re.sub(r"；{2,}", "；", remainder).strip(" ；;，,。")
    parts = [ASPECT_RATIO_TOKEN]
    if remainder:
        parts.append(remainder)
    if music_mentioned:
        parts.append("禁止额外配乐")
    return "；".join(parts)


def _generation_limits_without_aspect(value: Any) -> str:
    """Return visual limits without duplicating the explicit GPT aspect line."""

    remainder = _ASPECT_RATIO_PHRASE_RE.sub("", _text(value))
    remainder = re.sub(r"\s*[；;]\s*", "；", remainder)
    return re.sub(r"；{2,}", "；", remainder).strip(" ；;，,。")


def _normalize_transition_text(value: Any, *, legacy: bool) -> str:
    """Validate new transitions and clean only read-only legacy imports."""

    text = _text(value)
    if not text:
        return ""
    empty_separator = re.search(r"[；;。]\s*[；;。]", text)
    parts = [
        part.strip(" \t\r。；;，,")
        for part in re.split(r"[；;。\n]+", text)
        if part.strip(" \t\r。；;，,")
    ]
    normalized = [_normalized_prose(part) for part in parts]
    duplicate_indexes = [
        index
        for index in range(1, len(normalized))
        if normalized[index] and normalized[index] == normalized[index - 1]
    ]
    if not legacy and (empty_separator or duplicate_indexes):
        reasons = []
        if empty_separator:
            reasons.append("empty separator")
        if duplicate_indexes:
            reasons.append("adjacent duplicate sentence")
        raise ValueError(
            "transition_contract_violation: " + ", ".join(reasons)
        )
    if legacy:
        cleaned: list[str] = []
        cleaned_keys: list[str] = []
        for part, key in zip(parts, normalized):
            if cleaned_keys and key == cleaned_keys[-1]:
                continue
            cleaned.append(part)
            cleaned_keys.append(key)
        parts = cleaned
    if not parts:
        return ""
    suffix = "。" if text.rstrip().endswith("。") else ""
    return "；".join(parts) + suffix


def _contract_hits(text: str, terms: Iterable[str]) -> list[str]:
    return sorted({term for term in terms if term in text})


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


def _assert_active_timing_precision(shot: dict[str, Any]) -> None:
    for field in ("时长", "累计时长"):
        try:
            numeric = float(shot.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"timing_precision_contract_violation: {field} must be numeric"
            ) from exc
        if abs(numeric * 10 - round(numeric * 10)) > 1e-8:
            raise ValueError(
                f"timing_precision_contract_violation: {field} must use "
                "0.1-second precision"
            )


def _visual_only_text(value: str, *, strip_dialogue: bool = False) -> str:
    """Keep only visible still-frame semantics from any projected field."""

    result = _TIMECODE_RE.sub("", value)
    protected_visual: dict[str, str] = {}
    if strip_dialogue:
        result, protected_visual = _protect_visual_text_fragments(result)
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
            if not re.search(r"(?:尚未|没有|未曾|并未)开口\s*$", clause):
                clause = re.sub(r"(?:开口|发声)\s*$", "", clause).strip()
            if not clause:
                continue
            if any(marker.casefold() in clause.casefold() for marker in _AUDIO_ONLY_CLAUSE_MARKERS):
                continue
            if _AUDIO_SOURCE_CLAUSE_RE.search(clause):
                continue
            visible_minor.append(clause)
        if visible_minor:
            visible_major.append("，".join(visible_minor))
    result = "；".join(visible_major)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"([；;，,])\s*(?=[；;，,。])", "", result)
    for token, fragment in protected_visual.items():
        result = result.replace(token, fragment)
    return result.strip(" \n；;，,。:：”\"")


def _visual_only_picture_content(value: str) -> str:
    """Backward-compatible alias for the all-field visual scrubber."""

    return _visual_only_text(value, strip_dialogue=True)


def compile_formal_merged_field(
    shot: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> str:
    """Compile the compact execution cell without adding semantic content."""

    if not isinstance(shot, dict):
        raise ValueError("materialization_shot_invalid: shot must be an object")
    compact = dict(shot) if assume_compact else migrate_legacy_shot(shot)
    _assert_active_timing_precision(compact)
    compact["生成限制"] = _normalize_generation_limits(
        compact.get("生成限制")
    )
    compact["切镜/转场提示词"] = _normalize_transition_text(
        compact.get("切镜/转场提示词"),
        legacy=False,
    )
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
            f"镜头类型：{_text(compact.get('镜头类型'))}｜"
            f"可见主体：{_text(compact.get('可见主体'))}"
        ),
    ]
    paragraphs.extend(
        f"{field}：{_text(compact.get(field))}"
        for field in FORMAL_MERGED_ATOMIC_FIELDS
    )
    formal = "\n\n".join(paragraphs)
    _assert_projected_aspect_contract(formal, projection="formal_master")
    if re.search(r"(?<![A-Za-z])BGM(?![A-Za-z])", formal, flags=re.I):
        raise ValueError(
            "formal_master_audio_contract_violation: BGM literal is forbidden; "
            "use 禁止额外配乐 and keep music cues outside shot execution"
        )
    active_music = _active_music_directive(formal)
    if active_music:
        raise ValueError(
            "formal_master_audio_contract_violation: affirmative in-shot "
            f"music directive is forbidden: {active_music!r}"
        )
    context_hits = _contract_hits(formal, BLANK_CONTEXT_FORBIDDEN_TERMS)
    if context_hits:
        raise ValueError(
            "formal_master_context_violation: cross-shot references are "
            "forbidden: " + ", ".join(context_hits)
        )
    return formal


def compile_gpt_keyframe_instruction(
    shot: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> str:
    """Compile a visual-only keyframe instruction without timecodes/dialogue."""

    if not isinstance(shot, dict):
        raise ValueError("materialization_shot_invalid: shot must be an object")
    compact = dict(shot) if assume_compact else migrate_legacy_shot(shot)
    normalized = dict(compact)
    normalized["生成限制"] = _generation_limits_without_aspect(
        compact.get("生成限制")
    )
    body = _labeled_lines(
        normalized,
        GPT_KEYFRAME_ATOMIC_FIELDS,
        transforms={
            field: (
                lambda value: _visual_only_text(value, strip_dialogue=True)
            )
            for field in GPT_KEYFRAME_SCRUB_FIELDS
        },
    )
    lines = ["GPT关键帧生成指令", f"画幅：{ASPECT_RATIO_TOKEN}"]
    if body:
        lines.append(body)
    prompt = "\n".join(lines)
    _assert_projected_aspect_contract(prompt, projection="gpt_keyframe")
    dynamic_hits = sorted(
        {match.group(0) for match in KEYFRAME_FORBIDDEN_RE.finditer(prompt)}
    )
    context_hits = _contract_hits(prompt, BLANK_CONTEXT_FORBIDDEN_TERMS)
    if dynamic_hits or context_hits:
        reasons = []
        if dynamic_hits:
            reasons.append("dynamic=" + ",".join(dynamic_hits))
        if context_hits:
            reasons.append("cross_shot=" + ",".join(context_hits))
        raise ValueError(
            "keyframe_static_contract_violation: " + "; ".join(reasons)
        )
    return prompt


def compile_legacy_v050_formal_master(shot: dict[str, Any]) -> str:
    """Reproduce the immutable v0.5.0 fifth-column projection."""

    shot_id = _text(shot.get("镜头编号"))
    duration = _seconds_label(shot.get("时长"))
    cumulative = _seconds_label(shot.get("累计时长"))
    timing = ""
    if duration and cumulative:
        timing = f"（{duration}；累计{cumulative}）"
    elif duration:
        timing = f"（{duration}）"
    lines = [f"镜头序号：{shot_id}{timing}" if shot_id else "镜头序号："]
    function_scene = _function_scene_cell(shot)
    if function_scene:
        lines.append(function_scene)
    body = _labeled_lines(shot, LEGACY_V050_FORMAL_FIELDS)
    if body:
        lines.append(body)
    return "\n".join(lines)


def compile_legacy_v050_gpt_keyframe(shot: dict[str, Any]) -> str:
    """Reproduce the immutable v0.5.0 sixth-column projection."""

    return _labeled_lines(
        shot,
        LEGACY_V050_GPT_FIELDS,
        heading="GPT关键帧生成指令",
        transforms={
            field: (
                _visual_only_picture_content
                if field == "画面内容"
                else _visual_only_text
            )
            for field in LEGACY_V050_GPT_FIELDS
        },
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
    compact["生成限制"] = _normalize_generation_limits(source.get("生成限制"))
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
    compact["切镜/转场提示词"] = _normalize_transition_text(
        _deduplicated_join(
            source.get("切镜/转场提示词"),
            source.get("切镜转场提示词"),
        ),
        legacy=bool(legacy),
    )
    if (
        not _text(compact["切镜/转场提示词"])
        and legacy
        and _text(source.get("转场类型"))
    ):
        transition = _text(source.get("转场类型")).rstrip("。；; ")
        compact["切镜/转场提示词"] = _normalize_transition_text(
            f"按{transition}执行。",
            legacy=True,
        )

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


def legacy_v050_mother_template_rows(
    storyboard: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the frozen v0.5.0 ten-column projection for read verification."""

    shots = storyboard.get("shots")
    if not isinstance(shots, list):
        raise ValueError("materialization_storyboard_invalid: shots must be an array")
    return [
        {
            "分镜序号": shot.get("镜头编号"),
            "时长": shot.get("时长"),
            "累计时长": shot.get("累计时长"),
            "功能类型/场景连续性ID": _function_scene_cell(shot),
            FORMAL_MERGED_FIELD: compile_legacy_v050_formal_master(shot),
            GPT_KEYFRAME_FIELD: compile_legacy_v050_gpt_keyframe(shot),
            "参考图锚定": shot.get("参考图锚定"),
            "覆盖原文": shot.get("覆盖原文"),
            "原文依据类型": shot.get("原文依据类型"),
            "原文支撑句": shot.get("原文支撑句"),
        }
        for shot in shots
    ]


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
