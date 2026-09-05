# V16.3-148-R10-StableRuntime Full Template Library

每个模板均为独立、可执行结构；不得用同一通用壳替代。当前0.5.24规则由`rule_registry.json`唯一维护；时长统一引用`timing_policy`，未标定候选不能写为实测。本页的规划表字段不是新增正式母版顶层字段或工作簿列。

## TPL-R10-01 Source Progress Lock模板

- 触发条件：每集生成前
- 字段顺序：上集终点｜本集起点｜本集计划终点｜下一集起点｜禁止重复｜禁止提前｜人物状态｜道具状态｜场景状态｜Borrow Ledger｜备注
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：缺起点/终点/下一集起点即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-02 Episode Contract模板

- 触发条件：分集边界确定后
- 字段顺序：集数｜版本｜承接点｜起点｜终点｜下一集起点｜总时长｜总镜数｜场景｜角色｜钩子
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：边界或时长不闭合即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-03 Source Ledger模板

- 触发条件：拆Beat后
- 字段顺序：原文单元｜处理方式｜Beat等级｜覆盖镜头｜证据｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：S级漏拍或处理无证据即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-04 Borrow Ledger模板

- 触发条件：使用后文时
- 字段顺序：借入内容｜来源位置｜原因｜授权｜下一集影响｜剧透风险｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：无授权借后文即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-05 E级Beat登记模板

- 触发条件：出现E级Beat时
- 字段顺序：Beat｜E级理由｜禁止拍摄内容｜影响｜审核状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：E级内容进入正式分镜即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-06 Source Beat表模板

- 触发条件：正式分镜前
- 字段顺序：Beat ID｜原文句｜功能｜等级｜可视化方式｜处理决定
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：无Beat表或S级不完整即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-07 删除/合并/压缩Ledger模板

- 触发条件：发生删合压时
- 字段顺序：原文项｜动作｜理由｜视觉承载｜授权｜影响｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：压缩无承载或删除S级即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-08 DialogueUnit模板

- 触发条件：每条台词
- 字段顺序：原句出现ID｜角色｜台词字符范围｜发音单位及依据｜profile_id/证据状态｜发言事件起止｜阶段范围｜独立停顿事件｜动作/反应及先后依赖｜必要镜尾依据｜镜头时长｜结论
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：THG-05原句重拼/说话人不精确，或THG-10按`timing_policy`逐发言、逐阶段核对时发言/音轨/依赖事件超出可用范围即fail；真实串行相加，可并行共用，停顿去重，无固定呼吸/反应/镜尾费。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-09 ActionUnit模板

- 触发条件：每个动作镜
- 字段顺序：主体｜动作事件ID｜主动作｜次动作｜可见部位｜动作起止｜前置依赖｜并行依据｜听者反应起止｜必要镜尾依据｜复杂度｜生成结论
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：复杂动作超时或不可见即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-10 Motion Plan模板

- 触发条件：存在运镜时
- 字段顺序：运镜类型｜运镜主体｜移动路径｜触发Beat｜叙事功能｜生成限制
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：缺主体或叙事功能即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-11 Beat Budget模板

- 触发条件：节奏轴阶段
- 字段顺序：Beat ID｜信息量｜动作量｜台词量｜情绪量｜建议时长｜最终时长
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：预算与镜头时长不一致即warning/fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-12 ShotGraph模板

- 触发条件：多镜场景
- 字段顺序：镜头编号｜前置镜头｜后续镜头｜主体｜视线方向｜动作连续｜光线连续｜转场
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：轴线/动作/光线断裂即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-13 ShotGraph Gate模板

- 触发条件：场景连续性审计
- 字段顺序：场景ID｜轴线｜视线｜动作接续｜光源方向｜色温｜通过状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：任一连续性硬项失败即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-14 无对白compact镜头模板

