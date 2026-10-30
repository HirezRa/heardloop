<#
.SYNOPSIS
  Heardloop installer for Windows 10/11.

.DESCRIPTION
  Installs what is missing (Git, WSL 2, Docker Desktop) with winget, downloads Heardloop at the newest release
  tag, creates .env with random secrets (scripts/new-env.ps1), asks for the LinkedIn app keys (optional),
  starts the stack, waits until it is healthy and creates the owner account. Safe to run again: it keeps an
  existing clone, .env and database.

  Review it before you run it:
    cd $HOME
    Invoke-WebRequest https://raw.githubusercontent.com/HirezRa/heardloop/main/scripts/install.ps1 -OutFile heardloop-install.ps1
    notepad .\heardloop-install.ps1
    powershell -ExecutionPolicy Bypass -File .\heardloop-install.ps1

  Never prints secrets and never sets HEARDLOOP_TOKEN_KEY or POSTGRES_PASSWORD as environment variables
  (Compose would prefer them over .env).

.PARAMETER Dir        Where Heardloop lives (default: this clone, else $HOME\heardloop).
.PARAMETER Ref        Release tag or branch to check out (default: newest vX.Y.Z tag).
.PARAMETER Lang       Interface language: en or he.
.PARAMETER WithAi     Add a local Ollama service for the optional AI writing assistant.
.PARAMETER AiModel    Model for -WithAi (default llama3.1:8b).
.PARAMETER OllamaTag  Ollama image tag for -WithAi (check hub.docker.com/r/ollama/ollama).
.PARAMETER SkipDocker Never install Docker Desktop, only check it.
.PARAMETER NoOwner    Do not create the owner account.
.PARAMETER Yes        Accept defaults, no questions (LinkedIn keys and owner are skipped).
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [string]$Ref = '',
    [ValidateSet('', 'en', 'he')][string]$Lang = '',
    [switch]$WithAi,
    [ValidatePattern('^[A-Za-z0-9._:/-]+$')][string]$AiModel = 'llama3.1:8b',
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$OllamaTag = '0.40.5',
    [switch]$SkipDocker,
    [switch]$NoOwner,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/HirezRa/heardloop.git'
$WebUrl = 'http://localhost:8380'
$HealthTimeout = 420

function Step([string]$m) { Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "    $m" }
function Ok([string]$m) { Write-Host "    $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "    $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host ''; Write-Host "Error: $m" -ForegroundColor Red; exit 1 }

function Ask([string]$q, [string]$def = '') {
    if ($Yes) { return $def }
    $prompt = if ($def) { "    $q [$def]" } else { "    $q" }
    $r = Read-Host $prompt
    if ([string]::IsNullOrWhiteSpace($r)) { return $def }
    return $r.Trim()
}
function Ask-YesNo([string]$q, [bool]$def = $true) {
    if ($Yes) { return $def }
    $hint = if ($def) { 'Y/n' } else { 'y/N' }
    $r = Read-Host "    $q ($hint)"
    if ([string]::IsNullOrWhiteSpace($r)) { return $def }
    return $r -match '^[Yy]'
}
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u"
    $dockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if ((Test-Path $dockerBin) -and ($env:Path -notlike "*$dockerBin*")) { $env:Path = "$env:Path;$dockerBin" }
}
function Has([string]$cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Winget-Install([string]$id, [string]$label) {
    if (-not (Has 'winget')) { Fail "winget is not available. Install $label by hand, then run this again." }
    Info "Installing $label with winget"
    & winget install --id $id -e --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "winget could not install $label (exit $LASTEXITCODE)." }
    Refresh-Path
}

# ---------- tools ----------
Write-Host 'Heardloop installer' -ForegroundColor White
if ($env:OS -ne 'Windows_NT') { Fail 'This script is for Windows. On Linux or macOS use scripts/install.sh.' }
Refresh-Path

Step 'Checking Git'
if (-not (Has 'git')) {
    if (-not (Ask-YesNo 'Git is missing. Install it with winget?' $true)) { Fail 'Git is required: https://git-scm.com/downloads' }
    Winget-Install 'Git.Git' 'Git'
    if (-not (Has 'git')) { Fail 'Git was installed but is not on PATH yet. Open a new PowerShell window and run this again.' }
}
Ok ((& git --version) -join '')

