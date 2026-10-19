# Updates Heardloop on this machine to a release tag (or the newest vX.Y.Z).
# Usage (from the repository root, or any path - the script cds to the root):
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1 -Tag v0.9.0 -Yes
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1 -WhatIf
#   powershell -ExecutionPolicy Bypass -File scripts/update.ps1 -DryRun
# Compares the RUNNING API version (image label or app_version()), not only VERSION on disk.
# Never prints environment variables. Never touches .env.
param(
    [string]$Tag = "",
    [switch]$Yes,
    [Alias('DryRun')]
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$msg) {
    Write-Error $msg
    exit 1
}

function Get-FilesVersionTag {
    $ver = (Get-Content -Raw (Join-Path $root "VERSION")).Trim()
    if ($ver -notmatch '^\d+\.\d+\.\d+$') { Fail "VERSION is not semver: $ver" }
    return "v$ver"
}

function Get-RunningVersionTag {
    # Returns "vX.Y.Z", "" if unknown / stack down (caller prints the not-running message).
    $cid = ""
    try { $cid = (docker compose ps -q api 2>$null | Select-Object -First 1).Trim() } catch { $cid = "" }
    if (-not $cid) { return "" }

    $label = ""
    try {
        # Single-quoted format so PowerShell does not expand the Go template.
        $label = (docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' $cid 2>$null | Select-Object -First 1)
        if ($null -eq $label) { $label = "" } else { $label = $label.Trim() }
    } catch { $label = "" }
    if ($label -match '^\d+\.\d+\.\d+$') { return "v$label" }
    if ($label -match '^v\d+\.\d+\.\d+$') { return $label }

    $fromApp = ""
    try {
        $fromApp = (docker compose exec -T api python -c "from heardloop.version import app_version; print(app_version())" 2>$null | Select-Object -Last 1)
        if ($null -eq $fromApp) { $fromApp = "" } else { $fromApp = $fromApp.Trim() }
    } catch { $fromApp = "" }
    if ($fromApp -match '^\d+\.\d+\.\d+$') { return "v$fromApp" }
    if ($fromApp -match '^v\d+\.\d+\.\d+$') { return $fromApp }
    return ""
}

function Confirm-RunningEquals([string]$expectedTag) {
    $got = Get-RunningVersionTag
    return ($got -eq $expectedTag)
}

function Invoke-RebuildAndUp([string]$sha, [string]$versionPlain, [string]$rollbackHint, [string]$backupSet) {
    $env:GIT_SHA = $sha
    $env:VERSION = $versionPlain
    Write-Host "Building (VERSION=$versionPlain GIT_SHA=$sha)..."
    docker compose build --build-arg "GIT_SHA=$sha" --build-arg "VERSION=$versionPlain"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed. Rollback: $rollbackHint"
        if ($backupSet) { Write-Host "Backup set: $backupSet" }
        exit 1
    }
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Compose up failed. Rollback: $rollbackHint"
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
        $plain = docker compose ps 2>$null | Out-String
        if ($plain -match 'api\s+.*\(healthy\)') {
            $healthy = $true
            break
        }
    }
    if (-not $healthy) {
        Write-Host "API not healthy in time. Rollback: $rollbackHint"
        if ($backupSet) { Write-Host "Backup set: $backupSet" }
        exit 1
    }

    try {
        $health = Invoke-WebRequest -Uri "http://localhost:8380/api/v1/health" -UseBasicParsing -TimeoutSec 10
        if ($health.StatusCode -ne 200) { throw "status $($health.StatusCode)" }
    } catch {
        Write-Host "Health check failed. Rollback: $rollbackHint"
        if ($backupSet) { Write-Host "Backup set: $backupSet" }
        exit 1
    }
}

if (-not (Test-Path (Join-Path $root "VERSION"))) {
    Fail "Run this script from a Heardloop checkout (VERSION file missing)."
}

$filesTag = Get-FilesVersionTag

