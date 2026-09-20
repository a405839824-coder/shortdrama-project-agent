$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

& $PythonBin -m venv (Join-Path $PluginRoot ".venv")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$VenvPython = Join-Path $PluginRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython -m pip install -r (Join-Path $PluginRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "本地环境已建立：$PluginRoot\.venv"
