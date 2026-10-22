# powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating .venv (Python 3.12)..."
    py -3.12 -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 not found. Install it from python.org and re-run." }
}
Write-Host "Installing Python dependencies..."
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "Created .env from .env.example - review it."
}

foreach ($d in "data\videos", "data\media", "models", "tools", "logs") {
    New-Item -ItemType Directory -Force (Join-Path $root $d) | Out-Null
}

& (Join-Path $PSScriptRoot "sync_models.ps1")

Write-Host "Checking ONNX Runtime device..."
& $venvPython -m worker.app.check_device
