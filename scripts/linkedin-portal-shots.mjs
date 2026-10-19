/**
 * Dev-only: capture LinkedIn developer-portal screens for docs/INSTALL.md §4.
 *
 * Hard rules (enforced in code):
 * - Never submit anything on LinkedIn. Non-GET/HEAD requests to *.linkedin.com are aborted.
 * - Never automate login. The owner signs in by hand in the headed window.
 * - Never click Create app / Request access / Update / Verify / Generate / Save / Delete / Sign out
 *   (except the Create app *navigation* link that only loads the form).
 * - Shots are written under docs/cursor-prompts/32-assets/linkedin/ (git-excluded).
 *   Do NOT copy them into docs/assets/install/ until the owner says "add linkedin shots".
 *
 * Usage (from repo root, after npm i playwright in a temp folder or from src/frontend):
 *   node scripts/linkedin-portal-shots.mjs
 * Optional: HEARDLOOP_SHOTS_CHANNEL=chrome uses the installed Google Chrome instead of
 * Playwright's bundled Chromium (also used automatically if Chromium is missing).
 * Profile is always %LOCALAPPDATA%\heardloop-shots-profile — never the real Chrome profile.
 */
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stdin as input, stdout as output } from 'node:process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..');

// Prefer playwright from cwd, then from src/frontend.
let chromium;
try {
  const require = createRequire(join(process.cwd(), 'package.json'));
  ({ chromium } = require('playwright'));
} catch {
  const require = createRequire(join(repoRoot, 'src/frontend/package.json'));
  ({ chromium } = require('playwright'));
}

const profileDir = join(
  process.env.LOCALAPPDATA || join(homedir(), '.local', 'share'),
  'heardloop-shots-profile',
);
const outDir = join(repoRoot, 'docs', 'cursor-prompts', '32-assets', 'linkedin');
mkdirSync(outDir, { recursive: true });
mkdirSync(profileDir, { recursive: true });

const blocked = [];
const FORBIDDEN_CLICK = /create app|request access|update|verify|generate|save|delete|sign out/i;

const rl = createInterface({ input, output });

console.log(`Persistent shots profile (delete after use; never your real Chrome profile):\n  ${profileDir}`);
console.log(`Shots will be written to (git-excluded):\n  ${outDir}\n`);

const blurListRaw = await rl.question(
  'Type values to blur (app name, Page name, profile name, client ID), comma-separated: ',
);
const blurValues = blurListRaw
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
  .map((s) => s.toLowerCase());

const launchOpts = {
  headless: false,
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
};

async function launchShotsBrowser() {
  const wantChrome = (process.env.HEARDLOOP_SHOTS_CHANNEL || '').toLowerCase() === 'chrome';
  if (wantChrome) {
    console.log('Launching installed Google Chrome (HEARDLOOP_SHOTS_CHANNEL=chrome).');
    return chromium.launchPersistentContext(profileDir, { ...launchOpts, channel: 'chrome' });
  }
  try {
    return await chromium.launchPersistentContext(profileDir, launchOpts);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (!/executable doesn't exist|browserType\.launch|chromium/i.test(msg)) throw err;
    console.log('Bundled Chromium missing; launching installed Google Chrome instead.');
    return chromium.launchPersistentContext(profileDir, { ...launchOpts, channel: 'chrome' });
  }
}

const context = await launchShotsBrowser();

await context.route('**/*', async (route) => {
  const req = route.request();
  const url = req.url();
  const method = req.method();
  if (/\.linkedin\.com/i.test(url) && method !== 'GET' && method !== 'HEAD') {
    blocked.push(`${method} ${url}`);
    console.log(`blocked non-GET: ${method} ${url}`);
    await route.abort();
    return;
  }
  await route.continue();
});

const page = context.pages()[0] || (await context.newPage());

await page.goto('https://www.linkedin.com/developers/apps', { waitUntil: 'domcontentloaded' });
await rl.question(
  'Sign in in the browser window, open your Heardloop app once, then press Enter here… ',
);
const appId = (await rl.question('App numeric ID from the URL (digits only): ')).trim();
if (!/^\d+$/.test(appId)) {
  console.error('Refusing: app id must be digits only.');
  await context.close();
  rl.close();
  process.exit(1);
}

async function blurSensitive() {
  await page.evaluate((values) => {
    document.querySelectorAll('img').forEach((img) => {
      img.style.filter = 'blur(8px)';
    });
    document.querySelectorAll('*').forEach((el) => {
      if (el.children.length) return;
      const text = (el.textContent || '').trim().toLowerCase();
      if (!text) return;
      if (values.some((v) => v && text.includes(v))) {
        el.style.filter = 'blur(8px)';
      }
    });
    document.querySelectorAll('*').forEach((el) => {
      const t = (el.textContent || '').trim();
      if (/^Client ID$/i.test(t) || /^Primary Client Secret$/i.test(t)) {
        let node = el.parentElement;
        for (let i = 0; i < 3 && node; i++) {
          node.style.filter = 'blur(8px)';
          node = node.parentElement;
        }
      }
    });
    document.querySelectorAll('[class*="notification"], [class*="badge"]').forEach((el) => {
      if (/^\d+$/.test((el.textContent || '').trim())) el.style.visibility = 'hidden';
    });
  }, blurValues);
}

async function safeClick(selector, { allowCreateNav = false } = {}) {
  const el = page.locator(selector).first();
  const text = ((await el.textContent().catch(() => '')) || '').trim();
  const tag = await el.evaluate((n) => n.tagName.toLowerCase()).catch(() => '');
  const href = (await el.getAttribute('href').catch(() => '')) || '';
  if (FORBIDDEN_CLICK.test(text)) {
    if (allowCreateNav && tag === 'a' && /\/developers\/apps\/new/i.test(href || '')) {
      await el.click();
      return;
    }
    throw new Error(`Refusing to click forbidden control: ${text}`);
  }
  await el.click();
}

async function shot(name) {
  await blurSensitive();
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(outDir, name), fullPage: false });
  console.log(`saved ${name}`);
}

