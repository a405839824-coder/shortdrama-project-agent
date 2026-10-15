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

import timing_model
from episode_rules import chapter_required, delivery_text, inspect_episodes, inspect_borrowing
from written_payload import context_instruction_text, protect_visual_text_fragments
import repair_observations
from field_content import (assert_field_content, field_content_findings,
                           voice_content_findings, preserved_voice_baseline, r5_keyframe_sections,
                           FORMAL_EXECUTION_FIELDS, project_lighting, music_directives, strip_negative_music)
from continuity_checks import state_equal, audit_adjacent_text
from performance_checks import eye_contract_faults, emphasis_binding_faults, visible_subject_annotation_faults, listener_reaction_faults
from lighting_checks import affirmative_matches, lighting_context_faults, scene_lighting_continuity_faults
from dialogue_occurrences import align_storyboard_dialogue
from voice_sequence import (VOICE_TEMPLATE_LABELS, LEGACY_DIALOGUE_TAG_RE, extract_dialogues)
from projection_semantics import (field_sha256, labelled_sections, tail_binding_faults,
                                  explicit_state_conflicts, environment_phase_findings,
                                  mask_static_eye_exclusions)
from bounded_normalization import normalize_music_prohibition
from source_content_limits import (
    AFFORDANCE as SOURCE_LIMIT_AFFORDANCE,
    ENTAILMENT as SOURCE_LIMIT_ENTAILMENT,
    TREATMENT as SOURCE_LIMIT_TREATMENT,
    inspect_source_content_limits,
    oral_communication_candidate_text,
)

VERSION = "V16.3-148-R10-StableRuntime"
SCHEMA_VERSION = "R10.1"
WORKFLOW_PROFILE = "R10-PG1"
STRICT_OUTPUT_CONTRACT = "R10-SPC-1"
ADAPTED_DIALOGUE_CONTRACT = "source-fact-dialogue-v1"

COMPACT_SHOT_FIELDS = [
    "镜头编号", "时长", "累计时长", "功能类型", "场景连续性ID", "镜头类型", "可见主体",
    "空间站位描述", "起始状态", "机位", "运镜方式", "画面内容", "音色", "心理音轨",
    "环境音效", "环境动态", "光影", "光源来源", "光源方向", "色温倾向", "生成限制",
    "转场类型", "切镜/转场提示词", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
]
COMPACT_MOTHER_COLUMNS = [
    "分镜序号", "时长", "累计时长", "功能类型/场景连续性ID", "正式分镜母版",
    "GPT关键帧生成指令", "参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句",
    "光影",
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
VOICE_TEMPLATE_LINE_RE = re.compile(
    r"^(基础声线|言语行为|当前表达|发声方式|音量|语速与节奏|呼吸|停顿|重音|尾音|禁止语气)([：:])(.*)$"
)
VOICE_NO_DIALOGUE_RE = re.compile(r"^无对白[，,；;。\s]*(?:本镜)?不适用[。.]?$|^不适用[：:]?\s*无对白[。.]?$")
NARRATIVE_SPEECH_ACT_RE = re.compile(
    r"劝|说|说道|称|声称|宣称|陈述|解释|回答|答应|回应|问|反问|"
    r"承认|指出|禀告|报告|吩咐|命令|要求|提醒|警告|承诺|拒绝|央求|叫嚷|"
    r"告知|告诉|通知|通报|请求|恳请|宣读|追问|交代|说明|命人|派人|召见"
)
NEGATED_SPEECH_ACT_RE = re.compile(
    r"(?:不敢继续|没有|并未|未曾|不再|无需|无须)[^。；，]{0,24}(?:"
    + NARRATIVE_SPEECH_ACT_RE.pattern + r")"
)
ADAPTED_DIALOGUE_TREATMENTS = {
    "原文直接对白",
    "原文事实转化对白",
    "可视动作",
    "心理音轨",
    "合并压缩",
    "不转对白",
    SOURCE_LIMIT_TREATMENT,
}
ADAPTED_DIALOGUE_AFFORDANCE = {"必须转对白", "建议转对白", "不宜转对白", SOURCE_LIMIT_AFFORDANCE}
NARRATIVE_SCENE_SEMANTICS = {"叙述性会话场景", "混合会话场景", "非会话叙述"}
COMMUNICATION_FUNCTIONS = {
    "问答", "劝决", "令应", "报听", "辩核", "证判", "说明", "协商", "拒绝", "其他",
}
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
    "audit_workbook_evidence",
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
EYE_VISIBLE_TERMS = ["面部", "眼睛", "眼神", "眼睑", "眼球", "眼眸", "双眼", "侧脸", "人物反应"]
EYE_VISIBLE_SHOT_SCALES = ["大特写", "特写", "近景", "中近景", "胸部以上", "腰部以上"]
STIFF_GAZE_RE = re.compile(r"全程凝视|目光锁死|目光锁住|一直盯住|保持眼神不动|保持凝视")
EYE_FOCUS_RE = re.compile(r"眼球.{0,12}(?:换焦|位移|焦点调整|微调)|(?:视线|目光).{0,12}(?:换焦|焦点调整|微调)")
EYELID_CHANGE_RE = re.compile(r"(?:上|下)?眼睑|眼皮|眼睫")
GAZE_PATH_RE = re.compile(
    r"(?:眼球|视线|目光|眼神).{0,24}(?:从|由).{0,24}"
    r"(?:移向|转向|落向|扫向|回到|看向|落在)"
)
EYES_LEAD_HEAD_RE = re.compile(
    r"(?:眼球|视线|目光).{0,20}先.{0,30}头部|"
    r"头部.{0,16}(?:随后|再|轻微跟|保持不动|不跟随|不转)"
)
BLINK_TIMING_RE = re.compile(
    r"(?:开口|说话|对白|视线落稳|换焦|听完|台词结束|反应|情绪回收|换气|句间|镜尾)"
    r".{0,18}(?:前|后|时|期间|阶段|反应段).{0,18}(?:自然)?(?:短|轻)?眨|"
    r"(?:自然)?(?:短|轻)?眨(?:眼)?.{0,18}(?:开口|说话|对白|视线落稳|换焦|听完|台词结束|反应|情绪回收|换气|句间|镜尾)"
)
INTENTIONAL_NO_BLINK_RE = re.compile(r"(?:本镜|全镜|本段|台词期间|重音期间|关键词期间)[^。；]{0,28}不眨眼")
NO_BLINK_REASON_RE = re.compile(r"高压|压迫|震惊|认出|警觉|决定性|关键词|重音|尾音|情绪峰值")
EMPHASIS_NO_BLINK_RE = re.compile(
    r"(?:重音|关键词|尾音|[‘“][^’”]{1,12}[’”]).{0,24}不眨眼|"
    r"不眨眼.{0,24}(?:重音|关键词|尾音|[‘“][^’”]{1,12}[’”])"
)
STATIC_OPEN_EYES_RE = re.compile(
    r"双眼自然睁开.{0,32}(?:不处于|不生成|避开|禁止|无)(?:半眨眼|半闭眼)|"
    r"(?:不处于|不生成|避开|禁止)(?:半眨眼|半闭眼).{0,32}双眼自然睁开"
)
PLOT_CLOSED_EYES_RE = re.compile(r"(?:原文|剧情|表演)(?:明确|要求|需要).{0,20}(?:闭眼|合眼|睡着|昏迷)")
BOUNDARY_EXPLANATION_RE = re.compile(r"切|转入|转到|换场|场景|地点|时空|翌日|次日|稍后|片刻|另一|新空间|跨集|集间")
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
    r"(?:禁止|不要|不得|不使用|不生成|没有|不加|无)(?:任何)?(?:让)?"
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
    "上一个镜头", "上个镜头", "上一个镜", "上个镜", "前一个镜头", "前个镜头",
    "上一个关键帧", "上个关键帧", "上一帧", "上一个画面", "上个画面",
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
KEYFRAME_UNOBSERVABLE_RE = re.compile(
    r"整个故事中最幸福的一刻|没有战争|没有离别|没有未来的悲伤|"
    r"(?:红色|颜色)[^；。\n]{0,10}代表(?:着)?[^；。\n]{0,12}(?:爱情|婚姻|希望)|"
    r"(?:未来|多年后)[^；。\n]{0,18}(?:无法忘记|仍然难忘|仍难忘|忘不了)",
    re.I,
)

KEYFRAME_TIMECODE_RE = re.compile(
    r"(?:\[|【)?\s*\d+(?:\.\d+)?\s*(?:秒|s)?\s*[-—–~至]\s*"
    r"\d+(?:\.\d+)?\s*(?:秒|s)\s*(?:\]|】|[:：])?",
    re.I,
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
LIGHT_DIRECTION_RE = re.compile(r"画面左|画面右|画面正前侧|左侧|右侧|左前|右前|左后|右后|上方|下方|前方|后方")
LIGHT_PATH_RE = re.compile(r"斜落|斜照|投向|落向|补面|衰减|递减|纵深|暗侧|明侧")
DAY_SCENE_RE = re.compile(r"清晨|晨间|早晨|上午|正午|午后|白昼|日间|晴日|早膳后")
NIGHT_SCENE_RE = re.compile(r"夜晚|夜间|深夜|月夜|入夜|黄昏后|黎明前|子时|亥时")
DAYLIGHT_SOURCE_RE = re.compile(r"日光|阳光|晨光|午后光|太阳光")
MOONLIGHT_SOURCE_RE = re.compile(r"月光|月色|夜空")
PRACTICAL_LIGHT_RE = re.compile(r"红烛|烛火|烛光|烛台|油灯|灯笼|火盆|火焰|火场|灯火")
INTERIOR_SCENE_RE = re.compile(r"室内|内室|闺阁|厅|房|堂|殿|阁|书斋|卧室|侧厅")
DAYLIGHT_APERTURE_RE = re.compile(r"窗|门|天井|敞口|廊|庭院|院中|室外|户外|街|巷|市|城门|野外")
DARK_INTERIOR_EXCEPTION_RE = re.compile(r"暗室|密室|地窖|地下|无窗|封闭|遮光")
EXPLICIT_INTERIOR_RE = re.compile(
    r"室内|内室|闺阁|厅内|房内|堂内|殿内|阁内|屋内|书斋|卧室|侧厅|"
    r"正厅|花厅|新房|祠堂内|寝殿|厢房|偏殿|卧房|书房|账房|厅堂|内景"
)
EXPLICIT_EXTERIOR_RE = re.compile(r"室外|户外|外景|门外|窗外|外廊|露天|街市|街道|庭院|院中")
CLOSED_APERTURE_RE = re.compile(
    r"(?:门|窗)(?:扇)?[^；。\n]{0,8}(?:闭合|关闭|紧闭|关着)|"
    r"(?:闭合|关闭|紧闭|关着)[^；。\n]{0,8}(?:门|窗)|窗纸完整"
)
OPEN_APERTURE_RE = re.compile(
    r"(?:门|窗)(?:扇)?[^；。\n]{0,8}(?:开启|打开|敞开|半开)|"
    r"(?:开启|打开|敞开|半开)[^；。\n]{0,8}(?:门|窗)"
)
INDOOR_HAZE_RE = re.compile(
    r"薄雾|浓雾|雾气|雾蒙蒙|烟雾|烟尘|烟气|细烟|薄烟|轻烟|浓烟|"
    r"炊烟|烟霭|蒸汽|水汽|茶汽|空气颗粒|奶雾|"
    r"全屋[^；。\n]{0,8}(?:雾|烟)|朦胧雾化"
)
INDOOR_MILKY_OPTICS_RE = re.compile(
    r"柔焦|镜头扩散|高光晕染|halation|镜头光晕|全画面光晕|全画面雾化|泛白光晕",
    re.I,
)
VISIBLE_RAY_RE = re.compile(
    r"丁达尔|体积光|可见光束|光柱|耶稣光|"
    r"(?:日光|晨光|阳光|光线)[^；。\n]{0,24}(?:穿过|射入|斜射|穿插)"
)
INDOOR_STRONG_BACKLIGHT_RE = re.compile(
    r"强逆光|侧逆光|背光(?!一侧|侧面|面)|(?:斜后方|侧后方)[^；。\n]{0,8}(?:日光|晨光|光线|主光)|"
    r"背后[^；。\n]{0,8}(?:日光|晨光|光线|主光)|轮廓光"
)
PLOT_ATMOSPHERE_RE = re.compile(
    r"火场|失火|着火|燃烧|浓烟|烟尘|扬尘|尘土飞扬|爆炸|蒸汽|"
    r"水汽弥漫|雾天|雨雾|破门"
)
ATMOSPHERE_SOURCE_RE = re.compile(
    r"香炉|药炉|灶火|灶台|灶烟|火盆|火场|失火|着火|烛火|红烛|油灯|"
    r"热茶|热水|沸水|蒸锅|扬尘|尘土飞扬|雨雾|蒸汽"
)
ATMOSPHERE_LOCAL_SCOPE_RE = re.compile(r"局部|一缕|(?:仅|只在)[^；。\n]{0,24}(?:烟|雾|尘|汽)")
ATMOSPHERE_NO_SPREAD_RE = re.compile(r"不扩散|不遮挡|不蔓延|不笼罩|不覆盖")
BACKGROUND_READABLE_RE = re.compile(r"(?:背景|木构|门窗|陈设|空间结构)[^；。\n]{0,16}(?:清晰|清楚|可辨)")
GLOBAL_ATMOSPHERE_RE = re.compile(
    r"(?:全屋|整个房间|整间房|全画面)[^；。\n]{0,12}(?:烟|雾)|"
    r"(?:烟|雾)[^；。\n]{0,12}(?:全屋|整个房间|整间房|全画面)"
)
LOCAL_HIGHLIGHT_RE = re.compile(r"(?:火焰|烛火|灯芯)[^；。\n]{0,16}(?:边缘|附近)[^；。\n]{0,16}(?:局部|轻微|克制)|(?:局部|轻微|克制)[^；。\n]{0,16}(?:火焰|烛火|灯芯)[^；。\n]{0,16}(?:边缘|附近)")
INDOOR_BACKGROUND_CLARITY_RE = re.compile(
    r"空气清澈|背景[^；。\n]{0,12}(?:清晰|清楚|可辨)|"
    r"(?:木构|门窗|陈设|空间结构)[^；。\n]{0,12}(?:清晰|清楚|可辨)|"
    r"不泛白|不雾化"
)
BACKLIGHT_STORY_NEED_RE = re.compile(r"剪影|逆光登场|门口现身|轮廓辨人|遮蔽身份|需要逆光|逆光用途|逆光关系")
NEGATED_EFFECT_PREFIX_RE = re.compile(
    r"(?:禁止|不得|不要|避免|不使用|不生成|不出现|不存在|没有|无|不)"
    r"(?:任何|明显|全画面|可见|强烈|额外)?[^；。！？!?，,、]{0,12}$"
)
BACKLIGHT_EFFECT_RE = re.compile(r"轮廓光|发丝(?:边缘|外缘)[^；。\n]{0,8}(?:光|亮)|肩(?:线|部)(?:边缘)?[^；。\n]{0,8}(?:光|亮)")
BACKLIGHT_DIRECTION_RE = re.compile(r"后方|后侧|左后|右后|侧后|斜后|背后|逆光")
EYE_FIRE_REFLECTION_RE = re.compile(
    r"(?:眼睛|眼中|眼神)[^；。\n]{0,12}(?:烛火|灯火|火光)[^；。\n]{0,10}(?:反射|倒影|光点)|"
    r"(?:烛火|灯火|火光)[^；。\n]{0,12}(?:映在|反射)[^；。\n]{0,8}(?:眼睛|眼中|眼神)"
)
HALATION_RE = re.compile(r"halation|高光晕染|高光[^；。\n]{0,6}光晕|镜头光晕", re.I)
RESTRAINED_EFFECT_RE = re.compile(r"轻微|极轻微|克制|细腻|仅有|少量")
VOLUMETRIC_LIGHT_RE = re.compile(r"丁达尔|体积光|光束")
SCATTERING_MEDIUM_RE = re.compile(r"雾|烟|尘|水汽|香火|空气颗粒")
UNIFORM_FACE_LIGHT_RE = re.compile(r"面部[^；。\n]{0,12}(?:柔和均匀|均匀照亮)|全脸[^；。\n]{0,8}均匀")
WEAK_FILL_RE = re.compile(r"反射|补光|夜空|月色|浅色墙|地面|纱幔|窗外")


def _invisible_face_terms(text, entities):
    """Keep mixed-shot facial instructions bound to their visible owner."""
    from performance_checks import _subject
    face_names = {name for name, description in entities.descriptions.items()
                  if re.match(re.escape(name) + r'（(?:面部|[^；）]*、面部)(?:、|；|）)', description)}
    eye_action = r'(?:眨眼|闭眼|闭上(?:双眼|眼睛)|睁眼|睁开(?:双眼|眼睛))'
    inherited_face = re.compile(r'^(?:眼睛|眼神|视线|目光|眉|嘴唇|嘴角|瞳孔|脸颊|眼眶|睫毛|面部|双眼|眼球|眼睑|'
                                r'(?:自然|轻轻|缓慢|快速|短促|微微|轻)*' + eye_action + r')')
    bad = set()
    for sentence in re.split(r'[。；;\n]|【\d+\.\d—\d+\.\d秒】', text):
        sentence = sentence.strip()
        if any(sentence == name + '眼睛不入画' for name in entities.people):
            continue
        owner = None
        for clause in re.split(r'[，,]', sentence):
            probe = re.sub(r'在\d+\.\d秒', '', clause.strip())
            for name in entities.people:
                probe = re.sub(r'^' + re.escape(name) + r'(?:观察|看着|看见|望着|注视)', '', probe)
            named_part = re.match(r'([^\W\d_]{1,64}?)(?:眼睛|眼神|视线|目光|眉|嘴唇|嘴角|瞳孔|脸颊|眼眶|睫毛|面部)', probe)
            unknown_owner = named_part and named_part[1] not in entities.people
            subject = _subject(probe, entities.people) if not unknown_owner else None
            if subject is not None:
                owner = subject
            # Only explicit subjectless facial fragments inherit across a
            # comma; an unfamiliar name plus a blink/close-eye verb cannot.
            elif unknown_owner or not inherited_face.match(probe):
                owner = None
            if owner not in face_names:
                bad.update(term for term in FACE_TERMS if term in clause)
                bad.update(match.group() for match in re.finditer(eye_action, clause))
    return sorted(bad)


def _has_affirmative_match(pattern: re.Pattern[str], value: Any) -> bool:
    """Ignore effect names that occur only inside explicit negative clauses."""

    text = str(value or "")
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 28):match.start()]
        clause_prefix = re.split(r"[；。！？!?，,\n]", prefix)[-1]
        # Bare effect lists inherit a prohibition; a new affirmative predicate
        # or contrast starts its own clause even when separated by a list comma.
        list_head = clause_prefix.split("、", 1)[0]
        current_item = clause_prefix.rsplit("、", 1)[-1] + match.group(0)
        affirmative_restart = re.search(
            r"但是|但|然而|却|不过|改用|改为|使用|采用|允许|出现|生成|形成|摆放|点燃",
            current_item,
        ) or (
            re.search(r"打开|开启|敞开|半开|闭合|关闭|紧闭|关着", current_item)
            and not re.match(r"\s*(?:禁止|不得|不要|避免)", list_head)
        )
        if NEGATED_EFFECT_PREFIX_RE.search(clause_prefix) or (
            "、" in clause_prefix
            and not affirmative_restart
            and re.match(
                r"\s*(?:禁止|不得|不要|避免|不使用|不生成|不出现|不存在|没有|无)",
                list_head,
            )
        ):
            continue
        return True
    return False


