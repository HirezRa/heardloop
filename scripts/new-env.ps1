# Creates .env with strong random secrets. Refuses to overwrite an existing .env.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/new-env.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root '.env'
if (Test-Path $envPath) { Write-Error ".env already exists - delete it first if you really want new secrets."; exit 1 }

function New-RandomBytes([int]$count) {
    $bytes = New-Object byte[] $count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return $bytes
}

$dbPassword = -join ((New-RandomBytes 24) | ForEach-Object { $_.ToString('x2') })
$tokenKey = [Convert]::ToBase64String((New-RandomBytes 32))

$content = (Get-Content (Join-Path $root '.env.example') -Raw) `
    -replace 'POSTGRES_PASSWORD=.*', "POSTGRES_PASSWORD=$dbPassword" `
    -replace 'HEARDLOOP_TOKEN_KEY=.*', "HEARDLOOP_TOKEN_KEY=$tokenKey"

# UTF-8 without BOM (Windows PowerShell 5.1 would add one with -Encoding UTF8).
[System.IO.File]::WriteAllText($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ".env created. Fill in the LINKEDIN_* values before phase 2. Back up HEARDLOOP_TOKEN_KEY somewhere safe."
