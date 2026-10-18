# Demo stack and README screenshots

The README screenshots come from a separate demo stack filled with fictional data. It never touches the real
stack: own project name, own env file, own ports (8480/8481) and own volumes. The worker does not run, so nothing
can be published.

```powershell
# 1. Throwaway env file outside the repo (random values, never committed)
$demo = Join-Path $env:TEMP 'heardloop-demo'; New-Item -ItemType Directory -Force $demo | Out-Null
powershell -NoProfile -Command {
  param($demo)
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $b = New-Object byte[] 32; $rng.GetBytes($b); $key = [Convert]::ToBase64String($b)
  $p = New-Object byte[] 24; $rng.GetBytes($p); $pw = -join ($p | ForEach-Object { $_.ToString('x2') })
  [System.IO.File]::WriteAllText((Join-Path $demo 'demo.env'), "POSTGRES_PASSWORD=$pw`nHEARDLOOP_TOKEN_KEY=$key`n",
    (New-Object System.Text.UTF8Encoding($false)))
} -args $demo

# 2. Start and seed (prints a one-time password for demo@example.com)
$dc = @('compose','-p','heardloop-demo','--env-file',"$demo\demo.env",'-f','docker-compose.yml','-f','docker-compose.demo.yml')
docker @dc up -d --build
docker @dc exec api python -m heardloop.devtools.seed_demo

# 3. Screenshots (Playwright installed in $demo; run node from there)
Push-Location $demo; npm init -y | Out-Null; npm i playwright@1.56.1 --save-exact; Pop-Location
powershell -NoProfile -Command { param($demo,$repo,$pw) $env:DEMO_PASSWORD=$pw; Set-Location $demo
  node "$repo\scripts\readme-screenshots.mjs" --base http://localhost:8480 --out "$repo\docs\assets\screenshots" } `
  -args $demo,(Get-Location).Path,'<password from step 2>'

# 4. Tear down (only the demo volumes)
docker @dc down --volumes; Remove-Item -Recurse -Force $demo
```

The script captures the main content area only (English UI): `posts-list.png`, `posts-editor.png`,
`accounts.png`. Check every image by eye before committing: fictional data only.
