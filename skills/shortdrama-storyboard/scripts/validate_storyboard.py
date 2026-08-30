#!/usr/bin/env python3
"""R10.1 compact-master validator for Chinese short-drama storyboard JSON.

The validator is intentionally deterministic. It validates the compact
27-field contract, the R10-SPC-1 strict single-shot prompt contract, and the
learned method modules without regenerating any independent batch.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION = "V16.3-148-R10-StableRuntime"
SCHEMA_VERSION = "R10.1"
WORKFLOW_PROFILE = "R10-PG1"
STRICT_OUTPUT_CONTRACT = "R10-SPC-1"

COMPACT_SHOT_FIELDS = [
    "镜头编号", "时长", "累计时长", "功能类型", "场景连续性ID", "镜头类型", "可见主体",
    "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
    "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
    "转场类型", "切镜/转场提示词", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
]
COMPACT_MOTHER_COLUMNS = [
    "分镜序号", "时长", "累计时长", "功能类型/场景连续性ID", "正式分镜母版",
    "GPT关键帧生成指令", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
]
DEPRECATED_SHOT_FIELDS = {
    "运镜主体", "运镜功能", "人物调度", "动作与表演", "台词", "发声方式", "标点语气",
    "心理音轨属性", "生成批次",
}
DEPRECATED_DISPLAY_LABELS = (
    "运镜主体：", "运镜主体:", "运镜功能：", "运镜功能:", "叙事功能：", "叙事功能:",
    "人物调度：", "人物调度:", "动作与表演：", "动作与表演:", "台词：", "台词:",
    "发声方式：", "发声方式:", "标点语气：", "标点语气:",
    "心理音轨属性：", "心理音轨属性:", "生成批次：", "生成批次:",
)
DEPRECATED_LABEL_SCAN_FIELDS = {
    "功能类型", "场景连续性ID", "镜头类型", "可见主体", "空间站位描述", "起始状态",
    "机位", "运镜方式", "画面内容", "音色", "心理音轨", "环境音效", "环境动态",
    "光影", "光源来源", "光源方向", "色温倾向", "生成限制", "转场类型", "切镜/转场提示词",
}
PERSISTED_DERIVED_FIELDS = {
    "正式分镜母版", "正式分镜母版·字段合并", "GPT关键帧生成指令",
    "Seedance 2.5独立正式执行分镜", "Seedance 2.0独立正式执行分镜",
}
COMPACT_TOP_LEVEL_FIELDS = {
    "project_version", "schema_version", "schema_profile", "workflow_profile",
    "prevention_evidence", "source_ingestion", "chapter_intake",
    "field_order", "source_lock", "shots", "total_duration", "chapter_map", "source_mapping",
    "protected_dialogue", "cognition_matrix", "scene_state", "coverage", "anchors",
    "character_anchors", "scene_anchors", "prop_anchors", "costume_anchors", "project_anchors",
    "stage_evidence", "human_review", "canonical_extension",
    "episode_id", "episode", "分集",
}
FORBIDDEN_GENERATION_TOP_LEVEL_FIELDS = {
    "batches", "i2v_batches", "seedance_batches", "seedance_prompt_cache", "derived",
    "formal_mother_template_rows", "mother_template_columns", "gpt_keyframe_prompt_cache", "prompt_cache",
}
DERIVED_OR_CACHE_ROOT_RE = re.compile(
    r"(?:^|_)(?:batches?|seedance|i2v|derived|prompt_cache|cache)(?:$|_)",
    re.I,
)
FORBIDDEN_CANONICAL_EXTENSION_KEY_RE = re.compile(
    r"(?:seedance|i2v|batch|derived|prompt[_-]?cache|cache)",
    re.I,
)

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ABSTRACT_EMOTIONS = {
    "开心", "委屈", "紧张", "生气", "惊讶", "害怕", "悲伤", "厌恶", "惊喜",
    "困惑", "自豪", "羞涩", "惆怅", "平静", "调皮", "冷漠", "焦虑", "惊恐",
    "欣慰", "期待", "伤心", "感动", "爱慕", "尴尬", "放松",
}
DETAIL_PATTERNS = {
    "mouth": re.compile(r"嘴角|嘴唇|上唇|下唇|唇线|嘴巴|下颌|下巴"),
    "eyes": re.compile(r"眼神|目光|眼睛|眼眶|瞳孔|睫毛|眼角"),
    "face": re.compile(r"眉|额头|脸颊|面部|鼻梁|面颊|下颚"),
    "hands": re.compile(r"手|指|拳|掌|袖口"),
    "body": re.compile(r"肩|颈|身体|上身|胸口|背部|姿态|前倾|后仰|蜷缩|僵住"),
    "gaze": re.compile(r"视线|看向|注视|凝视|低垂|移开|锁定|闪躲|游移"),
    "breath": re.compile(r"呼吸|气息|吸气|吞咽"),
    "head": re.compile(r"低头|抬头|侧头|头部|歪头|后仰"),
}
INVISIBLE_SHOT_TERMS = ["书页", "书上内容", "POV书", "第一视角看书", "手部特写", "背影", "远景"]
FACE_TERMS = ["眼睛", "眼神", "目光", "眉", "嘴唇", "嘴角", "瞳孔", "脸颊", "眼眶", "睫毛", "面部", "眨眼"]
FACE_VISIBLE_TERMS = ["面部可见", "眼睛可见", "侧脸可见", "人物反应", "过肩同框", "OTS同框"]
VAGUE_TERMS = ["电影感", "高级感", "氛围感", "自然移动", "高级运镜", "电影感运镜"]
STATIC_MOTIONS = ["无运镜", "固定镜头", "静态镜头", "固定机位"]
TIMELINE_RANGE_RE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到)\s*(?P<end>\d+(?:\.\d+)?)\s*(?:秒|s)",
    re.I,
)
STRICT_TIMELINE_STAGE_RE = re.compile(
    r"【(?P<start>\d+\.\d)—(?P<end>\d+\.\d)秒】(?P<body>.*?)(?=【\d+\.\d—\d+\.\d秒】|\Z)",
    re.S,
)
EP_SHOT_ID_RE = re.compile(r"^EP(?P<episode>\d{2})-(?P<shot>\d{3})$")
LEGACY_DIALOGUE_TAG_RE = re.compile(
    r"【(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})】\s*[“\"]?(?P<line>[^【\n｜|；;”\"]+)[”\"]?"
)
INTEGRATED_DIALOGUE_RE = re.compile(
    r"(?:^|[；;。！？!?，,、】])\s*(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})说道[：:]?\s*[“\"](?P<line>[^”\"\n]+)[”\"]"
)
REQUIRED_ASPECT_RATIO_PHRASE = "9:16竖屏"
REQUIRED_ASPECT_RATIO_RE = re.compile(
    r"(?<!\d)9:16竖屏(?=$|[\s；;，,。.])"
)
ANY_ASPECT_TOKEN_RE = re.compile(
    r"(?<!\d)(?P<width>\d+)\s*(?P<separator>[:：/])\s*"
    r"(?P<height>\d+(?:\.\d+)?)(?!\d)"
)
FORBIDDEN_PROMPT_PHRASE_RE = re.compile(
    r"9\s*[:：]\s*16\s*竖屏真人古装短剧(?:截图质感|单帧)"
)
BGM_TOKEN_RE = re.compile(r"(?<![A-Za-z])BGM(?![A-Za-z])", re.I)
NEGATIVE_MUSIC_CLAUSE_RE = re.compile(
    r"(?:禁止|不要|不得|不使用|不生成|无)(?:任何)?(?:让)?"
    r"(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)?\s*"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)"
    r"(?:响起|播放|进入|渐入|切入|铺开|持续|开始)?",
    re.I,
)
ACTIVE_MUSIC_DIRECTIVE_RE = re.compile(
    r"(?:加入|播放|配上|使用|生成|插入|铺设|搭配|切入)\s*(?:任何)?"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)|"
    r"(?:BGM|背景音乐|额外配乐|配乐|音乐)\s*"
    r"(?:响起|播放|进入|渐入|切入|铺开|持续|采用|使用|开始|为)",
    re.I,
)
MUSIC_TERM_RE = re.compile(
    r"(?<![A-Za-z])BGM(?![A-Za-z])|背景音乐|额外配乐|配乐|音乐",
    re.I,
)
TRANSITION_EMPTY_SEPARATOR_RE = re.compile(r"[；;。]\s*[；;。]")
CROSS_SHOT_TERMS = {
    "承接上一镜", "延续之前", "保持关键帧", "保持上一关键帧", "按照上一张",
    "沿用上一张", "同上", "继续原动作", "继续刚才", "保持上一镜", "承接关键帧",
    "上一关键帧", "上一镜头", "前一镜", "前镜",
    "下一镜",
}
KEYFRAME_FORBIDDEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*\d+(?:\.\d+)?\s*(?:秒|s)|"
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
    re.I,
)

KEYFRAME_TIMECODE_RE = re.compile(
    r"(?:\[|【)?\s*\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*"
    r"\d+(?:\.\d+)?\s*(?:秒|s)\s*(?:\]|】|[:：])?",
    re.I,
)
KEYFRAME_PROTECTED_VISUAL_LABEL_PATTERN = (
    r"(?:参考图锚点|可见文字|画内文字|道具文字|"
    r"匾额(?:文字)?|牌匾(?:文字)?|招牌(?:文字)?|题字)"
)
KEYFRAME_PROTECTED_VISUAL_FRAGMENT_RE = re.compile(
    rf"(?:【{KEYFRAME_PROTECTED_VISUAL_LABEL_PATTERN}】[^；;\n，,]*|"
    rf"(?:画内)?(?:匾额|牌匾|招牌|门牌|题字|"
    rf"可见文字|画内文字|道具文字)"
    rf"(?=[^；;\n，,]{{0,32}}(?:[“\"「『]|写着|写有|刻着|刻有|"
    rf"题着|题有|印着|印有|可见文字|字样|清晰可见))"
    rf"[^；;\n，,]*)"
)
KEYFRAME_BRACKETED_DIALOGUE_RE = re.compile(
    r"【[^】\n]{1,40}】\s*[^。！？!?；;\n]*(?:[。！？!?；;]|$)"
)
KEYFRAME_QUOTED_DIALOGUE_RE = re.compile(r"[“\"「『][^”\"」』\n]*[”\"」』]")
KEYFRAME_LABELED_DIALOGUE_RE = re.compile(
    r"(?:精确)?(?:台词|对白)(?:文字|内容)?\s*[:：]\s*"
    r"[^。！？!?；;\n]*(?:[。！？!?；;]|$)"
)
KEYFRAME_AUDIO_ONLY_CLAUSE_MARKERS = (
    "口型", "说话时", "对白", "动作声", "环境声", "问句",
    "陈述句", "感叹句", "对白节奏", "声音强弱", "语速节奏", "呼吸状态",
    "停顿位置", "重音位置", "尾音变化", "声量", "语速", "停顿", "重音",
    "尾音", "发声", "环境音", "音效", "配乐", "音乐", "BGM", "声音",
    "鸟声", "脚步声", "呼吸声", "衣料声", "器物声", "叶声", "鸟鸣",
    "虫鸣", "风声", "声线", "音轨", "音频",
)
KEYFRAME_AUDIO_SOURCE_CLAUSE_RE = re.compile(
    r"(?:古琴|琴|雨|水|敲门|雷|钟|婴儿哭|哭|笑|喊|脚步|呼吸|"
    r"衣料|器物|风|鸟|虫|叶)(?:声|鸣)"
)
MULTI_CAMERA_RE = re.compile(r"第二机位|另一机位|另一个机位|多机位|切至[^；。]{0,20}机位")
VISIBLE_NON_CHARACTER_RE = re.compile(
    r"人群|路人|群众|百姓|行人|摊贩|商贩|顾客|食客|茶客|脚夫|守卫|群演|背景人物|"
    r"道具|手部|书页|信件|木盒|礼匣|茶盏|茶杯|门窗|环境|空间|前景|中景|后景"
)
START_RESULT_ACTION_PATTERNS = (
    (re.compile(r"已(?:经)?(?:接过|接稳|拿到|持稳)[^；。]{0,12}(?:礼匣|木盒|茶盏|茶杯|道具)?"), re.compile(r"接过|接稳|接杯|接盏|交接|递交"), "接取结果已在起始状态完成"),
    (re.compile(r"已(?:经)?(?:奉上|递出|递交)"), re.compile(r"奉上|奉茶|递出|递交"), "递交结果已在起始状态完成"),
    (re.compile(r"已(?:经)?(?:揭盖|开盖|打开|开启)"), re.compile(r"揭盖|开盖|打开|开启"), "开启结果已在起始状态完成"),
    (re.compile(r"已(?:经)?(?:浅啜|饮下|喝下|喝完)"), re.compile(r"浅啜|饮下|喝下|喝茶|举杯"), "饮用结果已在起始状态完成"),
    (re.compile(r"已(?:经)?(?:落座|坐下|起身|站起|跨过|进入|离开|跪下|行礼)"), re.compile(r"落座|坐下|起身|站起|跨过|跨门槛|进入|离开|跪下|行礼"), "位移或姿态结果已在起始状态完成"),
)
ACTION_DURATION_PATTERNS = (
    ("奉茶", re.compile(r"奉茶"), 0.8),
    ("接杯", re.compile(r"接杯|接盏|接过[^；。]{0,8}(?:杯|盏)"), 0.8),
    ("揭盖", re.compile(r"揭盖|开盖"), 0.5),
    ("浅啜", re.compile(r"浅啜|饮茶|喝茶"), 1.0),
    ("递交", re.compile(r"递交|递出|交接"), 0.8),
    ("跨门槛", re.compile(r"跨(?:过)?门槛"), 1.0),
    ("起落坐姿", re.compile(r"起身|坐下|落座|屈膝|跪下|行礼"), 0.8),
    ("点头", re.compile(r"点头|颔首"), 0.35),
    ("抬收手", re.compile(r"抬手|收手|指向"), 0.4),
    ("头眼动作", re.compile(r"转头|抬眼|低头"), 0.3),
)

EXECUTION_SHOT_FIELDS = (
    "镜头类型", "可见主体", "空间站位描述", "起始状态", "机位", "运镜方式",
    "画面内容", "音色", "心理音轨", "环境音效", "环境动态", "光影", "光源来源",
    "光源方向", "色温倾向", "生成限制", "转场类型", "切镜/转场提示词",
)
KELVIN_RE = re.compile(r"(?:\d{3,5}\s*(?:K|k|开尔文))|Kelvin", re.I)
COLOR_RELATION_RE = re.compile(r"暖|冷|中性")
COLOR_MATERIAL_RE = re.compile(r"前景|中景|后景|背景|空间|主区|暗部|高光|肤色|面部|皮肤|衣料|木|纸|本色|低饱和|色偏")
LIGHT_SUBJECT_RE = re.compile(r"主体|人物|面部|眼神|手部|道具|高光|暗部|受光|明暗|皮肤|衣料|木构")
LIGHT_SOURCE_RE = re.compile(r"窗|门|日光|晨光|天光|烛|灯|火|月光|反射光|补光|廊|庭院|水面|雪面")
LIGHT_DIRECTION_RE = re.compile(r"画面左|画面右|左侧|右侧|左前|右前|左后|右后|上方|下方|前方|后方")
LIGHT_PATH_RE = re.compile(r"斜落|斜照|投向|落向|补面|衰减|递减|纵深|暗侧|明侧")

FORMAL_MASTER_FIELD_ORDER = [
    "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
    "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
    "转场类型", "切镜/转场提示词",
]

VOICE_DIMENSION_PATTERNS = {
    "声音强弱": re.compile(r"声音|音量|放轻|压低|变亮|变稳|抬高"),
    "语速节奏": re.compile(r"语速|一字一句|断断续续|咬字|平稳|从容|急促|缓慢|利落"),
    "呼吸状态": re.compile(r"呼吸|气息|吸气"),
    "停顿位置": re.compile(r"停顿|短停|停一下|开口前|句中|关键词前|卡住"),
    "重音位置": re.compile(r"重音|加重|强调|咬重"),
    "尾音变化": re.compile(r"尾音|上扬|下压|发虚|放柔|拉长|落下|收住"),
}

PUBLIC_SCENE_RE = re.compile(
    r"街道|街市|闹市|市集|集市|市场|商街|商业街|长街|巷口|巷道|城门|码头|渡口|"
    r"车站|驿站|驿道|茶馆|酒楼|饭馆|餐馆|客栈大堂|客栈前厅|庙会|寺院前庭|山门|"
    r"店铺|药铺|医馆|衙门前|衙门外|宫门|宫道|广场"
)
PUBLIC_EMPTY_RE = re.compile(r"空街|空巷|无人街|街上无人|人迹罕至|闭市|清场|人群已撤离|店门紧闭")
PUBLIC_EXEMPTION_CAUSE_RE = re.compile(
    r"宵禁|闭市|封锁|疏散|清场|戒严|灾后|深夜|黎明前|停业|剧情要求|危险预警|追捕"
)
PUBLIC_DENSITY_RE = re.compile(
    r"稀疏|低密度|少量|两三|三五|零散|中等密度|中等人流|适中人流|密集|高密度|拥挤|人潮|"
    r"(?:约)?[一二三四五六七八九十百\d]+(?:至|到|-|—)?[一二三四五六七八九十百\d]*名(?:行人|路人|百姓|顾客|摊贩|脚夫|守卫|香客|食客|茶客)"
)
PUBLIC_ROLE_RE = re.compile(
    r"行人|路人|百姓|摊贩|商贩|小贩|顾客|食客|茶客|脚夫|挑夫|车夫|骑手|伙计|小二|"
    r"掌柜|守卫|兵丁|差役|僧人|香客|船工|纤夫|搬运工|店家|货郎|孩童|老人"
)
PUBLIC_ACTION_RE = re.compile(
    r"行走|穿行|挑选|交谈|驻足|避让|叫卖|吆喝|搬运|装卸|推车|赶车|牵马|端菜|收拾|"
    r"排队|巡逻|查验|上香|进出|开门|收摊|交易|付钱|张望|擦肩|落座|饮茶|吃饭|挑担|招呼"
)
PUBLIC_VISUAL_LAYER_PATTERNS = {
    "near": re.compile(r"前景|近处|身侧|画面左侧|画面右侧"),
    "mid": re.compile(r"中景|中层|街面|周围"),
    "far": re.compile(r"远景|远处|背景|后景|街口|巷口|城门方向"),
}
PUBLIC_AUDIO_LAYER_PATTERNS = {
    "near": re.compile(r"近处|脚边|身侧|贴近|前景"),
    "mid": re.compile(r"中层|中景|街面|周围|身后"),
    "far": re.compile(r"远处|远景|画外|街口|巷口|城门方向|门外"),
}
PUBLIC_AUDIO_FAMILY_PATTERNS = {
    "movement": re.compile(r"脚步|鞋底|踩水|踩雪|衣料|衣摆|木屐|马蹄|车轮|车辙|轿杆|推车|担子|扁担"),
    "crowd": re.compile(r"人声底噪|低声交谈|交谈声|含混人声|人群低语|模糊人声|远处人声|客声|食客低语"),
    "commerce": re.compile(r"叫卖|吆喝|铜钱|算盘|货物|瓷碗|碗筷|杯盏|盘碟|柜台|案板|秤砣"),
    "weather_nature": re.compile(r"风声|雨声|雨滴|滴水|溅水|踏雪|雪地|浪声|水声|橹声|钟声|鸟鸣|虫鸣|檐铃|门轴|木门"),
    "work": re.compile(r"搬运|装卸|锤打|切菜|炉火|油锅|劈柴|扫地"),
    "official": re.compile(r"守卫口令|查验声|城门|木栓|甲片|兵器|号角"),
}
PUBLIC_DIALOGUE_DUCK_RE = re.compile(r"退至底层|压至底层|压低|不遮盖(?:对白|台词)|对白清晰|台词清晰|对白优先|为对白让位")
PUBLIC_MODERN_AUDIO_RE = re.compile(r"汽车喇叭|手机铃声|电子提示音|广播|扩音器|警笛|发动机|地铁|电车")
PUBLIC_ANONYMOUS_CLEAR_SPEECH_RE = re.compile(
    r"(?:路人|人群|百姓|食客|茶客|摊贩|商贩|小贩|顾客|客人)(?:甲|乙|丙)?"
    r"(?:说|喊|道|台词)[：:]?\s*[“‘「『\"]"
)
PUBLIC_FORBIDDEN_EXTRAS_RE = re.compile(
    r"禁止(?:出现|添加|生成|加入)?(?:任何|所有|全部)?(?:额外角色|路人|人群|群众|行人)(?:出现|入镜|进入)?"
)
PUBLIC_TRANSITION_RE = re.compile(r"涌入|散开|疏散|离开|聚集|人流变化|逐渐(?:变得)?(?:稀疏|密集)|由(?:稀疏|密集)变")
PUBLIC_WIDE_RE = re.compile(r"大全景|全景|远景|建立")
PUBLIC_SOURCE_PAIRS = [
    (re.compile(r"马蹄|马嘶"), re.compile(r"马|马车|骑手|车夫")),
    (re.compile(r"车轮|木轮|车辙"), re.compile(r"车|马车|板车|推车|轿")),
    (re.compile(r"碗筷|杯盏|盘碟"), re.compile(r"食客|茶客|客人|伙计|小二|饭桌|茶桌|摊位")),
    (re.compile(r"叫卖|吆喝"), re.compile(r"摊贩|商贩|小贩|货郎|摊位|店家")),
    (re.compile(r"钟声|木鱼"), re.compile(r"寺|庙|钟楼|僧人|香客")),
]


def _candidate_rule_paths(explicit: str | None = None) -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend([
        here.parent / "rules" / "storyboard_runtime_rules.json",
        here.parent / "references" / "storyboard_runtime_rules.json",
        here / "storyboard_runtime_rules.json",
        Path.cwd() / "rules" / "storyboard_runtime_rules.json",
        Path.cwd() / "storyboard_runtime_rules.json",
    ])
    return candidates


def load_runtime_rules(explicit: str | None = None) -> dict[str, Any]:
    for path in _candidate_rule_paths(explicit):
        if path.is_file():
            rules = json.loads(path.read_text(encoding="utf-8"))
            # The standalone catalog is the canonical documentation source.
            # Merge newly introduced codes so the functional validator returns
            # their blocking and repair metadata even before a runtime-rule
            # snapshot is regenerated.
            catalog_path = path.parent / "error_catalog.json"
            if catalog_path.is_file():
                standalone = json.loads(catalog_path.read_text(encoding="utf-8"))
                existing = {row.get("error_code") for row in rules.get("error_catalog", []) if isinstance(row, dict)}
                rules.setdefault("error_catalog", []).extend(
                    row for row in standalone
                    if isinstance(row, dict) and row.get("error_code") not in existing
                )
            return rules
    raise FileNotFoundError("storyboard_runtime_rules.json not found")


def _catalog_map(rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x["error_code"]: x for x in rules.get("error_catalog", [])}


def cjk_len(value: Any) -> int:
    return len(CJK_RE.findall(str(value or "")))


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _forbidden_extension_paths(value: Any, location: str = "canonical_extension") -> list[str]:
    """Return nested extension keys that would persist generated/cache state."""

    hits: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_location = f"{location}.{key}"
            if FORBIDDEN_CANONICAL_EXTENSION_KEY_RE.search(key):
                hits.append(child_location)
            hits.extend(_forbidden_extension_paths(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(_forbidden_extension_paths(child, f"{location}[{index}]"))
    return hits


def is_none_value(value: Any) -> bool:
    text = str(value or "").strip().rstrip("。.;；")
    return not text or text in {"无", "不适用", "N/A", "none", "None"} or text.startswith("不适用：")


def _issue(
    catalog: dict[str, dict[str, Any]],
    code: str,
    location: str,
    evidence: str,
    repair: str | None = None,
    severity: str | None = None,
    template_gate_id: str | None = None,
    blocking: bool | None = None,
) -> dict[str, Any]:
    spec = catalog.get(code, {})
    sev = severity or spec.get("severity", "fail")
    issue = {
        "error_code": code,
        "severity": sev,
        "blocking": bool(
            blocking
            if blocking is not None
            else (sev == "fail" if severity is not None else spec.get("blocking", sev == "fail"))
        ),
        "location": location,
        "evidence": evidence,
        "repair_action": repair or spec.get("repair_action", "按R10规则修复后重跑。"),
    }
    if template_gate_id:
        issue["template_gate_id"] = template_gate_id
    return issue


def normalize_shot(raw: dict[str, Any], aliases: dict[str, str]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    shot = copy.deepcopy(raw)
    used = []
    for old, new in aliases.items():
        if old in shot and new not in shot:
            shot[new] = shot[old]
            used.append((old, new))
    return shot, used


def detail_categories(text: str) -> list[str]:
    return [name for name, pattern in DETAIL_PATTERNS.items() if pattern.search(text)]


def match_motion(value: str, motions: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = value.strip().casefold()
    for motion in motions:
        names = [motion["name"], *motion.get("aliases", [])]
        if any(str(name).casefold() in normalized or normalized in str(name).casefold() for name in names):
            return motion
    return None


def _motion_has_natural_subject(motion_text: str, visible: str) -> bool:
    if re.search(r"(?:推近|靠近|跟随|围绕|对准|落到|移向|锁定|聚焦|展示|揭示|拍摄|朝向)\s*(?:无|不适用)", motion_text):
        return False
    target_cue = re.compile(
        r"(?:推近|靠近|跟随|环绕|对准|落到|移向|锁定|聚焦|展示|揭示|拍摄|朝向)"
        r"\s*(?:在|向|着|至)?\s*(?!用于|以便|从而)(?:[\u3400-\u9fffA-Za-z0-9·]{1,20})"
    )
    if target_cue.search(motion_text):
        return True
    if re.search(r"主体|人物|角色|面部|眼神|双手|道具|门窗|桌面|信件|环境|空间|前景|后景", motion_text):
        return True
    for part in re.split(r"[、，,；;｜|与和及\s]", visible):
        subject = re.split(r"上半身|全身|面部|双手|肩缘|背影|轮廓|可辨|可见", part, maxsplit=1)[0].strip()
        if len(subject) >= 2 and subject in motion_text:
            return True
    return False


def _motion_has_narrative_purpose(motion_text: str, motion: dict[str, Any]) -> bool:
    if _has_any(motion_text, [str(value) for value in motion.get("functions", [])]):
        return True
    return bool(re.search(r"用于|以便|从而|强化|突出|聚焦|制造|表现|展示|揭示|建立|营造|传达|强调|放大|呈现", motion_text))


def parse_blink_range(text: str) -> tuple[int, int] | None:
    patterns = [
        r"(\d+)\s*(?:-|–|—|~|～|至|到)\s*(\d+)\s*次(?:自然)?眨眼",
        r"(?:自然)?眨眼\s*(\d+)\s*(?:-|–|—|~|～|至|到)\s*(\d+)\s*次",
        r"(\d+)\s*次(?:自然)?眨眼",
        r"(?:自然)?眨眼\s*(\d+)\s*次",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            if len(m.groups()) == 2:
                return int(m.group(1)), int(m.group(2))
            value = int(m.group(1))
            return value, value
    return None


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _validate_punctuation(dialogue: str, delivery: str) -> list[str]:
    missing = []
    checks = [
        (("……" in dialogue or "..." in dialogue), ["停顿", "卡住", "断续"], "省略号停顿/卡住"),
        (("——" in dialogue or "—" in dialogue), ["转折", "打断", "强调", "断裂"], "破折号转折/打断"),
        (("？！" in dialogue or "!?" in dialogue or "?!" in dialogue), ["质问", "爆发", "加重", "重音"], "问叹号质问/爆发"),
        (("？" in dialogue or "?" in dialogue), ["上扬", "试探", "质问", "疑问", "收住"], "问号尾音/意图"),
        (("！" in dialogue or "!" in dialogue), ["重音", "加强", "爆发", "警告", "兴奋"], "感叹号情绪/重音"),
        (("～" in dialogue or "~" in dialogue), ["拉长", "轻快", "柔软", "放柔"], "波浪号尾音"),
    ]
    for triggered, expected, label in checks:
        if triggered and not _has_any(delivery, expected):
            missing.append(label)
    return missing


def _pause_budget(dialogue: str) -> float:
    ellipses = dialogue.count("……") + dialogue.count("...")
    dashes = dialogue.count("——") + dialogue.count("—")
    explosive = dialogue.count("？！") + dialogue.count("?!") + dialogue.count("!?")
    return 0.3 * (ellipses + dashes) + 0.2 * explosive


def extract_dialogues(picture_content: Any) -> list[tuple[str, str]]:
    """Extract integrated R10-SPC-1 dialogue and legacy read-only tags."""
    rows: list[tuple[str, str]] = []
    text = str(picture_content or "")
    for match in INTEGRATED_DIALOGUE_RE.finditer(text):
        line = match.group("line").strip()
        if line:
            rows.append((match.group("speaker").strip(), line))
    for match in LEGACY_DIALOGUE_TAG_RE.finditer(text):
        line = match.group("line").strip()
        sentence = re.match(r"(.+?[。！？?!](?:[。！？?!…])?)", line)
        if sentence:
            line = sentence.group(1).strip()
        if line:
            rows.append((match.group("speaker").strip(), line))
    return rows


def _validate_timeline(text: str, duration: float, *, strict: bool = False) -> list[str]:
    """Return concrete timeline faults; R10-SPC-1 requires exactly three stages."""
    strict_matches = list(STRICT_TIMELINE_STAGE_RE.finditer(text))
    if strict:
        faults: list[str] = []
        broad_segments = list(TIMELINE_RANGE_RE.finditer(text))
        if len(strict_matches) != 3:
            faults.append(f"R10-SPC-1时间阶段必须恰好3段，当前标准阶段为{len(strict_matches)}段")
        if len(broad_segments) != len(strict_matches):
            faults.append("存在未使用全角方括号、中文长横、一位小数或“秒”单位的非标准时间段")
        if len(strict_matches) != 3:
            return faults
        segments = [(float(m.group("start")), float(m.group("end"))) for m in strict_matches]
        for index, match in enumerate(strict_matches, 1):
            if not match.group("body").strip():
                faults.append(f"第{index}段为空")
    else:
        segments = [(float(m.group("start")), float(m.group("end"))) for m in TIMELINE_RANGE_RE.finditer(text)]
        if not segments:
            return []
        faults = []
        if len(segments) > 3:
            faults.append(f"时间段数量为{len(segments)}，最多3段")
    if not segments:
        return faults
    tolerance = 0.02
    if abs(segments[0][0]) > tolerance:
        faults.append(f"首段必须从0秒开始，当前{segments[0][0]:g}秒")
    previous_end: float | None = None
    for index, (start, end) in enumerate(segments, 1):
        if end <= start:
            faults.append(f"第{index}段终点{end:g}必须大于起点{start:g}")
        if previous_end is not None:
            if start < previous_end - tolerance:
                faults.append(f"第{index}段与上一段重叠：{start:g}<{previous_end:g}")
            elif start > previous_end + tolerance:
                faults.append(f"第{index}段与上一段不连续：{previous_end:g}->{start:g}")
        previous_end = end
    if segments and abs(segments[-1][1] - duration) > tolerance:
        faults.append(f"末段必须覆盖至镜头时长{duration:g}秒，当前止于{segments[-1][1]:g}秒")
    return faults


def _format_seconds(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s*(?:秒|s)$", "", text, flags=re.I).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        text = f"{float(text):.1f}"
    return text


def _projection_text(value: Any) -> str:
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


def _assert_generation_aspect_contract(value: Any) -> None:
    text = str(value or "").strip()
    malformed = sorted(
        {
            match.group(0)
            for match in ANY_ASPECT_TOKEN_RE.finditer(text)
            if (
                match.group("width") != "9"
                or match.group("separator") not in {":", "："}
                or match.group("height") != "16"
            )
        }
    )
    remainder = re.sub(
        r"(?<!\d)9\s*[:：]\s*16(?![\d.])\s*(?:"
        r"竖屏真人古装短剧(?:截图质感|单帧)?|"
        r"真人古装短剧(?:截图质感|单帧)?|竖屏)?",
        "",
        text,
        flags=re.I,
    )
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
    text = _projection_text(value)
    matches = list(ANY_ASPECT_TOKEN_RE.finditer(text))
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
        text.count(REQUIRED_ASPECT_RATIO_PHRASE) != 1
        or len(matches) != 1
        or malformed
        or any(term in text for term in ("横屏", "方屏"))
    ):
        raise ValueError(
            f"aspect_ratio_projection_violation: {projection} must contain "
            "exactly one 9:16竖屏 contract and no competing ratio"
        )


def _normalize_generation_limits(value: Any) -> str:
    _assert_generation_aspect_contract(value)
    text = str(value or "").strip()
    active_music = _active_music_directive(text)
    if active_music:
        raise ValueError(
            "generation_limit_audio_contract_violation: affirmative in-shot "
            f"music directive is forbidden: {active_music!r}"
        )
    remainder = re.sub(
        r"(?<!\d)9\s*[:：]\s*16(?![\d.])\s*(?:"
        r"竖屏真人古装短剧(?:截图质感|单帧)?|"
        r"真人古装短剧(?:截图质感|单帧)?|竖屏)?",
        "",
        text,
        flags=re.I,
    )
    remainder, music_mentioned = _strip_negative_music_clauses(remainder)
    remainder = re.sub(r"\s*[；;]\s*", "；", remainder)
    remainder = re.sub(r"；{2,}", "；", remainder).strip(" ；;，,。")
    parts = [REQUIRED_ASPECT_RATIO_PHRASE]
    if remainder:
        parts.append(remainder)
    if music_mentioned:
        parts.append("禁止额外配乐")
    return "；".join(parts)


def project_formal_master(shot: dict[str, Any]) -> str:
    """Compile the exact fifth-column R10-SPC-1 formal master cell."""
    projected = dict(shot)
    _assert_active_timing_precision(projected)
    projected["生成限制"] = _normalize_generation_limits(
        projected.get("生成限制")
    )
    paragraphs = [
        f"镜头序号：{_projection_text(projected.get('镜头编号'))}"
        f"（{_format_seconds(projected.get('时长'))}s；"
        f"累计{_format_seconds(projected.get('累计时长'))}s）",
        f"镜头类型：{_projection_text(projected.get('镜头类型'))}｜"
        f"可见主体：{_projection_text(projected.get('可见主体'))}",
    ]
    paragraphs.extend(
        f"{field}：{_projection_text(projected.get(field))}"
        for field in FORMAL_MASTER_FIELD_ORDER
    )
    formal = "\n\n".join(paragraphs)
    _assert_projected_aspect_contract(formal, projection="formal_master")
    if BGM_TOKEN_RE.search(formal):
        raise ValueError(
            "formal_master_audio_contract_violation: BGM literal is forbidden"
        )
    active_music = _active_music_directive(formal)
    if active_music:
        raise ValueError(
            "formal_master_audio_contract_violation: affirmative in-shot "
            f"music directive is forbidden: {active_music!r}"
        )
    context_hits = sorted(term for term in CROSS_SHOT_TERMS if term in formal)
    if context_hits:
        raise ValueError(
            "formal_master_context_violation: cross-shot references are "
            "forbidden: " + ", ".join(context_hits)
        )
    return formal


def _protect_visual_text_fragments(value: Any) -> tuple[str, dict[str, str]]:
    """Mirror the service's protection of explicitly visible text/anchor labels."""

    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"\ue000VISUAL{len(protected)}\ue001"
        protected[token] = match.group(0)
        return token

    return (
        KEYFRAME_PROTECTED_VISUAL_FRAGMENT_RE.sub(
            replace,
            _projection_text(value),
        ),
        protected,
    )


