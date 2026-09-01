# 0.5.11：旧交接生产预检证据受控补签

0.5.11 在不增加公共工具的前提下，为一组精确登记的旧交接增加受控历史生产预检证明。公共工具仍精确为 19 个，能力只存在于 `recover_storyboard_handoff` 的既有补签/恢复内部路由；一般导入、`extract_xlsx`、生产提交和独立审计规则均不放宽。

> 勘误（0.5.12）：本页记录 0.5.11 当时的设计与发布事实；其中把后六条真实当前验证报告概括为“仅纵横比措辞漂移”的资格判断不成立，不能作为现行执行依据。现行路线改为逐 handoff 精确锁定 canonical、完整当前报告 SHA、镜头/错误/警告计数、错误直方图和当前 validator 指纹，见 [UPGRADE_0.5.12.md](UPGRADE_0.5.12.md)。0.5.11 的冻结报告等于旧签名 PASS 报告、`HISTCACHE` 隔离、不得声称当前 PASS、必须完整当前独审及不可改写历史等安全边界仍保持原义。

## 双证据，不是当前通过

后六条旧交接只能在冻结来源、规则/validator 指纹、原工作簿、canonical、旧 handoff、旧生产证书和原生产 trace 全部闭合时补签。每条新证据必须同时保存：

- 冻结历史合同的精确重放报告：`historical_contract_pass`，问题数为 0，且报告必须与原签名生产证书中的报告完全相同；
- 当前 validator 的完整失败报告：`current_validation_status=fail_current`，问题数至少为 1，并保留报告哈希及错误直方图。该 FAIL 只允许已封存的 v0.5.3 后纵横比措辞漂移：每条问题均须为 `E-I2V-INDEP-002`、`THG-01`、`severity=fail`、`blocking=true`，位置匹配 `shots[n].生成限制`，evidence 精确为 `逐字短语'9:16竖屏'应恰好出现1次，当前=0`；同时 warning 必须为 0，issue/error/shot 数必须闭合。任何其他当前问题都在写入前失败关闭。

该记录的证据类型为 `controlled_historical_production_preflight_attestation`，证书/审计类为 `historical_production_preflight_attestation`，来源为 `controlled_historical_contract_replay`。它明确设置 `current_production_pass_claimed=false`、`current_preflight_cache_reusable=false`、`conclusion_reusable_for_independent_audit=false` 和 `requires_full_current_independent_audit=true`。其缓存键使用独立 `HISTCACHE-` 域，绝不能当作当前生产预检缓存或独立审计缓存复用。

## 精确补签范围

补签缺口固定为 `GAP-1bb91d83751b422f`，有序 handoff 清单只能是：

1. `HND-f6fd29730a074d2c`
2. `HND-95e3b1c141a64783`
3. `HND-c88ee199d5fb4676`
4. `HND-e8ca409208134104`
5. `HND-0dd8fa51a9e24cd0`
6. `HND-9029110f35d74270`

逐条规则画像也是授权范围的一部分：前两条必须绑定 ruleset `V16.3-148-R10-StableRuntime:R10-PG1:c0867f81158ffca0` 及完整指纹 `c0867f81158ffca02cd5f2b3d53e527e0dcddf5a7ad02341758a54954636f5b9`；后四条必须绑定 ruleset `V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed` 及完整指纹 `d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc`。任何交叉使用或画像漂移都在写入前失败关闭。

当前 production 窗口必须使用独立的 `rule_improvement + execute + explicit_execute` 工单，targets 精确包含该 gap、上述有序列表和 `controlled_phase:preflight_evidence_backfill`。先执行 plan；全部合格后，使用完全相同的列表和 `plan_sha256`，以 `REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE` 执行。任一对象、顺序、指纹、报告或确认不符，整批计划或对应单条事务在写入前失败关闭。

## 统一恢复原 11 条

补签成功后，恢复缺口固定为 `GAP-0e5d48969c4c494e`，有序 handoff 清单只能是：

1. `HND-5394638be5fe4a55`
2. `HND-ea122cf3048e482a`
3. `HND-3a090b4f44c74929`
4. `HND-c765ba78aab840ff`
5. `HND-7738fa25ce4044c9`
6. `HND-f6fd29730a074d2c`
7. `HND-95e3b1c141a64783`
8. `HND-c88ee199d5fb4676`
9. `HND-e8ca409208134104`
10. `HND-0dd8fa51a9e24cd0`
11. `HND-9029110f35d74270`

恢复必须使用另一张当前 production 窗口工单，targets 精确包含恢复 gap、上述 11 条和 `controlled_phase:handoff_recovery`。先 plan；全部合格后，以完全相同列表和 `plan_sha256` 配合 `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT` 执行。每条原子恢复只把旧 handoff 标记为 `superseded`，不删除旧记录；新 handoff 必须为 `prepared + PENDING_AUDIT`。后六条的新 handoff 保留历史证明与当前失败诊断，但不继承任何当前 PASS 或可复用审计结论。

## 不变边界与发布门禁

- 不修改分镜、对白、canonical、XLSX、旧生产证书、原生产 trace 或历史审计记录；
- production 窗口不执行或签发独立审计；恢复后的 11 条均由独立审计窗口按当前 validator 完整重审；
- plan 零写入，execute 逐条原子提交，精确重放返回原记录而不重复签发；
- 冻结 provenance、双报告、`HISTCACHE` 隔离、负向范围矩阵、plan→execute→replay、完整回归以及 stdio/HTTP 精确 19 工具冒烟全部通过后，才可部署；
- 部署后必须运行 `get_agent_status(full_self_test=true)`，取得绑定 0.5.11 当前指纹的新 PASS 证书，才能新建补签与恢复工单。

0.5.10 的历史工作簿受控投影复验规则保持原义，见 [UPGRADE_0.5.10.md](UPGRADE_0.5.10.md)。0.5.11 只为上述精确历史合同分歧增加受控证明，不把历史 PASS 升格为当前 PASS。
