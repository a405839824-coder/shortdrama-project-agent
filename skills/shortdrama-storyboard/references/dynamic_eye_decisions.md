# 明确眼部决定与实际可见阶段

入口：`scripts/authoring_contract_blocks.py::dynamic_eye_blocks(decision, *, picture_body)`。
也可直接调用 `scripts/dynamic_eye_decisions.py::compile_dynamic_eye_decision`。
此组件只产生作者已经决定的动态文字与事件，不签发源文、几何或审计通过结论。
原有 `eye_blocks` 继续负责首帧；不要把本组件动态结果塞进首帧、环境或其他静态字段。

`decision` 仅接受以下键，未知键或漏键拒绝：

- `version`: 整数 `1`；`subject`、`shot_id`: 具体字符串标签。
- `duration`: 有限正数，精确到十分之一秒，不接受 bool 或数字字符串。
- `phases`: 数组，顺序和起止时间必须与 `picture_body` 中所有 `【x.x—y.y秒】` 阶段完全一致，从零连续覆盖整镜。

每个 phase 的精确键和类型：

| 键 | 类型及条件 |
|---|---|
| `start`, `end` | 有限数值，十分之一秒，正时长且在镜长内 |
| `visibility` | `visible` 或 `hidden` |
| `visibility_basis` | 真实正文中的完整具名肯定子句；以逗号、分号、句号分句后须逐字相同；引用或他人的可见性不算 |
| `mode` | 可见段 `hold` / `shift` / `head_led`；不可见段 `none` |
| `targets` | `[{"id": 字符串, "label": 字符串}]`；hold 一个目标；shift/head_led 两个不同目标；hidden 空数组 |
| `head_relation` | hold 用 `still` / `tracks_target`；shift 用 `still` / `eyes_first`；head_led 用 `follows_head`；hidden 为 null |
| `head_basis` | 实际正文同人物的具名肯定头部关系子句；hidden 为 null |
| `eyelid` | `relax` / `slightly_tighten`，作者明确选择；hidden 为 null |
| `blinks` | 显式数组，每项精确为 `{start:数值,end:数值}`；只能位于本可见段内，按时间排序，不得重叠；hidden 必须空数组 |

可见性依据例如“甲眼睛清楚可见”；不可见性依据例如“甲眼睛不入画”。
固定头向依据例如“甲头部保持当前方向”；其他关系必须有真实低头、转头等动作依据。
同一阶段不能同时断言背向机位/眼部不可见和眼睛可见。转成背面时先由作者决定实际切换时刻并拆分正文阶段，组件不会猜出转身完成时间。

`picture_body` 应先由同一空间与姿态决定产生，并保留实际对白、姿态、头部关系与可见性。它不可已有该人物另一套换焦/眨眼/眼睑动作供组件叠加。其他人物的段落和对白保留。
函数返回新 `picture`、`phase_appends`、`events`、`initial_target`、`final_target`、规范化决定内容的 SHA256 与独立复核义务，不修改输入对象。
事件携带真实 `actor/action/basis/start/end/after`，basis 就是返回正文中的实际句子，须通过现有 timing events 校验。
调用方必须将 `initial_target` / `final_target` 绑定到同一作者状态源；不能保留一份相反的首帧或结束状态。不可见段后的目标重新由作者给定，不推定其不可见期间发生了何种眼动。

hold 表示持续看一个确定目标，允许在头部小幅改变时继续看它；它不会被改写成虚构的换焦。shift 或 head_led 表示真正运动，缺端点、相同端点或相邻可见阶段目标冲突时拒绝。普通“对某人作一次微小换焦”仍缺具体端点。
空 blinks 只是作者未在这一段排眨眼，不代表整镜可跳过既有眨眼、重音、错峰、自然眼睑或呼吸合同。源文要求全程闭眼仍走现有有来源依据的闭眼路线，不补造睁眼。

程序只能证明类型、范围、文本归属和重复序列化一致性。画面裁切、遮挡、真实头眼角度与源文支持仍须独立审计；宽景中“人物在画内”本身不能证明眼睛可见。不得将组件检查计作独立手稿准确率。
