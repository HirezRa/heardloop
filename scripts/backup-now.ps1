# Runs one backup now (same as the nightly one) and prints the result.
# Usage (from anywhere):  powershell -ExecutionPolicy Bypass -File scripts/backup-now.ps1
# Docker writes progress to stderr; that must not stop the script. Exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker compose run --rm backup now
$exit = $LASTEXITCODE

# The manifest of the newest set (success or failure), read through the backup container.
$json = docker compose run -T --rm --no-deps --entrypoint cat backup /backups/latest.json 2>$null
if ($LASTEXITCODE -ne 0 -or -not $json) {
    Write-Error "Backup finished with exit code $exit and no latest.json was found."
    exit 1
}
$m = ($json -join "`n") | ConvertFrom-Json
$mb = [math]::Round(($m.db_bytes + $m.media_bytes) / 1MB, 2)
Write-Host ""
Write-Host "Set:       $($m.set)"
Write-Host "Status:    $($m.status)"
Write-Host "Finished:  $($m.finished_at)"
Write-Host "Size:      $mb MB (database $([math]::Round($m.db_bytes / 1MB, 2)) MB, media $([math]::Round($m.media_bytes / 1MB, 2)) MB)"
Write-Host "Encrypted: $($m.encrypted)"
if ($m.error) { Write-Host "Error:     $($m.error)" }
if ($m.status -ne 'ok' -or $exit -ne 0) { exit 1 }
Write-Host "Files are in BACKUP_DIR (default: ./backups). .env is not included: keep your own copy of HEARDLOOP_TOKEN_KEY."
