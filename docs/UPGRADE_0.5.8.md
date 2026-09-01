# 0.5.8：历史 Hub 交接投影严格只读兼容

0.5.8 修复旧 `prepared production_to_audit` 交接在受控生产预检证据补签与恢复预演中的投影形态兼容问题。公共工具仍精确为 19 个，补签、恢复、确认字面量、工单权限与不可变边界均沿用 0.5.7 契约。

## 只接受两种精确形态

服务读取历史 Hub 交接投影时，只接受以下任一完整相等关系：

1. **精确 stripped projection**：与签名 handoff 完全相同，但去除签名信封字段 `record_id`、`project_id`、`integrity_hmac`；
2. **精确 full signed record**：与完整签名 handoff 完全相同，包括上述三个信封字段。

这不是宽松字段过滤。以下任一情况都必须在计划返回或持久化写入前失败关闭：

- 只保留部分信封字段的混合形态；
- 增加未知字段；
- 删除业务字段或信封字段；
- 修改任一业务字段、`record_id`、`project_id` 或 `integrity_hmac`；
- 将一个 handoff 的信封与另一个 handoff 的正文拼接。

## 兼容边界

- 兼容判断本身只用于读取并核对历史投影，不会单独迁移、规范化、回填或改写 Hub 行。只有用户另行授权的恢复事务会按既有契约把旧交接标记为 `superseded`，并将该状态及新 successor 写成标准 stripped Hub 投影；权威签名历史记录不删除。
- 不修改旧 handoff、旧生产证书、分镜、对白、canonical、XLSX、源文、边界或历史生产/审计/修复记录。
- production 窗口不得签发、推断、复制或复用独立审计结论。
- 未来生产路径写入 Hub 时统一使用 stripped projection，签名记录仍保存在其权威签名存储中。
- 已存在的 full signed Hub 行只在用户已授权的补签/恢复事务中按原契约参与核对；兼容读取本身不产生任何写入。

## 受控补签与统一恢复

生产预检证据补签仍须使用绑定当前 production 窗口的独立 `rule_improvement + execute + explicit_execute` 工单，先以 `phase=preflight_evidence_backfill` 预演，再以完全相同的 handoff 列表和 `plan_sha256` 配合 `REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE` 执行。

证据齐备后，交接恢复使用另一张绑定当前 production 窗口的同类工单，先以 `phase=handoff_recovery` 预演，再以完全相同的列表和计划哈希配合 `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT` 执行。旧 handoff 只标记 `superseded`，不删除；新 handoff 固定为 `prepared + PENDING_AUDIT`。

## 发布门禁

0.5.8 必须在同一冻结提交上连续通过三轮完整回归。专项至少覆盖：

- exact stripped 与 exact full signed 两种正向形态的 plan、execute 和幂等重放；
- 服务预演、补签存储 CAS 与恢复存储 CAS 三层一致的兼容判断；
- 部分信封、额外字段、缺失字段、业务字段漂移及三个信封字段分别漂移的零写入拒绝；
- 未来生产 Hub 写入只产生 stripped projection；
- 兼容读取前后历史记录字节不变；
- 精确 19 工具、stdio/HTTP 冒烟、当前 0.5.8 版本与有效全量回归证书。

部署后重新调用 `get_agent_status(full_self_test=true)`。只有线上状态明确为 0.5.8、运行时 PASS、证书与当前完整指纹一致且工具集精确为 19 个，才可对真实项目继续补签与恢复。