# ---------- Docker ----------
function Docker-Ready { & docker info *> $null; return ($LASTEXITCODE -eq 0) }

Step 'Checking Docker Desktop'
if (-not (Has 'docker')) {
    if ($SkipDocker) { Fail 'Docker Desktop is missing and -SkipDocker was given.' }

    & wsl.exe --status *> $null
    if ($LASTEXITCODE -ne 0) {
        if (-not (Ask-YesNo 'Docker Desktop needs WSL 2, which is not installed. Install it now? (asks for admin rights, then a restart)' $true)) {
            Fail 'WSL 2 is required: https://learn.microsoft.com/windows/wsl/install'
        }
        Start-Process -FilePath 'wsl.exe' -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait
        Warn 'WSL 2 is installed. Restart Windows, then run this script again.'
        exit 0
    }
    if (-not (Ask-YesNo 'Install Docker Desktop with winget?' $true)) { Fail 'Docker Desktop is required: https://www.docker.com/products/docker-desktop/' }
    Winget-Install 'Docker.DockerDesktop' 'Docker Desktop'
}
if (-not (Docker-Ready)) {
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $exe) {
        Info 'Starting Docker Desktop. The first time, accept its terms in the window that opens.'
        Start-Process -FilePath $exe
    }
    $waited = 0
    while (-not (Docker-Ready)) {
        Start-Sleep -Seconds 5; $waited += 5
        if ($waited -ge 300) {
            Fail 'Docker Desktop did not start within 5 minutes. If it was just installed, sign out of Windows and in again, start Docker Desktop, wait for "Engine running", then run this again.'
        }
    }
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker Compose is missing. Update Docker Desktop and run this again.' }
Ok "Docker Desktop is running (Compose $((& docker compose version --short) -join ''))"

# ---------- source ----------
Step 'Getting Heardloop'
$here = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
$useHere = $false
if (-not $Dir -and $here -and (Test-Path (Join-Path $here 'docker-compose.yml'))) {
    if (Select-String -Path (Join-Path $here 'docker-compose.yml') -Pattern '^name: heardloop' -Quiet) { $Dir = $here; $useHere = $true }
}
if ($useHere -and -not $Ref) {
    Ok "Using this clone: $Dir (version $((Get-Content (Join-Path $Dir 'VERSION') -Raw).Trim()))"
} else {
    if (-not $Dir) { $Dir = Join-Path $HOME 'heardloop' }
    if (-not $Ref) {
        $tags = & git ls-remote --tags --refs $RepoUrl 2>$null | ForEach-Object { ($_ -split '/')[-1] } |
            Where-Object { $_ -match '^v\d+\.\d+\.\d+$' }
        $Ref = $tags | Sort-Object { [version]($_.TrimStart('v')) } | Select-Object -Last 1
        if (-not $Ref) { Fail 'Could not read the release tags from GitHub. Check the network, or pass -Ref main.' }
    }
    if (Test-Path (Join-Path $Dir '.git')) {
        Info "Found an existing clone in $Dir"
        & git -C $Dir fetch -q --tags origin
        $dirty = & git -C $Dir status --porcelain --untracked-files=no
        if ($dirty) { Warn 'The clone has local changes; leaving the checked-out version as it is.' }
        else { & git -C $Dir -c advice.detachedHead=false checkout -q $Ref; Ok "Heardloop $Ref in $Dir" }
    } elseif ((Test-Path $Dir) -and (Get-ChildItem -Force $Dir | Select-Object -First 1)) {
        Fail "$Dir exists and is not empty. Choose another folder with -Dir."
    } else {
        Info "Cloning into $Dir"
        & git clone -q $RepoUrl $Dir
        if ($LASTEXITCODE -ne 0) { Fail 'git clone failed.' }
        & git -C $Dir -c advice.detachedHead=false checkout -q $Ref
        Ok "Heardloop $Ref in $Dir"
    }
}
Set-Location $Dir

