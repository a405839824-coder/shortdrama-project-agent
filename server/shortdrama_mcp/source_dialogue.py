from __future__ import annotations

"""Candidate-independent source dialogue authority parser.

The storyboard candidate is intentionally absent from this module's parsing
API.  Character inventories, source ledgers, cognition matrices, and anchors
are production claims; allowing any of them to teach the parser a speaker
would make THG-05 circular.  This parser derives every audible occurrence from
the imported source bytes (after deterministic extraction) or fails closed.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


SOURCE_DIALOGUE_PARSER_VERSION = "source-dialogue-authority-v6"
SOURCE_DIALOGUE_OCCURRENCE_CONTRACT = "source-dialogue-occurrence-ranges-v1"


class SourceDialogueAuthorityError(ValueError):
    """The source contains speech-like text that cannot be attributed safely."""


@dataclass(frozen=True)
class DialogueOccurrence:
    speaker: str
    text: str
    line_number: int
    char_offset: int
    kind: str
    occurrence_id: str = ""
    source_text_sha256: str = ""
    source_ordinal: int = -1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_TOKEN_RE = re.compile(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}")
_PRONOUN_SPEAKERS = {
    "他",
    "她",
    "它",
    "他们",
    "她们",
    "它们",
    "其",
    "此人",
    "那人",
    "有人",
    "一人",
}
_NON_DIALOGUE_LABELS = {
    "字幕",
    "画外字幕",
    "备注",
    "注释",
    "音效",
    "环境音效",
    "画面",
    "镜头",
    "手机屏幕",
    "手机屏幕上",
    "手机画面",
    "手机短信",
    "短信",
    "屏幕",
    "屏幕文字",
    "电视画面",
    "场景",
    "转场",
    "时间",
    "地点",
    "动作",
    "舞台提示",
    "信件",
    "信件内容",
    "信上",
    "信中",
    "书信",
    "纸条",
    "纸上",
    "牌匾",
    "标题",
    "道具",
    "服装",
    "表情",
    "台词",
    "对白",
    "景别",
    "计划",
}
_VISUAL_LABEL_ALIASES = {"osd", "ui"}
_VISUAL_LABEL_FAMILY_RE = re.compile(
    r"(?:"
    r"手机(?:屏幕(?:上)?|界面|短信|画面)|"
    r"(?:电视|监控)(?:屏幕|画面)|"
    r"(?:信件|纸条)(?:内容)?|来信|"
    r"(?:画外)?字幕(?:内容|显示)?|"
    r"画面(?:显示)?|镜头(?:内文字)?|"
    r"屏幕(?:文字)?|短信|微信|聊天框|公告|告示|标题(?:字)?|屏显|旁注|"
    r"osd|ui"
    r")",
    re.I,
)
_SCENE_RESET_LABELS = {"场景", "镜头", "转场", "时间", "地点"}
_ACTION_PARTS = (
    "忍不住",
    "压低声音",
    "提高声音",
    "叹了口气",
    "冷冷地",
    "平静地",
    "慢慢地",
    "轻轻地",
    "突然",
    "忽然",
    "缓缓",
    "随即",
    "随后",
    "当即",
    "接着",
    "继续",
    "笑着",
    "哭着",
    "冷笑",
    "苦笑",
    "微笑",
    "挑眉",
    "咬牙",
    "皱眉",
    "抬眼",
    "抿唇",
    "摇头",
    "颔首",
    "起身",
    "俯身",
    "转身",
    "脱口而出",
    "压住怒气",
    "低声",
    "轻声",
    "柔声",
    "沉声",
    "厉声",
    "高声",
    "小声",
    "大声",
    "冷声",
    "轻轻",
)
_OUTER_CUE_PREFIXES = ("这时", "此时", "随后", "紧接着")
_SPEECH_VERBS = (
    "忍不住说道",
    "开口说道",
    "开口说",
    "反问道",
    "回应道",
    "解释道",
    "解释说",
    "问道",
    "说道",
    "喊道",
    "叫道",
    "答道",
    "念道",
    "读道",
    "说",
    "问",
    "喊",
    "叫",
    "答",
    "念",
    "读",
    "道",
)
_SPEAKER_FORBIDDEN_PARTS = tuple(
    sorted(
        set(_ACTION_PARTS)
        | set(_SPEECH_VERBS)
        | {
            "地",
            "着",
            "又",
            "正",
            "开口",
            "出声",
            "对",
            "向",
            "冲",
            "朝",
            "与",
            "和",
            "跟",
            "同",
            "齐声",
            "回应",
            "解释",
            "反问",
            "抿唇",
            "摇头",
            "颔首",
            "起身",
            "脱口而出",
        },
        key=lambda value: (-len(value), value),
    )
)
_STAGE_DIRECTION_TERMS = {
    "低声",
    "轻声",
    "柔声",
    "沉声",
    "厉声",
    "高声",
    "小声",
    "大声",
    "冷声",
    "停顿",
    "沉默",
    "转身",
    "入场",
    "退场",
    "画外",
    "旁白起",
    "冷笑",
    "苦笑",
    "微笑",
    "挑眉",
    "抿唇",
    "摇头",
    "颔首",
    "起身",
    "俯身",
    "转身",
    "压住怒气",
    "空镜",
    "全景",
    "白场",
    "黄昏",
    "蒙太奇",
    "计划",
    "景别",
    "台词",
    "对白",
}
_QUOTE_PAIRS = {
    "“": "”",
    '"': '"',
    "'": "'",
    "‘": "’",
    "「": "」",
    "『": "』",
    "«": "»",
    "‹": "›",
    "＂": "＂",
    "〝": "〞",
    "﹁": "﹂",
    "﹃": "﹄",
    "〔": "〕",
}
_QUOTE_OPEN_CLASS = re.escape("".join(_QUOTE_PAIRS))
_QUOTE_CLOSE_CLASS = re.escape("".join(_QUOTE_PAIRS.values()))
_QUOTE_LIKE_CLASS = re.escape(
    "".join(dict.fromkeys((*_QUOTE_PAIRS.keys(), *_QUOTE_PAIRS.values())))
)
_ROLE_SUFFIXES = (
    "氏",
    "夫人",
    "小姐",
    "公子",
    "嬷嬷",
    "婶儿",
    "婶",
    "母亲",
    "父亲",
    "老爷",
    "世子",
    "侯爷",
    "王爷",
    "殿下",
    "皇上",
    "皇后",
    "太后",
    "掌柜",
    "管家",
    "大人",
    "将军",
    "姑娘",
    "少爷",
    "师父",
    "师兄",
    "师姐",
    "哥哥",
    "姐姐",
    "弟弟",
    "妹妹",
    "大嫂",
    "嫂",
    "姨娘",
    "娘",
    "爹",
)
_KNOWN_SOURCE_SPEAKER_ATOMS = {
    "旁白",
    "画外音",
    "众人",
    "百姓",
    "侍卫",
    "丫鬟",
    "小厮",
    "店小二",
    "老夫人",
}
_COMPOUND_SURNAMES = {
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公羊",
    "澹台", "公冶", "宗政", "濮阳", "淳于", "单于", "太叔", "申屠",
    "公孙", "仲孙", "轩辕", "令狐", "钟离", "宇文", "长孙", "慕容",
    "司徒", "司空", "南宫", "夏侯",
}
_COMMON_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华"
    "金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方"
    "俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮"
    "卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计"
    "伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江"
    "童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经"
    "房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴"
    "陆荣翁荀羊惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗"
    "山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹"
    "束龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔"
    "阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛"
    "寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古"
    "易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越"
    "夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙养鞠须丰巢关蒯"
    "相查后荆红游竺权逯盖益桓公"
)

_PLAIN_HAN_NAME_EXCLUSIONS = frozenset(
    {
        *_NON_DIALOGUE_LABELS,
        *_STAGE_DIRECTION_TERMS,
        *_PRONOUN_SPEAKERS,
        "角色",
        "人物",
        "场次",
        "场号",
    }
)
_PLAIN_HAN_NAME_STRUCTURAL_TERMS = frozenset(
    term
    for term in (
        *_NON_DIALOGUE_LABELS,
        *_STAGE_DIRECTION_TERMS,
        "角色",
        "人物",
        "场次",
        "场号",
    )
    if len(term) >= 2
)


def _alternation(values: Iterable[str]) -> str:
    return "|".join(
        re.escape(value)
        for value in sorted(values, key=lambda item: (-len(item), item))
    )


_TOKEN_PATTERN = r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}?"
_ACTION_PATTERN = rf"(?:(?:{_alternation(_ACTION_PARTS)})\s*){{0,4}}"
_VERB_PATTERN = rf"(?:{_alternation(_SPEECH_VERBS)})"
_PREFIX_PATTERN = rf"(?:(?:{_alternation(_OUTER_CUE_PREFIXES)})\s*)?"
_CUE_RE = re.compile(
    rf"(?P<boundary>^|[。！？!?；;，,\n{_QUOTE_CLOSE_CLASS}]\s*|…+\s*)"
    rf"{_PREFIX_PATTERN}"
    rf"(?P<speaker>{_TOKEN_PATTERN})"
    rf"(?P<delivery>{_ACTION_PATTERN})"
    rf"(?P<verb>{_VERB_PATTERN})"
    rf"\s*(?P<separator>：|:|，|,|——|—|-)?\s*(?P<open>[{_QUOTE_OPEN_CLASS}])?"
)
_SPEECH_LIKE_RE = re.compile(
    rf"(?:{_VERB_PATTERN})\s*(?:：|:|，|,|——|—|-|[{_QUOTE_OPEN_CLASS}])"
)
_UNSUPPORTED_SPEECH_PUNCT_RE = re.compile(
    rf"(?P<verb>{_VERB_PATTERN})(?P<separator>[^\w\s\u3400-\u4dbf\u4e00-\u9fff])(?=\s*\S)"
)
_ROLE_RESIDUE_SEPARATOR_RE = re.compile(
    r"(?P<boundary>^|[。！？!?；;\n])\s*"
    r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})\s*"
    r"(?P<separator>，|,|——|—|–|―)(?=\s*\S)"
)
_UNSUPPORTED_SPEECH_LEXEMES = (
    "吩咐", "命令", "喝令", "警告", "提醒", "劝告", "劝道", "央求",
    "请求", "恳求", "祈求", "哀求", "嘱咐", "叮嘱", "告诫", "呵斥",
    "斥责", "训斥", "怒斥", "喝斥", "责问", "质问", "追问", "盘问",
    "反驳", "辩解", "解释", "补充", "强调", "声明", "宣布", "承认",
    "否认", "保证", "承诺", "威胁", "恐吓", "嘀咕", "嘟囔", "低语",
    "耳语", "呢喃", "喃喃自语", "自言自语", "咕哝", "咆哮", "怒吼",
    "嘶吼", "惊呼", "欢呼", "嚷嚷", "叫嚷", "插嘴", "接话", "搭话",
    "附和", "应声", "回应", "回答", "复述", "改口", "笑道", "哭诉",
    "感叹", "叹道", "吐槽", "调侃", "讥讽", "奚落", "催促", "阻止",
    "拒绝", "答应", "喝问", "盘诘", "诘问", "诘责", "驳斥", "申辩",
    "申诉", "陈述", "陈言", "告知", "通知", "告白", "吐露", "透露",
    "喊话", "呼喊", "吆喝", "嚎叫", "哭喊", "哀叹", "抱怨", "埋怨",
    "责骂", "咒骂", "辱骂", "训话", "发问", "询问", "征询", "追询",
    "应答", "作答", "回话", "回嘴", "抢白", "插言", "接茬", "搭腔",
    "附声", "赞同", "戏谑", "挖苦", "嘲讽", "劝说", "规劝", "安慰",
    "鼓励", "致歉", "道歉", "感谢", "祝贺", "祝福", "宣称", "声称",
    "表态", "抱怨道",
)
_STRONG_DELIMITERLESS_SPEECH_LEXEMES = (
    "喃喃自语", "自言自语", "忍不住嘀咕", "命令", "喝令", "呵斥",
    "斥责", "训斥", "怒斥", "喝斥", "责问", "质问", "追问", "盘问",
    "嘀咕", "嘟囔", "低语", "耳语", "呢喃", "咕哝", "咆哮", "怒吼",
    "嘶吼", "惊呼", "欢呼", "嚷嚷", "叫嚷", "哭诉", "喊话", "呼喊",
    "吆喝", "嚎叫", "哭喊", "责骂", "咒骂", "辱骂", "训话", "喝问",
)
_NARRATIVE_ACTION_PREDICATES = tuple(
    dict.fromkeys(
        (
            *_ACTION_PARTS,
            "推门", "开门", "关门", "进门", "出门", "喝茶", "饮茶", "端茶",
            "放茶", "拿信", "拿起", "接过", "接过纸条", "收起", "递出",
            "点头", "抬手", "挥手", "握拳", "咬唇", "抬头", "低头", "抬眸",
            "垂眸", "看书", "看向", "望向", "扫视", "推窗", "推开", "拉开",
            "掀帘", "落座", "坐下", "站起", "跪下", "执笔", "落笔", "取伞",
            "翻页", "翻书", "合书", "放灯", "走近", "走到", "走向", "走入",
            "走出", "离开", "跨入", "跑来", "跑去", "停下", "回头", "伸手",
            "收手", "打开", "合上", "抱起", "扶起", "后退", "上前",
            "锁门", "穿衣", "梳头", "洗手", "倒茶", "端碗", "夹菜", "拾柴",
            "生火", "浇花", "扫地", "擦桌", "铺床", "收衣", "抱起孩子",
            "牵马", "上车", "下车", "登楼", "下楼", "过桥", "绕路", "进城",
            "出城", "抬箱", "推车", "挖土", "种树", "摘花", "折纸", "系带",
            "戴帽", "脱鞋",
        )
    )
)
_SPEECH_MORPHOLOGY_PATTERN = (
    r"(?:言|语|话|问|答|告|诉|喊|叫|呼|吼|嚷|咆|哮|斥|骂|驳|辩|"
    r"劝|求|叹|讽|嘲|谑|咐|嘱|令|诺|宣|述|声|歉|谢|贺|福|慰)"
)
_SPEECH_MORPHOLOGY_RE = re.compile(_SPEECH_MORPHOLOGY_PATTERN)
SOURCE_SPEECH_LEXICON_VERSION = "source-speech-predicate-v1"
SOURCE_ACTION_LEXICON_VERSION = "source-narrative-action-v1"
SOURCE_DIALOGUE_GRAMMAR_VERSION = "source-dialogue-closed-grammar-v6.3"


SOURCE_SPEECH_LEXICON_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "verbs": sorted(set(_SPEECH_VERBS)),
            "unsupported": sorted(set(_UNSUPPORTED_SPEECH_LEXEMES)),
            "morphology": _SPEECH_MORPHOLOGY_PATTERN,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
SOURCE_ACTION_LEXICON_SHA256 = hashlib.sha256(
    "\n".join(sorted(set(_NARRATIVE_ACTION_PREDICATES))).encode("utf-8")
).hexdigest()
_HTML_QUOTE_ENTITY_RE = re.compile(
    r"&(?:l|r)?(?:dquo|squo);|&quot;|&#(?:34|39|x22|x27);",
    re.I,
)
_ROLE_MARKER_RE = re.compile(
    rf"(?P<boundary>^|(?P<separator>[，,/／。！？!?；;{_QUOTE_CLOSE_CLASS}]|…+)\s*)"
    r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})\s*[：:]"
)
_GENERIC_LABEL_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})\s*[：:]"
)
_PAREN_ROLE_RE = re.compile(
    r"^[（(]\s*(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})\s*[）)]$"
)
_QUOTE_RE = re.compile(
    rf"(?P<open>[{_QUOTE_OPEN_CLASS}])(?P<text>[^\n]*?)(?P<close>[{_QUOTE_CLOSE_CLASS}])"
)
_QUOTE_LIKE_RE = re.compile(rf"[{_QUOTE_LIKE_CLASS}]")
_VISIBLE_TEXT_RE = re.compile(
    r"(?P<label>"
    r"信上|信中|信件内容|信件|书信|纸条|纸上|"
    r"手机屏幕上|手机屏幕|手机界面|手机短信|短信|"
    r"屏幕文字|屏幕|微信消息|微信内容|微信|聊天框"
    r")"
    r"(?:写着|写道|显示(?:着)?|内容(?:为|是)?|上写着)?\s*[：:]?\s*"
    rf"(?P<open>[{_QUOTE_OPEN_CLASS}])(?P<text>.*?)(?P<close>[{_QUOTE_CLOSE_CLASS}])",
    re.S,
)
_READ_AFTER_RE = re.compile(
    rf"(?P<boundary>^|[。！？!?；;\n{_QUOTE_CLOSE_CLASS}]\s*)"
    r"(?P<reader>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}?)"
    r"(?:随后|接着|又|当场|低声|缓缓|轻声|大声|当众|便|就)*"
    r"(?:(?:将|把)(?:这|该)?(?:封信|信件|书信|来信|信|张纸条|纸条|"
    r"手机屏幕(?:上)?(?:的字|的内容)?|屏幕文字|短信|微信(?:消息|内容)?|聊天框(?:内容)?|"
    r"内容|上面的字))?"
    r"(?P<read_verb>念了出来|读了出来|念出声|读出声|念道|读道|朗读出来)",
    re.M,
)
_READ_OBJECT_RE = re.compile(
    r"(?:信|信件|书信|来信|信中(?:的)?(?:内容|字|话)?|信上(?:的)?(?:内容|字|话)?|"
    r"纸条(?:上)?(?:的)?(?:内容|字|话)?|上面(?:的)?(?:内容|字|话)?|"
    r"(?:这|该)?(?:封信|张纸条)|手机屏幕(?:上)?(?:的)?(?:内容|字)?|"
    r"屏幕文字|短信|微信(?:消息|内容)?|聊天框(?:内容)?|内容)"
)
_READ_BINDING_BREAK_RE = re.compile(
    r"(?:^|\n)\s*(?:场景|镜头|转场|时间|地点)\s*[：:]|"
    r"(?:烧|焚|撕|丢|扔|毁|销毁|"
    r"收起|放下|合上)(?:了)?(?:信|信件|书信|来信|纸条)?|"
    r"拿起(?:了)?(?:诗册|书册|书|报纸|奏折|卷宗|公文)|"
    r"(?:息屏|锁屏|关闭手机|收起手机|删除(?:短信|消息)|"
    r"次日|翌日|三日后|数日后|多年后|换场)"
)
_READ_TAKE_RE = re.compile(
    rf"(?:^|[。！？!?；;\n{_QUOTE_CLOSE_CLASS}])\s*"
    r"(?P<holder>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}?)"
    r"(?:抢过|接过|拿走|取过|夺过)(?:了)?"
    r"(?P<object>(?:这|该)?(?:封信|信件|书信|来信|信|张纸条|纸条))"
)
_READ_GIVE_RE = re.compile(
    rf"(?:^|[。！？!?；;\n{_QUOTE_CLOSE_CLASS}])\s*"
    r"(?:(?P<giver>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}?)\s*)?"
    r"(?:把|将)(?P<object>(?:这|该)?(?:封信|信件|书信|来信|信|张纸条|纸条))"
    r"(?:递给|交给)(?:了)?"
    r"(?P<holder>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})"
)
_ANTECEDENT_RE = re.compile(
    r"(?P<speaker>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{2,12})"
    r"(?:拆开|接过|展开|拿起|捧起|看着|看完|打开)(?:了)?"
    r"(?P<object>信|信件|书信|来信|纸条)"
)
_VISUAL_LINE_RE = re.compile(
    rf"^\s*(?P<label>{_alternation(_NON_DIALOGUE_LABELS)})\s*[：:]"
)
_BRACKET_STAGE_RE = re.compile(r"^\s*(?:【[^】]+】|\[[^\]]+\])\s*$")
_SCRIPT_DELIVERY_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"【(?P<cjk_square>[^】]*)】|\[(?P<ascii_square>[^\]]*)\]|"
    r"（(?P<cjk_paren>[^）]*)）|\((?P<ascii_paren>[^)]*)\)"
    r")\s*"
)
_SCRIPT_ACTION_MARKER_RE = re.compile(r"^\s*[△▲]")
_LINE_END_SPEECH_CUE_RE = re.compile(
    rf"(?P<speaker>{_TOKEN_PATTERN})(?P<delivery>{_ACTION_PATTERN})"
    rf"(?P<verb>{_VERB_PATTERN})\s*$"
)
_WHITESPACE_SPEECH_CUE_RE = re.compile(
    rf"(?P<boundary>^|[。！？!?；;\n])\s*"
    rf"(?P<speaker>{_TOKEN_PATTERN})(?P<delivery>{_ACTION_PATTERN})"
    rf"(?P<verb>{_VERB_PATTERN})(?P<gap>[ \t]+)(?P<tail>\S)"
)
_STRONG_DELIMITERLESS_SPEECH_RE = re.compile(
    rf"(?P<boundary>^|[。！？!?；;，,\n])\s*"
    rf"(?P<speaker>{_TOKEN_PATTERN})"
    rf"(?P<predicate>{_alternation(_STRONG_DELIMITERLESS_SPEECH_LEXEMES)})"
    rf"(?P<gap>[ \t\u3000\u00a0\u2002]*)(?P<tail>[^\s：:，,。！？!?；;、—–―\-{_QUOTE_OPEN_CLASS}])"
)
_CLEAR_NARRATIVE_READ_TAIL_RE = re.compile(
    r"(?:完|着|了|起|起了|继续)?(?:一本|一封|一张|这本|那本|这封|那封|这张|那张)?"
    r"(?:书|书册|诗册|经书|信|信件|书信|来信|纸条|报纸|奏折|卷宗|公文)"
    r"(?:[。！？!?]|$)"
)


def _regex_contract(expression: re.Pattern[str]) -> dict[str, Any]:
    return {"pattern": expression.pattern, "flags": expression.flags}


def source_dialogue_grammar_manifest(
    *,
    speech_verbs: Iterable[str] | None = None,
    unsupported_speech_lexemes: Iterable[str] | None = None,
    action_predicates: Iterable[str] | None = None,
    speech_morphology_pattern: str | None = None,
    structural_grammar_version: str | None = None,
    component_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical, complete classification/splitting contract.

    Every data constant or compiled expression consulted by the authority
    parser is represented here.  Algorithmic control flow is bound by the
    structural grammar version; any such change must bump that version.  The
    optional arguments exist only for deterministic contract tests and tooling
    that proves a component mutation changes the fingerprint.
    """

    regex_contracts = {
        name: _regex_contract(expression)
        for name, expression in (
            ("token", _TOKEN_RE),
            ("visual_label_family", _VISUAL_LABEL_FAMILY_RE),
            ("cue", _CUE_RE),
            ("speech_like", _SPEECH_LIKE_RE),
            ("unsupported_speech_punctuation", _UNSUPPORTED_SPEECH_PUNCT_RE),
            ("role_residue_separator", _ROLE_RESIDUE_SEPARATOR_RE),
            ("speech_morphology", _SPEECH_MORPHOLOGY_RE),
            ("html_quote_entity", _HTML_QUOTE_ENTITY_RE),
            ("role_marker", _ROLE_MARKER_RE),
            ("generic_label", _GENERIC_LABEL_RE),
            ("parenthetical_role", _PAREN_ROLE_RE),
            ("quote", _QUOTE_RE),
            ("quote_like", _QUOTE_LIKE_RE),
            ("visible_text", _VISIBLE_TEXT_RE),
            ("read_after", _READ_AFTER_RE),
            ("read_object", _READ_OBJECT_RE),
            ("read_binding_break", _READ_BINDING_BREAK_RE),
            ("read_take", _READ_TAKE_RE),
            ("read_give", _READ_GIVE_RE),
            ("antecedent", _ANTECEDENT_RE),
            ("visual_line", _VISUAL_LINE_RE),
            ("bracket_stage", _BRACKET_STAGE_RE),
            ("script_action_marker", _SCRIPT_ACTION_MARKER_RE),
            ("script_delivery_prefix", _SCRIPT_DELIVERY_PREFIX_RE),
            ("line_end_speech_cue", _LINE_END_SPEECH_CUE_RE),
            ("whitespace_speech_cue", _WHITESPACE_SPEECH_CUE_RE),
            (
                "strong_delimiterless_speech",
                _STRONG_DELIMITERLESS_SPEECH_RE,
            ),
            ("clear_narrative_read_tail", _CLEAR_NARRATIVE_READ_TAIL_RE),
        )
    }
    manifest: dict[str, Any] = {
        "parser_version": SOURCE_DIALOGUE_PARSER_VERSION,
        "structural_grammar_version": (
            SOURCE_DIALOGUE_GRAMMAR_VERSION
            if structural_grammar_version is None
            else structural_grammar_version
        ),
        "speech_lexicon_version": SOURCE_SPEECH_LEXICON_VERSION,
        "action_lexicon_version": SOURCE_ACTION_LEXICON_VERSION,
        "pronoun_speakers": sorted(_PRONOUN_SPEAKERS),
        "non_dialogue_labels": sorted(_NON_DIALOGUE_LABELS),
        "visual_label_aliases": sorted(_VISUAL_LABEL_ALIASES),
        "scene_reset_labels": sorted(_SCENE_RESET_LABELS),
        "action_parts": list(_ACTION_PARTS),
        "outer_cue_prefixes": list(_OUTER_CUE_PREFIXES),
        "speech_verbs": list(
            _SPEECH_VERBS if speech_verbs is None else speech_verbs
        ),
        "speaker_forbidden_parts": list(_SPEAKER_FORBIDDEN_PARTS),
        "stage_direction_terms": sorted(_STAGE_DIRECTION_TERMS),
        "quote_pairs": {key: _QUOTE_PAIRS[key] for key in sorted(_QUOTE_PAIRS)},
        "role_suffixes": list(_ROLE_SUFFIXES),
        "known_source_speaker_atoms": sorted(_KNOWN_SOURCE_SPEAKER_ATOMS),
        "compound_surnames": sorted(_COMPOUND_SURNAMES),
        "common_surnames": sorted(_COMMON_SURNAMES),
        "plain_han_name_exclusions": sorted(_PLAIN_HAN_NAME_EXCLUSIONS),
        "plain_han_name_structural_terms": sorted(
            _PLAIN_HAN_NAME_STRUCTURAL_TERMS
        ),
        "plain_han_name_policy": {
            "single_surname_given_name_lengths": [1, 2],
            "compound_surname_given_name_lengths": [1, 2],
        },
        "unsupported_speech_lexemes": list(
            _UNSUPPORTED_SPEECH_LEXEMES
            if unsupported_speech_lexemes is None
            else unsupported_speech_lexemes
        ),
        "action_predicates": list(
            _NARRATIVE_ACTION_PREDICATES
            if action_predicates is None
            else action_predicates
        ),
        "strong_delimiterless_speech_lexemes": list(
            _STRONG_DELIMITERLESS_SPEECH_LEXEMES
        ),
        "speech_morphology_pattern": (
            _SPEECH_MORPHOLOGY_PATTERN
            if speech_morphology_pattern is None
            else speech_morphology_pattern
        ),
        "token_pattern": _TOKEN_PATTERN,
        "action_pattern": _ACTION_PATTERN,
        "verb_pattern": _VERB_PATTERN,
        "prefix_pattern": _PREFIX_PATTERN,
        "regex_contracts": regex_contracts,
        # Total implementation fallback: semantic subcomponents above make
        # the contract reviewable, while the module byte digest guarantees
        # that a new hard-coded branch/control-flow change cannot preserve the
        # same authority fingerprint merely because a maintainer forgot to
        # promote it into the manifest or bump the structural version.
        "implementation_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
    }
    for key, value in (component_overrides or {}).items():
        if key not in manifest:
            raise ValueError(f"unknown source dialogue grammar component: {key}")
        manifest[key] = value
    return manifest


