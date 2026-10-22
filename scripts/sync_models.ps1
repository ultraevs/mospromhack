param([string]$RunsDir = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $RunsDir) {
    $envFile = Join-Path $root ".env"
    if (-not (Test-Path $envFile)) { $envFile = Join-Path $root ".env.example" }
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*TRAIN_RUNS_DIR\s*=' } | Select-Object -First 1
    if ($line) { $RunsDir = ($line -split '=', 2)[1].Trim().Trim('"') }
}
if (-not $RunsDir -or -not (Test-Path $RunsDir)) {
    Write-Error "Training runs folder not found: '$RunsDir'. Set TRAIN_RUNS_DIR in .env or pass -RunsDir."
}

foreach ($model in "ppe", "hazard") {
    $src = Join-Path $RunsDir $model
    $dst = Join-Path $root "models\$model"
    $onnx = Join-Path $src "weights\best.onnx"
    $yaml = Join-Path $src "model.yaml"
    foreach ($f in $onnx, $yaml) {
        if (-not (Test-Path $f)) { Write-Error "Missing file: $f" }
    }
    New-Item -ItemType Directory -Force $dst | Out-Null
    Copy-Item $onnx (Join-Path $dst "best.onnx") -Force
    Copy-Item $yaml (Join-Path $dst "model.yaml") -Force
    $hash = (Get-FileHash (Join-Path $dst "best.onnx") -Algorithm SHA256).Hash.Substring(0, 12)
    Write-Host ("{0,-7} -> {1}  best.onnx {2:N1} MB  sha256 {3}" -f $model, $dst, ((Get-Item $onnx).Length / 1MB), $hash)
}
