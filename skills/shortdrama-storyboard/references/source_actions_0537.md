# 0.5.37 同源动作与声音

本扩展使用既定 `production-decisions-v1`、来源动作计划及冻结生产计划，输出仍为27字段、十一列正式母版、同源R5与GPT。它增加有限动作，不能接收自由状态补丁或第二份画面正文。动作与音频计时均为预测的0.1秒模型，不是实测表演时长。

## 世界和动作

场景可添加 `passages:[{id,exit,entry,opening}]`；exit/entry引用两个相邻场景的点，间距不大于0.5米。过门前须到达出口，清除跨场景视线，门为开启，人物为站立。`set_opening:{opening,to:open|closed,hand}`必须由源文动作计划执行，opening同时是已登记开口和可触及的点。纸窗开关同时更新同一光源状态。

道具可添加 `physical:{material,contents,integrity}`。材质仅限unspecified/ceramic/cloth/metal/wood/paper，内容仅限none/water/hot_water，完整度intact/broken。初始佩戴锚点可用 `{actor,worn:head|torso}`；悬空位置仅由落物模拟派生，输入不得自造free锚点。人物派生动作状态记录真实接触、湿部位、擦拭部位、手形、表情及累计拍手/磕头数。

| 动作 | 专用参数 | 核对 |
|---|---|---|
| posture | to, point, landmarks | 站立/坐姿/蹲姿/跪姿/跪伏/躬身/躺卧；坐标、眼高、手和触及范围同步变化；不能替代远距离行走 |
| fall | point, landmarks | 来源明确的跌倒，输出“跌倒”，不改成主动躺下 |
| hand_move | hand, to | 真实手移动到可触及点，受抓握约束 |
| eye_state / hand_shape / expression | to；hand_shape另含hand | 有限且确实改变的状态，闭眼前清除视觉目标 |
| cross_passage | passage | 实际过门并继承新场景，不能靠剪切迁移人物 |
| pick_up / put_down | prop, hand；放下另含to | 原物主、空手、手与物距离及放置点 |
| drop_prop | prop, hand, to, contact:null或{actor,part}, breaks | 主动松手、空中路径、真实落点和内容洒落；只有陶瓷可碎裂，不自动添加受伤结论 |
| wear_prop / remove_worn | prop, hand；穿戴另含part | 实际佩戴者与空置部位 |
| dress_actor / undress_actor | prop, hand, target_actor；穿戴另含part | 为另一个人穿脱，不能把服务对象改成自己 |
| release_binding | prop, hand, target_actor | 初始`{actor,binding:wrists}`绳索约束只能经真实解绳解除；解开前不能自由移动身体或手，绳索随后由实际解绳者拿住 |
| wipe | prop, hand, part | 手持布料确实触及自己的脸或手 |
| contact_actor | hand, target_actor, target_part, mode | grasp/release_grasp/support/release_support/slap；真抓握必须解除后才可移动受约束部位；掌掴必须有真实手部趋近及面部接触 |
| push_actor | hand, target_actor, target_part:torso, point | 由冻结source_step执行；空置张开的手先接触躯干，再使对方被动移至已登记终点，随后释放收手；双站姿、平地、侧向或远离施力者、0＜距离≤0.6米且全程可及，接触与位移须入画。不得改成自愿让路、拉回、掌掴、跌倒或受伤 |
| push_actor_no_displacement | hand, target_actor, target_part:torso | 由冻结source_step执行；两人同为坐姿或同为站姿，至少0.8秒；空置张开的手趋近并真实推抵对方躯干，保持推抵后释放收回。目标逐样本位置、姿态不变，不接受终点参数；真实接触与未被推动的目标须入画，下一镜继承结果。不得用扶持、抓腕、主动让路、跌倒、受伤或镜外桥接替代 |
| clap / kowtow | count | 有界准确次数；磕头须先跪地；动作跨段时分配实际接触次数，不重复执行 |
| head_gesture | direction:nod或shake, count | 点头/摇头与视线状态相容 |

`landmarks`恰好含`eye_height,reach_origin_offset_xyz,hands:{left,right}`，手坐标相对身体。合法范围是保守地标模型，不宣称完整人体运动学。`part`参见当前状态工具的动作合同及实现。关键源文动作必须使用`source_step`引用已冻结的`source_action_plans`，并使实际动作及接触地标入画；未知遮挡和开放语义仍须独审。

## 时空衔接

允许在明确的later_same_place/different_place/episode_boundary切换前填写`transition.bridge:{ticks,actions}`。它模拟省略的普通走位、姿态、视线和`scene_light:{scene_id,light}`变化，保留轨迹及前后指纹。禁止连续切镜用桥接重置状态；禁止对白、核心动作、掌掴、落杯、衣物转移、拍手、磕头通过桥接被省略。桥接动作`source_use_id`必须为null；知识立场为not_applicable；不能消费原文、取得知情或计入成片秒数。最小省略模拟时间不代表故事经过时间。下一镜开始状态从执行结果继承。

## 对白、心声与禀报

