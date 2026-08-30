# 0.5.3 无API性能诊断升级说明

0.5.3是在0.5.2会话绑定、R10.1紧凑母版与R10-SPC-1输出合同上的可观测性补丁。它不改变canonical字段、Excel列、审计门禁或增量权限，也不删除任何逐镜内容。

## 新增能力

新增第15个工具 `mark_performance_stage`。仅当用户明确要求排查原生ChatGPT窗口耗时时，围绕一个真实、已授权、角色绑定的分镜工单记录五个服务器收件检查点：

1. `task_started`
2. `inputs_ready`
3. `plan_ready`
4. `canonical_ready`
5. `delivery_ready`

工具返回签名的 `diagnostic_run_id`，完成后给出四段工具夹持区间、最大观察区间、已解析插件trace的真实服务端毫秒数/占比、未解析trace和无API不可测项。

## 测量边界

- 生产、导入、delta、审计和修复的原有trace继续标记 `measurement_scope=server_only`。
- 五阶段诊断单独标记 `measurement_scope=tool_bracketed_chat`。
- 诊断时间来自服务端收到标记的时刻；不接受调用端自报时间。
- 它不能精确拆分模型排队、隐藏思考、令牌生成速率、客户端传输和最终UI渲染。
- 单次运行只能定位最大观察区间，不能证明思考模式因果。思考模式比较必须保持源文件、范围、route、集数、镜头数、窗口类型、缓存和R10-SPC-1交付完全一致。

## 零质量回退

0.5.3继续强制：

- 每镜完整27字段；
- `空间站位描述`、`起始状态`、`音色`、环境声、环境动态、生成限制逐镜存在；
- `光影`、`光源来源`、`光源方向`、`色温倾向`四项逐镜独立；
- `画面内容`包含动作、表情、精确对白、六维发声、听者反应和恰好三段Seedance 2.5时间；
- 正式Excel严格十列，`正式分镜母版`为唯一视频执行真值；
- 49项validator、G0-G13、八层独立审计不减少；
- `legacy_generation_calls=0`、`seedance_batches_generated=0`、`model_authored_derived_chars=0`、`duplicate_generated_bytes=0`；
- 生产、审计、修复、诊断、缓存预热和后台任务都不生成独立Seedance批次。

## 普通运行与诊断运行

普通生产不调用性能标记，仍走最短安全路线，因此0.5.3不会给每批任务固定增加五次工具调用。

只有显式诊断时才加载 `no_api_performance_diagnostics.md` 并执行五阶段标记。生产和独立审计仍使用两个独立窗口；repair只在审计失败且授权后开启。性能诊断不需要额外窗口。

## 验收

本地发布门禁：

- R10黄金测试73/73；
- R10-SPC-1严格单镜合同10/10；
- 公共场景11/11；
- Agent 16/16；
- 全项目Agent 21/21；
- 插件单元/集成/安全/事务/增量/XLSX/诊断162/162；
- stdio与HTTP均报告版本0.5.3、15个工具；
- 插件与技能清单验证通过。

云端只有在部署后 `/health` 返回0.5.3、ChatGPT刷新连接发现15个工具、`get_agent_status`返回诊断能力可用时，才算升级完成。

