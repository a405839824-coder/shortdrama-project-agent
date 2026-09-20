# 单镜规划与展示合同

由 rule_registry.json 生成；不得单独编辑。

合同：shortdrama-shot-design-v1

- 先完整读取原文与锁定设定，再按事实、知情、世界状态、当前刺激、镜头任务、景别机位、声画容量关联规划；缺依据保持未知，不以导演选择补造事实。
- AI仿真人常规单镜≤4秒；2—4秒是对白规划参考，不是最低时长。反应/插入/关系镜按真实信息需求选时长，不强行每4秒切一次，也不设置小于1秒或2秒的最低镜数比例。
- 中近景与近景约占整集镜数60%，优先用于说话人物；特写单列不并入近景类。关系交代、身体动作、道具交接需要可见空间时扩大景别。其余40%不固定分配，不靠标签或无意义镜头凑比例；正反打没有强制百分比。
- 三人及以上先按需要交代真实关系，发言时选择说话者、主要听者或有剧情作用的第三人反应；可用双人/多人镜，不强制每镜全员同框。画外人物仍保留身份、知情、姿态、持物与位置；不得改成不具名群演。
- 空间站位必须同时给出画面左/中/右与实际场景参照物关系；参照物登记为有依据的point。屏幕左右随机位重算，世界位置不随切镜漂移。数值坐标留在内部重放，不把内部推导说明写入画面。
- 人物发言的可见表情、肢体动作和音色须受人物、对象、当前台词刺激与前态支持；不能默认统一平静克制，也不能用统一夸张替代。静止和克制在具体情境中可以成立，不能强制每镜加手势、眨眼或反应。
- 新生产显式选择可见表情姿态，人物初态不默认补平静。表情动作由同一状态机承接。整体横向检查重复姿态与声调，具体是否自然仍须独立判断；有限姿态库不代表任意表演均已支持。
- 基础声线保持角色身份；当前表达、语速节奏、重音、音量和尾音按交流目的变化，不能统一平淡、统一喊叫或一味压低。声音属性仅写音色；内部解释和审计依据不得写入画面或GPT。
- 长连续台词按原句出现身份与连续字符跨度拆镜，保留每个字、标点、说话人和顺序，不加速或删词硬塞。优先换合理机位；有意义时声音可跨听者反应继续，画外声不伪造口型；同步检查音轨连续与前后姿态。
- 一个镜头一个主要叙事任务，允许自然同步的脸部、身体、说话，不统一叠加开头准备和镜尾等待。切换须有信息、关系、表演、动作或容量理由。
- 4秒<时长≤6秒仅限已列明的逐镜例外；必须提交来源、需要连续保留的内容、所需秒数与估计/实测依据、等总时长且每镜≤4秒的替代切法及具体损失、低复杂度与4秒之后仍有价值的内容。填满文字不代表例外语义成立。
- 6秒<时长≤8秒仅固定机位、固定构图的静态/低动态信息阅读，不安排人物连续说话、动态表演或运镜；当前重放要求有源文字在全镜可见。>8秒默认拆镜。按实际数值判定，不四舍五入掩盖超时，不补齐到6/8秒。
- 对白字数多、人物多、动作多、电影感、凑比例或生成平台档位均不单独成立为长镜例外。所有例外继续满足身份、来源、时空、声音、光影、可见性；整集仍90—180秒且原文完整。
- 固定或一次简单运镜须服务实际目标；不得叠加推拉摇移、为快节奏强迫运镜。当前结构化重放支持固定机位及镜间换机位，连续运镜暂不支持，返回not_supported，不能写成已执行或改用自由canonical绕过。
- 正式母版原A—J列保留位置，K列光影含光影/光源来源/光源方向/色温倾向四段。E列移除这四段及可见主体标签，但保留真实人物名称；F列仍保留四项静态光影和人物身份。内部27字段不删。
- 画面内容只写可拍到的动作、表情、视线、道具和准确对白；不得恢复数字制作时间码、承接上一镜、为下一镜准备、导演解释或心理目的。各字段只承载其所属内容，静态GPT不含动作过程/声音/运镜。
- 例外、景别和表演依据留在冻结生产计划design及审计证据；不新增用户表格的解释列。机器负责支持的身份、时序、可见点位与字段边界，语义必要性和自然度由独立逐镜全维审计判断；不据此承诺首批98%。

## 结构与数值

```json
{
  "contract": "shortdrama-shot-design-v1",
  "normal_max_seconds": 4,
  "dynamic_max_seconds": 6,
  "reading_max_seconds": 8,
  "close_class_target_percent": 60,
  "close_class_frames": [
    "medium_close",
    "face"
  ],
  "framing": {
    "wide": "宽景",
    "medium": "中景",
    "medium_close": "中近景",
    "face": "面部近景",
    "face_detail": "面部特写",
    "hands": "手部特写",
    "object": "物件特写"
  },
  "style_enforcement": "advisory_with_grounded_review; never a shot quota",
  "exception_kinds": {
    "emotional_change": "必须同画面持续看见有依据的关键情绪变化",
    "simple_action": "一个简单动作在正常速度下连续闭合",
    "meaningful_wait": "等待本身传递必要剧情，具有对象和可见起止",
    "continuous_reveal": "一次简单连续揭示不可替代的关系；当前固定机位重放不支持，必须重拆",
    "information_reading": "稳定清晰且确需整体阅读的有源信息"
  },
  "complex_actions": [
    "transfer",
    "cross_passage",
    "contact_actor",
    "assist_stand",
    "fall",
    "dress_actor",
    "undress_actor",
    "release_binding",
    "scene_light"
  ],
  "design_shape": {
    "framing_reason": "具体景别与本镜信息/表演/关系/动作的依据",
    "cut_reason": "具体切换目的；首镜说明首次呈现依据",
    "spatial_anchors": "{每个首帧实际入画人物/道具: 已登记且有来源/设定依据的场景point名称}",
    "performance": "[{actor,fact_ids,stimulus,visible_choice,gesture_choice,voice_choice}]；覆盖实际入画人物和所有说话者；无人物时[]",
    "duration_exception": "null或{kind,fact_ids,continuous_content,required_seconds,timing_basis,measurement:estimate|measured,alternative_cuts_seconds,alternative_loss,after_four,complexity}"
  },
  "output": {
    "canonical_fields": 27,
    "visible_columns": 11,
    "lighting_column": "K:光影",
    "formal_lighting": false,
    "formal_visible_subject_label": false,
    "keyframe_lighting_fields": 4
  }
}
```
