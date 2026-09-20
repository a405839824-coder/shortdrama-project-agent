# 场景时间与叙事因果合同

由 rule_registry.json 生成；不得单独编辑。

合同：scene-narrative-v1

- 生成分镜前先读完整授权原文，在事实图depends_on/conditions保留必要触发、获知、决定、结果；原文有不代表观众已知。模型不得以删去依赖、降为非必要或director_choice补造剧情来获得通过。
- production_plan v2必填narrative；全部镜头按实际剪辑顺序唯一归入连续场景段，跨地点、时间跳跃、连续性ID或集号变更必须另起段。每段声明时间、切场作用、实际入场识别主体与来源事实，至少一项实际信息交付。
- time.start_tick使用内部0.1秒剧情时间；与成片累计时长分开。同时异地须以simultaneous关联此前场景段、时间区间相交、地点不同，并使用simultaneous_different_place转场。时间依据必须引用实际原文事实；同场状态只按剧情时间前进。
- 当前支持状态可独立重放的异地平行剪辑。读取未来人物/道具/场景、跨场走位倒流、同一事件重复取景和倒叙未建模时返回not_supported，不能改标签绕过。跨场物理行动保留原状态链。
- 每条来源事实depends_on和conditions边必须有causal_links：shown引用实际先行交付镜；prior_delivery引用同项目历史canonical的文件哈希、镜号、观众字段及原句；withheld引用实际后续揭示镜或授权未来原文跨度。无依据保持不足，不能编造报信、书信、行走或对白。
- shown检查实际可见/可听事件的交付顺序；同一句有源声音或同一可见文字允许共同交付原因和结果，语义仍独审。未投影动作、仅覆盖原文/规则/计划文字均不算观众交付。程序检查的是已有事实图，完整提取与合理因果仍须独审。
- prior_delivery在预检和提交均由服务器按当前项目读取真实已登记canonical并核对字节及可见/可听字段。它证明历史分镜存在该表述，不证明已上映或引用解释正确；其前后顺序及支持关系仍须独审。本地CLI不能签署该服务器权限检查。
- withheld只保留有源悬念，不要求每个短场景重讲完整起承转合；跨批未来兑现记payoff_pending_outside_batch，不能声称全剧已闭环；每次后续生产须重新核对相应兑现。已知原因不能伪装为未揭示悬念。
- 人物知情与观众理解分开核对。实际heard/source_report获知按剧情时间完成后才供依赖动作使用；声音剪辑不能让异地人物听到或预知。初始已知、误解、伪装仍需独立源文核对。
- 新场景用有源人物、参照物及当前动作建立识别；不强制空镜/大全景，不为约60%近景软目标删除必要定位。返回已建立地点可直接有效近景，入场主体必须实际可见。
- 画面内容只写可见内容；时间/因果/导演理由留内部计划，剪辑放切镜/转场字段，声音各归所属字段。GPT静态首帧不写同时/然后/承接。J-cut/L-cut声音桥是合法剪辑建议，但当前重放无跨镜重叠声轨，audio_bridge只能none，其他值明确not_supported。
- 沿用常规单镜≤4秒及已有例外、整集90—180秒；补回必要信息后重新分镜/分集，不靠语速加快、任意改对白或延长镜头塞入。完整授权对白和情节权限保持。
- 独立审计逐镜scene_causality须检查完整原文事实图，再只读实际分镜及所引上下游证据；禁止把机器结构通过当内容自然连贯或98%首批准确率。旧计划不能自动升级成新审计证据。

## 输入结构

```json
{
  "contract": "scene-narrative-v1",
  "segments": [
    {
      "id": "stable scene segment ID",
      "shot_ids": [
        "contiguous actual shot IDs"
      ],
      "time": {
        "relation": "initial|continuous|later|simultaneous",
        "reference_segment_id": "earlier segment ID; null only for initial",
        "start_tick": "nonnegative story-time integer tenths; first segment 0",
        "evidence_fact_ids": [
          "source facts supporting chronology"
        ]
      },
      "purpose": "advance|answer|contrast|setup|reaction|result",
      "purpose_fact_ids": [
        "actually delivered fact IDs"
      ],
      "orientation_subject_ids": [
        "actually visible actor/prop or source-bound landmark point IDs in entry frame"
      ],
      "entry_basis": "how the viewer recognizes who/where/current action",
      "cut_basis": "why cut here; not final picture text",
      "audio_bridge": "none; J/L overlap not supported by current renderer"
    }
  ],
  "causal_links": [
    {
      "fact_id": "source fact",
      "requires_fact_id": "its depends_on/conditions prerequisite",
      "treatment": "shown|prior_delivery|withheld",
      "establishment_shot_id": "shown delivery shot or null",
      "prior_delivery_id": "prior_deliveries ID or null",
      "reveal_shot_id": "later actual reveal shot or null",
      "future_source_span": "null or {source_id,start,end,text} exact authorized future span",
      "reason": "source-grounded presentation/suspense reasoning"
    }
  ],
  "prior_deliveries": [
    {
      "id": "stable receipt ID",
      "fact_ids": [
        "supported facts"
      ],
      "artifact_id": "same-project registered canonical artifact",
      "artifact_sha256": "SHA256 of exact artifact bytes",
      "shot_id": "actual historical shot ID",
      "field": "画面内容|起始状态|心理音轨|环境音效",
      "quote": "exact nonempty field excerpt"
    }
  ]
}
```
