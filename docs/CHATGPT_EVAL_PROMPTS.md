# ChatGPT 插件验收提示词（0.5.12 / R10.1）

每条在新聊天运行，并记录实际工具、参数、结果 ID、追踪和确认行为。文字声称不算执行证据。

## 应触发

1. `使用分镜项目 Agent 创建《夜雨来信》，告诉我还缺哪些输入。`
2. `继续项目 PROJECT_ID，导入我上传的剧本，先给第一集改写方案，不修改原稿。`
3. `按刚才批准的方案改写第一集，并保存新版本，不要覆盖旧稿。`
4. `完整读取我上传的小说第一章，先建立剧情地图、取舍台账、人物认知和对白保护，再生成正式分镜；禁止逐段直接转镜头。`
5. `在生产窗口生成第一集R10.1正式分镜。每镜只提交27字段canonical，不生成独立Seedance提示词或生成批次；完成生产预检后交给独立审计。`
6. `在新的独立审计窗口读取生产交接包，只读审计这个精确canonical，不重新生成镜头、母版、GPT字段或Excel。`
7. `上一轮独立审计失败。我明确授权在第三个独立修复窗口消费签名修复包并修复全部问题；完成后交回原审计窗口，不要在修复窗口签发最终通过。`
8. `删除镜12、合并镜13和镜14，并同步所有受影响字段；保留第二集不动。不要把结构变化伪装成delta。`
9. `我批准归档，签核人写“项目负责人”。`
10. `生成古代闹市单镜：正式母版必须写普通人物密度、地点角色、错峰动作、空间层次、近中远环境音和对白避让。`
11. `审计这个公共街道分镜，其中环境音只写“街道嘈杂声”，不要放过这个问题。`
12. `继续这个项目。原文和规则没有变化，请使用批量范围与ETag缓存，不要重复逐页读取；先验证生产handoff，再独立审计。`
13. `我明确授权在修复窗口只修复审计证书确认的镜S001“光源方向”，其他字段不动；完成全表生产校验后交回原审计窗口。`
14. `不做独立审计，直接把刚生成的分镜签核归档。`
15. `继续项目 PROJECT_ID，只修改同一镜头身份的5个镜头画面内容、机位和光影；原文、认知、场景、编号和镜头数量不变。只提交变化的27字段镜头，服务端合并后全表验证。`
16. `继续项目 PROJECT_ID，把镜S010拆成两镜并重排后续编号。只重建完整受影响场景，不使用delta。`
17. `这是该版本第一次独立审计。即使机器校验零报错，也必须对精确canonical完成八层语义复核并提交每层证据；缺少证据应阻断。`
18. `生成一个3.6秒单镜：画面内容可按0.0—0.8秒、0.8—2.9秒、2.9—3.6秒三段连续描述动作、对白和镜尾状态，音色单独填写。`
19. `把旧0.4.0分镜导入新项目继续制作；保留历史artifact不变，只创建一次R10.1 successor，禁止重新导出旧字段和Seedance批次。`
20. `生成单镜时逐镜写入9:16竖屏，并把GPT关键帧限制为静态白名单投影；完成后只标记生产预检通过、待独立审计。`
21. `导入一段含“江氏忽然说道”、角色冒号换行、同行两名角色和跨句念信的源文；只依据服务器登记的源文件解析逐条可听对白，并交叉核对payload SHA、Hub authority entry与artifact metadata中的source role，不允许候选分镜或source_lock反向提供说话人。遇到歧义整批阻断且不要创建artifact。`
22. `对上一轮FAIL执行强制复审；不要重开已消费handoff。先消费最新签名修复包生成新artifact/canonical，再由原审计窗口使用新successor handoff复审。`
23. `模拟独立复审event已经落盘但certificate尚未写入后重启；用完全相同semantic重试应只补同一certificate，不增加轮次。随后改一个issue或指纹重试必须零变异拒绝。`
24. `上一轮独立审计已PASS；对同一artifact执行强制复审，必须使用新successor handoff以及新的route/event trace。若新证据发现阻断问题，新FAIL必须撤销旧PASS的latest与signoff资格，同时保留旧certificate为不可变历史。`
25. `把已完成请求的semantic_audit.canonical_sha256仅改成等价的大写十六进制后重放；必须返回同一签名receipt，且successor、round、event和certificate计数均不变。再改任一普通证据ID的大小写必须拒绝。`
26. `保留真实event，修改certificate中的一个绑定字段后用有效服务HMAC重新签名；系统仍须按完整确定性CERT绑定零权威/投影变异拒绝，不能把“签名有效”当成“内容正确”。`
27. `模拟已完成FAIL只缺repair package或Hub投影并并发启动下一successor；投影恢复必须在同一storage lock内重验最新lineage、certificate HMAC与fenced claim CAS。若新authority先完成，旧重放不得写投影或返回旧FAIL。`
28. `确认插件0.5.12仍精确暴露19个工具且包含recover_storyboard_handoff。为缺少旧生产预检字段的prepared production_to_audit交接在当前production窗口建立专用rule_improvement + execute + explicit_execute工单，targets精确包含gap、对应handoff IDs和controlled_phase:preflight_evidence_backfill；模拟宿主缓存旧schema而省略phase，先预演，再用相同列表、plan_sha256和REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE执行。普通分支只追加当前零问题production_preflight；精确历史分支只追加独立历史证明，不改旧交接或旧证书。`
29. `补签完成后，另建一张不同的当前production窗口rule_improvement + execute + explicit_execute工单，targets精确包含gap、完整恢复handoff IDs和controlled_phase:handoff_recovery。以phase=handoff_recovery先预演，再用完全相同列表、plan_sha256和SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT执行。旧交接只标记superseded，新交接必须是prepared + PENDING_AUDIT。`
30. `用两组历史Hub交接投影分别验收补签与恢复：一组精确去除record_id、project_id、integrity_hmac，另一组精确保留完整签名记录。两组均应通过；执行后未来Hub successor投影统一不含签名信封字段，历史行不得因兼容读取被迁移或改写。`
31. `用旧交接所绑定的0.5.3合法基线XLSX预演补签：侧车是精确8列信封且veryHidden，可见表是短10列表头，每个正式/GPT单元格由冻结0.5.3投影器重放完全一致。返回的受控复验证明必须封存选定投影器、冻结来源、raw XLSX/canonical、旧handoff、旧生产证书和原生产trace哈希；普通extract_xlsx对同一历史特例仍应拒绝。`
32. `用带精确shortdrama-projector/0.5.4投影契约和审计签名的旧XLSX预演补签，受控路由应通过并标明strict_projection_contract；改一个可见单元格、投影签名、审计签名或侧车状态后，计划必须零写入失败。`
33. `为GAP-1bb91d83751b422f按UPGRADE_0.5.12.md中的精确顺序预演后六条补签。逐条核对冻结报告与原签名生产报告完全相等且PASS/0；真实当前报告须精确匹配该handoff独有的canonical SHA、完整current-report SHA、shot/error/warning计数、完整error-code histogram，以及当前validator composite和完整SHA，不能用error类别集合或另一条画像代替。执行后必须得到historical_production_preflight_attestation + historical_contract_pass、完整双报告、HISTCACHE和四个不可复用/强制复审标志，绝不能声称current pass。`
34. `在同一后六条历史补签样本中，分别篡改canonical、完整current-report SHA、shot/error/warning任一计数、histogram任一键值、当前validator composite/完整SHA、handoff规则画像，或篡改冻结validator/rules/manifest/error catalog；逐条交叉复用另一handoff的完整画像。plan或execute必须在任何PFE、证书、trace、handoff或恢复记录权威写入前失败关闭。`
35. `后六条补签完成后，为GAP-0e5d48969c4c494e按文档精确顺序统一恢复原11条；先plan，再用相同列表、plan_sha256和SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT执行并重放。旧handoff只标记superseded，新handoff均为prepared + PENDING_AUDIT；后六条的HISTCACHE与历史结论不得命中独立审计，必须按当前validator完成完整独审。`

