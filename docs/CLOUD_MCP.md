# 云端 MCP 上线门禁

## 必填外部条件

真正部署前必须确定三项：稳定 HTTPS 域名、支持 MCP OAuth 2.1 的身份提供商、带备份的持久存储。仓库不会替用户购买云资源、创建域名或擅自配置账户。

## 部署配置

使用 `.env.cloud.example` 作为模板。关键约束：

- `PUBLIC_BASE_URL` 必须是服务的 HTTPS 根地址，不含 `/mcp`
- `SHORTDRAMA_DEPLOYMENT_MODE=cloud`
- `SHORTDRAMA_AUTH_MODE=oauth`
- `OAUTH_ISSUER`、`OAUTH_AUDIENCE`、`OAUTH_JWKS_URL` 必须与身份提供商配置一致
- `/data` 必须是持久卷；多副本部署时应改用共享事务存储，当前文件锁版本默认单副本
- 反向代理必须允许 MCP Streamable HTTP，并把 `Authorization` 请求头传给应用

## 安全检查

上线前验证：

1. 无令牌访问受保护 MCP 工具会被拒绝。
2. 错误签名、错误 issuer、错误 audience、过期令牌和缺少 scope 均会被拒绝。
3. 用户 A 无法读取用户 B 的 `project_id`。
4. 文件导入拒绝 HTTP、私网 IP、重定向到私网以及超过 50 MiB 的内容。
5. `/files/...` 链接过期、被篡改或引用不存在文件时返回失败。
6. `/data` 重启后仍保留项目中枢、问题台账和所有版本。

## ChatGPT 连接

1. 在 ChatGPT Developer mode 新建 MCP 连接，URL 必须包含 `/mcp`。
2. 完成 OAuth 登录并检查 19 个精确名称匹配的工具及其读写注解。
3. 复制地址栏中的 `plugin_asdk_app...` ID。
4. 运行 `scripts/configure_chatgpt_connection.py`。
5. 安装本地插件包，或按工作区流程发布私有插件。
6. 每次修改工具名称、schema、认证或注解后，在 ChatGPT Plugins 页面 Refresh，并从新聊天复测。

## 当前有意限制

- 当前是工具型插件，没有自定义 UI。
- 当前持久化层面向单实例文件卷；横向扩容需要把存储适配为数据库/对象存储并增加并发测试。
- 当前不主动读取其他聊天正文；跨聊天只同步 MCP 中已记录的结构化项目状态和问题台账。
- 插件中的 ChatGPT 模型完成创作与改写；MCP 不单独调用 OpenAI API，因此本地测试不需要 `OPENAI_API_KEY`。
