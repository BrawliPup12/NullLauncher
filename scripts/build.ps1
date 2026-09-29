$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-dev.txt
& $Python scripts\sync_version_info.py
& $Python -m PyInstaller --clean --noconfirm NullLauncher.spec

Write-Host ""
Write-Host "Build complete: $Root\dist\NullLauncher.exe"
