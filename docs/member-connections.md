# 小团队共用 Google 的成员连接方案

适用版本：0.5.30。启用开关默认关闭；不能把代码检查通过或代码部署成功当成员工已接入。

员工各自使用自己的 ChatGPT，授权时可以选择同一个 Google。管理员为每位成员建立一个独立的 Auth0 confidential OAuth application，将它的 Client ID 和 Client Secret 只配置到该成员自己的 ChatGPT 插件连接。你的管理员连接使用另一套凭据。服务端按签名 access token 的 `iss + azp` 和允许的 `sub` 查找成员，再决定角色及项目空间。

这是按**独立连接凭据**划分成员，不是识别 ChatGPT 账号本身。把同一套连接凭据给两个人，两个人就会被当成同一成员。Client ID 是公开标识，安全边界是 Auth0 在授权码交换、续期时验证 Client Secret；所以绝不能给登记的客户端启用 public/none、implicit、password 或 client_credentials 流程。Google 账号以及 Auth0、Railway 管理后台自身的访问权限不受此插件权限控制，管理后台仍需由你独占的 MFA 保护。

## 实现效果

- 你：独立管理员连接，Admin，可管理规则及系统操作。
- 员工：每人独立连接，Producer，可生产、审计、修正和导出；不能使用管理员工具。
  这里限制的是分镜流程规则、审核标准、校验门禁和程序配置的修改权限；对白、画面、动作、镜头与剧情仍可按现有流程修改。详见[员工内容与流程权限边界](employee-permission-boundary.md)。
- 未登记连接：拒绝访问，即使它使用的是你的 Google。
- 共用 Google：项目空间与操作日志按 `member_id` 区分。
- 换用个人 Google：管理员把经过核对的 Auth0 `sub` 加入该成员的 `allowed_subjects`，成员 ID 与项目空间不变；不按邮箱自动绑定。
- 停用：原子更新成员配置后，下一次请求拒绝旧 access token。已经开始执行的操作会完成，不强行中断写入。
- 更新规则/服务端功能：所有连接继续访问同一个 MCP 服务。工具列表、参数或认证设置改变时，需要刷新 ChatGPT 连接并新开对话。

## 配置顺序

先在测试环境完成下列步骤，再切换生产。正式生产必须使用同一个持久卷和原有签名密钥；不要清空历史项目或 Library 指针。

1. 为你建立管理员 OAuth application，再为首位员工建立独立 application。Auth0 设置为第三方 Regular Web Application，token endpoint authentication 选 `client_secret_post`（或 `client_secret_basic`），grant types **只保留** `authorization_code` 和 `refresh_token`。授权码使用 PKCE S256。启用 Google connection，仅授权本插件 API 的 `storyboard:read`、`storyboard:write`，允许需要的 OIDC scopes / offline access。不要给它们 Management API 权限。
2. 在成员自己的 ChatGPT 中启用 Developer mode，进入 Plugins 新建连接，URL 填目标环境的 `/mcp`，OAuth 使用预先配置的 Client ID 与 Client Secret。生产 URL 是 `https://energetic-success-production-0634.up.railway.app/mcp`。从该连接管理页面复制**实际显示的回调 URL**到对应 Auth0 application；不要猜 callback ID，也不要填通配符。如果该员工界面不提供开发者连接或 OAuth 凭据配置，先解决账号/工作区访问条件，不要改用大家共用管理员连接。
3. 从 Auth0 用户页面核对 Google 的真实 `user_id`，作为 `allowed_subjects`。使用共享 Google 的不同成员可以填同一个值，但 Client ID 必须各不相同。新 Google 未经绑定不能进入已有成员的项目空间。
4. 按下面的 JSON 建立成员清单。不要把 Client Secret、密码或 Auth0 Management API token 写进去。
5. 在目标服务环境中使用 `scripts/configure_member_connections.py` 安装清单。它先读取 Auth0 的实际客户端配置，检查 confidential、授权类型与回调地址，再原子写入持久卷根部 `member_connections.json`。需要短期 `AUTH0_MANAGEMENT_TOKEN`，权限只需 `read:clients`；通过受保护的环境变量传入，完成后移除。不要把 token 放在命令行、聊天或 Git 中。
6. 启用 `SHORTDRAMA_MEMBER_CONNECTION_ACCESS=1` 并部署 0.5.30 或后续兼容版本。保留 `SHORTDRAMA_ADMIN_SUBJECTS` 仅用于核对历史 owner namespace，新模式下它不再授予管理员权限。新代码若发现成员文件存在但开关关闭，会拒绝启动，避免意外回退为共享 Google 管理员。
7. 用你的新管理员连接和员工连接完成下一节的验收后，再配置其他员工。旧连接若没有登记，会被拒绝，必须使用分配给自己的新凭据重新连接。

