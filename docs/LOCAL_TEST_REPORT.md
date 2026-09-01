# 本地测试报告（0.5.12 冻结候选）

- 目标插件版本：`0.5.12`
- 目标分镜 Schema：`R10.1`
- 工作流：`R10-PG1`
- 输出契约：`R10-SPC-1`
- 规则运行时：`V16.3-148-R10-StableRuntime`
- 源文对白语法：`source-dialogue-closed-grammar-v6.3`
- 验证日期：`2026-09-01`（UTC）
- 当前状态：`PASS`

本报告只记录 0.5.12 冻结候选的实际结果。两轮完整回归均在实现提交 `2133d8b25bb1f8a11ea09c7c1d044a244b0ac4b6`、树 `76d011e06dac0c6e587013d958c583d1814a0cab` 上执行；其后的唯一修改是把这些实测结果写入本报告。0.5.11 及更早版本的回归、部署、自检证书和线上预演只作为历史基线，不计作 0.5.12 通过。

## 0.5.12 候选契约

- 公共工具必须仍精确为 19 个并包含 `recover_storyboard_handoff`；manifest、服务 `__version__`、runtime rules、capabilities、stdio/HTTP 冒烟与 packaging 期望统一为 0.5.12。
- `GAP-1bb91d83751b422f` 只接受 [UPGRADE_0.5.12.md](UPGRADE_0.5.12.md) 中精确有序的后六条 handoff；补签工单须绑定当前 production 窗口，且为 `rule_improvement + execute + explicit_execute`。
- 冻结历史报告必须 PASS、问题数为 0，并与原签名生产证书中的报告逐字段相等；冻结 validator、规则、error catalog、manifest、提交/树及逐 handoff rules profile provenance 必须精确匹配。
- 每条真实当前 FAIL 必须精确匹配其不可互换的封存画像：handoff ID、canonical SHA、完整规范化 current-report SHA-256、shot/error/warning 计数、完整 error-code histogram，以及当前 validator composite/完整 SHA-256。不得使用 0.5.11 已勘误的 aspect-only 集合判断。
- 后六条只能追加 `controlled_historical_production_preflight_attestation` / `historical_production_preflight_attestation`，状态为 `historical_contract_pass`；不得签成普通当前 `production_preflight_pass`。
- 证明必须完整保存冻结历史报告和当前失败报告，并设置 `current_production_pass_claimed=false`、`current_preflight_cache_reusable=false`、`conclusion_reusable_for_independent_audit=false`、`requires_full_current_independent_audit=true`。
- 历史证明使用独立 `HISTCACHE-` 域；不得作为当前生产预检或独立审计缓存命中，也不得替代当前 validator 或八层语义审计。
- 补签全部成功后，`GAP-0e5d48969c4c494e` 只接受文档中的精确有序原 11 条。恢复只把旧 handoff 标记为 `superseded`，不删除历史；每条只签发一个 `prepared + PENDING_AUDIT` 后继。
- 不修改分镜、对白、canonical、XLSX、旧生产证书、原 production trace 或历史审计记录；production 窗口不执行独立审计。
- plan 零写入；execute 使用完全相同的有序列表、`plan_sha256` 和确认字面量逐条原子提交；同参 replay 返回原记录，不重复签发。

## 0.5.12 实测记录

| 验证项 | 实际结果 |
| --- | --- |
| R10 黄金测试 | 两轮均 `73/73 PASS` |
| R10-SPC-1 严格提示词契约 | 两轮均 `25/25 PASS` |
| 公共场景人物与环境音专项 | 两轮均 `11/11 PASS` |
| Agent 生命周期 | 两轮均 `38/38 PASS` |
| 全项目 Agent | 两轮均 `22/22 PASS` |
| 0.5.10/0.5.11 历史回归 | v0.5.11 + v0.5.7 聚焦复验 `61/61 PASS`；完整套件两轮通过 |
| 0.5.12 六条真实封存画像专项 | 只读下载的六条真实 canonical 在最终 validator/rules 上 `6/6 PASS`；问题数精确为 `334/373/380/401/375/378` |
| 六条补签 plan→execute→replay | 本地签名存储全链 PASS；append-only、双报告和幂等重放均闭合 |
| 十一条恢复 plan→execute→replay | 本地签名存储全链 PASS；旧记录 `superseded`、新记录 `prepared + PENDING_AUDIT` |
| 当前报告画像逐字段投毒负向矩阵 | 完整报告 evidence 同直方图投毒、profile 对调、bool 计数、validator composite/完整指纹漂移均零写失败关闭 |
| 冻结 provenance、双报告和 `HISTCACHE` 隔离 | PASS；原报告完全相等、19 个 PFE 权威字段投毒及发布围栏回归通过 |
| 当前独审与 force re-audit lineage | PASS；历史证明不可复用，当前 validator 仍执行，lineage 不可变 |
| 插件完整回归第一轮 | `492/492 PASS`；单元/集成 `452.524s`；全入口墙钟 `7m41.557s` |
| 插件完整回归第二轮 | `492/492 PASS`；单元/集成 `455.315s`；全入口墙钟 `7m44.782s` |
| stdio MCP：版本 0.5.12、精确 19 工具 | 两轮均 PASS，包含 `recover_storyboard_handoff` |
| HTTP MCP：版本 0.5.12、精确 19 工具 | 两轮均 PASS，health 与 MCP inspection 一致 |
| manifest/服务/runtime rules/capabilities 版本一致 | PASS；packaging `6/6`，服务画像与 runtime-rules 镜像 `6/6` |
| 插件/技能结构 | `validate_plugin` PASS；`quick_validate` PASS |
| JSON、Python 编译与差异检查 | PASS |

完整测试入口：

```bash
./scripts/run_tests.sh
```

部署后仍须按本报告“发布边界”取得新的线上完整自检证书；本地 PASS 不替代线上证书，也不替代随后两次生产 plan 与显式 execute。

## 负向矩阵

除全部历史回归外，0.5.12 至少覆盖：错误 gap、handoff 缺项/多项/乱序/重复；六条画像交叉复用；canonical SHA、current-report SHA、shot/error/warning 计数、histogram 任一键值及当前 validator composite/完整 SHA 漂移；冻结依赖或原签名报告篡改；把历史证明伪装为当前 PASS；`HISTCACHE` 被当前预检或独立审计复用；PFE/certificate/trace/handoff 任一双报告或不可复用标志缺失/漂移；恢复前后清单不一致；旧记录删除或新记录非 `prepared + PENDING_AUDIT`。所有负例必须在对应 plan/execute 权威写入前失败关闭。

## 发布边界

测试通过不等于发布完成。只有完整回归、第二轮冻结复验、stdio/HTTP 精确 19 工具冒烟与部署检查都通过，且部署后 `get_agent_status(full_self_test=true)` 签发绑定 0.5.12 当前规则、validator、脚本、依赖和服务指纹的新 PASS 证书，才可声明云端同步完成并新建补签/恢复工单。
