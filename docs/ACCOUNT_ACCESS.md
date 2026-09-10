# Admin / Producer 账号权限（0.5.29，契约 admin-producer-v1）

账号权限于 0.5.28 基于 0.5.26 加入。0.5.29 将此前独立开发的 0.5.27 分镜质量与剧情生产约束合入，同时保留权限入口与 `admin-producer-v1` 契约；实际线上版本须在部署后核实，不能由本说明推定。

| 操作 | Admin | Producer |
| --- | --- | --- |
| 新建项目、导入小说/剧本/素材、读取工件 | 允许 | 允许 |
| 生产、修改、独立审计、按审计结果修正 | 允许 | 允许，仍须满足原有工单及审计门禁 |
| 导出及保存、挂载 Library 项目快照 | 允许 | 允许，仍须校验签名、哈希及代次 |
| 导入规则、规则/模板/Validator/错误码/Runtime/配置/版本维护 | 允许已有维护入口 | 拒绝 |
| 控制状态迁移、历史回滚、受控交接恢复、正式签核 | 允许 | 拒绝 |
| 管理账号角色、停用账号、读取账号访问日志 | 允许 | 拒绝 |
| 新增且尚未明确授权的工具、操作及工件类型 | 允许已有入口 | 默认拒绝 |

“允许”仍受原有功能及用户授权门禁约束；权限模块本身不会凭空增加修改服务器源码或部署版本的 MCP 工具。

## 身份与数据

OAuth 继续校验签名、issuer、audience、有效期及 scopes。账号角色仅由服务器配置和 `SHORTDRAMA_DATA_DIR/account_access.sqlite3` 决定，不采信工具参数、窗口角色、token 中的自由角色标签或可编辑用户 metadata。未配置的已认证用户默认是 Producer。无身份请求仍拒绝。

初始管理员通过服务器环境变量设置，必须填写插件身份提供方中的精确 User ID（OAuth `sub`），不能填写邮箱、ChatGPT 账号名或 OAuth client ID：

```dotenv
SHORTDRAMA_ADMIN_SUBJECTS=["auth0|YOUR_EXISTING_OWNER_SUBJECT"]
```

该值是 JSON 数组。角色保存于持久卷，按 issuer + subject 隔离；项目仍按现有 OAuth 用户命名空间隔离，不会自动向员工开放管理员的历史项目。导入的项目快照不能覆盖账号数据库或规则代码。部署代码、规则、模板仍由 Git/Railway 管理，不向 Producer 提供文件系统写入接口。

已有合法 token 每次调用都重新查询角色；降权和停用在下一次调用生效，已经开始的调用不强制中断。初始管理员和当前调用者不能通过账号管理工具降权或停用，避免误锁；初始管理员的更换通过服务器配置完成。

账号权限与生产/独立审计/修正的服务端执行身份是两层约束。`window_role_restrictions=false` 继续表示同一用户可换聊天窗口执行；`account_access.account_role_restrictions=true` 表示账号 RBAC 已启用，不能把两者混为一谈。

## 管理和员工使用

管理员连接现有 MCP 后调用 `get_agent_status`，确认 `data.account_access.role=admin`。Admin 发现 24 个工具，Producer 发现 21 个工具。新增的 `preflight_narrative_plan`、`review_narrative_artifact`、`record_narrative_attempt`、`read_narrative_record` 均对 Producer 开放，但保留生产及独立审查的服务端身份门禁。Producer 即使手动调用隐藏的管理工具也会被服务端拒绝。

`manage_user_access` 支持 `action=list`、`action=audit`，以及 `action=set` 搭配精确 `subject`、`role=admin|producer`、`enabled=true|false`。停用后同一旧 token 也无法继续使用工具。日志包含用户、角色、工具、项目、开始/成功/失败/拒绝和时间，不记录 token、原文、提示词或上传正文；更改角色与对应日志在同一个事务内写入。

员工使用自己的账号连接同一个 MCP 地址，首次调用状态时默认显示 Producer，无需复制管理员插件源码或共享管理员登录凭据。使用功能遵守同一组服务端规则。

## 上线顺序与验收

1. 从 0.5.28 升级时保留原 Auth0 配置、初始管理员环境变量及账号数据库；首次启用权限时才在 Auth0 Users 核对现有所有者的精确 User ID 并配置初始管理员。
2. 完成权限专项测试、完整服务回归、运行时自检、HTTP/stdio 和源码打包检查，再合并并部署。
3. `/health` 应报告 `version=0.5.29`、`account_access_contract=admin-producer-v1`、`account_role_restrictions=true`、`bootstrap_admin_configured=true`。
4. 用现有管理员连接验证账号角色；用独立 Producer 账号验证正常生产调用成功以及 `source_role=rule`、维护操作、账号管理被拒绝。线上账号验收必须使用真实 OAuth 用户，不能以本地测试冒充。
5. 升级改变规则版本指纹，已有项目后续写入前需刷新运行时自检证书；历史审计状态仍按原有签名和版本规则判断，不自动宣称重新通过。

持久卷中的账号数据库和访问日志应随管理员备份策略保存，源码包不包含它们。回滚到 0.5.26 会恢复旧版“所有已认证用户均无账号角色限制”的行为，因此不应在已分享给员工后直接回滚到没有 RBAC 的版本。