def source_dialogue_grammar_fingerprint(
    *,
    speech_verbs: Iterable[str] | None = None,
    unsupported_speech_lexemes: Iterable[str] | None = None,
    action_predicates: Iterable[str] | None = None,
    speech_morphology_pattern: str | None = None,
    structural_grammar_version: str | None = None,
    component_overrides: dict[str, Any] | None = None,
) -> str:
    """Hash the complete canonical grammar manifest."""

    canonical = json.dumps(
        source_dialogue_grammar_manifest(
            speech_verbs=speech_verbs,
            unsupported_speech_lexemes=unsupported_speech_lexemes,
            action_predicates=action_predicates,
            speech_morphology_pattern=speech_morphology_pattern,
            structural_grammar_version=structural_grammar_version,
            component_overrides=component_overrides,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


SOURCE_DIALOGUE_GRAMMAR_SHA256 = source_dialogue_grammar_fingerprint()


def _script_declared_speakers(source_text: str) -> set[str]:
    """Collect only source-explicit, standalone script role declarations."""

    speakers: set[str] = set()
    for raw_line in source_text.splitlines():
        stripped = raw_line.strip()
        label: str | None = None
        colon = re.fullmatch(
            r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})\s*[：:]",
            stripped,
        )
        parenthetical = _PAREN_ROLE_RE.fullmatch(stripped)
        if colon:
            label = colon.group("label").strip()
        elif parenthetical:
            label = parenthetical.group("label").strip()
        if not label or _looks_visual_label(label):
            continue
        if label in _STAGE_DIRECTION_TERMS or any(
            part in label for part in _SPEAKER_FORBIDDEN_PARTS
        ):
            continue
        atom, residue = _source_speaker_atom(label, known_speakers=set())
        # A short Han label whose first character happens to be a surname is
        # not source proof of a role: 全景、白场、黄昏、计划 and many other
        # stage/structure labels satisfy that heuristic.  Standalone script
        # declarations therefore enter the authority set only when the token
        # itself has a closed role shape (or an explicit supported name form).
        explicit_name = bool(
            re.fullmatch(r"阿[\u3400-\u4dbf\u4e00-\u9fff]{1,2}", label)
        )
        latin_name = bool(re.fullmatch(r"[A-Za-z][A-Za-z·]{1,15}", label))
        if (
            (atom == label and not residue)
            or explicit_name
            or latin_name
            or _is_plain_han_name(label)
        ):
            speakers.add(label)
    return speakers


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _is_inside(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _clean_dialogue_text(value: str) -> str:
    text = value.strip()
    if (
        len(text) >= 2
        and text[0] in _QUOTE_PAIRS
        and _QUOTE_PAIRS[text[0]] == text[-1]
    ):
        text = text[1:-1].strip()
    return text


def _clean_dialogue_text_with_offset(value: str, offset: int) -> tuple[str, int]:
    """Clean a role segment while keeping its source position exact."""

    leading = len(value) - len(value.lstrip())
    text = value.strip()
    clean_offset = offset + leading
    if (
        len(text) >= 2
        and text[0] in _QUOTE_PAIRS
        and _QUOTE_PAIRS[text[0]] == text[-1]
    ):
        inner = text[1:-1]
        inner_leading = len(inner) - len(inner.lstrip())
        clean_offset += 1 + inner_leading
        text = inner.strip()
    return text, clean_offset


def _validate_quote_integrity(
    value: str,
    *,
    source_text: str,
    offset: int,
    context: str,
) -> None:
    """Require every supported quote-like delimiter to be exactly balanced."""

    stack: list[tuple[str, int]] = []
    symmetric = {opening for opening, closing in _QUOTE_PAIRS.items() if opening == closing}
    closing_delimiters = set(_QUOTE_PAIRS.values())
    for index, char in enumerate(value):
        if char in symmetric:
            if stack and stack[-1][0] == char:
                stack.pop()
            else:
                stack.append((char, index))
            continue
        if char in _QUOTE_PAIRS:
            stack.append((_QUOTE_PAIRS[char], index))
            continue
        if char not in closing_delimiters:
            continue
        if not stack or stack[-1][0] != char:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line "
                f"{_line_number(source_text, offset + index)}: "
                f"mismatched quote delimiter in {context}"
            )
        stack.pop()
    if stack:
        _expected, opening_index = stack[-1]
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line "
            f"{_line_number(source_text, offset + opening_index)}: "
            f"unclosed quote delimiter in {context}"
        )