## 应阻断或纠正

1. `给新分镜增加独立Seedance 2.5执行列和59个生成批次。`
2. `把人物调度、动作与表演、台词、发声方式和标点语气恢复成独立字段。`
3. `把同一3.6秒镜头写成4段时间，或者让时间段重叠、留空档、超过3.6秒。`
4. `光影保留，光源来源、光源方向和色温倾向留空。`
5. `不使用插件，只解释什么是分镜。`
6. `分析这段剧情，但不要创建项目或文件。`
7. `替我自动签字归档。`
8. `读取我另一个未提供project_id且插件里没有记录的聊天全文。`
9. `关键帧里写硬切、推镜、跟镜、几秒后和承接上一镜；生成限制写禁止BGM。`
10. `独立审计尚未运行，先把审计页问题数、残留数、新增数和阻塞数都填0并标PASS。`
11. `上一轮FAIL后不改文件，只把semantic_audit改成PASS并换一个大小写不同的指纹。`
12. `使用根审计FAIL产生的旧修复包，忽略后继复审刚产生的新FAIL修复包。`
13. `伪造一个certificate-only持久化状态并要求系统自动补event、增加轮次或更新latest。`
14. `在旧台账里只写authority_verification=true作为伪checkpoint，再追加legacy-* history/location、整条假issue或只改counter；即使JSON Schema仍合法也继续加载并签核。`
15. `在独立审计窗口补签旧交接生产预检证据，或者把补签写回旧handoff/旧certificate并顺便标记审计PASS。`
16. `补签与恢复复用同一张工单，省略controlled_phase token，或者在targets里多放未请求handoff；即使只是action=plan也继续返回计划。`
17. `把历史Hub交接投影做成混合形态：只保留部分签名信封、增加未知字段、删改业务字段，或修改record_id/project_id/integrity_hmac之一；即使其余字段相同也继续补签或恢复。`
18. `省略phase，但在工单targets中不放controlled_phase、重复同一token、同时放两个合法token、混入unknown token，或者把token改大小写/加空格；仍自动默认handoff_recovery并继续计划或执行。`
19. `把0.5.3基线工作簿中的正式母版/GPT单元格换成当前投影行、修改短表头、让canonical侧车只是hidden或加删一个侧车字段，再要求用“历史兼容”继续补签。`
20. `在0.5.4签名工作簿中改一个投影或审计SHA，或让原XLSX/canonical与旧handoff、旧生产证书或原生产trace不再绑定；仍要求受控路由生成plan。`
21. `把后六条历史证明写成production_preflight_pass，删除当前FAIL报告，把HISTCACHE当作独立审计cache hit，或用0.5.11已勘误的aspect-only集合判断代替0.5.12逐handoff完整封存画像。`

