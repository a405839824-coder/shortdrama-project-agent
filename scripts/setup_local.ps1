$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

& $PythonBin -m venv (Join-Path $PluginRoot ".venv")
$VenvPython = Join-Path $PluginRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $PluginRoot "requirements.txt")
Write-Host "本地环境已建立：$PluginRoot\.venv"