- 触发条件：画面内容无具名可听对白
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空；R10-SPC-1 的`画面内容`仍须恰好三个闭合阶段。
- 失败标准：画面内容无授权却出现说话口型，或回写独立台词字段即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-15 带对白compact镜头模板

- 触发条件：具名角色开口
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 必填规则：`画面内容`按R10-SPC-1恰好三个阶段，同时写可见动作、表情、`角色说道：“精确对白”`、对白时点、口型一致、可见呼吸/停顿、重音期表演、视线不看镜头、听者错峰反应和镜尾状态；`音色`按固定顺序写基础声线、言语行为、当前表达、发声方式六项和2—4项禁止语气；四项光线全部非空。对白来源必须明确归入`原文直接对白`或`原文事实转化对白`。叙述文本若实质构成问答、劝决、令应、报听、辩核或证判场景，也必须还原对白，不得因没有引号而输出全动作镜头。
- 失败标准：明确言语行为被机械改成无声动作、事实转化对白冒充原文原句、音色一行自由描述、固定标签缺失/乱序/空值、本镜实际对白重音不符、统一中性尾缀、标点/表演不一致、听者反应/容量/四项光线缺失，或恢复顶层发声方式字段即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-16 带心理音轨镜头模板

- 触发条件：存在心理音轨且无角色开口
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：心理音轨字段内缺不张嘴/不口型/不字幕/不计入安全属性，新增心理音轨属性字段，或实际发声时间与跨镜范围未按timing_policy完整覆盖即fail；不计角色对白不等于不占时间。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-17 带台词+心理音轨镜头模板

- 触发条件：台词与心理音轨同镜
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：两类音轨未分层、心声驱动口型、实际发声时间超范围，或无明确可听辨依据的声音重叠即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-18 书页特写模板

- 触发条件：书页/POV特写
- 字段顺序：镜头编号｜镜头类型｜可见主体｜书页内容｜手指/翻页动作｜画外声音｜生成限制｜原文证据
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：出现不可见面部表演即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-19 人物反应镜模板

- 触发条件：书页镜后需人物反应
- 字段顺序：镜头编号｜人物｜可见部位｜表情细节｜手部｜身体｜视线｜呼吸｜原文Beat
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：反应无源或不足4类细节即warning/fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-20 精确十列母版投影模板

- 触发条件：正式XLSX导出
- 字段顺序：分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 必填规则：恰好十列且顺序精确；第五列每格以`镜头序号：EP##-###（x.xs；累计x.xs）`开头并按`strict_prompt_contract.md`空行分段；正式母版是唯一可执行单镜文本且包含`9:16竖屏`；GPT列以`画幅：9:16竖屏`开头，仅为无时间、对白、口型、音频、运镜过程和转场指令的静态白名单投影；canonical→投影→XLSX→反向解析按UTF-8 NFC+LF哈希一致，多行对白不丢行。
- 失败标准：出现Seedance列、生成批次表、batches、错列、缺列或持久化派生正文即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-21 R10-SPC-1单镜时间阶段Gate模板

- 触发条件：新生成、重建、修正或导出的Seedance 2.5正式镜头
- 字段顺序：镜头编号｜镜头时长｜阶段数｜阶段边界｜连续性｜重叠数｜最终边界｜结论
- 必填规则：恰好3段，使用`【0.0—x.x秒】`一位小数格式，从0.0开始，连续不重叠，最终边界等于本镜时长；三段依次为刺激/起始反应、核心动作/对白、镜尾反应/切点；不为旧4秒下限包装。
- 失败标准：少于或超过3段、括号/横线/小数格式错误、重叠、留空、越界或不闭合即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-22 审计状态与报告模板

