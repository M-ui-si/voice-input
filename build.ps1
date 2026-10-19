$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -c 'from speak.windows import automation; automation()'
if ($LASTEXITCODE -ne 0) { throw 'UI Automation 依赖检查失败' }
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot '.review-tmp') -Force | Out-Null
& .\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp (Join-Path $PSScriptRoot '.review-tmp\build')
if ($LASTEXITCODE -ne 0) { throw '测试失败，停止打包' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist-review --workpath build-review Speak.spec
if ($LASTEXITCODE -ne 0) { throw '打包失败' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $PSScriptRoot 'dist-review\Speak\使用说明.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'docs') -Destination (Join-Path $PSScriptRoot 'dist-review\Speak\docs') -Recurse -Force
$env:SPEAK_LICENSES_DIR = Join-Path $PSScriptRoot 'dist-review\Speak\licenses\python'
& .\.venv\Scripts\python.exe scripts\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw '许可证收集失败' }
'{"path":"../.."}' | Set-Content -Encoding utf8 (Join-Path $PSScriptRoot 'dist-review\Speak\portable-root.json')
Write-Output (Join-Path $PSScriptRoot 'dist-review\Speak\Speak.exe')
