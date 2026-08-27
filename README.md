# 分镜项目 Agent 0.4.0

这是一个“技能 + MCP 执行服务”的完整工具型插件。ChatGPT 负责理解、生成与改写内容，MCP 负责项目状态、文件导入、版本化提交、全表审计、修复回归、跨聊天问题台账和人工签核。没有真实工具调用时，插件规则禁止声称“已经执行”。

## 当前状态

- 本地 MCP：可运行，Streamable HTTP 端点为 `/mcp`
- 工具：14 个，覆盖项目、工单、自动批量文件读取、变更、剧本/方案、全量与增量分镜、独立审计、受控局部修复、验证、能力缺口和签核
- R10 运行时：`V16.3-148-R10-StableRuntime`、`R10-PG1`
- 小说转分镜：完整章节读取→剧情地图→取舍台账→人物状态/认知→对白保护→时长预算→节拍→100%覆盖审计
- 公共场景生态：街道、市集、商街、巷道、城门、码头、公共餐饮等场景强制普通人物密度/角色/错峰动作/空间层次，以及具体近中远环境声、对白避让和声画来源；有因空场除外
- 正式视频执行：`Seedance 2.5独立正式执行分镜`；常规批次保持4–15秒，30秒仅用于明确长场景模式
- 跨聊天：同一用户命名空间 + 同一 `project_id` 共用项目中枢和问题台账
- 窗口架构：生产与独立审计为两个常驻独立 authority；只有审计失败且用户授权修复时才启用第三个 repair authority，修复结果必须返回原审计 authority 复核
- 增量提速：已有分镜的局部变更使用 `commit_storyboard_delta`，只传受影响镜头及边界；服务端校验稳定 UID、CAS、保护范围，确定性物化派生字段后仍执行完整 R10 全表生产校验并生成独立审计 handoff
- 安全修复：非结构字段使用证书约束 patch；结构、边界、认知、原文证据、镜头顺序/时长或核心计划变化必须使用绑定同一修复包的完整场景/分集重建。生产 delta 不能借给 repair 绕过证书；repair 不能签发最终结论，也不能替代独立审计
- 云端：已锁定 Railway + Railway 自动域名 + Auth0，并补齐部署、持久卷、预检和验收配置
- UI：当前采用工具型插件，不含自定义小组件；功能会出现在插件/MCP 工具中，不依赖聊天窗口的“技能选项”

## 本地运行

macOS / Linux：

```bash
./scripts/setup_local.sh
./scripts/run_tests.sh
./scripts/run_local.sh
```

Windows PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./scripts/setup_local.ps1
./scripts/run_tests.ps1
./scripts/run_local.ps1
```

运行后检查：

- 健康检查：`http://127.0.0.1:8000/health`
- MCP：`http://127.0.0.1:8000/mcp`

可选：用 MCP Inspector 检查工具。

```bash
npx @modelcontextprotocol/inspector@latest
```

在 Inspector 中选择 Streamable HTTP，输入 `http://127.0.0.1:8000/mcp`。

## 在 ChatGPT 中做本地联调

ChatGPT 不能直接访问普通的 `localhost`。先保持本地服务运行，再通过 Secure MCP Tunnel 或你自己的临时 HTTPS 转发地址暴露 `/mcp`。

1. ChatGPT → Settings → Security and login → 打开 Developer mode。
2. 打开 ChatGPT Plugins，点加号。
3. 选择 Tunnel，或填写完整 HTTPS 地址，例如 `https://你的测试域名/mcp`。
4. 创建连接，确认能发现 14 个工具。
5. 从浏览器地址复制 `plugin_asdk_app...` 技术 ID。
6. 在本目录运行：

```bash
./.venv/bin/python scripts/configure_chatgpt_connection.py plugin_asdk_app_xxx
```

脚本会生成 `.app.json`，给插件清单加入 `"apps": "./.app.json"`，并默认关闭清单中的本地 stdio MCP，防止同名工具重复。然后把插件加入本地 marketplace，重启 ChatGPT 桌面端并安装：

