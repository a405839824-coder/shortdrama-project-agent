# 0.5.6：源文权威、复审后继与受控恢复

0.5.6 为完整指纹失效、但历史分镜与生产预检仍可验证的
`prepared production_to_audit` 交接增加 `recover_storyboard_handoff`。
它只恢复路由证据，不修改故事内容，也不替代独立审计。本版本同时
加入源文对白闭集权威、独立复审 successor、最新修复包血缘和旧台账
签名重放检查点；这些门禁共同阻止候选自证、失败证据换标签翻绿与
持久化半写导致的双权威。

## 源文对白闭集权威

- `source-dialogue-authority-v6` 只读取服务器登记的源文件，并交叉核对
  payload SHA、Hub authority entry 与 artifact metadata 中的 novel/script 角色；候选分镜 inventory、`source_lock`、台账、认知矩阵、
  人物锚点和候选说话人均为待审声明，不能反向授权解析器。
- `source-dialogue-closed-grammar-v6.3` 逐 occurrence 提取精确文本、标点、
  唯一可确定说话人、源文偏移和可听/非对白分类；覆盖动作状语发声提示、
  角色冒号/换行、同行多角色、引号/无引号对白与跨句朗读。
- 屏幕、UI、字幕、备注、音效、画面、信件等可见文字默认不是对白；只有
  源文明示朗读时才计入可听 occurrence。
- 未知动作残留、冲突分隔符/引号、读者或被读对象不唯一、或来源格式不在
  闭集内时，整次预检失败关闭。失败只留诊断/学习证据，不创建 artifact、
  版本、XLSX、投影、successor 或 handoff。
- occurrence 偏移必须指向剥离外围引号/发声包装后的首个正文字符；cue 消费后
  仍须按源偏移解析同文剩余 role/visual marker。marker 与前文无合法边界、或
  候选姓名内含 stage/visual 结构词族时不得猜测角色，必须失败关闭。
- authority manifest SHA 在 preflight report 与签名 draft-preflight record 中
  直接绑定源文指纹、角色、完整 occurrences 与 grammar SHA；artifact metadata
  只通过 `draft_preflight_id` 引用该签名记录。grammar SHA 再间接绑定
  implementation source SHA，因此实现源码变化会沿权威链改变指纹。

## 权限与双阶段确认

- 只接受服务端绑定的 `production` authority。
- 工单必须是 `rule_improvement` 或 `version_management`、
  `execute + explicit_execute`，并精确包含能力缺口和全部 handoff ID。
- `action=plan` 无写入，返回逐条 before 身份、当前完整指纹摘要、
  新 ID、`plan_sha256` 和固定确认字面量。
- `action=execute` 必须回传同一个 `plan_sha256` 和
  `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`；任一输入漂移即失败关闭。
- 运行时、规则、schema、脚本、依赖或服务代码变化后，必须先通过
  `get_agent_status(full_self_test=true)` 签发当前全量回归证书。

## 单条原子恢复

每个 handoff 独立建立签名 `handoff_recoveries` 日志，并记录旧记录、
新记录和 Hub 投影的 before/after SHA-256。事务按以下安全顺序写入：

1. 持久化 `prepared` 恢复日志；
2. 将旧 handoff 标记为 `superseded`，使其立即不可被审计领取；
3. 写入按当前完整指纹和新生产预检证书绑定的新 handoff；
4. 更新 Hub 投影并校验全部 after 哈希；
5. 将恢复日志标记为 `committed`。

进程在任一步中断时，旧 handoff 会保持不可消费；相同请求只补全签名
日志指定的同一后继，不会生成第二个 handoff。CAS 外的修改不会被覆盖。

## 不可越过的审计边界

- 旧 handoff、原分镜、canonical、XLSX、生产证书和全部历史记录均保留。
- 新证书仍是 `production_preflight`，不得出现独立审计 verdict。
- 新 handoff 固定为 `prepared + PENDING_AUDIT`，缓存状态为 `miss`；
  任何旧独立审计结论都不能跨新的 handoff/scope 身份复用。
- 独立审计必须在不同 authority/window/session 下领取新 handoff，首次审计
  仍须提交完整八层语义证据。

## 独立复审 successor 与最新修复包

