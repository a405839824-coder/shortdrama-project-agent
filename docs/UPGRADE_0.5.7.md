# 0.5.7：旧交接生产预检证据受控补签

0.5.7 在不增加公共工具的前提下，为历史
`prepared production_to_audit` 交接增加生产预检证据受控补签。
插件仍精确暴露 19 个工具；现有 `recover_storyboard_handoff` 新增
`phase=preflight_evidence_backfill`，默认
`phase=handoff_recovery` 继续执行 0.5.6 首次引入的受控恢复。

该能力只修复缺失的路由证据。它不会修改创作内容、历史记录或审计结论，
也不会把当前机器复验冒充为原始初稿质量或独立审计。

## 权限与前置条件

- 只允许服务端绑定的 `production` authority/window/session。
- 必须已登记能力缺口，并在当前 production 窗口建立两张不同的
  `rule_improvement + execute + explicit_execute` 工单：一张只授权补签，
  一张只授权恢复；不得用 `version_management` 或同一工单跨阶段授权。
- 补签工单 targets 必须精确等于该 gap、对应 handoff IDs 和机器可读 token
  `controlled_phase:preflight_evidence_backfill`；恢复工单 targets 必须精确等于
  该 gap、对应 handoff IDs 和 `controlled_phase:handoff_recovery`。
- 缺失或额外 target、token 与调用 phase 不一致、工单未绑定当前 production
  window/session，或两阶段复用同一工单时，`action=plan` 必须以
  `scope_mismatch` 在任何计划返回或持久化写入前失败关闭。
- 运行时、规则、Schema、Skill、脚本、依赖或服务代码变化后，先调用
  `get_agent_status(full_self_test=true)`，只接受与线上 0.5.7 指纹一致的
  当前 PASS 全量回归证书。
- 旧交接必须仍为未领取、已发布的
  `prepared production_to_audit`，并能精确解析原生产工单、工件、
  canonical、源文、边界和生产 trace。已消费、已 superseded、路由错误、
  显式记录过生产预检失败或创作内容漂移的交接失败关闭。

## 第一阶段：补签生产预检证据

只使用 targets 含 `controlled_phase:preflight_evidence_backfill` 的补签工单，
对其精确 handoff 列表调用：

1. `recover_storyboard_handoff(phase="preflight_evidence_backfill", action="plan")`；
2. 核对逐条旧记录 SHA、工件/canonical 身份、当前完整指纹、拟追加的
   `preflight_evidence_id`、生产证书 ID、`plan_sha256` 和确认字面量；
3. 使用完全相同的 handoff 列表与 `plan_sha256` 调用 `action="execute"`，
   确认字面量必须精确为
   `REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE`；
4. 以同样参数再次执行时，必须返回同一证据/证书身份并标记幂等重放，
   不得追加第二套证据。

整批 handoff 的有序授权列表会同时绑定进计划哈希、逐条证据、生产证书、
逐条生产复验 trace 和批次 trace；消费端还会反查原补签工单的完整 targets
与批次映射。不能把一张整批工单降格成若干未获授权的单条工单，也不能用
重签但语义错绑的记录替换 artifact、canonical、原证书、镜头数或安全声明。

`plan` 必须零写入。`execute` 会以当前解析器、规则、Schema 和验证器重新
验证原工件；只有零问题结果才能按确定性身份追加一条签名
`production_preflight` 证据和生产证书。新证书固定
`PENDING_AUDIT`，`first_draft_grade_reconstructed=false`；当前复验不追溯
伪造原始初稿等级。逐条 trace 使用服务端真实测得的当前复验起止时间与耗时，
不得以零毫秒占位冒充已测验证。

补签事务采用签名、可重放的逐条原子日志。进程在证书或证据写入之间中断时，
相同请求只补全同一确定性记录；不同内容、指纹、计划或确认一律拒绝。

## 第二阶段：统一恢复交接

所有必要生产预检证据齐备后，只使用另一张 targets 含
`controlled_phase:handoff_recovery` 的恢复工单，对完整目标列表调用：

1. `recover_storyboard_handoff(phase="handoff_recovery", action="plan")`；
2. 核对每条资格、旧记录、当前完整指纹、新 handoff/证书 ID、
   `plan_sha256` 和确认字面量；
3. 使用完全相同的列表和 `plan_sha256` 调用 `action="execute"`，确认字面量
   必须精确为 `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`；
4. 以同样参数重放，必须返回同一旧/新映射、恢复记录 ID 和幂等结果。

每条 handoff 独立提交签名 CAS 恢复日志。旧 handoff 只标记
`superseded`，不删除；新 handoff 固定为 `prepared + PENDING_AUDIT`。
新生产证书和 successor handoff 受同一恢复日志的发布围栏保护：恢复日志
提交前，successor 不可被独立审计领取；崩溃重试只完成同一个后继。
提交后围栏持续精确校验生产/恢复不可变投影，同时只允许签名的审计领取、
释放、租约重领和 consumed 完成字段按生命周期变化；同一审计工单重放不会
因合法 claim 或 Hub 完成态被误判为未发布。

## 永久不可变边界

两个阶段都不得：

- 除恢复阶段按签名 CAS 将旧 handoff 状态标记为 `superseded` 外，改写或
  删除旧 handoff、旧生产证书、历史生产/审计/修复记录；
- 修改分镜、对白、canonical、XLSX、源文、边界或任何创作工件字节；
- 在 production 窗口签发、复制、复用或推断独立审计 PASS/FAIL；
- 把补签证据当作首次独立审计缓存或最终发布凭据；
- 在输入、完整指纹、运行时证书、工单权限、计划哈希或确认字面量漂移时
  继续执行。

恢复后的新 handoff 必须由不同的 `independent_audit` authority/window/session
只读领取，并对该精确 canonical 和完整指纹执行正常的八层独立审计。

## 发布与验收门禁

部署前在同一冻结 Git 提交和文件哈希上连续通过至少三轮完整
`scripts/run_tests.sh`。正式回归必须覆盖：精确 19 工具及 phase Schema、
plan 零写入、确认/计划漂移零写入、缺失字段补签、显式失败与内容漂移阻断、
证书半写崩溃恢复、补签幂等、原始/补签证据混合恢复、恢复批量重放，以及
整批授权消费、证据语义错绑阻断、真实复验计时、successor 在恢复日志提交前
不可领取、领取/释放/完成后的发布围栏与同审计工单幂等。

部署后重新执行 `get_agent_status(full_self_test=true)`。只有线上状态广告
0.5.7、精确 19 工具、当前指纹 PASS 证书且包含
`recover_storyboard_handoff` 时，才可对真实项目先补签、后统一恢复。