```bash
./.venv/bin/python scripts/install_repo_marketplace.py --repo-root ../..
```

若仓库的 `.agents` 目录受管理策略保护，请在你有写权限的真实仓库中运行这一步，或使用个人 marketplace。

需要恢复“本地 stdio + ChatGPT 连接”双路模式时，可加 `--keep-bundled-mcp`；普通 ChatGPT 插件测试不建议双开。

## 新聊天怎么使用

安装并启用插件后，普通中文请求即可触发，不需要输入固定口令。建议首句包含项目名；继续旧项目时附上 `project_id` 最稳妥。

按角色使用独立聊天；无需每次同时打开三个：

1. 窗口1（生产）：`使用分镜项目 Agent 创建《项目名》，导入剧本并生成第一集正式分镜；完成生产预检后交给独立审计。`
2. 窗口2（独立审计）：`继续项目 PROJECT_ID，建立 storyboard_audit / audit_only / read_only 工单，只审计生产版本，不修改。`
3. 仅当窗口2判定失败且你明确授权修复时，窗口3建立独立 repair 工单并消费签名修复包；修复后把新版本交回原窗口2复审。审计通过时不创建窗口3。

每个角色都应先调用 `get_agent_status`，再分别建立角色专属工单并执行。判断是否真的执行，只看工具结果中是否出现 `project_id`、`work_order_id`、`artifact_id`、handoff或下载链接；只有文字说明而没有这些标识，等于没有执行。

## 云端 MCP

云端必须使用稳定 HTTPS、持久卷和 OAuth 2.1。服务在 `SHORTDRAMA_DEPLOYMENT_MODE=cloud` 时会拒绝无认证启动，避免不同用户的私有项目混用。

1. 复制 `.env.cloud.example` 为 `.env.cloud`，填入域名和 OAuth 参数；不要提交该文件。
2. 用 `Dockerfile` 部署，并把 `/data` 挂载到持久存储。
3. 确认 `https://你的域名/health` 和 `https://你的域名/mcp` 可访问。
4. 在 ChatGPT Developer mode 中创建该 MCP 连接。
5. 用返回的 `plugin_asdk_app...` ID 运行连接配置脚本。
6. 刷新连接元数据，在新聊天运行 `docs/CHATGPT_EVAL_PROMPTS.md`。

Railway 逐步执行单见 [docs/RAILWAY_DEPLOY.md](docs/RAILWAY_DEPLOY.md)；通用门禁见 [docs/CLOUD_MCP.md](docs/CLOUD_MCP.md)；Auth0 设置见 [docs/AUTH0.md](docs/AUTH0.md)。

## 数据和安全边界

- 本地模式是单用户命名空间；云端模式按 OAuth `issuer + subject` 隔离用户。
- 文件下载限制大小、协议和目标地址；默认阻止 localhost、私网和保留地址，降低 SSRF 风险。
- 输出文件从不覆盖旧版，下载链接带短期签名。
- 相同 canonical、源文、边界、规则、验证器和审计类别时才允许复用同类审计；生产预检永远不能替代首次独立审计。
- 长章节默认自动批量读取，单次总正文最多 16,000 字符；连续令牌必须无重叠、无缺口，条件读取命中时不重新解析正文。调用方可显式关闭自动批量以兼容旧流程。
- 服务端 trace 明确标记 `measurement_scope=server_only`；无法观测的 ChatGPT 规划/生成时长标记为未测量，绝不把数秒工具耗时冒充聊天总耗时。
- 修改后必须全链路回归；残留问题或新增问题不为零时，不能输出 PASS。
- 公共场景只写“人群/路人/环境声/街道嘈杂声”、匿名群演完整台词、时代错位声源或Seedance未复现声画生态时，`commit_storyboard`会返回阻塞错误。
- 人工签核只能由用户明确批准后调用，Agent 不能替用户签字。

官方依据：

- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/build/mcp-server
- https://developers.openai.com/plugins/deploy/connect-chatgpt
- https://developers.openai.com/plugins/build/auth
