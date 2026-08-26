# V16.3-148-R10-StableRuntime Full Template Library

每个模板均为独立、可执行结构；不得用同一通用壳替代。

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
- 字段顺序：角色｜台词｜字数｜2.8最小时长｜停顿预算｜镜头时长｜结论
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：时长不足即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-09 ActionUnit模板

- 触发条件：每个动作镜
- 字段顺序：主体｜主动作｜次动作｜可见部位｜时间预算｜复杂度｜生成结论
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

## TPL-R10-14 无台词镜头模板

- 触发条件：台词为无
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜机位｜运镜方式｜运镜主体｜运镜功能｜人物调度｜画面内容｜动作与表演｜台词｜音色｜发声方式｜标点语气｜心理音轨｜心理音轨属性｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句｜生成批次
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：台词未写无或人物出现口型即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-15 带台词镜头模板

- 触发条件：角色开口
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜机位｜运镜方式｜运镜主体｜运镜功能｜人物调度｜画面内容｜动作与表演｜台词｜音色｜发声方式｜标点语气｜心理音轨｜心理音轨属性｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句｜生成批次
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：缺音色/发声/标点/容量即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-16 带心理音轨镜头模板

- 触发条件：存在心理音轨且无角色开口
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜机位｜运镜方式｜运镜主体｜运镜功能｜人物调度｜画面内容｜动作与表演｜台词｜音色｜发声方式｜标点语气｜心理音轨｜心理音轨属性｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句｜生成批次
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：缺不张嘴/不口型/不字幕/不计入即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-17 带台词+心理音轨镜头模板

- 触发条件：台词与心理音轨同镜
- 字段顺序：镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜机位｜运镜方式｜运镜主体｜运镜功能｜人物调度｜画面内容｜动作与表演｜台词｜音色｜发声方式｜标点语气｜心理音轨｜心理音轨属性｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句｜生成批次
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：两类音轨未分层或心声驱动口型即fail
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

## TPL-R10-20 生成批次模板

- 触发条件：i2v生成前
- 字段顺序：批次编号｜覆盖镜头｜时长｜场景｜人物｜主动作｜主运镜｜情绪｜内部时间轴｜声音｜光影｜环境动态｜参考锚定｜生成限制｜负面提示词
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：<4秒、>15秒或多主运镜即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-21 BatchGraph Gate模板

- 触发条件：多批次连续生成
- 字段顺序：批次编号｜前批次｜后批次｜首尾帧｜人物状态｜道具状态｜光影状态｜衔接结论
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：首尾状态不闭合即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-22 审计报告模板

- 触发条件：分镜审计
- 字段顺序：对象｜范围｜版本｜规则｜证据｜错误码｜阻塞项｜非阻塞项｜最终状态
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
- 失败标准：无证据pass即fail
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
- 失败标准：字段错序或改写剧情即fail
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-26 JSON归档模板

- 触发条件：结构化归档
- 字段顺序：schema_version｜project_version｜episodes｜shots｜batches｜statistics｜audit｜human_review
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
- 字段顺序：Source Lock｜Beat/Ledger｜模板｜可见性｜台词｜表演｜运镜｜光影｜批次｜统计｜人工签核
- 必填规则：字段必须出现；无内容时写“无”或“不适用+原因”，不得留空。
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

## TPL-R10-41 场景状态首尾闭合模板

- 触发条件：同场景存在两个及以上连续镜头
- 字段顺序：scene_id｜shot_id｜start_state｜end_state｜人物位置｜身体朝向｜视线落点｜手持道具｜门窗状态｜动作进度｜光源方向｜transition_explanation
- 必填规则：七项状态向量均不得留空；上一镜end_state必须等于下一镜start_state。
- 失败标准：瞬移、朝向跳变、道具跳变、门窗变化、动作断裂或光源跳变且无画面交代即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-42 镜头颗粒度与演员表演链模板

- 触发条件：正式拆镜、台词镜和重要情绪镜
- 字段顺序：shot_id｜core_function｜viewpoint_count｜camera_position｜main_action_stage｜detail_shot_function｜transition_function｜stimulus｜listen_or_observe_reaction｜gaze_path｜micro_expression｜ecological_action｜body_or_breath｜end_state
- 必填规则：单镜一个核心阶段、一个视角；带台词/重要情绪镜七项表演链完整且符合可见性。
- 失败标准：多视角、隐形切镜、站桩对白、只动嘴、动作只写结果即fail；无功能特写warning。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-43 Seedance 2.5独立正式执行分镜模板

- 触发条件：生成任何Seedance 2.5/i2v正式批次或10列正式母版
- 字段顺序：批次编号｜覆盖镜头｜时长｜起始画面｜起始表演状态｜动作过程｜听话反应｜结束状态｜摄影机｜声音｜环境动态｜光影｜独立执行提示词｜主运镜｜生成限制
- 对外列名：`Seedance 2.5独立正式执行分镜`
- 必填规则：每格可脱离历史上下文独立执行；默认4–15秒；单批一个主运镜；Seedance阶段禁止BGM；时间戳只进入模型专用提示词。
- 失败标准：出现承接上一镜、保持关键帧、同上、延续之前等代指，或独立十字段缺失即fail。
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
- 必填规则：先预算后镜头；保护对白按2.8汉字/秒加停顿计算；所有覆盖指标必须100%；单镜一个核心阶段但允许同阶段连续动作链。
- 失败标准：最低时长超预算、事件未进节拍、固定镜头数挤压高潮或任一覆盖率不足100%即fail。
- 输出状态：pass / warning / fail_current / unable_to_check

## TPL-R10-48 公共场景人物与环境音生态模板

- 触发条件：街道、闹市、市集、商街、巷道、城门、码头、驿站、公共餐饮、寺院、店铺公共区等公共流动场景
- 字段顺序：shot_id｜scene_id｜location_type｜empty_scene_state｜empty_scene_reason｜crowd_density｜ordinary_roles｜foreground_actions｜midground_actions｜background_actions｜near_audio_sources｜mid_audio_sources｜far_audio_sources｜dialogue_ducking｜visible_audio_sources｜offscreen_audio_sources｜seedance_people_match｜seedance_audio_match｜continuity_transition｜status
- 必填规则：非豁免场景必须填密度、地点角色、错峰动作和空间层次；建立/全/远景至少三类三层声源，其余至少两类两层；对白镜必须避让；Seedance独立复现。空场必须同时填状态和剧情原因。
- 失败标准：只写人群、路人、环境声或街道嘈杂声；匿名群演清晰说话；时代错位声源；声画无源；批次未复现；同场生态无过渡跳变即fail。
- 输出状态：pass / warning / fail_current / unable_to_check