## 通过标准

### 数据契约与Excel

- 插件版本为`0.5.12`，精确工具集仍为19个且包含`recover_storyboard_handoff`；该工具的`phase`非必填且枚举必须同时包含`handoff_recovery`与`preflight_evidence_backfill`，不得保留默认恢复阶段。省略或null时只可从当前会话绑定工单的唯一精确controlled_phase令牌推断。分镜Schema为`R10.1`，工作流为`R10-PG1`。
- 新 canonical 的模型镜头契约严格为27字段：`镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`。内部UID和身份元数据不属于模型写入字段。
- Excel正式母版严格为10列：`分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`。旧列名`正式分镜母版·字段合并`只允许只读导入，不得回写。
- `正式分镜母版`是唯一完整视频执行真值，必须能在空白上下文中独立执行；不得出现独立Seedance列、独立执行提示词、顶层`batches`、镜内`生成批次`或“生成批次”工作表。
- canonical不得持久化`正式分镜母版`、旧列名或`GPT关键帧生成指令`；正式母版与GPT关键帧只在Excel导出时从同一canonical确定性投影一次，并在保存后回读逐格比对。
- `GPT关键帧生成指令`首部明确写`画幅：9:16竖屏`，只保留白名单内的静态单帧信息；数字时间段、对白、口型、声音、运镜过程、转场和“几秒后/随后”等时间过程词必须为零命中。

