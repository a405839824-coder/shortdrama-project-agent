# 安装与接入

## 1. 安装独立运行环境

将源码放到固定目录，目录名使用 `shortdrama-project-agent`。
本项目 Python 下限取依赖要求，推荐 Python 3.12；无需系统级安装任何 Python 包。

Windows：

```powershell
cd C:\path\to\shortdrama-project-agent
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_local.ps1
.\.venv\Scripts\python.exe server\main.py --self-test
.\.venv\Scripts\python.exe tests\stdio_smoke.py
```

macOS/Linux：

```bash
cd /path/to/shortdrama-project-agent
bash scripts/setup_local.sh
.venv/bin/python server/main.py --self-test
.venv/bin/python tests/stdio_smoke.py
```

首次安装需联网从 Python 包索引下载依赖。不能联网时，先在受信任环境准备与目标
系统/Python 版本匹配的 wheel 缓存，再用 pip 的离线选项安装。

## 2. 接入 MCP 客户端

安装后 `.mcp.json` 内的 `command`、`args` 和 `cwd` 均指向刚安装的源码及虚拟环境。
支持该 JSON 格式的客户端可直接使用它。需要分项填写时，照抄这三个字段和 `env`。
不要添加 API key，也不要把维护者的远程连接 ID 当成自己的连接。

Codex CLI 可直接添加本地服务。Windows PowerShell，在源码目录执行：

```powershell
$runtimePath = (Resolve-Path .\.venv\Scripts\python.exe).Path
$serverPath = (Resolve-Path .\server\main.py).Path
codex mcp add shortdrama-local --env SHORTDRAMA_DEPLOYMENT_MODE=local -- $runtimePath $serverPath --stdio
```

macOS/Linux：

```bash
codex mcp add shortdrama-local --env SHORTDRAMA_DEPLOYMENT_MODE=local -- "$PWD/.venv/bin/python" "$PWD/server/main.py" --stdio
```

之后启动新会话，通过 `/mcp` 查看工具。只添加 MCP 服务不会自动安装技能；
同时需要技能工作流时使用下一节的本地插件方式，避免重复启用两个同名功能入口。

## 3. 本地桌面插件（含技能）

在独立的测试项目中安排以下目录，再执行第一节安装步骤：

```text
my-storyboard-workspace/
  plugins/
    shortdrama-project-agent/
      .codex-plugin/plugin.json
      .mcp.json
      .venv/
      server/
      skills/
```

从插件目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\install_repo_marketplace.py --repo-root ..\..
```

macOS/Linux 使用 `.venv/bin/python scripts/install_repo_marketplace.py --repo-root ../..`。
脚本只更新该测试项目内的 `.agents/plugins/marketplace.json`。
在桌面应用中打开 `my-storyboard-workspace`，刷新插件目录，安装并启用本地插件，
然后开始新会话。不同客户端/工作区对本地插件的支持可能不同。

桌面应用会把插件复制到缓存，Python 虚拟环境仍留在原源码目录。
因此安装脚本生成的 MCP 配置采用绝对路径：保留这个源码目录，移动目录后重新
运行 `configure_local_mcp.py`，并刷新或重新安装本地插件。
本候选已按实际测试记录验证运行协议；桌面插件目录中的点击安装仍需客户端验收。

## 4. 可选本地 HTTP

Windows 执行 `scripts/run_local.ps1`；macOS/Linux 执行 `bash scripts/run_local.sh`。
检查 `http://127.0.0.1:8000/health`，客户端的 Streamable HTTP 地址为
`http://127.0.0.1:8000/mcp`。停止前台服务使用 Ctrl+C。

不要将未认证的本地 HTTP 入口公开到互联网。云端运行使用自己的 OAuth、HTTPS
和存储，详见 [CLOUD_MCP.md](CLOUD_MCP.md)。本地 stdio 不需要云部署。

## 5. 更新、迁移与常见问题

- 更新前备份自己的 `data/`；在新的源码目录安装与验证，再切换客户端配置。
- `.venv/bin/python` 在 Windows 找不到：重新执行 Windows 安装脚本，或在已装好
  依赖后运行 `.venv\Scripts\python.exe scripts/configure_local_mcp.py`。
- Python 命令打开 Microsoft Store：安装 Python，或将 `PYTHON_BIN` 指向已有解释器。
- 自检失败：保留错误摘要，先核对 Python、依赖和运行路径；不要跳过生产门禁。
- 打包报本机绝对路径：先运行 `configure_local_mcp.py --portable`。
- 模型或会员不可用：本项目不提供模型额度，由使用的 MCP 客户端和模型服务决定。

官方接入参考（2026-10-26 核对）：

- [MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)
- [插件打包和本地 marketplace](https://developers.openai.com/plugins/build/plugins)
