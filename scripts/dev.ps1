# 开发启动脚本：检查依赖 → 迁移（若已建立）→ 后台启动后端与前端 → 打印访问地址。
# 只处理本脚本启动的进程；Ctrl+C 或 -Stop 时结束它们。
#
#   pwsh -File scripts/dev.ps1            # 启动后端(8765) + 前端(5173)
#   pwsh -File scripts/dev.ps1 -SkipFrontend
#   pwsh -File scripts/dev.ps1 -Stop      # 结束上次由本脚本启动的进程
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
# 项目正文与文档都是 UTF-8：避免 Windows 默认编码（GBK）导致控制台乱码或读文件失败。
$env:PYTHONUTF8 = '1'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot 'data\run\dev-pids.json'
$script:started = @()

function Save-Started {
    if ($script:started.Count -eq 0) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pidFile) | Out-Null
    $script:started | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8
}

function Stop-DevProcesses {
    if (-not (Test-Path $pidFile)) { return }
    $recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($entry in @($recorded)) {
        if (-not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) { continue }
        Write-Host "停止 $($entry.name) (PID $($entry.pid))"
        # 先按进程树结束（npm → node、uv → python 会派生子进程），失败再退化为单进程结束。
        & taskkill /PID $entry.pid /T /F *> $null
        if ($LASTEXITCODE -ne 0) {
            Stop-Process -Id $entry.pid -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ($Stop) {
    Stop-DevProcesses
    Write-Host '已按记录停止由 dev.ps1 启动的进程。'
    exit 0
}

# 优先使用 uv；受限环境中 uv 缓存不可访问时回退到已同步的 venv。
function Get-BackendInvoker {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --project backend python -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Exe = 'uv'; Args = @('run', '--project', 'backend', 'python') }
        }
        Write-Warning 'uv run 不可用（例如受限沙箱无法访问 uv 缓存），回退到 backend\.venv。'
    }
    $venvPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return [pscustomobject]@{ Exe = $venvPython; Args = @() }
    }
    throw '未找到后端环境，请先运行：uv sync --project backend --all-groups'
}

foreach ($tool in @('uv', 'node', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "缺少依赖：$tool。请先安装并确保在 PATH 中。"
    }
}

$backendInvoker = Get-BackendInvoker

Push-Location $repoRoot
try {
    Write-Host '同步后端依赖（uv sync --project backend --all-groups）…'
    & uv sync --project backend --all-groups
    if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }

    if (Test-Path (Join-Path $repoRoot 'backend\alembic.ini')) {
        Write-Host '执行数据库迁移…'
        & $backendInvoker.Exe @($backendInvoker.Args + @('-m', 'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'))
        if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败' }
    }
    else {
        Write-Host '尚未建立 migrations（T01 引入），跳过 alembic。'
    }

    if (-not $SkipFrontend) {
        if (-not (Test-Path (Join-Path $repoRoot 'frontend\node_modules'))) {
            Write-Host '安装前端依赖（在 frontend 目录执行 npm install）…'
            Push-Location (Join-Path $repoRoot 'frontend')
            try {
                & npm install
                if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
            }
            finally { Pop-Location }
        }
    }

    if (-not $SkipBackend) {
        Write-Host '启动后端 127.0.0.1:8765 …'
        $backend = Start-Process -FilePath $backendInvoker.Exe `
            -ArgumentList @($backendInvoker.Args + @('-m', 'ndr')) `
            -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
        $script:started += [pscustomobject]@{ name = 'backend'; pid = $backend.Id }
        Save-Started
    }

    if (-not $SkipFrontend) {
        Write-Host '启动前端 127.0.0.1:5173 …'
        # Get-Command npm 通常解析到 npm.ps1；Start-Process 需要 npm.cmd。
        $npmDir = Split-Path (Get-Command npm).Source -Parent
        $npmCmd = Join-Path $npmDir 'npm.cmd'
        if (-not (Test-Path -LiteralPath $npmCmd)) { $npmCmd = (Get-Command npm.cmd).Source }
        $frontend = Start-Process -FilePath $npmCmd `
            -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173') `
            -WorkingDirectory (Join-Path $repoRoot 'frontend') -WindowStyle Hidden -PassThru
        $script:started += [pscustomobject]@{ name = 'frontend'; pid = $frontend.Id }
        Save-Started
    }

    Write-Host ''
    Write-Host '已启动：'
    if (-not $SkipBackend) { Write-Host '  后端 API      http://127.0.0.1:8765/api/health' }
    if (-not $SkipFrontend) { Write-Host '  前端开发页面  http://127.0.0.1:5173' }
    Write-Host '  停止：Ctrl+C，或另开终端运行 scripts/dev.ps1 -Stop'
    Write-Host ''
    Write-Host '按 Ctrl+C 结束本次启动的两个进程…'
    while ($true) { Start-Sleep -Seconds 3600 }
}
finally {
    Stop-DevProcesses
    Pop-Location
}
