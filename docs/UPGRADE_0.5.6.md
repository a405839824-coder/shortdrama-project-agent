# 0.5.6：受控分镜交接恢复

0.5.6 为完整指纹失效、但历史分镜与生产预检仍可验证的
`prepared production_to_audit` 交接增加 `recover_storyboard_handoff`。
它只恢复路由证据，不修改故事内容，也不替代独立审计。

## 权限与双阶段确认

- 只接受服务端绑定的 `production` authority。
- 工单必须是 `rule_improvement` 或 `version_management`、
  `execute + explicit_execute`，并精确包含能力缺口和全部 handoff ID。
- `action=plan` 无写入，返回逐条 before 身份、当前完整指纹摘要、
  新 ID、`plan_sha256` 和固定确认字面量。
- `action=execute` 必须回传同一个 `plan_sha256` 和
  `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`；任一输入漂移即失败关闭。
- 运行时、规则、schema、脚本、依赖或服务代码变化后，必须先通过
  `get_agent_status(full_self_test=true)` 签发当前全量回归证书。

## 单条原子恢复

每个 handoff 独立建立签名 `handoff_recoveries` 日志，并记录旧记录、
新记录和 Hub 投影的 before/after SHA-256。事务按以下安全顺序写入：

1. 持久化 `prepared` 恢复日志；
2. 将旧 handoff 标记为 `superseded`，使其立即不可被审计领取；
3. 写入按当前完整指纹和新生产预检证书绑定的新 handoff；
4. 更新 Hub 投影并校验全部 after 哈希；
5. 将恢复日志标记为 `committed`。

进程在任一步中断时，旧 handoff 会保持不可消费；相同请求只补全签名
日志指定的同一后继，不会生成第二个 handoff。CAS 外的修改不会被覆盖。

## 不可越过的审计边界

- 旧 handoff、原分镜、canonical、XLSX、生产证书和全部历史记录均保留。
- 新证书仍是 `production_preflight`，不得出现独立审计 verdict。
- 新 handoff 固定为 `prepared + PENDING_AUDIT`，缓存状态为 `miss`；
  任何旧独立审计结论都不能跨新的 handoff/scope 身份复用。
- 独立审计必须在不同 authority/window/session 下领取新 handoff，首次审计
  仍须提交完整八层语义证据。

## 发布门禁

部署前必须通过新恢复测试、现有服务/安全/打包测试以及五个冻结套件：

- 73 项 Golden Tests
- 25 项 R10-SPC-1 严格提示词测试
- 11 项公共场景生态测试
- 16 项 Agent 生命周期测试
- 22 项项目 Agent 测试

部署后重新运行 `get_agent_status(full_self_test=true)`，并仅在返回的运行时
证书与线上 0.5.6 服务代码指纹一致后执行真实恢复。
