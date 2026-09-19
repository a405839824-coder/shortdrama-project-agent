# 分镜项目 Agent 0.5.45

0.5.45 补齐逐镜审计覆盖签证，当前字段和规则变化使旧声明失效；修正人物/道具识别及强制表演误报，增加只读诊断分页、流程状态与脱敏失败追踪。保留历史证据和唯一R5模板，不把测试通过当成首稿98%证明。见 [升级说明](docs/UPGRADE_0.5.45.md)。

0.5.43 修复新生产工单同时引用来源图和集数时误触既有分镜范围保护的问题，原分镜修改仍严格保护范围外镜头。歧义场景别名的旧失败只在同工单完整预检通过、各稳定场景已有签名验证且原作用域镜头逐字相同时追加覆盖解决记录；旧失败与首稿不改写。相同输入、来源和运行时的纯编译结果限量复用，避免逐场检查重复编译整批；每次仍核对候选全部绑定与实际字段，权限、权威源、独立审计和正式回执不从此缓存取得。

0.5.42 修复跨 JSON 运行时将0.0传输为0时误判生产绑定失效的问题。计划、动作与成品绑定统一按相同数值校验；原文字符串及来源图文件仍逐字节绑定，实际数值和台词变更仍拦截。

0.5.41 将正式 HTTP 入口的请求体上限设为32MiB，容纳携带完整来源与状态证据的分镜；超过上限仍拒绝，来源、权限、模板与审计门禁不变。

0.5.40 修复三人画面站位子句串接与换行识别，并补充由真实正面头部可见条件约束的面部近景输入。唯一模板和审计门禁不变；面部面积仍须独立核对。

0.5.39 补充真实支撑接触中的扶起动作与道具承托关系；前者保留双人全程状态，后者使杯随托盘移动。唯一模板、源文动作冻结和正式审计门禁不变。

0.5.38 修正多场景生产预检的完整生成凭据衔接：校验原始完整记录后筛选场景问题，篡改正文、来源或世界状态仍会阻断。唯一模板与独立审计要求不变。

0.5.37 补齐同源姿态、过门、物件、穿戴、身体接触、心声和逐句声线执行，保留唯一正式模板及全部交付校验。[本版范围](docs/UPGRADE_0.5.37.md)。

0.5.36 增加唯一模板正式交付核验及批次引用检查；员工连接包排除本地生产程序，
ChatGPT 和 Codex 使用同一远程模板。未核验的文件不能作为正式完成或续作依据。
范围、接口和宿主边界见 [升级说明](docs/UPGRADE_0.5.36.md)。

0.5.35 将决定依据、人物知情与整镜声画交付写入同一规则源。新生产要求先冻结计划，核对规则、来源和初态，并从实际事件和机位检查依赖；保留原始失败尝试，未知与未支持不冒充通过。继续使用同源 R5 母版与无数字时间码的画面内容；软件通过不代表真实首稿98%已验证。见 [升级说明](docs/UPGRADE_0.5.35.md)。

0.5.31 取消画面内容的数字制作时间码，正式母版输出自然动作正文；内部计时与正文绑定并继续检查对白容量，独立时长列保留。补充跨镜代指词形和明确对白的语境识别，旧稿与签审计证据保持独立。完整 R5 阅读卡片渲染器及更广语义约束仍为后续范围。见 [0.5.31 升级说明](docs/UPGRADE_0.5.31.md)。

0.5.30 增加默认关闭的成员连接模式：每位成员使用独立的 confidential OAuth 连接凭据，共用 Google 时仍分别授权及隔离项目；启用后未登记连接拒绝访问，管理员可保留历史项目空间。员工可修改分镜内容，不能修改分镜流程规则、审核门禁或程序配置。代码部署与账号切换分别验收；配置、必要的 Auth0 安全设置及真实登录验收见 [成员连接方案](docs/member-connections.md) 和 [0.5.30 升级说明](docs/UPGRADE_0.5.30.md)。

