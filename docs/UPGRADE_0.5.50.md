# 0.5.50 间接言语的来源审查与生产衔接

此前规则要求将有事实依据的间接问答转成对白，来源编译器却只接受原文已有的直接引语。现在先冻结真实来源、事实、说话人和具体台词，经独立审查后，编译器可以采用这一组精确转译。

复用现有 narrative 工具、工单角色和签名存储。来源图只携带可核验的批准引用，不增加自由授权字段。预检、提交、后续稿件读取及独审会重新核验来源、规则、提案版本和最新审查结论；撤回或不确定结论不能沿用旧通过。原文已有对白仍受原保护，不允许借转译批准改写。

协议：[source_dialogue_review.md](../skills/shortdrama-storyboard/references/source_dialogue_review.md)。线上读取 `get_agent_status.data.production_prevention_contract.source_dialogue_review`，再用 `read_narrative_record(record_id="source-dialogue-contract")` 获取完整结构。

新增自动控制覆盖真实 MCP 提案/审查、拒绝自审及漏项、unknown 阻断、精确文字/说话人绑定、伪造引用、规则/来源/提案变化、后续撤回、实际编译渲染与重放。公开合成样例的自动检查不计入两轮真实分镜审计，也不证明首批98%。十一列输出和单镜节奏规则未变。