APERTURE_KEY_RE = re.compile(
    r"左前|左后|右前|右后|左|右|前|后|东南|东北|西南|西北|东|南|西|北|"
    r"第[一二三四五六七八九十百0-9]+(?:扇|道|号)?|[A-Za-z0-9]+号"
)


def _aperture_state_rows(value: str, kind: str, state: str) -> list[str]:
    """Keep each opening's identity with its affirmative state."""

    noun = rf"(?:门窗|窗门|{kind})"
    gap = r"[^门窗；。！？!?，,、\n]{0,8}"
    pattern = re.compile(rf"{noun}{gap}(?P<after>{state})|(?P<before>{state}){gap}{noun}")
    negated_state = re.compile(r"没有|尚未|并未|从未|还未|未|不能|不得|不要|禁止|不再|不")
    text = str(value or "")
    rows: list[str] = []
    for match in pattern.finditer(text):
        if not _has_affirmative_match(re.compile(re.escape(match.group())), text):
            continue
        state_start = match.start("after") if match.group("after") else match.start("before")
        # Negation can occur inside a noun-to-state match: “左窗没有打开”.
        before_state = re.split(
            r"[；。！？!?，,、\n]", text[max(0, state_start - 16):state_start]
        )[-1]
        if negated_state.search(before_state):
            continue
        local = re.split(
            r"[门窗；。！？!?，,、\n]", text[max(0, match.start() - 12):match.start()]
        )[-1] + match.group()
        keys = list(APERTURE_KEY_RE.finditer(local))
        rows.append(keys[-1].group() if keys else "")
    return rows


def _has_aperture_state(value: str, kind: str, state: str) -> bool:
    return bool(_aperture_state_rows(value, kind, state))


def _aperture_reference_keys(value: str, kind: str) -> list[str]:
    text = str(value or "")
    keys: list[str] = []
    for match in re.finditer(rf"(?:门窗|窗门|{kind})", text):
        local = re.split(
            r"[门窗；。！？!?，,、\n]", text[max(0, match.start() - 12):match.start()]
        )[-1]
        found = list(APERTURE_KEY_RE.finditer(local))
        keys.append(found[-1].group() if found else "")
    return keys


FORMAL_MASTER_FIELD_ORDER = FORMAL_EXECUTION_FIELDS

