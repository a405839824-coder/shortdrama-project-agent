from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable

from .written_payload import discover_current_written_payloads, context_instruction_text
from .picture_presentation import display_picture
from .field_content import (assert_field_content, r5_keyframe_sections, LIGHTING_FIELDS as LIGHTING_FIELDS,
                            FORMAL_EXECUTION_FIELDS, project_lighting)


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

LEGACY_MOTHER_TEMPLATE_COLUMNS = [
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
MOTHER_TEMPLATE_COLUMNS = [*LEGACY_MOTHER_TEMPLATE_COLUMNS, "光影"]

# Narrative execution fields are listed here; the shot/timing header and the
# combined shot-type/visible-subject paragraph are added explicitly above them.
LEGACY_FORMAL_MERGED_ATOMIC_FIELDS = (
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
FORMAL_MERGED_ATOMIC_FIELDS = FORMAL_EXECUTION_FIELDS

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
    *LEGACY_FORMAL_MERGED_ATOMIC_FIELDS,
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

INTERNAL_SHOT_FIELDS = ("shot_uid", "episode_id", "scene_uid", "picture_timing")

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
VOICE_NESTED_LABELS = (
    "基础声线", "言语行为", "当前表达", "发声方式", "音量", "语速与节奏",
    "呼吸", "停顿", "重音", "尾音", "禁止语气",
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
    r"(?:禁止|不要|不得|不使用|不生成|没有|不加|无)(?:任何)?(?:让)?"
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
KEYFRAME_UNOBSERVABLE_RE = re.compile(
    r"整个故事中最幸福的一刻|没有战争|没有离别|没有未来的悲伤|"
    r"(?:红色|颜色)[^；。\n]{0,10}代表(?:着)?[^；。\n]{0,12}(?:爱情|婚姻|希望)|"
    r"(?:未来|多年后)[^；。\n]{0,18}(?:无法忘记|仍然难忘|仍难忘|忘不了)",
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
    "上一个镜头", "上个镜头", "上一个镜", "上个镜", "前一个镜头", "前个镜头",
    "上一个关键帧", "上个关键帧", "上一帧", "上一个画面", "上个画面",
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


def _protect_visual_text_fragments(value: Any, *, field_name: str = "") -> tuple[str, dict[str, str]]:
    """Protect written payloads, never adjacent action or quoted speech."""
    protected: dict[str, str] = {}
    source_text = _text(value)
    typed_quotes = {item.quote.start: item.quote.literal
                    for item in discover_current_written_payloads(source_text, field_name)}

    def replace(match: re.Match[str]) -> str:
        token = f"\ue000VISUAL{len(protected)}\ue001"
        protected[token] = match.group(0)
        return token

    surface_re = re.compile(r"纸面|纸上|纸签|记录纸|账册|册页|书页|木牌|布告|标签|标题|题头|栏名|表头|简图|地图|示意图|页签|最后一行|门牌|匾额|牌匾|招牌|题字|可见文字|画内文字|道具文字")
    marking_re = re.compile(r"写着|写有|写到|写好|已写的|刻着|刻有|印着|印有|标注|标记|现有|已有|可读|标签|标题|题头|栏名|表头|字样|文字|题字|清晰可见")
    speaking_re = re.compile(
        r"说道|说着|问道|答道|喊道|念道|朗读|朗诵|念出|念着|读出|读着|默读|"
        r"低声|轻声|高声|开口|转述|口述|心理内声|内声|旁白|对白|台词|音轨|"
        r"(?:说|问|答|喊|念|(?<!可)读)\s*[:：]?\s*$|"
        r"(?:说|问|念|(?<!可)读)(?=纸面|纸上|册页|书页|文字)"
    )
    historical_re = re.compile(
        r"昨日|昨天|前日|前一日|从前|以前|当年|当时|曾经|"
        r"曾(?:写|有|刻|印)|回忆|记得|想起|原文|源文|历史引用"
    )
    enumeration_re = re.compile(r"[\s，,、；;:：]*(?:(?:以及|及|和|与)[\s，,、；;:：]*)?")
    current_re = re.compile(r"现在|当前|此刻|眼下")
    correction_re = re.compile(r"[\s，,]*而是(?:写着|写有|刻着|刻有|印着|印有|标注为)[\s:：]*")
    column_intro_re = re.compile(r"栏|列|字段")
    column_value_re = re.compile(
        r'[\s，,、；;]+(?:(?:以及|及|和|与)\s*)?[^“"「『，,、；;:：=。！？!?\n]+?(?:为|是|[:：=])\s*'
    )
    column_break_re = re.compile(
        r"随后|稍后|正在|将(?:要)?(?:写|改|标|刻|印|为|是)|(?:改|变)(?:成|为)|"
        r"(?:不再|已不|不)(?:存在|可读|显示)|擦除|抹去|收起|移走"
    )

    # Keep the physical paper scope separate from the kind of its first
    # value: a title can precede named columns without itself being a column.
    paper_surface_re = re.compile(
        r"纸面|纸上|纸签|记录纸|账册|册页|书页|"
        r"(?:同一张|这张|该张|一张)纸(?:的(?:上部|下部|左侧|右侧|中部))?|"
        r"纸的(?:上部|下部|左侧|右侧|中部)"
    )
    current_role_re = re.compile(
        r'[\s，,、；;]+(?:(?:以及|及|和|与)\s*)?(?:现有|已有)'
        r'(?:标题|题头|表头|栏名|栏目|字段|[^“"「『，,、；;:：=。！？!?\n]*?(?:栏|列))'
        r'(?:为|是|[:：=])\s*'
    )
    negative_marking_re = re.compile(
        r"(?:没有|未曾|从未|并未|尚未|不曾|不是|不|未)(?:写着|写有|标注|现有|已有)|不存在"
    )

    def protect_written_quotes(match: re.Match[str]) -> str:
        clause = match.group(0)
        end = 0
        written = False
        columns = False
        paper_scope = False

        def choose(quote: re.Match[str]) -> str:
            nonlocal end, written, columns, paper_scope
            prefix = clause[end:quote.start()]
            end = quote.end()
            typed_quote = typed_quotes.get(match.start() + quote.start()) == quote.group(0)
            scope_break = bool(speaking_re.search(prefix) or historical_re.search(prefix)
                               or column_break_re.search(prefix))
            if scope_break:
                paper_scope = False
            if (paper_scope and current_role_re.fullmatch(prefix)
                    and not scope_break and not negative_marking_re.search(prefix)):
                written = True
                columns = True
                return replace(quote)
            if written and correction_re.fullmatch(prefix):
                # Preserve both the negated label and its positive correction;
                # only this same-surface continuation may inherit the binding.
                return replace(quote)
            if (written and columns and column_value_re.fullmatch(prefix)
                    and not speaking_re.search(prefix) and not historical_re.search(prefix)
                    and not column_break_re.search(prefix)):
                # Arbitrary column labels inherit this sentence's explicit
                # current paper declaration. Only the value is masked; speech,
                # state changes and other unquoted instructions remain visible.
                return replace(quote)
            if not enumeration_re.fullmatch(prefix):
                # A new unquoted clause needs its own physical-text binding.
                # In particular 可读 is a visibility property, not 读 speech.
                local_prefix = re.split(r"[，,；;]", prefix)[-1]
                scope_prefix = re.split(r"[；;]", prefix)[-1]
                suffix = re.split(r'[“"「『，,。；;！？!?\n]', clause[end:], maxsplit=1)[0]
                context = local_prefix + suffix
                historical = list(historical_re.finditer(scope_prefix))
                current = list(current_re.finditer(scope_prefix))
                historical_only = bool(historical and not (current and current[-1].start() > historical[-1].start()))
                written = bool(
                    (surface_re.search(context) or paper_surface_re.search(context))
                    and marking_re.search(context)
                    and not speaking_re.search(scope_prefix)
                    and not historical_only
                )
                paper_scope = bool(
                    written and paper_surface_re.search(local_prefix)
                    and not column_break_re.search(scope_prefix)
                    and not negative_marking_re.search(scope_prefix)
                )
                columns = bool(written and column_intro_re.search(local_prefix)
                               and re.search(r"现有|已有|现在|当前|此刻|眼下", scope_prefix)
                               and not column_break_re.search(scope_prefix))
            return replace(quote) if written or typed_quote else quote.group(0)

        return _QUOTED_DIALOGUE_RE.sub(choose, clause)

    # Sentence boundaries reset the binding; commas/semicolons may enumerate
    # several payloads after one explicit paper-text declaration. Protect only
    # the quotes, so a following actor/camera process still reaches the gate.
    text = re.sub(r'(?:[“"「『][^”"」』\n]*[”"」』]|[^。！？!?\n])+', protect_written_quotes, source_text)

    def protect_explicit(match: re.Match[str]) -> str:
        fragment = match.group(0)
        if "\ue000VISUAL" in fragment or re.search(r'[“"「『]', fragment):
            # Only the explicit marker needs protection from bracketed-dialogue
            # removal. The quoted payload is already protected, and a trailing
            # body/environment action must remain visible to the static guard.
            return re.sub(r"【(?:参考图锚点|可见文字|画内文字|道具文字|匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)】", replace, fragment)
        return replace(match)

    return _PROTECTED_VISUAL_FRAGMENT_RE.sub(protect_explicit, text), protected



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
    """Remove proved music bans without editing quotes or borrowing negation."""
    # A quoted statement is payload, not an instruction to this converter.
    text, protected = _protect_visual_text_fragments(str(value or ""))
    def protect_quote(match):
        token = f"\ue010MUSICQUOTE{len(protected)}\ue011"
        protected[token] = match.group(0)
        return token
    text = _QUOTED_DIALOGUE_RE.sub(protect_quote, text)
    mentioned = False
    negative_start = re.compile(r"^\s*(?:禁止|不要|不得|不使用|不生成|没有|不加|无)(?:任何)?")
    # These are known ban-list objects. An unfamiliar mixed list stays intact
    # for the active/ambiguous-music gate rather than inheriting guessed polarity.
    visual_item = re.compile(r"(?:字幕|水印|现代物件|现代元素|现代服饰|现代标识|现代logo|人物看镜头|塑料皮肤|过度锐化|雾化|烟雾|薄雾|柔焦|光晕|镜头扩散)", re.I)
    music_item = re.compile(r"(?:任何)?(?:让)?(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)?\s*(?:BGM|背景音乐|额外配乐|配乐|音乐)(?:响起|播放|进入|渐入|切入|铺开|持续|开始)?", re.I)
    connectors = re.compile(r"(?:以及|[、与和且及])")

    def clean_atom(original):
        nonlocal mentioned
        clause = original.strip()
        if not _MUSIC_TERM_RE.search(clause):
            return original
        # Matching the complete negative atom excludes 未禁止/不必没有 and
        # arbitrary prefixes. A later affirmative music clause remains visible.
        if _NEGATIVE_MUSIC_CLAUSE_RE.fullmatch(clause):
            mentioned = True
            return ""
        prefix = negative_start.match(clause)
        if prefix is None:
            return original
        rest = clause[prefix.end():]
        items = [item.strip() for item in connectors.split(rest)]
        if len(items) < 2 or not all(items):
            return original
        keep = []
        removed = False
        for item in items:
            if music_item.fullmatch(item):
                removed = True
                continue
            own_prefix = negative_start.match(item)
            noun = item[own_prefix.end():] if own_prefix else item
            if not visual_item.fullmatch(noun):
                return original
            keep.append(item)
        if not removed:
            return original
        mentioned = True
        if not keep:
            return ""
        retained = "、".join(keep)
        return retained if negative_start.match(retained) else prefix.group(0).strip() + retained

    parts = re.split(r"([；;。！？!?，,\n]+)", text)
    for index in range(0, len(parts), 2):
        original = parts[index]
        cleaned = clean_atom(original)
        if cleaned != original or not _MUSIC_TERM_RE.search(original):
            parts[index] = cleaned
            continue
        # Recognized noun enumeration was handled above. These conjunctions
        # introduce separate assertions when that whole-list proof failed.
        clauses = re.split(r"(但是|然而|不过|而且|并且|随后|然后|而后|接着|同时|但|却|且)", original)
        if len(clauses) > 1:
            atoms = [clean_atom(clauses[offset]) for offset in range(0, len(clauses), 2)]
            if atoms != clauses[::2]:
                retained = []
                for ordinal, atom in enumerate(atoms):
                    if not atom.strip():
                        continue
                    if retained and ordinal:
                        retained.append(clauses[2 * ordinal - 1])
                    retained.append(atom)
                parts[index] = "".join(retained)
    cleaned = "".join(parts)
    # Restore quote placeholders before visual placeholders in case one wraps
    # the other; no quoted words or internal punctuation are normalized.
    for token, fragment in reversed(list(protected.items())):
        cleaned = cleaned.replace(token, fragment)
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

    # Normalize each music prohibition before the visual scrubber removes audio.
    # Otherwise "禁止配乐。禁止字幕" can be swallowed as one audio clause.
    remainder = _ASPECT_RATIO_PHRASE_RE.sub("", _normalize_generation_limits(value))
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


def _is_allowed_nested_voice_label(field: str, label: str, value: Any) -> bool:
    """Distinguish the legal voice subheading from the retired shot field."""

    return bool(
        field == "音色"
        and label == "发声方式："
        and re.search(r"(?m)^\s*发声方式\s*[：:]\s*$", _text(value))
    )


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


def _keyframe_environment_process_hits(value: Any) -> list[str]:
    """Reject unambiguous environmental processes, preserving static results.

    This bounded grammar supplements the existing camera/time/audio guard. It
    never invents a still pose from a video instruction. Explicit visible text,
    stopped results and scoped prohibitions are not motion instructions.
    Keep this helper identical in the standalone validator and materializer.
    """
    text = _keyframe_instruction_text(value)
    motion_re = re.compile(
        r"位移|移动|摆动|晃动|摇动|摇曳|起伏|飘动|抖动|颤动|漂移|流动|闪烁|明灭|变化"
    )
    subject_re = re.compile(
        r"衣摆|衣袖|袖口|袖摆|裙摆|下摆|布料|帘布|帘幕|窗帘|窗纱|窗纸|"
        r"树叶|叶片|枝叶|枝条|树枝|烛焰|烛火|火焰|水面|水流|光斑|影子|树影"
    )
    temporal_re = re.compile(
        r"随(?:着)?|受.{0,20}(?:动作|风)|产生|发生|持续|连续|不断|逐渐|渐渐|"
        r"慢慢|正在|开始|继续|仍在|仍然|反复"
    )
    restart_re = re.compile(r"正在|开始|继续|持续|不断|逐渐|渐渐|仍在|仍然|产生|发生|允许")
    negative_re = re.compile(
        r"(?:禁止|不得|不要|避免|不再|没有|未曾|并未|未见|尚未|不(?!断|停|住)|无)"
        r"[^，,；;。！？!?、\n]{0,16}$"
    )
    hits = []
    for major in re.split(r"[；;。！？!?，,\n]|但是|然而|反而|但|却", text):
        inherited_negative = False
        for ordinal, part in enumerate(major.split("、")):
            clause = part.strip()
            if not clause:
                continue
            unlabeled = re.sub(r"^[^：:]{1,24}[：:]", "", clause).strip()
            if ordinal == 0:
                inherited_negative = bool(re.match(r"禁止|不得|不要|避免|没有|未见|无", unlabeled))
            for match in motion_re.finditer(clause):
                prefix, suffix = clause[:match.start()], clause[match.end():]
                temporal = bool(temporal_re.search(prefix))
                if not (subject_re.search(prefix) or temporal):
                    continue
                # Light/material variation can be a static appearance.
                if match.group() == "变化" and not temporal:
                    continue
                not_stopped = bool(re.search(
                    r"(?:没有|并未|未曾|尚未|未|不会|不)(?:停止|停下|停住)(?:了)?$", prefix
                ))
                if not not_stopped:
                    if negative_re.search(prefix) or re.search(r"(?:停止|停下|停住)(?:了)?$", prefix):
                        continue
                    if inherited_negative and not restart_re.search(prefix):
                        continue
                # A completed movement followed by a concrete stopped result is
                # still representable; a later positive movement is checked again.
                if re.match(r"(?:后|之后)(?:已|已经)?(?:停在|停于|停稳|静止|定格在|固定在)", suffix):
                    continue
                # The stopped result can follow the motion noun as well as
                # precede it. Require completion, not "尚未停止" or a question.
                if re.match(
                    r"(?:已经|已)(?:完全|彻底)?(?:停止|停下|停住|停)(?:了)?(?:$|在|于)",
                    suffix,
                ):
                    continue
                hits.append("environment_process=" + clause)
    return sorted(set(hits))



def _keyframe_instruction_text(value: Any) -> str:
    """Ignore written quoted payloads, but still check surrounding directives."""
    text, protected = _protect_visual_text_fragments(value)
    for token, fragment in protected.items():
        instruction = _QUOTED_DIALOGUE_RE.sub("", fragment)
        if instruction == fragment:
            literal = re.match(r"(?:【(?:可见文字|画内文字|道具文字|匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)】|(?:牌匾|匾额|门牌|招牌)?(?:可见文字|画内文字|道具文字)[:：])", fragment)
            if literal:
                instruction = literal.group(0)
        text = text.replace(token, instruction)
    return text


def _keyframe_generation_limits(value: str) -> str:
    """Remove only recognized video-eye policy from the static projection.

    A matching sentence/list must consist entirely of the known policy. Mixed
    or unfamiliar instructions are retained and rejected by the static guard;
    we never guess how their negation should bind to a remaining visual item.
    """
    positive = {
        "单镜单一主机位、单一核心动作阶段",
        "眼球轻微换焦与位移、眼睑松紧按画面内容执行",
        "眼球轻微换焦与位移", "眼睑松紧按画面内容执行",
        "保留轻微眼球焦点调整和眼睑松紧变化",
        "眼球换焦或位移", "眼睑松紧变化",
        "眼部动作按本镜已列事件执行",
        "未列出眼部动作时保持已声明的自然状态，不额外添加模板动作",
    }
    negative = {
        "禁止无依据全程不眨眼", "禁止高频机械眨眼", "禁止多人同步眨眼",
    }
    instruction, protected = _protect_visual_text_fragments(value)
    parts = re.split(r"([。；;\n])", instruction)
    for index in range(0, len(parts), 2):
        clause = parts[index].strip()
        video_policy = re.fullmatch(r"(?:视频|动态画面)中(?:的)?(.+?)按画面内容(?:所列时点)?执行", clause)
        video_eye_items = {"换焦", "眼球换焦", "眼球位移", "眼睑", "眼睑变化", "眼睑松紧变化", "眨眼"}
        video_only = bool(video_policy) and all(
            item in video_eye_items for item in re.split(r"[、与和]", video_policy.group(1))
        )
        if clause in negative or clause in positive or video_only:
            parts[index] = ""
            continue
        # The standard positive clause follows an independent static gaze.
        for gaze in ("眼神自然聚焦", "人物眼神自然聚焦"):
            if clause.startswith(gaze + "，") and clause[len(gaze + "，"):] in positive:
                parts[index] = gaze
    result = re.sub(r"[。；;\n]{2,}", "；", "".join(parts)).strip("。；;\n ")
    for token, fragment in protected.items():
        result = result.replace(token, fragment)
    return result


def _keyframe_eye_process_hits(value: Any) -> list[str]:
    text = _keyframe_instruction_text(value)
    motion = re.compile(
        r"(?:眼球|视线|目光)[^，,。；;\n]{0,14}(?:换焦|位移|焦点调整|转移|扫视)|"
        r"眼睑[^，,。；;\n]{0,14}(?:松紧|变化|开合)|"
        r"眨眼|半眨(?:眼)?"
    )
    hits = []
    if re.search(r"(?:视频|动态画面)中(?:的)?[^。；;\n]*按画面内容(?:所列时点)?执行", text):
        hits.append("unprojected_video_policy")
    if any(phrase in text for phrase in (
        "眼部动作按本镜已列事件执行", "未列出眼部动作时保持已声明的自然状态",
    )):
        hits.append("unprojected_event_policy")
    for clause in re.split(r"[，,。；;\n]", text):
        # A still can require open eyes and explicitly exclude a half blink.
        # Such exclusion is not a command to animate eyelids.
        if re.search(r"(?:避开|避免|不处于|不是|排除)(?:半眨眼?|闭眼)(?:瞬间|状态|中途|截帧)?", clause):
            clause = re.sub(r"(?:避开|避免|不处于|不是|排除)(?:半眨眼?|闭眼)(?:瞬间|状态|中途|截帧)?", "", clause)
        if motion.search(clause):
            hits.append("eye_process=" + clause)
    return sorted(set(hits))


def _keyframe_authored_process_hits(value: Any) -> list[str]:
    """Reject observed unprojectable body/audio processes without inventing a pose."""
    text = _keyframe_instruction_text(value)
    patterns = (
        r"(?:站立|站姿|蹲姿|蹲下)(?:至|到|转为)(?:站姿|蹲姿|蹲下|站立)",
        r"(?:蹲下|站起|转身|抬手)后(?:仍|再|才|将|留)",
        r"(?:仍在|正在|正从|从|由)[^，,。；;\n]{0,36}(?:接近|随行|前行|行走)",
        r"(?:沿|向)[^，,。；;\n]{0,18}(?:返院|返[^，,。；;\n]{1,8}(?:院|厅|房|殿))",
        r"(?:在|跟在)[^，,。；;\n]{0,20}(?:随行|跟在[^，,。；;\n]{0,10}后方)",
        r"正在(?:书写|落笔|抄写|换衣|走动|接取|递交)",
        r"(?:将要|即将|将)(?:写有|写着|写下|刻有|标注)",
        r"(?:能够|可以|能|已)?(?:听清|听见|听到)(?:低声|交代|话语|脚步|呼吸)?",
        r"(?:说|问|答|喊)(?:道)?[:：]?$",
    )
    hits = []
    # Only quoted written payloads have been masked above. Commas do not end
    # a historical/narrated attribution. An explicit later current binding may
    # coexist with removed historical quotation; otherwise no current readable
    # inscription has been supplied for this static field. Fail closed instead
    # of dropping neighboring camera/actor instructions or inventing contents.
    for line in text.splitlines():
        written = re.search(r"(?:纸面|纸上|纸签|记录纸|账册|册页|书页|木牌|标签|门牌)[^，,；;。\n]{0,32}(?:写着|写有|刻着|刻有|印着|印有|可读文字|现有文字)", line)
        if not written:
            continue
        attribution = list(re.finditer(r"昨日|昨天|前日|前一日|从前|以前|当年|当时|曾经|回忆|旁白转述|旁白说明|旁白记述", line[:written.end()]))
        current_bindings = []
        for scope in re.finditer(r"[^；;]+", line):
            part = scope.group()
            if re.search(r"旁白|转述|心理内声|说道", part):
                continue
            for current in re.finditer(r"现在|当前|此刻|眼下", part):
                if re.search(r"(?:纸面|纸上|纸签|记录纸|账册|册页|书页|木牌|标签|门牌)[^，,；;。\n]{0,32}(?:写着|写有|刻着|刻有|印着|印有|可读文字|现有文字)", part):
                    current_bindings.append(scope.start() + current.start())
        if attribution and not (current_bindings and current_bindings[-1] > attribution[-1].start()):
            hits.append("written_scope_not_current=" + line)
    for clause in re.split(r"[，,。；;\n]|但是|然而|但|却|随后|然后", text):
        for pattern in patterns:
            for match in re.finditer(pattern, clause):
                before, after = clause[:match.start()], clause[match.end():]
                # A prohibition can include the explicitly authored subject.
                # Contrast/restart clauses above must not inherit its negation.
                negated = re.search(r"(?:禁止|不要|不得|并未|没有|未曾|尚未)$", before)
                subject_ban = re.search(r"(?:^|[:：])(?:禁止|不要|不得)([^，,。；;\n]{1,12})$", before.strip())
                if subject_ban:
                    # This must be a nominal prohibited subject, not a clause
                    # about another predicate (禁止喧哗时暖轿由…).
                    subject = subject_ban.group(1)
                    if not re.search(r"的|时|之后|以前|以后|在|里|中|有|无|并|且|又|再", subject):
                        negated = subject_ban
                double_negative = re.search(r"(?:并非|并不是|不是|不再|不|没有)(?:没有|禁止|不要|不得)[^，,。；;\n]{0,16}$", before)
                if negated and not double_negative:
                    continue
                stopped = re.search(r"(?:已|已经)(?:到达|站定|停稳|停止)", after)
                if stopped and not re.search(r"又|再|继续", after):
                    if not re.search(r"(?:并非|不是|没有|尚未)[^，,。；;\n]{0,6}$", after[:stopped.start()]):
                        continue
                hits.append("authored_process=" + clause)
    return sorted(set(hits))


def _visual_only_text(
    value: str, *, strip_dialogue: bool = False,
    strict_static_quotes: bool = False, field_name: str = "",
) -> str:
    """Keep only visible still-frame semantics from any projected field."""

    result = value
    protected_visual: dict[str, str] = {}
    if strip_dialogue:
        result, protected_visual = _protect_visual_text_fragments(result, field_name=field_name)
        if strict_static_quotes:
            unbound = _QUOTED_DIALOGUE_RE.search(result)
            if unbound:
                raise ValueError(
                    "keyframe_static_contract_violation: unbound_visual_text; "
                    f"field={field_name or '<static>'}; masked_offset={unbound.start()}"
                )
    result = _TIMECODE_RE.sub("", result)
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
        minor_parts = re.split(r"([，,、]|并且|并伴随|同时伴随|并有)", major)
        for minor_index in range(0, len(minor_parts), 2):
            minor = minor_parts[minor_index]
            clause = minor.strip()
            if not clause:
                continue
            # A bare speech-verb suffix is not evidence of speech: "南门通道"
            # must retain its final character. Ambiguous wording stays intact
            # for the static contract guard instead of being silently shortened.
            clause = re.sub(
                r"(?:(?:低声|轻声|高声|平静地|克制地)(?:开口)?|开口)"
                r"(?:说道|问道|答道|喊道|说|问|答|喊|道)\s*[:：]?\s*$",
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
            # Preserve enumeration scope. Turning 顿号 into a comma would make
            # a later negative list item look like an independent assertion.
            separator = "、" if minor_index and minor_parts[minor_index - 1] == "、" else "，"
            visible_minor.append((separator if visible_minor else "") + clause)
        if visible_minor:
            visible_major.append("".join(visible_minor))
    result = "；".join(visible_major)
    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"([；;，,])\s*(?=[；;，,。])", "", result)
    if strict_static_quotes:
        missing = [token for token, fragment in protected_visual.items()
                   if _QUOTED_DIALOGUE_RE.search(fragment) and token not in result]
        if missing:
            raise ValueError(
                "keyframe_static_contract_violation: dropped_visual_text; "
                f"field={field_name or '<static>'}; payload_count={len(missing)}"
            )
    for token, fragment in protected_visual.items():
        result = result.replace(token, fragment)
    return result.strip(" \n；;，,。:：")


def _visual_only_picture_content(value: str) -> str:
    """Backward-compatible alias for the all-field visual scrubber."""

    return _visual_only_text(value, strip_dialogue=True)


def compile_formal_merged_field(
    shot: dict[str, Any],
    *,
    assume_compact: bool = False,
    _legacy_layout: bool = False,
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
            f"{'可见主体：' if _legacy_layout else ''}{_text(compact.get('可见主体'))}"
        ),
    ]
    for field in (LEGACY_FORMAL_MERGED_ATOMIC_FIELDS if _legacy_layout else FORMAL_MERGED_ATOMIC_FIELDS):
        value = display_picture(compact) if field == "画面内容" else _text(compact.get(field))
        paragraphs.append(f"音色：\n\n{value}" if field == "音色" else f"{field}：{value}")
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
    context_text = "\n".join(context_instruction_text(_text(compact.get(field)), field)
                             for field in ("镜头类型", "可见主体", *FORMAL_MERGED_ATOMIC_FIELDS))
    context_hits = _contract_hits(context_text, BLANK_CONTEXT_FORBIDDEN_TERMS)
    if context_hits:
        raise ValueError(
            "formal_master_context_violation: cross-shot references are "
            "forbidden: " + ", ".join(context_hits)
        )
    assert_field_content(compact, mask=context_instruction_text)
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
    assert_field_content(compact, mask=context_instruction_text)
    _assert_projected_aspect_contract(
        ASPECT_RATIO_TOKEN + "\n" + _text(compact.get("参考图锚定")),
        projection="reference_anchor",
    )
    normalized = dict(compact)
    normalized["生成限制"] = _keyframe_generation_limits(_generation_limits_without_aspect(
        compact.get("生成限制")
    ))
    values = {field: _visual_only_text(_text(normalized.get(field)),
                strip_dialogue=True, strict_static_quotes=True, field_name=field)
              for field in ("空间站位描述", "起始状态", "机位")}
    values.update({field: _text(normalized.get(field)) for field in
                   ("镜头类型", "可见主体", "光影", "光源来源", "光源方向", "色温倾向")})
    from .field_content import appearance_anchor_text, assert_static_text_preserved
    for field in ("空间站位描述", "起始状态", "机位"):
        assert_static_text_preserved(_text(normalized.get(field)), values[field], field)
    appearance = appearance_anchor_text(_text(compact.get("参考图锚定")))
    if appearance:
        original_appearance = appearance
        appearance = _visual_only_text(appearance, strip_dialogue=True,
                                      strict_static_quotes=True, field_name="参考图锚定")
        assert_static_text_preserved(original_appearance, appearance, "参考图锚定.文字造型锚")
    limits = _visual_only_text(normalized["生成限制"], strip_dialogue=True,
                              strict_static_quotes=True, field_name="生成限制")
    sections = r5_keyframe_sections(values, aspect=ASPECT_RATIO_TOKEN,
                                   visual_limits=limits, appearance_anchor=appearance)
    prompt = "\n".join(["GPT关键帧生成指令"] + [f"{label}：{value}" for label, value in sections])
    _assert_projected_aspect_contract(prompt, projection="gpt_keyframe")
    instruction_text = _keyframe_instruction_text(prompt)
    dynamic_hits = sorted(
        {match.group(0) for match in KEYFRAME_FORBIDDEN_RE.finditer(instruction_text)}
        | set(_keyframe_environment_process_hits(prompt))
        | set(_keyframe_eye_process_hits(prompt))
        | set(_keyframe_authored_process_hits(prompt))
    )
    unobservable_hits = sorted(
        {match.group(0) for match in KEYFRAME_UNOBSERVABLE_RE.finditer(instruction_text)}
    )
    context_hits = _contract_hits(instruction_text, BLANK_CONTEXT_FORBIDDEN_TERMS)
    if dynamic_hits or context_hits or unobservable_hits:
        reasons = []
        if dynamic_hits:
            reasons.append("dynamic=" + ",".join(dynamic_hits))
        if context_hits:
            reasons.append("cross_shot=" + ",".join(context_hits))
        if unobservable_hits:
            reasons.append("unobservable=" + ",".join(unobservable_hits))
        raise ValueError(
            (
                "keyframe_unobservable_contract_violation: "
                if unobservable_hits
                else "keyframe_static_contract_violation: "
            )
            + "; ".join(reasons)
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
        compact["音色"] = _deduplicated_join(
            source.get("音色"),
            f"历史发声参数（待按固定音色子模板修订）：{voice}",
        )
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
        and not _is_allowed_nested_voice_label(field, label, compact[field])
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
                    if label in value and not _is_allowed_nested_voice_label(
                        field, label, value
                    ):
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


# Frozen v0.5.3 transitional projector.  A short-lived deployed exporter used
# the current short ten-column header while still writing the eight-column
# canonical sidecar envelope.  Those immutable workbooks cannot be verified by
# either the v0.5.0 projector or today's stricter projector.  This code is
# read-only compatibility for the controlled handoff revalidation path; new
# exports never call it.
LEGACY_V053_FORMAL_FIELDS = (
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
LEGACY_V053_TIMECODE_RE = re.compile(
    r"(?:\[|【)?\s*\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*"
    r"\d+(?:\.\d+)?\s*(?:秒|s)\s*(?:\]|】|[:：])?",
    flags=re.IGNORECASE,
)
LEGACY_V053_AUDIO_ONLY_CLAUSE_MARKERS = (
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


def _legacy_v053_text(value: Any) -> str:
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


def _legacy_v053_seconds_label(value: Any) -> str:
    text = _legacy_v053_text(value)
    if not text:
        return ""
    text = re.sub(
        r"\s*(?:秒|s)$", "", text, flags=re.IGNORECASE
    ).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        text = f"{float(text):.1f}"
    return f"{text}s"


def _legacy_v053_visual_only_clauses(value: Any) -> str:
    result = LEGACY_V053_TIMECODE_RE.sub(
        "", _legacy_v053_text(value)
    )
    result = re.sub(
        r"[\u3400-\u9fffA-Za-z·]{1,20}说道[：:]?\s*[“\"]"
        r"[^”\"\n]+[”\"]",
        "",
        result,
    )
    result = re.sub(
        r"【(?!\d+\.\d—|参考图锚点)[^】\n]{1,20}】\s*[“\"]?"
        r"[^。！？!?；;\n]*[”\"]?",
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
        for minor in re.split(
            r"[，,、]|并伴随|同时伴随|伴随", major
        ):
            clause = minor.strip(" 。；;")
            if not clause or forbidden.search(clause):
                continue
            minor_visible.append(clause)
        if minor_visible:
            visible.append("，".join(minor_visible))
    return "；".join(visible).strip("；。 ")


def _legacy_v053_function_scene_cell(shot: dict[str, Any]) -> str:
    function = _legacy_v053_text(shot.get("功能类型"))
    scene = _legacy_v053_text(shot.get("场景连续性ID"))
    if function and scene:
        return f"功能类型：{function}\n场景连续性ID：{scene}"
    if function:
        return f"功能类型：{function}"
    if scene:
        return f"场景连续性ID：{scene}"
    return ""


def _legacy_v053_formal_master(shot: dict[str, Any]) -> str:
    shot_id = _legacy_v053_text(shot.get("镜头编号"))
    duration = _legacy_v053_seconds_label(shot.get("时长"))
    cumulative = _legacy_v053_seconds_label(shot.get("累计时长"))
    timing = ""
    if duration and cumulative:
        timing = f"（{duration}；累计{cumulative}）"
    elif duration:
        timing = f"（{duration}）"
    paragraphs = [
        f"镜头序号：{shot_id}{timing}" if shot_id else "镜头序号：",
        (
            f"镜头类型：{_legacy_v053_text(shot.get('镜头类型'))}"
            f"｜可见主体：{_legacy_v053_text(shot.get('可见主体'))}"
        ),
    ]
    paragraphs.extend(
        f"{field}：{_legacy_v053_text(shot.get(field))}"
        for field in LEGACY_V053_FORMAL_FIELDS
        if field not in {"镜头类型", "可见主体"}
    )
    return "\n\n".join(paragraphs)


def _legacy_v053_gpt_keyframe(shot: dict[str, Any]) -> str:
    start_state = _legacy_v053_visual_only_clauses(
        shot.get("起始状态")
    ) or "人物保持本镜开场的可见站位、朝向、姿态与道具状态"
    environment = _legacy_v053_visual_only_clauses(
        shot.get("环境动态")
    ) or "场景内可见物保持真实、克制的自然状态"
    anchor = _legacy_v053_text(shot.get("参考图锚定")) or "无"
    return (
        "真人古装短剧，真实摄影机拍摄质感；"
        f"镜头类型与主体：{_legacy_v053_text(shot.get('镜头类型'))}，"
        f"{_legacy_v053_text(shot.get('可见主体'))}；"
        f"空间站位：{_legacy_v053_visual_only_clauses(shot.get('空间站位描述'))}；"
        f"定格状态：{start_state}；"
        f"摄影机：{_legacy_v053_visual_only_clauses(shot.get('机位'))}；"
        f"光影：{_legacy_v053_visual_only_clauses(shot.get('光影'))}；"
        f"光源来源：{_legacy_v053_visual_only_clauses(shot.get('光源来源'))}；"
        f"光源方向：{_legacy_v053_visual_only_clauses(shot.get('光源方向'))}；"
        f"色彩：{_legacy_v053_visual_only_clauses(shot.get('色温倾向'))}；"
        f"环境可见状态：{environment}；"
        f"参考图锚定：{_legacy_v053_visual_only_clauses(anchor)}；"
        "单一稳定视觉时刻，禁止字幕、水印、现代物件、人物看镜头、"
        "复制脸、上下黑边、无来源光、重HDR、塑料皮肤与海报摆拍。"
    )


def legacy_v053_mother_template_rows(
    storyboard: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reproduce the immutable transitional v0.5.3 projection."""

    shots = storyboard.get("shots")
    if not isinstance(shots, list):
        raise ValueError(
            "materialization_storyboard_invalid: shots must be an array"
        )
    return [
        {
            "分镜序号": shot.get("镜头编号"),
            "时长": shot.get("时长"),
            "累计时长": shot.get("累计时长"),
            "功能类型/场景连续性ID": (
                _legacy_v053_function_scene_cell(shot)
            ),
            FORMAL_MERGED_FIELD: _legacy_v053_formal_master(shot),
            GPT_KEYFRAME_FIELD: _legacy_v053_gpt_keyframe(shot),
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
    _legacy_layout: bool = False,
) -> list[dict[str, Any]]:
    """Project one canonical; the old layout is read-only workbook compatibility."""

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
                    _legacy_layout=_legacy_layout,
                ),
                GPT_KEYFRAME_FIELD: compile_gpt_keyframe_instruction(
                    shot,
                    assume_compact=True,
                ),
                "参考图锚定": shot.get("参考图锚定"),
                "覆盖原文": shot.get("覆盖原文"),
                "原文依据类型": shot.get("原文依据类型"),
                "原文支撑句": shot.get("原文支撑句"),
                **({} if _legacy_layout else {"光影": project_lighting(shot, _text)}),
            }
        )
    return rows
