# Demo stack and README screenshots

The README screenshots come from a separate demo stack filled with fictional data. It never touches the real
stack: own project name (`heardloop-demo`), own env file, own ports (**8390** / **8391**), own volumes. The
worker does not run, so nothing can be published.

`seed_demo` creates workspace **Acme Studio**, people `alex@example.com` (owner, password + TOTP),
`sam@example.com`, `robin@example.com`, LinkedIn account **Alex Demo**, posts, a campaign, audiences **IL** and **US-E**, a template,
metrics, a webhook URL `https://hooks.example.com/heardloop`, and an `update_check` row for the banner.
The screenshot script captures `update-banner.png` first, then dismisses the banner (writes
`dismissed_update` in the owner's `ui_prefs`) so every other shot is free of it.

The owner already has two-step verification enabled with a fixed demo secret (development only):

```text
DEMO_TOTP_SECRET=JBSWY3DPEHPK3PXP
```

The screenshot script computes the current code itself; no authenticator app is needed.

```powershell
# 1. Throwaway env + backup manifest outside the repo
$demo = Join-Path $env:TEMP 'heardloop-demo'; New-Item -ItemType Directory -Force $demo | Out-Null
$backups = Join-Path $demo 'backups'; New-Item -ItemType Directory -Force $backups | Out-Null
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$b = New-Object byte[] 32; $rng.GetBytes($b); $key = [Convert]::ToBase64String($b)
$p = New-Object byte[] 24; $rng.GetBytes($p); $pw = -join ($p | ForEach-Object { $_.ToString('x2') })
$finished = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
@"
POSTGRES_PASSWORD=$pw
HEARDLOOP_TOKEN_KEY=$key
APP_ENV=development
COOKIE_SECURE=true
APP_BASE_URL=http://127.0.0.1:8390
UI_LANGUAGE=en
BACKUP_DIR=$($backups -replace '\\','/')
"@ | Set-Content (Join-Path $demo 'demo.env') -Encoding ascii
@"
{"set":"heardloop-demo","finished_at":"$finished","status":"ok","db_bytes":1024,"media_bytes":2048,"encrypted":false}
"@ | Set-Content (Join-Path $backups 'latest.json') -Encoding ascii

# 2. Start and seed (prints DEMO_PASSWORD for alex@example.com)
$repo = (Get-Location).Path
$dc = @('compose','-p','heardloop-demo','--env-file',"$demo\demo.env",'-f','docker-compose.yml','-f','docker-compose.demo.yml')
docker @dc up -d --build
$seed = docker @dc exec -T api python -m heardloop.devtools.seed_demo 2>&1 | Out-String
$seed
$demoPassword = if ($seed -match 'DEMO_PASSWORD=(\S+)') { $Matches[1] } else { throw 'no DEMO_PASSWORD' }

# 3. Screenshots (Playwright in $demo; normal text size, English, light)
Push-Location $demo; npm init -y | Out-Null; npm i playwright@1.56.1 --save-exact; npx playwright install chromium; Pop-Location
$env:DEMO_PASSWORD = $demoPassword
$env:DEMO_TOTP_SECRET = 'JBSWY3DPEHPK3PXP'
Set-Location $demo
node "$repo\scripts\readme-screenshots.mjs" --base http://127.0.0.1:8390 --out "$repo\docs\assets\screenshots"

# 4. Tear down (only the demo volumes)
docker @dc down --volumes
Remove-Item -Recurse -Force $demo
```

Screenshots land in `docs/assets/screenshots/` (English UI, 1440×900, normal text size). Check every image by
eye before committing: fictional data only, no secrets, no QR / recovery codes on the security shot.
