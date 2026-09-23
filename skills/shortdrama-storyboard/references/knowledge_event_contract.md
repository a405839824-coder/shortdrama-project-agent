# 来源事件与人物知情合同

由 rule_registry.json 生成；不得单独编辑。

合同：source-event-knowledge-v1

- 来源事件归属与角色知情分开。uses可加event_fact_ids，仅说明此真实动作正在演出本人主事件；不授予已知，不证明对白命题为真，也不代表事件结果提前发生。旧uses形状保留，不能自动把历史initial洗成有效证据。
- 主事件取实际source_use.fact_ids[0]对应fact.event_id。event_fact_ids只接受该主事件、同一实际人物、匹配physical_action/action、spoken/oral或inner_voice/thought的事实；与fact_ids不交，不得吞掉depends_on/conditions上下文。两集合合并仍须覆盖实际source_use的全部事实。
- 实际物理事件须肯定、已发生或进行中、无未满足条件。本人实际说出未来、否定或条件台词只证明expression_occurrence为occurred/current且expression_status为asserted；不把台词内容当作事实，不要求未来承诺已经兑现。依赖信息仍逐事实另列。
- self_event只授予本人实际physical source use的主事件事实，evidence_fact_ids必须与取得事实完全相同；该use全部实际步骤必须已完成，action_id绑定其中剧情时间最晚完成步骤。仅self_event_effect_kinds中的实际物理效果可支撑，不接受声音、纯视线/表情/保持、调度或他人私谋。
- source_reading是受限的原文明示阅读收悉，额外必填prop_id；只取得有源阅读事件，不取得未给出的清单文字或任意命题。来源主体、确切收悉原句及先读后反应的语义仍独审。不得把未读、猜测、看到纸或普通持纸改为已读懂。
- source_reading绑定同physical use最后完成的hold段；该段每个样本均须自然睁眼注视同一真实纸张，纸张与人物同场且无未建模遮挡。先look再hold；只有注视转移完成而没有持续阅读时段不算取得。模型不证明像素辨字、字号可读或实际阅读速度。
- 本版阅读收悉原句限“人物名+reading_receipt_phrases中一项”，可有句末句号；必须精确原文支持，不能改写凑句型。否定、仅望纸、普通停住、别人阅读、后续反应并在同一跨度、未支持措辞均不自动通过，返回not_supported或保持待核。
- self_event/source_reading仅在真实完成时点更新known，并记录剧情时钟；earlier反应、未完成步骤、同名错误人物、错误物件、跨场未来结果、将自身发言当真均拒绝。不得添加自我听取、伪报告或预填未来initial换取通过。
- heard/source_report保持原来真实来源、同场、非自我授予、完成后报告及非真值授权检查；reported不升级为known。猜测/误解/伪装仍须确切语义依据，不能代替本修复。
- 独立审查必须检查完整原文的必要依赖是否提取；primary事件字段不是自动豁免。未给出的知识、动作结果和阅读内容不得靠标签或来源相同来授予。模板、原剧情台词、基础声线均不变。

## 结构与支持范围

```json
{
  "contract": "source-event-knowledge-v1",
  "self_event_effect_kinds": [
    "move_actor",
    "turn",
    "posture",
    "hand_move",
    "hand_shape",
    "cross_passage",
    "pick_up",
    "put_down",
    "transfer",
    "drop_prop",
    "wear_prop",
    "remove_worn",
    "dress_actor",
    "undress_actor",
    "release_binding",
    "assist_stand",
    "wipe",
    "contact_actor",
    "push_actor",
    "push_actor_no_displacement",
    "clap",
    "kowtow",
    "head_gesture",
    "set_opening",
    "fall"
  ],
  "uses_shape": {
    "required": [
      "action_id",
      "actor",
      "fact_ids",
      "stance",
      "evidence_fact_ids"
    ],
    "optional": [
      "event_fact_ids"
    ]
  },
  "acquisition_shape": {
    "required": [
      "id",
      "actor",
      "fact_ids",
      "evidence_fact_ids",
      "action_id",
      "mode"
    ],
    "source_reading_additional_required": [
      "prop_id"
    ]
  },
  "semantic_status": "pending_independent_review",
  "reading_receipt_phrases": [
    "看清内容",
    "看清清单",
    "看清账单",
    "看清纸上内容",
    "读清内容",
    "读完清单",
    "读完账单",
    "读完纸上内容"
  ]
}
```