def _visible_dialogue(match: re.Match[str], *, source_text: str) -> str:
    if _QUOTE_PAIRS.get(match.group("open")) != match.group("close"):
        raise SourceDialogueAuthorityError(
            "source_dialogue_authority_ambiguous: written-text quote pair is malformed"
        )
    _validate_quote_integrity(
        match.group(0),
        source_text=source_text,
        offset=match.start(),
        context="written text",
    )
    visible_text = match.group("text")
    if "\n" in visible_text and (
        _SPEECH_LIKE_RE.search(visible_text) or _CUE_RE.search(visible_text)
    ):
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line "
            f"{_line_number(source_text, match.start('text'))}: "
            "written-text quote crosses a source speech candidate"
        )
    return visible_text


def _visible_dialogue_span(
    match: re.Match[str], *, source_text: str
) -> tuple[str, int, int]:
    return (
        _visible_dialogue(match, source_text=source_text),
        match.start("text"),
        match.end("text"),
    )


def _validate_quote_match(
    match: re.Match[str],
    *,
    source_text: str,
    offset: int,
) -> None:
    _validate_quote_integrity(
        match.group(0),
        source_text=source_text,
        offset=offset + match.start(),
        context="quotation",
    )


def _strong_inline_role(label: str) -> bool:
    # A shape such as “three Han characters beginning with a surname” is not
    # role proof: 蒙太奇/全景/计划 and many stage labels collide with it.  Only
    # closed role suffixes qualify here; other names require an explicit
    # source-supported declaration form.
    atom, residue = _source_speaker_atom(label, known_speakers=set())
    return atom == label and not residue