- 显式复审不重开或改写已消费的生产 handoff。服务端为下一连续轮签发
  一张新的、一次性 successor handoff；它绑定不可变 root/parent 血缘、
  原审计工单/会话/周期、完整指纹，以及互不重复的 route/event trace。
- 每张现代完成证书必须用 `event_sha256` 绑定精确持久化 event；successor
  还必须在签名 lineage 中绑定直接父证书 HMAC 与父 event SHA，storage 在项目锁
  内复验。缺 `event_sha256` 的旧证书不能普通重放或签核，只能在显式强制复审中
  作为经确定性复验的历史 parent；新 successor 完成后才可作为非授权祖先保留。
- 每个阻断问题必须解析到至少一个 canonical 镜头。显式 `shot_ids` 为权威；
  同时提供 location 时必须解析到相同集合。空目标、未知 ID、越界索引、
  别名重复或冲突定位在任何 event/certificate/package/handoff 写入前拒绝。
- FAIL 只为当前最新完成血缘生成确定性签名修复包和 `audit_to_repair` 路由。
  后继轮再次 FAIL 后，旧 root/前轮修复包即使签名仍有效也属于 superseded，
  不得再启动 repair。可修复的 source handoff 仅限
  `production_to_audit / repair_to_audit / audit_intake`；其中 `audit_intake`
  没有伪造的 producer telemetry，其 root trace 必须精确绑定创建 intake 的
  `independent_audit` trace，审计完成时再写入已验证的 canonical CAS 坐标。
- FAIL 不能靠同一工件换一份 clean 语义矩阵变成 PASS。合法闭环是：最新
  修复包 → 新修复 artifact/canonical → 新 file/source_version/artifact/canonical/
  handoff/trace 绑定 → 返回原审计 authority 复审。
- 修复包持久状态只允许 `issued / consuming / consumed`。完成态重放只接受
  “零下游证据的 issued”或“repair 工单、successor artifact/canonical、base CAS、
  metadata、`repair_to_audit` handoff 与 Hub 全部闭合的 consumed”；`consuming`、
  伪终态和“已有下游证据却删除包”均失败关闭，不得复活为 issued。
- lifecycle Schema 将任一 successor 专属标记视为完整权威集合的触发器，强制
  root/parent、轮次、route/event/production trace、父证书 HMAC/event SHA、原审计
  工单/窗口/actor/session、server-minted 与 lineage SHA 全部存在，并区分
  `prepared / consuming / consumed` 的 claim/finalization 形状。运行时全量测试证书
  只存为独立签名 certificate record，不在 Hub 维护第二份投影；旧 Hub 若残留该
  重复投影会进入控制态迁移，迁移只删除副本并保持签名证书记录原样。
- Schema 负责上述 successor 字段集与状态形状；运行时和存储另行强制 route/event
  trace 互不相等且全局新鲜，不把标准 JSON Schema 无法表达的跨字段比较冒充为
  Schema-only 保证。`consumed` successor 的 `audit_certificate_id` 与
  `latest_audit_certificate_id` 必须指向同一证书，顶层完成字段与嵌套
  `consumption_claim` 必须是同一份 claim/租约快照；运行时解析和存储提交均独立复核。
- 任一签名迁移 journal 处于 `prepared / replaying / rolling_back` 时，都会全项目
  封住普通写入；只有同一 `migration_id` 的精确续跑或回滚可取得窄化写许可。
  显式 ID 不得回退选择同工单的另一份 journal；execute 调用者的 expected version
  必须等于签名 `from_version`，rollback 调用者必须等于签名 `target_version`。
- 签名 WAL 必须先于任一 Hub/Ledger after-image。首次提交和 `rolled_back` 重执行都
  在项目锁内复验当前 PASS 运行时证书；后者先持久化签名 `replaying` 意图，再替换
  状态文件。`prepared / replaying` 可按同一 ID、同一 before/after CAS 显式回滚为
  终态 `rolled_back`，避免规则或运行时升级后只能人工改文件解锁。
- 同一 runtime key 的全量测试证书由项目锁内单调 `certificate_sequence` 建立签名
  全序；同秒 PASS/FAIL 不得再由文件名或秒级时间戳决定。旧证书仅在不存在序号证书
  时按唯一最新时间兼容读取，同秒并列必须失败关闭。
