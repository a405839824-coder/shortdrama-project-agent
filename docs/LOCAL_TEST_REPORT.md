# 本地测试报告（0.5.11 冻结候选）

- 目标插件版本：`0.5.11`
- 目标分镜 Schema：`R10.1`
- 工作流：`R10-PG1`
- 输出契约：`R10-SPC-1`
- 规则运行时：`V16.3-148-R10-StableRuntime`
- 源文对白语法：`source-dialogue-closed-grammar-v6.3`
- 验证日期：2026-09-01
- 当前状态：冻结候选连续两轮完整回归通过；等待部署后运行时证书与受控线上执行

本报告只记录 0.5.11 冻结候选的实际结果。0.5.10 的完整回归、受控投影和补签/恢复结果只作为历史基线，不得计作 0.5.11 通过；任何未在最终冻结提交上实际执行的项目都保持“待执行”。

## 0.5.11 候选契约

- 公共工具必须仍精确为 19 个并包含 `recover_storyboard_handoff`；manifest、服务 `__version__`、runtime rules、capabilities、stdio/HTTP 冒烟与 packaging 期望统一为 0.5.11。
- 0.5.10 的历史工作簿受控投影复验保持原义；一般 `extract_xlsx`、普通导入、生产提交和独立审计路径均不放宽。
- `GAP-1bb91d83751b422f` 只接受 `UPGRADE_0.5.11.md` 中精确有序的后六条 handoff；补签工单必须绑定当前 production 窗口，且为 `rule_improvement + execute + explicit_execute`。
- 冻结历史报告必须 PASS、问题数为 0，并与原签名生产证书中的报告逐字段相等；冻结 validator、规则、error catalog、manifest、提交/树及逐 handoff rules profile 的 provenance 必须精确匹配。
- 当前 validator 必须完整运行并保留 FAIL 报告。唯一允许的 current FAIL 是全部问题均为 `E-I2V-INDEP-002` / `THG-01`、blocking fail、位置匹配 `shots[n].生成限制`、evidence 精确为 `逐字短语'9:16竖屏'应恰好出现1次，当前=0`，且 warning 为 0、issue/error/shot 数闭合。任何其他当前问题必须零写入失败关闭。
- 后六条只能追加 `controlled_historical_production_preflight_attestation` / `historical_production_preflight_attestation`，状态为 `historical_contract_pass`；不得签成普通当前 `production_preflight_pass`。
- 证明必须同时保存冻结历史报告和当前失败报告，并设置 `current_production_pass_claimed=false`、`current_preflight_cache_reusable=false`、`conclusion_reusable_for_independent_audit=false`、`requires_full_current_independent_audit=true`。
- 历史证明使用独立 `HISTCACHE-` 域；不得作为当前生产预检或独立审计缓存命中，也不得替代当前 validator 或八层语义审计。
- 补签全部成功后，`GAP-0e5d48969c4c494e` 只接受 `UPGRADE_0.5.11.md` 中精确有序的原 11 条。恢复只把旧 handoff 标记为 `superseded`，不删除历史；每条只签发一个 `prepared + PENDING_AUDIT` 后继。
- 不修改分镜、对白、canonical、XLSX、旧生产证书、原 production trace 或历史审计记录；production 窗口不执行独立审计。
- plan 零写入；execute 使用完全相同的有序列表、`plan_sha256` 和确认字面量逐条原子提交；同参 replay 返回原记录，不重复签发。

## 0.5.11 实测记录

| 验证项 | 实际结果 |
| --- | --- |
| R10 黄金测试 | PASS，73/73 |
| R10-SPC-1 严格提示词契约 | PASS，25/25 |
| 公共场景人物与环境音专项 | PASS，11/11 |
| Agent 生命周期 | PASS，38/38 |
| 全项目 Agent | PASS，22/22 |
| 0.5.10 受控投影历史回归 | PASS，v0.5.7/v0.5.10 链路回归 24/24 |
| 0.5.11 历史生产预检证明专项 | PASS，专项 9/9；连同导入回归共 33/33 |
| 六条补签 plan→execute→replay | PASS，精确顺序、双报告、append-only 与幂等重放均验证 |
| 十一条恢复 plan→execute→replay | PASS，旧 superseded、新 prepared + PENDING_AUDIT 与幂等重放均验证 |
| 当前失败范围、rules profile、provenance、报告与 PFE 投毒负向矩阵 | PASS，含 19 个逐字段重签投毒、冻结入口、布尔计数及运行时合同漂移 |
| HISTCACHE 与独立审计隔离 | PASS，当前 validator、首次独审与 force re-audit lineage 均验证 |
| 插件完整回归总数与墙钟 | 第一轮 PASS 488/488，单元测试 441.132 秒，完整入口 7分29.768秒；第二轮 PASS 488/488，单元测试 443.452 秒，完整入口 7分32.465秒 |
| stdio MCP 冒烟：版本 0.5.11、精确 19 工具 | PASS，含 `recover_storyboard_handoff` |
| HTTP MCP 冒烟：版本 0.5.11、精确 19 工具 | PASS，健康检查与 MCP inspection 均通过 |
| 插件与技能清单验证 | PASS，manifest、服务、runtime rules、capabilities 版本一致，skill quick validation 通过 |
| JSON、Python 编译与差异检查 | PASS |
| 冻结提交第二轮完整回归 | PASS，488/488；两路冒烟再次通过 |

完整测试入口：

```bash
./scripts/run_tests.sh
```

该入口应先执行技能运行时自检和技能专项，再执行服务自检、插件测试以及 stdio/HTTP 两路冒烟。0.5.11 的测试总数、通过数和墙钟只能填写冻结候选的实测值。

## 负向矩阵

除全部历史回归外，0.5.11 至少覆盖：错误 gap、handoff 缺项/多项/乱序/重复；逐 handoff rules profile 错配；冻结依赖、原报告、当前报告或任一 lineage 指纹篡改；current FAIL 混入其他 error code、gate、severity、blocking、location、evidence、warning 或计数漂移；把历史证明伪装为当前 PASS；`HISTCACHE` 被当前预检或独立审计复用；PFE/certificate/trace/handoff 任一双报告或不可复用标志缺失/漂移；恢复前后清单不一致；旧记录删除或新记录非 `prepared + PENDING_AUDIT`。所有负例必须在对应 plan/execute 权威写入前失败关闭。

## 发布边界

测试通过不等于发布完成。本报告必须在冻结提交上实跑并填写；任一受测文件变化都会使已有轮次作废。只有完整回归、第二轮冻结复验、stdio/HTTP 精确 19 工具冒烟与部署检查都通过，且部署后 `get_agent_status(full_self_test=true)` 签发绑定 0.5.11 当前规则、validator、脚本、依赖和服务指纹的新 PASS 证书，才可声明云端同步完成并新建补签/恢复工单。