def _visual_only_clauses(value: Any, *, strip_dialogue: bool = False) -> str:
    """Mirror the service's visible-only deterministic projection exactly."""
    result = KEYFRAME_TIMECODE_RE.sub("", str(value or ""))
    protected_visual: dict[str, str] = {}
    if strip_dialogue:
        result, protected_visual = _protect_visual_text_fragments(result)
        result = KEYFRAME_QUOTED_DIALOGUE_RE.sub("", result)
        result = KEYFRAME_BRACKETED_DIALOGUE_RE.sub("", result)
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
            if any(
                marker.casefold() in clause.casefold()
                for marker in KEYFRAME_AUDIO_ONLY_CLAUSE_MARKERS
            ):
                continue
            if KEYFRAME_AUDIO_SOURCE_CLAUSE_RE.search(clause):
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


def project_gpt_keyframe(shot: dict[str, Any]) -> str:
    """Compile the visual-only sixth-column projection from the same canonical."""
    fields = (
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
    scrubbed_fields = {
        "空间站位描述",
        "起始状态",
        "机位",
        "环境动态",
        "参考图锚定",
        "生成限制",
    }
    lines = ["GPT关键帧生成指令", f"画幅：{REQUIRED_ASPECT_RATIO_PHRASE}"]
    for field in fields:
        value = _projection_text(shot.get(field))
        if field == "生成限制":
            value = re.sub(
                r"(?<!\d)9\s*[:：]\s*16(?![\d.])\s*(?:"
                r"竖屏真人古装短剧(?:截图质感|单帧)?|"
                r"真人古装短剧(?:截图质感|单帧)?|竖屏)?",
                "",
                _normalize_generation_limits(value),
                flags=re.I,
            )
            value = re.sub(r"\s*[；;]\s*", "；", value)
            value = re.sub(r"；{2,}", "；", value).strip(" ；;，,。")
        if field in scrubbed_fields:
            value = _visual_only_clauses(value, strip_dialogue=True)
        if value:
            lines.append(f"{field}：{value}")
    prompt = "\n".join(lines)
    _assert_projected_aspect_contract(prompt, projection="gpt_keyframe")
    forbidden_hits = _keyframe_forbidden_hits(prompt)
    context_hits = sorted(term for term in CROSS_SHOT_TERMS if term in prompt)
    if forbidden_hits or context_hits:
        raise ValueError(
            "keyframe_static_contract_violation: "
            + "; ".join(
                part
                for part in (
                    "dynamic=" + ",".join(forbidden_hits)
                    if forbidden_hits else "",
                    "cross_shot=" + ",".join(context_hits)
                    if context_hits else "",
                )
                if part
            )
        )
    return prompt


def project_export_row(shot: dict[str, Any]) -> dict[str, Any]:
    """Return one exact ten-column export row without persisting projections."""
    function = _projection_text(shot.get("功能类型"))
    scene = _projection_text(shot.get("场景连续性ID"))
    function_scene = "\n".join(
        row
        for row in (
            f"功能类型：{function}" if function else "",
            f"场景连续性ID：{scene}" if scene else "",
        )
        if row
    )
    return {
        "分镜序号": shot.get("镜头编号", ""),
        "时长": shot.get("时长", ""),
        "累计时长": shot.get("累计时长", ""),
        "功能类型/场景连续性ID": function_scene,
        "正式分镜母版": project_formal_master(shot),
        "GPT关键帧生成指令": project_gpt_keyframe(shot),
        "参考图锚定": shot.get("参考图锚定", ""),
        "覆盖原文": shot.get("覆盖原文", ""),
        "原文依据类型": shot.get("原文依据类型", ""),
        "原文支撑句": shot.get("原文支撑句", ""),
    }


def _normalized_clause(value: Any) -> str:
    return re.sub(
        r"[\s，。；：、,.!?！？;:\-—–（）()\[\]【】“”\"']",
        "",
        str(value or ""),
    ).casefold()


def _transition_duplicate_faults(value: Any) -> list[str]:
    text = str(value or "")
    faults: list[str] = []
    if TRANSITION_EMPTY_SEPARATOR_RE.search(text):
        faults.append("出现连续空分隔符")
    clauses = [item.strip() for item in re.split(r"[；;。\n]+", text) if item.strip()]
    normalized = [_normalized_clause(item) for item in clauses]
    for index in range(1, len(normalized)):
        if normalized[index] and normalized[index] == normalized[index - 1]:
            faults.append(f"相邻切镜短句重复：{clauses[index]!r}")
    return faults


def _visible_character_names(value: Any) -> list[str]:
    names: list[str] = []
    for raw in re.split(r"[、，,；;和与及+＋]", str(value or "")):
        candidate = re.sub(r"[（(].*?[）)]", "", raw).strip()
        candidate = re.sub(r"^(?:画面|前景|中景|后景|左侧|右侧|中央)", "", candidate).strip()
        if not candidate or VISIBLE_NON_CHARACTER_RE.search(candidate):
            continue
        if candidate not in names:
            names.append(candidate)
    return names


def _start_result_faults(start_state: Any, picture_content: Any) -> list[str]:
    start = str(start_state or "")
    picture = str(picture_content or "")
    return [label for result_re, action_re, label in START_RESULT_ACTION_PATTERNS if result_re.search(start) and action_re.search(picture)]


def _prop_state_faults(value: Any, *, strict: bool = False) -> list[str]:
    faults: list[str] = []
    if value in (None, "", "无", "不适用"):
        return faults
    if isinstance(value, dict):
        for prop_id, raw_state in value.items():
            if not isinstance(raw_state, dict):
                faults.append(f"道具{prop_id!r}状态必须为holder/state对象")
                continue
            holder = raw_state.get("holder")
            state = raw_state.get("state")
            if isinstance(holder, list):
                holders = [str(item).strip() for item in holder if str(item).strip()]
            else:
                holders = [item.strip() for item in re.split(r"[、/|｜]", str(holder or "")) if item.strip()]
            if len(holders) != 1:
                faults.append(f"道具{prop_id!r}同一时点持有人数量={len(holders)}：{holders}")
            if is_empty(state):
                faults.append(f"道具{prop_id!r}缺状态")
        return faults
    if isinstance(value, list):
        seen: dict[str, str] = {}
        for row in value:
            if not isinstance(row, dict):
                faults.append("道具状态行不是对象")
                continue
            prop_id = str(row.get("prop_id") or "").strip()
            holder = str(row.get("holder") or "").strip()
            if not prop_id or not holder:
                faults.append(f"道具状态缺prop_id/holder：{row!r}")
            elif prop_id in seen and seen[prop_id] != holder:
                faults.append(f"道具{prop_id!r}同时属于{seen[prop_id]!r}和{holder!r}")
            else:
                seen[prop_id] = holder
        return faults
    if strict:
        faults.append(
            "R10-SPC-1道具状态必须使用prop_id/holder/state结构，不能使用自由文本"
        )
        return faults
    pairs = re.findall(r"([^@；;，,]+)@([^；;，,]+)", str(value))
    seen_text: dict[str, str] = {}
    for prop_id, holder in pairs:
        prop_id = prop_id.strip()
        holder = holder.strip()
        if prop_id in seen_text and seen_text[prop_id] != holder:
            faults.append(f"道具{prop_id!r}同时属于{seen_text[prop_id]!r}和{holder!r}")
        else:
            seen_text[prop_id] = holder
    return faults


def _action_capacity_seconds(picture_content: Any) -> tuple[float, list[str]]:
    text = str(picture_content or "")
    text = re.sub(r"[\u3400-\u9fffA-Za-z·]{1,20}说道[：:]?\s*[“\"][^”\"\n]+[”\"]", "", text)
    matched: list[str] = []
    seconds = 0.0
    for label, pattern, minimum in ACTION_DURATION_PATTERNS:
        if pattern.search(text):
            matched.append(label)
            seconds += minimum
    return seconds, matched


def _keyframe_forbidden_hits(value: Any) -> list[str]:
    return sorted({match.group(0) for match in KEYFRAME_FORBIDDEN_RE.finditer(str(value or ""))})


def _active_music_directive(value: Any) -> str:
    without_bans, _mentioned = _strip_negative_music_clauses(value)
    instruction_text, _protected = _protect_visual_text_fragments(without_bans)
    instruction_text = KEYFRAME_QUOTED_DIALOGUE_RE.sub("", instruction_text)
    instruction_text = KEYFRAME_LABELED_DIALOGUE_RE.sub("", instruction_text)
    match = ACTIVE_MUSIC_DIRECTIVE_RE.search(instruction_text)
    if match:
        return match.group(0)
    match = MUSIC_TERM_RE.search(instruction_text)
    return match.group(0) if match else ""


def _strip_negative_music_clauses(value: Any) -> tuple[str, bool]:
    text = str(value or "")
    mentioned = False
    parts = re.split(r"([；;。！？!?\n]+)", text)
    for index in range(0, len(parts), 2):
        original = parts[index]
        if not MUSIC_TERM_RE.search(original):
            continue
        negative_prefix = re.match(
            r"^\s*(?:禁止|不要|不得|不使用|不生成|无)(?:任何)?",
            original,
            flags=re.I,
        )
        clause, direct_count = NEGATIVE_MUSIC_CLAUSE_RE.subn("", original)
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
        if not MUSIC_TERM_RE.search(clause):
            parts[index] = clause
            continue
        if negative_prefix is None:
            parts[index] = clause
            continue
        if ACTIVE_MUSIC_DIRECTIVE_RE.search(clause):
            parts[index] = clause
            continue
        clause = MUSIC_TERM_RE.sub("", clause)
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


def _validate_protected_dialogue_alignment(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    protected_rows: list[dict[str, Any]] = []
    if isinstance(obj.get("protected_dialogue"), list):
        protected_rows.extend(row for row in obj["protected_dialogue"] if isinstance(row, dict))
    source_ingestion = obj.get("source_ingestion")
    if isinstance(source_ingestion, dict) and isinstance(source_ingestion.get("protected_dialogue"), list):
        protected_rows.extend(
            row
            for row in source_ingestion["protected_dialogue"]
            if isinstance(row, dict)
            and (strict or row.get("mapped_shot_uids") or row.get("target_shots"))
        )
    if not protected_rows:
        return []

    by_key: dict[str, dict[str, Any]] = {}
    for shot in shots:
        for key in (shot.get("镜头编号"), shot.get("shot_uid")):
            if key not in (None, ""):
                by_key[str(key)] = shot
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(protected_rows, 1):
        speaker = str(row.get("speaker") or "").strip()
        text = str(row.get("text") or "").strip()
        targets = row.get("mapped_shot_uids") or row.get("target_shots")
        if isinstance(targets, list) and targets:
            target_shots = [by_key[str(key)] for key in targets if str(key) in by_key]
            unknown = [str(key) for key in targets if str(key) not in by_key]
        else:
            target_shots = shots
            unknown = []
        location = f"protected_dialogue[{index}]"
        if unknown:
            issues.append(_issue(
                catalog,
                "E-SOURCE-SELECT-001",
                location,
                f"保护对白映射到不存在的镜头：{unknown}",
                template_gate_id="THG-05",
            ))
            continue
        actual = [item for shot in target_shots for item in extract_dialogues(shot.get("画面内容"))]
        if (speaker, text) in actual:
            continue
        same_text_speakers = sorted({actual_speaker for actual_speaker, actual_text in actual if actual_text == text})
        same_speaker_texts = [actual_text for actual_speaker, actual_text in actual if actual_speaker == speaker]
        if same_text_speakers:
            evidence = f"保护对白说话人={speaker!r}，实际说话人={same_text_speakers}，文本与标点={text!r}"
        elif same_speaker_texts:
            evidence = f"保护对白必须逐字逐标点为{text!r}，{speaker!r}实际对白={same_speaker_texts}"
        else:
            evidence = f"保护对白未覆盖：speaker={speaker!r}, text={text!r}, 实际={actual}"
        issues.append(_issue(
            catalog,
            "E-SOURCE-SELECT-001",
            location,
            evidence,
            template_gate_id="THG-05",
        ))
    return issues


def _missing_fields(obj: Any, fields: list[str]) -> list[str]:
    if not isinstance(obj, dict):
        return list(fields)
    return [field for field in fields if field not in obj or is_empty(obj.get(field))]


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matched_pattern_names(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    return {name for name, pattern in patterns.items() if pattern.search(text)}


def _public_density_class(text: str) -> str | None:
    if re.search(r"稀疏|低密度|少量|两三|三五|零散", text):
        return "low"
    if re.search(r"中等密度|中等人流|适中人流", text):
        return "medium"
    if re.search(r"密集|高密度|拥挤|人潮", text):
        return "high"
    return None


def _validate_public_scene_ecology(
    shots: list[dict[str, Any]],
    _batch_by_id: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate ordinary-person and ambience ecology in public locations.

    The checks intentionally operate on concrete source, layer, action and
    continuity evidence.  A generic ``人群`` or ``环境声`` placeholder cannot
    pass, while an explicitly motivated empty public space can be exempted.
    """
    issues: list[dict[str, Any]] = []
    metrics = {
        "public_scene_shot_count": 0,
        "public_scene_exemption_count": 0,
        "public_scene_visual_pass_count": 0,
        "public_scene_audio_pass_count": 0,
        "public_scene_master_pass_count": 0,
    }
    scene_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for index, shot in enumerate(shots, 1):
        location = f"shots[{index}]"
        visual_text = " ".join(str(shot.get(field, "")) for field in (
            "可见主体", "空间站位描述", "起始状态", "画面内容", "环境动态", "生成限制"
        ))
        scene_text = visual_text
        if not PUBLIC_SCENE_RE.search(scene_text):
            continue

        metrics["public_scene_shot_count"] += 1
        empty_declared = bool(PUBLIC_EMPTY_RE.search(scene_text))
        has_empty_cause = bool(PUBLIC_EXEMPTION_CAUSE_RE.search(scene_text))
        exempt = empty_declared and has_empty_cause
        if exempt:
            metrics["public_scene_exemption_count"] += 1
            continue
        if empty_declared and not has_empty_cause:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-ECOLOGY-001",
                f"{location}.画面内容/环境动态",
                "公共场景声明为空场，但没有宵禁、闭市、封锁、疏散、剧情要求等可核验原因。",
            ))

        density_ok = bool(PUBLIC_DENSITY_RE.search(visual_text))
        role_ok = bool(PUBLIC_ROLE_RE.search(visual_text))
        action_ok = bool(PUBLIC_ACTION_RE.search(visual_text))
        visual_layers = _matched_pattern_names(visual_text, PUBLIC_VISUAL_LAYER_PATTERNS)
        wide = bool(PUBLIC_WIDE_RE.search(f"{shot.get('镜头类型', '')} {shot.get('功能类型', '')}"))
        required_visual_layers = 2 if wide else 1
        forbidden_extras = PUBLIC_FORBIDDEN_EXTRAS_RE.search(str(shot.get("生成限制", "")))

        if not role_ok or forbidden_extras:
            evidence = []
            if not role_ok:
                evidence.append("没有与地点/时代匹配的普通人物角色")
            if forbidden_extras:
                evidence.append(f"生成限制排除了公共人物：{forbidden_extras.group(0)!r}")
            issues.append(_issue(
                catalog,
                "E-PUBLIC-ECOLOGY-001",
                f"{location}.画面内容/环境动态/生成限制",
                "；".join(evidence),
            ))

        missing_visual = []
        if not density_ok:
            missing_visual.append("人流密度")
        if not action_ok:
            missing_visual.append("错峰生活动作")
        if len(visual_layers) < required_visual_layers:
            missing_visual.append(f"空间层次（需{required_visual_layers}层，现有{sorted(visual_layers)}）")
        if missing_visual:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-CROWD-001",
                f"{location}.空间站位描述/画面内容/环境动态",
                f"公共人物生态缺少：{missing_visual}；当前={visual_text!r}",
            ))
        elif role_ok and not forbidden_extras:
            metrics["public_scene_visual_pass_count"] += 1

        audio_text = str(shot.get("环境音效", ""))
        audio_families = _matched_pattern_names(audio_text, PUBLIC_AUDIO_FAMILY_PATTERNS)
        audio_layers = _matched_pattern_names(audio_text, PUBLIC_AUDIO_LAYER_PATTERNS)
        required_audio_count = 3 if wide else 2
        dialogues = extract_dialogues(shot.get("画面内容"))
        audio_missing = []
        if is_none_value(audio_text):
            audio_missing.append("可听环境声")
        if len(audio_families) < required_audio_count:
            audio_missing.append(f"声源族（需{required_audio_count}类，现有{sorted(audio_families)}）")
        if len(audio_layers) < required_audio_count:
            audio_missing.append(f"声场层次（需{required_audio_count}层，现有{sorted(audio_layers)}）")
        if dialogues and not PUBLIC_DIALOGUE_DUCK_RE.search(f"{audio_text} {shot.get('生成限制', '')}"):
            audio_missing.append("对白避让/ducking")
        if audio_missing:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-AUDIO-001",
                f"{location}.环境音效",
                f"公共场景环境音缺少：{audio_missing}；当前={audio_text!r}",
            ))
        else:
            metrics["public_scene_audio_pass_count"] += 1

        av_faults = []
        modern = PUBLIC_MODERN_AUDIO_RE.search(audio_text)
        if modern:
            av_faults.append(f"古装/非现代公共场景出现现代声源{modern.group(0)!r}")
        anonymous_line = PUBLIC_ANONYMOUS_CLEAR_SPEECH_RE.search(audio_text)
        if anonymous_line:
            av_faults.append(f"匿名群演被分配清晰完整台词{anonymous_line.group(0)!r}")
        offscreen_cue = bool(re.search(r"画外|远处|门外|街另一端|巷口|城门方向", audio_text))
        for sound_pattern, source_pattern in PUBLIC_SOURCE_PAIRS:
            sound_hit = sound_pattern.search(audio_text)
            if sound_hit and not source_pattern.search(visual_text) and not offscreen_cue:
                av_faults.append(f"声源{sound_hit.group(0)!r}在画内不可见，也未标为可信画外声")

        if av_faults:
            issues.append(_issue(
                catalog,
                "E-PUBLIC-AVSYNC-001",
                location,
                "；".join(av_faults),
            ))
        else:
            metrics["public_scene_master_pass_count"] += 1

        scene_id = str(shot.get("场景连续性ID", ""))
        if scene_id:
            scene_rows[scene_id].append({
                "index": index,
                "shot_id": str(shot.get("镜头编号", "")),
                "density": _public_density_class(visual_text),
                "text": f"{visual_text} {audio_text}",
            })

    for scene_id, rows in scene_rows.items():
        for previous, current in zip(rows, rows[1:]):
            if (
                previous["density"]
                and current["density"]
                and previous["density"] != current["density"]
                and not PUBLIC_TRANSITION_RE.search(f"{previous['text']} {current['text']}")
            ):
                issues.append(_issue(
                    catalog,
                    "E-PUBLIC-CONT-001",
                    f"scene:{scene_id}:{previous['shot_id']}->{current['shot_id']}",
                    f"同场连续镜人流密度从{previous['density']}跳到{current['density']}，无涌入、散开、疏散或离开等画面过渡。",
                ))

    return issues, metrics


def _validate_chapter_intake(
    obj: dict[str, Any],
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the conditional novel/chapter-to-storyboard planning gate."""
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "chapter_intake_active": False,
        "chapter_complete_read": None,
        "chapter_core_event_coverage_percent": None,
        "chapter_causal_link_coverage_percent": None,
        "chapter_character_state_change_coverage_percent": None,
        "chapter_protected_dialogue_coverage_percent": None,
        "chapter_source_treatment_percent": None,
    }
    intake = obj.get("source_ingestion")
    if not isinstance(intake, dict) or intake.get("mode") != "chapter_to_storyboard":
        return issues, metrics

    metrics["chapter_intake_active"] = True
    policy = rules.get("modules", {}).get("CHAPTER_INTAKE", {})
    required_fields = policy.get("required_fields", [])
    missing = [field for field in required_fields if field not in intake]
    if missing:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion",
            f"章节前置规划缺字段：{missing}",
        ))

    chunk_count = int(_as_number(intake.get("chunk_count"), 0))
    chunks_read = int(_as_number(intake.get("chunks_read"), 0))
    complete_read = intake.get("complete_read") is True
    metrics["chapter_complete_read"] = complete_read and chunk_count >= 1 and chunks_read == chunk_count
    if not metrics["chapter_complete_read"]:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.complete_read",
            f"complete_read={intake.get('complete_read')!r}; chunk_count={chunk_count}; chunks_read={chunks_read}",
        ))

    boundary = intake.get("boundary_context")
    boundary_missing = _missing_fields(boundary, policy.get("boundary_fields", []))
    if boundary_missing:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.boundary_context",
            f"章节边界缺字段：{boundary_missing}",
        ))
    if is_empty(intake.get("chapter_id")) or is_empty(intake.get("chapter_goal")):
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.chapter_id/chapter_goal",
            "chapter_id或chapter_goal为空。",
        ))
    if intake.get("uncertainty_status") not in {"clear", "resolved"}:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-INTAKE-001",
            "source_ingestion.uncertainty_status",
            f"仍有未解决歧义：{intake.get('uncertainty_status')!r}",
        ))

    events = intake.get("events")
    event_ids: set[str] = set()
    event_source_unit_ids: set[str] = set()
    if not isinstance(events, list) or not events:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.events", "章节核心事件为空。"))
        events = []
    for index, event in enumerate(events, 1):
        location = f"source_ingestion.events[{index}]"
        if not isinstance(event, dict):
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, "事件不是对象。"))
            continue
        event_missing = _missing_fields(event, ["event_id", "source_unit_ids", "value_grade", "causal_role", "target_beats"])
        if event_missing or not isinstance(event.get("source_unit_ids"), list) or not isinstance(event.get("target_beats"), list):
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, f"事件字段缺失或类型错误：{event_missing}"))
            continue
        event_id = str(event.get("event_id"))
        if event_id in event_ids:
            issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", location, f"重复event_id：{event_id}"))
        event_ids.add(event_id)
        event_source_unit_ids.update(str(x) for x in event.get("source_unit_ids", []))

    causal_links = intake.get("causal_links")
    if not isinstance(causal_links, list) or not causal_links:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.causal_links", "章节因果链为空。"))
        causal_links = []
    for index, link in enumerate(causal_links, 1):
        missing_link = _missing_fields(link, ["from_event_id", "to_event_id", "relation"])
        unknown = [
            str(link.get(field))
            for field in ("from_event_id", "to_event_id")
            if not is_empty(link.get(field)) and str(link.get(field)) not in event_ids
        ] if isinstance(link, dict) else []
        if missing_link or unknown:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-INTAKE-001",
                f"source_ingestion.causal_links[{index}]",
                f"因果字段缺失={missing_link}; 未知事件={unknown}",
            ))

    state_changes = intake.get("character_state_changes")
    state_required = [
        "character", "start_state", "trigger_event_ids", "end_state",
        "cognition_before", "cognition_after", "forbidden_knowledge",
    ]
    if not isinstance(state_changes, list) or not state_changes:
        issues.append(_issue(catalog, "E-CHAPTER-INTAKE-001", "source_ingestion.character_state_changes", "人物状态变化为空。"))
        state_changes = []
    for index, row in enumerate(state_changes, 1):
        row_missing = _missing_fields(row, state_required)
        triggers = row.get("trigger_event_ids") if isinstance(row, dict) else None
        unknown = [str(x) for x in triggers or [] if str(x) not in event_ids] if isinstance(triggers, list) else []
        if row_missing or not isinstance(triggers, list) or not triggers or unknown:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-INTAKE-001",
                f"source_ingestion.character_state_changes[{index}]",
                f"人物变化缺字段={row_missing}; 未知触发事件={unknown}",
            ))

    beat_plan = intake.get("beat_plan")
    beat_policy = rules.get("modules", {}).get("ADAPTATION_BUDGET", {})
    beat_required = beat_policy.get("beat_required_fields", [])
    beat_ids: set[str] = set()
    covered_events: set[str] = set()
    beat_source_unit_ids: set[str] = set()
    if not isinstance(beat_plan, list) or not beat_plan:
        issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.beat_plan", "节拍计划为空。"))
        beat_plan = []
    for index, beat in enumerate(beat_plan, 1):
        location = f"source_ingestion.beat_plan[{index}]"
        if not isinstance(beat, dict):
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍不是对象。"))
            continue
        beat_missing = _missing_fields(beat, beat_required)
        if beat_missing:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"节拍缺字段：{beat_missing}"))
            continue
        beat_id = str(beat.get("beat_id"))
        beat_ids.add(beat_id)
        beat_events = beat.get("event_ids")
        source_units = beat.get("source_unit_ids")
        shot_range = beat.get("shot_count_range")
        if not isinstance(beat_events, list) or not beat_events or not isinstance(source_units, list) or not source_units:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍必须映射事件和源文单元。"))
        else:
            unknown = [str(x) for x in beat_events if str(x) not in event_ids]
            if unknown:
                issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"节拍引用未知事件：{unknown}"))
            covered_events.update(str(x) for x in beat_events if str(x) in event_ids)
            beat_source_unit_ids.update(str(x) for x in source_units)
        if (
            not isinstance(shot_range, list)
            or len(shot_range) != 2
            or _as_number(shot_range[0], 0) < 1
            or _as_number(shot_range[1], 0) < _as_number(shot_range[0], 0)
        ):
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, f"shot_count_range非法：{shot_range!r}"))
        if _as_number(beat.get("target_duration_seconds"), 0) <= 0:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", location, "节拍目标时长必须大于0。"))
    missing_event_beats = sorted(event_ids - covered_events)
    if missing_event_beats:
        issues.append(_issue(
            catalog,
            "E-CHAPTER-BUDGET-001",
            "source_ingestion.beat_plan",
            f"核心事件未进入节拍：{missing_event_beats}",
        ))
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            continue
        unknown_beats = [str(x) for x in event.get("target_beats", []) if str(x) not in beat_ids]
        if unknown_beats:
            issues.append(_issue(
                catalog,
                "E-CHAPTER-BUDGET-001",
                f"source_ingestion.events[{index}].target_beats",
                f"事件目标节拍不存在：{unknown_beats}",
            ))

    selection = rules.get("modules", {}).get("SOURCE_SELECTION", {})
    value_axes = selection.get("value_axes", [])
    allowed_grades = set(selection.get("allowed_grades", []))
    allowed_treatments = set(selection.get("allowed_treatments", []))
    treatment_rows = intake.get("source_treatment_ledger")
    treatment_unit_ids: set[str] = set()
    if not isinstance(treatment_rows, list) or not treatment_rows:
        issues.append(_issue(catalog, "E-SOURCE-SELECT-001", "source_ingestion.source_treatment_ledger", "源文取舍台账为空。"))
        treatment_rows = []
    for index, row in enumerate(treatment_rows, 1):
        location = f"source_ingestion.source_treatment_ledger[{index}]"
        if not isinstance(row, dict):
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "取舍台账行不是对象。"))
            continue
        row_missing = _missing_fields(row, [
            "source_unit_id", "source_type", "source_text", "value_axes",
            "value_grade", "cinematic_function", "treatment", "reason",
        ])
        if row_missing:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"取舍台账缺字段：{row_missing}"))
            continue
        unit_id = str(row.get("source_unit_id"))
        if unit_id in treatment_unit_ids:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"重复source_unit_id：{unit_id}"))
        treatment_unit_ids.add(unit_id)
        axes = row.get("value_axes")
        missing_axes = [axis for axis in value_axes if not isinstance(axes, dict) or axis not in axes]
        if missing_axes:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"多轴价值判断缺失：{missing_axes}"))
        grade = str(row.get("value_grade"))
        treatment = str(row.get("treatment"))
        if grade not in allowed_grades or treatment not in allowed_treatments:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"非法等级/处理：{grade!r}/{treatment!r}"))
        targets = row.get("target_beats")
        if treatment == "删除":
            has_authorization = not is_none_value(row.get("authorization"))
            has_redundancy = not is_none_value(row.get("redundancy_evidence"))
            if not (has_authorization or has_redundancy):
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "删除缺授权或冗余证据。"))
            if not is_none_value(row.get("cinematic_function")) and is_none_value(row.get("retained_function_in")):
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, "删除内容承担电影功能但未说明由哪个节拍承接。"))
        elif not isinstance(targets, list) or not targets:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"处理方式{treatment!r}缺目标节拍。"))
        else:
            unknown = [str(x) for x in targets if str(x) not in beat_ids]
            if unknown:
                issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"目标节拍不存在：{unknown}"))
    missing_treatment_units = sorted((event_source_unit_ids | beat_source_unit_ids) - treatment_unit_ids)
    if missing_treatment_units:
        issues.append(_issue(
            catalog,
            "E-SOURCE-SELECT-001",
            "source_ingestion.source_treatment_ledger",
            f"事件或节拍引用的源文单元未进入取舍台账：{missing_treatment_units}",
        ))

    protected = intake.get("protected_dialogue")
    if not isinstance(protected, list):
        issues.append(_issue(catalog, "E-SOURCE-SELECT-001", "source_ingestion.protected_dialogue", "protected_dialogue必须是数组。"))
        protected = []
    protected_required_seconds = 0.0
    for index, row in enumerate(protected, 1):
        location = f"source_ingestion.protected_dialogue[{index}]"
        row_missing = _missing_fields(row, ["source_unit_id", "speaker", "text", "functions", "target_beats"])
        targets = row.get("target_beats") if isinstance(row, dict) else None
        functions = row.get("functions") if isinstance(row, dict) else None
        unknown = [str(x) for x in targets or [] if str(x) not in beat_ids] if isinstance(targets, list) else []
        if row_missing or not isinstance(functions, list) or not functions or not isinstance(targets, list) or not targets or unknown:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"对白保护字段缺失={row_missing}; 未知节拍={unknown}"))
            continue
        if str(row.get("source_unit_id")) not in treatment_unit_ids:
            issues.append(_issue(catalog, "E-SOURCE-SELECT-001", location, f"保护对白源文单元未进入取舍台账：{row.get('source_unit_id')!r}"))
        dialogue_text = str(row.get("text"))
        protected_required_seconds += cjk_len(dialogue_text) / 2.8 + _pause_budget(dialogue_text)

    budget = intake.get("adaptation_budget")
    budget_required = beat_policy.get("required_budget_fields", [])
    budget_missing = _missing_fields(budget, budget_required)
    if budget_missing:
        issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"改编预算缺字段：{budget_missing}"))
    else:
        target = _as_number(budget.get("target_duration_seconds"), 0)
        dialogue_seconds = _as_number(budget.get("dialogue_seconds"), -1)
        action_seconds = _as_number(budget.get("action_seconds"), -1)
        transition_seconds = _as_number(budget.get("transition_seconds"), -1)
        shot_min = int(_as_number(budget.get("shot_count_min"), 0))
        shot_max = int(_as_number(budget.get("shot_count_max"), 0))
        component_sum = dialogue_seconds + action_seconds + transition_seconds
        if target <= 0 or min(dialogue_seconds, action_seconds, transition_seconds) < 0 or component_sum > target + 1e-9:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"目标={target:g}s，最低组成={component_sum:g}s，不可行。"))
        if dialogue_seconds + 1e-9 < protected_required_seconds:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget.dialogue_seconds", f"保护对白至少需{protected_required_seconds:.2f}s，预算仅{dialogue_seconds:.2f}s。"))
        if shot_min < 1 or shot_max < shot_min:
            issues.append(_issue(catalog, "E-CHAPTER-BUDGET-001", "source_ingestion.adaptation_budget", f"镜头数范围非法：{shot_min}-{shot_max}"))

    visual_license = intake.get("visualization_license")
    if not isinstance(visual_license, list):
        issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", "source_ingestion.visualization_license", "visualization_license必须是数组。"))
        visual_license = []
    for index, row in enumerate(visual_license, 1):
        location = f"source_ingestion.visualization_license[{index}]"
        if not isinstance(row, dict):
            issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", location, "视觉化许可行不是对象。"))
            continue
        row_missing = _missing_fields(row, ["description", "source_unit_ids", "category", "plot_change"])
        if row_missing or not isinstance(row.get("source_unit_ids"), list) or not row.get("source_unit_ids") or row.get("category") not in {"allowed", "conditional"} or row.get("plot_change") is not False:
            issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", location, f"视觉化许可不合规：missing={row_missing}, row={row!r}"))
    new_plot_events = intake.get("new_plot_events")
    if not isinstance(new_plot_events, list) or new_plot_events:
        issues.append(_issue(catalog, "E-SOURCE-EXPANSION-001", "source_ingestion.new_plot_events", f"新增原文外剧情：{new_plot_events!r}"))

    coverage = intake.get("coverage_metrics")
    coverage_fields = policy.get("coverage_fields", [])
    threshold = _as_number(policy.get("coverage_required_percent"), 100)
    for field in coverage_fields:
        value = _as_number(coverage.get(field), -1) if isinstance(coverage, dict) else -1
        metrics[f"chapter_{field}"] = value
        if value != threshold:
            code = "E-SOURCE-SELECT-001" if field in {"protected_dialogue_coverage_percent", "source_treatment_percent"} else "E-CHAPTER-INTAKE-001"
            issues.append(_issue(catalog, code, f"source_ingestion.coverage_metrics.{field}", f"覆盖率必须为{threshold:g}%，当前{value:g}%。"))

    return issues, metrics