### 单镜内容

- `空间站位描述`与`起始状态`均为独立必填字段。
- 每镜`生成限制`必须逐字含`9:16竖屏`；单镜执行字段不得出现`BGM`字面量，负面限制统一写`禁止额外配乐`。
- `画面内容`合并可见动作、表演、表情、精确对白、六维发声细节、标点效果、听话反应、呼吸和镜尾状态；不得再生成`人物调度、动作与表演、台词、发声方式、标点语气`独立字段。
- `音色`保持独立且不得被删除。带对白镜的口型、语气和停顿信息在`画面内容`闭合，音色在独立字段闭合。
- 面向Seedance 2.5的新写入中，`画面内容`必须恰好使用3段标准时间描写，从0开始、顺序连续、互不重叠，最后一段终点等于本镜时长；旧自然阶段只允许只读迁移。
- `光影、光源来源、光源方向、色温倾向`四项均必填；任一缺失或同场景不连续都必须报告问题。

### 流程与质量

- 新聊天先调用`get_agent_status`；写操作先有工单，文件任务返回`artifact_id`。
- `begin_project_task`必须在不增加公共工具数量的前提下支持`action=create|plan_role_continuation|execute_role_continuation`，并为接续动作接受`resume_work_order_id`、`expected_plan_sha256`与`confirmation`。接续仅支持`storyboard_create、storyboard_regenerate、storyboard_change、storyboard_audit、storyboard_repair`及其精确角色矩阵；`rule_improvement`、版本管理或其他production操作必须拒绝。plan返回`status=planned`以及`action、resume_work_order_id、plan_sha256、confirmation_required、authority_root_id、role、operation、mode、authorization、authority_contract_sha256、pre_state_sha256、current_generation、next_generation、new_binding_status`；它必须零写入，本版不得虚构`expires_at`或计划TTL。
- plan哈希绑定精确工单契约/快照、当前authority head、目标可信cloud session和运行时证书；execute只接受这些绑定未漂移的同一目标session、同一工单、同一`plan_sha256`和精确确认`ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION`，返回`status=continued`以及`continuation_id、authority_epoch_id、generation、previous_generation、previous_status=revoked、plan_sha256、idempotent_replay、session_binding_status`。同参重放只有在前一独占operation已经结束、该continuation仍是活动head，或正在补齐其签名记录到Hub的半写时才返回同一身份；后续代已推进时必须返回superseded。不得把并发同root请求当成幂等重放，也不得把任意已完成claim、handoff或certificate变化笼统写成计划哈希输入。
- 活动operation lease以及当前工单持有的未过期handoff、repair-package或draft-preflight commit claim必须在plan和execute时零写拒绝；每个logical authority root的operation lease严格独占，同root第二个请求即使同工单、同session和同payload也返回`authority_operation_in_progress`且不能共享holder或join，不同authority root可并行；租约释放/过期后可轮换。repair package消费使用token、递增claim generation和bounded lease；0.5.10无完整lease字段的`consuming`包失败关闭且不自动迁移。已消费package补偿回`issued`只允许精确原claim token/generation与固定事务身份、base successor transaction为`aborted`、签名handoff及Hub投影均为`rejected + aborted`且零handoff claim/消费快照/审计证书或其他独审下游；prepared/committed base、不同claim、live/published handoff或审计下游必须阻止释放。
- 普通handoff、repair package和独立复审successor的领取必须绑定领取工单当前活动authority root/generation/server session、fresh token、递增claim generation与有效bounded lease；finish/release在项目锁内复核全部绑定。旧代次、被新claim替换或过期的token、过期lease、旧session重放必须零写失败，租约过期后只能由当前活动epoch使用新token和下一claim generation重领。普通handoff完成还必须读取真实certificate store并核对签名`independent_audit`证书的certificate/handoff/work order/artifact绑定，调用方标签、production preflight、未签名或错绑证书均不能完成。
- candidate artifact延迟发布不得用旧`prepared`候选覆盖已`consuming/consumed`的签名handoff；只能推进candidate事务并逐字保留签名handoff与Hub投影中的claim/history/token/generation和audit certificate。`passed`审计工单通常不可接续，只有同一审计authority恰有一个服务签名`prepared independent_audit_successor`时，才可受限恢复该既有路线；零条、多条、未签名、非prepared或错绑路线均零写拒绝，且不能重开PASS或创建第二条successor。
- 同一工单接续保持稳定`authority_root_id`，追加新签名continuation并把唯一活动head推进到单调新epoch/generation；旧代由head语义逻辑撤销，历史签名记录不可回写`revoked`。所有后续authority写入在下一持久化边界复核active generation；只有新签发handoff、repair package及当前Schema明确声明这些字段的其他记录持久化root/epoch/generation，消费claim另用token/generation/lease契约，不要求certificate/trace补写这些字段。跨角色、跨项目、跨工单或契约漂移必须失败关闭；repair回审仍属于原audit authority root/cycle的当前活动代，`PENDING_AUDIT`不得因接续而解除production阻断。
- 小说章节生成前完成`source_ingestion`证据，核心事件、因果、人物变化、保护对白和源文处理覆盖率均为100%。
- 源文对白只由服务器登记的源文件与经payload SHA、Hub authority entry、artifact metadata source role交叉核对的parser v6闭集语法逐occurrence解析；候选inventory、`source_lock`、台账、认知和锚点不能提供解析权威。authority manifest SHA直接绑定source fingerprint、role、occurrences与grammar SHA，grammar SHA再间接绑定implementation source；歧义必须失败关闭且零正式产物。
- 公共场景写明密度、地点角色、错峰动作、空间层次、具体近中远声源和对白避让；只写“人群/环境声”必须阻断。
- 同身份同数量局部修改可用`commit_storyboard_delta`；插入、删除、移动、拆分、合并、重编号及原文、认知、边界或场景拓扑变化必须拒绝delta并完整重建受影响场景或分集。
- 生产窗口普通内容路线只签发`production_preflight`；0.5.12精确历史补签路线只能签发非当前结论的`historical_production_preflight_attestation`。独立审计窗口只读复核。只有审计失败、已签发精确修复包且用户明确授权时才启用第三个repair窗口，修复结果必须返回原审计窗口。
- 补签与恢复必须使用两张不同的、绑定当前production窗口/session的`rule_improvement + execute + explicit_execute`工单。补签targets精确等于gap、对应handoff IDs和`controlled_phase:preflight_evidence_backfill`；恢复targets精确等于gap、对应handoff IDs和`controlled_phase:handoff_recovery`。缺项、额外项、错误token/窗口或复用同一工单时，plan必须返回`scope_mismatch`且零写入。
- 旧交接生产预检证据补签必须先`phase=preflight_evidence_backfill, action=plan`，再以相同handoff列表和`plan_sha256`配合`REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE`执行。普通分支只在当前validator零问题时追加`production_preflight`；`GAP-EXAMPLE`精确有序后六条则要求冻结报告与原签名PASS/0报告完全相等，且每条真实当前FAIL精确匹配自身封存的canonical SHA、完整current-report SHA、shot/error/warning计数、完整error-code histogram和当前validator composite/完整SHA，并只追加`historical_production_preflight_attestation`。该证明保留完整双报告、使用`HISTCACHE`、不声称current pass且不可作为当前预检或独审缓存复用；旧handoff/证书、分镜、canonical、XLSX、生产历史和审计历史均不修改。
- 受控工作簿复验只接受精确签名0.5.4/当前投影，或“8列canonical侧车 + 短10列表头 + 冻结0.5.3投影器逐格一致”的合法基线。证明绑定raw XLSX/canonical、旧handoff、旧生产证书、原生产trace、投影器和冻结来源；一般`extract_xlsx`不接受该历史特例。
- 证据齐备后的旧交接恢复使用另一张工单执行`phase=handoff_recovery`；`GAP-EXAMPLE`只接受文档精确有序原11条，同样先plan再以完全相同列表/哈希和`SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`执行。每条独立原子恢复，旧handoff仅变为`superseded`且不删除，新handoff为`prepared + PENDING_AUDIT`；后六条必须由当前validator和完整八层语义独审重新得出结论。
- 历史Hub交接投影只接受精确去签名信封投影或精确完整签名记录；任何混合、额外、缺失或变更字段必须在写入前失败关闭。兼容读取不得迁移或改写历史，未来生产Hub写入统一为去信封投影。
- 生产导出的审计页固定为`PENDING_AUDIT／待独立审计／未签发结论`，不得预填PASS、零问题或审计计数，且生产XLSX永不就地回填；独立审计签发后必须创建新的不可变签名overlay successor，只在新工件中写入初始、残留、新增、阻塞、最终状态、trace与文件指纹。
- 首次或安全指纹变化后的审计提交绑定精确canonical与镜头数的八层`semantic_audit`；机器零问题不能自动合成独立语义通过。
- 每轮显式复审使用新server-minted successor handoff与route/event trace；只有最新完成FAIL血缘的修复包可消费。同字节FAIL不得换semantic翻为PASS，event-only半写只允许完全相同semantic确定性补证书，certificate-only状态必须零权威变异拒绝。仅 caller semantic 的合法 canonical SHA 做小写归一，大小写重放返回同一receipt且不烧轮；CERT必须完整确定性匹配，validly re-signed内容失配仍零变异拒绝；缺repair/Hub投影只能在同一storage lock内通过latest-lineage、精确CERT HMAC与fenced claim CAS后恢复。
- 旧台账的replay checkpoint必须由独立HMAC签名且CAS提交的migration journal授权并绑定project/migration身份、before/after ledger SHA与完全相同checkpoint；伪marker、检查点后`legacy-*`后缀、整条假issue或counter-only篡改，即使Schema合法也必须零变异拒绝。
- 局部修复必须绑定基础canonical哈希、独立审计证书、问题码和影响范围；结构、边界、认知、原文、编号或时长变化必须拒绝patch。
- 没有精确版本的独立审计通过证书时，`signoff_task`必须阻断归档。
- 优化流程完成证据同时包含同一版本的production trace、independent-audit trace、handoff和certificate；缺少任一项返回`fast_path_incomplete`。

### 无重复生成与Legacy

- 新生产、delta、审计、修复、缓存预热和后台任务均不得生成或保存独立Seedance文本或批次；追踪应证明`legacy_generation_calls=0`、`seedance_batches_generated=0`、`model_authored_derived_chars=0`。
- 0.3.x/0.4.x仅允许只读导入或审计；历史原始文件和canonical哈希保持不变。继续生产时创建新的R10.1 successor，并把旧动作、表演、对白、发声和标点事实确定性合并一次。
- Legacy successor不得重新输出已删除字段、Seedance列或批次。无法可靠得到`起始状态`时必须阻断，不得编造。
- 性能说明只能声称架构已消除重复模型写作、批次包装和后台扩写；没有同源、同范围、同模型和同输出契约的A/B回放，不得声称具体分钟数或百分比。
