# 分镜项目 Agent 0.5.11

这是一个“技能 + MCP 执行服务”的完整工具型插件。ChatGPT 负责理解、生成与改写内容，MCP 负责项目状态、文件导入、版本化提交、全表审计、修复回归、跨聊天问题台账和人工签核。没有真实工具调用时，插件规则禁止声称“已经执行”。

## 当前状态

- 本地 MCP：可运行，Streamable HTTP 端点为 `/mcp`
- 工具：19 个，覆盖项目、工单、状态迁移、受控交接恢复、跨批次问题画像、草稿预检、自动批量文件读取、变更、剧本/方案、全量与增量分镜、独立审计、受控局部修复、验证、能力缺口、签核及按需性能诊断
- R10 运行时：`R10.1` 紧凑母版 Schema，工作流保持 `R10-PG1`；底层规则运行时标识为 `V16.3-148-R10-StableRuntime`
- 小说转分镜：完整章节读取→剧情地图→取舍台账→人物状态/认知→对白保护→时长预算→节拍→100%覆盖审计
- 公共场景生态：街道、市集、商街、巷道、城门、码头、公共餐饮等场景强制普通人物密度/角色/错峰动作/空间层次，以及具体近中远环境声、对白避让和声画来源；有因空场除外
- 27 字段 canonical：每镜只保存一次语义事实，模型字段固定为 `镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`。服务端内部 UID/分集/场景身份不属于模型写入字段
- 唯一视频执行真值：`正式分镜母版` 是单镜空白上下文下唯一完整视频执行母版；旧列名`正式分镜母版·字段合并`只读兼容；新写入不再创建独立 Seedance 字段、Seedance 提示词、`batches` 对象或“生成批次”工作表
- 画面与表演合并：人物可见动作、表情、精确对白、六维发声细节、标点效果、听话反应、呼吸和镜尾状态全部只写在 `画面内容`；`音色`保持独立。面向 Seedance 2.5 的新写入必须恰好包含 3 段连续、无重叠且闭合到本镜时长的时间阶段
- 光照闭合：`光影、光源来源、光源方向、色温倾向` 四项必须同时存在，并接受同场景连续性校验
- 精确 10 列 Excel：`分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`
- 0.5.5 初稿质量门：生成前读取跨批次问题画像，逐场景执行无产物预检，再对完整 canonical 签发全量预检证书；总体正确率与直接可用率均须至少98%，每集至少96%，新增局部问题率最多2%，硬错、重大问题、已知问题复发和系统性重复均为0
- 0.5.5 模板硬门禁：逐镜`9:16竖屏`、转场去重、静态关键帧、无跨镜代指、对白逐字与说话人一致、主角人数/单机位/状态/道具/时长容量闭合；单镜执行字段禁止`BGM`字面量
- 0.5.11 旧交接生产预检证据受控补签：只对`GAP-1bb91d83751b422f`登记的精确后六条交接追加独立的`historical_production_preflight_attestation`。证据同时封存冻结历史合同`historical_contract_pass`与当前validator的完整`fail_current`诊断；当前FAIL只允许封存的`E-I2V-INDEP-002 / THG-01`缺少逐字`9:16竖屏`措辞漂移，其他问题零写入失败关闭。它不声称当前生产预检通过，`HISTCACHE`不得作为当前预检或独立审计缓存复用。补签后仅在`GAP-0e5d48969c4c494e`下按精确顺序统一恢复原11条，旧交接只标记`superseded`，新交接保持`prepared + PENDING_AUDIT`并强制完整当前独立审计
- 0.5.10 旧交接生产预检证据受控补签：仅在`recover_storyboard_handoff` 的补签/恢复内部路由复验历史工作簿。可接受精确签名的0.5.4/当前投影，或通过冻结0.5.3投影器逐格重放、且与8列canonical侧车及正式10列短表头完全一致的合法基线组合。复验证明必须绑定原始XLSX、canonical、旧handoff、旧生产证书与原生产trace；一般`extract_xlsx`不放宽，不修改任何原件/历史记录，公共工具仍精确为19个
- 0.5.9 旧工具参数表受控兼容：`recover_storyboard_handoff`省略或提交`null` phase时，不再默认恢复阶段；服务端先校验当前会话和`rule_improvement + execute + explicit_execute` production工单，再只从targets中唯一、精确的`controlled_phase:preflight_evidence_backfill`或`controlled_phase:handoff_recovery`令牌推断。无令牌、重复、双阶段、未知、大小写/空白漂移或任意额外target全部零写入失败关闭；工具总数仍精确为19
- 0.5.8 历史Hub交接投影严格兼容：旧交接恢复/补签读取只接受两种精确形态——去掉`record_id`、`project_id`、`integrity_hmac`的签名记录投影，或包含这三个信封字段的完整签名记录。任何混合、增删或字段漂移一律在写入前失败关闭。该兼容只读，不迁移、不规范化或改写历史记录；未来生产Hub写入统一使用去信封投影
- 0.5.7 旧交接生产预检证据受控补签：精确19工具不增项，在`recover_storyboard_handoff`内新增`phase=preflight_evidence_backfill`。补签与恢复必须分别使用两张不同的、绑定当前production窗口的`rule_improvement + execute + explicit_execute`工单；各自targets精确包含gap、对应handoff IDs及`controlled_phase:preflight_evidence_backfill`或`controlled_phase:handoff_recovery`。缺项、额外项、token错误或复用同一工单时，plan以`scope_mismatch`零写入阻断。整批有序handoff授权同时绑定计划、逐条证据/证书、真实计时trace和批次trace；补签执行必须回传原样`plan_sha256`与`REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE`，且只追加签名生产预检证据，不改旧handoff/证书、分镜、canonical、XLSX或历史审计，也不签发独立审计结论
- 0.5.6 源文对白权威：只从服务器登记的导入源文运行闭集 parser v6，并交叉核对 payload SHA、Hub authority entry 与 artifact metadata 中的 source role；逐 occurrence 绑定精确文本、标点、说话人和源文位置。候选 inventory/`source_lock`/台账/认知/锚点不能反向教解析器。任何歧义整批失败关闭且零正式产物
- 0.5.6 受控交接恢复：`recover_storyboard_handoff`先预演并返回精确`plan_sha256`，执行时要求原样回传哈希和确认字面量；每条失效`prepared production_to_audit`使用签名日志与CAS保留旧记录并标记`superseded`，再按当前15项完整指纹签发新的`PENDING_AUDIT`交接。该路线不改写分镜/canonical/XLSX，也不签发或复用独立审计结论
- 0.5.6 独立复审血缘：每轮强制复审使用新的服务器签名 successor handoff 和 route/event trace，保留已消费 root；阻断问题必须落到 canonical 镜头，只有最新失败血缘的修复包可用。FAIL 转 PASS 必须先生成新修复 artifact/canonical，并更换 file/source_version/artifact/canonical/handoff/trace 绑定，不能在同一字节上换标签翻绿
- 0.5.6 证据桥与修复生命周期：现代证书绑定精确 event SHA，successor 绑定父证书 HMAC 与父 event SHA；旧证书只能经强制新独审降为历史祖先。修复包只允许 issued/consuming/consumed，删除已消费包或伪造 consumed 不能复活授权
- 0.5.6 Schema/存储同口径：任一 successor 标记都会触发完整权威与 claim 状态约束，并继承根交接的 production、repair 或 external-input 路线而不伪装成生产血缘；full-repair 区分“记录缺失”和“已存在记录完整性损坏”。运行时全量证书只保存在签名 certificate store，不在 Hub 复制一份易漂移投影；旧 Hub 副本由控制态迁移清除，签名证书不变；prepared/replaying/rolling_back 迁移全项目封写，rolled-back 重执行先签 replaying 意图并在项目锁内复验当前 PASS 证书
- 0.5.6 崩溃恢复与台账重放：event-only 半写只在重交 semantic/event/trace/指纹完全一致时确定性补证书，certificate-only 和变体重试失败关闭；旧台账 checkpoint 必须由独立签名 migration journal/CAS 授权，检查点后的 issue/history/location/counter 全部从新增 event 重放
- 审计状态隔离：生产文件只写`PENDING_AUDIT／待独立审计`且永不就地回填；独立审计签发后创建新的不可变签名 overlay successor，并只在新工件中写入问题计数、trace、canonical 哈希与文件指纹
- GPT 导出投影：`GPT关键帧生成指令`只在导出时从同一 canonical 确定性生成，不写回 canonical；投影会移除时间段、对白、口型和声音指令
- 跨聊天：同一用户命名空间 + 同一 `project_id` 共用项目中枢和问题台账
- 窗口架构：生产与独立审计为两个常驻独立 authority；只有审计失败且用户授权修复时才启用第三个 repair authority，修复结果必须返回原审计 authority 复核
- 增量提速：已有分镜的同身份、同数量、对白与人物认知不变的局部执行变化使用 `commit_storyboard_delta`，只传受影响的完整 27 字段镜头及边界；服务端校验稳定 UID、CAS、保护范围，合并未变化镜头后仍执行完整 R10.1 全表生产校验并生成独立审计 handoff。任何对白或说话人变化、心理音轨、插删移、拆并、重编号或上游证据失效，都重建完整受影响场景/分集
- 停止后台重复生成：canonical 不持久化正式母版、GPT投影、独立 Seedance 文本或批次；导出器只对最终 canonical 执行一次正式母版和GPT投影，不在生产、审计、修复、缓存预热或后台任务中再次扩写语义
- 真实独立审计：首次或指纹变化后的 `audit_storyboard` 必须提交绑定精确 canonical 的八层 `semantic_audit` 证据；服务端把它与新鲜机器报告合并，不能再由机器零报错自动合成独立语义通过。仅精确独立审计缓存命中可省略
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
4. 创建连接，确认能发现 19 个工具。
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

