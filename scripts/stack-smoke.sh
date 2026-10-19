#!/usr/bin/env bash
# Build the real Compose images, check health through the web port, load the login
# page in Chromium, tear down. Throwaway project; never touches a real .env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT="${STACK_SMOKE_PROJECT:-heardloop-ci-smoke}"
WEB_PORT="${STACK_SMOKE_WEB_PORT:-8390}"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/heardloop-stack-smoke.XXXXXX")"
ENV_FILE="$WORKDIR/preview.env"
OVERLAY="$WORKDIR/docker-compose.preview.yml"
BASE_URL="http://127.0.0.1:${WEB_PORT}"

cleanup() {
  docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml -f "$OVERLAY" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

db_password="$(openssl rand -hex 24)"
token_key="$(openssl rand -base64 32 | tr -d '\n')"

cat >"$ENV_FILE" <<EOF
POSTGRES_PASSWORD=${db_password}
HEARDLOOP_TOKEN_KEY=${token_key}
APP_ENV=development
COOKIE_SECURE=true
APP_BASE_URL=${BASE_URL}
LINKEDIN_CLIENT_ID=unused
LINKEDIN_CLIENT_SECRET=unused
LINKEDIN_REDIRECT_URI=${BASE_URL}/api/v1/linkedin/callback
UI_LANGUAGE=en
EOF

cat >"$OVERLAY" <<EOF
services:
  web:
    ports: !override
      - "127.0.0.1:${WEB_PORT}:8080"
EOF

echo ">>> stack-smoke: building ${PROJECT} (web :${WEB_PORT})"
docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml -f "$OVERLAY" up -d --build

echo ">>> waiting for api healthy"
deadline=$((SECONDS + 180))
while true; do
  status="$(docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml -f "$OVERLAY" ps --format '{{.Service}} {{.Health}}' | awk '$1=="api"{print $2}')"
  if [[ "$status" == "healthy" ]]; then
    break
  fi
  if (( SECONDS >= deadline )); then
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml -f "$OVERLAY" logs api --tail 80 || true
    echo "api not healthy in time (status=${status:-unknown})" >&2
    exit 1
  fi
  sleep 3
done

echo ">>> GET ${BASE_URL}/api/v1/health"
health="$(curl -fsS "${BASE_URL}/api/v1/health")"
echo "$health"
echo "$health" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"' || {
  echo "health body missing status healthy: $health" >&2
  exit 1
}

echo ">>> login page in Chromium"
cd src/frontend
npm ci --ignore-scripts
npx playwright install --with-deps chromium
export STACK_SMOKE_BASE="$BASE_URL"
node --input-type=module <<'NODE'
import { chromium } from 'playwright';

const base = process.env.STACK_SMOKE_BASE;
if (!base) throw new Error('STACK_SMOKE_BASE is not set');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push(String(err)));
  await page.goto(base + '/', { waitUntil: 'networkidle', timeout: 30_000 });
  await page.waitForSelector('form.login-card input[type="email"]', { timeout: 20_000 });
  await page.waitForSelector('form.login-card input[type="password"]', { timeout: 5_000 });
  const brand = await page.locator('.brand').first().textContent();
  if (!brand || !brand.includes('Heardloop')) {
    throw new Error(`expected Heardloop brand, got ${JSON.stringify(brand)}`);
  }
  const actionable = consoleErrors.filter(
    (line) => !/Failed to load resource|net::ERR_|404 \(Not Found\)|401 \(Unauthorized\)/i.test(line),
  );
  if (actionable.length) {
    throw new Error(`console errors:\n${actionable.join('\n')}`);
  }
  console.log('stack-smoke login ok');
} finally {
  await browser.close();
}
NODE

echo ">>> stack-smoke ok"
