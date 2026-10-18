# Restores a backup set into the running stack: stops api and worker, restores the database and the media
# files, starts api and worker again. This REPLACES the current data.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/restore.ps1 heardloop-20261018T010000Z
#         add -Yes to skip the question.
# Encrypted set: set $env:BACKUP_PASSPHRASE to the passphrase used for that backup first (it is read from
# .env otherwise). Without the original HEARDLOOP_TOKEN_KEY in .env, LinkedIn tokens and two-step secrets in
# the restored database cannot be read: see docs/GUIDE.md, section 10.
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Set,
    [switch]$Yes
)
# Docker writes progress to stderr; that must not stop the script. Exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($Set -notmatch '^heardloop-\d{8}T\d{6}Z$') {
    Write-Error "Set name must look like heardloop-20261018T010000Z (see the files in your backup folder)."
    exit 2
}
if (-not $Yes) {
    Write-Host "This replaces the database and all media files with the contents of $Set."
    $answer = Read-Host "Type yes to continue"
    if ($answer -ne 'yes') { Write-Host "Cancelled."; exit 1 }
}

Write-Host "Stopping api and worker..."
docker compose stop api worker
if ($LASTEXITCODE -ne 0) { Write-Error "Could not stop api and worker."; exit 1 }

# The restore service: media volume read-write, root with three capabilities (the media files belong to the
# application user, the backup files to the backup user). The container is removed afterwards (--rm).
docker compose run --rm -e CONFIRM_RESTORE=yes restore $Set
if ($LASTEXITCODE -ne 0) {
    Write-Error "Restore failed. api and worker are still stopped. Fix the problem above, then run this script again or start them with: docker compose start api worker"
    exit 1
}

Write-Host "Starting api and worker..."
docker compose start api worker
if ($LASTEXITCODE -ne 0) { Write-Error "Restore finished, but api and worker did not start. See: docker compose logs api worker"; exit 1 }
Write-Host "Done. Open http://localhost:8380 and check your data."
