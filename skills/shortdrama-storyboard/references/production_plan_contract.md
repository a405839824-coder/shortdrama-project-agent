# 冻结生产计划输入合同

0.5.37补充见[source_actions_0537.md](source_actions_0537.md)：`source_report`只能登记已发生的原文禀报收悉；心声不能被heard；桥接不能消费剧情或取得知情。既有heard语义保持不变。

0.5.33 新 MCP 生产请求和官方生产 CLI 要求来源图中包含 `production_plan`。
完整规则由 [production_prevention_contract.md](production_prevention_contract.md) 提供，唯一来源是 `rule_registry.json.runtime.production_prevention_policy`。
本文件说明输入格式；不能单独改变规则优先级或把语义待审改为通过。

## 生产顺序和计量

1. 固定授权原文与设定，完成来源事实图。事实图仍需独立核对，不因跨度或哈希相同而被认定解释正确。
2. 在正文生成前确定人物世界、事件、导演选择、知情范围和各镜交付要求，将下面的计划与来源图一起冻结成独立项目资料。
3. 读取 `get_agent_status.data.production_prevention_contract.context`，填入当前规则和模板指纹。填写实际来源与初始世界指纹。
4. 生产输入引用冻结计划中的决定，使用 `production-authoring-request-v1` 调用预检。服务器先保存原始输入，再检查；不会自动创建、补全或刷新缺失的计划。
5. 失败回到对应决定，核对原文和合法例外，更新完整依赖范围。计划更改和生产重试分别留档，不能将改好的版本重新称为首次尝试。
6. `data.compiled_storyboard` 用于后续预检和提交。正式母版、GPT、阅读页保持同源；低层渲染或手工 canonical 只属兼容路径，不代表执行过本计划。

新增计划是生产前约束。计划自己存在错误的可能，所以仍须核对事实前提、导演选择、知情证据和表演自然度。生成后拒绝坏稿不等于首批正确率提升。

## 顶层结构

`production_plan` 必须恰好有 `contract`、`context`、`decisions`、`knowledge`、`shots`、`narrative` 六项。

- `contract`：`storyboard-production-plan-v2`；旧v1不再用于当前新生产，不自动补齐缺失信息。
- `narrative`：场景时间、切场作用、观众因果交付和有源悬念，见 [scene_narrative_contract.md](scene_narrative_contract.md)。源文事实图必须保留必要触发/获知/决定/结果；源文存在不表示已在分镜中交付。
- `context`：`rule_registry_sha256`、`template_contract_sha256`、`source_sha256`、`initial_world_sha256`。前两项来自当前状态工具；`source_sha256` 与来源图的 `sources` 一致，源文本实际指纹也必须相符；初始世界指纹为 UTF-8、键排序、无多余空白、保留中文的有限 JSON SHA256。
- 规则、来源或初始世界变化后，旧计划失效。源图自身的字节哈希由原有请求参数和项目资料哈希另行双重校验，不在图内自指。

## 决定依据 decisions

每项恰好包含 `path`、`basis`、`fact_ids`、`source_spans`、`value`。

`path` 采用固定、不重叠的分区：每个 `/initial_world/scenes/<id>`、`actors/<id>`、`props/<id>`、`points/<id>`；每镜 `/shots/<index>/camera`、`ticks`、`transition`、`editorial`、`shot_id`、`shot_uid`、`episode_id`、`scene_id`、`continuity_id`、`source_unit_ids`；以及每个 `/shots/<index>/actions/<index>`。身份中的 `~`、`/` 分别使用 JSON Pointer 的 `~0`、`~1`。必须覆盖实际输入的完整集合，不能用一条“全部内容”代替，也不能遗漏或重复。

`value` 是冻结时该分区的准确 JSON 值，运行时与真实输入比较。它不是模型填写的“已检查”标签。

| basis | 所需依据 | 边界 |
|---|---|---|
| source_fact | 非空、实际存在的 fact_ids | 编号和跨度不证明蕴含，独立检查事实是否支持决定 |
| locked_setting | 非空、逐字匹配的 source_spans | 不接受没有原句的自称锁定设定 |
| director_choice | 允许无原文摄影细节，但必须符合锁定事实和整镜交付条件 | 不能用它更改原句、伪造事实或绕过来源编译 |

`source_spans` 每项恰好含 `source_id`、`start`、`end`、`text`，使用字符下标 `[start,end)`。无跨度时显式填写空数组。
`derived_state` 专供程序派生，不允许作为自由填写的输入依据；摄影机左右、起末状态、口型交集和输出字段由既有编译器计算。

## 知情范围 knowledge

必须含 `initial`、`acquisitions`、`uses` 三个数组。