同镜可顺序安排多个真实说话人。音色固定11标签且各一次，每一标签正文用`〔1:角色〕`、`〔2:角色〕`按实际对白顺序绑定。各句分别执行原有声线、语气、重音、尾音和字段归属检查。主音轨仍禁止重叠，不通过增加镜数来强行拆开同场交流。

`inner_voice`不含自由actor/text参数，从来源用途读取。来源事实channel为thought，须提供真实思考者和已发生/当前心声，wording只接受verbatim，逐字匹配冻结原文及必要条件。输出“角色内心独白”至心理音轨，保留安全属性及真实音频字符范围；不生成口型，不成为客观旁白，不能被heard授予别人。猜测、误会、伪装仍采用原知识立场，独立核对其含义。

`knowledge.acquisitions.mode=source_report`用于原文明确“某人已经听了禀报”的事件。必须绑定实际narrate音轨，证据channel=report、subject为收到禀报的人、affirmative/asserted/occurred、无条件；完整叙述必须逐字等于该证据的实际源文跨度，获得的fact_ids必须由该音轨携带。该音轨完成前不能使用所得消息，完成后只登记reported，不登记known。report是需独立审查的源文判断，不是凭“知情”标签授予全知；省略的汇报原话不得编造。

## 接触音效

`drop_impact`及`slap`仅在真实接触结束tick发声；抓握不能绑定掌掴音。`handclaps`与`kowtow_contact`按照准确次数及实际接触tick发声。`cloth`只绑定已执行的布料动作。脚步声读取动作开始时姿态，不能用镜头开始时仍躺卧的状态误拒稍后起身行走。所有音效保留动作ID、时点及音量，不能通过自由音效正文添加剧情事件。

原有源文、时长、唯一模板、完整预检、独立审计和正式交付回执门禁继续生效。新字段语法与知情模式经显式0.5.37迁移登记；规则或模板指纹变化后，旧生产计划须重新按真实输入冻结，不能伪造旧证据为当前通过。

## 0.5.39 有限承托与扶起

道具锚点可用 {prop:真实承托物ID,offset_xyz:[x,y,z]}，水平偏移不超过0.3米，高度在0至0.3米之间。承托链必须有界、无环且各承托物完整；杯随盘的位置从同一实际手部运动推导，不能给一只手同时登记盘和杯。pick_up 从承托物中取杯后才改为真实持杯手，盘的持有人保持不变。承托物也继承所承载原文道具的来源保护，不能通过未绑定的移动绕开来源动作。

assist_stand 仅由来源动作计划执行。参数为 target_actor,hand,target_part,point,landmarks,helper_point,helper_landmarks，actor 是施扶者。动作开始前必须已经真实 support 住对方的一只手或腕；抓握不能冒充扶持，腕绳须先解除，两人没有其他接触约束且视线已清除。施扶者从站/坐/蹲姿，被扶者从躺/坐/蹲/跪姿，按同一时轴到站姿；地标格式与 posture 相同，最少0.8秒，近距离起身不能代替走路。支撑手逐样本跟随被扶手，全程检查可及距离和遮挡，之后仍须真实 release_support 才能松开。该动作不能放入时空省略桥接。

这些是有限地标模型的输入能力，不是表演实测、人体动力学或源文语义通过证明。
# 0.5.66 source-bound mirror effects and scene voices

`surface_effect` is a frozen `source_step`: its source subject is an existing
prop with `surface={pattern:still|ripple,glow:none|faint|flicker,mist:none|local_white,extent_m}`.
The sole parameter `to` has the same schema and the same radius, in `(0,1]`
metres. The intact prop must remain visible throughout the event and at its
result. The exact start frame keeps the previous state; subsequent samples
carry the new bounded state until another sourced action changes it. Static
keyframes describe only current texture/light/mist, never replay a future
change. Local mist never authorizes whole-room fog, a new figure, sound, or
invented lighting on actors. This finite state is not a fluid or optical solver.

`speak` may explicitly carry `emitter={prop:<registered prop>,mode:disembodied}`.
The source speaker remains unchanged and must not also be a bodily actor.
The intact prop must belong to the shot's actual scene. Dialogue is timed and
voiced through the normal eleven-label contract, with no generated body, mouth,
lip sync, or visual gaze. Other characters may receive a report only after the
actual speech finishes in the same unobstructed scene; hearing does not prove
its proposition true. This is scene speech, not narration or inner speech.

Additional finite sourced gestures preserve actions that cannot be replaced by
generic holding: `stroke_prop={prop,hand,to}` follows two distinct contact
landmarks within 0.15 m of an intact optical prop; `hand_tremor={hand}` models
a free hand's small transient motion over 0.4–3 seconds. `contact_actor`
supports explicit left/right upper-arm support and own `lap_cloth` using
`grasp_cloth`/`release_grasp_cloth`, requiring seated posture and exactly one
intact cloth garment worn on the torso. The cloth stays worn. Grasping another
body part, an absent garment, or an occupied hand is rejected. These gestures
retain source-plan, contact, visibility, timing and semantic-review gates.
