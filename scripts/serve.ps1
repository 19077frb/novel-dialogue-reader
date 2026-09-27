# 生产同源启动：检查依赖 → 迁移 → 构建前端 → 由后端在同一端口提供 API 与前端页面。
#
#   pwsh -File scripts/serve.ps1                 # 构建前端并启动（默认 127.0.0.1:8765）
#   pwsh -File scripts/serve.ps1 -SkipBuild      # 复用已有的 frontend/dist
#   pwsh -File scripts/serve.ps1 -Port 8800      # 换端口
#   pwsh -File scripts/serve.ps1 -Stop           # 结束由本脚本启动的进程
#
# 数据目录与迁移：默认仓库内 data\；脚本会显式执行迁移（也可用 -DataDir 指向别的目录）。
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$Stop,
    [int]$Port = 8765,
    [string]$DataDir = '',
    # 仅用于离线演示/验收：显式启用测试用 FakeProvider（不访问网络，也不代表真实模型能力）。
    [switch]$AllowFakeProvider,
    [string]$FakeLabels = 'deterministic'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot 'data\run\serve-pids.json'

function Stop-ServeProcesses {
    if (-not (Test-Path -LiteralPath $pidFile)) { return }
    $recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($entry in @($recorded)) {
        if (-not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) { continue }
        Write-Host "停止 $($entry.name) (PID $($entry.pid))"
        & taskkill /PID $entry.pid /T /F *> $null
        if ($LASTEXITCODE -ne 0) {
            Stop-Process -Id $entry.pid -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ($Stop) {
    Stop-ServeProcesses
    Write-Host '已按记录停止由 serve.ps1 启动的进程。'
    exit 0
}

function Get-BackendInvoker {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --project backend python -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Exe = 'uv'; Args = @('run', '--project', 'backend', 'python') }
        }
        Write-Warning 'uv run 不可用（例如受限环境无法访问 uv 缓存），回退到 backend\.venv。'
    }
    $venvPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return [pscustomobject]@{ Exe = $venvPython; Args = @() }
    }
    throw '未找到后端环境，请先运行：uv sync --project backend --all-groups'
}

foreach ($tool in @('node', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "缺少依赖：$tool。请先安装并确保在 PATH 中。"
    }
}
$backendInvoker = Get-BackendInvoker
$distDir = Join-Path $repoRoot 'frontend\dist'

Push-Location $repoRoot
try {
    if ($backendInvoker.Exe -eq 'uv') {
        Write-Host '同步后端依赖（uv sync --project backend --all-groups）…'
        & uv sync --project backend --all-groups
        if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
    }
    else {
        Write-Warning '跳过 uv sync（使用已有的 backend\.venv）；如需同步依赖请手动运行 uv sync。'
    }

    if ($DataDir) { $env:NDR_DATA_DIR = $DataDir }
    if ($AllowFakeProvider) {
        Write-Warning '启用测试用 FakeProvider（NDR_ALLOW_FAKE_PROVIDER=1）：只用于离线演示/验收，不代表真实模型能力。'
        $env:NDR_ALLOW_FAKE_PROVIDER = '1'
        $env:NDR_FAKE_PROVIDER_LABELS = $FakeLabels
        $env:NDR_CREDENTIAL_BACKEND = 'session'
    }
    Write-Host '执行数据库迁移…'
    & $backendInvoker.Exe @($backendInvoker.Args + @('-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'))
    if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }

    if (-not $SkipBuild) {
        if (-not (Test-Path (Join-Path $repoRoot 'frontend\node_modules'))) {
            Write-Host '安装前端依赖…'
            Push-Location (Join-Path $repoRoot 'frontend')
            try {
                & npm install
                if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
            }
            finally { Pop-Location }
        }
        Write-Host '构建前端（npm run build）…'
        & npm --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    }

    if (-not (Test-Path (Join-Path $distDir 'index.html'))) {
        throw "找不到前端构建产物：$distDir\index.html（去掉 -SkipBuild 重新构建）"
    }

    Write-Host "启动同源服务 http://127.0.0.1:$Port …"
    $env:NDR_HOST = '127.0.0.1'
    $env:NDR_PORT = "$Port"
    $env:NDR_STATIC_DIR = $distDir
    $backend = Start-Process -FilePath $backendInvoker.Exe `
        -ArgumentList @($backendInvoker.Args + @('-m', 'ndr')) `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pidFile) | Out-Null
    @([pscustomobject]@{ name = 'serve-backend'; pid = $backend.Id }) | ConvertTo-Json |
        Set-Content -LiteralPath $pidFile -Encoding utf8

    Write-Host ''
    Write-Host '已启动（单端口 = API + 前端页面）：'
    Write-Host "  应用       http://127.0.0.1:$Port/"
    Write-Host "  健康检查   http://127.0.0.1:$Port/api/health"
    Write-Host '  停止：Ctrl+C，或另开终端运行 scripts/serve.ps1 -Stop'
    Write-Host ''
    Write-Host '按 Ctrl+C 结束服务…'
    while ($true) { Start-Sleep -Seconds 3600 }
}
finally {
    Stop-ServeProcesses
    Pop-Location
}