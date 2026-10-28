# Shortdrama Project Agent / 分镜项目 Agent

Python MCP server and agent skill for source-based Chinese short-drama and
storyboard workflows. Runtime version: **0.5.68**. License: **MIT**.

本项目把原文导入、分集方案、分镜生产、版本留档、问题追踪、独立审查和 Excel
交付连接到同一项目。模型负责阅读与创作，服务端负责有界规则校验、状态、权限、
签名记录和导出；软件测试通过不等于剧情、表演或实际视频质量已经验收。

## 功能

- 绑定原文和对白来源，保存方案、首稿、失败和修订记录。
- 输出正式分镜母版、GPT 静态起始帧和逐镜阅读页，保留内部 canonical 数据。
- 跟踪逐镜及相邻镜问题，区分机器检查、独立语义审查与用户签核。
- 本地单用户运行；自托管云端可配置 OAuth 和 Admin/Producer 权限。
- MCP 支持 stdio 与 Streamable HTTP；本地 Admin 暴露 24 个工具。

本地使用需要 Python **3.12 或更新的兼容版本**，以及支持 MCP 的客户端。
安装本服务器和运行自检不需要 OpenAI API key。实际模型服务由客户端另行提供；
本项目不包含 ChatGPT 订阅、模型额度或公共托管服务。

## Windows 快速开始

下载源码后解压为 `shortdrama-project-agent`，在该目录打开 PowerShell：

```powershell
python --version
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_local.ps1
.\.venv\Scripts\python.exe server\main.py --self-test
.\.venv\Scripts\python.exe tests\stdio_smoke.py
```

若 `python` 不是所需版本，可先设置 `$env:PYTHON_BIN` 为实际 Python 可执行文件路径。
安装脚本创建本项目独立的 `.venv`，安装 `requirements.txt`，并生成适配当前电脑的
`.mcp.json`。生成后的文件含本机绝对路径，只供本地安装使用。

## macOS / Linux 快速开始

```bash
python3 --version
bash scripts/setup_local.sh
.venv/bin/python server/main.py --self-test
.venv/bin/python tests/stdio_smoke.py
```

通过 `PYTHON_BIN=/path/to/python3 bash scripts/setup_local.sh` 指定解释器。
参见[完整安装与客户端接入](docs/INSTALL.md)。

## 首次使用

客户端启用插件后，可用下面的示例请求开始。示例文本是演示用的合成内容：

> 创建名为“公开演示”的短剧项目，先检查工具状态，再告诉我导入原创短文和建立
> 分集方案需要哪些输入。演示短文：小雨在车站捡到一把伞。她等到失主回来，
> 把伞交还，对方说：“谢谢你。”

生产按技能说明执行原文读取、方案、预检、提交、独立审查和授权修正。
自检或连通性检查不会自动生成整部作品，也不会替用户签核。
完整工作流说明在 [SKILL.md](skills/shortdrama-storyboard/SKILL.md)。

## 数据与部署

本地默认将运行数据写入 `data/`，可通过 `SHORTDRAMA_DATA_DIR` 指定其他私有目录。
源码包不包括项目数据、账号连接、密钥、Python 环境或 Git 历史。
HTTP 本地入口为 `http://127.0.0.1:8000/mcp`，健康检查为 `/health`。
需要云端访问时，按 [云端配置](docs/CLOUD_MCP.md) 和
[Railway 部署说明](docs/RAILWAY_DEPLOY.md) 配置自己的 HTTPS、OAuth 和存储。

## 测试与源码打包

完整现有测试：Windows 运行 `scripts/run_tests.ps1`，macOS/Linux 运行
`bash scripts/run_tests.sh`。本次开源整理的验证范围见 docs/OSS_RELEASE.md；
没有执行的平台或测试不应记为通过。

安装之后，打包前先将本机路径恢复成可分发路径：

```powershell
.\.venv\Scripts\python.exe scripts\configure_local_mcp.py --portable
.\.venv\Scripts\python.exe scripts\package_plugin.py --profile source
```

POSIX 使用 `.venv/bin/python` 执行相同两个脚本。打包输出位于 `dist/`，
并生成 `SHA256SUMS`。完成打包后运行 `configure_local_mcp.py` 恢复本机配置。
源码 ZIP 需要安装依赖，不能视为包含 Python 环境的即装即用包。

## 项目资料

- [开源范围与来源](docs/OSS_RELEASE.md)
- [贡献说明](docs/CONTRIBUTING.md)
- [安全与数据说明](docs/SECURITY.md)
- [第三方依赖说明](docs/THIRD_PARTY.md)
- [历史开发记录](docs/DEVELOPMENT_HISTORY.md)

MIT 许可见 [LICENSE](LICENSE)。第三方依赖保留各自许可证；用户导入的小说、
剧本、分镜、图片及其他素材不属于本项目许可证授予范围。
