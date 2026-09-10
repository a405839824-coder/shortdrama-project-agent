# 剧情生产、修改与审计约束

适用：章节理解、剧情诊断、改编/分集方案、人物线、剧本、对白及这些内容的修正建议。它们使用剧情证据和场景依赖；不是把分镜的 27 字段、三阶段、摄影或眼部合同强加给散文。

本文件规定工作方式和验收边界，不证明在线插件已经部署，不证明真实首批已经达到 98%。运行服务是否接入、具体检查是否执行、内容是否合格，分别以当前工具能力、实际返回和独立审查记录为准。

## 1. 原文事实、推断和改写授权

开始写作前，记录原文版本/哈希、任务范围、用户目标、必须保留的关系和事件、允许改写的内容及跨批承接事项。每项保留事实绑定原文位置；每项授权变更绑定用户的实际授权范围。至少区分明确事实、作者推断、必要未知和已授权改编决定。

事实抽取表本身可以错。复核必须双向进行：从原文找清单遗漏，从清单回查引用是否支持所声明的主体、条件、否定、数量、时间与结果。引用存在、哈希一致及作者填写“已核对”都不等于含义正确。必要事实矛盾或缺失时，保留未决原因并暂停依赖它的场景；继续没有该依赖的工作。

用户授权重构人物或情节时允许改变相应事实。不能以保真为由锁死全部原文，也不能为爽点自行更改受保护血缘。基线抽错或用户批准新版本时，保存旧基线和变更原因，作废依赖旧值的计划、正文核对结果和签证，然后复查影响范围；不能为了保留旧“通过”拒绝纠错。

## 2. 每场先决定事件与状态，再写正文

对本批每场写清时间、地点、在场者、每人的已知信息及获得事件、权限、相关物件及持有人、本场事件的前置条件和结束状态。人物的认知只能从自己的已知信息和实际发生的信息获得事件推出；作者、读者或另一人物知道，不等于本场人物知道。

在扩写正文前检查场景计划中的引用、事件顺序、地点承接、认知来源、权限与持物条件。显式关系不成立时先修决定。通过计划结构检查，仍须保留对事实抽取与因果解释的语义待审项；不能把计划格式正确当作故事正确。

判断依据是含义：删掉“重生”而改写成“上一世的结果”并未消除提前认知；正常引用或否定句出现同一词语也不是自动违规。没有相关动作或道具时不要求虚构信息来凑满表格。

## 3. 本批与后批依赖闭合

给需要后续使用的关系、能力、物件、财产和约定登记前置事件、获得者、成立时间及消费它的后场。未发生的计划不能当成已经完成的取得、授权或归来。

允许按改编目标移动、合并或重做铺垫，但须同时处理后续依赖。例如后批使用见面礼或铺契，前批应建立取得来源；若用户授权删除财产线，应同步重构后文，而不是强制恢复原文。仅计划在未来发生且本批没有提前消费的事项，记为未来安排，不能按本批缺陷扣分。

跨批计划变化时记录受影响场景和依赖版本。旧正文即使字节未变，其所依赖的事实、授权或前置事件变了，也不能自动继承原审通过。

## 4. 正文与计划核对实际含义

先保存实际完整候选，再检查正文实际写出的主体、时间、地点、知情范围、数量、权限、物件变化和结果。逐项对照已核准计划，同时反向检查正文有没有计划之外的新事实；不能只检查作者给正文附带的状态表。

程序负责可确定的结构、引用、类型、显式依赖及版本匹配；自然语言是否真正表达对应事实，仍需阅读正文并给具体位置和解释。换说法不能掩盖同一错误，同样含义的合法写法也不应只因不同字面被拒。

作者自填“忠实=true”“问题=0”“语义通过”不能成为自己的通过证据。模型生成的语义观察同样需要独立审查其依据，不因使用了另一个模型或更高思考等级就自动可信。

## 5. 修正按问题与依赖确定范围