def _validate_preventive_gates(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    mode: str,
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "profile": obj.get("workflow_profile"),
        "source_mapping_percent": 0.0,
        "old_content_hit_count": 0,
        "cross_context_term_count": 0,
        "first_repair_effectiveness_percent": None,
        "residual_rate_percent": None,
        "side_effect_rate_percent": None,
    }
    policy = rules.get("preventive_gate_policy", {})
    strict_contract = isinstance(obj.get("source_lock"), dict) and obj["source_lock"].get("output_contract") == STRICT_OUTPUT_CONTRACT
    expected_profile = policy.get("profile", WORKFLOW_PROFILE)
    required = mode in {"audit", "repair", "archive"}
    evidence = obj.get("prevention_evidence")
    if obj.get("workflow_profile") != expected_profile or not isinstance(evidence, dict):
        if required:
            issues.append(_issue(
                catalog,
                "E-GATE-001",
                "workflow_profile/prevention_evidence",
                f"expected profile={expected_profile!r}; got {obj.get('workflow_profile')!r}; evidence_type={type(evidence).__name__}",
            ))
        return issues, metrics

    required_blocks = policy.get("required_evidence_blocks", [])
    missing_blocks = [name for name in required_blocks if name not in evidence]
    if missing_blocks:
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence", f"缺少证据块：{missing_blocks}"))

    scope_lock = evidence.get("scope_lock")
    scope_fields = ["authoritative_source", "task_scope", "protected_scope", "active_rule_revision"]
    scope_missing = _missing_fields(scope_lock, scope_fields)
    if scope_missing or (isinstance(scope_lock, dict) and scope_lock.get("active_rule_revision") != expected_profile):
        issues.append(_issue(
            catalog,
            "E-GATE-001",
            "prevention_evidence.scope_lock",
            f"范围锁缺失={scope_missing}; revision={scope_lock.get('active_rule_revision') if isinstance(scope_lock, dict) else None!r}",
        ))

    shot_ids = {str(shot.get("镜头编号")) for shot in shots}
    story_blob = json.dumps({"shots": shots}, ensure_ascii=False)
    ledger = evidence.get("source_ledger")
    covered_shots: set[str] = set()
    allowed_treatments = {"保留", "合并", "压缩", "授权删除", "借用"}
    if not isinstance(ledger, list) or not ledger:
        issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.source_ledger", "源文覆盖台账缺失或为空。"))
    else:
        for index, row in enumerate(ledger, 1):
            location = f"prevention_evidence.source_ledger[{index}]"
            missing = _missing_fields(row, ["source_id", "source_type", "source_text", "speaker", "treatment", "target_shots", "authorization"])
            if missing:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"台账缺字段：{missing}"))
                continue
            treatment = str(row.get("treatment"))
            targets = row.get("target_shots")
            if treatment not in allowed_treatments:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"非法处理方式：{treatment!r}"))
            if treatment == "授权删除":
                if is_none_value(row.get("authorization")):
                    issues.append(_issue(catalog, "E-SOURCE-005", location, "授权删除缺用户授权证据。"))
            elif not isinstance(targets, list) or not targets:
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"处理方式{treatment!r}缺目标镜头。"))
            else:
                unknown = [str(x) for x in targets if str(x) not in shot_ids]
                if unknown:
                    issues.append(_issue(catalog, "E-SOURCE-005", location, f"目标镜头不存在：{unknown}"))
                covered_shots.update(str(x) for x in targets if str(x) in shot_ids)
        missing_shot_coverage = sorted(shot_ids - covered_shots)
        if missing_shot_coverage:
            issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.source_ledger", f"未进入源文台账的镜头：{missing_shot_coverage}"))
        metrics["source_mapping_percent"] = round(100.0 * len(covered_shots) / max(len(shot_ids), 1), 2)

    change_lock = evidence.get("change_lock")
    if not isinstance(change_lock, dict) or not isinstance(change_lock.get("must_include"), list) or not isinstance(change_lock.get("must_exclude"), list):
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.change_lock", "change_lock必须包含must_include与must_exclude数组。"))
    else:
        missing_required = [str(term) for term in change_lock["must_include"] if str(term).strip() and str(term) not in story_blob]
        if missing_required:
            issues.append(_issue(catalog, "E-SOURCE-005", "prevention_evidence.change_lock.must_include", f"必含内容未进入正式镜头：{missing_required}"))
        residue = [str(term) for term in change_lock["must_exclude"] if str(term).strip() and str(term) in story_blob]
        if residue:
            metrics["old_content_hit_count"] = len(residue)
            issues.append(_issue(catalog, "E-CHANGELOCK-001", "prevention_evidence.change_lock.must_exclude", f"禁止残留内容仍命中：{residue}"))

    cognition = evidence.get("cognition_matrix")
    cognition_fields = ["character", "knows", "does_not_know", "relationship_stage", "emotion_stage", "forbidden"]
    if not isinstance(cognition, list) or not cognition:
        issues.append(_issue(catalog, "E-COGNITION-001", "prevention_evidence.cognition_matrix", "人物认知矩阵缺失或为空。"))
    else:
        for index, row in enumerate(cognition, 1):
            missing = _missing_fields(row, cognition_fields)
            if missing or not isinstance(row.get("knows"), list) or not isinstance(row.get("does_not_know"), list) or not isinstance(row.get("forbidden"), list):
                issues.append(_issue(catalog, "E-COGNITION-001", f"prevention_evidence.cognition_matrix[{index}]", f"人物认知字段缺失或类型错误：{missing}"))

    state_fields = policy.get("state_vector_fields", [])
    state_rows = evidence.get("scene_state_ledger")
    state_by_shot: dict[str, dict[str, Any]] = {}
    if isinstance(state_rows, list):
        state_by_shot = {str(row.get("shot_id")): row for row in state_rows if isinstance(row, dict)}
    missing_state_shots = sorted(shot_ids - set(state_by_shot))
    if missing_state_shots:
        issues.append(_issue(catalog, "E-CONTINUITY-001", "prevention_evidence.scene_state_ledger", f"缺场景状态的镜头：{missing_state_shots}"))
    for shot_id, row in state_by_shot.items():
        for label in ("start_state", "end_state"):
            missing = _missing_fields(row.get(label), state_fields)
            if missing:
                issues.append(_issue(catalog, "E-CONTINUITY-001", f"scene_state:{shot_id}.{label}", f"状态向量缺字段：{missing}"))
            prop_faults = _prop_state_faults(
                row.get(label, {}).get("手持道具")
                if isinstance(row.get(label), dict)
                else None,
                strict=strict_contract,
            )
            if prop_faults:
                issues.append(_issue(
                    catalog,
                    "E-CONTINUITY-001",
                    f"scene_state:{shot_id}.{label}.手持道具",
                    "；".join(prop_faults),
                    repair="同一道具同一时点只能有一个持有人，并明确状态；交接必须通过本镜start/end完成。",
                    template_gate_id="THG-09",
                ))
    previous: tuple[str, dict[str, Any]] | None = None
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        row = state_by_shot.get(shot_id)
        if row is None:
            previous = None
            continue
        scene_id = str(row.get("scene_id", shot.get("场景连续性ID", "")))
        if previous and previous[0] == scene_id:
            previous_row = previous[1]
            prior_end = previous_row.get("end_state", {})
            current_start = row.get("start_state", {})
            mismatches = {field: [prior_end.get(field), current_start.get(field)] for field in state_fields if prior_end.get(field) != current_start.get(field)}
            prop_mismatch = "手持道具" in mismatches
            if mismatches and (
                prop_mismatch
                or is_none_value(row.get("transition_explanation"))
            ):
                issues.append(_issue(
                    catalog,
                    "E-CONTINUITY-001",
                    f"scene:{scene_id}:{previous_row.get('shot_id')}->{shot_id}",
                    f"首尾状态不闭合：{mismatches}",
                    template_gate_id="THG-09" if prop_mismatch else None,
                ))
        previous = (scene_id, row)

    plans = evidence.get("shot_plan")
    plan_by_shot = {str(row.get("shot_id")): row for row in plans if isinstance(row, dict)} if isinstance(plans, list) else {}
    missing_plan_shots = sorted(shot_ids - set(plan_by_shot))
    if missing_plan_shots:
        issues.append(_issue(catalog, "E-SHOTLOAD-001", "prevention_evidence.shot_plan", f"缺镜头功能卡：{missing_plan_shots}"))
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        plan = plan_by_shot.get(shot_id)
        if not plan:
            continue
        missing = _missing_fields(plan, ["core_function", "viewpoint_count", "camera_position", "main_action_stage", "transition_function"])
        if missing or _as_number(plan.get("viewpoint_count"), 99) > 1:
            issues.append(_issue(catalog, "E-SHOTLOAD-001", f"shot_plan:{shot_id}", f"缺字段={missing}; viewpoint_count={plan.get('viewpoint_count')!r}"))
        if strict_contract:
            camera_count = int(_as_number(plan.get("main_camera_count"), 0))
            stage_count = int(_as_number(plan.get("main_action_stage_count"), 0))
            if camera_count != 1 or stage_count != 1:
                issues.append(_issue(
                    catalog,
                    "E-BATCH-004",
                    f"shot_plan:{shot_id}",
                    f"单镜必须恰好一个主机位和一个核心动作阶段；main_camera_count={camera_count}, main_action_stage_count={stage_count}",
                    severity="fail",
                    blocking=True,
                    template_gate_id="THG-07",
                ))
        if "特写" in str(shot.get("镜头类型", "")) or bool(plan.get("is_detail_shot")):
            if is_none_value(plan.get("detail_shot_function")):
                issues.append(_issue(catalog, "E-SHOTFRAG-001", f"shot_plan:{shot_id}", "特写未说明剧情、信息或情绪功能。", severity="warning"))

    chain_fields = policy.get("performance_chain_fields", [])
    chains = evidence.get("performance_chains")
    chain_by_shot = {str(row.get("shot_id")): row for row in chains if isinstance(row, dict)} if isinstance(chains, list) else {}
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        dialogue = " ".join(line for _, line in extract_dialogues(shot.get("画面内容")))
        core = str(shot.get("功能类型", ""))
        relevant = not is_none_value(dialogue) or _has_any(core, ["情绪", "关系", "冲突", "反应", "内心"])
        if not relevant:
            continue
        chain = chain_by_shot.get(shot_id)
        missing = _missing_fields(chain, chain_fields)
        if missing:
            issues.append(_issue(catalog, "E-PERFORMANCE-001", f"performance_chain:{shot_id}", f"演员表演链缺字段：{missing}"))

    expected_layers = policy.get("audit_layer_order", [])
    if evidence.get("audit_layers") != expected_layers:
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.audit_layers", f"分层审计顺序必须为：{expected_layers}"))

    audio = evidence.get("audio_workflow")
    audio_missing = _missing_fields(audio, ["seedance_bgm_policy", "bgm_cue_stage", "scene_emotional_key", "dialogue_ducking"])
    if audio_missing or (isinstance(audio, dict) and "禁止" not in str(audio.get("seedance_bgm_policy"))) or (isinstance(audio, dict) and not _has_any(str(audio.get("bgm_cue_stage")), ["画面锁定后", "后期", "独立"])):
        issues.append(_issue(catalog, "E-AUDIO-STAGE-001", "prevention_evidence.audio_workflow", f"音频分层缺失或不合规：missing={audio_missing}, audio={audio!r}"))

    forbidden_context_terms = sorted(set(policy.get("i2v_forbidden_context_terms", [])) | CROSS_SHOT_TERMS)
    independent_fields = ["空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "环境音效", "环境动态", "光影"]
    for index, shot in enumerate(shots, 1):
        location = f"shots[{index}]"
        missing = _missing_fields(shot, independent_fields)
        if missing:
            issues.append(_issue(catalog, "E-I2V-INDEP-002", location, f"紧凑母版无法空白上下文独立执行，缺字段：{missing}"))
        master_text = " ".join(str(shot.get(field, "")) for field in EXECUTION_SHOT_FIELDS)
        context_hits = [term for term in forbidden_context_terms if term in master_text]
        if context_hits:
            metrics["cross_context_term_count"] += len(context_hits)
            issues.append(_issue(
                catalog,
                "E-I2V-INDEP-001",
                location,
                f"跨上下文代指：{context_hits}",
                template_gate_id="THG-04",
            ))
    repair_history = evidence.get("repair_history")
    if mode == "repair" and (not isinstance(repair_history, list) or not repair_history):
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.repair_history", "repair模式必须记录至少一轮修正证据。"))
    if isinstance(repair_history, list):
        for index, row in enumerate(repair_history, 1):
            if not isinstance(row, dict):
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", "修正记录不是对象。"))
                continue
            missing = _missing_fields(row, ["round", "initial_issue_count", "residual_issue_count", "introduced_issue_count", "repeat_rounds", "action"])
            if missing:
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", f"修正记录缺字段：{missing}"))
                continue
            initial = _as_number(row.get("initial_issue_count"))
            residual = _as_number(row.get("residual_issue_count"))
            introduced = _as_number(row.get("introduced_issue_count"))
            resolved = max(initial - residual, 0.0)
            if initial > 0:
                metrics["first_repair_effectiveness_percent"] = round(100.0 * resolved / initial, 2)
                metrics["residual_rate_percent"] = round(100.0 * residual / initial, 2)
            metrics["side_effect_rate_percent"] = round(100.0 * introduced / max(resolved, 1.0), 2)
            if residual > 0:
                issues.append(_issue(catalog, "E-REGRESSION-001", f"repair_history[{index}]", f"初审={initial:g}, 修后残留={residual:g}"))
            if introduced > 0:
                issues.append(_issue(catalog, "E-REGRESSION-002", f"repair_history[{index}]", f"修正新增问题={introduced:g}"))
            repeat_rounds = int(_as_number(row.get("repeat_rounds")))
            action = str(row.get("action"))
            if repeat_rounds == 2 and action not in {"root_cause_repair", "rebuild_upstream"}:
                issues.append(_issue(catalog, "E-SYSTEMIC-001", f"repair_history[{index}]", f"连续2轮仍使用动作 {action!r}，必须执行根因修复。"))
            if repeat_rounds >= 3 and action != "rebuild_upstream":
                issues.append(_issue(catalog, "E-SYSTEMIC-001", f"repair_history[{index}]", f"连续{repeat_rounds}轮仍使用动作 {action!r}，必须回退上游重建。"))

    return issues, metrics


def validate_project(obj: dict[str, Any] | list[Any], mode: str = "audit", rules_path: str | None = None) -> dict[str, Any]:
    rules = load_runtime_rules(rules_path)
    catalog = _catalog_map(rules)
    issues: list[dict[str, Any]] = []
    if isinstance(obj, list):
        obj = {"shots": obj}
    if not isinstance(obj, dict):
        issues.append(_issue(catalog, "E-TEMPLATE-001", "root", "输入必须是JSON对象或镜头数组。"))
        obj = {}

    if obj.get("project_version") != VERSION:
        issues.append(_issue(catalog, "E-VERSION-001", "project_version", f"expected {VERSION}, got {obj.get('project_version')!r}"))
    if obj.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue(catalog, "E-VERSION-001", "schema_version", f"expected {SCHEMA_VERSION}, got {obj.get('schema_version')!r}"))

    unknown_root_fields = set(obj) - COMPACT_TOP_LEVEL_FIELDS
    duplicate_generation_fields = sorted(
        field
        for field in unknown_root_fields
        if field in FORBIDDEN_GENERATION_TOP_LEVEL_FIELDS or DERIVED_OR_CACHE_ROOT_RE.search(field)
    )
    if duplicate_generation_fields:
        issues.append(_issue(
            catalog,
            "E-DUPLICATE-GENERATION-001",
            "root",
            "R10.1禁止持久化批次、派生提示词或缓存顶层字段；"
            f"命中={duplicate_generation_fields}。旧版字段仅允许迁移层只读处理。",
        ))
    unregistered_root_fields = sorted(unknown_root_fields - set(duplicate_generation_fields))
    if unregistered_root_fields:
        issues.append(_issue(
            catalog,
            "E-TEMPLATE-001",
            "root",
            f"R10.1 canonical包含未登记顶层字段：{unregistered_root_fields}；仅允许Schema登记字段。",
        ))
    forbidden_extension_paths = _forbidden_extension_paths(obj.get("canonical_extension"))
    if forbidden_extension_paths:
        issues.append(_issue(
            catalog,
            "E-DUPLICATE-GENERATION-001",
            "canonical_extension",
            "canonical_extension不得嵌套Seedance/i2v/batch/derived/cache状态；"
            f"命中={forbidden_extension_paths}。",
        ))

    required_fields = COMPACT_SHOT_FIELDS
    declared_order = obj.get("field_order")
    if declared_order != required_fields:
        issues.append(_issue(catalog, "E-FIELDORDER-001", "field_order", "顶层field_order缺失或与R10.1紧凑字段顺序不一致。"))

    batches = []

    source_lock = obj.get("source_lock")
    source_lock_fields = ["上集终点", "本集起点", "本集计划终点", "下一集起点", "禁止重复", "禁止提前"]
    if not isinstance(source_lock, dict) or any(is_empty(source_lock.get(f)) for f in source_lock_fields):
        issues.append(_issue(catalog, "E-SOURCE-001", "source_lock", "Source Lock缺失或核心边界字段不完整。"))
    strict_contract = isinstance(source_lock, dict) and source_lock.get("output_contract") == STRICT_OUTPUT_CONTRACT
    if strict_contract:
        expected_contract_lock = {
            "output_contract": STRICT_OUTPUT_CONTRACT,
            "target_model": "Seedance 2.5",
            "aspect_ratio": "9:16",
        }
        for field, expected in expected_contract_lock.items():
            if source_lock.get(field) != expected:
                issues.append(_issue(
                    catalog,
                    "E-TEMPLATE-008",
                    f"source_lock.{field}",
                    f"R10-SPC-1契约锁必须为{expected!r}，当前={source_lock.get(field)!r}",
                ))

    raw_shots = obj.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        issues.append(_issue(catalog, "E-TEMPLATE-001", "shots", "没有可校验的镜头列表。"))
        raw_shots = []

    aliases = rules.get("field_aliases", {})
    motions = rules["modules"]["MOTION"]["catalog"]
    normalized_shots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    scene_groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    episode_running: dict[str, float] = defaultdict(float)
    strict_duration_sum = 0.0

    for index, raw in enumerate(raw_shots, 1):
        location = f"shots[{index}]"
        if not isinstance(raw, dict):
            issues.append(_issue(catalog, "E-TEMPLATE-001", location, "镜头不是对象。"))
            continue
        deprecated_hits = sorted(DEPRECATED_SHOT_FIELDS.intersection(raw))
        if deprecated_hits:
            issues.append(_issue(
                catalog,
                "E-DEPRECATED-FIELD-001",
                location,
                f"R10.1镜头仍含已删除字段：{deprecated_hits}",
            ))
        deprecated_label_hits = sorted({
            f"{field}:{label}"
            for field in DEPRECATED_LABEL_SCAN_FIELDS
            for label in DEPRECATED_DISPLAY_LABELS
            if label in str(raw.get(field, ""))
        })
        if deprecated_label_hits:
            issues.append(_issue(
                catalog,
                "E-DEPRECATED-FIELD-001",
                location,
                "R10.1展示/执行字段仍含退休子标签："
                f"{deprecated_label_hits}；原文证据字段未参与此扫描。",
            ))
        derived_hits = sorted(PERSISTED_DERIVED_FIELDS.intersection(raw))
        if derived_hits:
            issues.append(_issue(
                catalog,
                "E-DUPLICATE-GENERATION-001",
                location,
                f"canonical不得持久化按需编译字段：{derived_hits}",
            ))
        shot, used_aliases = normalize_shot(raw, aliases)
        normalized_shots.append(shot)
        for old, new in used_aliases:
            issues.append(_issue(catalog, "E-INTEGRATION-003", location, f"使用旧字段 {old!r}，已兼容映射到 {new!r}。", severity="warning"))
        for field in required_fields:
            if field not in shot or is_empty(shot.get(field)):
                issues.append(_issue(catalog, "E-TEMPLATE-008", f"{location}.{field}", f"缺字段或空值：{field}"))

        shot_id = str(shot.get("镜头编号", ""))
        if shot_id in seen_ids:
            issues.append(_issue(catalog, "E-TEMPLATE-008", f"{location}.镜头编号", f"重复镜头编号：{shot_id}"))
        seen_ids.add(shot_id)
        ep_match = EP_SHOT_ID_RE.fullmatch(shot_id)
        if strict_contract and not ep_match:
            issues.append(_issue(
                catalog,
                "E-TEMPLATE-008",
                f"{location}.镜头编号",
                f"R10-SPC-1显示编号必须符合EP##-###，当前={shot_id!r}",
            ))

        try:
            duration = float(shot.get("时长", 0))
        except (TypeError, ValueError):
            duration = 0.0
            issues.append(_issue(catalog, "E-STAT-001", f"{location}.时长", "时长不是数字。"))
        try:
            cumulative = float(shot.get("累计时长", 0))
        except (TypeError, ValueError):
            cumulative = 0.0
            issues.append(_issue(catalog, "E-STAT-001", f"{location}.累计时长", "累计时长不是数字。"))
        if abs(duration * 10 - round(duration * 10)) > 1e-8:
            issues.append(_issue(
                catalog,
                "E-STAT-001",
                f"{location}.时长",
                f"镜头时长必须使用0.1秒粒度，当前={duration:g}s",
                template_gate_id="THG-11",
            ))
        if abs(cumulative * 10 - round(cumulative * 10)) > 1e-8:
            issues.append(_issue(
                catalog,
                "E-STAT-001",
                f"{location}.累计时长",
                f"累计时长必须使用0.1秒粒度，当前={cumulative:g}s",
                template_gate_id="THG-11",
            ))
        if strict_contract:
            strict_duration_sum += duration
            if ep_match:
                episode_key = ep_match.group("episode")
                episode_running[episode_key] += duration
                expected_cumulative = episode_running[episode_key]
                if abs(cumulative - expected_cumulative) > 0.01:
                    issues.append(_issue(
                        catalog,
                        "E-STAT-001",
                        f"{location}.累计时长",
                        f"R10-SPC-1按单集累计应为{expected_cumulative:.1f}s，当前={cumulative:.1f}s",
                    ))

        shot_type = str(shot.get("镜头类型", ""))
        visible = str(shot.get("可见主体", ""))
        image_content = str(shot.get("画面内容", ""))
        combined_performance = image_content
        timeline_faults = _validate_timeline(image_content, duration, strict=strict_contract)
        if timeline_faults:
            issues.append(_issue(
                catalog,
                "E-TIMELINE-001",
                f"{location}.画面内容",
                "；".join(timeline_faults),
            ))
        for field in EXECUTION_SHOT_FIELDS:
            field_text = str(shot.get(field, ""))
            bgm_match = BGM_TOKEN_RE.search(field_text)
            active_music = _active_music_directive(field_text)
            if bgm_match or active_music:
                evidence = (
                    f"单镜执行字段禁止出现BGM字面量，命中字段={field!r}"
                    if bgm_match
                    else f"单镜执行字段禁止主动配乐指令：{active_music!r}"
                )
                issues.append(_issue(
                    catalog,
                    "E-AUDIO-STAGE-001",
                    f"{location}.{field}",
                    evidence,
                    repair="改写为‘禁止额外配乐’，音乐Cue仅在画面锁定后独立处理。",
                    template_gate_id="THG-AUDIO",
                ))
            elif (
                field == "生成限制"
                and MUSIC_TERM_RE.search(field_text)
                and "禁止额外配乐" not in field_text
            ):
                issues.append(_issue(
                    catalog,
                    "E-AUDIO-STAGE-001",
                    f"{location}.{field}",
                    "单镜负面音乐限制必须统一逐字写为‘禁止额外配乐’。",
                    repair="把禁止/不得/无BGM或背景音乐等写法统一为‘禁止额外配乐’。",
                    template_gate_id="THG-AUDIO",
                ))
        transition_faults = _transition_duplicate_faults(shot.get("切镜/转场提示词"))
        if transition_faults:
            issues.append(_issue(
                catalog,
                "E-DUPLICATE-GENERATION-001",
                f"{location}.切镜/转场提示词",
                "；".join(transition_faults),
                repair="删除空分隔符并仅保留一次确定性切镜短句。",
                template_gate_id="THG-02",
            ))
        if strict_contract:
            generation_limits = str(shot.get("生成限制", ""))
            aspect_occurrences = len(REQUIRED_ASPECT_RATIO_RE.findall(generation_limits))
            aspect_conflicts = sorted({
                match.group(0)
                for match in ANY_ASPECT_TOKEN_RE.finditer(generation_limits)
                if (
                    match.group("width") != "9"
                    or match.group("separator") not in {":", "："}
                    or match.group("height") != "16"
                )
            })
            aspect_faults = []
            if aspect_occurrences != 1:
                aspect_faults.append(
                    f"逐字短语{REQUIRED_ASPECT_RATIO_PHRASE!r}应恰好出现1次，当前={aspect_occurrences}"
                )
            if aspect_conflicts:
                aspect_faults.append(f"存在冲突画幅={aspect_conflicts}")
            screen_terms = [
                term
                for term in ("横屏", "方屏")
                if term in generation_limits
            ]
            if screen_terms:
                aspect_faults.append(f"出现冲突画面方向={screen_terms}")
            if aspect_faults:
                issues.append(_issue(
                    catalog,
                    "E-I2V-INDEP-002",
                    f"{location}.生成限制",
                    "；".join(aspect_faults),
                    repair=f"只保留一次{REQUIRED_ASPECT_RATIO_PHRASE}并删除横屏或其他比例。",
                    template_gate_id="THG-01",
                ))
            legacy_dialogue = LEGACY_DIALOGUE_TAG_RE.search(image_content)
            if legacy_dialogue:
                issues.append(_issue(
                    catalog,
                    "E-DEPRECATED-FIELD-001",
                    f"{location}.画面内容",
                    f"R10-SPC-1禁止旧对白标签：{legacy_dialogue.group(0)!r}；改为‘角色说道：“完整对白。”’。",
                ))
            prompt_text = " ".join(str(shot.get(field, "")) for field in DEPRECATED_LABEL_SCAN_FIELDS)
            forbidden_prompt = FORBIDDEN_PROMPT_PHRASE_RE.search(prompt_text)
            if forbidden_prompt:
                issues.append(_issue(
                    catalog,
                    "E-DEPRECATED-FIELD-001",
                    location,
                    f"R10-SPC-1提示词仍含已删除的冗长9:16固定短语：{forbidden_prompt.group(0)!r}",
                ))
            visible_characters = _visible_character_names(visible)
            if len(visible_characters) > 3:
                issues.append(_issue(
                    catalog,
                    "E-VIS-001",
                    f"{location}.可见主体",
                    f"单镜可识别主要人物最多3人，当前={len(visible_characters)}：{visible_characters}",
                    repair="拆镜或把非核心普通人物降为匿名背景生态。",
                    template_gate_id="THG-06",
                ))
            start_faults = _start_result_faults(shot.get("起始状态"), image_content)
            if start_faults:
                issues.append(_issue(
                    catalog,
                    "E-CONTINUITY-001",
                    f"{location}.起始状态/画面内容",
                    "；".join(start_faults),
                    repair="把起始状态回退到动作发生前，并将完成结果只保留在镜尾状态。",
                    template_gate_id="THG-08",
                ))
            if MULTI_CAMERA_RE.search(f"{shot.get('机位', '')} {shot.get('运镜方式', '')} {image_content}"):
                issues.append(_issue(
                    catalog,
                    "E-BATCH-004",
                    f"{location}.机位/运镜方式/画面内容",
                    "单镜出现第二机位、另一机位或隐形切机位。",
                    severity="fail",
                    blocking=True,
                    template_gate_id="THG-07",
                ))
            try:
                project_formal_master(shot)
            except ValueError as exc:
                message = str(exc)
                if "aspect_ratio" in message:
                    code, gate = "E-I2V-INDEP-002", "THG-01"
                elif "audio_contract" in message:
                    code, gate = "E-AUDIO-STAGE-001", "THG-AUDIO"
                elif "transition_contract" in message:
                    code, gate = "E-DUPLICATE-GENERATION-001", "THG-02"
                elif "timing_precision" in message:
                    code, gate = "E-STAT-001", "THG-11"
                else:
                    code, gate = "E-INTEGRATION-001", "THG-11"
                issues.append(_issue(
                    catalog,
                    code,
                    f"{location}.正式分镜母版",
                    f"确定性正式母版投影失败：{message}",
                    template_gate_id=gate,
                ))
            try:
                gpt_projection = project_gpt_keyframe(shot)
            except ValueError as exc:
                message = str(exc)
                if "aspect_ratio" in message:
                    code, gate = "E-I2V-INDEP-002", "THG-01"
                else:
                    code, gate = "E-JIMENG-TIMING-001", "THG-03"
                issues.append(_issue(
                    catalog,
                    code,
                    f"{location}.GPT关键帧生成指令",
                    f"确定性静态关键帧投影失败：{message}",
                    template_gate_id=gate,
                ))
                gpt_projection = ""
            keyframe_hits = _keyframe_forbidden_hits(gpt_projection)
            if keyframe_hits:
                issues.append(_issue(
                    catalog,
                    "E-JIMENG-TIMING-001",
                    f"{location}.GPT关键帧生成指令",
                    f"静态关键帧投影命中动态、时间、对白或声音指令：{keyframe_hits}",
                    repair="只从静态起始状态、静态机位、光影、锚点和9:16生成限制重投影。",
                    template_gate_id="THG-03",
                ))
        invisible_context = _has_any(f"{shot_type} {visible}", INVISIBLE_SHOT_TERMS) and not _has_any(visible, FACE_VISIBLE_TERMS)
        if invisible_context:
            bad = [term for term in FACE_TERMS if term in combined_performance]
            if bad:
                issues.append(_issue(catalog, "E-VIS-005", location, f"不可见镜头写入面部/眨眼细节：{sorted(set(bad))}"))

        categories = detail_categories(image_content)
        emotion_labels = [x for x in ABSTRACT_EMOTIONS if x in image_content]
        core_emotion = _has_any(str(shot.get("功能类型", "")), ["情绪", "关系", "冲突", "哭", "内心", "反应"]) or bool(emotion_labels)
        if emotion_labels and len(categories) < 2:
            issues.append(_issue(catalog, "E-EXPRESSION-001", f"{location}.画面内容", f"只有抽象情绪标签 {emotion_labels}，缺少成组的可观察表演细节；当前={categories}。"))
        elif core_emotion and len(categories) < 4:
            issues.append(_issue(catalog, "E-EXPRESSION-002", f"{location}.画面内容", f"重要情绪镜只有 {len(categories)} 类细节：{categories}", severity="warning"))

        if "眨眼" in combined_performance:
            blink_range = parse_blink_range(combined_performance)
            if "同步眨眼" in combined_performance or "同时眨眼" in combined_performance or "同频眨眼" in combined_performance:
                issues.append(_issue(catalog, "E-EXPRESSION-003", location, "双人镜头出现同步/同频眨眼。"))
            if blink_range:
                _, high = blink_range
                if 3 <= duration <= 5 and high > 1:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"{duration}s镜头眨眼上限应为1次，当前写到{high}次。"))
                elif 5 < duration <= 10 and high > 2:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"{duration}s镜头眨眼上限应为2次，当前写到{high}次。"))
                elif duration < 3 and high > 1:
                    issues.append(_issue(catalog, "E-EXPRESSION-003", location, f"短于3s镜头不应机械安排{high}次眨眼。"))

        camera = str(shot.get("机位", ""))
        if is_none_value(camera) or _has_any(camera, ["随便", "自然机位", "高级机位"]):
            issues.append(_issue(catalog, "E-CAMERA-001", f"{location}.机位", f"机位不可执行：{camera!r}"))

        motion_text = str(shot.get("运镜方式", ""))
        matched_motion = match_motion(motion_text, motions)
        is_static = _has_any(motion_text, STATIC_MOTIONS)
        if _has_any(motion_text, VAGUE_TERMS) or (not matched_motion and not is_static):
            issues.append(_issue(catalog, "E-MOTION-001", f"{location}.运镜方式", f"运镜不属于R10菜单或过于空泛：{motion_text!r}"))
        if matched_motion and not is_static:
            if not _motion_has_natural_subject(motion_text, visible):
                issues.append(_issue(
                    catalog,
                    "E-MOTION-002",
                    f"{location}.运镜方式",
                    "移动镜头的自然句未说明摄影机朝哪个人物、道具或空间运动。",
                ))
            if not _motion_has_narrative_purpose(motion_text, matched_motion):
                issues.append(_issue(
                    catalog,
                    "E-MOTION-003",
                    f"{location}.运镜方式",
                    "移动镜头的自然句未说明聚焦、揭示、强化或营造等叙事用途。",
                    severity="warning",
                ))

        light = str(shot.get("光影", ""))
        light_source = str(shot.get("光源来源", ""))
        light_direction = str(shot.get("光源方向", ""))
        color_temp = str(shot.get("色温倾向", ""))
        if any(is_none_value(value) for value in (light, light_source, light_direction, color_temp)) or (light.strip() in VAGUE_TERMS):
            issues.append(_issue(catalog, "E-LIGHT-001", f"{location}.光影", f"光影四项必须完整：光影={light!r}, 光源={light_source!r}, 方向={light_direction!r}, 色温={color_temp!r}"))
        if strict_contract:
            light_faults = []
            if not LIGHT_SUBJECT_RE.search(light):
                light_faults.append("光影未说明受光主体、面部/道具可读性或高光暗部材质")
            if not LIGHT_SOURCE_RE.search(light_source):
                light_faults.append("光源来源未写场内实际采用的有源主光或弱补光")
            if "或" in light_source or "或" in light_direction or re.search(r"视情况|可选|二选一", f"{light_source} {light_direction}"):
                light_faults.append("光源来源/方向含二选一或候选表述")
            if not LIGHT_DIRECTION_RE.search(light_direction) or not LIGHT_PATH_RE.search(light_direction):
                light_faults.append("光源方向未同时写明画面侧向/高度、落光路径或纵深衰减")
            if KELVIN_RE.search(color_temp):
                light_faults.append("色温倾向禁止Kelvin/K数值")
            if not COLOR_RELATION_RE.search(color_temp) or not COLOR_MATERIAL_RE.search(color_temp):
                light_faults.append("色温倾向必须写定性暖冷关系及空间/肤色/材质本色")
            if color_temp.strip() in {"暖中性", "中性微暖", "偏暖", "偏冷", "冷暖对比", "电影感"}:
                light_faults.append("色温倾向只有空泛色调标签")
            if light_faults:
                issues.append(_issue(
                    catalog,
                    "E-LIGHT-001",
                    f"{location}.光影/光源来源/光源方向/色温倾向",
                    "；".join(light_faults),
                ))
        if "底光" in f"{light} {light_source}" and _has_any(str(shot.get("功能类型", "")), ["甜宠", "温情", "浪漫", "关系"]):
            issues.append(_issue(catalog, "E-LIGHT-002", f"{location}.光影", "普通关系/浪漫镜使用底光，需改为有源且有剧情必要的光效。", severity="warning"))

        dialogue_rows = extract_dialogues(image_content)
        dialogue = "".join(line for _, line in dialogue_rows)
        if dialogue_rows:
            if is_none_value(shot.get("音色")):
                issues.append(_issue(catalog, "E-VOICE-001", f"{location}.音色", "带对白镜必须保留独立音色字段。"))
            delivery = f"{image_content} {shot.get('音色', '')}"
            dimensions = [name for name, pattern in VOICE_DIMENSION_PATTERNS.items() if pattern.search(delivery)]
            if strict_contract and len(dimensions) < 6:
                issues.append(_issue(catalog, "E-VOICE-001", f"{location}.画面内容/音色", f"R10-SPC-1对白发声只覆盖 {len(dimensions)}/6 项：{dimensions}"))
            elif core_emotion and len(dimensions) < 6:
                issues.append(_issue(catalog, "E-VOICE-001", f"{location}.画面内容", f"核心情绪对白的发声说明只覆盖 {len(dimensions)}/6 项：{dimensions}"))
            elif len(dimensions) < 4:
                issues.append(_issue(catalog, "E-VOICE-002", f"{location}.画面内容", f"普通对白的发声说明只覆盖 {len(dimensions)}/6 项：{dimensions}", severity="warning"))
            if re.search(r"标点(?:语气|效果|按[^：:]{0,30}表现)[：:]\s*(?:无|不适用)", image_content):
                issues.append(_issue(catalog, "E-PUNCT-001", f"{location}.画面内容", "带对白镜在画面内容中明确取消了标点语气。"))
            missing_punct = _validate_punctuation(dialogue, image_content)
            if missing_punct:
                issues.append(_issue(catalog, "E-PUNCT-004", f"{location}.画面内容", f"对白标点语气未说明：{missing_punct}"))
            min_duration = cjk_len(dialogue) / 2.8 + _pause_budget(dialogue)
            if duration + 1e-9 < min_duration:
                issues.append(_issue(catalog, "E-DIALOGUE-003", f"{location}.画面内容", f"{cjk_len(dialogue)}个对白汉字 + 停顿预算需至少{min_duration:.2f}s，镜头仅{duration:.2f}s。"))
            if strict_contract:
                if "口型与对白一致" not in image_content:
                    issues.append(_issue(catalog, "E-VOICE-001", f"{location}.画面内容", "R10-SPC-1可见对白未写“口型与对白一致”。"))
                if "不看镜头" not in image_content:
                    issues.append(_issue(catalog, "E-PERFORMANCE-001", f"{location}.画面内容", "R10-SPC-1对白人物未明确不看镜头。"))
                visible_people = [part for part in re.split(r"[、，,；;]", visible) if part.strip()]
                if len(visible_people) > 1 and not re.search(r"错开|晚约|随后|不抢反应|先.{0,20}再|依次|晚半拍|延迟", image_content):
                    issues.append(_issue(catalog, "E-PERFORMANCE-001", f"{location}.画面内容", "多人对白镜未写听者错峰反应。"))

        if strict_contract:
            action_seconds, action_units = _action_capacity_seconds(image_content)
            dialogue_seconds = cjk_len(dialogue) / 2.8 + _pause_budget(dialogue) if dialogue_rows else 0.0
            pre_speech_breath_seconds = 0.2 if dialogue_rows else 0.0
            listener_seconds = 0.3 if dialogue_rows and len(_visible_character_names(visible)) > 1 else 0.0
            end_hold_seconds = 0.2
            required_seconds = (
                dialogue_seconds
                + pre_speech_breath_seconds
                + action_seconds
                + listener_seconds
                + end_hold_seconds
            )
            if duration + 1e-9 < required_seconds:
                issues.append(_issue(
                    catalog,
                    "E-BATCH-003",
                    f"{location}.时长/画面内容",
                    "镜头容量不足："
                    f"对白={dialogue_seconds:.2f}s，说话前呼吸={pre_speech_breath_seconds:.2f}s，"
                    f"动作={action_seconds:.2f}s({action_units})，"
                    f"听者反应={listener_seconds:.2f}s，镜尾稳定={end_hold_seconds:.2f}s，"
                    f"最低={required_seconds:.2f}s，当前={duration:.2f}s。",
                    repair="延长镜头、删除非核心动作或按原子动作阶段拆镜。",
                    severity="fail",
                    blocking=True,
                    template_gate_id="THG-10",
                ))

        psych = str(shot.get("心理音轨", ""))
        if not is_none_value(psych):
            safety = f"{psych} {shot.get('生成限制', '')} {image_content}"
            required_psych = ["不张嘴", "不驱动口型", "不生成字幕", "不计入"]
            missing = [term for term in required_psych if term not in safety]
            if missing:
                issues.append(_issue(catalog, "E-PSYCH-001", location, f"心理音轨缺安全属性：{missing}"))

        scene_id = str(shot.get("场景连续性ID", ""))
        if scene_id:
            scene_groups[scene_id].append((index, shot))

    issues.extend(_validate_protected_dialogue_alignment(
        obj,
        normalized_shots,
        catalog,
        strict=strict_contract,
    ))

    for scene_id, items in scene_groups.items():
        if len(items) < 2:
            continue
        directions = {str(shot.get("光源方向")) for _, shot in items if not is_none_value(shot.get("光源方向"))}
        temperatures = {str(shot.get("色温倾向")) for _, shot in items if not is_none_value(shot.get("色温倾向"))}
        sources = {str(shot.get("光源来源")) for _, shot in items if not is_none_value(shot.get("光源来源"))}
        if len(directions) > 1 or len(temperatures) > 1 or len(sources) > 1:
            issues.append(_issue(catalog, "E-LIGHT-002", f"scene:{scene_id}", f"连续镜光线不一致：光源={sorted(sources)}, 方向={sorted(directions)}, 色温={sorted(temperatures)}", severity="warning"))

    batch_by_id: dict[str, dict[str, Any]] = {}

    if normalized_shots and obj.get("total_duration") is not None:
        try:
            expected_total = float(obj.get("total_duration"))
            actual_total = sum(_as_number(shot.get("时长")) for shot in normalized_shots)
            if abs(expected_total - actual_total) > 0.01:
                issues.append(_issue(catalog, "E-STAT-001", "total_duration", f"总时长{expected_total}与全部镜头时长之和{actual_total}不一致。"))
        except (TypeError, ValueError):
            issues.append(_issue(catalog, "E-STAT-001", "total_duration", "总时长不是数字。"))

    public_issues, public_metrics = _validate_public_scene_ecology(
        normalized_shots,
        batch_by_id,
        catalog,
    )
    issues.extend(public_issues)

    gate_issues, gate_metrics = _validate_preventive_gates(
        obj,
        normalized_shots,
        batches,
        mode,
        rules,
        catalog,
    )
    issues.extend(gate_issues)
    gate_metrics.update(public_metrics)
    chapter_issues, chapter_metrics = _validate_chapter_intake(obj, rules, catalog)
    issues.extend(chapter_issues)
    gate_metrics.update(chapter_metrics)

    if mode == "archive":
        stage_evidence = obj.get("stage_evidence")
        required_stages = ["STAGE-A", "STAGE-B", "STAGE-C", "STAGE-D"]
        if not isinstance(stage_evidence, dict) or any(not stage_evidence.get(stage) for stage in required_stages):
            issues.append(_issue(catalog, "E-BOOTSTRAP-001", "stage_evidence", "归档模式缺阶段A-D证据。"))
        review = obj.get("human_review")
        if not isinstance(review, dict) or review.get("最终归档") != "approved" or is_empty(review.get("签名")):
            issues.append(_issue(catalog, "E-ARCHIVE-001", "human_review", "归档模式缺approved状态或真实签名。"))

    fail_count = sum(1 for issue in issues if issue["severity"] == "fail")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    return {
        "validator_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "workflow_revision": rules.get("workflow_revision", WORKFLOW_PROFILE),
        "workflow_profile": obj.get("workflow_profile"),
        "mode": mode,
        "status": status,
        "shot_count": len(normalized_shots),
        "batch_count": len(batches),
        "error_count": fail_count,
        "warning_count": warning_count,
        "quality_metrics": gate_metrics,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json")
    parser.add_argument("--mode", choices=["draft", "audit", "repair", "archive"], default="audit")
    parser.add_argument("--rules", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    obj = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    report = validate_project(obj, mode=args.mode, rules_path=args.rules)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    return 0 if report["status"] == "pass" else 2 if report["status"] == "warning" else 1


if __name__ == "__main__":
    sys.exit(main())