- 触发条件：分镜审计
- 字段顺序：对象｜范围｜版本｜canonical_sha256｜artifact_content_sha256｜audit_status｜initial_issue_count｜residual_issue_count｜introduced_issue_count｜blocking_issue_count｜audit_trace_id｜certificate_id｜规则｜证据｜错误码｜最终状态
- 必填规则：生产导出时`audit_status=PENDING_AUDIT`、显示`未签发结论`，四类问题计数、`audit_trace_id`和`certificate_id`为空/null，不得用0代替未审；原生产XLSX保持不可变。仅当指纹一致的独立审计证书返回后，服务端才创建不可变的签名audit overlay新工件和后继工作簿，写入计数、trace、证书和PASS/FAIL。
- 失败标准：无证据pass，生产阶段预填PASS/0问题，就地回填原生产XLSX，或签名审计新工件缺trace/指纹即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-23 修复报告模板

- 触发条件：分镜修复后
- 字段顺序：原问题｜错误码｜修复范围｜修改内容｜未修改范围｜回归测试｜最终状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：越界修改或未回归即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-24 复审报告模板

- 触发条件：修复完成后
- 字段顺序：复审对象｜前次错误｜修复证据｜重跑结果｜残留风险｜最终状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：未重跑触发模块即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-25 表格转换模板

- 触发条件：转换表格
- 字段顺序：字段顺序｜输入映射｜缺失值策略｜原文列｜校验状态｜导出路径
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：字段错序、改写剧情、多行对白丢行，或转换前后字符数/哈希不一致即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-26 JSON归档模板

- 触发条件：结构化归档
- 字段顺序：schema_version｜project_version｜episodes｜shots｜statistics｜audit｜human_review
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：JSON无效或版本不一致即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-27 Execution Log模板

- 触发条件：每次关键任务
- 字段顺序：execution_id｜时间｜任务｜输入｜规则版本｜阶段A-D｜校验结果｜输出
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：阶段令牌或版本缺失即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-28 Final Release Checklist模板

- 触发条件：最终发布
- 字段顺序：Source Lock｜Beat/Ledger｜原文直接对白/事实转化对白双轨｜27字段模板｜可见性｜画面内容中的对白/表演/眼部链｜音色固定子模板｜运镜｜四项光线｜单镜母版执行完整性｜THG-01–17｜N-1相邻镜头逐组证明｜十列投影与反向解析｜独立审计证书｜统计｜人工签核
- 必填规则：字段必须出现；每条负向样本必须fail并命中目标错误码，对应修复样本必须pass；多行XLSX样本必须执行真实导出与反向解析。
- 失败标准：任一硬门未过即blocked
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-29 Final Completeness Gate模板

- 触发条件：完整度声明
- 字段顺序：Level1内容｜Level2流程｜Level3执行｜Level4机械｜Level5生产｜证据｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：无证据宣称100%即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-30 Final Integration Checklist模板

- 触发条件：项目集成
- 字段顺序：DOCX版本｜规则JSON｜Schema｜冲突表｜模板库｜任务卡｜测试｜脚本｜哈希｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：镜像不一致或测试失败即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-31 Rule Registry模板

- 触发条件：新增规则
- 字段顺序：rule_id｜module｜name｜priority｜hard_rule｜trigger｜rule_text｜pass｜fail｜error_code｜status｜version
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：缺唯一ID或执行语义即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-32 Validator Ruleset模板

- 触发条件：新增校验器
- 字段顺序：validator_id｜module｜handler｜severity｜error_code｜applies_when｜pass_condition｜repair
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：无handler或无法运行即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-49 原文事实转化对白审计模板

