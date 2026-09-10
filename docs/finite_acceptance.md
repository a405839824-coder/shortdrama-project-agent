# 有限验收运行器

当前为本地源码候选。控制器是否通过验收、是否部署到远端，以及稿件语义正确率，是三个不同结论。

`scripts/controlled_acceptance.py` 是 host 管理接口，不注册新 MCP 工具。先写完整合同文件，再用同一 registry 执行：

```powershell
.venv\Scripts\python.exe scripts/controlled_acceptance.py --registry <固定账本目录> freeze --manifest <完整合同.json> --workspace <插件绝对目录>
.venv\Scripts\python.exe scripts/controlled_acceptance.py --registry <固定账本目录> run-round --contract <合同ID> --output <插件目录内明确排除的证据目录>
.venv\Scripts\python.exe scripts/controlled_acceptance.py --registry <固定账本目录> status --contract <合同ID>
```

合同必须冻结 `contract_version=finite-acceptance-v1`、contract/scope/lineage、namespace/project、frozen_files、candidate_files、repair_allowlist、tracked_roots、exclude_globs 和 steps。步骤包含 step_id、inputs、depends_on、operation_limits，以及精确 recipe 和 expected。官方实现以 `acceptance_control.py` 的严格字段校验为准；未知字段拒绝。

CLI 仅执行固定 Python 解释器上的命名 unittest 模块或工作区脚本，使用参数数组并关闭 shell；拒绝 `-c` 和未知模块入口。解释器及测试判据文件必须冻结，解释器不得由作者工具参数替换。CLI 的 expected 固定为 process/accepted/空原因，禁止 `semantic_pass_claim` 状态。目标原因和下游阻断由冻结的独立测试断言核查；控制器检查实际证据文件及哈希，不能自行证明文件中的自然语言判断正确。

每轮每个列明步骤执行一次；进程返回错误记录实际退出及日志，根因须依据日志另判。超时/中断不退款，进入停止。依赖未满足的后继只留 not_run，不启动进程。全部独立步骤完成后只会成功终止、等待唯一一次允许修正，或失败停止。CLI 不会自动修正或启动第二轮。首轮后在允许清单内完成一次有证据的修改，host 通过 `repair --changed-path <精确路径> --rationale <问题证据及修正逻辑>` 绑定实际差异；再次手工 `run-round` 执行第二轮。标准、原始失败和第一份输入字节保留。第三轮与成功后的继续执行一律拒绝。

服务仅在 host 显式启用受控配置后执行请求额度守卫，详见 [服务入口与保证范围](../skills/shortdrama-storyboard/references/finite_acceptance_control.md)。默认旧服务会明确显示 `legacy_uncontrolled`。受控配置不改变本机管理员文件权限；另开 registry/直接编辑代码的 host 管理员仍有绕过能力。因此本实现是受控流程限制，不能描述成已经部署的远端隔离系统。

统一生产输入检查读取现有规则与真实生成状态，缺项阻断并保留原始输入。独立语义审计、原文完整理解、实际输出检查及正式签核仍必要。软件验收通过不等于新分镜首批 95%/98% 或两审收敛已经成立。
