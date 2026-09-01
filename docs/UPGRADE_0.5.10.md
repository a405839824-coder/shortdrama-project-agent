# 0.5.10：旧交接工作簿的受控历史投影复验

0.5.10 为 `recover_storyboard_handoff` 的既有生产预检证据补签与交接恢复流程增加内部历史投影复验。公共工具仍精确为 19 个，没有新增旁路工具；一般导入、读取和独立审计仍只接受当前严格 XLSX 契约。

## 适用范围

该能力只能在已登记能力缺口、有效全量回归证书、当前 production 会话绑定的 `rule_improvement + execute + explicit_execute` 工单以及精确 handoff 列表下运行。补签和恢复继续使用两张不同工单、各自的 `controlled_phase` token、先 plan 后 execute、原样 `plan_sha256` 与既有确认字面量：

- 补签：`REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE`
- 恢复：`SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`

## 唯一允许的两类工作簿

受控内部读取器只接受：

1. 带完整 11 列 canonical sidecar、精确 `shortdrama-projector/0.5.4` 或当前投影 marker、有效可见投影 SHA 和审计页 SHA 的签名工作簿；
2. 合法 v0.5.3 基线组合：8 列 canonical sidecar 信封保持 `veryHidden`，可见母版使用精确短 10 列表头，所有可见行由冻结 v0.5.3 投影器逐格重放一致。

冻结实现来源固定为 v0.5.3 发布快照 `9deb528db3f9eb27e0f406634036e98b8a3d1f80`、原 materializer Git blob `d8d4cce11a50c40d5594867a9de8ae89d717fde1`。该快照是独立的合法 v0.5.3 基线，不能描述为 v0.5.1 同一提交谱系中的回退。

纯 v0.5.0 长表头、当前行伪装成 8 列 sidecar、未知 projector、非 `veryHidden` sidecar、任一表头/单元格/侧车块/canonical/投影 SHA/审计 SHA 漂移都失败关闭。新导出永远不调用冻结投影器。

## 证据绑定

每条受控复验证明封存并哈希绑定：

- 原始 XLSX SHA 与 linked canonical SHA；
- 旧 handoff 完整签名记录；
- 旧 production-preflight certificate；
- 原 production `commit_storyboard` trace；
- 选定 projector、模式、冻结来源、表头、可见投影、审计页与 sidecar 状态。

v0.5.3 的旧 8 列 sidecar 没有 `projection_sha256` 或 `audit_page_sha256`。因此本次计算的可见投影 SHA 只能作为新的受控重验证据，不能伪称历史 sidecar 签名；旧审计页完整性只由旧证书、旧 handoff、原生产 trace 与精确原始 XLSX SHA 共同绑定，也不构成独立审计结论。

证明 SHA 进入 PFE 确定性身份、补签证书、恢复计划/恢复键、新生产证书、新 handoff、批次映射、幂等重放与后续复审不可变 lineage。旧 0.5.6—0.5.9 记录可不含此新证明；新写入必须生成并逐对象精确一致。

## 不变边界

- `extract_xlsx` 和普通 `workbook_to_storyboard` 默认路径没有放宽；
- plan 零写入；execute 逐条原子提交并支持 WAL/CAS 幂等续提；
- 补签只追加 PFE 与生产证书，不改旧 handoff、旧证书或原生产 trace；
- 恢复只把旧 handoff 标记为 `superseded`，不删除历史，并签发 `prepared + PENDING_AUDIT` 后继；
- 不修改分镜、对白、canonical、XLSX、生产证书或历史审计记录；
- production 不得签发、复用或推断独立审计结论。

## 发布验收

部署前必须通过冻结 v0.5.3 差分/现场字节样本、0.5.4 签名契约、一般读取拒绝、表头/单元格/sidecar/签名/血缘负例、service 级 plan→execute→replay、WAL 中断后二次续提与第三次幂等重放、完整回归以及 stdio/HTTP 精确 19 工具冒烟。部署后重新调用 `get_agent_status(full_self_test=true)` 签发绑定 0.5.10 当前规则、脚本、依赖与服务指纹的新 PASS 证书，再创建新的当前窗口补签/恢复工单。