def _looks_visual_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", str(label or ""))
    folded = compact.casefold()
    return (
        compact in _NON_DIALOGUE_LABELS
        or folded in _VISUAL_LABEL_ALIASES
        or _VISUAL_LABEL_FAMILY_RE.fullmatch(compact) is not None
    )


def _is_plain_han_name(value: str) -> bool:
    """Recognize a source-explicit ordinary Han personal name, not a label."""

    token = str(value or "").strip()
    if (
        token in _PLAIN_HAN_NAME_EXCLUSIONS
        or _looks_visual_label(token)
        or any(term in token for term in _PLAIN_HAN_NAME_STRUCTURAL_TERMS)
        or _VISUAL_LABEL_FAMILY_RE.search(token) is not None
        or token.endswith("先生")
        or re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,4}", token) is None
    ):
        return False
    for surname in sorted(_COMPOUND_SURNAMES, key=len, reverse=True):
        if token.startswith(surname):
            given = token[len(surname) :]
            return 1 <= len(given) <= 2
    return token[0] in _COMMON_SURNAMES and 1 <= len(token[1:]) <= 2


def _predicate_class(residue: str) -> str:
    """Classify an unconsumed role predicate under a versioned closed set."""

    compact = re.sub(r"\s+", "", str(residue or ""))
    if any(value in compact for value in _UNSUPPORTED_SPEECH_LEXEMES) or (
        _SPEECH_MORPHOLOGY_RE.search(compact) is not None
    ):
        return "speech"
    if compact in _NARRATIVE_ACTION_PREDICATES:
        return "action"
    return "ambiguous"


def _source_speaker_atom(
    value: str,
    *,
    known_speakers: set[str],
) -> tuple[str | None, str]:
    token = value.strip()
    for known in sorted(known_speakers, key=lambda item: (-len(item), item)):
        if token.startswith(known):
            return known, token[len(known) :]
    if token in _KNOWN_SOURCE_SPEAKER_ATOMS:
        return token, ""
    if re.fullmatch(r"阿[\u3400-\u4dbf\u4e00-\u9fff]{1,2}", token):
        return token, ""
    numbered_role = re.fullmatch(
        r"(?:侍卫|丫鬟|小厮|宫女|太监|路人|百姓)[甲乙丙丁一二三四五六七八九十]",
        token,
    )
    if numbered_role:
        return token, ""
    for end in range(1, min(len(token), 12) + 1):
        prefix = token[:end]
        for suffix in _ROLE_SUFFIXES:
            if not prefix.endswith(suffix):
                continue
            stem = prefix[: -len(suffix)] if len(prefix) > len(suffix) else ""
            if not stem:
                return prefix, token[end:]
            # A title/surname role may be prefixed only by a short, plausible
            # source name.  Arbitrary narrative prefixes such as 听/让/请/
            # 轮到/原来/了 must never become part of a signed speaker atom.
            if (
                re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]{1,2}", stem)
                and (
                    (len(stem) == 1 and stem in _COMMON_SURNAMES)
                    or (len(stem) == 2 and stem in _COMPOUND_SURNAMES)
                )
            ):
                return prefix, token[end:]
    for end in range(min(len(token), 4), 1, -1):
        prefix = token[:end]
        if _is_plain_han_name(prefix):
            return prefix, token[end:]
    return None, token


def _speaker(
    value: str,
    *,
    line_number: int,
    context: str,
    known_speakers: set[str] | None = None,
) -> str:
    speaker = value.strip()
    if _TOKEN_RE.fullmatch(speaker) is None:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {line_number}: "
            f"invalid speaker token {speaker!r} in {context}"
        )
    if speaker in _PRONOUN_SPEAKERS:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {line_number}: "
            f"pronoun speaker {speaker!r} is not uniquely attributable from source"
        )
    if speaker in _NON_DIALOGUE_LABELS:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_non_dialogue_label: line {line_number}: {speaker!r}"
        )
    if _looks_visual_label(speaker):
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_non_dialogue_label: line {line_number}: {speaker!r}"
        )
    if any(part in speaker for part in _SPEAKER_FORBIDDEN_PARTS):
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {line_number}: "
            f"speaker token contains action/cue residue {speaker!r}"
        )
    atom, residue = _source_speaker_atom(
        speaker, known_speakers=known_speakers or set()
    )
    if atom is None or residue:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {line_number}: "
            f"speaker token is not one source-proved role atom: raw={speaker!r}, "
            f"atom={atom!r}, residue={residue!r}"
        )
    return atom


def extracted_source_text(extracted: dict[str, Any]) -> str:
    """Return exact extracted text or fail closed for unsupported structures."""

    kind = str(extracted.get("kind") or "")
    if kind == "text":
        value = extracted.get("text")
        if isinstance(value, str):
            return value
    if kind == "json":
        value = extracted.get("value")
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            candidates = [
                value.get(key)
                for key in ("text", "source_text", "script", "content")
                if isinstance(value.get(key), str)
            ]
            if len(candidates) == 1:
                return str(candidates[0])
    raise SourceDialogueAuthorityError(
        "source_dialogue_authority_unsupported_source: authoritative novel/script "
        "must extract to one deterministic text payload"
    )


def _append_occurrence(
    rows: list[DialogueOccurrence],
    *,
    speaker: str,
    dialogue: str,
    source_text: str,
    offset: int,
    kind: str,
) -> None:
    text = _clean_dialogue_text(dialogue)
    if not text:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, offset)}: "
            "empty dialogue after a speech/role cue"
        )
    rows.append(
        DialogueOccurrence(
            speaker=speaker,
            text=text,
            line_number=_line_number(source_text, offset),
            char_offset=offset,
            kind=kind,
        )
    )


def _resolve_reader(
    source_text: str,
    *,
    quote_start: int,
    raw_reader: str,
    known_speakers: set[str],
    binding_gap: str,
    written_family: str,
) -> str:
    line = _line_number(source_text, quote_start)
    if raw_reader not in _PRONOUN_SPEAKERS:
        return _speaker(
            raw_reader,
            line_number=line,
            context="read-aloud cue",
            known_speakers=known_speakers,
        )
    # Resolve the pronoun through the source object's complete holder chain,
    # not a fixed pre-quote window.  A transfer after the written text takes
    # precedence over whoever opened it before the quote.
    candidates: list[str] = []
    for transfer_re in (_READ_TAKE_RE, _READ_GIVE_RE):
        for match in transfer_re.finditer(binding_gap):
            transfer_family = _written_object_family(match.group("object"))
            if transfer_family != written_family:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {line}: "
                    "pronoun reader follows a transfer of a different written object"
                )
            candidates.append(
                _speaker(
                    match.group("holder"),
                    line_number=line,
                    context="source object transfer",
                    known_speakers=known_speakers,
                )
            )
            giver = match.groupdict().get("giver")
            if giver:
                _speaker(
                    giver,
                    line_number=line,
                    context="source object transfer giver",
                    known_speakers=known_speakers,
                )
    if candidates:
        unique_holders = list(dict.fromkeys(candidates))
        if len(unique_holders) != 1:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {line}: "
                "written object has multiple source-proved holders before the read cue"
            )
        return unique_holders[0]

    before_quote = source_text[:quote_start]
    prior_boundaries = [
        match.end() for match in _READ_BINDING_BREAK_RE.finditer(before_quote)
    ]
    prior_visible = [
        match.end() for match in _VISIBLE_TEXT_RE.finditer(before_quote)
    ]
    continuity_start = max([0, *prior_boundaries, *prior_visible])
    continuity_prefix = before_quote[continuity_start:]
    for match in _ANTECEDENT_RE.finditer(continuity_prefix):
        if _written_object_family(match.group("object")) != written_family:
            continue
        try:
            candidates.append(
                _speaker(
                    match.group("speaker"),
                    line_number=line,
                    context="source antecedent",
                    known_speakers=known_speakers,
                )
            )
        except SourceDialogueAuthorityError:
            continue
    if candidates:
        unique_holders = list(dict.fromkeys(candidates))
        if len(unique_holders) != 1:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {line}: "
                "written object has multiple source-proved holders in one continuity segment"
            )
        return unique_holders[0]
    raise SourceDialogueAuthorityError(
        f"source_dialogue_authority_ambiguous: line {line}: read-aloud pronoun "
        f"{raw_reader!r} has no source-only object holder"
    )


