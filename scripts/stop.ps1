# если окно dev.ps1 закрыли крестиком
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "logs\dev.pids"
if (-not (Test-Path $pidFile)) { Write-Host "Nothing to stop: logs\dev.pids not found."; exit 0 }
foreach ($id in Get-Content $pidFile) {
    if ($id.Trim()) { & taskkill /PID $id.Trim() /T /F 2>$null | Out-Null }
}
Remove-Item $pidFile
Write-Host "Stopped."
