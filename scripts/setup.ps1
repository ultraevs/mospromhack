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

# mediamtx качаем в tools/, в гит не кладем
$mtxVersion = "v1.21.1"
$mtxExe = Join-Path $root "tools\mediamtx\mediamtx.exe"
if (-not (Test-Path $mtxExe)) {
    Write-Host "Downloading MediaMTX $mtxVersion..."
    $zip = Join-Path $root "tools\mediamtx.zip"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri "https://github.com/bluenviron/mediamtx/releases/download/$mtxVersion/mediamtx_${mtxVersion}_windows_amd64.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath (Join-Path $root "tools\mediamtx") -Force
    Remove-Item $zip
}
Write-Host ("MediaMTX: " + (& $mtxExe --version))

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host ("ffmpeg: " + ((ffmpeg -version | Select-Object -First 1)))
} else {
    Write-Warning "ffmpeg not found in PATH. Install it: winget install Gyan.FFmpeg  (or: choco install ffmpeg), then reopen the terminal."
}

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "Installing frontend dependencies..."
    Push-Location (Join-Path $root "frontend")
    npm install --no-fund --no-audit
    Pop-Location
} else {
    Write-Warning "npm not found. Install Node.js 20+ from nodejs.org, then run: cd frontend; npm install"
}

Write-Host "Checking ONNX Runtime device..."
& $venvPython -m worker.app.check_device
