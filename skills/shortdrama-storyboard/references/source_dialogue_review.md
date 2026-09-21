# 来源事实转对白的生成前独立审查

0.5.50 使用现有工具完成提案、审查、生产三段衔接，不增加工具参数。先用 `read_narrative_record(record_id="source-dialogue-contract")` 读取本协议。

仅处理来源已经支持的信息、但没有给出逐字说法的实际间接言语。原文已有直接台词继续逐字、逐说话人、逐出现次序保护。不得把未知内容、含糊说话人、未来或假设发言伪装成已经发生的对白。

1. 用 `storyboard_plan / plan_only / plan_only` 生产权限工单冻结转译提案，调用 `preflight_narrative_plan(plan=...)`。提案结构检查不批准语义。
2. 独立审查者完整读取签名提案及真实来源，另建 `narrative_audit / audit_only / read_only` 工单，targets 包含返回的 plan_id。逐条逐维度核对后调用 `review_narrative_artifact`。服务端检查角色、窗口和身份分离；填写 PASS 本身不是独审权限。
3. 只有全部 pass 才返回 `data.dialogue_realization`。把该完整对象放进来源图 `dialogue_realizations` 数组，保留原事实的 `unstated / event_only / content_text=""`，不能改成原文给定台词。决定里的 uses 必须与批准内容完全相同。
4. 来源图仍须冻结真实 production_plan，正常走生产请求、预检、提交、完整分镜独审和签核。转译批准不替代成片内容审计，不计为一次完整分镜审计。

提案：

```json
{
  "contract": "source-dialogue-proposal-v1",
  "source_artifact_id": "当前项目导入的权威原文 artifact_id",
  "source_sha256": "该原文文件指纹",
  "source_texts": {"P911": "足够上下文的精确源文片段，在完整提取正文中恰好出现一次"},
  "binding": {
    "sources": {"P911": "上述片段UTF-8 SHA256"},
    "facts": ["source-facts-v1 完整事实对象，含所有条件和依赖；supports 的字符范围相对 source_texts"],
    "uses": [
      {"id":"U911Q","fact_ids":["F911Q"],"mode":"spoken","wording":"adapted","actor":"明确说话人","text":"拟采用的完整对白"}
    ]
  }
}
```

facts/uses 使用既有来源合同对象，不用上述说明字符串代替对象。facts 要有明确发生过的 oral 事件，expression_status=asserted、expression_occurrence=occurred/current；正文的事实真假、否定、条件、先后和说话人必须由独审判断。背景依赖事实可保留原本类型。提案只包含这组转译所需事实，依赖不可遗漏。

review：

```json
{
  "contract": "source-dialogue-review-v1",
  "target_sha256": "返回的 plan_sha256",
  "checks": [
    {"use_id":"U911Q","dimension":"source_entailment","status":"pass","evidence":[{"source_id":"P911","start":0,"end":5,"text":"实际字符片段"}],"reason":"对这条台词的具体核对结论"}
  ]
}
```

每个 use 必须分别提供五项检查：`source_entailment`、`speaker_attribution`、`conditions_and_causality`、`no_added_plot`、`original_dialogue_preserved`。状态为 pass/fail/unknown；每项证据必须落到原文真实半开字符区间和具体理由。无法确定说话人或事实，不可填 pass。证据句中没有逐字说法时，应说明是基于什么事实做的最小转译，而不能声称原文存在该句。

上述五项只批准 `binding.uses` 的局部措辞：因果项检查拟采用的话是否改变条件、真假、先后或因果含义；原对白保护项检查是否将已有原话冒充未给措辞并重写。它们不能证明尚未产生的完整分镜已经交付所有结果、保留相邻原句或满足时序。对这些后续义务继续保持待审，交由冻结生产计划和完整稿件审计验证，不能以此局部批准关闭。反过来，不应要求先生产完整分镜，才允许审查两句拟用措辞。

重审必须提交 `supersedes_review_id` 和 `change_reason`。最新 fail/unknown 会立即阻断旧通过记录。修改来源、提案、工单请求或运行规则会使批准失效；修后重新冻结并独审。服务端每次重新读取签名记录；离线编译只验证内容绑定，不能自行签发批准或以缓存结果恢复失效批准。

服务端不证明模型理解永远正确，也不保证首稿98%。若提案不能保留原文条件、因果与信息，不可用静默改写、配音旁白、手工母版或假称原文给定来绕过阻断，应保留未解决状态。
