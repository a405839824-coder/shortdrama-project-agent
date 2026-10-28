# 0.5.12：精确逐 handoff 当前报告封存修正

0.5.12 修正 0.5.11 对六条真实旧交接当前验证结果的错误概括。线上只读预演证明，这六条的当前 FAIL 并非都只含缺少逐字 `9:16竖屏` 的单一问题，因此 0.5.12 删除“按问题类别宽泛放行”的资格判断，改为逐 handoff 的不可互换封存画像。公共工具仍精确为 19 个并包含 `recover_storyboard_handoff`；一般导入、`extract_xlsx`、当前生产提交和独立审计路线均不放宽。

## 逐条封存，不按错误类别放行

`GAP-EXAMPLE` 仍只接受以下精确有序清单：

1. `HND-f6fd29730a074d2c`
2. `HND-95e3b1c141a64783`
3. `HND-c88ee199d5fb4676`
4. `HND-e8ca409208134104`
5. `HND-0dd8fa51a9e24cd0`
6. `HND-9029110f35d74270`

真实当前报告的计数摘要如下；完整 canonical/report SHA 与 error histogram 由服务常量和 runtime rules 中的逐条画像共同锁定：

| handoff | shot | error | warning |
| --- | ---: | ---: | ---: |
| `HND-f6fd29730a074d2c` | 56 | 334 | 0 |
| `HND-95e3b1c141a64783` | 61 | 373 | 0 |
| `HND-c88ee199d5fb4676` | 55 | 380 | 0 |
| `HND-e8ca409208134104` | 60 | 401 | 0 |
| `HND-0dd8fa51a9e24cd0` | 57 | 375 | 0 |
| `HND-9029110f35d74270` | 59 | 378 | 0 |

每条资格画像分别锁定且必须同时精确相等：

- handoff ID、旧 handoff 规则画像及该 handoff 已签名绑定的 canonical SHA；
- 完整当前 validator 报告的规范化 SHA-256；
- `shot_count`、`error_count`、`warning_count` 和完整 error-code histogram；
- 当前 validator composite `V16.3-148-R10-StableRuntime:R10.1:R10-PG1:af1fccfe2ab8c3bc` 及完整 SHA-256 `af1fccfe2ab8c3bc946b0537cf9fe23241f51afc776a9d33275c133599570140`。

这些值由服务内逐 handoff 封存常量作为执行权威；它们不是可由调用方提交、删减、归一或推断的参数。计划和执行都重新运行当前 validator 并计算完整报告，任何 canonical、报告 SHA、计数、直方图、validator 指纹、清单或顺序漂移都必须在 PFE、证书、trace、handoff 或恢复记录写入前失败关闭。不得退化为“只检查一个 error code/gate/evidence”的集合判断，也不得把一条 handoff 的画像借给另一条。

## 历史 PASS 与当前 FAIL 严格分离

逐条当前画像匹配只证明“当前诊断与受控补签时封存的真实诊断完全一致”，不证明当前生产通过。每条仍须满足：

- 冻结 v0.5.3 重放报告逐字段等于原签名生产证书中的 PASS/0 报告；冻结 validator、rules、error catalog、manifest、提交/树和逐 handoff rules profile provenance 全部匹配；
- 只追加 `controlled_historical_production_preflight_attestation` / `historical_production_preflight_attestation`，状态为 `historical_contract_pass`；
- 完整保存冻结报告和当前 FAIL 报告，且 `current_production_pass_claimed=false`、`current_preflight_cache_reusable=false`、`conclusion_reusable_for_independent_audit=false`、`requires_full_current_independent_audit=true`；
- 缓存键只使用独立 `HISTCACHE-` 域，不能命中当前生产预检或独立审计，不能替代当前 validator 或八层语义独审。

## 补签与统一恢复

当前 production 窗口须使用两张不同的 `rule_improvement + execute + explicit_execute` 工单。补签工单 targets 精确等于补签 gap、上述六条有序 handoff 和 `controlled_phase:preflight_evidence_backfill`；恢复工单 targets 精确等于恢复 gap、原 11 条有序 handoff 和 `controlled_phase:handoff_recovery`。

补签先 `plan`，全部六条逐项画像通过后，再以完全相同的列表、`plan_sha256` 和 `REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE` 执行。补签全部存在后，`GAP-EXAMPLE` 的原 11 条仍按 [UPGRADE_0.5.11.md](UPGRADE_0.5.11.md) 所列精确顺序统一恢复：先 `plan`，再以同一列表、哈希和 `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT` 执行。

每条事务保持原子性和幂等性。旧 handoff 只标记 `superseded`，不删除；每条只签发一个 `prepared + PENDING_AUDIT` 后继。不得修改分镜、对白、canonical、XLSX、旧生产证书、原 production trace 或历史审计记录；production 窗口不得执行或签发独立审计，后继必须由独立审计窗口按当前 validator 完整重审。

## 发布门禁

- 正例必须覆盖六条真实封存画像的 plan→execute→replay、后续 11 条恢复及 force re-audit；
- 负例必须逐字段覆盖 handoff/canonical/report SHA/计数/histogram/validator 指纹、冻结 provenance、PFE/certificate/trace/handoff 双报告和不可复用标志投毒；
- 0.5.11 的 aspect-only 测试夹具只能作为历史回归样本，不能再代表六条真实线上资格；
- 完整回归、第二轮冻结复验、stdio/HTTP 精确 19 工具冒烟和 packaging 检查全部通过后才可部署；
- 部署后必须运行 `get_agent_status(full_self_test=true)`，取得绑定 0.5.12 当前代码、规则、validator、脚本、依赖和服务指纹的新 PASS 证书，才能新建当前窗口补签与恢复工单。

0.5.11 首次引入的 append-only 历史证明、冻结报告等于旧签名报告、`HISTCACHE` 隔离、不得声称当前 PASS、强制当前独审及不改写历史边界保持不变；本版只把不正确的宽泛问题范围资格改为六条真实报告的精确逐项封存。
