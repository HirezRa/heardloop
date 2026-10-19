// Captures README / GUIDE / INSTALL screenshots (English UI) from the DEMO stack - fictional data only.
// Usage (from a temp folder where `npm i playwright@<pinned>` ran):
//   DEMO_PASSWORD=… DEMO_TOTP_SECRET=JBSWY3DPEHPK3PXP \
//   node <repo>/scripts/readme-screenshots.mjs --base http://127.0.0.1:8390 --out <repo>/docs/assets/screenshots
// Resolve playwright from process.cwd() (the temp demo folder), not from this script's directory.
import { createRequire } from 'node:module';
import { createHmac } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(join(process.cwd(), 'package.json'));
const { chromium } = require('playwright');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s) for the fixed demo secret. */
function totpCode(secretB32, nowMs = Date.now()) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of secretB32.toUpperCase().replace(/=+$/, '')) {
    const val = alphabet.indexOf(ch);
    if (val < 0) throw new Error(`bad base32: ${ch}`);
    bits += val.toString(2).padStart(5, '0');
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const step = Math.floor(nowMs / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const dig = createHmac('sha1', Buffer.from(bytes)).update(msg).digest();
  const off = dig[dig.length - 1] & 0xf;
  const code =
    ((dig[off] & 0x7f) << 24) | ((dig[off + 1] & 0xff) << 16) | ((dig[off + 2] & 0xff) << 8) | (dig[off + 3] & 0xff);
  return String(code % 10 ** 6).padStart(6, '0');
}

const base = arg('base', 'http://127.0.0.1:8390');
const out = arg('out', 'screenshots');
const password = process.env.DEMO_PASSWORD;
const totpSecret = process.env.DEMO_TOTP_SECRET || 'JBSWY3DPEHPK3PXP';
if (!password) throw new Error('Set DEMO_PASSWORD (printed by seed_demo) for this process.');
if (!/^http:\/\/127\.0\.0\.1:8390$/.test(base) && !/^http:\/\/localhost:8390$/.test(base)) {
  throw new Error('Refusing: screenshots come from the demo stack on port 8390 only.');
}
mkdirSync(out, { recursive: true });

const browser = await chromium
  .launch({ channel: 'msedge', headless: true })
  .catch(() => chromium.launch({ channel: 'chrome', headless: true }))
  .catch(() => chromium.launch({ headless: true }));
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

// Sign in as demo owner (password + TOTP). Normal text size + light theme come from seed ui_prefs.
await page.goto(base);
await page.fill('input[type=email]', 'alex@example.com');
await page.fill('input[type=password]', password);
await page.click('button[type=submit]');
await page.waitForSelector('input[autocomplete="one-time-code"], input[inputmode="text"]', {
  timeout: 15000,
});
await page.fill('input[autocomplete="one-time-code"], input[inputmode="text"]', totpCode(totpSecret));
await page.click('button[type=submit]');
await page.waitForSelector('main.content', { timeout: 15000 });

// Seeded update_check shows the banner. Capture it alone, then dismiss so every other shot is clean.
await go('calendar');
const updateBanner = page.locator('.banner').filter({ hasText: /available/i });
if (await updateBanner.count()) {
  await page.waitForTimeout(300);
  await updateBanner.first().screenshot({ path: join(out, 'update-banner.png') });
  console.log('saved update-banner.png');
  await updateBanner.getByRole('button', { name: /dismiss/i }).click();
  await updateBanner.waitFor({ state: 'detached', timeout: 10000 });
  console.log('dismissed update banner for remaining shots');
} else {
  console.log('skip update-banner.png (no seeded update_check / banner)');
}

await go('calendar');
await shot('calendar');

await go('posts');
await page.waitForSelector('.post-item', { timeout: 10000 }).catch(() => null);
if (await page.locator('.post-item').count()) {
  // List view: open the post waiting for approval (sidebar + detail).
  const pending = page.locator('.post-item').filter({ hasText: /Waiting for approval|lesson/i }).first();
  if (await pending.count()) await pending.click();
  else await page.locator('.post-item').nth(1).click();
  await page.waitForTimeout(500);
  await shot('posts-list');

  // Editor view: open the scheduled post with the image preview.
  const scheduled = page.locator('.post-item').filter({ hasText: /Scheduled|shipped/i }).first();
  if (await scheduled.count()) await scheduled.click();
  else await page.locator('.post-item').first().click();
  await page.waitForTimeout(500);
  await shot('posts-editor');

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
await page.locator('text=/two-step|authenticator/i').first().waitFor({ timeout: 5000 }).catch(() => null);
await shot('settings-security');

await go('settings', 'webhook');
await page
  .locator('input[type="password"], input[name*="secret"]')
  .first()
  .evaluate((el) => {
    if (el instanceof HTMLInputElement) el.value = '••••••••';
  })
  .catch(() => null);
await shot('settings-webhook');

await go('settings', 'workspace');
await shot('settings-backups');

await browser.close();
