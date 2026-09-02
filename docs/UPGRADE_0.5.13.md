# 0.5.13：窗口角色接续与权限恢复

0.5.11 在保持精确19个公开工具的前提下，扩展既有`begin_project_task`，让因聊天上下文耗尽、窗口关闭或无法定位旧窗口而中断的五类分镜角色工单，可以在用户明确授权后受控接续到新的可信cloud MCP会话。可接续operation精确为`storyboard_create、storyboard_regenerate、storyboard_change、storyboard_audit、storyboard_repair`；`rule_improvement`、版本管理以及其他被归类为production的工单均不可借此接续。该能力恢复的是同一逻辑authority的执行能力，不创造新的角色，不改变工单范围，也不解除项目本身的质量或独立审计阻塞。

## 公共接口

`begin_project_task`保留全部既有创建参数，并新增以下可选参数：

- `action=create|plan_role_continuation|execute_role_continuation`，默认`create`，因此旧调用保持兼容；
- `resume_work_order_id`：只能指向同一项目、同一用户命名空间的一张既有工单；
- `expected_plan_sha256`：执行时必须原样回传预演哈希；
- `confirmation`：执行时必须精确等于`ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION`。

服务端只从既有工单派生角色，调用方不得提交或改变角色。生产只接受`storyboard_create/regenerate/change + execute + explicit_execute`，独立审计只接受`storyboard_audit + audit_only + read_only`，修复只接受`storyboard_repair + audit_and_repair + explicit_execute`。接续请求中的`operation、mode、authorization、targets、source_lock、protected_scope、expected_outputs`必须与既有工单精确等值；`window_key`和自然语言`request_text`都不是权限来源。本路线仅在cloud部署和可信宿主session中可用，local/stdio不能轮换authority。

## 预演与执行

1. 新窗口先调用`get_agent_status(project_id)`，读取下一步所需角色及可接续工单的安全摘要；状态不得泄露原始会话指纹。
2. 对一张工单调用`action=plan_role_continuation`。预演零写入，返回`status=planned`以及`action、resume_work_order_id、plan_sha256、confirmation_required、authority_root_id、role、operation、mode、authorization、authority_contract_sha256、pre_state_sha256、current_generation、next_generation、new_binding_status`。
3. 核对角色、工单、完整契约、当前状态和下一代后，以完全相同的请求调用`action=execute_role_continuation`，提交相同`plan_sha256`与精确确认字面量。
4. 成功执行返回`status=continued`以及`continuation_id、authority_epoch_id、generation、previous_generation、previous_status=revoked、plan_sha256、idempotent_replay、session_binding_status`。当该continuation仍是当前活动head时，前一请求已经释放独占operation lease后的同一目标会话同参重放，返回相同接续与epoch身份并标记`idempotent_replay=true`；若前一请求仍在运行，第二个同root请求必须返回`authority_operation_in_progress`，不能共享lease或join成并发幂等。若后续代已经推进，旧计划重放必须返回`role_continuation_superseded`，不能复活旧代。签名continuation已落盘但Hub head尚未投影的半写状态，只允许持有恢复路径的同会话同计划补齐这一代。

预演计划绑定精确工单契约与快照、目标服务端session、当前authority head和当前运行时证书。执行在项目锁内重新计算这些绑定并做head/pre-state CAS；工单契约或快照、目标session、运行时证书、authority head任一漂移都会使计划失配。活动operation lease以及当前工单拥有的未过期handoff、repair-package或draft-preflight commit claim，是plan和execute各自独立复核的零写阻断条件；已完成的claim、handoff或certificate变化不应被笼统描述为必然改变`plan_sha256`。0.5.11的plan不持久化、没有TTL或过期时间字段，不得在文档或验收中宣称存在尚未实现的计划时效门禁。

工单状态为`passed`时通常不属于可接续状态。唯一受限例外是：该工单必须仍是原`storyboard_audit`独立审计authority，并且服务端签名存储中恰有一个绑定该工单、状态为`prepared`的`independent_audit_successor`。此时plan将其识别为`pending_reaudit_successor`恢复，只允许新活动epoch领取这一条既有复审路线；零条、多条、未签名、非prepared、跨工单或伪Hub投影都必须零写拒绝。该例外不会重开已通过的根handoff、撤销既有PASS、生成第二条successor或授予生产/修复权限。

## Authority root 与物理epoch

- 一张工单保持稳定的`authority_root_id`；旧工单和所有历史handoff、证书、trace及原始WIN/ACT/session绑定不改写。
- 每次接续追加一条新的签名continuation记录，生成新的服务器物理窗口、actor、session和单调递增generation，并以CAS推进Hub中的唯一活动head。前一代因此逻辑撤销；服务不会回写旧continuation、旧工单绑定或其他历史签名记录来标记`revoked`。响应中的`previous_status=revoked`表达的是活动head语义，不是历史记录被改写。
- 每个后续authority写入在其下一次持久化边界复核稳定root的active generation；旧窗口或在途旧context通过入口后若head已经推进，必须在落盘前被fence。只有新签发handoff、repair package以及当前Schema明确声明这些字段的其他记录持久化root/epoch/generation；消费claim使用独立的token/claim-generation/lease契约。不得声称所有既有certificate或trace都被补写这些字段。
- `repair_to_audit`仍必须返回原独立审计authority root及原审计周期。受控接续后的当前活动后代可以复审，但另一个审计root、生产root或修复root不能接管。