- prepared/replaying/rolled_back 的签名 before/after 快照必须在任何写盘之前按当前
  校验器、Schema、checkpoint 与证书重新验证。`rolled_back` 仅在 before CAS、after
  内容、changed paths、规则和校验器全部等于签名 journal 时允许跨时间精确重执行；
  同一 migration key 不得换 ID 再提交。陈旧 journal 保留为终态历史，使用新的授权
  迁移工单生成新 key；任何拒绝都不得改变 Hub、Ledger 或 journal。
- successor 继承根交接的合法路线与来源角色：`production_to_audit/production`、
  `repair_to_audit/repair` 或 `audit_intake/external_input`，目标仍为
  `independent_audit`，不得把修复或外部输入血缘改写成生产血缘。

## 硬崩溃与幂等恢复

- successor 的签名记录、Hub 投影、claim、ledger event、certificate、overlay、
  telemetry 和 metadata 均按服务器派生身份恢复；普通重试不得制造新轮次。
- 若 event 已落而 certificate 未落，只有重交的 normalized semantic 与已落
  event 的 report、handoff、trace、cache、全部指纹、结果、问题和摘要逐项
  相同时，服务端才确定性补回唯一 certificate。
- normalized semantic 只把 caller `semantic_audit.canonical_sha256` 在通过
  64 位十六进制校验后转为小写；其他 semantic、证据和身份字段保持精确。
  因而同一 canonical SHA 的大小写重放返回同一签名 receipt，不新增 successor、
  round、event 或 certificate。
- 已有 certificate 必须与 event 及全部权威字段确定性重建出的完整内容精确
  一致，不能只验证 HMAC。即使攻击者用有效服务 HMAC 重新签名，任何内容失配
  仍零权威/投影变异拒绝；certificate-only、不同 semantic、伪造 event、活跃
  租约、过期但身份不一致或 Hub/签名副本漂移同样失败关闭。
- 已完成请求依靠签名 certificate/receipt 只读幂等重放，同一请求不增加 round、
  event、certificate、handoff、telemetry、metadata 或 overlay。若仅缺 repair package
  或 Hub 投影，恢复必须在同一 storage lock 内重新验证最新 lineage、精确 certificate
  HMAC，并以 fenced single-finisher claim 做 CAS；并发期间 authority 已推进时，旧重放
  既不得修投影，也不得返回旧结论。
- 所有项目级签名记录必须满足路径名、请求 ID、`record_id` 与该类型主 ID 精确
  相等；telemetry 的 `trace_id/telemetry_id` 也不得分叉。有效 HMAC 不能授权改名、
  复制到另一条路径或重签错误身份。状态查询遇损坏 runtime certificate 时只返回
  `action_required/self_test_only`，执行入口仍失败关闭且不掩盖其他证书的原始完整性错误。
- full-repair 只有在精确记录确实不存在时才映射为 missing；repair package、certificate
  或 handoff 已存在但 HMAC/身份/路径损坏时必须保留原始完整性错误，且拒绝路径对项目
  文件树零变异。

## 旧台账签名重放检查点

- 带历史 event 的迁移由显式 migration ID 建立 `replay_checkpoint`，密封旧
  ledger canonical SHA、event/history/location 前缀、issue 基线、scope alias
  与轮次边界。
- checkpoint 内的 `authority_verification` 只是要求标志，不是授权。普通加载、
  ingest、reopen 和 signoff 必须到独立签名 migration journal 验证 HMAC、
  before/after CAS、project/migration 身份和完全相同的 checkpoint。
- 检查点后的 issue/history/location/counter 必须由新增 event 确定性重放。
  追加 `legacy-*` 行、整条假 issue、只改计数或改密封身份，即使 JSON Schema
  仍合法也会拒绝且账本零变异。

## 发布门禁

部署前必须在同一冻结 Git 树和同一文件哈希上，通过源文权威、复审/修复包、
台账重放、服务安全/事务/迁移、R10 Golden、R10-SPC-1、公共场景、Agent、
stdio 和 HTTP 全部正式入口，并连续完成至少三轮完整 `scripts/run_tests.sh`。
任何代码、规则、Schema、Skill、文档或测试改动都会使既有轮次作废并从零重跑；
实际项目数与耗时写入本次发布报告，不沿用旧版本数字。

部署后重新运行 `get_agent_status(full_self_test=true)`，并仅在返回的运行时
证书与线上 0.5.6 服务代码指纹一致后执行真实恢复。
