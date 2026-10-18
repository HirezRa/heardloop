// Captures README / GUIDE / INSTALL screenshots (English UI) from the DEMO stack - fictional data only.
// Usage (from a temp folder where `npm i playwright@<pinned>` and `npx playwright install chromium` ran):
//   node <repo>/scripts/readme-screenshots.mjs --base http://localhost:8480 --out <repo>/docs/assets/screenshots
// The demo password comes from the DEMO_PASSWORD environment variable of this process only.
// Resolve playwright from process.cwd() (the temp demo folder), not from this script's directory.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(join(process.cwd(), 'package.json'));
const { chromium } = require('playwright');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

const base = arg('base', 'http://localhost:8480');
const out = arg('out', 'screenshots');
const password = process.env.DEMO_PASSWORD;
if (!password) throw new Error('Set DEMO_PASSWORD (printed by seed_demo) for this process.');
if (!/^http:\/\/localhost:84\d\d$/.test(base)) throw new Error('Refusing: screenshots come from the demo stack only.');
mkdirSync(out, { recursive: true });

// Prefer a system browser when Playwright's Chromium download is blocked (corporate TLS/proxy).
const browser = await chromium.launch({ channel: 'msedge' }).catch(() => chromium.launch({ channel: 'chrome' }));
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

async function shot(name) {
  await page.waitForTimeout(400);
  await page.locator('main.content').screenshot({ path: join(out, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

async function go(section, tab) {
  const q = tab ? `?section=${section}&tab=${tab}` : `?section=${section}`;
  await page.goto(`${base}/${q}`);
  await page.waitForSelector('main.content', { timeout: 15000 });
  await page.waitForLoadState('networkidle');
}

await page.goto(base);
await page.fill('input[type=email]', 'demo@example.com');
await page.fill('input[type=password]', password);
await page.click('button[type=submit]');
await page.waitForSelector('main.content', { timeout: 15000 });

// Seed a fake update_check row via the API so the owner banner appears (demo owner).
try {
  await page.evaluate(async () => {
    // Banner needs app_meta; if the demo seed did not insert it, the shot is skipped later.
  });
} catch {
  /* ignore */
}

await go('calendar');
await shot('calendar');

await go('posts');
await page.waitForSelector('.post-item', { timeout: 10000 }).catch(() => null);
if (await page.locator('.post-item').count()) {
  await page.locator('.post-item').first().click();
  await shot('posts-list');
  const editor = page.locator('.post-item').filter({ hasText: /approval|lesson|post/i }).first();
  if (await editor.count()) {
    await editor.click();
    await page.waitForTimeout(500);
  }
  await shot('posts-editor');
  // Version history dialog with a diff when the button exists.
  const history = page.getByRole('button', { name: /history|versions/i });
  if (await history.count()) {
    await history.first().click();
    await page.waitForTimeout(400);
    await shot('post-history');
    await page.keyboard.press('Escape');
  } else {
    console.log('skip post-history.png (no history control)');
  }
} else {
  console.log('skip posts-* (no demo posts)');
}

await go('approvals');
await shot('approvals');

await go('campaigns');
await shot('campaigns');

await go('templates');
await shot('templates');

await go('reports');
await shot('reports');

await go('accounts');
await shot('accounts');

await go('settings', 'appearance');
await shot('settings-appearance');

await go('settings', 'security');
// Two-step section only; avoid QR / recovery codes on screen.
await page.locator('text=/two-step|authenticator/i').first().waitFor({ timeout: 5000 }).catch(() => null);
await shot('settings-security');

await go('settings', 'webhook');
// Capture before generating a secret, or with the field masked.
await page.locator('input[type="password"], input[name*="secret"]').first().evaluate((el) => {
  if (el instanceof HTMLInputElement) el.value = '••••••••';
}).catch(() => null);
await shot('settings-webhook');

await go('settings', 'workspace');
await shot('settings-backups');

// Update banner: only if the API already reports an update (seeded app_meta).
await go('calendar');
const banner = page.locator('.banner').filter({ hasText: /available/i });
if (await banner.count()) {
  await shot('update-banner');
} else {
  console.log('skip update-banner.png (no seeded update_check / banner)');
}

await browser.close();
