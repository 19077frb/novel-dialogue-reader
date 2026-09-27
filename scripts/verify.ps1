# 验证脚本：只运行已建立的检查；任何一项失败即返回非零。
#
#   pwsh -File scripts/verify.ps1
#   pwsh -File scripts/verify.ps1 -SkipFrontend
[CmdletBinding()]
param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
# 项目正文与文档都是 UTF-8：避免 Windows 默认编码（GBK）造成误判。
$env:PYTHONUTF8 = '1'

$failures = @()

function Invoke-Check {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ''
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    try {
        & $Body
        if ($LASTEXITCODE -ne 0) { throw "退出码 $LASTEXITCODE" }
        Write-Host "OK: $Name" -ForegroundColor Green
    }
    catch {
        Write-Warning "失败：$Name —— $($_.Exception.Message)"
        $script:failures += $Name
    }
}

# 优先使用文档规定的 uv 命令；受限环境中 uv 缓存不可访问时回退到已同步的 venv。
function Get-BackendInvoker {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run --project backend python -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{ Exe = 'uv'; Prefix = @('run', '--project', 'backend', 'python') }
        }
        Write-Warning 'uv run 不可用（例如受限沙箱无法访问 uv 缓存），回退到 backend\.venv。'
    }
    $venvPython = Join-Path $repoRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        return [pscustomobject]@{ Exe = $venvPython; Prefix = @() }
    }
    throw '未找到后端环境，请先运行：uv sync --project backend --all-groups'
}

$backend = Get-BackendInvoker

Invoke-Check 'ruff check' {
    & $backend.Exe @($backend.Prefix + @('-m', 'ruff', 'check', 'backend/src', 'backend/tests', 'backend/scripts'))
}

Invoke-Check 'pytest' {
    & $backend.Exe @($backend.Prefix + @('-m', 'pytest', 'backend/tests'))
}

Invoke-Check 'openapi 与 docs/openapi.json 一致' {
    & $backend.Exe @($backend.Prefix + @('backend/scripts/export_openapi.py', '--output', 'docs/openapi.json', '--check'))
}

if (-not $SkipFrontend) {
    Invoke-Check 'frontend typecheck' {
        & npm --prefix frontend run typecheck
    }

    Invoke-Check 'frontend API 类型与 docs/openapi.json 一致' {
        & npm --prefix frontend run check:api
    }

    Invoke-Check 'frontend unit tests' {
        & npm --prefix frontend run test -- --run
    }

    Invoke-Check 'frontend build' {
        & npm --prefix frontend run build
    }
}

Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host "验证失败：$($failures -join '、')" -ForegroundColor Red
    exit 1
}
Write-Host '全部检查通过。' -ForegroundColor Green
exit 0
