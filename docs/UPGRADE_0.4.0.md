# 0.4.0 架构升级与窗口运行协议

## 结论

正式流程不是每次都同时运行三个窗口。应长期保留两个互相独立的 ChatGPT/MCP 会话：

1. `production`：生成或修改，只签发 `production_preflight` 和生产交接包。
2. `independent_audit`：只读审计生产交接包，只由该会话签发最终审计结论。

第三个 `repair` 会话是条件分支：只有独立审计失败、已经生成签名修复包且用户明确授权后才创建。修复窗口不能签发最终通过；修复结果必须通过 `repair_to_audit` 交回原独立审计会话。

## 0.4.0 数据流

```text
模型只写唯一语义原子
  -> 服务端合并完整 canonical
  -> 确定性编译 3 个派生长字段和 10 列正式母版
  -> 完整 R10 机器生产预检
  -> production_to_audit handoff
  -> 独立审计会话提交八层语义证据并合并机器问题
  -> 审计证书
  -> [仅失败且获授权] repair -> 原审计会话终审
```

服务端确定性生成下列重复内容，调用方提供的旧值会被覆盖：

- `正式分镜母版·字段合并`
- `GPT关键帧生成指令`
- `Seedance 2.5独立正式执行分镜`

这三项从相同原子字段编译，不允许物化器补写剧情、对白、人物认知、动作、机位、声音或光影事实。原子事实缺失时直接阻断。

## 路由规则

| 变化类型 | 0.4.0 路由 | 说明 |
| --- | --- | --- |
| 首次正式分镜 | `commit_storyboard` | 生成完整 canonical、XLSX、生产预检和 handoff |
| 既有镜头同身份、同数量的受控变化 | `commit_storyboard_delta` | 只上传受影响连续镜头和闭合批次，服务端合并未变化内容并全表验证 |
| 同身份镜头的对白变化 | `commit_storyboard_delta` 或完整路线 | 仅当原文映射、人物认知、功能、可见主体和场景身份不变，并提交新鲜完整 `performance_chains` 证据时可增量 |
| 心理音轨或心理音轨属性变化 | 完整受影响场景/分集重建 | 0.4.0 没有逐镜认知证明，不能用表演证据推定认知未变化 |
| 审计证书确认的非结构字段 | `commit_storyboard_patch` | 必须绑定基础哈希、证书问题和影响范围 |
| 插入、删除、移动、拆分、合并、重编号 | 完整受影响场景/分集重建 | 0.4.0 不把结构变化伪装成安全 delta |
| 原文映射、人物认知、集边界、场景拓扑或核心镜头计划变化 | 完整受影响场景/分集重建 | 必须重算上游证据和完整门禁 |

Delta 只减少模型生成和传输的重复内容，不减少任何最终验证、导出或独立审计规则。

## 首次独立审计契约

首次审计或任一安全指纹变化后的审计，必须向 `audit_storyboard` 提交 `semantic_audit`：

- 精确 `canonical_sha256`；
- 已复核镜头数和批次数；
- 八个不重复审计层，每层有状态、摘要和具体证据 ID；
- 发现的问题、受影响镜头/批次、证据和修复动作；
- 与八层状态一致的最终结论。

服务端重新运行完整机器校验，并把机器问题与语义问题合并。只有安全缓存键完全命中时才允许省略 `semantic_audit`。生产预检、物化成功或机器零报错都不能自动生成独立语义通过结论。

## 快速路径硬证据

完成状态至少必须同时能解析到：

- 生产 `trace_id`；
- 独立审计 `trace_id`；
- `artifact_id` 与 `canonical_sha256`；
- 生产 `handoff_id`；
- 独立审计 `certificate_id`；
- `fast_path_evidence` 中两条追踪与同一版本的绑定；
- `cache_hit` 及未命中原因（如未命中）。

缺少生产或审计任一追踪，不得宣称走完 0.4.0 优化流程。插件追踪只测服务端时间；ChatGPT 阅读、推理和创作时间必须标为未测量。

## 项目 `SBP-EXAMPLE` 的迁移

现有 0.3.x canonical 可以作为只读基线迁移稳定 UID，但旧工单、生产预检、审计结论和修复包不能替代 0.4.0 新角色契约。升级连接后应在新聊天分别创建新的 production 与 independent-audit 工单；不要重复导入指纹未变化的原文、80 集方案或 canonical。

### 生产窗口模板

> 使用短剧分镜项目管理与审计工具，继续项目 `SBP-EXAMPLE`。本会话角色为 `production`，先调用 `get_agent_status`，再为第06–07集创建 `execute + explicit_execute` 的生产工单并保护第01–05集和第08–80集。复用指纹未变化的原文、80集方案和最新版canonical；禁止重复上传、重复解析。读取长内容使用自动 `bulk_ranges`，已有ETag时传 `if_none_match`。优先只生成受影响镜头的语义原子；符合安全边界时调用 `commit_storyboard_delta`，否则只重建完整受影响场景或分集并调用 `commit_storyboard`。提交后停在 `production_preflight`，禁止调用 `audit_storyboard`。最终返回 `project_id、work_order_id、artifact_id、canonical_sha256、handoff_artifact_id、handoff_id、trace_id、cache_hit及未命中原因`；缺少任一执行证据不得宣称完成。

### 独立审计窗口模板

> 使用短剧分镜项目管理与审计工具，继续项目 `SBP-EXAMPLE`。本会话角色为 `independent_audit`，先调用 `get_agent_status`，再创建 `storyboard_audit + audit_only + read_only` 工单。只审计 `artifact_id=___`、`handoff_artifact_id=___` 和生产返回的精确canonical；验证全部指纹后调用 `audit_storyboard(force_machine_recheck=false)`。安全缓存未命中时，必须完成八层语义复核并提交 `semantic_audit`；禁止重新上传、重新生成、优化剧情、修改Excel或重新导出。最终返回 `audit_certificate_id、production_trace_id、audit_trace_id、fast_path_evidence、cache_hit及未命中原因、问题数量、受影响镜头`。

### 条件修复窗口模板

> 仅当原独立审计失败并生成签名修复包、且我明确授权后执行。新建独立 `repair` 工单，严格消费 `repair_package_id=___` 和 `audit_to_repair` handoff。证书确认的非结构字段使用 `commit_storyboard_patch`；插删移、对白/剧情、人物认知、镜头数量、时长、场景拓扑、原文映射或核心计划变化，重建完整受影响场景或分集。禁止扩大问题范围，完成全表生产预检后生成 `repair_to_audit`，并交回原审计工单/会话；本窗口不得宣称最终审计通过。

## 性能验收

`12–25 分钟`、`15–25 分钟` 或 `50%–80%` 压缩目前都只能作为工程目标，不能写成已验证结论。发布验收必须用相同源文件、相同镜头范围、相同模型/服务档位和相同输出契约做 0.3.x/0.4.0 A/B 回放，并同时记录：

- 端到端墙钟；
- 模型实际生成字符数；
- delta 与完整 canonical 字节数；
- 生产和审计两条服务端追踪；
- 缓存命中/未命中原因；
- 最终问题数、残留数和新增问题数。

在真实 A/B 数据完成前，只能说明 0.4.0 已从架构上消除了三组重复长字段的模型生成，并为同身份局部变化提供增量传输与服务端合并。
