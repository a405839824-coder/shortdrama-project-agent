# 本地测试报告

- 版本：0.1.0
- 运行时：R10-PG1
- MCP 传输：Streamable HTTP `/mcp` 与 stdio
- R10 黄金测试：62/62 通过
- Agent 生命周期测试：14/14 通过
- 全项目 Agent 测试：18/18 通过
- 插件单元/集成测试：11/11 通过
- stdio MCP 冒烟测试：通过，发现 12/12 个工具
- HTTP MCP 冒烟测试：通过，发现 12/12 个工具

验收范围：12 个工具契约、ChatGPT 文件参数、项目工单、版本化交付、分镜失败/修复/通过闭环、重复审计去重、XLSX 导入回读、压缩包膨胀限制、XLSX 公式注入转义、HTTP MCP 初始化、R10 三套回归测试、云端认证配置门禁、Auth0 风格 JWT 与 scope 校验、Railway 配置契约、变量生成及未登录 OAuth challenge 验收。
