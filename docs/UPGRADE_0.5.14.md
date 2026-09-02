# 0.5.14 窗口无角色限制升级

## 用户结果

同一认证用户命名空间中的任意 ChatGPT 窗口都可以直接建立或继续生产、独立审计和修正工单。用户不再需要声明“当前窗口角色”、寻找原窗口、为新窗口恢复权限，或为了独立审计而另外打开聊天。

`get_agent_status` 明确返回：

- `window_neutral_orchestration=true`；
- `window_role_restrictions=false`；
- `window_access_scope=authenticated_namespace`；
- `window_access_capabilities=[production, independent_audit, repair]`；
- `role_continuation_required=false`。

待审计或待修正时，`execution_available` 表示当前窗口可以执行解除质量门所需的动作；`allowed_storyboard_operations` 给出当前工作流允许的动作。待独审仍暂缓下一批生产，这是版本质量顺序，不是窗口权限。

## 证据身份仍然隔离

聊天窗口只记录请求来源，不再充当角色权限主体。公开 `begin_project_task(action=create)` 由服务端路由为窗口无角色限制创建，并为每张工单分别签发不可伪造的：

- authority root；
- window binding；
- actor binding；
- operation/mode/authorization 契约。

因此，同一个聊天可以依次发起生产、审计和修正，但三张后台执行身份仍不同。独立审计仍须使用不同工单、authority root、window binding 和 actor binding，并重新计算完整八层证据；生产预检证书不能成为独立审计结论。

修正仍须满足：独审失败、用户明确授权、签名修复包、精确基线与范围、单次消费、完整回归，以及返回原独审 authority 复核。任意窗口可调用原独审工单，但不能把另一个审计 root 冒充原审计身份。

## 跨窗口续作

窗口无角色限制工单在 cloud 模式仍要求服务器观察到可信 MCP 会话。服务端以 OAuth 用户命名空间作为访问边界：同一命名空间的另一窗口可继续预检、提交、审计、修正和复审；缺少可信会话或使用另一用户命名空间仍失败关闭。

草稿场景预检、完整预检和提交不再绑定“调用它的那一个窗口”，而是绑定工单当前服务端 authority。性能诊断也使用同一 authority 证据绑定，所以跨窗口接力不会使合法场景预检或诊断记录失效。

## 历史兼容

0.5.13 及更早的历史窗口绑定工单、continuation 记录、authority head、claim、handoff、证书和 trace 都不改写。旧客户端或直接 service 调用显式使用 legacy `action=create` 时，原窗口绑定与 `plan_role_continuation/execute_role_continuation` 契约仍可复验。

公开 MCP 的 `action=create` 从 0.5.14 起默认路由到窗口无角色限制契约。公共工具总数仍精确为 19，不新增用户需要学习的权限工具。

## 不放宽的边界

- 不跨 OAuth 用户命名空间；
- 不跳过运行时证书、项目迁移、生产预检或独立审计；
- 不允许生产执行身份签发独审结论；
- 不允许审计执行身份修改分镜；
- 不允许无签名修复包或无明确授权的修正；
- 不允许待独审状态扩大下一批生产；
- 不泄露会话指纹、claim token、HMAC 或内部绑定材料。

## 发布门禁

发布前必须覆盖：公开 `create` 路由、同窗口三类工单、跨窗口场景预检/完整预检/提交、同窗口生产后真实独审、跨窗口修正与原独审复核、缺少可信 cloud session、跨用户/跨项目、待审计生产门、生产预检不可冒充独审、修复包单次消费、0.5.13 legacy continuation、并发 claim/lease fencing、状态投影无敏感字段、精确 19 工具、技能结构校验、完整服务回归和 stdio/HTTP 冒烟。

部署后必须重新运行 `get_agent_status(full_self_test=true)`，签发绑定 0.5.14 当前规则、验证器、脚本、依赖与服务指纹的新 PASS 证书。只有线上版本、工具数、状态字段和新证书全部一致，才可开始原 11 条交接的统一独立审计。

本地首轮完整验证已实际通过：五组技能专项分别为 `73/73`、`25/25`、`11/11`、`38/38`、`22/22`，服务单元/集成/安全事务回归为 `553/553 PASS`（`592.937s`），stdio/HTTP 冒烟均确认版本 `0.5.14` 与精确 19 个公共工具。`quick_validate`、`validate_plugin`、JSON、Python 编译与差异检查均为 PASS。