修正输入保留问题身份、被审版本、适用依据、根因、拟改变的事件/状态、前后影响场景，以及不受影响内容的依据。修正来源决定后，更新依赖它的正文和状态；失效检查结果不得沿用。复审可以承接有证据证明完全不受影响的部分，须如实称为完整差异复核，不能冒称新盲读。

保留已正确且无关联的内容，避免无目的全文重写；但允许跨场、跨章的必要重构。不能用“只改报错句”“最多改一次”等次数或字数限制掩盖源头错误。用户已授权范围内不逐条重复索取确认。若提出的变更超出原授权，先完成不依赖该决定的工作，再明确尚需用户决定的范围。

所有修改保存新版本。更换候选名、工单、目录、模型或把修订称为“重新生成”，都不能清除同一任务的首稿失败记录。

## 6. 审计者和修正建议也必须给证据

审计前固定要求及生效范围。每条发现包含：被审版本、适用要求、原文/用户设定依据、正文位置、不能同时成立的关系、影响场景和分类。

| 分类 | 处理方式 |
|---|---|
| 确定违约/内容错误 | 给出具体反证，计入对应受影响单位，进入修正闭环 |
| 必要依赖未决/未检查 | 说明缺哪项材料或检查，保持未知，不当作正确或已证错误 |
| 可选文风/节奏建议 | 明示可选，不扣正确率，不强制作者执行 |
| 合法后批安排 | 记录将来验证点；没有被本批提前消费时不当成本批错误 |
| 审计规则发生变化 | 记录新旧版本和适用范围，不追溯改写旧规则下的成绩 |

修正建议实施前也须检查四件事：它是否解决原因而非只换措辞；是否新增未经依据支持的事实/认知/权限；是否处理前后依赖；建议本身是否违反同一标准。建议与正文适用相同规则。若先推荐某表达，后发现其确实违反原有要求，应记录审计建议错误或先前漏检，不能无解释地将责任全部计为作者新错。

固定要求不禁止发现旧规则下的遗漏。争议回到实际原文、授权和正文裁定，允许撤销误报；不能把审计轮数多当成正确性证据。

## 7. 完成声明与证据阶段分离

| 可用表述 | 需要的实际证据 | 不能代表 |
|---|---|---|
| 已修正 | 指定问题对应的新版本和差异 | 全稿正确或独审通过 |
| 计划/结构检查通过 | 当前输入、规则版本、逐项结果及未检查范围 | 正文语义已核实 |
| 独立内容审查完成 | 审查者独立职责、准确正文/计划版本、完整预定范围与逐项依据 | 没有未知就自然取得用户签核 |
| 正式冻结/签核 | 当前流程要求的真实发布或签核记录 | 初稿天然正确、旧版本被替换抹除 |

审查者不能只读上一轮问题表就声称覆盖整批。对每个要求与场景记录 pass/fail/unknown/not_checked，并注明适用范围。必要项未检查或未决时不能宣布完整通过；不适用需说明依据。关键词归零、引号配对、页面无溢出或软件回归通过都不证明剧情可冻结。

摘要同时给出正文问题、具体未决依赖和工作流证据缺项。缺签核不制造不存在的剧情错误；真实正文错误也不能藏进“只缺证据”。哈希用于绑定版本，不能替代独立语义判断或授权签名。

## 8. 首批与修订成绩不重置

任务开始固定计分单位、要求、批次/每集范围和首稿定义。保存首次完成正文的原始字节及版本；第一次完整正文之后发生的任何内部重写都属于修订，无论是否展示给用户。正文之前的事实整理、计划预检、构建拒绝另记成本和尝试，不改称零成本一次成功。

服务端不能观察没有调用工具的聊天草稿或模型内部思考。完整正文产生后立即提交原稿留痕；如之前已经有未记录重写，必须披露，不能因服务器只看到一次提交就宣称强制保证了一次生成成功。尝试账只证明实际登记的范围。

