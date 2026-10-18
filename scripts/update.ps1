# Updates Heardloop on this machine to a release tag (or the newest vX.Y.Z).
# Usage (from the repository root, or any path - the script cds to the root):
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1 -Tag v0.9.0 -Yes
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1 -WhatIf
# Never prints environment variables. Never touches .env.
param(
    [string]$Tag = "",
    [switch]$Yes,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$msg) {
    Write-Error $msg
    exit 1
}

if (-not (Test-Path (Join-Path $root "VERSION"))) {
    Fail "Run this script from a Heardloop checkout (VERSION file missing)."
}

$currentVersion = (Get-Content -Raw VERSION).Trim()
$currentTag = "v$currentVersion"

# Resolve target tag early so -WhatIf can print a plan even on a dirty tree.
if (-not $WhatIf) {
    Write-Host "Fetching tags..."
    git fetch --tags --prune
    if ($LASTEXITCODE -ne 0) { Fail "git fetch failed." }
}

if (-not $Tag) {
    $tags = git tag -l "v*.*.*" --sort=-v:refname
    $Tag = ($tags | Select-Object -First 1)
    if (-not $Tag) { $Tag = $currentTag }
}
if ($Tag -notmatch '^v\d+\.\d+\.\d+$') {
    Fail "Tag must look like v1.2.3 (got: $Tag)."
}

$releaseUrl = "https://github.com/HirezRa/heardloop/releases/tag/$Tag"
Write-Host "Current: $currentTag"
Write-Host "Target:  $Tag"
Write-Host "Release: $releaseUrl"

if ($WhatIf) {
    Write-Host ""
    Write-Host "WhatIf plan (no changes):"
    Write-Host "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
    Write-Host "  2. git fetch --tags --prune"
    Write-Host "  3. scripts/backup-now.ps1 (abort unless status=ok)"
    Write-Host "  4. git checkout $Tag"
    Write-Host "  5. docker compose build --build-arg GIT_SHA=<short> ; docker compose up -d"
    Write-Host "  6. Wait up to 120s for api healthy; GET /api/v1/health; print VERSION"
    Write-Host "On failure after checkout: git checkout $currentTag ; docker compose up -d --build"
    exit 0
}

# Tracked changes block the update; common local-only files are fine.
$porcelain = git status --porcelain
$blocked = @()
foreach ($line in ($porcelain -split "`n")) {
    if (-not $line.Trim()) { continue }
    $path = $line.Substring(3).Trim().Trim('"')
    if ($path -in @('docker-compose.override.yml', '.env') -or $path -like 'backups/*' -or $path -like 'backups\*') {
        continue
    }
    $blocked += $line
}
if ($blocked.Count -gt 0) {
    Fail "Working tree has tracked changes. Commit or stash them first:`n$($blocked -join "`n")"
}

if (-not $Yes) {
    $answer = Read-Host "Continue? (y/N)"
    if ($answer -notin @('y', 'Y', 'yes', 'YES')) {
        Write-Host "Aborted."
        exit 0
    }
}

Write-Host "Running backup..."
& (Join-Path $PSScriptRoot "backup-now.ps1")
if ($LASTEXITCODE -ne 0) { Fail "Backup did not report ok; update aborted." }

$backupSet = ""
try {
    $json = docker compose run -T --rm --no-deps --entrypoint cat backup /backups/latest.json 2>$null
    if ($json) {
        $m = ($json -join "`n") | ConvertFrom-Json
        $backupSet = [string]$m.set
    }
} catch { }

Write-Host "Checking out $Tag..."
git checkout $Tag
if ($LASTEXITCODE -ne 0) { Fail "git checkout $Tag failed." }

$sha = (git rev-parse --short HEAD).Trim()
$env:GIT_SHA = $sha
Write-Host "Building (GIT_SHA=$sha)..."
docker compose build --build-arg "GIT_SHA=$sha"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed. Rollback: git checkout $currentTag ; docker compose up -d --build"
    if ($backupSet) { Write-Host "Backup set: $backupSet" }
    exit 1
}
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "Compose up failed. Rollback: git checkout $currentTag ; docker compose up -d --build"
    if ($backupSet) { Write-Host "Backup set: $backupSet" }
    exit 1
}

Write-Host "Waiting for api (up to 120s)..."
$deadline = (Get-Date).AddSeconds(120)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $ps = docker compose ps --format json 2>$null
    if ($ps -match '"Service"\s*:\s*"api"' -and $ps -match '"Health"\s*:\s*"healthy"') {
        $healthy = $true
        break
    }
    # Fallback when --format json is unavailable
    $plain = docker compose ps 2>$null | Out-String
    if ($plain -match 'api\s+.*\(healthy\)') {
        $healthy = $true
        break
    }
}
if (-not $healthy) {
    Write-Host "API not healthy in time. Rollback: git checkout $currentTag ; docker compose up -d --build"
    if ($backupSet) { Write-Host "Backup set: $backupSet" }
    exit 1
}

try {
    $health = Invoke-WebRequest -Uri "http://localhost:8380/api/v1/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "status $($health.StatusCode)" }
} catch {
    Write-Host "Health check failed. Rollback: git checkout $currentTag ; docker compose up -d --build"
    if ($backupSet) { Write-Host "Backup set: $backupSet" }
    exit 1
}

$newVersion = (Get-Content -Raw VERSION).Trim()
Write-Host "Updated to Heardloop v$newVersion ($Tag)."
if ($backupSet) { Write-Host "Backup set kept: $backupSet" }