await page.goto('https://www.linkedin.com/developers/apps', { waitUntil: 'networkidle' });
await shot('linkedin-01-my-apps.png');

// Create app form via navigation link only — never submit.
const createNav = page.locator('a[href*="/developers/apps/new"]').first();
if (await createNav.count()) {
  await safeClick('a[href*="/developers/apps/new"]', { allowCreateNav: true });
} else {
  await page.goto('https://www.linkedin.com/developers/apps/new', { waitUntil: 'domcontentloaded' });
}
await page.waitForTimeout(800);
const nameInput = page.locator('input[type="text"]').first();
if (await nameInput.count()) {
  await nameInput.fill('Heardloop (personal)');
}
// Do not tick legal, do not search pages, do not upload logo, do not submit.
await shot('linkedin-02-create-app.png');

const baseApp = `https://www.linkedin.com/developers/apps/${appId}`;
await page.goto(`${baseApp}/settings`, { waitUntil: 'networkidle' }).catch(async () => {
  await page.goto(baseApp, { waitUntil: 'networkidle' });
  const settingsTab = page.getByRole('link', { name: /settings/i }).first();
  if (await settingsTab.count()) await safeClick('a:text-matches("settings", "i")');
});
await shot('linkedin-03-verify.png');

await page.goto(`${baseApp}/products`, { waitUntil: 'networkidle' }).catch(async () => {
  const tab = page.getByRole('link', { name: /products/i }).first();
  if (await tab.count()) await safeClick('a:text-matches("products", "i")');
});
await shot('linkedin-04-products.png');

await page.goto(`${baseApp}/auth`, { waitUntil: 'networkidle' }).catch(async () => {
  const tab = page.getByRole('link', { name: /^auth$/i }).first();
  if (await tab.count()) await safeClick('a:text-matches("^auth$", "i")');
});
await page.evaluate(() => {
  const el = [...document.querySelectorAll('*')].find((n) =>
    /Authorized redirect URLs/i.test(n.textContent || ''),
  );
  el?.scrollIntoView({ block: 'center' });
});
await shot('linkedin-05-redirect.png');

await page.evaluate(() => {
  const el = [...document.querySelectorAll('*')].find((n) => /OAuth 2\.0 scopes/i.test(n.textContent || ''));
  el?.scrollIntoView({ block: 'center' });
});
await shot('linkedin-06-scopes.png');

await page.evaluate(() => window.scrollTo(0, 0));
await shot('linkedin-07-credentials.png');

await context.close();
rl.close();

const logPath = join(outDir, 'blocked-requests.txt');
writeFileSync(logPath, blocked.length ? blocked.join('\n') + '\n' : '(none)\n', 'utf8');
console.log('\nBlocked non-GET/HEAD requests:');
console.log(blocked.length ? blocked.join('\n') : '(none)');
console.log(`\nWrote log: ${logPath}`);
console.log(
  '\nSTOP. Do not copy these into docs/assets/install/ until the owner says "add linkedin shots".',
);
console.log(`Delete the profile folder when done: ${profileDir}`);