VOICE_DIMENSION_PATTERNS = {
    "声音强弱": re.compile(r"声音|音量|放轻|压低|变亮|变稳|抬高"),
    "语速节奏": re.compile(r"语速|一字一句|断断续续|咬字|平稳|从容|急促|缓慢|利落"),
    "呼吸状态": re.compile(r"呼吸|气息|吸气"),
    "停顿位置": re.compile(r"停顿|短停|停一下|开口前|句中|关键词前|卡住"),
    "重音位置": re.compile(r"重音|加重|强调|咬重"),
    "尾音变化": re.compile(r"尾音|上扬|下压|发虚|放柔|拉长|落下|收住"),
}
# Baseline completeness uses field_content's registry-owned complete clauses.
# A second keyword list here previously disagreed with its adult/child grammar.
VOICE_ALLOWED_TAILS = ("上抬", "下压", "截断", "发虚", "落稳", "短收")
GENERIC_NEUTRAL_VOICE_CLAUSES = (
    "声音中等清晰", "语速从容", "关键词加重", "尾音随句意收住",
)

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
    r"排队|巡逻|查验|上香|进出|开门|收摊|交易|付钱|张望|擦肩|落座|饮茶|吃饭|挑担|招呼|"
    r"(?:分先后|先后|陆续|错时)[^，,。；;\n]{0,18}(?:经过|走过|通过|散行)"
)
PUBLIC_VISUAL_LAYER_PATTERNS = {
    "near": re.compile(r"前景|近处|身侧|画面左侧|画面右侧"),
    "mid": re.compile(r"中景|中层|街面|周围"),
    "far": re.compile(r"远景|远处|背景|后景|街口|巷口|城门方向"),
}
PUBLIC_AUDIO_LAYER_PATTERNS = {
    "near": re.compile(r"近处|近层|近景|脚边|身侧|贴近|前景"),
    "mid": re.compile(r"中层|中景|街面|周围|身后"),
    "far": re.compile(r"远处|远层|远景|画外|街口|巷口|城门方向|门外"),
}
PUBLIC_AUDIO_FAMILY_PATTERNS = {
    "movement": re.compile(r"脚步|鞋底|踩水|踩雪|衣料|衣摆|木屐|马蹄|车轮|车辙|轿杆|推车|担子|扁担"),
    "crowd": re.compile(r"人声底噪|低声交谈|交谈声|含混人声|人群低语|模糊人声|远处人声|客声|食客低语|无可辨词(?:的)?人声底"),
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
            # Generated from rule_registry.json; never merge another editable
            # catalog into this runtime view. Bootstrap verifies exact equality.
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


def _parse_voice_template(value: Any) -> tuple[dict[str, str], list[str], list[str]]:
    """Parse the fixed nested voice block without treating it as shot fields."""

    parsed: dict[str, str] = {}
    order: list[str] = []
    format_errors: list[str] = []
    current: str | None = None
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = VOICE_TEMPLATE_LINE_RE.match(line)
        if match:
            label, colon, body = match.groups()
            order.append(label)
            current = label
            if colon != "：":
                format_errors.append(f"{label}必须使用全角冒号")
            if label in parsed:
                format_errors.append(f"{label}重复")
            parsed.setdefault(label, body.strip())
            if label == "发声方式" and body.strip():
                format_errors.append("发声方式必须是空值分组标题，六项分别换行填写")
            continue
        if current is None:
            format_errors.append(f"固定子模板前出现无标签内容：{line}")
            continue
        parsed[current] = f"{parsed.get(current, '')}{line}".strip()
    return parsed, order, format_errors


def _voice_template_errors(
    value: Any, dialogue: str,
    dialogue_rows: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Return deterministic THG-15 failures for one dialogue shot."""

    text = str(value or "").strip()
    if not text or VOICE_NO_DIALOGUE_RE.fullmatch(text):
        return ["带对白镜不得把音色写为空值或‘无对白，不适用’"]
    parsed, order, errors = _parse_voice_template(text)
    if order != list(VOICE_TEMPLATE_LABELS):
        errors.append(
            "音色子模板必须按固定顺序且各出现一次："
            + "→".join(VOICE_TEMPLATE_LABELS)
        )
    from voice_sequence import has_markers, validate_voice_sequence
    if has_markers(text):
        return list(dict.fromkeys(errors + validate_voice_sequence(parsed, dialogue_rows)))
    for label in VOICE_TEMPLATE_LABELS:
        if label == "发声方式":
            continue
        if not parsed.get(label, "").strip():
            errors.append(f"{label}不能为空")

    # voice_content_findings below checks all five baseline components using
    # exactly the same owned-clause grammar as direct field/export checks.
    current_expression = parsed.get("当前表达", "")
    if cjk_len(current_expression) > 80:
        errors.append("当前表达必须是一句短而可执行的话，不得写成长篇心理分析")

    emphasis = parsed.get("重音", "")
    errors.extend(emphasis_binding_faults(
        emphasis, dialogue_rows if dialogue_rows is not None else [("本镜说话人", dialogue)],
    ))
    tail = parsed.get("尾音", "")
    if tail and not any(value in tail for value in VOICE_ALLOWED_TAILS):
        errors.append("尾音必须明确选择上抬、下压、截断、发虚、落稳或短收")

    prohibited = parsed.get("禁止语气", "")
    prohibited_items = [
        item.strip(" 。.")
        for item in re.split(r"[、，,；;/／]", prohibited)
        if item.strip(" 。.")
    ]
    if prohibited and not 2 <= len(prohibited_items) <= 4:
        errors.append("禁止语气必须只列本句最可能出现的2—4种错误表达")
    generic_count = sum(clause in text for clause in GENERIC_NEUTRAL_VOICE_CLAUSES)
    if generic_count >= 3:
        errors.append("个性化音色后不得追加统一中性发声模板")
    errors.extend(f"{row['path']}: {row['message']} [{row['excerpt']}]"
                  for row in voice_content_findings(text, dialogue_rows=dialogue_rows or [],
                                                    mask=context_instruction_text))
    return list(dict.fromkeys(errors))


def _deprecated_display_label_hits(field: str, value: Any) -> list[str]:
    """Allow only the fixed nested 发声方式 heading inside 音色."""

    text = str(value or "")
    hits: list[str] = []
    for label in DEPRECATED_DISPLAY_LABELS:
        if label not in text:
            continue
        if (
            field == "音色"
            and label in {"发声方式：", "发声方式:"}
            and re.search(r"(?m)^\s*发声方式\s*[：:]\s*$", text)
        ):
            continue
        hits.append(label)
    return hits


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
        r"(\d+)\s*次(?:自然)?(?:短|轻)?眨眼",
        r"(?:自然)?(?:短|轻)?眨眼\s*(\d+)\s*次",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            if len(m.groups()) == 2:
                return int(m.group(1)), int(m.group(2))
            value = int(m.group(1))
            return value, value
    chinese_counts = {
        "零": 0,
        "一": 1,
        "两": 2,
        "二": 2,
        "三": 3,
    }
    match = re.search(
        r"([零一两二三])\s*次(?:自然)?(?:短|轻)?眨眼|"
        r"(?:自然)?(?:短|轻)?眨眼\s*([零一两二三])\s*次",
        text,
    )
    if match:
        value = chinese_counts[match.group(1) or match.group(2)]
        return value, value
    if re.search(r"(?:自然)?(?:短|轻)?眨(?:眼)?一次", text):
        return 1, 1
    return None


def _stable_sha256(value: Any) -> str:
    from production_derivation import digest
    return digest(value)


def _shot_episode_key(shot: dict[str, Any], ordinal: int) -> str:
    shot_id = str(shot.get("镜头编号") or "")
    match = EP_SHOT_ID_RE.fullmatch(shot_id)
    if match:
        return f"EP{match.group('episode')}"
    explicit = str(shot.get("episode_id") or "").strip()
    return explicit or "LEGACY-SINGLE-EPISODE"


def _eye_contract_faults(
    shot: dict[str, Any],
    *,
    duration: float,
    strict: bool,
    visible_names: list[str] | None = None,
    non_person_names: list[str] | None = None,
    legacy_components: bool = False,
) -> list[str]:
    """Validate each visible person, keeping the historical callable signature."""
    from picture_presentation import timing_view, PictureTimingError
    try:
        shot = timing_view(shot)
    except PictureTimingError as exc:
        return [str(exc)]
    return eye_contract_faults(
        shot, duration=duration, strict=strict,
        visible_names=visible_names, non_person_names=non_person_names,
        legacy_components=legacy_components,
        dialogue_rows=extract_dialogues(shot.get("画面内容")),
    )


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _validate_punctuation(dialogue: str, delivery: str) -> list[str]:
    missing = []
    checks = [
        (("……" in dialogue or "..." in dialogue), ["停顿", "卡住", "断续"], "省略号停顿/卡住"),
        (("——" in dialogue or "—" in dialogue), ["转折", "打断", "强调", "断裂"], "破折号转折/打断"),
        (("？！" in dialogue or "!?" in dialogue or "?!" in dialogue), ["质问", "爆发", "加重", "重音"], "问叹号质问/爆发"),
        (("？" in dialogue or "?" in dialogue), ["上扬", "上抬", "下压", "落稳", "短收", "发虚", "截断", "试探", "质问", "疑问", "询问", "反问", "收住"], "问号尾音/意图"),
        (("！" in dialogue or "!" in dialogue), ["重音", "加强", "爆发", "警告", "兴奋"], "感叹号情绪/重音"),
        (("～" in dialogue or "~" in dialogue), ["拉长", "轻快", "柔软", "放柔"], "波浪号尾音"),
    ]
    for triggered, expected, label in checks:
        if triggered and not _has_any(delivery, expected):
            missing.append(label)
    return missing


def _validate_timeline(text: str, duration: float, *, strict: bool = False,
                       visible_subjects: str | None = None) -> list[str]:
    """Return concrete timeline faults; R10-SPC-1 requires exactly three stages."""
    from performance_checks import eye_visibility_timeline_scan
    scan = eye_visibility_timeline_scan(text, duration=duration, visible_subjects=visible_subjects)
    strict_matches = list(STRICT_TIMELINE_STAGE_RE.finditer(text))
    if strict:
        faults: list[str] = []
        broad_segments = list(TIMELINE_RANGE_RE.finditer(scan))
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
        segments = [(float(m.group("start")), float(m.group("end"))) for m in TIMELINE_RANGE_RE.finditer(scan)]
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
    unknown_music = [row for row in music_directives(text) if row["status"] == "unknown"]
    if unknown_music:
        raise ValueError("generation_limit_audio_contract_unknown: unresolved music scope: " + repr(unknown_music[0]["text"]))
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


def _transition_comparison_key(value: Any) -> str:
    return re.sub(
        r"[\s，。；：、,.!?！？;:\-—–【】\[\]()（）“”\"']",
        "",
        _projection_text(value),
    ).casefold()


def _normalize_transition_text(value: Any, *, legacy: bool) -> str:
    """Validate new transitions and clean only read-only legacy imports."""

    text = _projection_text(value)
    if not text:
        return ""
    empty_separator = re.search(r"[；;。]\s*[；;。]", text)
    parts = [
        part.strip(" \t\r。；;，,")
        for part in re.split(r"[；;。\n]+", text)
        if part.strip(" \t\r。；;，,")
    ]
    normalized = [_transition_comparison_key(part) for part in parts]
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


def project_formal_master(shot: dict[str, Any]) -> str:
    """Compile the exact fifth-column R10-SPC-1 formal master cell."""
    projected = dict(shot)
    _assert_active_timing_precision(projected)
    projected["生成限制"] = _normalize_generation_limits(
        projected.get("生成限制")
    )
    projected["切镜/转场提示词"] = _normalize_transition_text(
        projected.get("切镜/转场提示词"), legacy=False
    )
    paragraphs = [
        f"镜头序号：{_projection_text(projected.get('镜头编号'))}"
        f"（{_format_seconds(projected.get('时长'))}s；"
        f"累计{_format_seconds(projected.get('累计时长'))}s）",
        f"镜头类型：{_projection_text(projected.get('镜头类型'))}｜"
        f"{_projection_text(projected.get('可见主体'))}",
    ]
    from picture_presentation import display_picture
    for field in FORMAL_MASTER_FIELD_ORDER:
        value = display_picture(projected) if field == "画面内容" else _projection_text(projected.get(field))
        paragraphs.append(f"音色：\n\n{value}" if field == "音色" else f"{field}：{value}")
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
    context_text = "\n".join(context_instruction_text(_projection_text(projected.get(field)), field)
                             for field in ("镜头类型", "可见主体", *FORMAL_MASTER_FIELD_ORDER))
    context_hits = sorted(term for term in CROSS_SHOT_TERMS if term in context_text)
    if context_hits:
        raise ValueError(
            "formal_master_context_violation: cross-shot references are "
            "forbidden: " + ", ".join(context_hits)
        )
    assert_field_content(projected, mask=context_instruction_text)
    return formal


def _protect_visual_text_fragments(value: Any, *, field_name: str = "") -> tuple[str, dict[str, str]]:
    return protect_visual_text_fragments(_projection_text(value), field_name=field_name)



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
        instruction = KEYFRAME_QUOTED_DIALOGUE_RE.sub("", fragment)
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
    eye_state = r'(?:闭眼|合眼|睁眼|闭上(?:双眼|眼睛)|睁开(?:双眼|眼睛))'
    eye_sequence = re.compile(
        r'(?:先|从|由)?' + eye_state + r'[^。；;\n]{0,12}'
        r'(?:再|然后|随后|接着|又|到|至|转为|变为)' + eye_state)
    hits = []
    # Temporal links cross commas; splitting them first loses the sequence.
    for match in affirmative_matches(eye_sequence, text):
        hits.append('eye_process=' + match.group())
    if re.search(r"(?:视频|动态画面)中(?:的)?[^。；;\n]*按画面内容(?:所列时点)?执行", text):
        hits.append("unprojected_video_policy")
    if any(phrase in text for phrase in (
        "眼部动作按本镜已列事件执行", "未列出眼部动作时保持已声明的自然状态",
    )):
        hits.append("unprojected_event_policy")
    for clause in re.split(r"[，,。；;\n]", text):
        # A still can require open eyes and explicitly exclude a half blink.
        # Such exclusion is not a command to animate eyelids.
        clause = mask_static_eye_exclusions(clause)
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


def _visual_only_clauses(
    value: Any, *, strip_dialogue: bool = False,
    strict_static_quotes: bool = False, field_name: str = "",
) -> str:
    """Keep only visible still-frame semantics from any projected field."""

    result = str(value or "")
    protected_visual: dict[str, str] = {}
    if strip_dialogue:
        result, protected_visual = _protect_visual_text_fragments(result, field_name=field_name)
        if strict_static_quotes:
            unbound = KEYFRAME_QUOTED_DIALOGUE_RE.search(result)
            if unbound:
                raise ValueError(
                    "keyframe_static_contract_violation: unbound_visual_text; "
                    f"field={field_name or '<static>'}; masked_offset={unbound.start()}"
                )
    result = KEYFRAME_TIMECODE_RE.sub("", result)
    if strip_dialogue:
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
            if any(marker.casefold() in clause.casefold() for marker in KEYFRAME_AUDIO_ONLY_CLAUSE_MARKERS):
                continue
            if KEYFRAME_AUDIO_SOURCE_CLAUSE_RE.search(clause):
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
                   if KEYFRAME_QUOTED_DIALOGUE_RE.search(fragment) and token not in result]
        if missing:
            raise ValueError(
                "keyframe_static_contract_violation: dropped_visual_text; "
                f"field={field_name or '<static>'}; payload_count={len(missing)}"
            )
    for token, fragment in protected_visual.items():
        result = result.replace(token, fragment)
    return result.strip(" \n；;，,。:：")


def project_gpt_keyframe(shot: dict[str, Any]) -> str:
    """Compile the visual-only sixth-column projection from the same canonical."""
    assert_field_content(shot, mask=context_instruction_text)
    _assert_projected_aspect_contract(
        REQUIRED_ASPECT_RATIO_PHRASE + "\n" + _projection_text(shot.get("参考图锚定")),
        projection="reference_anchor",
    )
    from field_content import appearance_anchor_text, assert_static_text_preserved
    fields = ("空间站位描述", "起始状态", "机位", "镜头类型", "可见主体",
              "光影", "光源来源", "光源方向", "色温倾向")
    values = {field: _projection_text(shot.get(field)) for field in fields}
    for field in ("空间站位描述", "起始状态", "机位"):
        values[field] = _visual_only_clauses(values[field], strip_dialogue=True,
                                           strict_static_quotes=True, field_name=field)
        assert_static_text_preserved(_projection_text(shot.get(field)), values[field], field)
    appearance = appearance_anchor_text(_projection_text(shot.get("参考图锚定")))
    if appearance:
        original_appearance = appearance
        appearance = _visual_only_clauses(appearance, strip_dialogue=True,
                                         strict_static_quotes=True, field_name="参考图锚定")
        assert_static_text_preserved(original_appearance, appearance, "参考图锚定.文字造型锚")
    limits = re.sub(r"(?<!\d)9\s*[:：]\s*16(?![\d.])\s*(?:"
                    r"竖屏真人古装短剧(?:截图质感|单帧)?|"
                    r"真人古装短剧(?:截图质感|单帧)?|竖屏)?", "",
                    _normalize_generation_limits(shot.get("生成限制")), flags=re.I)
    limits = re.sub(r"\s*[；;]\s*", "；", limits)
    limits = re.sub(r"；{2,}", "；", limits).strip(" ；;，,。")
    limits = _visual_only_clauses(_keyframe_generation_limits(limits), strip_dialogue=True,
                                strict_static_quotes=True, field_name="生成限制")
    sections = r5_keyframe_sections(values, aspect=REQUIRED_ASPECT_RATIO_PHRASE,
                                   visual_limits=limits, appearance_anchor=appearance)
    prompt = "\n".join(["GPT关键帧生成指令"] + [f"{label}：{value}" for label, value in sections])
    _assert_projected_aspect_contract(prompt, projection="gpt_keyframe")
    instruction_text = _keyframe_instruction_text(prompt)
    forbidden_hits = sorted(set(_keyframe_forbidden_hits(prompt)) | set(_keyframe_eye_process_hits(prompt)) | set(_keyframe_authored_process_hits(prompt)))
    unobservable_hits = _keyframe_unobservable_hits(instruction_text)
    context_hits = sorted(term for term in CROSS_SHOT_TERMS if term in instruction_text)
    if forbidden_hits or context_hits or unobservable_hits:
        violation_name = (
            "keyframe_unobservable_contract_violation"
            if unobservable_hits
            else "keyframe_static_contract_violation"
        )
        raise ValueError(
            violation_name + ": "
            + "; ".join(
                part
                for part in (
                    "dynamic=" + ",".join(forbidden_hits)
                    if forbidden_hits else "",
                    "cross_shot=" + ",".join(context_hits)
                    if context_hits else "",
                    "unobservable=" + ",".join(unobservable_hits)
                    if unobservable_hits else "",
                )
                if part
            )
        )
    return prompt


def project_export_row(shot: dict[str, Any]) -> dict[str, Any]:
    """Return one exact eleven-column export row without persisting projections."""
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
        "光影": project_lighting(shot, _projection_text),
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


def _visible_character_names(value: Any, *, registered_people: list[str] | None = None) -> list[str]:
    from visible_entities import resolve_visible_subjects
    return list(resolve_visible_subjects(value, people=registered_people).people)


def _is_multi_person_visible(value: Any, *, registered_people: list[str] | None = None) -> bool:
    text = str(value or "")
    if re.search(r"两人|二人|双人|三人|多人|群像", text):
        return True
    return len(_visible_character_names(text, registered_people=registered_people)) > 1


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
            if strict:
                if any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ("prop_id", "holder", "state")):
                    faults.append(f"道具状态prop_id/holder/state必须是非空字符串：{row!r}")
                    continue
                if len([item for item in re.split(r"[、/|｜]", row["holder"]) if item.strip()]) != 1:
                    faults.append(f"道具{row['prop_id']!r}同一时点必须只有一个持有人：{row['holder']!r}")
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


def _keyframe_forbidden_hits(value: Any) -> list[str]:
    return sorted(
        {match.group(0) for match in KEYFRAME_FORBIDDEN_RE.finditer(_keyframe_instruction_text(value))}
        | set(_keyframe_environment_process_hits(value))
    )


def _keyframe_unobservable_hits(value: Any) -> list[str]:
    return sorted({
        match.group(0)
        for match in KEYFRAME_UNOBSERVABLE_RE.finditer(str(value or ""))
    })


def _practical_light_matches(value: Any) -> list[re.Match[str]]:
    """An explicitly unlit practical is a prop, not an established light source."""
    text = str(value or "")
    found = []
    for match in affirmative_matches(PRACTICAL_LIGHT_RE, text):
        before = re.split(r"[，,、；;。！？!?\n]", text[:match.start()])[-1]
        after = re.split(r"[，,、；;。！？!?\n]", text[match.end():])[0]
        if re.search(r"(?:(?:尚未|并未|未曾|没有|未)(?:点燃|点亮)|(?:已经|已)?熄灭(?:了)?)(?:的)?$", before):
            continue
        if re.match(r"\s*(?:(?:尚未|并未|未曾|没有|未)(?:点燃|点亮|亮起)|(?:已经|已)?熄灭)", after):
            continue
        found.append(match)
    return found


def _current_scene_time_flags(shot: dict[str, Any]) -> tuple[bool, bool]:
    """Read current-time declarations, never source evidence or spoken content.

    Legacy headings and location/time phrases remain supported. Both flags can
    be true: a daylight declaration never masks an authored nighttime conflict.
    Unrecognized prose is not used to guess the scene's current time.
    """
    fields = (
        "功能类型", "镜头类型", "空间站位描述", "起始状态",
        "画面内容", "环境动态", "参考图锚定",
    )
    declaration_prefix = re.compile(
        r"(?:(?:本镜|当前|此时|现在|当日|今天|时间|时段|时辰|场景时间|本镜时间)"
        r"(?:的|为|是|已是|已到|正是|正值|处于|在|[:：]|\s)*|已到|到了|已是|时至|正值|正是|在|于)+$"
    )
    location_prefix = re.compile(
        r"[^；。！？!?，,\n]{0,40}(?:室内|室外|内景|外景|内室|卧房|卧室|书房|书斋|"
        r"厅内|房内|堂内|殿内|院内|庭院|院中|廊下|回廊|湖边|岸边|湖岸|门外|窗外)(?:于|在)?\s*$"
    )
    flags = [False, False]
    for field in fields:
        text = _keyframe_instruction_text(shot.get(field))
        # Written payloads have already been masked by the visual-text guard.
        # Only attributed speech is removed here; quoted current-time choices
        # such as 当前时辰为“子时” retain their explicit declaration prefix.
        text = re.sub(r'(?:说道|问道|答道|喊道|回答|对白|台词|说|问|答|喊)[:：]?\s*[“"「][^”"」]*[”"」]', "", text)
        for clause in re.split(r"[；。！？!?，,\n]", text):
            clause = KEYFRAME_TIMECODE_RE.sub("", clause).strip()
            for index, pattern in enumerate((DAY_SCENE_RE, NIGHT_SCENE_RE)):
                for match in affirmative_matches(pattern, clause):
                    prefix = clause[:match.start()].strip()
                    declared = not prefix or declaration_prefix.fullmatch(prefix.rstrip('“"「'))
                    located = field != "画面内容" and location_prefix.fullmatch(prefix)
                    if declared or located:
                        flags[index] = True
    return flags[0], flags[1]


def _lighting_context_faults(shot: dict[str, Any]) -> list[str]:
    """Check that authored light effects follow the scene's physical facts."""

    light = str(shot.get("光影") or "")
    source = str(shot.get("光源来源") or "")
    direction = str(shot.get("光源方向") or "")
    color = str(shot.get("色温倾向") or "")
    physical_fields = (
        "可见主体", "空间站位描述", "起始状态", "画面内容",
        "环境动态", "参考图锚定",
    )
    context_fields = (
        "功能类型", "镜头类型", *physical_fields,
        "覆盖原文", "原文依据类型", "原文支撑句",
    )
    physical = " ".join(str(shot.get(field) or "") for field in physical_fields)
    context = " ".join(str(shot.get(field) or "") for field in context_fields)
    limits = str(shot.get("生成限制") or "")
    effect_blob = f"{light} {color} {limits}"
    lighting_blob = f"{light} {source} {direction} {color} {limits} {physical}"
    faults: list[str] = []

    is_day, is_night = _current_scene_time_flags(shot)
    daylight_sources = affirmative_matches(DAYLIGHT_SOURCE_RE, _keyframe_instruction_text(source))
    dark_exception = bool(DARK_INTERIOR_EXCEPTION_RE.search(context))
    if is_day and is_night:
        faults.append("同一静态镜头同时声明日景与夜景时段，必须先锁定唯一时间")
    if is_night and daylight_sources:
        faults.append("夜景/黄昏后/黎明前不得使用清晨、午后或太阳日光")
    if is_day and not dark_exception and (
        MOONLIGHT_SOURCE_RE.search(source)
        or re.search(r"青黑夜色|电影级低照度", effect_blob)
    ):
        faults.append("明确日景不得套用月光、青黑夜色或无依据低照度预设")

    if (
        daylight_sources
        and INTERIOR_SCENE_RE.search(context)
        and not DAYLIGHT_APERTURE_RE.search(physical)
    ):
        faults.append("室内日光缺窗、门、天井或其他真实入光开口")

    practical_matches = _practical_light_matches(source)
    practical_match = practical_matches[0] if practical_matches else None
    if practical_match and not _practical_light_matches(physical):
        external_practical = re.search(
            r"(?:门外|窗外|画外)[^；。\n]{0,12}(?:烛|灯|火)", source
        )
        has_aperture = re.search(r"门|窗", physical)
        if not (external_practical and has_aperture):
            faults.append(
                f"主光写{practical_match.group(0)!r}，但空间/起始/画面/锚点未建立对应灯火"
            )

    if MOONLIGHT_SOURCE_RE.search(source) and not DAYLIGHT_APERTURE_RE.search(physical):
        faults.append("月光/夜空补光缺室外空间或门窗等真实通路")

    if _has_affirmative_match(BACKLIGHT_EFFECT_RE, light) and not BACKLIGHT_DIRECTION_RE.search(direction):
        faults.append("发丝或肩线轮廓光必须由后方、侧后、斜后或明确逆光形成")

    if _has_affirmative_match(EYE_FIRE_REFLECTION_RE, light) and not practical_matches:
        faults.append("眼中灯火反射缺场内真实烛、灯或火光来源")

    if _has_affirmative_match(HALATION_RE, effect_blob) and not RESTRAINED_EFFECT_RE.search(effect_blob):
        faults.append("halation/镜头光晕必须限定为轻微、极轻微、克制或少量")

    if _has_affirmative_match(VOLUMETRIC_LIGHT_RE, effect_blob) and not _has_affirmative_match(SCATTERING_MEDIUM_RE, physical):
        faults.append("丁达尔/体积光/光束缺雾、烟、尘或水汽等散射介质")

    # Scan past time-only prefixes.  A room in “书房门外” describes the
    # building being viewed; an indoor location stays indoor when it also
    # mentions the view through its windows or doors.
    is_indoor = None
    for location_clause in re.split(
        r"[；;。，,\n]", str(shot.get("空间站位描述") or "")
    ):
        location_clause = re.sub(
            rf"(?:{EXPLICIT_INTERIOR_RE.pattern})(?:的)?(?=门外|窗外|外廊|外侧|外面)",
            "",
            location_clause,
        )
        interior = EXPLICIT_INTERIOR_RE.search(location_clause)
        exterior = EXPLICIT_EXTERIOR_RE.search(location_clause)
        if interior or exterior:
            is_indoor = bool(interior and (not exterior or interior.start() < exterior.start()))
            break
    if is_indoor is None:
        is_indoor = bool(EXPLICIT_INTERIOR_RE.search(context))
    has_plot_atmosphere = _has_affirmative_match(PLOT_ATMOSPHERE_RE, context)
    # A lighting description cannot invent the prop that authorizes its own smoke.
    has_atmosphere_source = has_plot_atmosphere or _has_affirmative_match(
        ATMOSPHERE_SOURCE_RE, physical
    )
    has_localized_atmosphere = bool(
        ATMOSPHERE_LOCAL_SCOPE_RE.search(lighting_blob)
        and ATMOSPHERE_NO_SPREAD_RE.search(lighting_blob)
        and BACKGROUND_READABLE_RE.search(lighting_blob)
        and not _has_affirmative_match(GLOBAL_ATMOSPHERE_RE, lighting_blob)
    )
    has_practical = bool(
        _has_affirmative_match(PRACTICAL_LIGHT_RE, source)
        and _has_affirmative_match(PRACTICAL_LIGHT_RE, physical)
    )
    aperture_open = any(
        _has_aperture_state(physical, kind, "开启|打开|敞开|半开")
        for kind in ("门", "窗")
    )
    aperture_closed = any(
        _has_aperture_state(physical, kind, "闭合|关闭|紧闭|关着")
        for kind in ("门", "窗")
    )

    if is_indoor:
        positive_haze = _has_affirmative_match(INDOOR_HAZE_RE, lighting_blob)
        positive_milky_optics = _has_affirmative_match(
            INDOOR_MILKY_OPTICS_RE, effect_blob
        )
        positive_visible_ray = _has_affirmative_match(VISIBLE_RAY_RE, lighting_blob)
        positive_strong_backlight = _has_affirmative_match(
            INDOOR_STRONG_BACKLIGHT_RE, f"{light} {direction}"
        )

        if positive_haze and not has_atmosphere_source:
            faults.append("室内烟雾、细烟或水汽必须由原文/场景中已存在的具体来源支撑，不能由光影字段凭空添加香炉等道具")
        local_practical_halation = bool(
            has_practical
            and LOCAL_HIGHLIGHT_RE.search(light)
            and not _has_affirmative_match(
                re.compile(r"柔焦|镜头扩散|全画面|全屋|整个室内"), effect_blob
            )
        )
        if positive_milky_optics and not local_practical_halation:
            faults.append("普通室内不得用柔焦、镜头扩散或高光晕染制造全画面雾白")
        if positive_visible_ray and not (
            has_atmosphere_source and has_localized_atmosphere and aperture_open
        ):
            faults.append("普通室内不得添加丁达尔光、体积光、光柱或穿插式可见光束")
        if positive_strong_backlight and not (
            has_practical
            or (aperture_open and BACKLIGHT_STORY_NEED_RE.search(context))
        ):
            faults.append("普通室内强逆光、侧逆光或轮廓光须有已开启门窗及明确剧情用途；否则改为前侧/侧面漫射明暗")
        authored_path = f"{source} {direction}"
        if aperture_closed and positive_visible_ray and (
            not aperture_open
            or not any(_aperture_reference_keys(authored_path, kind) for kind in ("门", "窗"))
        ):
            faults.append("门窗闭合不能授权可见光束或窗门光柱；闭合纸窗写窗纸漫射透光，已建立的闭合玻璃窗可透入日间自然光")
        if any(
            _has_aperture_state(authored_path, kind, "开启|打开|敞开|半开")
            for kind in ("门", "窗")
        ) and not aperture_open:
            faults.append("光影字段不得擅自开启场景尚未证明开启的门窗")
        for aperture_kind in ("门", "窗"):
            opened = set(_aperture_state_rows(physical, aperture_kind, "开启|打开|敞开|半开"))
            closed = set(_aperture_state_rows(physical, aperture_kind, "闭合|关闭|紧闭|关着"))
            authored_open = _has_aperture_state(authored_path, aperture_kind, "开启|打开|敞开|半开")
            references = _aperture_reference_keys(authored_path, aperture_kind)
            # An explicitly open right window cannot authorize a closed left
            # window. Unknown identities stay conservative in a mixed scene.
            exact_open_path = (
                bool(references)
                and all(key and key in opened and key not in closed for key in references)
                and "" not in closed
            )
            if closed and not exact_open_path and (
                authored_open or (positive_visible_ray and references)
            ):
                faults.append(f"光影与已有{aperture_kind}的闭合状态冲突；其他开口不能授权本开口透射光束")
        if positive_haze and not has_localized_atmosphere:
            faults.append("室内确有烟雾/扬尘/水汽时也必须限制为局部并保证不扩散、不遮挡、背景可辨")
        if not has_plot_atmosphere and not INDOOR_BACKGROUND_CLARITY_RE.search(
            f"{light} {limits}"
        ):
            faults.append("普通室内必须明确空气清澈、背景结构清晰可辨且不泛白/不雾化")

    if (
        UNIFORM_FACE_LIGHT_RE.search(light)
        and (PRACTICAL_LIGHT_RE.search(source) or MOONLIGHT_SOURCE_RE.search(source))
        and not WEAK_FILL_RE.search(source)
    ):
        faults.append("低照度灯火下的柔和均匀面光缺可解释的弱反射补光")

    if "如梦似幻" in effect_blob:
        visible_mood = re.search(r"低饱和|浅景深|浅笑|垂眸|唇线|色彩", f"{light} {color} {physical}")
        if not visible_mood and not (
            not is_indoor and "真实" in effect_blob and RESTRAINED_EFFECT_RE.search(effect_blob)
        ):
            faults.append("梦幻感须落成可见色彩、景深或人物表演；普通室内不能被要求添加扩散/晕染")

    return faults


def _active_music_directive(value: Any) -> str:
    records = music_directives(value)
    affirmative = [row for row in records if row["status"] == "affirmative"]
    return affirmative[0]["text"] if affirmative else ""


def _strip_negative_music_clauses(value: Any) -> tuple[str, bool]:
    return strip_negative_music(value)


def _protected_dialogue_alignment(
    obj: dict[str, Any], shots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute coverage from canonical utterances, never from counts."""
    try:
        return align_storyboard_dialogue(obj, shots, extract_dialogues)
    except (TypeError, ValueError, KeyError) as exc:
        return {
            "status": "fail_current", "covered_utterances": [],
            "issues": [{"code": "invalid_dialogue_mapping", "detail": str(exc)}],
        }


def _validate_protected_dialogue_alignment(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    result = _protected_dialogue_alignment(obj, shots)
    return [
        _issue(
            catalog, "E-SOURCE-SELECT-001",
            f"protected_dialogue[{fault.get('shot_id') or fault.get('occurrence_id') or index}]",
            f"{fault['code']}: {fault['detail']}",
            template_gate_id="THG-05",
        )
        for index, fault in enumerate(result["issues"])
    ]


def _validate_adapted_dialogue_audit(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    *, production_verified: bool = False, communication_bindings=(),
) -> list[dict[str, Any]]:
    """Validate the source-fact dialogue conversion ledger (THG-16).

    Direct source dialogue remains governed by THG-05.  Any audible line that
    is not one of those exact protected occurrences is an adapted line and
    must be traceable to a narrative source fact without adding plot facts.
    Every in-scope narrative unit receives a scene-semantic decision.  This is
    deliberately broader than verb matching: prose may describe a question /
    report / excuse / verification exchange without quoting a line or using a
    canonical speech verb.  Conversation and mixed-conversation units must be
    realised as dialogue beats instead of silently collapsing into gestures.
    """

    source_lock = obj.get("source_lock")
    enabled = bool(
        isinstance(source_lock, dict)
        and source_lock.get("adapted_dialogue_contract")
        == ADAPTED_DIALOGUE_CONTRACT
    )
    evidence = obj.get("prevention_evidence")
    audit = evidence.get("dialogue_adaptation_audit") if isinstance(evidence, dict) else None
    if not enabled and not isinstance(audit, dict):
        issues = []
        for candidate in _communication_candidates(obj, shots):
            # Exact source-support bindings, actual spoken output and heard
            # receipts are recomputed by production replay. They establish a
            # mapping, never the truth/entailment of the author's source fact.
            source_lines = [line for line in candidate['source_text'].splitlines()
                            if NARRATIVE_SPEECH_ACT_RE.search(NEGATED_SPEECH_ACT_RE.sub('', oral_communication_candidate_text(line)))]
            if production_verified and source_lines and all(any(
                    b['source_text'] == line and (b['shot_id'] in candidate['target_shots'] or any(
                        r.get('source_id') == b['source_id'] and r.get('source_text') == line
                        and b['shot_id'] in r.get('target_shots', [])
                        and set(candidate['target_shots']) & set(r.get('target_shots', []))
                        for r in evidence.get('source_ledger', []) if isinstance(r, dict)))
                    for b in communication_bindings) for line in source_lines):
                continue
            if production_verified and candidate.get("direct_wording_matches") and candidate.get("source_use_ids"):
                continue  # Replayed spoken source use and actual words; no invented adaptation.
            issue = _issue(catalog, "E-ADAPTED-DIALOGUE-001", "source_communication",
                "来源存在无引号会话候选，但缺来源单元到发送者、接收者、实际声音及收到时点的映射；"
                "观众旁白不能自动赋予角色知情：" + candidate["source_text"], template_gate_id="THG-16")
            issue.update(check_status="unknown", check_id="source_communication",
                         shot_ids=list(candidate["target_shots"]))
            issues.append(issue)
        return issues

    issues: list[dict[str, Any]] = []

    def add(location: str, message: str) -> None:
        issues.append(_issue(
            catalog,
            "E-ADAPTED-DIALOGUE-001",
            location,
            message,
            template_gate_id="THG-16",
        ))

    if not enabled:
        add(
            "source_lock.adapted_dialogue_contract",
            f"存在事实转化对白审计，但source_lock未锁定{ADAPTED_DIALOGUE_CONTRACT}。",
        )
    if not isinstance(audit, dict):
        add(
            "prevention_evidence.dialogue_adaptation_audit",
            "启用原文事实转化对白后，必须提交完整逐单元转化审计。",
        )
        return issues
    if audit.get("contract") != ADAPTED_DIALOGUE_CONTRACT:
        add(
            "prevention_evidence.dialogue_adaptation_audit.contract",
            f"事实转化对白契约必须为{ADAPTED_DIALOGUE_CONTRACT}。",
        )

    scope_episode_ids = audit.get("scope_episode_ids")
    if not isinstance(scope_episode_ids, list) or not scope_episode_ids:
        add(
            "prevention_evidence.dialogue_adaptation_audit.scope_episode_ids",
            "事实转化对白审计必须声明至少一个授权集范围。",
        )
        scope_episode_ids = []
    else:
        scope_episode_ids = [str(value).strip() for value in scope_episode_ids]
        if any(not re.fullmatch(r"EP\d{2,}", value) for value in scope_episode_ids):
            add(
                "prevention_evidence.dialogue_adaptation_audit.scope_episode_ids",
                f"授权集范围必须使用EP##格式：{scope_episode_ids}",
            )
    scope_episode_set = set(scope_episode_ids)

    def in_scope(shot_id: str) -> bool:
        episode_id = shot_id.split("-", 1)[0]
        return episode_id in scope_episode_set

    by_shot: dict[str, dict[str, Any]] = {}
    ordered_shot_ids: list[str] = []
    actual_dialogues: list[tuple[str, str, str]] = []
    actual_dialogue_refs: list[tuple[str, int]] = []
    for shot in shots:
        shot_id = str(shot.get("镜头编号") or shot.get("shot_uid") or "").strip()
        if shot_id:
            ordered_shot_ids.append(shot_id)
            by_shot[shot_id] = shot
        if in_scope(shot_id):
            for dialogue_index, (speaker, text) in enumerate(extract_dialogues(shot.get("画面内容"))):
                actual_dialogues.append((shot_id, speaker, text))
                actual_dialogue_refs.append((shot_id, dialogue_index))

    direct_coverage = _protected_dialogue_alignment(obj, shots)
    direct_refs = {
        (item["shot_id"], item["dialogue_index"])
        for item in direct_coverage["covered_utterances"]
    }

    protected_rows: list[dict[str, Any]] = []
    if isinstance(obj.get("protected_dialogue"), list):
        protected_rows.extend(row for row in obj["protected_dialogue"] if isinstance(row, dict))
    source_ingestion = obj.get("source_ingestion")
    if isinstance(source_ingestion, dict) and isinstance(source_ingestion.get("protected_dialogue"), list):
        protected_rows.extend(
            row for row in source_ingestion["protected_dialogue"] if isinstance(row, dict)
        )
    protected_occurrences: set[tuple[str, str, str]] = set()
    protected_target_refs: set[str] = set()
    for row in protected_rows:
        speaker = str(row.get("speaker") or "").strip()
        text = str(row.get("text") or "").strip()
        targets = row.get("mapped_shot_uids") or row.get("target_shots") or []
        if not isinstance(targets, list):
            continue
        for target in targets:
            ref = str(target).strip()
            if ref:
                protected_target_refs.add(ref)
                protected_occurrences.add((ref, speaker, text))

    source_ledger = evidence.get("source_ledger") if isinstance(evidence, dict) else None
    expected_candidates: list[dict[str, Any]] = []
    if isinstance(source_ledger, list):
        for index, row in enumerate(source_ledger, 1):
            if not isinstance(row, dict):
                continue
            source_text = str(row.get("source_text") or "").strip()
            source_type = str(row.get("source_type") or "").strip()
            targets = [
                str(value).strip()
                for value in (row.get("target_shots") or [])
                if str(value).strip()
            ]
            if not any(in_scope(target) for target in targets):
                continue
            if any(not in_scope(target) for target in targets):
                expected_candidates.append({
                    "source_ledger_index": index,
                    "source_unit_id": str(row.get("source_id") or "").strip(),
                    "source_text": source_text,
                    "target_shots": targets,
                    "mandatory": True,
                    "scope_crossing": True,
                })
                continue
            has_protected_target = bool(set(targets) & protected_target_refs)
            positive_speech_text = NEGATED_SPEECH_ACT_RE.sub(
                "", oral_communication_candidate_text(source_text)
            )
            speech_cue = bool(
                NARRATIVE_SPEECH_ACT_RE.search(positive_speech_text)
                or re.search(r"朗读|朗诵|读出|念出", positive_speech_text)
                or re.search(r"改编完整展开对白|保护对白完整展开", source_type)
            )
            # A protected quotation elsewhere in this shot cannot discharge a
            # separate unquoted communication in the same source/target unit.
            if has_protected_target and not _communication_candidates({}, [{
                "镜头编号": targets[0] if targets else "", "原文支撑句": source_text}]):
                continue
            expected_candidates.append({
                "source_ledger_index": index,
                "source_unit_id": str(row.get("source_id") or "").strip(),
                "source_text": source_text,
                "target_shots": targets,
                "speech_cue": speech_cue,
                "scope_crossing": False,
            })

    entries = audit.get("entries")
    if not isinstance(entries, list):
        add(
            "prevention_evidence.dialogue_adaptation_audit.entries",
            "事实转化对白审计entries必须是数组。",
        )
        entries = []

    required_fields = {
        "adaptation_id", "source_ledger_index", "source_unit_id", "source_text",
        "dialogue_affordance", "treatment", "speaker", "adapted_text",
        "target_shots", "fact_atoms", "speaker_basis", "authorization",
        "plot_change", "new_plot_facts", "entailment_status", "adaptation_reason",
        "scene_semantics", "communication_functions", "exchange_participants",
        "speech_necessity_basis",
    }
    candidate_keys = [
        (row["source_ledger_index"], row["source_unit_id"], tuple(row["target_shots"]))
        for row in expected_candidates
    ]
    entry_keys: list[tuple[int, str, tuple[str, ...]]] = []
    mapped_adapted: list[tuple[str, str, str]] = []
    adaptation_ids: list[str] = []
    seen_adaptation_ids: set[str] = set()
    for index, row in enumerate(entries, 1):
        location = f"prevention_evidence.dialogue_adaptation_audit.entries[{index}]"
        if not isinstance(row, dict):
            add(location, "事实转化对白审计项必须是对象。")
            continue
        missing = sorted(required_fields - set(row))
        if missing:
            add(location, f"事实转化对白审计项缺字段：{missing}")
            continue
        adaptation_id = str(row.get("adaptation_id") or "").strip()
        if not adaptation_id or adaptation_id in seen_adaptation_ids:
            add(location, "adaptation_id必须非空且全局唯一。")
        else:
            seen_adaptation_ids.add(adaptation_id)
            adaptation_ids.append(adaptation_id)
        try:
            source_index = int(row.get("source_ledger_index"))
        except (TypeError, ValueError):
            source_index = -1
        source_unit_id = str(row.get("source_unit_id") or "").strip()
        targets = row.get("target_shots")
        if not isinstance(targets, list) or not targets:
            add(location, "target_shots必须是至少含一个镜头的有序数组。")
            target_refs: list[str] = []
        else:
            target_refs = [str(value).strip() for value in targets]
            if any(not value or value not in by_shot for value in target_refs):
                add(location, f"target_shots含空值或不存在镜头：{target_refs}")
        entry_keys.append((source_index, source_unit_id, tuple(target_refs)))
        matching_candidate = next(
            (
                candidate for candidate in expected_candidates
                if candidate["source_ledger_index"] == source_index
                and candidate["source_unit_id"] == source_unit_id
                and candidate["target_shots"] == target_refs
            ),
            None,
        )
        if matching_candidate is None:
            add(location, "审计项未精确绑定source_ledger中的候选单元、顺序和目标镜头。")
        elif str(row.get("source_text") or "") != matching_candidate["source_text"]:
            add(location, "source_text必须逐字绑定source_ledger原文事实。")
        if matching_candidate and matching_candidate.get("scope_crossing"):
            add(location, "同一事实转化候选不得跨越授权集范围；请先拆分source_ledger单元。")

        affordance = str(row.get("dialogue_affordance") or "").strip()
        treatment = str(row.get("treatment") or "").strip()
        if affordance not in ADAPTED_DIALOGUE_AFFORDANCE:
            add(location, f"dialogue_affordance非法：{affordance!r}")
        if treatment not in ADAPTED_DIALOGUE_TREATMENTS:
            add(location, f"treatment非法：{treatment!r}")
        scene_semantics = str(row.get("scene_semantics") or "").strip()
        if scene_semantics not in NARRATIVE_SCENE_SEMANTICS:
            add(location, f"scene_semantics非法：{scene_semantics!r}")
        communication_functions = row.get("communication_functions")
        if not isinstance(communication_functions, list):
            add(location, "communication_functions必须是有序数组。")
            communication_functions = []
        invalid_functions = [
            value for value in communication_functions
            if str(value).strip() not in COMMUNICATION_FUNCTIONS
        ]
        if invalid_functions:
            add(location, f"communication_functions含非法值：{invalid_functions}")
        participants = row.get("exchange_participants")
        if not isinstance(participants, list):
            add(location, "exchange_participants必须是有序数组。")
            participants = []
        if not str(row.get("speech_necessity_basis") or "").strip():
            add(location, "speech_necessity_basis不能为空，必须说明本叙述为何需要或不需要人物开口。")

        conversation_scene = scene_semantics in {"叙述性会话场景", "混合会话场景"}
        source_limited = treatment == SOURCE_LIMIT_TREATMENT
        if matching_candidate and matching_candidate.get("speech_cue") and not conversation_scene:
            add(location, "原文已发生言语行为，不得判为非会话叙述。")
        if conversation_scene:
            if not source_limited and (affordance != "必须转对白" or treatment != "原文事实转化对白"):
                add(location, "会话或混合会话场景必须还原为对白，不得降级为无声动作。")
            if not communication_functions:
                add(location, "会话场景必须列出问答、劝决、令应、报听、辩核或证判等交流功能。")
            if len([value for value in participants if str(value).strip()]) < 2:
                add(location, "会话场景必须列出至少两个交流参与方（说话者与接收者）。")
        elif treatment == "原文事实转化对白":
            add(location, "采用原文事实转化对白时，scene_semantics不得标为非会话叙述。")

        if row.get("plot_change") is not False:
            add(location, "plot_change必须明确为false。")
        if row.get("new_plot_facts") != []:
            add(location, "new_plot_facts必须为空数组；事实转化对白不得新增剧情事实。")
        expected_entailment = SOURCE_LIMIT_ENTAILMENT if source_limited else "entailed_by_source"
        if str(row.get("entailment_status") or "") != expected_entailment:
            add(location, f"entailment_status必须为{expected_entailment}。")
        if not source_limited and (affordance == SOURCE_LIMIT_AFFORDANCE or "source_content_limit" in row):
            add(location, "来源未给内容接口仅可用于其专属客观叙述处理，不得豁免普通对白。")
        if not isinstance(row.get("fact_atoms"), list) or not row.get("fact_atoms"):
            add(location, "fact_atoms必须逐项列出对白承载的原文事实。")
        for field in ("speaker_basis", "authorization", "adaptation_reason"):
            if not str(row.get(field) or "").strip():
                add(location, f"{field}不能为空。")

        if treatment == "原文事实转化对白":
            speaker = str(row.get("speaker") or "").strip()
            adapted_text = str(row.get("adapted_text") or "").strip()
            if not speaker or not adapted_text:
                add(location, "事实转化对白必须填写speaker和adapted_text。")
            occurrences = [
                (shot_id, actual_speaker, actual_text)
                for shot_id, actual_speaker, actual_text in actual_dialogues
                if shot_id in target_refs
                and actual_speaker == speaker
                and actual_text == adapted_text
            ]
            if len(occurrences) != 1:
                add(
                    location,
                    "事实转化对白必须在目标镜头中恰好出现一次，且说话人、正文和标点完全一致。",
                )
            else:
                mapped_adapted.extend(occurrences)
        else:
            if str(row.get("speaker") or "").strip() or str(row.get("adapted_text") or "").strip():
                add(location, "非对白处理不得填写speaker或adapted_text。")
            if affordance == "建议转对白" and not str(row.get("non_dialogue_reason") or "").strip():
                add(location, "建议转对白但未采用时必须填写non_dialogue_reason。")

    if entry_keys != candidate_keys:
        add(
            "prevention_evidence.dialogue_adaptation_audit.entries",
            "entries必须按source_ledger顺序100%覆盖全部叙述性说话候选，且不得多项、少项或乱序。",
        )

    source_limits = inspect_source_content_limits(obj, shots, protected_alignment=direct_coverage)
    for fault in source_limits["issues"]:
        add("prevention_evidence.dialogue_adaptation_audit.source_content_partitions", fault)

    actual_adapted = [
        occurrence for occurrence, ref in zip(actual_dialogues, actual_dialogue_refs)
        if ref not in direct_refs
    ]
    if mapped_adapted != actual_adapted:
        add(
            "prevention_evidence.dialogue_adaptation_audit.entries",
            "所有非原文直接对白必须按成片顺序逐条进入事实转化台账；不得漏记、重复或把原文原句误标为改编对白。",
        )

    expected_count = len(expected_candidates)
    reviewed_count = len(entries)
    adapted_count = sum(
        1 for row in entries
        if isinstance(row, dict) and row.get("treatment") == "原文事实转化对白"
    )
    conversation_scene_count = sum(
        1 for row in entries
        if isinstance(row, dict)
        and row.get("scene_semantics") in {"叙述性会话场景", "混合会话场景"}
    )
    metrics = {
        "candidate_unit_count": expected_count,
        "reviewed_unit_count": reviewed_count,
        "adapted_dialogue_count": adapted_count,
        "conversation_scene_count": conversation_scene_count,
        "scene_classification_count": reviewed_count,
        "coverage_percent": 100,
        "ordered_entry_sha256": _stable_sha256(adaptation_ids),
    }
    for field, expected in metrics.items():
        if audit.get(field) != expected:
            add(
                f"prevention_evidence.dialogue_adaptation_audit.{field}",
                f"事实转化对白审计汇总不闭合：expected={expected!r}, actual={audit.get(field)!r}。",
            )
    return issues


def _missing_fields(obj: Any, fields: list[str], *, explicit_empty_lists: tuple[str, ...] = ()) -> list[str]:
    if not isinstance(obj, dict):
        return list(fields)
    return [field for field in fields if field not in obj or (
        is_empty(obj.get(field))
        and not (field in explicit_empty_lists and isinstance(obj[field], list) and not obj[field])
    )]


def _as_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _matched_pattern_names(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    return {name for name, pattern in patterns.items() if affirmative_matches(pattern, text)}


def _public_audio_layers(text: str) -> set[str]:
    """A distance label needs an audible source in its own clause.

    Merely listing 近层/中层/远层 or explicitly absent sounds supplies no
    evidence. This is a bounded text check, not a listening/quality verdict.
    """
    layers = set()
    for clause in _public_audible_clauses(text):
        if not _matched_pattern_names(clause, PUBLIC_AUDIO_FAMILY_PATTERNS):
            continue
        layers.update(_matched_pattern_names(clause, PUBLIC_AUDIO_LAYER_PATTERNS))
    return layers


def _public_audible_clauses(text: str) -> list[str]:
    absent = re.compile(r'(?:已|已经|完全)?(?:停止|消失|消音|静音|停歇)|'
                        r'(?:没有|无)(?:任何)?(?:声音|声响)|听不(?:见|到)|不可(?:听见|听到|闻)')
    clauses = re.split(r'[，,。；;\n]|但是|然而|但|却|随后|然后', text)
    # A shared final absence predicate applies to the entire coordinated list.
    # Unsupported mixed clauses remain conservative rather than supplying proof.
    return [clause for clause in clauses if not affirmative_matches(absent, clause)]


def _public_audio_families(text: str) -> set[str]:
    return {name for clause in _public_audible_clauses(text)
            for name in _matched_pattern_names(clause, PUBLIC_AUDIO_FAMILY_PATTERNS)}


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
        # “无剧情任务的行人” negates a narrative role, not their walking.
        # Mask this bounded noun modifier only; “行人不经过” stays negative.
        action_text = re.sub(r'无(?:剧情|主线)(?:任务|作用)(?:的)?',
                             lambda m: ' ' * len(m.group()), visual_text)
        action_ok = bool(affirmative_matches(PUBLIC_ACTION_RE, action_text))
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
        audio_families = _public_audio_families(audio_text)
        audio_layers = _public_audio_layers(audio_text)
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
    if not chapter_required(obj):
        return issues, metrics

    if not isinstance(intake, dict):
        intake = {}
    if intake.get('mode') != 'chapter_to_storyboard':
        issues.append(_issue(catalog, 'E-CHAPTER-INTAKE-001', 'source_ingestion.mode',
                             '章级来源或边界已声明，不能改mode或移除整章证据来跳过检查。'))

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
    budget = intake.get("adaptation_budget")
    chapter_timing = timing_model.chapter_budget(
        protected, rules=rules, project=obj,
        budget=budget if isinstance(budget, dict) else None,
    )
    protected_required_seconds = chapter_timing["required_dialogue_seconds"]
    for fault in chapter_timing["faults"]:
        issues.append(_issue(
            catalog, "E-CHAPTER-BUDGET-001",
            fault.get("location", "source_ingestion.adaptation_budget"),
            fault["message"],
        ))
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
        component_sum = chapter_timing["required_total_seconds"]
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


def dialogue_mouth_visible(speaker: str, shot: dict[str, Any]) -> bool:
    """Only explicit, person-bound framing exempts a speaker from lip sync."""
    name = re.escape(speaker)
    framing = "；".join(str(shot.get(key) or "") for key in
                       ("可见主体", "空间站位描述", "机位", "画面内容"))
    framing = re.sub(r'[“"].*?[”"]', '', framing, flags=re.S)
    boundary = r"(?:^|[；，。：:\n【】])\s*"
    offscreen = re.search(rf"{boundary}{name}(?:[（(：:\s]*)(?:画外发言|画外音|画外[）)]|不入画)", framing)
    cropped = re.search(rf"{boundary}{name}(?:的)?(?:嘴部|口部|口型)(?:完全)?不入画", framing)
    return not bool(offscreen or cropped)


def _validate_preventive_gates(
    obj: dict[str, Any],
    shots: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    mode: str,
    rules: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    production_binding: dict[str, Any] | None = None,
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
    delivered_blob = '\n'.join(delivery_text(shot) for shot in shots)
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
            if treatment in {"授权删除", "借用"} and is_none_value(row.get("authorization")):
                issues.append(_issue(catalog, "E-SOURCE-005", location, f"{treatment}缺用户授权证据。"))
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
        missing_required = [str(term) for term in change_lock["must_include"] if str(term).strip() and str(term) not in delivered_blob]
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
            # [] explicitly means no held props in the typed/canonical state
            # contract; missing/null/empty objects and other empty state fields
            # still fail. The prop checker below validates nonempty prop rows.
            missing = _missing_fields(row.get(label), state_fields, explicit_empty_lists=("手持道具",))
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
    expected_pair_rows: list[dict[str, Any]] = []
    for ordinal in range(max(len(shots) - 1, 0)):
        left_shot = shots[ordinal]
        right_shot = shots[ordinal + 1]
        left_id = str(left_shot.get("镜头编号"))
        right_id = str(right_shot.get("镜头编号"))
        pair_id = f"{left_id}->{right_id}"
        left_row = state_by_shot.get(left_id)
        right_row = state_by_shot.get(right_id)
        left_scene = str(
            (left_row or {}).get("scene_id", left_shot.get("场景连续性ID", ""))
        )
        right_scene = str(
            (right_row or {}).get("scene_id", right_shot.get("场景连续性ID", ""))
        )
        left_episode = _shot_episode_key(left_shot, ordinal)
        right_episode = _shot_episode_key(right_shot, ordinal + 1)
        boundary_type = (
            "episode_cut"
            if left_episode != right_episode
            else "scene_cut"
            if left_scene != right_scene
            else "same_scene"
        )
        prior_end = (
            left_row.get("end_state", {})
            if isinstance(left_row, dict)
            else {}
        )
        current_start = (
            right_row.get("start_state", {})
            if isinstance(right_row, dict)
            else {}
        )
        explanation = (
            right_row.get("transition_explanation")
            if isinstance(right_row, dict)
            else None
        )
        mismatches = {
            field: [prior_end.get(field), current_start.get(field)]
            for field in state_fields
            if not state_equal(prior_end.get(field), current_start.get(field))
        }
        pair_status = "closed"
        if left_row is None or right_row is None:
            pair_status = "fail_current"
            issues.append(_issue(
                catalog,
                "E-CONTINUITY-001",
                f"adjacent_pair:{pair_id}",
                "相邻镜头缺左镜end_state或右镜start_state，无法完成逐组连续性审计。",
                template_gate_id="THG-14",
            ))
        elif boundary_type == "same_scene":
            if mismatches:
                pair_status = "fail_current"
                issues.append(_issue(
                    catalog,
                    "E-CONTINUITY-001",
                    f"adjacent_pair:{pair_id}",
                    f"同场景相邻镜头首尾状态必须逐字段闭合（仅接受封闭词表同义状态），说明不能替代切点状态：{mismatches}",
                    template_gate_id="THG-14",
                ))
        else:
            pair_status = "explained_transition"
            if is_none_value(explanation) or not BOUNDARY_EXPLANATION_RE.search(
                str(explanation)
            ):
                pair_status = "fail_current"
                issues.append(_issue(
                    catalog,
                    "E-CONTINUITY-001",
                    f"adjacent_pair:{pair_id}",
                    f"{boundary_type}边界缺明确的时空/场景切换说明：{explanation!r}",
                    template_gate_id="THG-14",
                ))
        expected_pair_rows.append({
            "pair_id": pair_id,
            "left_shot_id": left_id,
            "right_shot_id": right_id,
            "boundary_type": boundary_type,
            "left_end_state_sha256": _stable_sha256(prior_end),
            "right_start_state_sha256": _stable_sha256(current_start),
            "reviewed_fields": list(state_fields),
            "continuity_status": pair_status,
            "transition_explanation": explanation,
        })

    pair_ids = [row["pair_id"] for row in expected_pair_rows]
    expected_pair_hash = _stable_sha256(pair_ids)
    submitted_pair_audit = evidence.get("adjacent_continuity_audit")
    pair_audit_faults: list[str] = []
    if not isinstance(submitted_pair_audit, dict):
        pair_audit_faults.append("缺adjacent_continuity_audit对象")
        submitted_pairs: list[Any] = []
    else:
        submitted_pairs = submitted_pair_audit.get("pairs")
        if not isinstance(submitted_pairs, list):
            pair_audit_faults.append("pairs必须为数组")
            submitted_pairs = []
        scalar_expectations = {
            "contract": "adjacent-pair-continuity-v1",
            "expected_pair_count": len(expected_pair_rows),
            "reviewed_pair_count": len(expected_pair_rows),
            "coverage_percent": 100,
            "ordered_pair_sha256": expected_pair_hash,
        }
        for field, expected_value in scalar_expectations.items():
            if submitted_pair_audit.get(field) != expected_value:
                pair_audit_faults.append(
                    f"{field}={submitted_pair_audit.get(field)!r}，应为{expected_value!r}"
                )
    if len(submitted_pairs) != len(expected_pair_rows):
        pair_audit_faults.append(
            f"pairs数量={len(submitted_pairs)}，应完整覆盖N-1={len(expected_pair_rows)}"
        )
    for ordinal, expected_row in enumerate(expected_pair_rows):
        if ordinal >= len(submitted_pairs):
            break
        submitted_row = submitted_pairs[ordinal]
        if not isinstance(submitted_row, dict):
            pair_audit_faults.append(f"pairs[{ordinal}]不是对象")
            continue
        differing = {
            field: [submitted_row.get(field), expected_value]
            for field, expected_value in expected_row.items()
            if submitted_row.get(field) != expected_value
        }
        if differing:
            pair_audit_faults.append(
                f"pairs[{ordinal}] {expected_row['pair_id']}证据不一致：{differing}"
            )
    if pair_audit_faults:
        issues.append(_issue(
            catalog,
            "E-CONTINUITY-001",
            "prevention_evidence.adjacent_continuity_audit",
            "相邻镜头逐组连续性证明不完整：" + "；".join(pair_audit_faults),
            repair="按成片顺序重建全部N-1对证据，逐对绑定左镜end_state、右镜start_state、七项字段、边界类型、说明与有序SHA-256。",
            template_gate_id="THG-14",
        ))
    metrics["adjacent_continuity"] = {
        "contract": "adjacent-pair-continuity-v1",
        "expected_pair_count": len(expected_pair_rows),
        "reviewed_pair_count": len(expected_pair_rows),
        "coverage_percent": 100.0,
        "ordered_pair_sha256": expected_pair_hash,
        "pair_ids": pair_ids,
        "pair_results": expected_pair_rows,
        "failed_pair_count": sum(
            row["continuity_status"] == "fail_current"
            for row in expected_pair_rows
        ),
        "proof_verified": not pair_audit_faults,
    }

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
    from performance_evidence import chain_relevant, inspect_performance_evidence
    performance = inspect_performance_evidence(obj, production_binding or {})
    derived_by_shot = {row['shot_id']: row for row in performance['shots']}
    metrics['performance_evidence'] = {key: value for key, value in performance.items() if key != 'shots'}
    if performance['present'] and not performance['verified']:
        issues.append(_issue(catalog, "E-PERFORMANCE-001", "prevention_evidence.performance_evidence",
                             "派生表演证据未通过真实输入、来源计划、动作与状态重放；不能替代原表演链门禁。"))
    for shot in shots:
        shot_id = str(shot.get("镜头编号"))
        dialogue = " ".join(line for _, line in extract_dialogues(shot.get("画面内容")))
        core = str(shot.get("功能类型", ""))
        relevant = chain_relevant(core, not is_none_value(dialogue))
        if not relevant:
            continue
        derived = derived_by_shot.get(shot_id)
        if performance['verified'] and derived is not None:
            unknown = [f"{actor['actor']}.{name}" for actor in derived['actors']
                       for name in chain_fields if actor['fields'].get(name, {}).get('evidence_status')
                       not in {'bound', 'not_applicable'}]
            if unknown:
                issues.append(_issue(catalog, "E-PERFORMANCE-001", f"performance_chain:{shot_id}",
                    f"重放表演证据仍未知：{unknown}；补实际来源/状态依据，不要求新增七种动作。"))
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
        for finding in field_content_findings(
            shot, mask=context_instruction_text,
            dialogue_rows=extract_dialogues(str(shot.get("画面内容") or "")),
        ):
            issues.append(_issue(catalog, finding["code"], location + "." + finding["path"],
                                 finding["message"] + "：" + finding["excerpt"],
                                 repair=finding["repair"], template_gate_id="THG-17"))
        missing = _missing_fields(shot, independent_fields)
        if missing:
            issues.append(_issue(catalog, "E-I2V-INDEP-002", location, f"紧凑母版无法空白上下文独立执行，缺字段：{missing}"))
        master_text = " ".join(context_instruction_text(str(shot.get(field, "")), field)
                               for field in EXECUTION_SHOT_FIELDS)
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
            required = ["round", "initial_issue_count", "residual_issue_count", "introduced_issue_count", "repeat_rounds", "action"]
            pending_origin = (row.get("introduced_issue_count") is None
                              and row.get("introduced_count_state") == "attribution_pending")
            missing = _missing_fields(row, [k for k in required if not (pending_origin and k == "introduced_issue_count")])
            if missing:
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", f"修正记录缺字段：{missing}"))
                continue
            invalid_counts = [k for k in required if k not in {"action"}
                              and not (pending_origin and k == "introduced_issue_count")
                              and (type(row.get(k)) is not int or row[k] < (1 if k in {"round", "repeat_rounds"} else 0))]
            if invalid_counts:
                issues.append(_issue(catalog, "E-GATE-001", f"repair_history[{index}]", f"修正计数必须为合法整数：{invalid_counts}"))
                continue
            initial = _as_number(row.get("initial_issue_count"))
            residual = _as_number(row.get("residual_issue_count"))
            introduced = _as_number(row.get("introduced_issue_count"))
            if index != len(repair_history):
                continue
            resolved = max(initial - residual, 0.0)
            metrics["historical_repair_observation_count"] = len(repair_history) - 1
            if initial > 0:
                metrics["first_repair_effectiveness_percent"] = round(100.0 * resolved / initial, 2)
                metrics["residual_rate_percent"] = round(100.0 * residual / initial, 2)
            else:
                metrics["first_repair_effectiveness_percent"] = None
                metrics["residual_rate_percent"] = None
            metrics["side_effect_rate_percent"] = (None if pending_origin else
                round(100.0 * introduced / max(resolved, 1.0), 2))
            # Historical counts remain observations. Only the latest row can
            # describe the current repair, and even it cannot prove a pass:
            # validate_project verifies its binding against freshly run checks.
            if residual > 0:
                issues.append(_issue(catalog, "E-REGRESSION-001", f"repair_history[{index}]", f"初审={initial:g}, 修后残留={residual:g}"))
            if introduced > 0:
                issues.append(_issue(catalog, "E-REGRESSION-002", f"repair_history[{index}]", f"修正新增问题={introduced:g}"))
            repeat_rounds = int(_as_number(row.get("repeat_rounds")))
            if repeat_rounds >= 2 and residual > 0:
                issues.append(_issue(catalog, "E-SYSTEMIC-001", f"repair_history[{index}]", f"连续{repeat_rounds}轮仍有已观测残留，须按实际根因与依赖修正；轮数不强制重建或停止。"))

    return issues, metrics


def _semantic_check(check_id, shot, status, reason, *, evidence=None, fields=None, findings=None):
    layers = {"saved_projection_format": "正式母版单镜执行完整性",
              "saved_keyframe_static": "正式母版单镜执行完整性", "saved_initial_state": "空间连续",
              "saved_visible_capacity": "机位可见性", "saved_light_consistency": "空间连续",
              "saved_projection_binding": "正式母版单镜执行完整性", "utterance_delivery_binding": "禁项与声音",
              "environment_actor_phase": "空间连续", "music_semantic_scope": "禁项与声音"}
    return {"check_id": check_id, "layer": layers[check_id], "status": status, "reason": reason,
            "shot_ids": [str(shot.get("镜头编号") or "")],
            "evidence": evidence or {},
            "field_sha256": {key: field_sha256(value) for key, value in (fields or {}).items()},
            "findings": findings or []}


def _semantic_entities(shot):
    from visible_entities import resolve_visible_subjects
    return resolve_visible_subjects(shot.get("可见主体"),
        speakers=[row[0] for row in extract_dialogues(shot.get("画面内容"))],
        evidence_texts=[shot.get(field) for field in ("起始状态", "画面内容", "光影", "光源方向")])


def validate_saved_projection(shot, saved_fields, *, bound_canonical=False,
                              projection_contract_verified=None):
    """Inspect actual workbook E/F/K before any optional deterministic comparison.

    The caller supplies the original cell strings, not freshly generated cells.
    A pass certifies only the named bounded check; source entailment and full
    scene geometry require their separate production/independent evidence.
    """
    from field_content import field_policy
    checks = []
    actual = {key: saved_fields[key] for key in ("正式分镜母版", "GPT关键帧生成指令", "光影")
              if key in saved_fields}
    f = actual.get("GPT关键帧生成指令")
    labels = [row["label"] for row in field_policy()["keyframe_sections"]]
    required = [row["label"] for row in field_policy()["keyframe_sections"] if not row.get("optional")]
    sections, duplicates = labelled_sections(f, labels)
    missing = [label for label in required if not sections.get(label)]
    format_faults = (["缺关键帧段：" + "、".join(missing)] if missing else [])
    if duplicates:
        format_faults.append("关键帧段重复：" + "、".join(duplicates))
    checks.append(_semantic_check("saved_projection_format", shot,
        "unknown" if f is None else "fail" if format_faults else "pass",
        "实际F固定字段结构；格式与内容语义分开", fields=actual, findings=format_faults))
    if f is None:
        checks.append(_semantic_check("saved_keyframe_static", shot, "unknown", "缺实际F原始单元格", fields=actual))
    else:
        hits = sorted(set(_keyframe_forbidden_hits(f) + _keyframe_environment_process_hits(f)
                          + _keyframe_eye_process_hits(f) + _keyframe_authored_process_hits(f)
                          + _keyframe_unobservable_hits(f)))
        context = [term for term in CROSS_SHOT_TERMS if term in _keyframe_instruction_text(f)]
        # A framing purpose covering several endpoints is not a static pose.
        process = re.findall(r'(?:从|由)[^，。；;\n]{1,32}(?:到|至)[^，。；;\n]{1,20}(?:全过程|全程)|'
                             r'(?:起身|屈膝|坐下|落座|接物|取物|抱起|跌倒)后(?:的)?[^，。；;\n]{0,12}(?:接触|坐稳|站稳|落地)',
                             sections.get("机位", ""))
        findings = hits + ["跨镜代指未展开：" + term for term in context] + process
        checks.append(_semantic_check("saved_keyframe_static", shot, "fail" if findings else "pass",
            "检查实际F的静态指令、当前时刻和独立定位", fields=actual, findings=findings))

    entities = _semantic_entities(shot)
    names = list(entities.people)
    conflicts = explicit_state_conflicts(shot.get("起始状态"), str(f or ''), names)
    checks.append(_semantic_check("saved_initial_state", shot,
        "fail" if conflicts else "unknown" if f is None or not shot.get("起始状态") or entities.faults else "pass",
        "仅比较实际E/F明确逐人起态；不推定未写明的姿态", fields=actual, findings=conflicts))
    visible = sections.get("镜头类型及可见主体", "")
    from visible_entities import resolve_visible_subjects
    resolution = resolve_visible_subjects(visible.split("；", 1)[-1], people=names, evidence_texts=[f])
    actual_names = list(resolution.people)
    capacity = [f"实际F主要可见人物{len(actual_names)}人，超过3人；须重选机位，不得删除世界人物"] if len(actual_names) > 3 else []
    checks.append(_semantic_check("saved_visible_capacity", shot,
        "fail" if capacity else "unknown" if not visible or resolution.faults else "pass", "实际F主要人物容量", fields=actual, findings=capacity + resolution.faults))
    light = actual.get("光影")
    k_sections, k_duplicates = labelled_sections(light, ("光影", "光源来源", "光源方向", "色温倾向"))
    lighting_faults = []
    lighting_unknown = light is None or not k_sections
    for label in ("光影", "光源来源", "光源方向", "色温倾向"):
        left, right = sections.get(label), k_sections.get(label)
        if left and right and _normalized_clause(left) != _normalized_clause(right):
            lighting_faults.append(f"实际F/K的{label}不一致，须从同一实体光源和当前机位重建")
        elif not left or not right:
            lighting_unknown = True
    if k_duplicates:
        lighting_faults.append("实际K光影子项重复：" + "、".join(k_duplicates))
    checks.append(_semantic_check("saved_light_consistency", shot,
        "fail" if lighting_faults else "unknown" if lighting_unknown else "pass",
        "比较实际F/K光影；缺K的旧10列仍保留原合同，不伪造K", fields=actual, findings=lighting_faults))
    projection_faults = []
    if bound_canonical and not projection_contract_verified:
        for field, compiler in (("正式分镜母版", project_formal_master), ("GPT关键帧生成指令", project_gpt_keyframe),
                                ("光影", lambda row: project_lighting(row, _projection_text))):
            try:
                if field not in actual or actual[field] != compiler(shot):
                    projection_faults.append(field + "实际保存字节与已绑定canonical投影不同")
            except (ValueError, TypeError) as exc:
                projection_faults.append(field + "绑定投影无法比较：" + str(exc))
    checks.append(_semantic_check("saved_projection_binding", shot,
        "fail" if projection_faults else "pass" if bound_canonical or projection_contract_verified else "unknown",
        "服务端已按原投影合同验证旧工作簿完整性；当前语义单独检查，不重投影原件" if projection_contract_verified else
        "已绑定canonical字节比对" if bound_canonical else "无已验证canonical绑定；实际文本检查不补生产证明",
        evidence={"verified_projection_contract": projection_contract_verified} if projection_contract_verified else {},
        fields=actual, findings=projection_faults))
    return checks


def _communication_candidates(obj, shots):
    """Source content, not an author's workflow switch, creates the obligation."""
    evidence = obj.get("prevention_evidence") or {}
    ledger = evidence.get("source_ledger") if isinstance(evidence, dict) else None
    records = [row for row in (ledger or []) if isinstance(row, dict)] if isinstance(ledger, list) else []
    records += [{"source_text": row.get("原文支撑句"), "target_shots": [row.get("镜头编号")]} for row in shots]
    actual_by_shot = {str(row.get("镜头编号")): extract_dialogues(row.get("画面内容")) for row in shots}
    uses = timing_model.source_uses(obj)
    candidates = {}
    for row in records:
        source = str(row.get("source_text") or "")
        targets = row.get("target_shots")
        targets = [target for target in targets if isinstance(target, str)] if isinstance(targets, list) else []
        # A colon-delimited direct source line has supplied audible contents
        # even without typography quotes. Only the exact current speaker/text
        # closes this route; another quote elsewhere in the shot cannot.
        direct_rows = [line for target in targets for line in actual_by_shot.get(target, [])]
        direct_matches = [(speaker, line) for speaker, line in direct_rows if re.fullmatch(
            re.escape(speaker) + r'(?:说道|说|问道|问|答道|答|喊道)\s*[:：]\s*' + re.escape(line), source.strip())]
        unquoted = re.sub(r'(?:[^，。；;\n]{0,12}(?:' + NARRATIVE_SPEECH_ACT_RE.pattern + r')[:：]?)?[“「][^”」]*[”」]', '', source)
        unquoted = NEGATED_SPEECH_ACT_RE.sub('', oral_communication_candidate_text(unquoted))
        if NARRATIVE_SPEECH_ACT_RE.search(unquoted):
            candidates[(source, tuple(targets))] = {"source_text": source, "target_shots": targets,
                "source_unit_id": row.get("source_id"), "direct_wording_matches": bool(direct_matches),
                "source_use_ids": [identity for identity, use in uses.items() if isinstance(use, dict)
                    and use.get('mode') == 'spoken' and (use.get('actor'), use.get('text')) in direct_matches]}
    return list(candidates.values())


def _shot_semantic_checks(shot):
    from continuity_checks import speech_state_findings
    from field_content import voice_parts
    from voice_sequence import has_markers, split_voice_sequence
    checks = []
    rows = extract_dialogues(shot.get("画面内容"))
    voice = dict(voice_parts(shot.get("音色")))
    sequence_markers = has_markers(str(shot.get("音色")))
    unresolved_voice = bool(rows and (not voice or len(rows) > 1 and not sequence_markers))
    silent = bool(VOICE_NO_DIALOGUE_RE.fullmatch(str(shot.get("音色") or '').strip()))
    faults = []
    if rows and voice:
        blocks, errors = split_voice_sequence(voice, rows) if sequence_markers else ([voice], [])
        faults.extend(errors)
        for index, block in enumerate(blocks):
            own = [rows[index]] if len(blocks) == len(rows) else rows
            faults.extend(emphasis_binding_faults(block.get("重音"), own))
            faults.extend(tail_binding_faults(block.get("尾音"), own))
    checks.append(_semantic_check("utterance_delivery_binding", shot,
        "fail" if faults else "unknown" if unresolved_voice or not rows and not silent else "pass" if rows else "not_applicable",
        "声法未解析为每条实际发言的独立绑定，保留原值待查" if unresolved_voice or not rows and not silent else
        "重音与尾音绑定实际本句；声线保持原值，话语目的适切性仍需独立语义审查",
        fields={key: shot.get(key) for key in ("画面内容", "音色")}, findings=faults))
    entities = _semantic_entities(shot)
    names = list(entities.people)
    environment = environment_phase_findings(shot, names)
    checks.append(_semantic_check("environment_actor_phase", shot,
        "fail" if any(row['status'] == 'fail' for row in environment) else "unknown" if environment or entities.faults or not shot.get("环境动态") else "pass",
        "环境中的人物动作必须属于本镜阶段；未出现的演员事件不从场景常量继承",
        fields={key: shot.get(key) for key in ("环境动态", "起始状态", "画面内容")}, findings=environment))
    records = music_directives(shot.get("生成限制"))
    status = "fail" if any(row['status'] == 'affirmative' for row in records) else "unknown" if any(row['status'] == 'unknown' for row in records) or not shot.get("生成限制") else "pass"
    checks.append(_semantic_check("music_semantic_scope", shot, status,
        "肯定、否定、未判定分别记录；否定句格式不合规范不能冒称主动配乐",
        fields={"生成限制": shot.get("生成限制")}, evidence={"occurrences": records}))
    speech_faults = speech_state_findings(shot)
    if speech_faults:
        checks.append(_semantic_check("speech_state_consistency", shot, "fail",
            "起态与无人物发声声明冲突", fields={"起始状态": shot.get("起始状态"), "音色": shot.get("音色")},
            findings=speech_faults))
    return checks


def validate_project(
    obj: dict[str, Any] | list[Any], mode: str = "audit", rules_path: str | None = None,
    *, timing_context: dict[str, Any] | None = None, episode_fragment: bool = False,
    saved_workbook_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rules = load_runtime_rules(rules_path)
    catalog = _catalog_map(rules)
    issues: list[dict[str, Any]] = []
    if isinstance(obj, list):
        obj = {"shots": obj}
    if not isinstance(obj, dict):
        issues.append(_issue(catalog, "E-TEMPLATE-001", "root", "输入必须是JSON对象或镜头数组。"))
        obj = {}
    if obj.get("audit_workbook_evidence") is not None and mode != "audit":
        issues.append(_issue(catalog, "E-GATE-001", "audit_workbook_evidence",
            "只读审计映射不能作为生产canonical；须走正式有源生产和绑定导出入口。", blocking=True))
    if saved_workbook_evidence is not None and mode != "audit":
        issues.append(_issue(catalog, "E-GATE-001", "saved_workbook_evidence",
            "实际工作簿审计证据参数仅限只读audit，不能作为生产canonical或生产证明。", blocking=True))
    elif saved_workbook_evidence is not None and not isinstance(saved_workbook_evidence, dict):
        issues.append(_issue(catalog, "E-TEMPLATE-001", "saved_workbook_evidence",
            "实际工作簿审计证据必须为对象。", blocking=True))

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

    from production_derivation import inspect_derivation
    production_binding = inspect_derivation(obj)
    # Only a full successful recompile can select actual event applicability.
    # A caller-written flag, a changed candidate, and legacy inputs cannot.
    planned_performance = production_binding.get('production_prevention_verified') is True
    from visible_entities import project_entity_registry, resolve_visible_subjects
    registered_people, registered_non_people = project_entity_registry(obj)
    entity_mapping_rows = []

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
            for label in _deprecated_display_label_hits(field, raw.get(field, ""))
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
        entities = resolve_visible_subjects(shot.get('可见主体'),
            people=registered_people, non_people=registered_non_people,
            speakers=[name for name, _ in extract_dialogues(shot.get('画面内容'))],
            evidence_texts=[shot.get('画面内容'), shot.get('起始状态')])
        entity_mapping_rows.append({'shot_id': str(shot.get('镜头编号', '')),
            'people': list(entities.people), 'non_people': list(entities.non_people),
            'background_people': list(entities.background_people),
            'unknown': list(entities.unknown), 'conflicts': list(entities.conflicts),
            'status': 'pending' if entities.faults else 'resolved'})
        if entities.faults:
            mapping_issue = _issue(catalog, 'E-VIS-001', f'{location}.可见主体',
                '；'.join(entities.faults),
                repair='根据角色/道具登记或源文补齐身份类型映射；保留原分镜，不为未知主体补写眼神、眨眼等动作。',
                blocking=True, severity='fail', template_gate_id='THG-06')
            mapping_issue['classification'] = 'input_mapping'
            issues.append(mapping_issue)
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
        from picture_presentation import timing_view, PictureTimingError
        try:
            execution_shot = timing_view(shot)
        except PictureTimingError as exc:
            issues.append(_issue(catalog, "E-TIMELINE-001", f"{location}.picture_timing", str(exc),
                                 repair="按当前正文重新生成内部计时依据；不要把数字时间标记补回画面内容。"))
            execution_shot = shot
        image_content = str(execution_shot.get("画面内容", ""))
        combined_performance = image_content
        timeline_faults = _validate_timeline(image_content, duration, strict=strict_contract,
                                              visible_subjects=visible)
        if timeline_faults:
            issues.append(_issue(
                catalog,
                "E-TIMELINE-001",
                f"{location}.画面内容",
                "；".join(timeline_faults),
            ))
        for field in EXECUTION_SHOT_FIELDS:
            field_text = str(shot.get(field, ""))
            # Accept explicitly equivalent bans on intake; projection emits
            # the fixed literal. Never erase affirmative/unknown music scope.
            normalized_music = normalize_music_prohibition(field_text) if field == '生成限制' else field_text
            bgm_match = BGM_TOKEN_RE.search(normalized_music)
            active_music = _active_music_directive(field_text)
            unknown_music = [row for row in music_directives(field_text) if row['status'] == 'unknown']
            if active_music:
                issues.append(_issue(
                    catalog,
                    "E-AUDIO-STAGE-001",
                    f"{location}.{field}",
                    f"单镜执行字段禁止主动配乐指令：{active_music!r}",
                    repair="核实并移除本镜主动配乐决定；音乐Cue仅在画面锁定后独立处理，不能只改文字掩盖矛盾。",
                    template_gate_id="THG-AUDIO",
                ))
            elif unknown_music:
                issues.append(_issue(
                    catalog,
                    "E-AUDIO-STAGE-001",
                    f"{location}.{field}",
                    "音乐限制含义或否定范围不明确，须核验：" + repr(unknown_music[0]['text']),
                    repair="确认本镜音乐意图；明确禁止的等义写法可规范为‘禁止额外配乐’，不得把条件、矛盾或肯定指令自动改成禁止。",
                    template_gate_id="THG-AUDIO",
                ))
            elif bgm_match:
                issues.append(_issue(
                    catalog, "E-AUDIO-STAGE-001", f"{location}.{field}",
                    f"单镜执行字段禁止出现BGM字面量，命中字段={field!r}",
                    repair="把已明确禁止的音乐约束归入生成限制并统一输出‘禁止额外配乐’。",
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
            visible_characters = list(entities.people)
            annotation_faults = visible_subject_annotation_faults(visible)
            if annotation_faults:
                issues.append(_issue(
                    catalog,
                    "E-VIS-001",
                    f"{location}.可见主体",
                    "；".join(annotation_faults),
                    repair="每位可见人物在顶层独立列出，括号仅补该人物的姿态、身体部位和可见性。",
                    template_gate_id="THG-06",
                ))
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
                elif "unobservable_contract" in message:
                    code, gate = "E-T2I-001", "THG-17"
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
            # Projection can fail earlier on field ownership. Still inspect
            # independent static inputs instead of masking a known semantic
            # category behind a generic projection error.
            unobservable_hits = _keyframe_unobservable_hits(gpt_projection or '\n'.join(
                context_instruction_text(str(shot.get(field) or ''), field)
                for field in ('起始状态', '机位', '光影', '光源来源', '光源方向', '色温倾向')))
            if unobservable_hits:
                issues.append(_issue(
                    catalog,
                    "E-T2I-001",
                    f"{location}.GPT关键帧生成指令",
                    f"静态关键帧含单帧不可证明的作者旁白、未来信息或象征结论：{unobservable_hits}",
                    repair="将必要含义转为可见眼神、唇线、手部、距离、道具、环境活动、材质与有源光色；其余从关键帧删除。",
                    template_gate_id="THG-17",
                ))
        invisible_context = _has_any(f"{shot_type} {visible}", INVISIBLE_SHOT_TERMS) and not _has_any(visible, FACE_VISIBLE_TERMS)
        if invisible_context:
            bad = _invisible_face_terms(combined_performance, entities)
            if bad:
                issues.append(_issue(catalog, "E-VIS-005", location, f"不可见镜头写入面部/眨眼细节：{sorted(set(bad))}"))

        categories = detail_categories(image_content)
        emotion_labels = [x for x in ABSTRACT_EMOTIONS if x in image_content]
        core_emotion = _has_any(str(shot.get("功能类型", "")), ["情绪", "关系", "冲突", "哭", "内心", "反应"]) or bool(emotion_labels)
        if emotion_labels and len(categories) < 2:
            issues.append(_issue(catalog, "E-EXPRESSION-001", f"{location}.画面内容", f"只有抽象情绪标签 {emotion_labels}，缺少成组的可观察表演细节；当前={categories}。"))
        elif core_emotion and len(categories) < 4:
            issues.append(_issue(catalog, "E-EXPRESSION-002", f"{location}.画面内容", f"重要情绪镜只有 {len(categories)} 类细节：{categories}", severity="warning"))

        eye_faults = [] if planned_performance or entities.faults else _eye_contract_faults(
            shot,
            duration=duration,
            strict=strict_contract,
            visible_names=list(entities.people), non_person_names=list(entities.non_people),
            legacy_components=False,
        )
        if eye_faults:
            issues.append(_issue(
                catalog,
                "E-EXPRESSION-003",
                f"{location}.画面内容/起始状态/生成限制",
                "已写眼部事件或可见性存在冲突：" + "；".join(eye_faults),
                repair="核对明确写出的视线、可见性及眨眼事件，修正冲突；自然静止不需要补换焦、眼睑变化或眨眼模板。",
                template_gate_id="THG-13",
            ))

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
            light_faults.extend(lighting_context_faults(
                shot, scene_anchors=obj.get("scene_anchors"),
                legacy_checker=_lighting_context_faults,
            ))
            if light_faults:
                issues.append(_issue(
                    catalog,
                    "E-LIGHT-001",
                    f"{location}.光影/光源来源/光源方向/色温倾向",
                    "；".join(light_faults),
                    repair=(
                        "按时间→室内外/门窗→天气→真实主光/弱补光→方向/高度→受光对象→曝光材质→叙事功能→同场连续性重写；"
                        "普通室内使用前侧/侧面漫射明暗并保持空气清澈、背景清晰，删除柔焦、镜头扩散、雾烟、穿插光束、"
                        "无依据逆光/轮廓光；室外或剧情烟尘场景的氛围光仍须满足物理前提并限制范围。"
                    ),
                    template_gate_id="THG-17",
                ))
        if "底光" in f"{light} {light_source}" and _has_any(str(shot.get("功能类型", "")), ["甜宠", "温情", "浪漫", "关系"]):
            issues.append(_issue(catalog, "E-LIGHT-002", f"{location}.光影", "普通关系/浪漫镜使用底光，需改为有源且有剧情必要的光效。", severity="warning"))

        dialogue_rows = extract_dialogues(image_content)
        dialogue = "".join(line for _, line in dialogue_rows)
        if dialogue_rows:
            if is_none_value(shot.get("音色")):
                issues.append(_issue(
                    catalog,
                    "E-VOICE-001",
                    f"{location}.音色",
                    "带对白镜必须填写固定结构音色子模板。",
                    template_gate_id="THG-15",
                ))
            elif strict_contract:
                voice_errors = _voice_template_errors(shot.get("音色"), dialogue, dialogue_rows)
                if voice_errors:
                    issues.append(_issue(
                        catalog,
                        "E-VOICE-001",
                        f"{location}.音色",
                        "；".join(voice_errors),
                        template_gate_id="THG-15",
                    ))
                picture_voice_labels = [
                    label for label in VOICE_TEMPLATE_LABELS
                    if re.search(rf"(?m)^\s*{re.escape(label)}\s*[：:]", image_content)
                ]
                if picture_voice_labels:
                    issues.append(_issue(
                        catalog,
                        "E-VOICE-001",
                        f"{location}.画面内容",
                        "固定音色子标签不得散落或复制进画面内容："
                        + "、".join(picture_voice_labels),
                        template_gate_id="THG-15",
                    ))
            else:
                delivery = f"{image_content} {shot.get('音色', '')}"
                dimensions = [name for name, pattern in VOICE_DIMENSION_PATTERNS.items() if pattern.search(delivery)]
                if core_emotion and len(dimensions) < 6:
                    issues.append(_issue(catalog, "E-VOICE-001", f"{location}.画面内容/音色", f"核心情绪对白的发声说明只覆盖 {len(dimensions)}/6 项：{dimensions}"))
                elif len(dimensions) < 4:
                    issues.append(_issue(catalog, "E-VOICE-002", f"{location}.画面内容/音色", f"普通对白的发声说明只覆盖 {len(dimensions)}/6 项：{dimensions}", severity="warning"))
            punctuation_delivery = str(shot.get("音色") or "")
            if re.search(r"标点(?:语气|效果|按[^：:]{0,30}表现)[：:]\s*(?:无|不适用)", punctuation_delivery):
                issues.append(_issue(catalog, "E-PUNCT-001", f"{location}.音色", "带对白镜在音色中明确取消了标点语气。"))
            missing_punct = _validate_punctuation(dialogue, punctuation_delivery)
            if missing_punct:
                issues.append(_issue(catalog, "E-PUNCT-004", f"{location}.音色", f"对白标点语气未说明：{missing_punct}"))
            if strict_contract:
                unbodied = production_binding.get('disembodied_speakers', {}).get(str(shot.get('镜头编号')), []) if production_binding.get('valid') else []
                visible_speakers = [name for name, _ in dialogue_rows if name not in unbodied and dialogue_mouth_visible(name, shot)]
                if visible_speakers and "口型与对白一致" not in image_content:
                    issues.append(_issue(catalog, "E-VOICE-001", f"{location}.画面内容", "R10-SPC-1可见对白未写“口型与对白一致”。"))
                # Current production replays the same speaking-eye geometry
                # that generated this picture. A magic phrase is not proof.
                if visible_speakers and not planned_performance and "不看镜头" not in image_content:
                    issues.append(_issue(catalog, "E-PERFORMANCE-001", f"{location}.画面内容", "R10-SPC-1对白人物未明确不看镜头。"))
                reaction_faults = [] if planned_performance or entities.faults else listener_reaction_faults(
                    execution_shot, duration=duration, dialogue_rows=dialogue_rows,
                    visible_names=list(entities.people), non_person_names=list(entities.non_people))
                if reaction_faults:
                    issues.append(_issue(catalog, "E-PERFORMANCE-001", f"{location}.画面内容", "多人对白镜未写听者错峰反应。" + "；".join(reaction_faults)))
        elif strict_contract and not VOICE_NO_DIALOGUE_RE.fullmatch(str(shot.get("音色") or "").strip()):
            issues.append(_issue(
                catalog,
                "E-VOICE-001",
                f"{location}.音色",
                "无对白镜的音色必须明确写“无对白，不适用。”，不得伪造发声规格。",
                template_gate_id="THG-15",
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

    # A server-owned scene projection retains the real cross-shot audio context.
    # This argument is not a canonical field or an externally writable tool input.
    timing_shots = normalized_shots
    if timing_context is not None:
        timing_shots = [
            normalize_shot(row, aliases)[0]
            for row in timing_context.get("shots", []) if isinstance(row, dict)
        ]
    timing_report = timing_model.validate_timing(
        timing_context if timing_context is not None else obj, timing_shots, rules,
    )
    local_timing_ids = {str(row.get("镜头编号") or "") for row in normalized_shots}
    for fault in timing_report["faults"]:
        timing_issue = _issue(
            catalog, fault["code"], fault.get("location", "timing"),
            fault["message"], severity="fail", blocking=True,
            template_gate_id="THG-10",
            repair="按实际发言和事件重排所在阶段；并行只共用合理时段，先后依赖必须保留，禁止删改原句或擅自提高语速配置。",
        )
        if fault.get("shot_id"):
            timing_issue["shot_ids"] = [fault["shot_id"]]
        timing_issue["check_family"] = "timing"
        for evidence_key in ('timing_evidence_kind', 'actual_audio_verified', 'utterance_ids', 'overlap_seconds'):
            if evidence_key in fault:
                timing_issue[evidence_key] = fault[evidence_key]
        if timing_context is not None:
            timing_issue["timing_scope_decidable"] = bool(
                fault.get("shot_id") in local_timing_ids
            )
        issues.append(timing_issue)

    issues.extend(_validate_protected_dialogue_alignment(
        obj,
        normalized_shots,
        catalog,
        strict=strict_contract,
    ))
    issues.extend(_validate_adapted_dialogue_audit(
        obj,
        normalized_shots,
        catalog,
        production_verified=planned_performance,
        communication_bindings=production_binding.get('source_communication', ()) if production_binding.get('valid') else (),
    ))

    for scene_id, items in scene_groups.items():
        if len(items) < 2:
            continue
        continuity_faults = scene_lighting_continuity_faults(items, obj.get("scene_anchors"))
        if continuity_faults:
            issues.append(_issue(catalog, "E-LIGHT-002", f"scene:{scene_id}", "；".join(continuity_faults), severity="warning"))

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
        production_binding=production_binding,
    )
    issues.extend(gate_issues)
    # Imported/older originals do not carry the production workflow profile.
    # Its gate must not suppress actual N-1 machine observations in an audit.
    adjacent_text_checks = []
    if "adjacent_continuity" not in gate_metrics:
        gate_metrics["adjacent_continuity"], adjacent_text_checks = audit_adjacent_text(obj, normalized_shots)
    gate_metrics.update(public_metrics)
    chapter_issues, chapter_metrics = _validate_chapter_intake(obj, rules, catalog)
    issues.extend(chapter_issues)
    gate_metrics.update(chapter_metrics)
    gate_metrics["timing"] = timing_report["metrics"]
    episode_report = inspect_episodes(obj, normalized_shots, timing=timing_report['metrics'],
                                     rules=rules.get('episode_rule_policy'), production_verified=planned_performance,
                                     fragment=episode_fragment)
    gate_metrics['episode_compliance'] = {k:v for k,v in episode_report.items() if k!='faults'}
    for finding in episode_report['faults'] + inspect_borrowing(obj):
        item = _issue(catalog, finding['error_code'], finding['location'], finding['evidence'])
        item['check_status'] = finding.get('status','fail')
        issues.append(item)

    if mode == "archive":
        stage_evidence = obj.get("stage_evidence")
        required_stages = ["STAGE-A", "STAGE-B", "STAGE-C", "STAGE-D"]
        if not isinstance(stage_evidence, dict) or any(not stage_evidence.get(stage) for stage in required_stages):
            issues.append(_issue(catalog, "E-BOOTSTRAP-001", "stage_evidence", "归档模式缺阶段A-D证据。"))
        review = obj.get("human_review")
        if not isinstance(review, dict) or review.get("最终归档") != "approved" or is_empty(review.get("签名")):
            issues.append(_issue(catalog, "E-ARCHIVE-001", "human_review", "归档模式缺approved状态或真实签名。"))

    gate_metrics['entity_mapping'] = {'contract': 'visible-entity-mapping-v1',
        'status': 'pending' if any(row['status'] == 'pending' for row in entity_mapping_rows) else 'resolved',
        'shots': entity_mapping_rows,
        'semantic_identity_review': 'separate_independent_review_required'}
    semantic_checks = adjacent_text_checks + [check for shot in normalized_shots for check in _shot_semantic_checks(shot)]
    # Only the service's separate read-only argument can attest original
    # sidecar integrity. Identically named author JSON never gains that trust.
    external_saved = saved_workbook_evidence if mode == "audit" and isinstance(saved_workbook_evidence, dict) else None
    workbook_evidence = external_saved if external_saved is not None else obj.get("audit_workbook_evidence")
    if isinstance(workbook_evidence, dict):
        gate_metrics['saved_workbook_evidence'] = copy.deepcopy(workbook_evidence)
        verified_contract = (external_saved.get("projection_contract")
            if external_saved is not None and external_saved.get("projection_integrity_verified") is True else None)
        verified_contract = verified_contract if isinstance(verified_contract, str) and verified_contract.strip() else None
        raw_saved_rows = workbook_evidence.get("rows")
        if not isinstance(raw_saved_rows, list):
            issues.append(_issue(catalog, "E-TEMPLATE-001", "audit_workbook_evidence.rows",
                "实际工作簿审计证据rows必须为数组；未取得的单元格保持未知。", blocking=True))
            raw_saved_rows = []
        saved_rows = {str(row.get("shot_id")): row for row in raw_saved_rows if isinstance(row, dict)}
        for shot in normalized_shots:
            cells = saved_rows.get(str(shot.get("镜头编号")), {}).get("cells", {})
            if not isinstance(cells, dict):
                issues.append(_issue(catalog, "E-TEMPLATE-001", "audit_workbook_evidence.cells",
                    "实际工作簿单元格证据必须为对象；未取得的单元格保持未知。", blocking=True))
                cells = {}
            saved = {name: cell.get("value") for name, cell in cells.items() if isinstance(cell, dict)}
            semantic_checks.extend(validate_saved_projection(shot, saved,
                projection_contract_verified=verified_contract))
    for candidate in _communication_candidates(obj, normalized_shots):
        target_ids = set(candidate['target_shots'])
        checks_failed = [issue for issue in issues if issue.get('error_code') == 'E-ADAPTED-DIALOGUE-001']
        semantic_checks.append({"check_id": "source_communication", "layer": "源文对白", "shot_ids": sorted(target_ids),
            "status": "unknown" if not planned_performance or any(row.get('check_status') == 'unknown' for row in checks_failed)
                      else "fail" if checks_failed else "pass",
            "reason": "缺接收者收到信息时刻的可重放证据；旁白不自动赋予角色知识" if not planned_performance else
                      "生产计划重放已核实际角色知识时序；原文语义仍由独立审查负责",
            "evidence": candidate, "field_sha256": {"source_text": field_sha256(candidate['source_text'])},
            "findings": []})
    gate_metrics['semantic_checks'] = semantic_checks
    for check in semantic_checks:
        # Missing production provenance is an audit coverage obligation. It is
        # not an invented content failure on a historical or pre-export draft.
        if check['status'] != 'fail':
            continue
        issue = _issue(catalog, "E-FIELD-CONTENT-001", check['check_id'],
            check['reason'] + "：" + str(check.get('findings') or check.get('evidence')), severity="fail")
        issue.update(check_id=check['check_id'], check_status='fail', shot_ids=check['shot_ids'])
        issues.append(issue)
    if production_binding['present']:
        gate_metrics['production_derivation'] = {
            'valid': production_binding['valid'],
            'source_obligations': len(production_binding['source_obligations']),
            'semantic_audit': 'separate_independent_review_required',
            'performance_check_basis': 'recompiled_plan_events' if planned_performance else 'declared_event_consistency',
        }
        for fault in production_binding['faults']:
            issues.append(_issue(catalog, 'E-GATE-001', fault['path'], fault['message']))

    for fault in repair_observations.check_faults(obj, issues, rules):
        issues.append(_issue(catalog, "E-GATE-001", "prevention_evidence.repair_history.current_check", fault))

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
        "timing_advisories": timing_report["advisories"],
        "issues": issues,
    }


def bind_current_repair_check(obj, *, rules_path=None):
    """Return a copy with a machine observation from an actual complete run.

    This never approves semantics or edits old rows. Service callers must first
    obtain the history and counts from their immutable source artifact.
    """
    result = copy.deepcopy(obj)
    report = validate_project(result, mode="repair", rules_path=rules_path)
    history = repair_observations._history(result)
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        raise ValueError("current_repair_check_requires_history")
    history[-1][repair_observations.FIELD] = repair_observations.make_check(
        result, report.get("issues", []), load_runtime_rules(rules_path))
    return result


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