# Resolve target tag early so -WhatIf can print a plan even on a dirty tree.
if (-not $WhatIf) {
    Write-Host "Fetching tags..."
    git fetch --tags --prune
    if ($LASTEXITCODE -ne 0) { Fail "git fetch failed." }
}

if (-not $Tag) {
    $tags = git tag -l "v*.*.*" --sort=-v:refname
    $Tag = ($tags | Select-Object -First 1)
    if (-not $Tag) { $Tag = $filesTag }
}
if ($Tag -notmatch '^v\d+\.\d+\.\d+$') {
    Fail "Tag must look like v1.2.3 (got: $Tag)."
}

$runningTag = Get-RunningVersionTag
$stackDown = [string]::IsNullOrEmpty($runningTag)
if ($stackDown) {
    Write-Host "Heardloop is not running"
    $runningDisplay = "unknown"
} else {
    $runningDisplay = $runningTag
}

$releaseUrl = "https://github.com/HirezRa/heardloop/releases/tag/$Tag"
Write-Host "Running: $runningDisplay"
Write-Host "Files:   $filesTag"
Write-Host "Target:  $Tag"
Write-Host "Release: $releaseUrl"

$needCheckout = ($filesTag -ne $Tag)
$needRebuild = $stackDown -or ($runningTag -ne $Tag)

if (-not $needRebuild) {
    Write-Host "already up to date"
    exit 0
}

$planRebuildOnly = (-not $needCheckout) -and $needRebuild

if ($WhatIf) {
    Write-Host ""
    Write-Host "WhatIf plan (no changes):"
    if ($planRebuildOnly) {
        Write-Host "  Files are already at $Tag; rebuilding containers"
        Write-Host "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
        Write-Host "  2. git fetch --tags --prune"
        Write-Host "  3. scripts/backup-now.ps1 (abort unless status=ok)"
        Write-Host "  4. docker compose build --build-arg VERSION=... --build-arg GIT_SHA=... ; docker compose up -d"
        Write-Host "  5. Wait up to 120s for api healthy; confirm running version is $Tag"
    } else {
        Write-Host "  1. Refuse if tracked files are dirty (untracked .env / override / backups/ ok)"
        Write-Host "  2. git fetch --tags --prune"
        Write-Host "  3. scripts/backup-now.ps1 (abort unless status=ok)"
        Write-Host "  4. git checkout $Tag"
        Write-Host "  5. docker compose build --build-arg VERSION=... --build-arg GIT_SHA=... ; docker compose up -d"
        Write-Host "  6. Wait up to 120s for api healthy; confirm running version is $Tag"
        Write-Host "On failure after checkout: git checkout $filesTag ; docker compose up -d --build"
    }
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

$rollbackFiles = $filesTag
if ($planRebuildOnly) {
    Write-Host "Files are already at $Tag; rebuilding containers"
} else {
    Write-Host "Checking out $Tag..."
    git checkout $Tag
    if ($LASTEXITCODE -ne 0) { Fail "git checkout $Tag failed." }
}

$sha = (git rev-parse --short HEAD).Trim()
$versionPlain = (Get-Content -Raw VERSION).Trim()
$rollbackHint = if ($planRebuildOnly) {
    "docker compose up -d --build"
} else {
    "git checkout $rollbackFiles ; docker compose up -d --build"
}

Invoke-RebuildAndUp -sha $sha -versionPlain $versionPlain -rollbackHint $rollbackHint -backupSet $backupSet

if (-not (Confirm-RunningEquals $Tag)) {
    $got = Get-RunningVersionTag
    if (-not $got) { $got = "unknown" }
    Write-Host "Running version after update is $got, expected $Tag. Rollback: $rollbackHint"
    if ($backupSet) { Write-Host "Backup set: $backupSet" }
    exit 1
}

Write-Host "Updated to Heardloop $Tag (running confirmed)."
if ($backupSet) { Write-Host "Backup set kept: $backupSet" }