报告至少分开：计划/构建尝试与拒绝、首次完整正文、首次送审、每次修订、当前独审及最终签核。失败、拒绝和未完成尝试全部可追溯；不能只评价通过筛选留下的稿件。未形成正文的尝试不虚构逐场 0%，但必须计入生产尝试记录，不能从“首试成功”统计中消失。

以预定单位计算正文正确率与直接可用率，并分别给已证合格数、确定缺陷数、必要未知数和总分母。未知不进入已证合格分子，检查覆盖不足时不声称真实首批成绩已确定。受影响单位按并集计数，不把一条错误重复几次就算几处新错；不能临时拆空场提高通过率。

用户目标为可用率均 ≥98% 时，整体及每集的正确率和直接可用率都须以未四舍五入的比例达到 98%，不能以总体均值掩盖低分单集。分镜使用其现有逐镜评分合同；剧情必须预先明确自己的场景/要求单位，不借用软件用例分母。最终正式准入仍服从全部必要门禁和独立证据，不因达到 98% 就忽略阻塞项。

## 六组必须保留的验收对照

这些是预期标准，不是本文件已经执行并通过的测试。

| 原问题 | 正常情况应允许 | 错误情况应发现 |
|---|---|---|
| 兄弟血缘混线 | 同一事件使用不同文风，保留已锁关系；经用户授权可建立新的关系基线 | 幼亡无后胞弟未经授权被改成孟子平父亲 |
| 人物认知提前 | 根据已见行为怀疑对方另有依据；正常引用/否定不因关键词被误拒 | 没有信息来源却知道另一世具体结果，换同义表达也仍属同错 |
| 人物瞬移 | 明确归来事件成立后再次出府 | 已出府，既无返程也无场景重设依据，却接着从府内出发 |
| 权限歧义 | 三处差事通过具体物件和职责交接 | 文本把有限差事暗示成未获授权的全府权力 |
| 赠礼链断层 | 保留赠礼；或依授权同步重构后续财产线 | 后文使用尚无取得来源的见面礼或铺契 |
| 审计建议制造返工 | 建议自身符合相同事实、授权和文风要求 | 推荐某表达后，又不说明规则变化或原建议错误，直接以该表达判作者新错 |

## 接口与运行边界

剧情路线包含以下服务工具。参数中的 ID 和哈希使用本项目实际返回值，不猜造；下表中的 `plan`、`candidate`、`review` 和 `repair` 遵循当前剧情结构合同，不能填成自由文本 PASS。

| 工具 | 参数及用途 | 实际证据 |
|---|---|---|
| `preflight_narrative_plan` | `project_id, work_order_id, plan`；冻结计划并运行明确关系预检 | `data.plan_id`、`plan_sha256`、`check`；计划检查不等于独立内容通过 |
| `read_narrative_record` | `project_id, record_id=None, offset=0, limit=20000`；首次用 `record_id="contract"` 分页读本协议；省略 ID 列历史，指定其他 ID 读准确记录 | 核对实际记录；需要原文正文时另用 `read_project_artifact` |
| `review_narrative_artifact` | `project_id, work_order_id, target_id, review`；使用独立 `narrative_audit/audit_only/read_only` 工单，`review.target_sha256` 必填 | `target_id` 为计划 ID 或正文 `publication_id`；目标哈希分别绑定计划哈希或正文 `artifact.fingerprint` |
| `record_narrative_attempt` | `project_id, work_order_id, outcome, reason`；`outcome` 为 `refused`、`incomplete` 或 `failed` | 保存没有完整正文的实际尝试；不能据此编造逐场成绩 |
| `commit_project_artifact` | 保留原项目/工单/文件参数，新增 `narrative_candidate={plan_id, candidate, previous_attempt_id?, repair?}` | 返回 `artifact`、`attempt_id`、`publication_id`、`first_candidate_id`、`candidate_ordinal`；正文仍待独立审查 |