## 活动租约与修复包

- role operation使用短期签名lease保护在途权威写入。该lease以logical `authority_root_id`为隔离键并严格独占；同root第二个请求无论是否同工单、同session或同payload，均返回`authority_operation_in_progress`，不得共享holders、join或同时执行。不同authority root各自持有独立lease，可以并行。lease仍存活时，接续plan和execute都必须零写拒绝；lease释放或自然过期后才可轮换。过期lease无需为了接续而改写，旧context会在下一次持久化边界因active generation不匹配而失败。
- `consuming` handoff、repair package和draft-preflight commit claim同样按其未过期lease阻止轮换。handoff与repair package租约过期后，合法重领使用新token和递增claim generation，并保留有限claim history。draft-preflight commit claim过期后不再阻止轮换，但原draft-preflight证书仍绑定旧session；新session必须重新取得自身绑定的有效preflight，不能重领或复用旧证书。
- 0.5.11的repair package消费权由`claim_token + claim_generation + bounded lease`闭合。0.5.10遗留的`consuming`包若没有完整lease字段，必须失败关闭；接续路线不得自动迁移、补造或把它当成已过期可重领。
- 普通handoff、repair package与独立复审successor的每次领取，都必须从领取工单当前活动head派生并签入`authority_root_id、authority generation、session fingerprint`，同时持有本次claim token、递增claim generation和未过期bounded lease。finish与release在同一项目锁内重新验证活动head、精确工单/角色、session、claim token/generation和lease；旧代次、被新claim替换的token、过期lease或旧session重放一律零写失败。租约过期只允许同一逻辑authority在当前活动epoch使用新token和下一claim generation重领，不会复活旧token。
- 普通handoff的`consuming → consumed`不是调用方自报完成。服务端必须读取真实certificate store中的签名`independent_audit`证书，并精确核对certificate ID、handoff ID、审计work order、artifact和签名完整性；缺失、伪造、自签、生产预检证书或任一绑定漂移都保持handoff不变。
- successor candidate的延迟发布不得拿早期`prepared`候选覆盖已经被独立审计claim或consume的签名handoff。发布收尾只能推进candidate artifact的事务/发布状态；签名handoff及其Hub精确投影的`consuming/consumed`生命周期、claim快照、claim generation、token与真实审计certificate绑定必须逐字保留。
- 已消费repair package的补偿回发不是普通重试。只允许固定同一`claim_token + claim_generation`及完整successor事务身份，在base artifact的successor transaction已经是`aborted`、签名`repair_to_audit` handoff与Hub投影同时是`status=rejected + publication_state=aborted`且没有handoff claim、消费快照、审计证书或任何独立审计下游引用时，把该包恢复为`issued`。任一身份漂移、base仍`prepared`或已`committed`、handoff/Hub未同时拒绝终止、或已存在审计下游，都必须失败关闭并保持repair package不可复用。

## 不变的分权和生产门禁

接续严格保持既有角色、操作、模式、授权和范围，且一次只处理一张工单、一个角色。只有上述五类operation可用；不能把一般production、`rule_improvement`或版本管理工单解释成可接续角色。生产会话历史不能接续独立审计或修复；独立审计会话历史不能接续修复；任何同会话自审、自修或跨角色轮换都失败关闭。

接续不等于项目解锁。只要存在`prepared`或`consuming`的生产/修复到审计交接，或上一生产工单仍等待独立审计，`get_agent_status`仍应返回`execution_mode=independent_audit_only`并禁止下一批生产。必须由独立审计authority完成闭环，状态恢复`normal`后，生产才能建立下一批工单。

本项目此前列出的11条失效`prepared production_to_audit`在0.5.11能力实现、文档更新或部署后仍保持“未恢复”。本升级不自动补签、supersede或重发它们，也不把production升级窗口变成独立审计或repair窗口。只有部署后取得当前构建的有效全量运行时证书，并分别使用两张符合0.5.10契约的production专用工单完成受控补签与恢复，才能生成待独立审计的新handoff；在此之前不得宣称11条已恢复或恢复生产权限。

## 发布门禁

发布前必须覆盖：旧`action=create`兼容、精确19工具、五类operation正例与`rule_improvement`等非支持operation负例、plan零写、完成后同参幂等与运行中不join、同root并发返回`authority_operation_in_progress`、跨root并行、范围/角色/模式/授权漂移拒绝、跨项目/用户拒绝、缺少可信cloud session拒绝、head/pre-state陈旧拒绝、活动operation lease和三类live claim零写拒绝、普通handoff/repair package/reaudit successor领取的root-generation-session-token-lease绑定、旧代次或过期token finish/release零写拒绝、普通handoff真实签名独审证书门禁、延迟candidate发布保持已claim/consumed签名handoff不变、passed audit仅在唯一签名prepared successor时受限接续、租约过期后新token/新claim generation重领、旧context在下一持久化边界被generation fence、历史签名记录不改写、0.5.10无lease consuming包失败关闭且不迁移、补偿回发的固定claim/aborted base/rejected+aborted handoff及Hub/零审计下游矩阵、跨角色会话历史拒绝、原审计root接续后的repair回审、错误root拒绝、pending audit继续阻断production、历史11条仍未恢复、重启恢复及HMAC/Hub活动head篡改拒绝。完整候选还必须通过全部技能、服务、stdio和HTTP回归，并在同一冻结提交上连续通过发布门禁；未实测前不得填写通过数或声称部署完成。
