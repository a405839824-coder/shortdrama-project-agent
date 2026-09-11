# Railway 部署执行单

本项目使用 Railway 托管 MCP、Railway Volume 持久化 `/data`、Auth0 提供 OAuth。Railway 自动域名即可，不需要自有域名。当前文件存储实现必须保持单副本。

## 已有服务升级到 0.5.30

已有线上 0.5.29 服务应使用合并后的 0.5.30 发布树，保留 Admin / Producer 权限、Auth0 配置、原 `/data` 卷与现有域名。不要用旧开发树覆盖，也不要为升级重复创建项目或卷。源码版本号不等于线上已部署；发布后必须读取线上 `/health` 与认证后的工具目录核实。

0.5.30 的目录仍为 Admin 24 个工具、Producer 21 个工具。Producer 仍不可见或调用 `manage_user_access`、`recover_storyboard_handoff`、`signoff_task`。成员连接模式默认关闭；真实 Auth0 客户端、管理员和员工连接未准备好时，不要启用该模式。代码部署成功不能当成旧共享管理员连接已隔离。详见 [升级说明](UPGRADE_0.5.30.md)。

下列创建项目步骤仅用于首次安装。

## 1. 创建空服务和域名

1. 登录 Railway，新建项目 `shortdrama-project-agent`。
2. 在项目里创建 Empty Service，服务名同上。
3. 给该服务添加 Volume，Mount Path 必须为 `/data`。
4. 在 Networking → Public Networking 生成一个 `*.up.railway.app` 域名，记下完整域名。
5. 不要先上传代码；缺少 OAuth 变量时，云端安全门禁会主动拒绝启动。

创建项目、服务、卷或部署可能计入 Railway 用量。界面若要求升级或确认费用，先停止并由账户所有者决定。

## 2. 配置 Auth0

按 [AUTH0.md](AUTH0.md) 创建 API/Resource Server：

- Identifier/Audience：`https://你的服务.up.railway.app`
- Signing algorithm：RS256
- Scopes：`storyboard:read`、`storyboard:write`

保留 Auth0 租户域名，例如 `tenant.us.auth0.com`。不要把客户端密钥、管理 API 密钥或访问令牌发到聊天中。

## 3. 生成 Railway Variables

在插件目录运行：

```bash
./.venv/bin/python scripts/configure_railway_env.py \
  --railway-domain 你的服务.up.railway.app \
  --auth0-domain 你的租户.REGION.auth0.com
```

脚本生成被 Git 忽略的 `.railway.env`。在 Railway 服务的 Variables → Raw Editor 中粘贴全部内容。它不含密钥，也不设置 `PORT`；Railway 会自动注入端口。

## 4. 上传和发布

安装并登录 Railway CLI 后，在插件目录运行：

```bash
railway link
railway up
```

若服务尚未关联，可先运行 `railway service` 选择 `shortdrama-project-agent`。`railway.json` 会指定 Dockerfile、`/health` 健康检查、单副本和失败重启策略。

不要把 `.env.cloud`、`.railway.env`、令牌或 Auth0 密钥提交到代码仓库。

## 5. 云端验收

部署成功后运行：

```bash
./.venv/bin/python scripts/check_cloud_endpoint.py \
  https://你的服务.up.railway.app
```

验收必须同时满足：

- `/health` 返回 `status=pass`、`version=0.5.30`，且账号权限契约仍为 `admin-producer-v1`；成员配置启用后还须 `member_connection_access=true`
- 未登录调用 `/mcp` 返回 401 或 403
- 响应包含 Bearer OAuth challenge
- Railway 重启服务后，`/data` 中的项目仍存在

## 6. 接入 ChatGPT

1. ChatGPT → Settings → Apps & Connectors → Advanced settings，打开 Developer mode。
2. 创建自定义 MCP 连接，URL 为 `https://你的服务.up.railway.app/mcp`。
3. 完成 Auth0 登录，刷新连接，确认 Admin 发现 24 个工具、Producer 发现 21 个工具，并核对新增剧情工具及原权限隔离。
4. 复制浏览器地址中的 `plugin_asdk_app...` ID，运行：

```bash
./.venv/bin/python scripts/configure_chatgpt_connection.py plugin_asdk_app_xxx
```

5. 新聊天输入：`使用分镜项目 Agent 创建《测试项目》，先检查 Agent 状态。`

只有出现真实工具调用及 `project_id` 才表示已执行。