`script`、`dialogue` 只用 `text` 或 `markdown` 输出；`content_text` 留空，不并行传 `structured_data`。`source_artifact_id` 必须显式传入，且与已审计划的 `plan.source_artifact_id` 一致，不能因通用签名允许省略就省略剧情提交所需的绑定。服务端以各场 `scene.text` 按双换行组成实际正文，避免另交一份与被检查候选不同的文本。修订绑定真实上一尝试及修正资料，不能把新工单或新计划当作清空首稿历史的理由。

实际顺序：

1. 首次以 `read_narrative_record(record_id="contract")` 完整读取当前协议（有 next_offset 就续读），再读取注册原文并准备计划。原文 `source_sha256` 绑定原始文件字节，`source_text_sha256` 绑定提取全文；二者不可混用。
2. 生产工单调用计划预检，保存返回的计划 ID、哈希、逐场检查及阻塞依赖。
3. 独立 `narrative_audit` 工单读取计划记录和实际原文，按该计划哈希提交审查。只有其 `clean_scene_ids` 所准许的场景可以进入正文生产；计划结构通过不跳过这一步。
4. 必要未知或结构阻断的场景及其依赖保留未生产原因，其余场景可以继续。部分候选可以保存和接受限定范围审查，但始终标明未覆盖场景，不能以部分通过调用 `complete_task_validation` 宣称整批通过。
5. 生成后立即提交 `narrative_candidate`。成功保存的正文状态仍为 `draft`；用返回的 `publication_id` 和正文 `artifact.fingerprint` 发起独立正文审查。
6. 依据审查证据修正源决定及影响范围，保存新尝试和新正文。源文件或计划当前版本发生变化时，旧结论失效，按新哈希重新审查；部分正文无变化不能证明其依赖仍然有效。

同一目标再次审查时，在服务提交的 `review` 中增加 `supersedes_review_id`（当前最新审查记录 ID）及非空 `change_reason`，解释新发现、纠正误报或评价变化；首审不填这两项。这不禁止发现新问题，而是保留为什么改变判断的证据。`target_sha256` 和这两个历史字段由服务层处理，不属于纯合同的 review 结构。

工具核查身份、版本、结构与覆盖关系，不会自动替审查者阅读正文或完成独立自然语言判断。必须先实际阅读，再填写逐场依据；不能从“接口接受审查提交”推断模型判断正确。

### 内容结构

以下名称对应 `server/shortdrama_mcp/narrative_contract.py` 的 `narrative-contract-v1`，不是额外设计一份相似 schema。对象采用精确键集合；不要附加自签 PASS 字段。ID 是 `[A-Za-z0-9_.:-]` 稳定标识，中文显示名称放 `characters[].name`。偏移是提取文本的字符位置，区间为 `[start,end)`；哈希是小写 SHA-256。