0.5.29 将分镜首稿质量与剧情生产约束合入 0.5.28 的 Admin / Producer 权限版本。员工可生产、审计、修正和导出，服务端继续拒绝规则及系统维护。配置与使用见 [账号权限说明](docs/ACCOUNT_ACCESS.md)；发布范围与验证边界见 [0.5.29 升级说明](docs/UPGRADE_0.5.29.md)。

这是一个“技能 + MCP 执行服务”的完整工具型插件。ChatGPT 负责理解、生成与改写内容，MCP 负责项目状态、文件导入、版本化提交、全表审计、修复回归、跨聊天问题台账和人工签核。没有真实工具调用时，插件规则禁止声称“已经执行”。

## 当前状态

- 0.5.29：新增 `preflight_narrative_plan`、`review_narrative_artifact`、`record_narrative_attempt`、`read_narrative_record`；正文前先核对原文、授权和场景计划，首稿及失败记录不可变保存，计划、正文和修订按精确版本分别复核。软件检查、线上部署、真实稿件质量分别验收，不据此承诺首稿 98% 或固定两审清零。

- 0.5.27 防复发候选：新增源文事实/条件编译、动作统一派生状态和正文、独立检查汇总、修改后旧证据失效及现有独审来源义务接入。见 [使用和边界](skills/shortdrama-storyboard/references/production_prevention.md)。源码升级、软件检查、完整稿件正确率及线上部署是不同状态；不据此宣称首稿 95%/98% 或两轮审计必定清零。