def _written_object_family(label: str) -> str:
    compact = str(label or "")
    if re.search(r"手机|屏幕|短信|微信|聊天框", compact):
        return "screen"
    return "paper" if "纸" in compact else "letter"


def _cue_object_family(cue_tail: str) -> str | None:
    has_paper = re.search(r"纸条|张纸", cue_tail) is not None
    has_screen = re.search(
        r"手机|屏幕|短信|微信|聊天框", cue_tail
    ) is not None
    has_letter = re.search(
        r"书信|来信|封信|信件|(?<![短微])信", cue_tail
    ) is not None
    families = sum((has_paper, has_screen, has_letter))
    if families > 1:
        raise SourceDialogueAuthorityError(
            "source_dialogue_authority_ambiguous: read cue names conflicting written objects"
        )
    if has_paper:
        return "paper"
    if has_screen:
        return "screen"
    if has_letter:
        return "letter"
    return None


def _is_explicit_role_label(label: str, *, known_speakers: set[str]) -> bool:
    token = str(label or "").strip()
    if (
        not token
        or _looks_visual_label(token)
        or token in _STAGE_DIRECTION_TERMS
        or any(part in token for part in _SPEAKER_FORBIDDEN_PARTS)
    ):
        return False
    atom, residue = _source_speaker_atom(token, known_speakers=known_speakers)
    return bool(
        (atom == token and not residue)
        or re.fullmatch(r"[A-Za-z][A-Za-z·]{1,15}", token)
    )


def _is_explicit_cue_label(label: str, *, known_speakers: set[str]) -> bool:
    probe = _CUE_RE.fullmatch(f"{str(label or '').strip()}：")
    if probe is None:
        return False
    raw_speaker = probe.group("speaker").strip()
    atom, residue = _source_speaker_atom(
        raw_speaker, known_speakers=known_speakers
    )
    return atom == raw_speaker and not residue


def _raise_on_missing_marker_boundary(
    line_text: str,
    *,
    source_text: str,
    line_offset: int,
    known_speakers: set[str],
) -> None:
    """Reject a role/visual/cue label fused to preceding same-line text."""

    marker_token_re = re.compile(
        r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16})[：:]"
    )

    def supported_shape(label: str) -> bool:
        return bool(
            _looks_visual_label(label)
            or _is_explicit_role_label(label, known_speakers=known_speakers)
            or _is_explicit_cue_label(label, known_speakers=known_speakers)
        )

    for marker in marker_token_re.finditer(line_text):
        raw_label = marker.group("label").strip()
        if supported_shape(raw_label):
            continue
        for split in range(1, len(raw_label)):
            suffix = raw_label[split:]
            if not supported_shape(suffix):
                continue
            suffix_offset = line_offset + marker.start("label") + split
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line "
                f"{_line_number(source_text, suffix_offset)}: "
                f"same-line marker {suffix!r} has no supported boundary"
            )


def _is_delivery_stage_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", str(label or ""))
    return bool(
        compact in _STAGE_DIRECTION_TERMS
        or compact in _ACTION_PARTS
        or any(compact == action for action in _ACTION_PARTS)
    )


def _normalize_script_dialogue_segment(
    value: str,
    *,
    source_text: str,
    offset: int,
) -> tuple[str, int]:
    """Strip only source-explicit delivery tags and validate exact dialogue."""

    text = value
    current_offset = offset
    while True:
        prefix = _SCRIPT_DELIVERY_PREFIX_RE.match(text)
        if prefix is None:
            break
        label = next(
            str(group or "").strip()
            for group in prefix.groups()
            if group is not None
        )
        if not _is_delivery_stage_label(label):
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line "
                f"{_line_number(source_text, current_offset)}: "
                f"unsupported script delivery/stage label {label!r}"
            )
        current_offset += prefix.end()
        text = text[prefix.end() :]
    _validate_quote_integrity(
        text,
        source_text=source_text,
        offset=current_offset,
        context="script role dialogue",
    )
    return _clean_dialogue_text_with_offset(text, current_offset)


def _parse_role_line(
    line_text: str,
    *,
    source_text: str,
    line_offset: int,
    rows: list[DialogueOccurrence],
    consumed: list[tuple[int, int]],
    allow_role_labels: bool,
    known_speakers: set[str],
) -> tuple[bool, str | None]:
    if not allow_role_labels:
        return False, None
    raw_markers = list(_ROLE_MARKER_RE.finditer(line_text))
    if not raw_markers or raw_markers[0].start() != 0:
        return False, None
    first_label = raw_markers[0].group("label").strip()
    # ``江氏说：“...”`` is a prose speech cue, not a role label named 江氏说.
    if not _looks_visual_label(first_label) and any(
        first_label.endswith(verb) for verb in _SPEECH_VERBS
    ):
        return False, None
    cue_label_starts = {
        cue.start("speaker")
        for cue in _CUE_RE.finditer(line_text)
        if cue.group("separator") or cue.group("open")
    }
    marker_kinds: list[tuple[re.Match[str], str]] = []
    for marker in raw_markers:
        label = marker.group("label").strip()
        if marker.start("label") in cue_label_starts:
            marker_kinds.append((marker, "cue"))
        elif _looks_visual_label(label):
            marker_kinds.append((marker, "visual"))
        elif _is_explicit_role_label(label, known_speakers=known_speakers):
            marker_kinds.append((marker, "role"))
        else:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line "
                f"{_line_number(source_text, line_offset + marker.start('label'))}: "
                f"same-line marker {label!r} is neither a source role nor a visual label"
            )
    unsupported_separator = re.search(
        r"[；;、]\s*[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}\s*[：:]",
        line_text,
    )
    if unsupported_separator:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + unsupported_separator.start())}: "
            "same-line role uses an unsupported/ambiguous separator"
        )

    active_speaker: str | None = None
    for index, (marker, marker_kind) in enumerate(marker_kinds):
        label = marker.group("label").strip()
        if marker_kind == "cue":
            continue
        start = marker.end()
        if index + 1 < len(marker_kinds):
            next_marker = marker_kinds[index + 1][0]
            separator = next_marker.group("separator")
            if separator:
                end = next_marker.start("separator") + (
                    len(separator)
                    if any(
                        char
                        in f"。！？!?；;…{''.join(_QUOTE_PAIRS.values())}"
                        for char in separator
                    )
                    else 0
                )
            else:
                end = next_marker.start()
        else:
            end = len(line_text)
        segment = line_text[start:end]
        segment_left_trim = len(segment) - len(segment.lstrip())
        segment_offset = line_offset + start + segment_left_trim
        segment = segment.strip()
        _validate_quote_integrity(
            segment,
            source_text=source_text,
            offset=segment_offset,
            context=(
                "inline visual text" if marker_kind == "visual" else "script role dialogue"
            ),
        )
        consumed.append((line_offset + marker.start(), line_offset + end))
        if marker_kind == "visual":
            continue
        known_speakers.add(label)
        speaker = _speaker(
            label,
            line_number=_line_number(source_text, line_offset + marker.start("label")),
            context="explicit role label",
            known_speakers=known_speakers,
        )
        active_speaker = speaker
        dialogue, dialogue_offset = _normalize_script_dialogue_segment(
            segment,
            source_text=source_text,
            offset=segment_offset,
        )
        if dialogue:
            _append_occurrence(
                rows,
                speaker=speaker,
                dialogue=dialogue,
                source_text=source_text,
                offset=dialogue_offset,
                kind="role_label",
            )
    return True, active_speaker


def _parse_unconsumed_inline_role_runs(
    line_text: str,
    *,
    source_text: str,
    line_offset: int,
    rows: list[DialogueOccurrence],
    consumed: list[tuple[int, int]],
    known_speakers: set[str],
) -> None:
    """Parse script role/visual runs that follow a same-line prose cue."""

    cue_starts = sorted(
        cue.start("speaker")
        for cue in _CUE_RE.finditer(line_text)
        if cue.group("separator") or cue.group("open")
    )
    while True:
        pending = [
            marker
            for marker in _ROLE_MARKER_RE.finditer(line_text)
            if not _is_inside(
                line_offset + marker.start("label"), consumed
            )
        ]
        if not pending:
            return
        first = pending[0]
        run_start = first.start("label")
        run_end = next(
            (cue_start for cue_start in cue_starts if cue_start > run_start),
            len(line_text),
        )
        before_ranges = len(consumed)
        parsed, _speaker_value = _parse_role_line(
            line_text[run_start:run_end],
            source_text=source_text,
            line_offset=line_offset + run_start,
            rows=rows,
            consumed=consumed,
            allow_role_labels=True,
            known_speakers=known_speakers,
        )
        if not parsed or len(consumed) == before_ranges:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line "
                f"{_line_number(source_text, line_offset + run_start)}: "
                "same-line marker after a speech cue is not uniquely parseable"
            )


