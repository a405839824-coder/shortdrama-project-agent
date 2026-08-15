# Auth0 配置

Auth0 是身份提供商，不是 MCP 托管平台。先取得实际 MCP 的 HTTPS 根地址，再配置 Auth0；平台提供的免费或默认子域名可以使用，不要求自有域名。

## Auth0 侧

1. 创建或选择 Auth0 tenant。
2. 在 Tenant Settings → Advanced 中启用 Resource Parameter Compatibility Profile。
3. 创建一个 API/Resource Server：
   - Identifier/Audience：MCP 的 HTTPS 根地址，例如 `https://你的服务平台域名`
   - Signing algorithm：RS256
   - Scopes：`storyboard:read`、`storyboard:write`
4. 使用 RFC 9068 access-token profile；如启用 RBAC，应把上述权限分配给实际用户角色。
5. 将允许登录的数据库、Google 或企业连接配置为适合第三方 MCP 客户端使用的 domain-level connection。
6. 按 Auth0 MCP 授权文档启用 ChatGPT 所需的 CIMD 或 DCR 客户端注册方式和 PKCE。

## MCP 环境变量

```dotenv
PUBLIC_BASE_URL=https://你的服务平台域名
SHORTDRAMA_DEPLOYMENT_MODE=cloud
SHORTDRAMA_AUTH_MODE=oauth
OAUTH_ISSUER=https://你的租户.REGION.auth0.com/
OAUTH_AUDIENCE=https://你的服务平台域名
OAUTH_JWKS_URL=https://你的租户.REGION.auth0.com/.well-known/jwks.json
OAUTH_SCOPES=storyboard:read,storyboard:write
```

`OAUTH_ISSUER` 必须与访问令牌中的 `iss` 完全一致；Auth0 通常带结尾 `/`。不要把管理 API 密钥、客户端密钥或访问令牌写入本文件或聊天。

## 上线验证

- MCP 未登录访问返回 OAuth challenge。
- ChatGPT 能发现 Auth0 授权服务器元数据并完成浏览器登录。
- issuer、audience、签名、期限或 scope 任一不符时拒绝调用。
- 两个 Auth0 用户的项目命名空间完全隔离。

