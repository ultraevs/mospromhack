param([switch]$Docker, [switch]$Cpu, [switch]$NoBrowser, [int]$MaxCameras = 4)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$cfg = @{ BACKEND_PORT = "8000"; FRONTEND_PORT = "5173"; INTERNAL_TOKEN = "change-me" }
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host "Created .env from .env.example."
}
foreach ($line in Get-Content $envFile -Encoding UTF8) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)$' -and $cfg.ContainsKey($Matches[1]) -and $Matches[2].Trim()) { $cfg[$Matches[1]] = $Matches[2].Trim() }
}
$api = "http://127.0.0.1:$($cfg.BACKEND_PORT)"
$ui = "http://localhost:$($cfg.FRONTEND_PORT)"

foreach ($d in "data\videos", "data\media", "models", "logs") { New-Item -ItemType Directory -Force (Join-Path $root $d) | Out-Null }

if (-not (Test-Path (Join-Path $root "models\ppe\best.onnx"))) {
    Write-Host "Models are missing, running sync_models.ps1..."
    & (Join-Path $PSScriptRoot "sync_models.ps1")
    if (-not (Test-Path (Join-Path $root "models\ppe\best.onnx"))) { throw "models\ppe\best.onnx not found. Set TRAIN_RUNS_DIR in .env or copy the models by hand (see README)." }
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not $Docker -and -not (Test-Path $python)) {
    Write-Host "First run: installing dependencies (scripts\setup.ps1)..."
    & (Join-Path $PSScriptRoot "setup.ps1")
}

$videoExt = ".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"
$videos = @(Get-ChildItem (Join-Path $root "data\videos") -File | Where-Object { $videoExt -contains $_.Extension.ToLower() })
if ($videos.Count -eq 0) {
    if (Test-Path $python) {
        Write-Host "No videos in data\videos, building a test clip from the css test images..."
        & $python (Join-Path $PSScriptRoot "make_test_video.py")
        $videos = @(Get-ChildItem (Join-Path $root "data\videos") -File | Where-Object { $videoExt -contains $_.Extension.ToLower() })
    }
    if ($videos.Count -eq 0) { throw "Put at least one video file into data\videos and run again." }
}

$seed = {
    param($api, $ui, $names, $openBrowser, $token)
    $h = @{ "X-Internal-Token" = $token }
    $ready = $false
    foreach ($i in 1..180) {
        try { Invoke-RestMethod "$api/api/health" -Headers $h -TimeoutSec 2 | Out-Null; $ready = $true; break } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { return "backend did not answer on $api in 3 minutes" }
    $existing = @((Invoke-RestMethod "$api/api/cameras" -Headers $h) | Where-Object { $_ -and $_.id })
    $added = 0
    if ($existing.Count -eq 0) {
        foreach ($name in $names) {
            $body = @{ name = [IO.Path]::GetFileNameWithoutExtension($name); source_type = "file"; source = $name; required_ppe = @("helmet") } | ConvertTo-Json
            $cam = Invoke-RestMethod "$api/api/cameras" -Headers $h -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
            if ($name -eq "factory_floor.mp4") {
                # restricted aisle between boxes and table, one person walks through it at ~1-3 s
                $zone = '{"name":"\u041f\u0440\u043e\u0445\u043e\u0434 \u0437\u0430\u043f\u0440\u0435\u0449\u0451\u043d","type":"restricted","polygon":[[0.565,0.495],[0.648,0.495],[0.655,0.58],[0.56,0.58]]}'
                Invoke-RestMethod "$api/api/cameras/$($cam.id)/zones" -Headers $h -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($zone)) | Out-Null
            }
            $added++
        }
    }
    if ($openBrowser) { Start-Process $ui }
    return "cameras before: $($existing.Count), added: $added"
}
$names = @($videos | Select-Object -First $MaxCameras | ForEach-Object { $_.Name })

if ($Docker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "docker not found. Install Docker Desktop or run without -Docker." }
    docker info --format "{{.ServerVersion}}" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker is not running. Start Docker Desktop and run again." }
    $files = @("-f", "docker-compose.yml")
    if (-not $Cpu) { $files += @("-f", "docker-compose.gpu.yml") }
    Write-Host "Building and starting containers (the first build downloads several GB)..."
    docker compose @files up -d --build
    if ($LASTEXITCODE -ne 0) {
        if ($Cpu) { throw "docker compose failed" }
        throw "docker compose failed. If the error mentions the nvidia driver or GPU, run again with -Cpu."
    }
    Write-Host (& $seed $api $ui $names (-not $NoBrowser) $cfg.INTERNAL_TOKEN)
    Write-Host ""
    Write-Host "Open $ui   (health: $api/api/health)"
    Write-Host "Logs: docker compose logs -f worker      Stop: docker compose down"
} else {
    $job = Start-Job -ScriptBlock $seed -ArgumentList $api, $ui, $names, (-not $NoBrowser), $cfg.INTERNAL_TOKEN
    try {
        & (Join-Path $PSScriptRoot "dev.ps1")
    } finally {
        Receive-Job $job -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "demo: $_" }
        Remove-Job $job -Force -ErrorAction SilentlyContinue
    }
}