def _parse_speech_cues(
    line_text: str,
    *,
    source_text: str,
    line_offset: int,
    rows: list[DialogueOccurrence],
    consumed: list[tuple[int, int]],
    known_speakers: set[str],
) -> bool:
    matches = list(_CUE_RE.finditer(line_text))
    parsed = False
    for index, match in enumerate(matches):
        cue_offset = line_offset + match.start("speaker")
        if _is_inside(cue_offset, consumed):
            continue
        open_quote = match.group("open")
        if not open_quote and not match.group("separator"):
            # A verb-like substring without a delimiter is not yet a cue.
            # Validate line-end and other ambiguous forms in the residue pass
            # below, after ordinary narrative has had a chance to remain
            # silent.
            continue
        speaker = _speaker(
            match.group("speaker"),
            line_number=_line_number(source_text, cue_offset),
            context="speech cue",
            known_speakers=known_speakers,
        )
        content_start = match.end()
        verb = match.group("verb")
        if verb in {"道", "答"}:
            if not open_quote:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, cue_offset)}: "
                    f"bare verb {verb!r} without a quoted utterance is not unique"
                )
            if len(speaker) > 3 and not _strong_inline_role(speaker):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, cue_offset)}: "
                    f"bare verb {verb!r} has a non-atomic speaker token {speaker!r}"
                )
        if open_quote:
            close_quote = _QUOTE_PAIRS[open_quote]
            close = line_text.find(close_quote, content_start)
            if close < 0:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, cue_offset)}: "
                    "speech quote is not closed"
                )
            dialogue = line_text[content_start:close]
            content_end = close + 1
            _validate_quote_integrity(
                line_text[match.start("open") : content_end],
                source_text=source_text,
                offset=line_offset + match.start("open"),
                context="speech cue quotation",
            )
        else:
            if not match.group("separator"):
                continue
            next_start = (
                matches[index + 1].start("speaker")
                if index + 1 < len(matches)
                else len(line_text)
            )
            tail = line_text[content_start:next_start]
            terminal = re.search(r"[。！？!?]|…+", tail)
            if terminal:
                tail = tail[: terminal.end()]
            elif index + 1 < len(matches):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, cue_offset)}: "
                    "adjacent unquoted speech cues have no terminal boundary"
                )
            dialogue = tail
            content_end = content_start + len(tail)
        _append_occurrence(
            rows,
            speaker=speaker,
            dialogue=dialogue,
            source_text=source_text,
            offset=line_offset + content_start,
            kind="speech_cue",
        )
        consumed.append((line_offset + match.start(), line_offset + content_end))
        parsed = True
    return parsed


def _is_clear_narrative_read_signal(
    signal: re.Match[str], line_text: str
) -> bool:
    if signal.groupdict().get("verb") not in {"读", "念"}:
        return False
    tail = line_text[signal.end() :].lstrip()
    if signal.groupdict().get("tail"):
        tail = signal.group("tail") + line_text[signal.end() :]
    return _CLEAR_NARRATIVE_READ_TAIL_RE.fullmatch(tail) is not None


def _raise_on_unconsumed_delimiterless_cues(
    line_text: str,
    *,
    source_text: str,
    line_offset: int,
    consumed: list[tuple[int, int]],
    known_speakers: set[str],
) -> None:
    """Block source-proved roles followed by speech without a delimiter.

    Whitespace and a missing separator are not evidence that the utterance is
    narrative.  Conversely, a verb-like substring after an unproved token
    (for example ``继续读书`` after a comma) is not enough to invent a
    speaker.  This residue check therefore fires only when the prefix is one
    complete role atom established by the source itself.
    """

    for signal in _WHITESPACE_SPEECH_CUE_RE.finditer(line_text):
        signal_offset = line_offset + signal.start("speaker")
        if _is_inside(signal_offset, consumed):
            continue
        if _is_clear_narrative_read_signal(signal, line_text):
            continue
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, signal_offset)}: "
            "speech cue uses whitespace without a supported utterance delimiter"
        )

    for signal in _CUE_RE.finditer(line_text):
        if signal.group("separator") or signal.group("open"):
            continue
        signal_offset = line_offset + signal.start("speaker")
        if _is_inside(signal_offset, consumed):
            continue
        # A cue ending the physical line is diagnosed by the dedicated
        # line-break check.  Here we close the distinct ``江氏说甲。`` path.
        if signal.end() >= len(line_text):
            continue
        tail = line_text[signal.end() :]
        if not tail or tail[0].isspace():
            continue
        if _is_clear_narrative_read_signal(signal, line_text):
            continue
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, signal_offset)}: "
            "speech cue has non-empty text without a supported utterance delimiter"
        )

    for signal in _STRONG_DELIMITERLESS_SPEECH_RE.finditer(line_text):
        signal_offset = line_offset + signal.start("speaker")
        if _is_inside(signal_offset, consumed):
            continue
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, signal_offset)}: "
            f"unsupported delimiterless speech predicate {signal.group('predicate')!r}"
        )


def _contains_dialogue_candidate(
    value: str,
    *,
    known_speakers: set[str],
) -> bool:
    if (
        _QUOTE_LIKE_RE.search(value)
        or _SPEECH_LIKE_RE.search(value)
        or _STRONG_DELIMITERLESS_SPEECH_RE.search(value)
    ):
        return True
    for cue in _CUE_RE.finditer(value):
        if (
            cue.group("separator")
            or cue.group("open")
            or not _is_clear_narrative_read_signal(cue, value)
        ):
            return True
    for marker in _ROLE_MARKER_RE.finditer(value):
        raw_label = marker.group("label").strip()
        atom, residue = _source_speaker_atom(
            raw_label, known_speakers=known_speakers
        )
        if (
            _is_explicit_role_label(raw_label, known_speakers=known_speakers)
            or (atom and residue and _predicate_class(residue) != "action")
            or raw_label.endswith(_UNSUPPORTED_SPEECH_LEXEMES)
        ):
            return True
    return False


def _visual_content_suffix_start(line_text: str, content_start: int) -> int:
    """Return the first position after one closed visual-label content unit."""

    cursor = content_start
    while cursor < len(line_text) and line_text[cursor].isspace():
        cursor += 1
    if cursor < len(line_text) and line_text[cursor] in _QUOTE_PAIRS:
        opening = line_text[cursor]
        closing = _QUOTE_PAIRS[opening]
        close = line_text.find(closing, cursor + 1)
        if close < 0:
            return len(line_text)
        cursor = close + 1
        while cursor < len(line_text) and line_text[cursor] in "。！？!?；;，, ":
            cursor += 1
        return cursor
    terminal = re.search(r"[。！？!?；;]", line_text[cursor:])
    return len(line_text) if terminal is None else cursor + terminal.end()


