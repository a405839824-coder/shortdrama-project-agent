$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $PluginRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "请先运行 ./scripts/setup_local.ps1"
}

$env:PYTHONPATH = Join-Path $PluginRoot "server"
& $VenvPython -m compileall -q (Join-Path $PluginRoot "server") (Join-Path $PluginRoot "tests")
& $VenvPython (Join-Path $PluginRoot "server\main.py") --self-test
& $VenvPython -m unittest discover -s (Join-Path $PluginRoot "tests") -p "test_*.py" -v
& $VenvPython (Join-Path $PluginRoot "tests\stdio_smoke.py")
& $VenvPython (Join-Path $PluginRoot "tests\http_smoke.py")