- 0.5.27 候选：首次候选留档、分层质量指标、复审归因、修复差异证据及指定分集只读诊断；真实质量验收尚未完成。见 [实施说明](docs/UPGRADE_0.5.27.md)。
- 0.5.26 审计附带本批修正模式建议，按问题复杂度分级；无待修问题写“无需修正”，不改变原审计门禁。见 [实施说明](docs/UPGRADE_0.5.26.md)。
- 0.5.25 母本迁移：明确批准的精确源文件经无损解析、逐场景和全量预检后，生成待独立审计的新绑定版本；不修改历史交接或原件。见 [迁移说明](docs/UPGRADE_0.5.25.md)。
- 0.5.24 合并规则升级：语速配置统一单镜和章节预算，逐发言/阶段校验并行与先后事件；候选5字/秒明确未标定，不等于实际视频验证。原句出现身份与字符范围保护合法拆镜，眼部逐人、重音逐词、清透分项与物理光影连续性按同一注册定义执行。任意认证窗口可连续生产、审计、修复；旧窗口绑定与旧计时只用于明确的历史兼容。
- 本地 MCP：可运行，Streamable HTTP 端点为 `/mcp`
- 工具：Admin 24 个、Producer 21 个，覆盖项目、工单、状态迁移、受控交接恢复、跨批次问题画像、草稿预检、自动批量文件读取、变更、剧本/方案、全量与增量分镜、独立审计、受控局部修复、验证、能力缺口、签核及按需性能诊断
- R10 运行时：`R10.1` 紧凑母版 Schema，工作流保持 `R10-PG1`；底层规则运行时标识为 `V16.3-148-R10-StableRuntime`
- 0.5.17 旧表演文字合同：包含视线起落、换焦、眼睑、眨眼与重音时序检查。0.5.35 经完整重编译验证的生产计划改为核对实际声明事件，不强制静止镜头添加动作，也不强制无须回应的听者反应；真实动作的状态和时序继续校验，表演自然度另行独立审查。0.5.45当前输入统一按自然静止规则检查；显式历史重放才保留旧组件合同。
- 0.5.17 相邻镜头全覆盖证明：N个canonical镜头必须形成精确N−1个有序相邻对，逐对审查人物位置、身体朝向、视线落点、手持道具、门窗、动作进度和光源方向，并保存两端状态哈希、边界类型、解释、结果、100%覆盖率和有序列表SHA-256；独立审计升级为`semantic-audit-v3-adjacent-continuity`，空间层必须按同序提交全部`pair:left->right`，缺失、重复、逆序、乱序、非相邻、哈希漂移或跨场/跨集无解释都拒绝签证
- 0.5.20 叙述性会话场景还原：授权范围内每个叙述单元都必须判定为会话、混合会话或非会话；判断依据是问答、劝决、令应、报听、辩核、证判等信息交换，而不是言语动词关键词。会话场景必须还原对白节拍和听者反应，独立审计逐单元提交`source-unit`证据并逐镜核对实际对白
- 0.5.18 正式母版音色固定模板：不增加顶层字段，原有`音色`按固定顺序写`基础声线、言语行为、当前表达、发声方式（音量、语速与节奏、呼吸、停顿、重音、尾音）、禁止语气`；重音必须逐字来自本镜实际对白，禁止一行自由描述和统一中性尾缀。独立审计“禁项与声音”层必须逐个覆盖所有带对白镜的`field:镜头编号.音色`
- 0.5.16 Library项目快照存储：`migrate_project_state.storage_action`在不增加公共工具数量的前提下，支持签名登记、SHA-256与目录树双校验、临时挂载、导出、提交新Library代次和安全卸载。`SHORTDRAMA_PROJECT_STORAGE_MODE=library`时，完整项目、源文件、分镜、canonical、审计证据与交接恢复原记录只存在于ChatGPT Library或系统临时目录；服务器持久卷仅保留规则运行所需密钥及Library ID、路径、哈希、状态和代次指针
- 0.5.15 恢复后继可安全再签发：已由签名CAS恢复产生的`prepared + PENDING_AUDIT`后继，如果后续部署只让完整依赖指纹失效，可在新登记的精确gap与新恢复工单下再次plan→execute。服务端必须验证立即前序恢复日志、旧/新after-image、恢复工单、生产证书、生产trace、不可变内容指纹和当前验证画像；历史证明后继仍保持`historical_contract_pass + current FAIL`，不得转成当前生产PASS或复用独审结论
- 0.5.14 窗口无角色限制：同一认证用户命名空间中的任意聊天窗口都可直接建立或继续生产、独立审计和修正工单，无需声明窗口角色、寻找旧窗口或执行权限恢复。聊天会话只作为请求来源；服务端仍为每张工单签发不同的authority root、window binding与actor binding，生产预检不能冒充独立审计，修正仍须消费签名修复包并返回原独审authority。待审计、待修正等状态只限制工作流顺序，不限制用户在哪个窗口执行下一动作
- 小说转分镜：完整章节读取→剧情地图→取舍台账→人物状态/认知→对白保护→时长预算→节拍→100%覆盖审计
- 公共场景生态：街道、市集、商街、巷道、城门、码头、公共餐饮等场景强制普通人物密度/角色/错峰动作/空间层次，以及具体近中远环境声、对白避让和声画来源；有因空场除外
- 27 字段 canonical：每镜只保存一次语义事实，模型字段固定为 `镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`。服务端内部 UID/分集/场景身份不属于模型写入字段
- 唯一视频执行真值：`正式分镜母版` 是单镜空白上下文下唯一完整视频执行母版；旧列名`正式分镜母版·字段合并`只读兼容；新写入不再创建独立 Seedance 字段、Seedance 提示词、`batches` 对象或“生成批次”工作表
- 画面与音色分工：人物可见动作、表情、精确对白、对白时点、口型、可见呼吸/停顿、重音期表演、听话反应和镜尾状态写在`画面内容`；完整发声规格只写在`音色`固定子模板。面向 Seedance 2.5 的内部阶段必须连续、无重叠并闭合到本镜时长；数字制作时间码不写进画面正文
- 光照闭合：`光影、光源来源、光源方向、色温倾向` 四项必须同时存在，并接受同场景连续性校验
- 精确 10 列 Excel：`分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`
- 0.5.5 初稿质量门：生成前读取跨批次问题画像，逐场景执行无产物预检，再对完整 canonical 签发全量预检证书；总体正确率与直接可用率均须至少98%，每集也须至少98%（0.5.29 当前口径），新增局部问题率最多2%，硬错、重大问题、已知问题复发和系统性重复均为0
- 0.5.21 文生图与光影决策：把参考提示词划分为“可直接进入静帧／先转为单帧可见语义／禁止进入静帧”三类；光影按时段、空间、天气、真实光源、方向、受光对象、曝光材质、叙事功能和同场连续性闭合。轮廓光、眼中灯火反射、halation与丁达尔光缺物理前提时阻断
- 0.5.21 模板硬门禁：THG-01至THG-17继续强制逐镜`9:16竖屏`、直接对白逐字、叙述性会话场景还原、事实转化对白来源证明、单机位/状态/道具/时长容量闭合、实际眼部事件与可见性约束、N−1相邻逐组证明、音色固定子模板及静帧可见光影证明
- 0.5.23 室内光影判断修正：补齐普通烟雾、英文halation、房间别名和无来源局部烟气的拦截；梦幻感可由色彩、浅景深和神态表达；局部烟气必须同时具备来源、范围、扩散控制与可辨背景。
- 0.5.22 室内外光影分流：普通室内默认空气清澈、背景可辨、闭窗窗纸漫射与前侧/侧面柔和塑形；无剧情依据的雾烟、可见穿插光束、全画面柔焦扩散、无开口强逆光、背景泛白或光线擅改门窗由THG-17阻断。室内剧情烟雾须局部且不遮背景；室外雾气、逆光和体积光只在天气、介质与真实光源闭合时启用
- 0.5.12 旧交接当前报告精确封存修正：只对`GAP-1bb91d83751b422f`登记的精确后六条交接追加独立的`historical_production_preflight_attestation`。不再把真实当前FAIL概括为单一9:16问题，而是逐handoff锁定canonical、完整`current_report_sha256`、镜头/错误/警告计数、错误直方图及当前validator完整指纹；任一字段漂移都在权威写入前失败关闭。冻结历史报告仍须逐字段等于旧签名PASS/0报告；历史证明不声称当前生产预检通过，`HISTCACHE`不得作为当前预检或独立审计缓存复用。补签后仅在`GAP-0e5d48969c4c494e`下按精确顺序统一恢复原11条，旧交接只标记`superseded`，新交接保持`prepared + PENDING_AUDIT`并强制完整当前独立审计
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
- GPT 导出投影：`GPT关键帧生成指令`只在导出时从同一 canonical 确定性生成，不写回 canonical；投影剔除可识别的时间与声音指令。静态字段中的引号必须绑定当下可见的道具文字；无法绑定，或已绑定文字在清洗中丢失时，导出明确拒绝并保留原稿，不再成功返回空栏目。现场对白或历史引文混入静态字段的旧调用也可能因此被拒绝，需要按实际内容调整相应字段；拒绝不代表内容已审查通过。
- 跨聊天：同一认证用户命名空间 + 同一 `project_id` 共用项目中枢和问题台账；所有窗口具有相同的用户编排能力，可直接继续同一窗口无角色限制工单。0.5.13及更早的窗口绑定历史工单仍可读取，并保留旧接续事务作为兼容路线
- 执行架构：生产与独立审计使用不同的服务端 authority root/window binding/actor binding；只有审计失败且用户授权修复时才启用独立 repair authority，修复结果必须返回原审计 authority 复核。这里的“独立”是后台证据身份隔离，不要求用户切换聊天窗口
- 增量提速：已有分镜的同身份、同数量、对白与人物认知不变的局部执行变化使用 `commit_storyboard_delta`，只传受影响的完整 27 字段镜头及边界；服务端校验稳定 UID、CAS、保护范围，合并未变化镜头后仍执行完整 R10.1 全表生产校验并生成独立审计 handoff。任何对白或说话人变化、心理音轨、插删移、拆并、重编号或上游证据失效，都重建完整受影响场景/分集
- 停止后台重复生成：canonical 不持久化正式母版、GPT投影、独立 Seedance 文本或批次；导出器只对最终 canonical 执行一次正式母版和GPT投影，不在生产、审计、修复、缓存预热或后台任务中再次扩写语义
- 真实独立审计：首次或指纹变化后的 `audit_storyboard` 必须按`semantic-audit-v8-adjacent-continuity-structured-voice-conversation-t2i-indoor-lighting`提交绑定精确canonical、镜头数、N−1相邻对数量及有序pair SHA-256的八层语义证据；空间层逐组列出完整有序pair全集，表演层复核眼球/眼睑/眨眼时间轴，禁项与声音层逐镜覆盖所有带对白镜的音色字段，源文对白层按Source Ledger逐单元复核会话分类并逐镜覆盖事实转化对白目标镜，机位可见性层按镜头顺序覆盖全部四项光影字段并核对普通室内清透/门窗状态或有依据的氛围介质。服务端与新鲜机器证明交叉校验，不能由机器零报错自动合成独立语义通过；仅同一v8契约下的精确缓存命中可省略
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
4. 创建连接，确认 Admin 能发现 24 个工具，Producer 能发现 21 个工具。
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