- 触发条件：授权集范围内的任一叙述单元；先判断事件是否必须通过人物语言交换完成，不以引号或言语动词为前提
- 字段顺序：adaptation_id｜source_ledger_index｜source_unit_id｜source_text｜dialogue_affordance｜treatment｜speaker｜adapted_text｜target_shots｜fact_atoms｜speaker_basis｜authorization｜plot_change｜new_plot_facts｜entailment_status｜adaptation_reason
- 必填规则：全部叙述单元按source_ledger顺序100%登记，填写会话／混合会话／非会话、交流功能、交流双方和开口必要性；会话或混合会话固定为`必须转对白`和`原文事实转化对白`；`plot_change=false`、`new_plot_facts=[]`、`entailment_status=entailed_by_source`；转化对白在目标镜头中恰好出现一次并采用完整固定音色模板。否定性言语描述不得误判为实际说话。
- 失败标准：漏记／乱序叙述单元、把会话场景误判为非会话、缺少交流闭环或参与方、用无声动作替代应有对白、对白包含原文无法推出的事实、说话人无依据、越过授权范围、转化对白冒充原句、目标镜头缺失／重复，或固定音色与本镜对白不闭合即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-33 Error Code模板

- 触发条件：新增错误码
- 字段顺序：error_code｜module｜severity｜blocking｜trigger｜evidence｜repair｜final_status
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：重复代码或无修复动作即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-34 Golden Test Case模板

- 触发条件：新增规则或校验器
- 字段顺序：test_id｜fixture_id｜mode｜mutations｜expected_status｜expected_error_codes｜forbidden_error_codes
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：不实际调用校验器即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-35 Task Package Card模板

- 触发条件：新增任务类型
- 字段顺序：task_package_id｜task_name｜trigger_phrases｜must_modules｜minimum_input｜minimum_output｜blocking_conditions｜final_status_field
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：任务专属模块缺失即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-36 Authorization Ledger模板

- 触发条件：规则例外
- 字段顺序：authorization_id｜请求｜风险｜可否授权｜用户决定｜范围｜到期/撤销｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：安全例外被授权或范围不明即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-37 ChangeLog模板

- 触发条件：版本升级
- 字段顺序：version｜date｜change｜reason｜affected_files｜migration｜validator｜status
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：版本变更无迁移说明即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-38 Deprecated Rule Map模板

- 触发条件：规则被替换
- 字段顺序：旧规则｜替代规则｜废弃原因｜兼容方式｜截止版本｜状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：旧新规则同时active即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-39 PG1版本与修改范围锁模板

- 触发条件：所有生成、审计、修复、插入、替换与归档任务
- 字段顺序：authoritative_source｜task_scope｜protected_scope｜active_rule_revision｜must_include｜must_exclude
- 必填规则：active_rule_revision必须为R10-PG1；保护范围之外不得修改。
- 失败标准：权威输入不明、旧版本并行、必含内容遗漏或禁用内容残留即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-40 源文覆盖与人物认知模板

- 触发条件：锁定剧情、对白和人物阶段
- 字段顺序：source_id｜source_type｜source_text｜speaker｜treatment｜target_shots｜authorization｜character｜knows｜does_not_know｜relationship_stage｜emotion_stage｜forbidden
- 必填规则：每个正式镜必须进入source ledger；授权删除必须提供用户授权；人物认知五项完整。
- 失败标准：关键事件/对白映射不足100%、说话人错误、认知或情感提前即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-41 全部相邻镜头逐组连续性证明模板

- 触发条件：所有正式分镜；N个镜头强制生成N-1个canonical顺序相邻组，N=1时生成空pair数组及其确定性哈希
- 字段顺序：contract｜expected_pair_count｜reviewed_pair_count｜coverage_percent｜ordered_pair_sha256｜pairs[pair_id｜left_shot_id｜right_shot_id｜boundary_type｜left_end_state_sha256｜right_start_state_sha256｜reviewed_fields｜continuity_status｜transition_explanation]
- 必填规则：`contract=adjacent-pair-continuity-v1`，pair数量恰为N-1且与成片顺序逐项相等；每对检查人物位置、身体朝向、视线落点、手持道具、门窗状态、动作进度、光源方向。同场必须闭合或有可见变化说明；跨场/跨集必须明确切换；同一时点每个prop_id只有一个主要持有人。独立审计空间层再按同序提交完整`pair:left->right`集合、N-1计数和有序pair SHA-256。
- 失败标准：缺组、重复、逆序、非相邻、覆盖率<100%、状态/道具跳变、跨场跨集无说明、左右状态哈希或有序pair哈希漂移即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-42 镜头颗粒度与演员表演链模板

