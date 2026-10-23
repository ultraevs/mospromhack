param(
    [switch]$Build,
    [string]$Script = "scripts/linq_stand.py",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Args_
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$ovpn = Join-Path $root "hack_materials\vpn\Hakathon.ovpn"
$creds = Join-Path $root "hack_materials\.env-creds"
foreach ($f in @($ovpn, $creds)) { if (-not (Test-Path $f)) { Write-Error "Missing $f" } }
$wheel = Get-ChildItem (Join-Path $root "hack_materials\stand") -Filter "tpu_pycloud_format-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
$pyPath = if ($wheel) { "/repo/hack_materials/stand/$($wheel.Name)" } else { "" }

if ($Build) {
    docker build -f (Join-Path $root "docker\linq-vpn.Dockerfile") -t linq-vpn (Join-Path $root "docker")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
docker run --rm --cap-add NET_ADMIN --device /dev/net/tun `
    -v "${ovpn}:/vpn/client.ovpn:ro" -v "${creds}:/creds:ro" -v "${root}:/repo" -w /repo `
    -e "PYTHONPATH=$pyPath" `
    linq-vpn python $Script @Args_
exit $LASTEXITCODE