# ---------- .env ----------
$EnvFile = Join-Path $Dir '.env'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-EnvValue([string]$key) {
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$key=")) { return $line.Substring($key.Length + 1) }
    }
    return ''
}
function Set-EnvValue([string]$key, [string]$value) {
    if ($value -match "[`r`n]") { Fail "Value for $key contains a line break." }
    $lines = [System.Collections.Generic.List[string]]::new()
    $done = $false
    foreach ($line in [System.IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$key=")) { $lines.Add("$key=$value"); $done = $true } else { $lines.Add($line) }
    }
    if (-not $done) { $lines.Add("$key=$value") }
    [System.IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $Utf8)
}

Step 'Creating .env'
if (Test-Path $EnvFile) {
    Ok '.env already exists; keeping its secrets'
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'scripts\new-env.ps1') | Out-Null
    if (-not (Test-Path $EnvFile)) { Fail 'scripts/new-env.ps1 did not create .env.' }
    Ok '.env created with a random database password and encryption key (not shown)'
    Warn "Back up $EnvFile now (password manager or encrypted USB). Without HEARDLOOP_TOKEN_KEY a backup cannot be restored."
}

Step 'Settings'
if (-not $Lang) {
    $cur = Get-EnvValue 'UI_LANGUAGE'
    if (-not $cur) { $cur = 'en' }
    $Lang = Ask 'Interface language, en or he' $cur
}
if ($Lang -ne 'en' -and $Lang -ne 'he') { Warn "Unknown language '$Lang', using en"; $Lang = 'en' }
Set-EnvValue 'UI_LANGUAGE' $Lang
Ok "Interface language: $Lang"

$curId = Get-EnvValue 'LINKEDIN_CLIENT_ID'
if ($curId -and $curId -ne '<client-id>') {
    Ok 'LinkedIn app keys are already set'
} else {
    Info 'Heardloop needs your own LinkedIn developer app to publish (free).'
    Info 'Create it at https://www.linkedin.com/developers/apps with the products'
    Info '"Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn", and the redirect URL'
    Info "$WebUrl/api/v1/linkedin/callback   (steps: docs/INSTALL.md, section 4)"
    if (-not $Yes -and (Ask-YesNo 'Do you have the Client ID and Client Secret now?' $false)) {
        $id = Ask 'Client ID'
        if ($id -notmatch '^[A-Za-z0-9]+$') { Fail 'That Client ID does not look right (letters and digits only).' }
        $sec = Read-Host '    Client Secret (not shown)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if (-not $plain) { Fail 'The Client Secret is empty.' }
        Set-EnvValue 'LINKEDIN_CLIENT_ID' $id
        Set-EnvValue 'LINKEDIN_CLIENT_SECRET' $plain
        $plain = $null
        Ok 'LinkedIn app keys saved in .env'
    } else {
        Info 'Skipped. Add LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET to .env later, then run: docker compose up -d'
    }
}

# ---------- start ----------
Step 'Building and starting Heardloop (the first build takes a few minutes)'
$listening = Get-NetTCPConnection -LocalPort 8380 -State Listen -ErrorAction SilentlyContinue
$webId = (& docker compose ps -q web 2>$null) -join ''
if ($listening -and -not $webId) { Fail 'Port 8380 is already used by another program. Free it, or map another port in docker-compose.override.yml.' }

$env:VERSION = (Get-Content (Join-Path $Dir 'VERSION') -Raw).Trim()
$env:GIT_SHA = ((& git rev-parse --short HEAD) -join '').Trim()
& docker compose build --build-arg "GIT_SHA=$env:GIT_SHA" --build-arg "VERSION=$env:VERSION"
if ($LASTEXITCODE -ne 0) { Fail "The build failed. Run 'docker compose build' in $Dir to see the full output." }
& docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail 'Could not start the stack.' }

Info 'Waiting until the API is healthy'
$waited = 0
while ($true) {
    try {
        $r = Invoke-WebRequest -Uri "$WebUrl/api/v1/health" -UseBasicParsing -TimeoutSec 5
        if ($r.Content -match '"status"\s*:\s*"healthy"') { break }
    } catch { }
    Start-Sleep -Seconds 5; $waited += 5
    if ($waited -ge $HealthTimeout) {
        & docker compose ps
        & docker compose logs --tail 40 api
        Fail 'Heardloop did not become healthy in time. See the logs above; docs/INSTALL.md, section 12 helps.'
    }
}
Ok "Heardloop $env:VERSION is running at $WebUrl"