- `initial`：每项 `{actor, fact_ids, evidence_fact_ids}`。记录角色开始时已知事实及原文依据，禁止重复。依据能否证明该角色知情，由独立语义核对完成。
- `acquisitions`：每项 `{id, actor, fact_ids, evidence_fact_ids, action_id, mode}`。`mode=heard`：引用真实对白事件，内容必须包含相关事实，听者与说者在事件期间同场，不能自我授予听取，事件完成后才更新知情。场景含声明遮挡时，声传播不在模型范围内，返回 `not_supported`。隔墙听取、读心、复杂看见/认出等不能假装已经建模。
- `uses`：每个真实动作恰好一项 `{action_id, actor, fact_ids, stance, evidence_fact_ids}`；来源动作涉及的事实不能从这里删掉。`known` 要求有已知事实依据；`reported` 要求已经通过实际听取事件收到该信息；`suspected`、`mistaken`、`pretended` 要求非空事实与证据，仍须独立核对；`narrator` 仅用于无场内角色的客观旁白；`not_applicable` 只能对应不使用事实的动作。

检查采用实际事件顺序，同一时刻先完成听取，再检查依赖动作。听到某种说法只登记为已收到信息，不自动升级为事实已经证实；猜测、误解和伪装也不变为世界真相。不强制自然眨眼或无信息动作借用剧情事实。
支持状态独立的同时异地场景，实际获知按narrative的剧情时间计量。倒叙、非线性重排或同一事件重新取景的认知回放仍报告 `not_supported`；不把有限平行剪辑能力当完整时间旅行模型。

## 整镜交付 shots

每镜恰好一项，包含：

- `shot_id`：实际镜号。
- `purpose_fact_ids`：非空的本镜信息目的，必须由本镜实际声明使用的来源交付。
- `required_action_ids`：本镜必需事件，不存在则失败；允许静态镜头为空，不强制加动作。
- `audio_action_ids`：与实际对白/旁白事件集合相等。画外声合法，不强制可见口型。
- `source_use_ids`：本镜必须实际使用的来源用途。动作或当前可见文字能够承载；只写在来源列不算完成。
- `visible`：必要可见窗口，每项 `{kind, subject, start, end}`，时间单位是内部 0.1 秒，正文仍不显示数字制作时间码。

`kind` 支持 `actor`、`eyes`、`left_hand`、`right_hand`、`prop`、`current_written`。逐时间点从同一机位和实际世界状态检查，窗口采用 `[start,end)`，不允许重复或超出镜长。
`current_written` 仅证明在当前状态存在且道具点位入画，不能证明文字字号可读。复杂人体网格、袖口遮挡、像素级辨字应报告未支持，不能用“清晰可见”替代证据。

## 结果和兼容边界

`pass` 只表示该条已支持的声明与实际数据一致；`fail` 表示已检出的冲突；`insufficient_evidence` 表示必要计划或依据缺失；`not_supported` 表示超出当前模型。任何一种结构通过都不签署语义准确率、98%或正式发布。

旧来源图和低层纯函数可以继续用于历史证据检查，其生产计划状态明确为 `insufficient_evidence`。新 MCP 生产及官方生产 CLI 不允许省略计划。旧式 typed CLI 不是生产计划入口，不能被标为新生产验收；交付仍受既有预检、权限及独立审计约束。

每条规则需同时验证缺陷反例和相近合法情况。自动检查器误判时修检查器，不改坏正确内容。按来源前提、决定、时空事件、输出、检查器和证据缺口定位问题，不限制修正轮数。

## 0.5.46 逐镜设计绑定

每镜新增必填 `design`，精确形状及数值来源见 [shot_design_contract.md](shot_design_contract.md) 和在线 `production_prevention_contract.shot_design_policy`。
`framing_reason/cut_reason`说明实际用途；`spatial_anchors`为每个首帧入画主体指定一个已有、有来源依据、同场景的point。
`performance`覆盖本镜实际入画角色与说话者：逐人填写 `actor/fact_ids/stimulus/visible_choice/gesture_choice/voice_choice`；无声/无手势可成立，但须符合事实和前态。原因只留内部。
`duration_exception`普通镜为null；超4秒须填逐镜来源、确切所需时长、估计/实测依据、具体拆法及损失。程序不会以非空文字认证语义正确。
人物初态 `action_state.expression`与expression动作目标必须从在线 `action_contract.expression_poses`选择可见姿态，不再默认补平静；其余action_state形状保持既有合同。
`camera.frame`支持wide/medium/medium_close/face/face_detail/hands/object，中近景和近景计入60%观察；实际取景面积及可读性仍须独审。
当前重放只支持固定机位与镜间换位；单次连续运镜未建模时明确拒绝，不绕过为自由文本。支持多人入画，但模型只检查声明的人体点位，不能冒充像素遮挡验收。
