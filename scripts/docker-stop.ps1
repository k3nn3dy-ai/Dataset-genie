# Stop the Dataset Genie container (Windows PowerShell). Data is kept in the `genie-data` volume.
#   .\scripts\docker-stop.ps1           # stop and remove the container
#   .\scripts\docker-stop.ps1 -Reset    # also delete the database + secrets volume (exports\ is untouched)
param([switch]$Reset)
# 'Continue', not 'Stop': in Windows PowerShell 5.1 a native command writing to stderr (docker does)
# would otherwise throw when its output is redirected. Exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..')
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host 'Docker is not running; nothing to stop.'; exit 0 }
if ($Reset) {
    $ans = Read-Host 'This deletes the database and stored tokens (exports\ is kept). Continue? [y/N]'
    if ($ans -match '^(y|yes)$') { docker compose down -v; Write-Host 'Dataset Genie stopped and data reset.' }
    else { Write-Host 'Cancelled.' }
} else {
    docker compose down
    Write-Host 'Dataset Genie stopped. Data kept; start again with scripts\docker-start.ps1'
}
