# Start Dataset Genie in Docker (Windows PowerShell). Mac / Linux users: scripts/docker-start.sh
#   powershell -ExecutionPolicy Bypass -File scripts\docker-start.ps1
#   $env:GENIE_PORT = 9000; .\scripts\docker-start.ps1
# 'Continue', not 'Stop': in Windows PowerShell 5.1 a native command writing to stderr (docker does)
# would otherwise throw when its output is redirected. Exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker is not installed. Install Docker Desktop: https://www.docker.com/products/docker-desktop/'
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is installed but not running. Start Docker Desktop, wait for it to say 'running', then try again."
    exit 1
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The 'docker compose' plugin is missing. Update Docker Desktop."
    exit 1
}

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example. Add your OpenRouter key there, or enter it later in Settings.'
}
$port = $env:GENIE_PORT
if (-not $port) {
    $line = Get-Content '.env' -ErrorAction SilentlyContinue | Where-Object { $_ -match '^GENIE_PORT=(.+)$' } | Select-Object -First 1
    if ($line) { $port = ($line -split '=', 2)[1].Trim() }
}
if (-not $port) { $port = '8765' }
$env:GENIE_PORT = $port
New-Item -ItemType Directory -Force -Path 'exports' | Out-Null

Write-Host 'Building and starting Dataset Genie (the first build takes a few minutes) ...'
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$url = "http://localhost:$port"
for ($i = 0; $i -lt 120; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) {
            Write-Host "Ready: $url"
            Write-Host "Exports land in $(Join-Path (Get-Location) 'exports'). Stop with scripts\docker-stop.ps1"
            Start-Process $url
            exit 0
        }
    } catch { }
    Start-Sleep -Seconds 1
}
Write-Host 'The app did not become healthy within two minutes. Last log lines:'
docker compose logs --tail 40 genie
exit 1
