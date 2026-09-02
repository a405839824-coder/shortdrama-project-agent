# 0.5.16：ChatGPT Library 项目快照存储

0.5.16 把项目内容从插件持久卷迁移到 ChatGPT Library，同时保持原有 19 个公共工具、R10-PG1 质量门、窗口无角色限制和服务端生产/独审/修正身份隔离不变。

## 持久存储边界

插件服务器持久卷只允许保存运行规则、重启稳定的完整性/下载密钥，以及签名后的 Library 文件 ID、Library 路径、快照 SHA-256、完整目录树 SHA-256、项目状态和单调代次。项目 Hub、问题台账、DOCX/PDF/MD、分镜 XLSX、canonical JSON、提取内容、审计报告与证据、handoff、恢复日志、证书、trace 和迁移日志必须只存在于 ChatGPT Library 或操作系统临时运行目录。

## 生命周期

1. 迁移前创建完整只读快照，并在服务器端记录文件清单与 SHA-256。
2. 保存到 ChatGPT Library，再从 Library 回读并验证同一快照 SHA-256。
3. 使用 `migrate_project_state(storage_action="register_snapshot")` 登记签名指针；本步零删除。
4. 部署 `SHORTDRAMA_PROJECT_STORAGE_MODE=library` 后，`get_agent_status` 对未挂载项目返回 `mount_required`。
5. 调用方把指针指定的精确 Library 文件传给 `storage_action="mount_snapshot"`；服务端同时校验快照和完整目录树哈希，然后只解包到系统临时目录。
6. 任一项目写入后，先 `storage_action="export_snapshot"`，把结果保存并从 Library 回读校验，再用上一代次执行 `storage_action="commit_snapshot"`。
7. 只有挂载目录树与已提交指针完全一致时，`storage_action="unmount_snapshot"` 才可删除临时副本。

## 删除门禁

旧持久项目目录的删除不属于登记或切换动作。必须先覆盖全部项目、通过 Library 回读、部署 Library 模式并用真实项目完成挂载往返；随后还要在动作发生前让用户确认精确删除目标。11 条历史交接及其完整恢复链在 Library 快照中保持原字节，不做压缩式改写、重签、改状态或丢弃。
