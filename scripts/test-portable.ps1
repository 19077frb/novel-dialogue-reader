[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ZipPath)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot 'backend/.venv/Scripts/python.exe'
& $python (Join-Path $repoRoot 'backend/scripts/test_portable.py') --zip $ZipPath
if ($LASTEXITCODE -ne 0) { throw 'Portable EXE acceptance checks failed.' }
