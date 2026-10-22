# mediamtx + backend + worker + frontend, логи в logs\. ctrl+c гасит все
param([switch]$NoWorker, [switch]$NoFrontend)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

$cfg = @{ BACKEND_PORT = "8000"; FRONTEND_PORT = "5173"; MEDIAMTX_RTSP_PORT = "8554"; MEDIAMTX_WEBRTC_PORT = "8889"; MEDIAMTX_API_PORT = "9997" }
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)$' -and $cfg.ContainsKey($Matches[1]) -and $Matches[2].Trim()) { $cfg[$Matches[1]] = $Matches[2].Trim() }
    }
} else {
    Write-Warning ".env not found, using defaults. Run scripts\setup.ps1 first."
}

$python = Join-Path $root ".venv\Scripts\python.exe"
$mediamtx = Join-Path $root "tools\mediamtx\mediamtx.exe"
foreach ($f in $python, $mediamtx) { if (-not (Test-Path $f)) { throw "Missing $f. Run scripts\setup.ps1 first." } }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "ffmpeg not found in PATH. Install: winget install Gyan.FFmpeg" }

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:MTX_RTSPADDRESS = ":" + $cfg.MEDIAMTX_RTSP_PORT
$env:MTX_WEBRTCADDRESS = ":" + $cfg.MEDIAMTX_WEBRTC_PORT
$env:MTX_APIADDRESS = "127.0.0.1:" + $cfg.MEDIAMTX_API_PORT

$procs = @()
function Start-Service-Proc([string]$name, [string]$command, [string]$cwd) {
    $log = Join-Path $logs "$name.log"
    # кавычки снаружи обязательны, cmd срезает первую и последнюю. убил час на это
    $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$command > `"$log`" 2>&1`"" -WorkingDirectory $cwd -WindowStyle Hidden -PassThru
    Write-Host ("{0,-9} pid {1,-6} log {2}" -f $name, $p.Id, $log)
    return [pscustomobject]@{ Name = $name; Proc = $p }
}

function Stop-All {
    foreach ($s in $procs) {
        if (-not $s.Proc.HasExited) { & taskkill /PID $s.Proc.Id /T /F 2>$null | Out-Null }
    }
    Remove-Item (Join-Path $logs "dev.pids") -ErrorAction SilentlyContinue
}

try {
    $procs += Start-Service-Proc "mediamtx" "`"$mediamtx`" `"$(Join-Path $root 'mediamtx.yml')`"" $root
    $procs += Start-Service-Proc "backend" "`"$python`" -m uvicorn backend.app.main:app --factory --host 0.0.0.0 --port $($cfg.BACKEND_PORT)" $root
    if (-not $NoWorker) { $procs += Start-Service-Proc "worker" "`"$python`" -m worker.app.main" $root }
    if (-not $NoFrontend) { $procs += Start-Service-Proc "frontend" "npm run dev" (Join-Path $root "frontend") }
    ($procs | ForEach-Object { $_.Proc.Id }) -join "`n" | Out-File -Encoding ascii (Join-Path $logs "dev.pids")

    Write-Host ""
    Write-Host "Open http://localhost:$($cfg.FRONTEND_PORT)   (health: http://localhost:$($cfg.BACKEND_PORT)/api/health)"
    Write-Host "Ctrl+C to stop everything."
    while ($true) {
        Start-Sleep -Seconds 1
        foreach ($s in $procs) {
            if ($s.Proc.HasExited -and -not $s.Reported) {
                Write-Warning "$($s.Name) exited with code $($s.Proc.ExitCode). See logs\$($s.Name).log"
                $s | Add-Member -NotePropertyName Reported -NotePropertyValue $true -Force
            }
        }
    }
} finally {
    Write-Host "Stopping..."
    Stop-All
}