| 对象 | 字段与约束 |
|---|---|
| `plan` | `contract, source_artifact_id, source_sha256, source_text_sha256, source_scope, source_map, authorizations, goals, characters, locations, permissions, props, facts, prior_events, initial_state, scenes, future_dependencies` |
| `source_scope` | `{start,end}`；`source_map` 按原文顺序连续覆盖整个选定范围，无空洞、重叠或漏尾 |
| `source_map[]` | `id,start,end,text,fact_ids,treatment,authorization_id`；treatment 为 `retained/authorized_change/context/unknown` |
| `authorizations[]` | `id,request_span,fact_ids`；`request_span={start,end,text}` 指向工单中实际用户请求，不能用模型编写的授权句替代 |
| `goals[]` | `id,text,authorization_id`；目标引用实际授权，不凭空声明用户偏好 |
| 人物及实体 | `characters[]` 为 `{id,name}`；`locations/permissions/props` 为稳定 ID 数组 |
| `facts[]` | `id,subject,predicate,value,source_spans,policy,authorization_id`；policy 为 `locked/adaptable/unknown`，adaptable 绑定实际授权，source_spans 逐段绑定原文 |
| `prior_events[]` | `id,fact_ids,knowledge,permissions,ownership`；分别明确已发生的信息获得、权限取得与物件取得记录 |
| `initial_state` | `locations,knowledge,permissions,ownership`；每人地点和知情/权限列表明确，取得记录均引用 `via_event_id`；未知不能用无依据已取得代替 |
| `scenes[]` | `id,start,end,location_id,present,depends_on,events`；v1 用非重叠的故事发生时间顺序，时间是整数顺序标记，不是分镜秒数预算 |
| `events[]` | `id,at,kind,actor_id,requires,uses_facts,data`；事件时间在本场范围内，requires 只能依赖已成立事件 |
| `candidate` | `contract,plan_sha256,scope_scene_ids,scenes`；各场为 `id,text,event_ids,end_state`，顺序与计划相符，末态核对实际计划推演结果 |
| `review`（纯合同） | `contract,stage,plan_sha256,candidate_sha256,scene_checks,source_to_facts,facts_to_source,issues`；stage 为 `plan/candidate`，计划审查的 candidate_sha256 为 null |
| `repair` | `contract,plan_sha256,previous_candidate_sha256,candidate_sha256,items,rechecked_scene_ids`；绑定真实新旧候选，不能用一份通用“已全部修好”覆盖 |

嵌套状态与审查映射也使用精确键，空数组表示没有已取得事项，不能省略人物：

- `prior_events[].knowledge[] = {actor_id,fact_id}`；`permissions[] = {actor_id,permission_id}`；`ownership[] = {prop_id,owner_id}`。
- `initial_state.locations` 为人物 ID 到地点 ID 的字典；`knowledge` 为每人到 `{fact_id,via_event_id}` 数组；`permissions` 为每人到 `{permission_id,via_event_id}` 数组；`ownership` 为物件 ID 到 `{owner_id,via_event_id}` 字典。取得事件必须在 prior_events 中确实支持该声明。
- `future_dependencies[] = {id,required_event_id,consumer,mode,authorization_id,replacement_event_id}`，mode 为 `preserve/authorized_change`；consumer 说明后续用途，改写须填真实授权；保留后续用途时填替代建立事件，已授权整条删除时可为 null 并在 consumer 说明后文如何一并删除。preserve 分支的授权及替代字段均为 null。
- 计划审查 `source_to_facts[] = {source_map_id,fact_ids,status,basis}`，`facts_to_source[] = {fact_id,source_map_ids,status,basis}`；status 为 `consistent/unknown/issue`，两向均逐项覆盖已声明的真实映射。
- `candidate.scenes[].end_state` 使用本次计划预检 `check.state_trace` 中该 scene_id 的 end_state 完整对象。它包含推演后的 locations、knowledge、permissions、ownership、fact_values、completed_events；不能省字段、不能用它替代实际正文核对。

事件 `kind` 与 `data` 的键必须匹配：

| kind | data |
|---|---|
| `move` | `from_location_id,to_location_id` |
| `reveal` | `fact_ids` |
| `learn` | `fact_id,via_event_id` |
| `assert_known` | `fact_id` |
| `grant_permission` | `permission_id,to_actor_id,required_permission_id` |
| `use_permission` | `permission_id` |
| `transfer_prop` | `prop_id,to_actor_id` |
| `use_prop` | `prop_id` |
| `assert_fact` | `fact_id,value` |
| `change_fact` | `fact_id,value,authorization_id` |

这是有限的显式事件模型。v1 按故事发生时间顺序检查计划；倒叙或多时间线必须显式分离故事事件顺序与呈现安排，并获得相应内容审查。当前程序不能声称已经验证任意叙事重排，也不能通过任意重排正文绕过门禁。接口尚不能表达的必要决定要保留能力缺项，不能将其判为用户的创作目标错误，或伪装成另一个支持的动作后宣布通过。剧情可改编通道不解除既有正式分镜的字面保真约束；后续分镜仍须依据其实际注册来源和适用合同验收。