任意已认证窗口都可依次发出以下请求，不需要给窗口分配角色：

1. `继续项目 PROJECT_ID，生成并提交第一集正式分镜。`
2. `继续项目 PROJECT_ID，独立审计待审计版本，只审不改。`
3. 仅在独审失败且你明确授权后：`继续项目 PROJECT_ID，按签名修复包修正并交回原独审复核。`

每个新任务仍先调用 `get_agent_status`。正式生产固定执行：状态检查 → 必要时 `migrate_project_state` → `query_project_issues` → 每场景预检 → 完整 canonical 全量预检 → 携带签名 `draft_preflight_id` 提交 → 由服务端独立审计身份执行 `audit_storyboard`。这些动作可以在同一个窗口完成，也可在不同窗口接力。失败草稿只写问题台账和质量画像，不创建 artifact、版本、XLSX 或 handoff。

0.5.14及以后新工单不再需要`plan_role_continuation/execute_role_continuation`。这两个动作只保留给0.5.13及更早的历史窗口绑定工单；其旧代次、租约、claim与签名记录仍按原兼容契约验证，不被窗口无角色限制版本迁移或改写。上一批待独立审计时仍暂缓下一批生产，但你可在当前任意窗口直接完成该审计；这是质量流程顺序，不是窗口权限。

