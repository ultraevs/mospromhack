param(
    [switch]$Build,
    [string]$Merged = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Command
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Build) {
    $packets = Join-Path $root "hack_materials\sdk\tpu_framework\packets"
    if (-not (Test-Path $packets)) { Write-Error "SDK wheels not found: $packets (see hack_materials/TASK.md)" }
    docker build -f (Join-Path $root "docker\linq-sdk.Dockerfile") -t linq-sdk $packets
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not $Command) { $Command = @("bash") }

if (-not $Merged) {
    $line = Get-Content (Join-Path $root ".env") -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*MERGED_DIR\s*=' } | Select-Object -First 1
    if ($line) { $Merged = ($line -split '=', 2)[1].Trim().Trim('"') }
}
$mounts = @("-v", "${root}:/repo")
if ($Merged -and (Test-Path $Merged)) { $mounts += @("-v", "${Merged}:/merged:ro") }
docker run --rm -it @mounts -w /repo linq-sdk @Command
exit $LASTEXITCODE
