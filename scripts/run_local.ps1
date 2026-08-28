$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $PluginRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "请先运行 ./scripts/setup_local.ps1"
}

if (-not $env:HOST) { $env:HOST = "127.0.0.1" }
if (-not $env:PORT) { $env:PORT = "8000" }
if (-not $env:PUBLIC_BASE_URL) { $env:PUBLIC_BASE_URL = "http://$($env:HOST):$($env:PORT)" }
if (-not $env:SHORTDRAMA_DEPLOYMENT_MODE) { $env:SHORTDRAMA_DEPLOYMENT_MODE = "local" }
if (-not $env:SHORTDRAMA_AUTH_MODE) { $env:SHORTDRAMA_AUTH_MODE = "none" }
if (-not $env:SHORTDRAMA_DATA_DIR) { $env:SHORTDRAMA_DATA_DIR = Join-Path $PluginRoot "data" }

& $VenvPython (Join-Path $PluginRoot "server\main.py") --http