接续后的领取也不是凭工单名即可继续：普通handoff、repair package和独立复审successor必须由当前活动root/generation/session取得新鲜、未过期的claim；旧代或过期token不得finish或release。普通handoff完成还必须提交服务器存储中真实签名且与该handoff、审计工单和artifact一致的独立审计证书。候选artifact的延迟发布不得覆盖已经被领取或消费的签名handoff。审计工单已`passed`时，只在恰有一个签名`prepared`复审successor的恢复场景允许受限接续，不得借此重开已完成审计或生成第二条复审路线。

窗口无角色限制本身不会补签、supersede或重发失效交接。首次历史补签与精确11条恢复仍按0.5.12契约执行；其签名后继若因后续部署再次失效，0.5.15只允许在新登记的精确gap、新`rule_improvement + execute + explicit_execute`后台production工单和当前全量回归证书下再次恢复。用户可在当前窗口发起全部动作，但服务端不会让production执行身份签发独审结论或消费修复包。

初稿质量等级与最终发布结论分开：初稿达到总体/直接可用≥98%、单集≥96%、新增局部≤2%，且硬错、重大问题、已知复发、系统性重复均为0，才算达到 A 级生产质量；全量预检仍须无未解决问题才签发可提交证书。独立审计不是替生产清理明显错误，而是对生产可能遗漏的语义问题做第二道防线；最终发布要求八层独立审计100%通过、残留0、新增0、阻塞0。

