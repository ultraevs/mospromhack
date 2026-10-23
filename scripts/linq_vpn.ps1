param(
    [ValidateSet("up", "down", "exec", "status")][string]$Action = "status",
    [switch]$Build,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Command
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$name = "linq-vpn"

if ($Action -eq "up") {
    $ovpn = Join-Path $root "hack_materials\vpn\Hakathon.ovpn"
    $creds = Join-Path $root "hack_materials\.env-creds"
    foreach ($f in @($ovpn, $creds)) { if (-not (Test-Path $f)) { Write-Error "Missing $f" } }
    if ($Build) {
        docker build -f (Join-Path $root "docker\linq-vpn.Dockerfile") -t linq-vpn (Join-Path $root "docker")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $wheel = Get-ChildItem (Join-Path $root "hack_materials\stand") -Filter "tpu_pycloud_format-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    $pyPath = if ($wheel) { "/repo/hack_materials/stand/$($wheel.Name)" } else { "" }
    $mounts = @("-v", "${ovpn}:/vpn/client.ovpn:ro", "-v", "${creds}:/creds:ro", "-v", "${root}:/repo")
    $line = Get-Content (Join-Path $root ".env") -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*MERGED_DIR\s*=' } | Select-Object -First 1
    $merged = if ($line) { ($line -split '=', 2)[1].Trim().Trim('"') } else { "" }
    if ($merged -and (Test-Path $merged)) { $mounts += @("-v", "${merged}:/merged:ro", "-e", "MERGED_DIR=/merged") }
    if (docker ps -aq --filter "name=^$name$") { docker rm -f $name | Out-Null }
    docker run -d --name $name --cap-add NET_ADMIN --device /dev/net/tun --add-host host.docker.internal:host-gateway `
        @mounts -w /repo -e "PYTHONPATH=$pyPath" linq-vpn sleep infinity | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        $log = docker logs $name 2>&1 | Out-String
        if ($log -match "VPN: ") { break }
        Start-Sleep 1
    }
    docker logs $name 2>&1 | Select-String "VPN"
    exit 0
}
if ($Action -eq "down") { docker rm -f $name | Out-Null; exit 0 }
if ($Action -eq "status") { docker ps --filter "name=$name" --format "{{.Names}} {{.Status}}"; exit 0 }
docker exec -i $name @Command
exit $LASTEXITCODE
