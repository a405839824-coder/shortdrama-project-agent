# 0.5.15 恢复后继再次签发

## 目标

0.5.14取消用户窗口角色限制后，服务发布会改变完整运行时依赖指纹。若一条`prepared + PENDING_AUDIT`交接本身已经是受控恢复产生的签名后继，旧实现只认识最初的生产handoff，无法再次恢复该后继，独立审计会以`handoff_fingerprint_mismatch`失败关闭。

0.5.15允许这类后继在后续部署后再次进入`recover_storyboard_handoff`的`handoff_recovery`阶段。它不是窗口权限恢复，也不会放宽审计、修复或内容门禁。

## 前置条件

- 当前插件、运行时、验证器、脚本和依赖必须有有效的全量回归PASS证书；
- 用户可从同一认证命名空间的任意窗口发起请求；
- 服务端仍签发独立production执行身份；
- 必须先登记描述精确失效handoff集合的新能力缺口；
- 必须新建`rule_improvement + execute + explicit_execute`工单；
- targets必须精确等于gap、同序handoff IDs和`controlled_phase:handoff_recovery`；
- 仍须先plan，再以完全相同的列表、`plan_sha256`和`SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`执行。

## 恢复血缘验证

后继只有在以下证据全部一致时才可再次恢复：

1. 当前handoff是签名、已发布、未领取的`prepared production_to_audit`，且仍为`PENDING_AUDIT`；
2. `recovery_id`、直接前序handoff、恢复gap、恢复工单、计划哈希和逐条trace与签名恢复日志完全一致；
3. 恢复日志处于`committed`，其old/new/certificate/trace after-image及Hub投影仍通过签名和哈希验证；
4. 恢复工单确为服务端production身份的`rule_improvement + execute + explicit_execute`；
5. XLSX原始字节、canonical、源文指纹、分集边界、锚点、稳定身份和所有不可变内容指纹未变；
6. 普通后继的生产证书仍为零问题当前预检；
7. 历史证明后继的`HISTCACHE`、冻结PASS/0报告、完整当前FAIL报告哈希、错误计数、错误直方图和当前validator必须逐项匹配。

任一日志、证书、trace、内容或失败画像漂移，plan在写入前失败关闭。

## 写入边界

执行仍逐条使用签名日志与CAS：

- 直接旧后继只追加标记为`superseded`，不删除；
- 新后继固定为`prepared + PENDING_AUDIT`；
- 不修改分镜、对白、canonical、XLSX、原生产证书、历史审计记录或已存在的恢复日志；
- 不签发、复用或伪装独立审计结论；
- 历史证明后继继续明确`current FAIL`、`current_production_pass_claimed=false`和`requires_full_current_independent_audit=true`；
- 完全相同的execute重试只返回同一后继并标记幂等重放。

## 窗口语义

0.5.15继续沿用0.5.14的窗口无角色限制：用户不需要给聊天窗口分配production、audit或repair权限，也不需要找回旧窗口。后台执行身份分离只用于证据真实性；待审计、待修正等状态仍只约束工作流顺序。

公共工具数保持精确19个。
