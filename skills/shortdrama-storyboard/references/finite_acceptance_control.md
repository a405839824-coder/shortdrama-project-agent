# 有限验收控制：执行边界

本控制只约束 host 配置的运行器与服务入口，不证明稿件准确率，也不是本机管理员不可绕过的权限沙箱。保留既有独立审计与签核要求。

## 冻结后才执行

host 在运行前冻结同一 scope/lineage 的合同：原始输入和验收标准的字节身份、候选代码、允许一次修正的精确文件清单、全部步骤和依赖、预期阶段/结果/原因/禁止后继状态。每轮每步只能 reserve 一次。reserve 持久成功即使用额度；崩溃、命令失败、重新启动进程或更改合同名称都不退款。旧输入及失败证据继续保存。

最多两轮整批验收。第一轮不匹配只能记录明确归因，允许范围内修正一次；不可改原标准、分母、scope 或预期。第二轮失败，或任何轮次出现未知归因、设计变化、执行中断，进入停止状态。成功也是终态。没有自动重设计、换样本、新合同续跑或第三轮边。

负例只有到达指定阶段、命中目标原因并避免全部禁止状态，才满足预期。无关输入错误先挡住下游，表示该目标未验证。单测断言、进程退出码、语义审计、生产可用率分别记录。CLI 的 process 结果只能说明冻结的检查程序是否成功，不生成语义 PASS。

## 服务配置与请求额度

由 host 配置 `SHORTDRAMA_ACCEPTANCE_CONTROL=1`、唯一 `SHORTDRAMA_ACCEPTANCE_REGISTRY` 与已 reserve 步骤的 `SHORTDRAMA_ACCEPTANCE_LEASE`。工具参数没有签发 lease、换 registry 或解除控制的入口。配额绑定实际服务方法、namespace、project、默认值展开后的业务参数 SHA256；transport session 不属于稿件身份。旧 compact 与 production-authoring 两条输入路径都经过同一外层守卫。

受控方法：`agent_status(full_self_test=true)`、`mark_performance_stage`、`create_project`、`migrate_project_state`、`recover_storyboard_handoff`、`diagnose_storyboard_artifact`、`preflight_storyboard_draft`、`begin_task`、`import_source_file`、`plan_change`、`commit_artifact`、`commit_storyboard`、`commit_storyboard_delta`、`commit_storyboard_patch`、`audit_storyboard`、`complete_validation`、`record_capability_gap`、`signoff_task`。同一外部操作中的同服务/同项目内部调用属于该外部步骤，不另给作者可重复调用的额度。缺失、失效、跨入口、跨输入、超量 lease 在该服务操作写入前拒绝。

只读 `agent_status(false)`、工件读取、问题查询和身份清单仍可使用；它们不能生成稿件或另行开启完整自检。创建项目没有可预知 project ID，因此已有 project 合同不允许借 create_project 重开范围。测试 fixture 的项目准备必须作为冻结测试程序的明确部分计入轮次。

普通服务默认报告 `legacy_uncontrolled`，维护兼容性但不提供这些预算保证。用户要求受限时，禁止借未启用的旧服务续跑。控制配置同时出现而开关关闭会拒绝启动，避免静默降级。本地有全部文件权限的人仍可编辑程序、账本或改 host 配置；更强隔离需另部署到由控制方持有权限的进程/目录，不能通过增加提示词声称已经实现。

受控模式的 Library `storage_action` 仅允许 `status`；登记、挂载、导出、提交和卸载当前没有接入有限合同，直接拒绝，不能绕过服务守卫。使用受控生产前由 host 在冻结清单中明确准备好输入；不得在测试失败后临时切换存储改范围。

## 输入与语义边界

`construct_production_input` 只复制明确作者决定。正式 production 入口先经 `record_attempt` 留原始字节，再执行统一 `inspect_production_input`，复用现有 schema、source、state、eye/listener 消费器；缺项不填入虚构人物认知、手位置或眨眼。合法输入才可编译，之后仍走完整生产校验、独立语义审计及正式签核。未知保留未知，不能因本控制器通过而宣称首稿 95%/98%。
