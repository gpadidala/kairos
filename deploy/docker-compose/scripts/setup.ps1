# PCAP Docker Compose bootstrap — Windows PowerShell.
# Run from the deploy/docker-compose/ directory:
#     .\scripts\setup.ps1            # default profile
#     .\scripts\setup.ps1 demo       # + synthetic feeder

param([string]$Profile = "")

$ErrorActionPreference = "Stop"
$HERE = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $HERE

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker not found on PATH"
    exit 1
}

$dc = if ((docker compose version 2>&1) -match "Docker Compose") { "docker compose" } else { "docker-compose" }

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "[pcap] created .env from .env.example — edit it if you need corporate overrides"
}

if (-not (Test-Path ".secrets")) {
    New-Item -ItemType Directory -Path ".secrets" | Out-Null
}

$profileArg = ""
if ($Profile -eq "demo" -or $Profile -eq "--demo") {
    $profileArg = "--profile demo"
    Write-Host "[pcap] demo profile — synthetic metric feeder will start"
}

Write-Host "[pcap] bringing stack up..."
Invoke-Expression "$dc --env-file .env -f compose/docker-compose.yml -p pcap $profileArg up -d --build"

Write-Host ""
Write-Host "[pcap] ── services ──"
Invoke-Expression "$dc --env-file .env -f compose/docker-compose.yml -p pcap ps"
Write-Host ""
Write-Host "[pcap] PCAP UI:   http://localhost:8090/ui"
Write-Host "[pcap] Swagger:   http://localhost:8090/docs"
Write-Host "[pcap] Grafana:   http://localhost:3000 (admin/admin)"
Write-Host "[pcap] Mimir:     http://localhost:9009"
