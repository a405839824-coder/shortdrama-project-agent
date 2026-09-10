$ErrorActionPreference = "Stop"
$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $PluginRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "请先运行 ./scripts/setup_local.ps1"
}

$env:PYTHONPATH = Join-Path $PluginRoot "server"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $VenvPython -m compileall -q (Join-Path $PluginRoot "server") (Join-Path $PluginRoot "tests")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython (Join-Path $PluginRoot "skills\shortdrama-storyboard\scripts\r10_bootstrap.py") --self-test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython (Join-Path $PluginRoot "server\main.py") --self-test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython -m unittest discover -s (Join-Path $PluginRoot "tests") -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython (Join-Path $PluginRoot "tests\stdio_smoke.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPython (Join-Path $PluginRoot "tests\http_smoke.py")
exit $LASTEXITCODE