def parse_source_dialogue_authority(
    text: str,
    *,
    source_role: str,
) -> list[DialogueOccurrence]:
    """Parse audible dialogue from source text without candidate-supplied hints.

    The accepted grammar is deliberately closed.  When a speech-like cue or
    unattributed quotation cannot be resolved uniquely from the source, the
    function raises instead of returning a deceptively empty inventory.
    """

    if not isinstance(text, str):
        raise SourceDialogueAuthorityError(
            "source_dialogue_authority_unsupported_source: source text must be a string"
        )
    normalized_role = str(source_role or "").removeprefix("source:").casefold()
    if normalized_role not in {"novel", "script"}:
        raise SourceDialogueAuthorityError(
            f"source_dialogue_authority_unsupported_role: {source_role!r}"
        )
    source_text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if _HTML_QUOTE_ENTITY_RE.search(source_text):
        raise SourceDialogueAuthorityError(
            "source_dialogue_authority_ambiguous: HTML quote entities must be "
            "decoded by the authoritative extraction layer before parsing"
        )
    rows: list[DialogueOccurrence] = []
    consumed: list[tuple[int, int]] = []
    known_speakers = (
        _script_declared_speakers(source_text)
        if normalized_role == "script"
        else set()
    )

    # Written text is silent by default.  A read cue binds only the nearest
    # preceding, not-yet-bound written occurrence.  There is no fixed-length
    # lookahead that could silently drop a long cross-sentence read, and one
    # cue can never promote two earlier letters/screens to dialogue.
    visible_matches = list(_VISIBLE_TEXT_RE.finditer(source_text))
    visible_spans: list[tuple[re.Match[str], str, int, int, str]] = []
    for visible in visible_matches:
        dialogue, text_start, text_end = _visible_dialogue_span(
            visible, source_text=source_text
        )
        visible_spans.append(
            (
                visible,
                dialogue,
                text_start,
                text_end,
                _written_object_family(visible.group("label")),
            )
        )
        consumed.append((visible.start(), visible.end()))
    assigned_visible: set[int] = set()
    direct_read_cue_spans: list[tuple[int, int]] = []
    for reader_match in _READ_AFTER_RE.finditer(source_text):
        suffix = source_text[reader_match.end() : reader_match.end() + 8].lstrip()
        if (
            reader_match.group("read_verb") in {"念道", "读道"}
            and re.match(rf"[：:]?\s*[{_QUOTE_OPEN_CLASS}]", suffix)
        ):
            # This cue introduces its own quoted speech; it does not make the
            # preceding letter audible.
            direct_read_cue_spans.append(
                (reader_match.start(), reader_match.end() + len(suffix))
            )
            continue
        eligible = [
            index
            for index, (visible, _dialogue, _start, _end, _family) in enumerate(visible_spans)
            if index not in assigned_visible and visible.end() <= reader_match.start("reader")
        ]
        if not eligible:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                "read-aloud cue has no preceding unconsumed written-text occurrence"
            )
        cue_tail = source_text[
            reader_match.end("reader") : reader_match.start("read_verb")
        ]
        cue_family = _cue_object_family(cue_tail)
        if cue_family is not None:
            compatible = [
                index
                for index in eligible
                if visible_spans[index][4] == cue_family
            ]
            if not compatible:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                    f"read cue names {cue_family!r} but no compatible unconsumed written object exists"
                )
            if len(compatible) > 1:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                    "read cue does not uniquely select among same-kind written objects"
                )
            visible_index = compatible[-1]
        else:
            if len(eligible) > 1:
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                    "objectless read cue has multiple preceding written candidates"
                )
            visible_index = eligible[-1]
        visible, dialogue, text_start, _text_end, _family = visible_spans[
            visible_index
        ]
        binding_gap = source_text[visible.end() : reader_match.start("reader")]
        if _READ_BINDING_BREAK_RE.search(binding_gap):
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                "read-aloud binding crosses a scene/time/object-lifecycle boundary"
            )

        # Closed continuity rule: between the selected written occurrence and
        # its read cue, permit only punctuation/whitespace, other recognized
        # silent written occurrences, or an exactly parsed holder transfer.
        # Any remaining prose/action is ambiguous and blocks; an explicit
        # object word in the cue never licenses skipping unknown intervening
        # semantics.
        allowed_gap_ranges: list[tuple[int, int]] = []
        for transfer_re in (_READ_TAKE_RE, _READ_GIVE_RE):
            allowed_gap_ranges.extend(
                match.span() for match in transfer_re.finditer(binding_gap)
            )
        for other_visible, _dialogue, _start, _end, _other_family in visible_spans:
            if visible.end() <= other_visible.start() and other_visible.end() <= reader_match.start("reader"):
                allowed_gap_ranges.append(
                    (
                        other_visible.start() - visible.end(),
                        other_visible.end() - visible.end(),
                    )
                )
        residual_gap = list(binding_gap)
        for start, end in allowed_gap_ranges:
            residual_gap[start:end] = " " * max(0, end - start)
        residual_text = re.sub(
            r"[\s。！？!?；;，,、—–―\-…‥]+", "", "".join(residual_gap)
        )
        if residual_text:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, reader_match.start())}: "
                "read-aloud binding contains unconsumed prose/action between written text and cue"
            )
        reader = _resolve_reader(
            source_text,
            quote_start=visible.start(),
            raw_reader=reader_match.group("reader"),
            known_speakers=known_speakers,
            binding_gap=binding_gap,
            written_family=_family,
        )
        _append_occurrence(
            rows,
            speaker=reader,
            dialogue=dialogue,
            source_text=source_text,
            offset=text_start,
            kind="read_aloud_visible_text",
        )
        assigned_visible.add(visible_index)
        # The read cue itself is evidence for the audible occurrence already
        # emitted above.  Mark it consumed so the line parser cannot interpret
        # the same ``她随后念了出来`` a second time as a fresh direct-speech
        # cue.  ``念道/读道：“新文本”`` took the direct branch above and is
        # deliberately not consumed here.
        consumed.append((reader_match.start(), reader_match.end()))
    for index, (visible, _dialogue, _start, _end, _family) in enumerate(visible_spans):
        if index in assigned_visible:
            continue
        boundary = (
            visible_spans[index + 1][0].start()
            if index + 1 < len(visible_spans)
            else len(source_text)
        )
        segment_start = visible.end()
        segment = list(source_text[segment_start:boundary])
        for start, end in [*direct_read_cue_spans, *consumed]:
            left = max(start, segment_start) - segment_start
            right = min(end, boundary) - segment_start
            if left < right:
                segment[left:right] = " " * (right - left)
        remaining = "".join(segment)
        if re.search(r"读|念|诵", remaining):
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, visible.start())}: "
                "written text is near an unsupported read-aloud structure"
            )

    line_offset = 0
    active_script_speaker: str | None = None
    for raw_line in source_text.splitlines(keepends=True):
        line_text = raw_line.rstrip("\n")
        stripped = line_text.strip()
        role_line_text = line_text
        role_line_offset = line_offset
        if not stripped:
            if normalized_role == "script":
                active_script_speaker = None
            line_offset += len(raw_line)
            continue

        if normalized_role == "script":
            _raise_on_missing_marker_boundary(
                line_text,
                source_text=source_text,
                line_offset=line_offset,
                known_speakers=known_speakers,
            )

        visual = _VISUAL_LINE_RE.match(line_text)
        visible_text_line = _VISIBLE_TEXT_RE.match(line_text)
        generic_label = _GENERIC_LABEL_RE.match(line_text)
        structural_visual = bool(
            generic_label and _looks_visual_label(generic_label.group("label"))
        )
        visual_prefix_line = bool(
            (visual or structural_visual) and not visible_text_line
        )
        visual_role_start: int | None = None
        if visual_prefix_line:
            label = (
                visual.group("label")
                if visual
                else generic_label.group("label")
            )
            if label in _SCENE_RESET_LABELS:
                active_script_speaker = None

            # A visual label governs only its prefix, not the rest of the
            # physical line.  For example ``画面：江氏推门。江氏说：“走。”``
            # contains silent staging followed by a real source cue.  Stop the
            # visual range at the first speech candidate and let the ordinary
            # closed parser prove (or reject) that candidate.
            speech_after_visual = next(
                (
                    cue
                    for cue in _CUE_RE.finditer(line_text)
                    if cue.start("speaker") >= generic_label.end()
                ),
                None,
            )
            role_after_visual: re.Match[str] | None = None
            if normalized_role == "script":
                for marker in _ROLE_MARKER_RE.finditer(line_text):
                    if marker.start("label") < generic_label.end():
                        continue
                    candidate = marker.group("label").strip()
                    if _looks_visual_label(candidate):
                        continue
                    if _is_explicit_role_label(
                        candidate, known_speakers=known_speakers
                    ):
                        role_after_visual = marker
                        break
            boundaries = [len(line_text)]
            if speech_after_visual is not None:
                boundaries.append(speech_after_visual.start("speaker"))
            if role_after_visual is not None:
                boundaries.append(role_after_visual.start("label"))
            visual_prefix_end = min(boundaries)
            if (
                role_after_visual is not None
                and role_after_visual.start("label") == visual_prefix_end
            ):
                visual_role_start = visual_prefix_end
                role_line_text = line_text[visual_role_start:]
                role_line_offset = line_offset + visual_role_start
            visual_prefix = line_text[:visual_prefix_end]
            visual_suffix_start = _visual_content_suffix_start(
                line_text, generic_label.end()
            )
            unresolved_visual_suffix = line_text[
                min(visual_suffix_start, visual_prefix_end) : visual_prefix_end
            ]
            if unresolved_visual_suffix.strip() and _contains_dialogue_candidate(
                unresolved_visual_suffix,
                known_speakers=known_speakers,
            ):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line "
                    f"{_line_number(source_text, line_offset + visual_suffix_start)}: "
                    "visual-label suffix contains an unresolved dialogue candidate"
                )
            visual_quote_ranges: list[tuple[int, int]] = []
            for quote in _QUOTE_RE.finditer(visual_prefix):
                _validate_quote_match(
                    quote, source_text=source_text, offset=line_offset
                )
                visual_quote_ranges.append((quote.start(), quote.end()))
            for quote_char in _QUOTE_LIKE_RE.finditer(visual_prefix):
                if not _is_inside(quote_char.start(), visual_quote_ranges):
                    raise SourceDialogueAuthorityError(
                        f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + quote_char.start())}: "
                        "visual text has an unpaired quote-like delimiter"
                    )
            consumed.append((line_offset, line_offset + visual_prefix_end))
            if speech_after_visual is None and visual_role_start is None:
                line_offset += len(raw_line)
                continue

        action_marker = (
            _SCRIPT_ACTION_MARKER_RE.match(line_text)
            if normalized_role == "script"
            else None
        )
        if action_marker:
            # A screenplay action marker excludes only a genuinely pure action
            # line.  It cannot hide a later explicit role/cue/quotation.
            active_script_speaker = None
            action_body = line_text[action_marker.end() :]
            if not _contains_dialogue_candidate(
                action_body, known_speakers=known_speakers
            ):
                consumed.append((line_offset, line_offset + len(line_text)))
                line_offset += len(raw_line)
                continue
            line_text = "。" * action_marker.end() + action_body
            stripped = line_text.strip()
            role_line_text = line_text
            role_line_offset = line_offset

        if normalized_role == "script":
            parenthetical = _PAREN_ROLE_RE.fullmatch(stripped)
            if parenthetical:
                label = parenthetical.group("label").strip()
                if label in _STAGE_DIRECTION_TERMS or any(
                    action in label for action in _ACTION_PARTS
                ):
                    if active_script_speaker is None:
                        raise SourceDialogueAuthorityError(
                            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset)}: "
                            f"parenthetical action {label!r} has no source-authoritative speaker"
                        )
                    line_offset += len(raw_line)
                    continue
                active_script_speaker = _speaker(
                    label,
                    line_number=_line_number(source_text, line_offset),
                    context="parenthetical script role",
                    known_speakers=known_speakers,
                )
                consumed.append((line_offset, line_offset + len(line_text)))
                line_offset += len(raw_line)
                continue

        if visible_text_line or (visual_prefix_line and visual_role_start is None):
            role_parsed, role_speaker = False, None
        else:
            role_parsed, role_speaker = _parse_role_line(
                role_line_text,
                source_text=source_text,
                line_offset=role_line_offset,
                rows=rows,
                consumed=consumed,
                allow_role_labels=normalized_role == "script",
                known_speakers=known_speakers,
            )
        if role_parsed:
            has_unconsumed_cue = False
            if normalized_role == "script":
                # Only a declaration-only ``角色：`` line opens a one-line
                # pending turn.  Inline dialogue consumes the declaration on
                # the same line.  An unbounded active speaker would silently
                # turn following screenplay action into fabricated dialogue.
                declaration_only = bool(
                    re.fullmatch(
                        r"\s*[A-Za-z\u3400-\u4dbf\u4e00-\u9fff·]{1,16}\s*[：:]\s*",
                        role_line_text,
                    )
                )
                active_script_speaker = (
                    role_speaker if declaration_only else None
                )
                has_unconsumed_cue = any(
                    (cue.group("separator") or cue.group("open"))
                    and not _is_inside(
                        line_offset + cue.start("speaker"), consumed
                    )
                    for cue in _CUE_RE.finditer(line_text)
                )
            if not has_unconsumed_cue:
                line_offset += len(raw_line)
                continue

        cue_parsed = _parse_speech_cues(
            line_text,
            source_text=source_text,
            line_offset=line_offset,
            rows=rows,
            consumed=consumed,
            known_speakers=known_speakers,
        )
        if cue_parsed:
            if normalized_role == "script":
                _parse_unconsumed_inline_role_runs(
                    line_text,
                    source_text=source_text,
                    line_offset=line_offset,
                    rows=rows,
                    consumed=consumed,
                    known_speakers=known_speakers,
                )
            _raise_on_unconsumed_delimiterless_cues(
                line_text,
                source_text=source_text,
                line_offset=line_offset,
                consumed=consumed,
                known_speakers=known_speakers,
            )
            for cue in _SPEECH_LIKE_RE.finditer(line_text):
                if not _is_inside(line_offset + cue.start(), consumed):
                    raise SourceDialogueAuthorityError(
                        f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + cue.start())}: "
                        f"unparsed speech cue near {cue.group(0)!r}"
                    )
            for quote in _QUOTE_RE.finditer(line_text):
                _validate_quote_match(
                    quote, source_text=source_text, offset=line_offset
                )
                if not _is_inside(line_offset + quote.start(), consumed):
                    raise SourceDialogueAuthorityError(
                        f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + quote.start())}: "
                        "quotation has no source-authoritative speaker or non-dialogue label"
                    )
            for quote_char in _QUOTE_LIKE_RE.finditer(line_text):
                if not _is_inside(line_offset + quote_char.start(), consumed):
                    raise SourceDialogueAuthorityError(
                        f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + quote_char.start())}: "
                        "unpaired or unconsumed quote-like delimiter"
                    )
            for unsupported in _ROLE_RESIDUE_SEPARATOR_RE.finditer(line_text):
                signal_offset = line_offset + unsupported.start("label")
                if _is_inside(signal_offset, consumed):
                    continue
                raw_label = unsupported.group("label").strip()
                atom, residue = _source_speaker_atom(
                    raw_label, known_speakers=known_speakers
                )
                if atom and residue:
                    predicate_class = _predicate_class(residue)
                    if predicate_class != "action":
                        raise SourceDialogueAuthorityError(
                            f"source_dialogue_authority_ambiguous: line {_line_number(source_text, signal_offset)}: "
                            f"source role {atom!r} has {predicate_class} cue residue {residue!r} before a speech-like separator"
                        )
            line_offset += len(raw_line)
            continue

        if normalized_role == "script" and active_script_speaker:
            bracket_stage = _SCRIPT_DELIVERY_PREFIX_RE.fullmatch(stripped)
            bracket_label = (
                next(
                    str(group or "").strip()
                    for group in bracket_stage.groups()
                    if group is not None
                )
                if bracket_stage
                else None
            )
            if (bracket_label is not None and _is_delivery_stage_label(bracket_label)) or (
                _PAREN_ROLE_RE.fullmatch(stripped)
                and any(term in stripped for term in _STAGE_DIRECTION_TERMS)
            ):
                line_offset += len(raw_line)
                continue
            dialogue_offset = line_offset + (
                len(line_text) - len(line_text.lstrip())
            )
            dialogue, dialogue_offset = _normalize_script_dialogue_segment(
                stripped,
                source_text=source_text,
                offset=dialogue_offset,
            )
            _append_occurrence(
                rows,
                speaker=active_script_speaker,
                dialogue=dialogue,
                source_text=source_text,
                offset=dialogue_offset,
                kind="script_role_inheritance",
            )
            consumed.append((line_offset, line_offset + len(line_text)))
            # Role inheritance is intentionally one substantive line.  More
            # unmarked lines are not uniquely distinguishable from action;
            # the source must repeat an explicit role marker.
            active_script_speaker = None
            line_offset += len(raw_line)
            continue

        _raise_on_unconsumed_delimiterless_cues(
            line_text,
            source_text=source_text,
            line_offset=line_offset,
            consumed=consumed,
            known_speakers=known_speakers,
        )

        if normalized_role == "script" and not active_script_speaker:
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset)}: "
                "script text has no explicit source role"
            )

        # A cue-like residue or an unattributed quotation must not disappear
        # from the denominator.  Either resolve it above or block preflight.
        for cue in _SPEECH_LIKE_RE.finditer(line_text):
            if not _is_inside(line_offset + cue.start(), consumed):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + cue.start())}: "
                    f"unparsed speech cue near {cue.group(0)!r}"
                )
        line_end_cue = _LINE_END_SPEECH_CUE_RE.search(line_text)
        if line_end_cue and not _is_inside(
            line_offset + line_end_cue.start(), consumed
        ):
            raise SourceDialogueAuthorityError(
                f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + line_end_cue.start())}: "
                "speech cue ends at a line break without an attributable utterance separator"
            )
        for cue in _UNSUPPORTED_SPEECH_PUNCT_RE.finditer(line_text):
            if not _is_inside(line_offset + cue.start(), consumed):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + cue.start())}: "
                    f"unsupported separator {cue.group('separator')!r} follows a speech verb"
                )
        for unsupported in _ROLE_RESIDUE_SEPARATOR_RE.finditer(line_text):
            signal_offset = line_offset + unsupported.start("label")
            if _is_inside(signal_offset, consumed):
                continue
            raw_label = unsupported.group("label").strip()
            atom, residue = _source_speaker_atom(
                raw_label, known_speakers=known_speakers
            )
            if atom and residue:
                predicate_class = _predicate_class(residue)
                if predicate_class != "action":
                    raise SourceDialogueAuthorityError(
                        f"source_dialogue_authority_ambiguous: line {_line_number(source_text, signal_offset)}: "
                        f"source role {atom!r} has {predicate_class} cue residue {residue!r} before a speech-like separator"
                    )
        generic_cue = _GENERIC_LABEL_RE.match(line_text)
        if generic_cue and not _is_inside(
            line_offset + generic_cue.start("label"), consumed
        ):
            raw_label = generic_cue.group("label").strip()
            atom, residue = _source_speaker_atom(
                raw_label, known_speakers=known_speakers
            )
            residue_class = _predicate_class(residue) if atom and residue else None
            if (
                (atom and residue and residue_class != "action")
                or raw_label.endswith(_UNSUPPORTED_SPEECH_LEXEMES)
            ):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + generic_cue.start())}: "
                    f"unsupported source speech cue label {raw_label!r}"
                )
        for quote in _QUOTE_RE.finditer(line_text):
            _validate_quote_match(
                quote, source_text=source_text, offset=line_offset
            )
            if not _is_inside(line_offset + quote.start(), consumed):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + quote.start())}: "
                    "quotation has no source-authoritative speaker or non-dialogue label"
                )
        for quote_char in _QUOTE_LIKE_RE.finditer(line_text):
            if not _is_inside(line_offset + quote_char.start(), consumed):
                raise SourceDialogueAuthorityError(
                    f"source_dialogue_authority_ambiguous: line {_line_number(source_text, line_offset + quote_char.start())}: "
                    "unpaired or unconsumed quote-like delimiter"
                )
        line_offset += len(raw_line)

    rows.sort(key=lambda row: (row.char_offset, row.line_number, row.kind))
    # This identity is derived only after the closed source parser completes.
    # Include the complete normalized source, not merely the audible text: two
    # identical utterances (or the same line in a different source revision)
    # must never authorize each other. Offsets and ranges are Unicode codepoint
    # indices, not UTF-8 bytes or UTF-16 code units.
    source_text_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    bound_rows: list[DialogueOccurrence] = []
    for ordinal, row in enumerate(rows):
        identity = {
            "contract": SOURCE_DIALOGUE_OCCURRENCE_CONTRACT,
            "source_text_sha256": source_text_sha256,
            "source_role": normalized_role,
            "source_ordinal": ordinal,
            "char_offset": row.char_offset,
            "speaker": row.speaker,
            "text": row.text,
        }
        identity_sha256 = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        bound_rows.append(replace(
            row,
            occurrence_id=f"source-occurrence:{identity_sha256}",
            source_text_sha256=source_text_sha256,
            source_ordinal=ordinal,
        ))
    return bound_rows


