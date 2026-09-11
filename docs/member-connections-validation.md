# 成员连接候选实现验证记录

日期：2026-09-10。基线：`394775c5c34ed30062d43cd2fb2381e017732a05`；开发分支：`codex/member-connection-access`。

这是基于 0.5.29 的开发候选，尚未分配发布版本、合并或部署。成员访问开关默认关闭。

## 已验证

- 47 项相关 unittest 全部通过：成员连接、既有账号权限、剧情工具权限、Library 存储、会话绑定、打包与 OAuth 配置。
- 11 项成员安全测试在最后的输入大小限制和配置读取加固后重新运行通过。
- RSA 签名 OAuth token 经真实 HTTP `/mcp` 路由验证；无效签名、错误 issuer/audience、过期、缺少 scope、未知客户端、Google 未绑定、已停用成员均拒绝。
- 1 个管理员与 9 个员工共用 OAuth subject 时，各自项目空间及审计身份分开，管理员沿用原项目空间。
- 并行异步调用不会混用成员身份；一次已授权操作内的角色和项目空间保持一致。
- 新 Google 经过明确绑定后继续使用原成员空间；旧的按 OAuth subject 授予 Admin 的规则在成员模式中不生效。
- HTTP smoke、stdio smoke 和 rule registry 一致性检查均通过。
- 新成员配置脚本校验要求 confidential web client、仅 code/refresh grants、精确 ChatGPT callback，拒绝公开客户端及不安全授权类型。

## 尚未验证或执行

- 没有创建/修改真实 Auth0 application，没有生成或分发员工 Client Secret。
- 没有使用真实 Management API token 执行客户端读取校验及安装。
- 没有更改 Railway 环境变量、线上成员文件或部署。
- 没有用员工自己的 ChatGPT 完成真实 OAuth 登录、实际 refresh token 交换或重新连接。
- 没有执行 10 人重任务负载测试；空间隔离测试不代表并发吞吐与容量保证。
- 没有执行整个仓库完整回归或 GitHub CI。相关测试与 transport 检查通过不等于完整发布验收。

达到本地运行及 HTTP 集成检查层级；真实 ChatGPT / Auth0 端到端验收待按 [成员连接方案](member-connections.md) 完成。发布时应更新统一版本注册及生成视图、运行完整发布检查后再部署。

## 2026-09-11 员工内容与流程权限复核

补充服务器调用说明，明确 Producer 可改分镜内容，不能改分镜流程规则、审核标准或程序配置。此说明不替代既有服务端权限门禁。

新增 `tests/test_employee_workflow_boundary.py`，通过本地 MCP 验证：

- 正常内容变更可以建立工单；稿件中出现“规则”不被误禁。把“跳过审核、移除门禁”写入工单参数，不能改变服务器分配的检查项与项目策略。
- 分镜方案可保存新版本并保留旧版；规则文件名及伪造的 `policy` / `role` 字段只作为项目内容保存，不能覆盖流程设置或提升角色。
- 员工修改后的分镜进入实际验证器；未通过的候选不能提交为通过，用 `complete_task_validation` 自报全部通过不能替代独立审计。

本轮账号权限、成员隔离、剧情权限、Library 存储及新增流程边界共 38 项测试通过；规则注册表一致性检查 PASS。未执行整个仓库完整回归或真实员工登录验收。

本轮未配置真实员工连接、未推送或部署。共用管理员 OAuth 身份的线上旧连接仍不能据此认定已限制为员工权限。
