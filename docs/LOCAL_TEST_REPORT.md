# 本地测试报告（0.5.9 待验证候选）

- 目标插件版本：`0.5.9`
- 目标分镜 Schema：`R10.1`
- 工作流：`R10-PG1`
- 输出契约：`R10-SPC-1`
- 规则运行时：`V16.3-148-R10-StableRuntime`
- 源文对白语法：`source-dialogue-closed-grammar-v6.3`
- 验证日期：2026-09-01 UTC
- 当前状态：省略phase专项23/23、完整回归449/449、stdio/HTTP冒烟、技能与静态检查全部通过；冻结、部署及线上补签/恢复仍待执行

本报告只记录当前 0.5.9 工作树的实际结果。0.5.8 的 444/444 三轮冻结回归与已部署线上行为是历史基线，不计作 0.5.9 结果；未在新候选上实际执行的项目仍标为待执行。

## 0.5.9 候选契约

- 模型只提交一份 27 字段 compact canonical；`正式分镜母版`和`GPT关键帧生成指令`只在导出时从同一 canonical 确定性投影。
- 源文对白由服务端闭集语法逐 occurrence 建立权威清单；说话人、正文和字符偏移必须精确匹配。候选 inventory、台账、认知矩阵和人物锚点只能是待审声明，不能反向授权源文解析。
- 无法唯一解析的 cue/role、视觉标签粘连、未知动作残留或派生 stage/visual 姓名一律失败关闭；预检失败不产生正式 artifact、版本、导出或 handoff。
- 独立审计 PASS 必须绑定实际检查镜头、零 issue、零 blocker、独立 handoff/trace 和完整工件指纹。复审使用服务器签发的一次性 successor handoff，不复用上一轮证据。
- 失败后的合法复审必须经过不可变修复包、新修复产物、新 handoff、新 trace 和新指纹；跨周期、改文件名、大小写或首尾空白不能把旧失败证据伪装成新证据。
- 迁移使用签名 WAL、项目级非终态 fence、调用方版本 CAS 和锁内运行时证书复核；`prepared`、`replaying`、`rolling_back` 均阻断普通写入，精确迁移 ID 才能恢复或回滚。
- 运行时完整回归证书使用项目锁内单调 `certificate_sequence` 排序；纯 legacy 同秒并列失败关闭，迁移只能接受锁内复核到的最新签名 PASS。
- 正式 XLSX 固定十列并保持 canonical/侧车反向一致；生产、审计、修复和签核均通过不可变事件、签名工件及 CAS 约束串联。
- `preflight_evidence_backfill` 只允许当前 production 窗口凭独立 `rule_improvement + execute + explicit_execute` 工单，对授权的 legacy `prepared production_to_audit` 交接重新验证并追加签名生产预检证据；不覆盖旧记录，不改分镜、对白、canonical、XLSX、生产证书或历史审计记录。
- 补签计划绑定有序 handoff 清单、完整当前指纹、预计证据 ID、计划 SHA 与确认字面量；执行使用项目锁、签名 WAL、CAS 和逐项原子提交，重放返回同一证据而不重复签发。
- `handoff_recovery` 使用另一个独立工单和计划；恢复只把旧 handoff 标记为 `superseded` 并签发新的 `prepared + PENDING_AUDIT` handoff，保留原生产血缘、补签来源、恢复记录和逐项/批次 trace，且 production 窗口不得代行独立审计。
- 历史 Hub 交接投影只读兼容两种精确形态：去掉 `record_id`、`project_id`、`integrity_hmac` 的签名记录投影，或包含这三个信封字段的完整签名记录。任何混合、额外、缺失或变更字段都失败关闭。
- 兼容读取不迁移、不规范化、不改写历史记录；未来生产 Hub 写入统一使用去信封投影。
- 宿主缓存旧工具参数表而省略或提交null `phase`时，服务端先校验transport session与当前production工单权限，再仅从唯一精确recognized `controlled_phase` token推断；不再默认handoff_recovery。
- 无token、重复target、双阶段token、未知/混合token、大小写或空白漂移、缺失/额外scope均在plan或execute前零写入失败关闭；显式phase仍须与精确targets一致。

## 0.5.9 实测记录

| 验证项 | 实际结果 |
| --- | ---: |
| R10 黄金测试 | 73/73 通过 |
| R10-SPC-1 严格提示词契约 | 25/25 通过 |
| 公共场景人物与环境音专项 | 11/11 通过 |
| Agent 生命周期 | 38/38 通过 |
| 全项目 Agent | 22/22 通过 |
| V040/V051/V055/V056/V057/V058 + 0.5.9兼容专项当前树组合回归 | 通过 |
| 插件单元、集成、安全、事务、迁移、解析与 XLSX 完整回归 | 449/449 通过；416.438秒 |
| 旧交接补签/恢复专项 | 23/23 通过；含 exact stripped、exact full signed、执行与幂等重放 |
| 混合、额外、缺失、业务及三个信封字段漂移失败关闭矩阵 | 通过；计划失败且权威存储零写入 |
| 真实 production commit Hub stripped writer | 1/1 通过 |
| 用户形状补签→恢复及幂等专项 | 通过 |
| 省略phase补签 plan→execute→replay及显式计划等价 | 通过 |
| 省略phase恢复 plan→execute→replay | 通过；原0.5.6恢复链继续有效 |
| 5类歧义token、3类显式重复target、跨session错误次序 | 通过；全部完整权威快照零写入 |
| 公开MCP工具完全省略phase预演 | 通过 |
| stdio MCP 冒烟 | 通过；版本0.5.9、精确19工具 |
| HTTP MCP 冒烟 | 通过；版本0.5.9、精确19工具 |
| 插件与技能清单验证 | 通过；Skill is valid |
| JSON、Python 编译与差异检查 | 通过；18个JSON、compileall、diff check |

完整测试入口：

```bash
./scripts/run_tests.sh
```

该入口应先执行技能运行时自检和技能专项，再执行服务自检、插件测试以及 stdio/HTTP 两路冒烟。0.5.9 的测试总数与墙钟必须以实际运行结果填写，不能复制 0.5.8 的数字。

## 历史失败样本回归

0.5.9 待执行负向矩阵除原0.5.8全部样本外，新增省略/null phase时的无token、重复、双阶段、未知、混合、大小写/空白漂移、跨session、错误authority、缺失/额外scope，以及显式phase重复target与token不匹配；全部必须在计划或执行写入前失败关闭。

旧错误样本必须 FAIL，对应修正样本必须 PASS；任一回归失败都阻断冻结、打包、部署和迁移。

## 发布边界

未来测试通过也不等于发布完成。本报告写入后必须冻结提交哈希，并在该哈希上运行完整入口；任一失败或受测文件变化都会使已有轮次作废。

只有冻结提交完整回归通过，且授权部署环境更新服务、刷新 ChatGPT 插件/技能连接，并调用 `get_agent_status(full_self_test=true)` 取得绑定当前规则、验证器、脚本和服务指纹的新证书后，才能声明 0.5.9 云端同步完成。线上受控补签和恢复必须在新证书有效且精确 19 工具集保持不变后另行执行；兼容推断本身不写权威数据，只有用户授权且哈希/确认闭合的事务可追加证据或恢复交接，历史 0.5.x artifact 仍保持只读。
