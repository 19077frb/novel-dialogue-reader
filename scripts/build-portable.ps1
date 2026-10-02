# Build only. GitHub Actions publishes the tested ZIP as a Release asset.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) {
    throw 'Build the portable edition on 64-bit Windows.'
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $repoRoot
try {
    & uv sync --project backend --locked --all-groups
    if ($LASTEXITCODE -ne 0) { throw 'Dependency synchronization failed.' }
    & npm --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
    & npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    $python = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
    $version = (& $python -c 'from ndr import __version__; print(__version__)').Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version.' }
    # Unique build root prevents accidentally including leftovers or replacing any user files.
    $buildRoot = Join-Path $repoRoot ('dist/portable-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $buildRoot | Out-Null
    $argsForBuild = @(
        '-m', 'PyInstaller', '--onedir', '--console', '--noupx', '--name', 'NovelDialogueReader',
        '--distpath', (Join-Path $buildRoot 'app'), '--workpath', (Join-Path $buildRoot 'work'),
        '--specpath', $buildRoot, '--paths', (Join-Path $repoRoot 'backend/src'),
        '--add-data', ((Join-Path $repoRoot 'frontend/dist') + ':frontend/dist'),
        '--add-data', ((Join-Path $repoRoot 'backend/alembic.ini') + ':backend'),
        '--add-data', ((Join-Path $repoRoot 'backend/migrations') + ':backend/migrations'),
        '--collect-submodules', 'uvicorn', '--collect-submodules', 'keyring.backends',
        '--copy-metadata', 'keyring', '--hidden-import', 'sqlalchemy.dialects.sqlite.pysqlite',
        '--exclude-module', 'pytest', '--exclude-module', 'ruff',
        'backend/scripts/portable_entry.py'
    )
    & $python @argsForBuild
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    $appDir = Join-Path $buildRoot 'app/NovelDialogueReader'
    Copy-Item -LiteralPath (Join-Path $repoRoot 'LICENSE') -Destination $appDir
    Copy-Item -LiteralPath (Join-Path $repoRoot 'docs/PORTABLE_README.txt') -Destination $appDir
    $licenseDir = Join-Path $appDir 'THIRD_PARTY_LICENSES'
    New-Item -ItemType Directory -Path $licenseDir | Out-Null
    $sitePackages = Join-Path $repoRoot 'backend/.venv/Lib/site-packages'
    foreach ($distribution in Get-ChildItem -LiteralPath $sitePackages -Directory -Filter '*.dist-info') {
        $files = @(Get-ChildItem -LiteralPath $distribution.FullName -Recurse -File | Where-Object {
            $_.Name -match '^(LICENSE|COPYING|NOTICE|AUTHORS)' -or $_.FullName -match '[\\/]licenses[\\/]'
        })
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($sitePackages.Length + 1)
            $target = Join-Path $licenseDir $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $target
        }
    }
    $pythonBase = (& $python -c 'import sys; print(sys.base_prefix)').Trim()
    $pythonLicense = Join-Path $pythonBase 'LICENSE.txt'
    if (-not (Test-Path -LiteralPath $pythonLicense)) { throw 'Python license file is missing.' }
    Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseDir 'Python-LICENSE.txt')
    # Explicit sources above: never include repository data, .env, logs, .git or internal reports.
    $zip = Join-Path $buildRoot "NovelDialogueReader-$version-windows-x64.zip"
    Compress-Archive -LiteralPath $appDir -DestinationPath $zip -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($zip))" | Set-Content -LiteralPath "$zip.sha256" -Encoding ascii
    Write-Host "Portable package: $zip"
    if ($env:GITHUB_OUTPUT) {
        "zip=$zip" | Add-Content -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8
        "checksum=$zip.sha256" | Add-Content -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8
        "version=$version" | Add-Content -LiteralPath $env:GITHUB_OUTPUT -Encoding utf8
    }
} finally {
    Pop-Location
}