```json
{
  "schema_version": 1,
  "issuer": "https://YOUR_AUTH0_TENANT/",
  "audience": "https://YOUR_MCP_SERVER",
  "members": [
    {
      "member_id": "owner",
      "name": "管理员",
      "client_id": "OWNER_CONFIDENTIAL_CLIENT_ID",
      "role": "admin",
      "enabled": true,
      "allowed_subjects": ["google-oauth2|EXACT_SHARED_GOOGLE_SUBJECT"],
      "tokens_valid_after": 0,
      "legacy_subject": "google-oauth2|EXACT_SHARED_GOOGLE_SUBJECT"
    },
    {
      "member_id": "staff01",
      "name": "员工01",
      "client_id": "STAFF01_CONFIDENTIAL_CLIENT_ID",
      "role": "producer",
      "enabled": true,
      "allowed_subjects": ["google-oauth2|EXACT_SHARED_GOOGLE_SUBJECT"],
      "tokens_valid_after": 0
    }
  ]
}
```

`issuer`、`audience` 必须与目标服务的 OAuth 配置完全一致，issuer 末尾斜线不能省略。只有对应既有初始管理员的 Admin 成员才能设置 `legacy_subject`；它继续使用原空间，不复制或改写旧数据。员工不能设置它。

在已配置该环境的 `OAUTH_ISSUER`、`OAUTH_AUDIENCE`、`OAUTH_JWKS_URL`、`SHORTDRAMA_ADMIN_SUBJECTS`、`SHORTDRAMA_DATA_DIR` 等变量的终端执行：

```sh
python scripts/configure_member_connections.py /secure/reviewed-members.json --check
python scripts/configure_member_connections.py /secure/reviewed-members.json
```

`--check` 仅验证本地结构，不访问 Auth0、不安装、不代表可以上线。正式安装只读取 Auth0 配置，不创建客户端或修改 Auth0 权限。客户端创建和 ChatGPT 凭据输入仍是配置步骤。

## 上线验收

1. 新管理员连接调用 `get_agent_status`：`identity_contract=member-connections-v1`、`member_id=owner`、`role=admin`，可读取原有项目及 Library 指针。
2. 首位员工用相同 Google 授权，结果应为其专属 `member_id`、`role=producer`，可见 21 个工具；你的管理员可见 24 个工具。直接调用管理工具也必须被拒绝。
3. 两位员工各建测试项目，互相读取对方项目 ID 必须失败；自己的项目可继续使用。
4. 真实走一次 Auth0 refresh token 续期，再核对成员 ID 不变。单元测试签名的“续期后 access token”不能替代这一步。
5. 停用一位员工后，用已经签发的 token 重试，必须被拒绝；管理员不受影响。
6. 给一位员工绑定其个人 Google，重新连接后应仍是同一成员、同一项目空间。任何未批准 Google / DCR / CIMD 连接都不能获得 Admin。
7. 在健康检查确认 `member_connection_access=true` 后记录部署 SHA、上述验收结果，再扩大使用范围。

## 日常维护及恢复

通过服务器维护清单，使用同一脚本校验并原子安装。新请求重新读取清单，无需为停用成员重启服务。已存在的成员 ID 不得删除或交给新人；离职设 `enabled=false`，同时在 Auth0 撤销其 refresh tokens / client credentials。需要废除某时刻之前的 access token，可将 `tokens_valid_after` 设为 Unix 秒数，时间下限不得倒退。重新启用成员前更新该下限并重新授权。

密钥泄露时，为该成员创建全新的 confidential client，更新 `client_id`，撤销旧客户端，再重新连接；成员 ID 与空间保持原值，旧 Client ID 不要转给其他人。Auth0 配置改变后重新运行安装校验。服务端信任授权服务器正确验证客户端，不能从 JWT 独立推断 Auth0 后台是否被改成 public client。

故障恢复优先恢复经验证的成员清单与兼容此模式的服务版本。不要删除成员文件、关闭开关或回滚到旧的按 Google sub 授予 Admin 的版本后继续让员工使用。历史签名密钥、Library 指针及项目文件保持原样。

独立 ChatGPT 账号的 Library 文件访问权限仍分别归属于各账号。此方案不自动分享你的旧项目、上传文件或聊天记录；如需多人协作同一个项目，需要另外设计明确的项目共享权限。

## 依据与验证边界

实现基于 OpenAI 的 [OAuth / predefined client 协议](https://developers.openai.com/plugins/build/auth)、[连接与刷新流程](https://developers.openai.com/plugins/deploy/connect-chatgpt)，以及 Auth0 的 [客户端认证](https://auth0.com/docs/secure/application-credentials)和[授权类型](https://auth0.com/docs/get-started/applications/application-grant-types)。每位成员独立 confidential client 是本项目的设计选择。

本地验证包含 RSA 签名 token 的 HTTP `/mcp` 调用、10 个成员空间、权限拒绝、禁用、错误配置、Google 绑定、历史 owner 空间及配置切换期间身份一致性。它不等于真实员工账号接入、Auth0 续期成功、生产部署或 10 人重任务并发性能验收。