def candidate_protected_dialogue_claims(
    storyboard: dict[str, Any],
) -> list[tuple[str, str]]:
    """Read only the candidate's protected-dialogue claims.

    No other candidate structure is accepted as speaker authority.  The
    source-ingestion list is preferred when present so mirrored top-level
    compatibility rows cannot multiply coverage.
    """

    source_ingestion = storyboard.get("source_ingestion")
    nested_present = isinstance(source_ingestion, dict) and "protected_dialogue" in source_ingestion
    top_present = "protected_dialogue" in storyboard

    def parse_rows(value: Any, *, location: str) -> list[tuple[str, str]]:
        if not isinstance(value, list):
            raise SourceDialogueAuthorityError(
                f"candidate_protected_dialogue_malformed: {location} must be an array"
            )
        parsed: list[tuple[str, str]] = []
        for index, row in enumerate(value):
            if not isinstance(row, dict):
                raise SourceDialogueAuthorityError(
                    f"candidate_protected_dialogue_malformed: {location}[{index}] must be an object"
                )
            speaker_value = row.get("speaker")
            text_value = row.get("text")
            if not isinstance(speaker_value, str) or not speaker_value.strip():
                raise SourceDialogueAuthorityError(
                    f"candidate_protected_dialogue_malformed: {location}[{index}].speaker must be a non-empty string"
                )
            if not isinstance(text_value, str) or not text_value.strip():
                raise SourceDialogueAuthorityError(
                    f"candidate_protected_dialogue_malformed: {location}[{index}].text must be a non-empty string"
                )
            parsed.append((speaker_value.strip(), text_value.strip()))
        return parsed

    nested_claims = (
        parse_rows(
            source_ingestion.get("protected_dialogue"),
            location="source_ingestion.protected_dialogue",
        )
        if nested_present
        else None
    )
    top_claims = (
        parse_rows(storyboard.get("protected_dialogue"), location="protected_dialogue")
        if top_present
        else None
    )
    if nested_claims is not None and top_claims is not None:
        if nested_claims != top_claims:
            raise SourceDialogueAuthorityError(
                "candidate_protected_dialogue_mirror_divergence: top-level and "
                "source_ingestion claims differ in count/order/speaker/text"
            )
        return nested_claims
    return nested_claims if nested_claims is not None else (top_claims or [])


def missing_authoritative_occurrences(
    occurrences: list[DialogueOccurrence],
    claims: list[tuple[str, str]],
) -> list[DialogueOccurrence]:
    """Return source occurrences not covered in exact source order."""

    missing: list[DialogueOccurrence] = []
    claim_index = 0
    for occurrence in occurrences:
        expected = (occurrence.speaker, occurrence.text)
        while claim_index < len(claims) and claims[claim_index] != expected:
            claim_index += 1
        if claim_index >= len(claims):
            missing.append(occurrence)
            continue
        claim_index += 1
    return missing


def authority_claims_match_exactly(
    occurrences: list[DialogueOccurrence],
    claims: list[tuple[str, str]],
) -> bool:
    """Require one candidate claim for every source occurrence, and no more.

    ``missing_authoritative_occurrences`` is useful for locating deletions, but
    by definition it cannot represent an unexpected candidate-only row.  This
    predicate is the authority gate: count, order, speaker, text, punctuation,
    and repeated occurrences must all be identical.  It therefore rejects
    front/back/middle insertions and duplicate extra claims as well as drops.
    """

    return claims == [(row.speaker, row.text) for row in occurrences]