每个角色都应先调用 `get_agent_status`。0.5.5 正式生产固定执行：状态检查 → 必要时 `migrate_project_state` → `query_project_issues` 读取活动问题画像 → 每场景 `preflight_storyboard_draft` → 完整 canonical 的全量预检 → 携带签名 `draft_preflight_id` 调用 `commit_storyboard` → 在独立窗口执行 `audit_storyboard`。失败草稿只写入问题台账和质量画像，不创建 artifact、版本、XLSX 或 handoff；生产必须修复失败范围后重新预检。

初稿质量等级与最终发布结论分开：初稿达到总体/直接可用≥98%、单集≥96%、新增局部≤2%，且硬错、重大问题、已知复发、系统性重复均为0，才算达到 A 级生产质量；全量预检仍须无未解决问题才签发可提交证书。独立审计不是替生产清理明显错误，而是对生产可能遗漏的语义问题做第二道防线；最终发布要求八层独立审计100%通过、残留0、新增0、阻塞0。

判断是否真的走完优化流程，要同时看到同一 artifact/canonical 绑定的草稿预检证书、生产 `trace_id` 和独立审计 `trace_id`，以及 handoff 与 audit certificate；缺少任一追踪只能记为 `fast_path_incomplete`。只有文字说明而没有这些标识，等于没有执行。