`scene_checks` 对被审范围每场覆盖八个固定规则：`source_fidelity, identity_relations, knowledge, time_location, authority, props, causality, goals_style`。这是剧情规则，不是分镜八层。

每条检查包含 `scene_id,rule_id,status,basis,source_map_ids,fact_ids,candidate_spans,issue_ids`；status 是 `consistent/not_applicable/unknown/issue`。正文检查必须引用该场实际文字的 `candidate_spans={scene_id,start,end,text}`，不适用也给具体依据。计划审查没有尚未生成的正文证据，其 candidate_spans 留空，并完整提交原文→事实及事实→原文双向核对；正文审查的两份来源核对数组为空，不能重新冒充计划已审。

`issues[]` 包含 `id,kind,rule_id,location,basis,conflict,impact,suggestion`；kind 为 `hard/unknown/style/future`。`location` 的键为 `scene_id,event_id,source_map_id,candidate_span`，至少一项真实定位。`suggestion` 可以为 null；有建议则为 `{change,answers}`。repair.items 各项为 `issue_id,root_cause,changed_scene_ids,affected_scene_ids,resolution,answers`。两处 answers 均逐项填写 `causal_or_wording,new_facts_knowledge_authority,affected_dependencies,same_rule_check` 四个问题的实际回答，不复制空泛保证。

### 参数拼装示例与公开结构样例

完整公开结构见仓库 `tests/test_narrative_contract.py`：`make_plan()` 返回 `(plan,source_text,request_text)`，`make_candidate(plan)` 给正文封套形状，`make_review(plan,candidate=None)` 给审查形状，`make_repair(plan,previous,candidate)` 给新旧绑定形状。它们是合成测试资料；尤其 make_review 内的“consistent”和统一说明绝不能复制成真实稿的独立审查结论。本次文档同步未执行这些 helpers 或真实稿件生成。

下面只演示工具参数拼装；变量均来自已登记的实际对象，省略了工单创建和真实内容。不是一份可替代事实整理或独审的默认答案：

```python
plan_args = {
    "project_id": project_id,
    "work_order_id": production_work_order_id,
    "plan": plan,
}
# preflight_narrative_plan(**plan_args) 的实际返回供后续绑定。
plan_review_args = {
    "project_id": project_id,
    "work_order_id": independent_plan_work_order_id,
    "target_id": plan_id,
    "review": {**actual_plan_review, "target_sha256": plan_sha256},
}
# 取得计划独审准许范围后才写 candidate，不在这里创建审查结论。
commit_args = {
    "project_id": project_id,
    "work_order_id": production_work_order_id,
    "artifact_type": "script",
    "output_format": "markdown",
    "file_name": "剧情正文.md",
    "source_artifact_id": plan_source_artifact_id,
    "narrative_candidate": {"plan_id": plan_id, "candidate": candidate},
}
# 成功保存仍是 draft。publication_id 与 fingerprint 来自该次实际返回。
body_review_args = {
    "project_id": project_id,
    "work_order_id": independent_body_work_order_id,
    "target_id": publication_id,
    "review": {**actual_body_review, "target_sha256": artifact_fingerprint},
}
```

`plan_source_artifact_id` 取自已审计划的 `source_artifact_id`；它不是本次正文 publication ID，也不是上一稿 artifact ID。修订在封套中明确添加真实 `previous_attempt_id` 和 `repair`。同一目标的再次审查才向 review 添加最新 `supersedes_review_id`、实际 `change_reason`。不得把上例变量名当字符串 ID 直接提交，不得手写“第一个候选”覆盖返回的 `first_candidate_id/candidate_ordinal`。

这条路线与 `preflight_storyboard_draft`、`commit_storyboard`、`audit_storyboard` 分开。一个名为 `storyboard_plan` 的文件可以作为后续分镜源图载体，但文件名/类型本身不证明剧情已经审过；必须保留实际计划及内容审查证据。