- 触发条件：正式拆镜、台词镜和重要情绪镜
- 字段顺序：shot_id｜core_function｜viewpoint_count｜camera_position｜main_action_stage｜detail_shot_function｜transition_function｜stimulus｜listen_or_observe_reaction｜gaze_path｜micro_expression｜ecological_action｜body_or_breath｜end_state
- 必填规则：单镜一个核心阶段、一个视角、一个主机位；具名/主要可见人物最多3人，公共场景背景普通人物不计入主要角色数；带台词/重要情绪镜七项表演链完整且符合可见性。面部清楚且>=2秒时另含视线起点、眼球换焦、眼球先动/头部跟随、眼睑变化、具体眨眼或有理由不眨、镜尾视线；重音不眨，多人错峰，静帧避开半眨眼。
- 失败标准：多视角、隐形切镜、站桩对白、只动嘴、动作只写结果、僵直凝视、机械/同步眨眼或眼部链缺项即fail；无功能特写warning。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-43 compact唯一单镜执行母版模板

- 触发条件：生成或导出任何正式镜头
- 原子字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句
- 对外执行列名：`正式分镜母版`
- 必填规则：每格首行为`镜头序号：EP##-###（x.xs；累计x.xs）`，之后严格按`strict_prompt_contract.md`空行分段并可脱离历史上下文执行；`生成限制`和正式母版都写`9:16竖屏`；动作/表情/眼部链/精确对白/声画同步时点/听者错峰反应/恰好3段时间描写只在`画面内容`写一次；完整发声规格只在`音色`固定子模板写一次；四项光线必填且色温不含Kelvin/K；单镜一主机位/主运镜/核心阶段；负面音乐限制统一写`禁止额外配乐`，不得出现`BGM`字面量。导出前THG-01–17、事实转化对白证明、静帧语义/光影物理闭合及N-1相邻组证明全部通过。
- 失败标准：第五列表头恢复“·字段合并”，出现跨镜代指、转场重复、GPT动态词、缺失干净`9:16竖屏`、两条冗长旧9:16短语、`BGM`、时间段非三段闭合、眼神/眨眼/静帧门禁失败、相邻组证明不全、音色固定子模板失败、旧顶层字段/批次回写、四项光线缺失/歧义、投影/XLSX反向解析不一致、生产阶段预填审计PASS/0问题，或后台预生成Seedance即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-44 修正影响与系统升级模板

- 触发条件：审计后修正与再次审计
- 字段顺序：round｜problem_group｜initial_issue_count｜residual_issue_count｜introduced_issue_count｜repeat_rounds｜root_cause｜allowed_change_scope｜protected_scope｜action｜regression_scope｜result
- 必填规则：第1轮局部修正并查相邻；第2轮root_cause_repair；第3轮及以后rebuild_upstream。
- 失败标准：原问题残留、修正新增问题、越界修改、连续多轮仍打局部补丁即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-45 章节整体读取与剧情地图模板

- 触发条件：小说章节或连续叙事直接转剧本、节拍或分镜
- 字段顺序：chapter_id｜complete_read｜chunk_count｜chunks_read｜previous_end｜current_start｜current_end｜next_start｜chapter_goal｜events｜causal_links｜uncertainty_status
- 必填规则：可分块读取但必须全部读完并合并；事件有稳定ID和源文单元；歧义未解决不得进入生成。
- 失败标准：边读边生成、章节边界缺失、核心事件或因果链为空、未解决歧义仍继续即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-46 源文取舍、对白与人物变化模板