# ---------- optional AI ----------
if (-not $WithAi -and -not $Yes) {
    $WithAi = Ask-YesNo 'Add the optional AI writing assistant with a local Ollama model? (needs several GB of disk and memory)' $false
}
if ($WithAi) {
    Step 'AI writing assistant (Ollama, local)'
    $override = Join-Path $Dir 'docker-compose.override.yml'
    $ready = $true
    if (Test-Path $override) {
        if (Select-String -Path $override -Pattern '^  ollama:' -Quiet) { Ok 'docker-compose.override.yml already has an ollama service' }
        else { Warn 'docker-compose.override.yml exists; not changing it. Add the ollama service from docs/INSTALL.md, section 13.'; $ready = $false }
    } else {
        $yaml = @(
            '# Created by scripts/install.ps1. Personal and git-ignored.',
            'services:',
            '  ollama:',
            "    image: ollama/ollama:$OllamaTag",
            '    restart: unless-stopped',
            '    volumes:',
            '      - ollama:/root/.ollama',
            'volumes:',
            '  ollama:'
        ) -join "`n"
        [System.IO.File]::WriteAllText($override, $yaml + "`n", $Utf8)
        Ok "docker-compose.override.yml created (ollama/ollama:$OllamaTag)"
    }
    if ($ready) {
        & docker compose up -d ollama
        if ($LASTEXITCODE -ne 0) { Fail 'Could not start Ollama. Check the image tag with -OllamaTag.' }
        Info "Downloading the model $AiModel (can take a while)"
        & docker compose exec -T ollama ollama pull $AiModel
        if ($LASTEXITCODE -ne 0) { Fail "Could not download $AiModel." }
        Set-EnvValue 'AI_BASE_URL' 'http://ollama:11434/v1'
        Set-EnvValue 'AI_MODEL' $AiModel
        & docker compose up -d api worker | Out-Null
        Ok "AI assistant set to $AiModel. Post text stays on this computer."
    }
}

# ---------- owner ----------
function Get-IanaZone {
    $win = [System.TimeZoneInfo]::Local.Id
    $iana = $null
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        if ([System.TimeZoneInfo]::TryConvertWindowsIdToIanaId($win, [ref]$iana)) { return $iana }
    }
    $map = @{
        'Israel Standard Time' = 'Asia/Jerusalem'; 'GMT Standard Time' = 'Europe/London'
        'W. Europe Standard Time' = 'Europe/Berlin'; 'Romance Standard Time' = 'Europe/Paris'
        'Eastern Standard Time' = 'America/New_York'; 'Central Standard Time' = 'America/Chicago'
        'Pacific Standard Time' = 'America/Los_Angeles'; 'UTC' = 'UTC'
    }
    if ($map.ContainsKey($win)) { return $map[$win] }
    return 'UTC'
}

if ($NoOwner -or $Yes) {
    Info 'Owner account skipped (see docs/INSTALL.md, section 7).'
} elseif (Ask-YesNo 'Create the owner account now? (skip if you already have one)' $true) {
    Step 'Owner account'
    $email = Ask 'E-mail'
    if ($email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { Fail 'That e-mail address does not look right.' }
    $name = Ask 'Your name'
    if (-not $name) { Fail 'The name is empty.' }
    $workspace = Ask 'Workspace name' 'My workspace'
    $tz = Ask 'Workspace time zone (IANA name)' (Get-IanaZone)
    Info 'Choose a password of at least 12 characters. It is not shown while you type.'
    & docker compose exec api python -m heardloop.devtools.create_owner --email "$email" --name "$name" --workspace "$workspace" --time-zone "$tz"
    if ($LASTEXITCODE -ne 0) {
        Warn "The owner was not created. Run it again later from ${Dir}:"
        Warn '  docker compose exec api python -m heardloop.devtools.create_owner --email <e-mail> --name "<name>" --workspace "<workspace>"'
    } else { Ok 'Owner account created' }
}

Step 'Done'
Info "Open $WebUrl and sign in."
Info '1. Turn on two-step verification (Settings).'
Info '2. Connect your LinkedIn profile (Accounts). Missing app keys? docs/INSTALL.md, section 4.'
Info '3. Write your first post.'
Info ''
Info "Folder:   $Dir"
Info "Update:   $Dir\scripts\update.ps1"
Info "Back up:  $Dir\.env (keep it private) and the backups folder"
Info "Guide:    $Dir\docs\GUIDE.md"