0.5.11 的受控历史生产预检证明与精确11条恢复见 [docs/UPGRADE_0.5.11.md](docs/UPGRADE_0.5.11.md)；0.5.10 的历史工作簿受控复验与补签见 [docs/UPGRADE_0.5.10.md](docs/UPGRADE_0.5.10.md)；0.5.9 的旧工具参数表受控阶段推断见 [docs/UPGRADE_0.5.9.md](docs/UPGRADE_0.5.9.md)；0.5.8 的历史Hub交接投影严格只读兼容见 [docs/UPGRADE_0.5.8.md](docs/UPGRADE_0.5.8.md)；0.5.7 的旧交接生产预检证据受控补签见 [docs/UPGRADE_0.5.7.md](docs/UPGRADE_0.5.7.md)；0.5.6 首次引入的源文权威、独立复审、台账重放与受控交接恢复见 [docs/UPGRADE_0.5.6.md](docs/UPGRADE_0.5.6.md)；0.5.5 的初稿质量门、项目迁移和发布验收见 [docs/UPGRADE_0.5.5.md](docs/UPGRADE_0.5.5.md)；0.5.4 的集成升级历史见 [docs/UPGRADE_0.5.4.md](docs/UPGRADE_0.5.4.md)。十二项模板硬门禁的原始设计见 [docs/UPGRADE_0.5.1.md](docs/UPGRADE_0.5.1.md)，无API性能诊断见 [docs/UPGRADE_0.5.3.md](docs/UPGRADE_0.5.3.md)。

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
- 公共场景只写“人群/路人/环境声/街道嘈杂声”、匿名群演完整台词、时代错位声源，或正式母版未完整复现声画生态时，`commit_storyboard`会返回阻塞错误。
- 人工签核只能由用户明确批准后调用，Agent 不能替用户签字。

## Legacy 兼容边界

- 0.3.x/0.4.x canonical、旧 10 列工作簿和旧 Seedance/批次字段仅用于只读导入与审计取证；历史 artifact、原始字节和原 canonical 哈希保持不变。
- 需要继续生产时，服务端把旧字段确定性折叠到一个新的 R10.1 successor：旧人物调度进入空间/起始状态，旧动作、表演、对白、发声和标点信息只合并到 `画面内容`，可保留的音色与四项光照原样迁移。
- 新 successor 永不重新写出已删除字段、独立 Seedance 列、独立提示词、批次对象或“生成批次”工作表。若旧数据不足以得到必需的 `起始状态` 或其他事实，迁移必须阻断并要求补充，禁止服务端编造。

官方依据：

- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/build/mcp-server
- https://developers.openai.com/plugins/deploy/connect-chatgpt
- https://developers.openai.com/plugins/build/auth

## 发布包

默认构建的是可审查、可部署但不包含 Python 虚拟环境的源码包：

```bash
./.venv/bin/python scripts/package_plugin.py --profile source
```

ChatGPT 私有连接包必须先用 Developer mode 返回的真实 `plugin_asdk_app...` 技术 ID 生成 `.app.json`，再构建：

```bash
./.venv/bin/python scripts/configure_chatgpt_connection.py plugin_asdk_app_xxx
./.venv/bin/python scripts/package_plugin.py --profile connected
```

`connected` 包只包含技能、图标、清单和该连接描述，不包含 `.mcp.json`、服务端代码、测试、数据、虚拟环境或凭据。构建器使用允许根、manifest 引用闭包、敏感路径/内容扫描、原子 ZIP 和 `SHA256SUMS` 门禁；源码包会明确标记为非便携安装包。