- 触发条件：章节整体解析完成后进行内容取舍
- 字段顺序：source_unit_id｜source_type｜source_text｜value_axes｜value_grade｜cinematic_function｜treatment｜target_beats｜reason｜authorization｜redundancy_evidence｜retained_function_in｜protected_dialogue_functions｜character_start_state｜trigger_event_ids｜character_end_state｜cognition_before｜cognition_after｜forbidden_knowledge
- 必填规则：S/A/B/C仅为多轴评估后的等级；C级不得自动删除；删除必须有授权或冗余证据；关键对白、人物状态和认知变化必须映射节拍。
- 失败标准：源文单元未处理、关键对白无去向、人物提前知情、无证据删除或电影功能丢失即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-47 改编预算、节拍与章节覆盖模板

- 触发条件：源文取舍完成、正式拆镜之前及生成之后
- 字段顺序：target_duration_seconds｜dialogue_seconds｜action_seconds｜transition_seconds｜shot_count_min｜shot_count_max｜beat_id｜core_stage｜event_ids｜source_unit_ids｜entry_state｜exit_state｜target_duration_seconds｜shot_count_range｜function｜core_event_coverage_percent｜causal_link_coverage_percent｜character_state_change_coverage_percent｜protected_dialogue_coverage_percent｜source_treatment_percent
- 必填规则：先预算后镜头；保护对白与心理音轨引用同一`timing_policy`并逐发言、逐阶段检查；串行依赖与并行占用明确，普通停顿不重复计时；实测配置优先、未标定候选明示；所有覆盖指标必须100%；单镜一个核心阶段但允许同阶段连续动作链。
- 失败标准：最低时长超预算、事件未进节拍、固定镜头数挤压高潮或任一覆盖率不足100%即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-48 公共场景人物与环境音生态模板

- 触发条件：街道、闹市、市集、商街、巷道、城门、码头、驿站、公共餐饮、寺院、店铺公共区等公共流动场景
- 字段顺序：shot_id｜scene_id｜location_type｜empty_scene_state｜empty_scene_reason｜crowd_density｜ordinary_roles｜foreground_actions｜midground_actions｜background_actions｜near_audio_sources｜mid_audio_sources｜far_audio_sources｜dialogue_ducking｜visible_audio_sources｜offscreen_audio_sources｜formal_master_people_match｜formal_master_audio_match｜continuity_transition｜status
- 必填规则：非豁免场景必须填密度、地点角色、错峰动作和空间层次；建立/全/远景至少三类三层声源，其余至少两类两层；对白镜必须避让；唯一正式母版从零复现。空场必须同时填状态和剧情原因。
- 失败标准：只写人群、路人、环境声或街道嘈杂声；匿名群演清晰说话；时代错位声源；声画无源；正式母版生态不完整；同场生态无过渡跳变即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-50 文生图静帧与光影决策模板

- 触发条件：GPT关键帧、文生图、场景定帧、古装视觉提示词或任何光影选择
- 字段顺序：candidate_semantic｜classification（可直接静态投影/先转为可见证据/不得进入单帧）｜visible_translation｜time｜interior_exterior｜door_window_state｜weather｜primary_source｜weak_fill_source｜source_direction_height｜lit_subject｜exposure_material｜indoor_background_clarity｜atmosphere_scope｜narrative_function｜scene_continuity_id｜physical_preconditions｜status
- 必填规则：每个词组必须单帧可见或摄影可控；普通室内保留方向字段但写大面积漫射明暗，锁定空气清澈、背景可辨、不泛白不雾化和门窗原状态；室外氛围光或室内剧情介质才允许雾烟、光束、扩散并须有物理前提及局部范围。
- 失败标准：作者旁白/未来/象征进入静帧；普通室内出现雾烟、穿插光束、柔焦扩散、强逆光、背景泛白或擅改门窗；剧情介质未局部控制；日夜/空间/光源冲突或同场光向跳变即fail。
- 输出状态：pass / warning / fail_current / unable_to_check
