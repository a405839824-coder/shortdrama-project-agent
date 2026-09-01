# 0.5.9：旧工具参数表的受控阶段推断

0.5.9 修复 ChatGPT 宿主仍缓存旧 `recover_storyboard_handoff` 参数表时，`phase` 无法送达服务端、导致受控生产预检证据补签无法启动的问题。公共工具仍精确为 19 个；没有新增旁路工具，也没有放宽补签、恢复、确认字面量、会话、工单或不可变历史边界。

## 失败关闭的兼容入口

当 `phase` 被省略或显式为 `null` 时，服务端不再默认 `handoff_recovery`，而是按以下次序处理：

1. 读取指定工单并先核验当前可信 transport session；
2. 核验工单恰为当前 production 角色的 `rule_improvement + execute + explicit_execute` 且仍可执行；
3. 在原始 targets 列表中要求恰好一个、无重复的受支持令牌；
4. 仅从 `controlled_phase:preflight_evidence_backfill` 或 `controlled_phase:handoff_recovery` 推断阶段；
5. 再以 gap、完整 handoff IDs 与阶段令牌执行原有精确 scope 校验。

无令牌、重复 target、同时出现两个阶段、未知阶段、合法阶段与未知阶段混合、大小写或空白漂移、缺少 gap/handoff、额外普通 target、额外未请求 handoff、跨会话或错误权限，均须在计划生成和权威写入前失败关闭。显式 `phase` 仍必须与精确 target 集合一致。

## 原有事务契约不变

- 补签与恢复继续使用两张不同且绑定当前 production 会话的工单。
- 补签仍须 plan 后以相同有序列表、`plan_sha256` 和 `REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE` 执行。
- 恢复仍须 plan 后以相同有序列表、`plan_sha256` 和 `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT` 执行。
- 补签只追加签名生产预检证据与新证书，不改旧 handoff、旧生产证书、分镜、对白、canonical、XLSX、生产历史或审计历史。
- 恢复只把旧 handoff 标记为 `superseded`，并签发一个 `prepared + PENDING_AUDIT` successor；旧记录不删除。
- production 窗口不能执行、重建、复用或宣称独立审计结论。
- 0.5.8 的 exact stripped / exact full signed 历史 Hub 投影兼容继续保持，其他形态仍失败关闭。

## 发布门禁

至少覆盖以下专项后方可部署：

- 公开 MCP 工具在完全不传 `phase` 时可按 backfill 工单正确预演；
- 两阶段省略与显式调用生成相同 phase 和 `plan_sha256`，且 plan/execute/replay 幂等；
- 省略、重复、双阶段、未知、混合、漂移及显式重复 target 全部零写入拒绝；
- session 校验先于 target 形状推断，跨窗口无法探测或借用工单权限；
- 工具数仍为 19，`phase` 非必填、只广告两个合法值且无旧默认恢复值；
- 原 0.5.8 历史 envelope 正负矩阵、补签→恢复全链、stdio/HTTP 冒烟和完整回归继续通过。

部署后必须重新调用 `get_agent_status(full_self_test=true)`，取得绑定 0.5.9 当前规则、脚本、依赖和服务指纹的新 PASS 证书，再创建新的当前窗口工单并处理真实项目。