判断是否真的走完优化流程，要同时看到同一 artifact/canonical 绑定的草稿预检证书、生产 `trace_id` 和独立审计 `trace_id`，以及 handoff 与 audit certificate；缺少任一追踪只能记为 `fast_path_incomplete`。只有文字说明而没有这些标识，等于没有执行。

0.5.20 叙述性会话场景还原见 [docs/UPGRADE_0.5.20.md](docs/UPGRADE_0.5.20.md)；0.5.19 原文事实转化对白双轨见 [docs/UPGRADE_0.5.19.md](docs/UPGRADE_0.5.19.md)；0.5.18 正式母版音色固定模板见 [docs/UPGRADE_0.5.18.md](docs/UPGRADE_0.5.18.md)；0.5.17 眼部表演与相邻镜头全覆盖审计见 [docs/UPGRADE_0.5.17.md](docs/UPGRADE_0.5.17.md)；0.5.16 Library项目快照迁移见 [docs/UPGRADE_0.5.16.md](docs/UPGRADE_0.5.16.md)；0.5.15 恢复后继再次签发见 [docs/UPGRADE_0.5.15.md](docs/UPGRADE_0.5.15.md)；0.5.14 窗口无角色限制见 [docs/UPGRADE_0.5.14.md](docs/UPGRADE_0.5.14.md)；0.5.12 对后六条真实当前验证报告的逐handoff精确封存修正见 [docs/UPGRADE_0.5.12.md](docs/UPGRADE_0.5.12.md)；0.5.11 首次引入的受控历史生产预检证明与精确11条恢复见 [docs/UPGRADE_0.5.11.md](docs/UPGRADE_0.5.11.md)；0.5.10 的历史工作簿受控复验与补签见 [docs/UPGRADE_0.5.10.md](docs/UPGRADE_0.5.10.md)；0.5.9 的旧工具参数表受控阶段推断见 [docs/UPGRADE_0.5.9.md](docs/UPGRADE_0.5.9.md)；0.5.8 的历史Hub交接投影严格只读兼容见 [docs/UPGRADE_0.5.8.md](docs/UPGRADE_0.5.8.md)；0.5.7 的旧交接生产预检证据受控补签见 [docs/UPGRADE_0.5.7.md](docs/UPGRADE_0.5.7.md)；0.5.6 首次引入的源文权威、独立复审、台账重放与受控交接恢复见 [docs/UPGRADE_0.5.6.md](docs/UPGRADE_0.5.6.md)；0.5.5 的初稿质量门、项目迁移和发布验收见 [docs/UPGRADE_0.5.5.md](docs/UPGRADE_0.5.5.md)；0.5.4 的集成升级历史见 [docs/UPGRADE_0.5.4.md](docs/UPGRADE_0.5.4.md)。十二项模板硬门禁的原始设计见 [docs/UPGRADE_0.5.1.md](docs/UPGRADE_0.5.1.md)，无API性能诊断见 [docs/UPGRADE_0.5.3.md](docs/UPGRADE_0.5.3.md)。

0.5.22 室内外光影分流见 [docs/UPGRADE_0.5.22.md](docs/UPGRADE_0.5.22.md)；0.5.21 文生图语义与光影决策见 [docs/UPGRADE_0.5.21.md](docs/UPGRADE_0.5.21.md)。

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
- 需要继续生产时，服务端把旧字段确定性折叠到一个新的 R10.1 successor：旧人物调度进入空间/起始状态，旧动作、表演和对白进入`画面内容`；旧发声参数保留到`音色`并标记待按固定子模板修订，标点时点保留在画面内容；四项光照原样迁移。迁移不得凭空补写角色声线或话语意图。
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

0.5.35 整集范围、旧规则保全与入口约束见 `skills/shortdrama-storyboard/references/episode_rule_contract.md`。软件检查不等于首稿98%。
