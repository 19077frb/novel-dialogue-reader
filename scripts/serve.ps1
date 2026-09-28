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

function Stop-ListenerOnPort([int]$port, [string[]]$allowed) {
    # uv run 会把 python 变成非子进程：停止时按端口兜底清理，且只动白名单里的进程名。
    $owners = @()
    try {
        $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
    }
    catch { $owners = @() }
    if ($owners.Count -eq 0) {
        $owners = @(netstat -ano | Select-String -Pattern (":{0}\s" -f $port) |
            ForEach-Object { ($_ -split '\s+')[-1] } |
            Where-Object { $_ -match '^\d+$' } | Select-Object -Unique)
    }
    foreach ($owner in @($owners)) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        if ($allowed -notcontains $proc.ProcessName) { continue }
        Write-Host ("停止端口 {0} 上的 {1} (PID {2})" -f $port, $proc.ProcessName, $owner)
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
}

function Get-BackendServerInvoker([string]$repoRoot) {
    # 长期运行的服务优先直接用 venv 解释器：记录的 PID 就是服务本身，停止时能杀干净。
    $venvPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return [pscustomobject]@{ Exe = $venvPython; Args = @() }
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        return [pscustomobject]@{ Exe = 'uv'; Args = @('run', '--project', 'backend', 'python') }
    }
    throw '未找到后端运行环境，请先运行：uv sync --project backend --all-groups'
}

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
    # 兜底：uv run 可能留下孤儿 python，按端口再清一次
    Stop-ListenerOnPort $Port @('python', 'pythonw', 'uv')
}

if ($Stop) {
    Stop-ServeProcesses
    Write-Host '已按记录停止由 serve.ps1 启动的进程。'
    exit 0
}

function Get-BackendInvoker {
    # 优先直接用 venv 里的解释器：这样记录的 PID 就是真正在跑的服务进程，
    # 停止脚本（taskkill /T）与「后端退出就收尾」的判断才准确；uv 仍用于同步依赖。
    $venvPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return [pscustomobject]@{ Exe = $venvPython; Args = @() }
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --project backend python -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Exe = 'uv'; Args = @('run', '--project', 'backend', 'python') }
        }
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

    if ($DataDir) {
        # -DataDir 指向的新目录由脚本创建（首次使用也要能直接启动）
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
        $env:NDR_DATA_DIR = $DataDir
    }
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
    $serverInvoker = Get-BackendServerInvoker $repoRoot
    $backend = Start-Process -FilePath $serverInvoker.Exe `
        -ArgumentList @($serverInvoker.Args + @('-m', 'ndr')) `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pidFile) | Out-Null
    @([pscustomobject]@{ name = 'serve-backend'; pid = $backend.Id }) | ConvertTo-Json |
        Set-Content -LiteralPath $pidFile -Encoding utf8

    # 等后端真的可用再宣布成功：端口被占用/迁移失败时给出明确错误，而不是假装启动成功。
    $healthUrl = "http://127.0.0.1:$Port/api/health"
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        Stop-ServeProcesses
        throw "服务在 20 秒内没有就绪：请检查端口 $Port 是否被占用（可换端口：scripts/serve.ps1 -Port 8800）"
    }

    Write-Host ''
    Write-Host '已启动（单端口 = API + 前端页面）：'
    Write-Host "  应用       http://127.0.0.1:$Port/"
    Write-Host "  健康检查   http://127.0.0.1:$Port/api/health"
    Write-Host '  停止：Ctrl+C，或另开终端运行 scripts/serve.ps1 -Stop'
    Write-Host ''
    Write-Host '按 Ctrl+C 结束服务…'
    # 后端进程退出（例如被 stop.bat 结束）就跟着收尾，避免留下一个「假装在跑」的窗口。
    while (-not $backend.HasExited) { Start-Sleep -Seconds 2 }
    Write-Host '后端已退出，本次服务结束。'
}
finally {
    Stop-ServeProcesses
    Pop-Location
}