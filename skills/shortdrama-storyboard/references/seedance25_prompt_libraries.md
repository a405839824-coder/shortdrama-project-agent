# Seedance 2.5：动漫、仿真人提示词分库

## 来源与适用范围

核验日期：2026-10-29。模型依据为火山引擎的
[Seedance 2.5 提示词指南](https://docs.volcengine.com/docs/ark/seedance-2-5-prompt-guide?lang=zh)
与[模型文档](https://docs.volcengine.com/docs/ark/seedance-2-5?lang=zh)。
2.5 指南引用的 2.0 参考用法仍标明原始来源，不将它伪装成 2.5 新增要求。
即梦页面具体可用版本、上传限制及输入模式应以实际页面为准，不把火山API参数当成即梦的界面操作。

官方要求与插件政策分开保存：`seedance25_guidance.json`记录来源与简短归纳；两份库中的风格句与隔离机制是用户授权的项目实现，不是官方唯一模板。
本库未通过成片A/B测试，不声明风格稳定率、生成成功率或无字幕保证。

## 两个库

| 库 | 子风格 | 文件 | 用途 |
|---|---|---|---|
| 动漫 `animation` | `3d`、`2d` | `prompt_library_animation.json` | 动漫造型与美术；2D、3D分别选择 |
| 仿真人 `photorealistic` | `live_action_look` | `prompt_library_photorealistic.json` | AI生成的真人影视观感 |

共享文件只承载模型与制作通用知识，不是第三套画风库。
检索先选库再选子风格；没有默认库，不按小说题材、人物姓名或台词猜测，不跨库补齐缺失模板。
同一项目新批次沿用已确认选择；切换库属于明确的制作方向变更，必须重编译相关候选并重新核验。

## 接入现有插件

1. `get_agent_status`的`production_prevention_contract.prompt_libraries.catalog`返回两库目录与当前内容指纹。目录不拼接两套长提示词。
2. 把选中的完整`selection`写入`source_lock.prompt_library`。本地开发可调用`prompt_libraries.make_selection(library_id, variant)`获得同一对象。
3. 独立读取时调用`get_agent_status(prompt_library_id="animation", prompt_variant="3d", full_self_test=false)`，或`get_agent_status(prompt_library_id="photorealistic", prompt_variant="live_action_look", full_self_test=false)`；返回的`selected_prompt_library`只含所选子风格与共享知识。2D选`animation`+`2d`。这不会替项目选库，生产前仍须将返回的`selection`冻结到`source_lock`。服务端与本地`resolve_selection`共用解析；内容指纹过期、未知库、混合子风格均拒绝，不静默回退。
4. 现有制作编译器把选定风格句写入每镜`生成限制`，正式母版与GPT关键帧继续从同一字段生成。
5. 机器预检检查选择及已登记的风格冲突；新写入门禁要求明确选库。历史未选库稿件保留只读，不自动改成仿真人。

不新增分镜列，不改27字段、十一列母版、原台词、基础音色、镜头时长和项目原定剧情。
官方允许时间轴或镜头顺序，不构成恢复本插件已取消的可见数字时间码的授权。
官方建议概括动作不构成删除关键接触、因果、源文动作或高潮结果的授权。

## 参考素材职责

素材记录在`source_lock.prompt_references`。每条记录包含`id`、`role`、`library_id`、`variant`。

- `appearance`、`style`、`scene_style`：必须与选定库及子风格一致；风格转换后使用已核验的目标风格素材。
- `composition`、`motion`、`audio`、`lighting`：可跨库参考指定维度，不因此继承其他库的画风。
- `neutral`、`unknown`只可用于不承担视觉身份/画风的参考职责。

上述元数据是提交者的素材声明，代码不能据此证明实际图片内容正确；正式核验仍需查看素材。
纯文本生成允许无参考素材。不要伪造“图片1”等尚未上传的素材。
首帧、首尾帧、全能参考、编辑和延长需按实际任务选择，参考图不自动等于第一帧。
2.5指南中的白模参考也按职责登记：白模提供调度、运动或光照时不充当角色最终画风；最终人物、场景外观采用本项目所选库的素材，逐一说明对应关系。

## 防混淆检查边界

- 同一选择不能同时属于动漫和仿真人。
- `生成限制`必须包含一次所选风格句，出现已登记的相反正向风格会阻止新写入。
- 不扫描或改写原台词来决定画风；对白中提到“动画”不会触发换库。
- “真实光影”“丝绸材质”可用于3D动漫，不能当成真人画风的充分证据。
- 有明确否定范围的“禁止真人电影风格”等不按正向冲突处理。
- 当前文字检查覆盖已登记词组，不能证明所有自然语言语义均无混淆；未知表述和实际成片仍需检查。

原C-010“真人写实优先”改为按选库和素材职责裁决。仿真人摄影案例仅作为仿真人建议，动漫不继承真人皮肤等默认描述。宋韵摄影母版及古装夜景母版均标明此范围，动漫保留场景事实和连续性检查。

## 本地候选与上线

本变更基于Git主分支`70d2044ab7842ac748a7af468ff294408b132d0b`（0.5.68），准备版本为0.5.69。
本次404已定位为Railway试用到期、未支付账单及服务无活动部署；CLI登录已恢复。用户随后选择暂不续费、先完成发布准备，当前未上线。
恢复部署前需核验服务、持久卷、最新Library快照、管理员权限和候选版本；不能以旧的已消费升级快照替代最新项目状态。具体步骤见仓库`docs/release-0.5.69.md`。
软件测试与正式分镜审计、实际媒体效果分别记录。
