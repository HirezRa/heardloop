/**
 * Serve dist/ and assert the login form mounts with no console errors.
 * Run after `npm run build`. Keep under 60 s (Chromium only).
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = await freePort();
const base = `http://127.0.0.1:${port}`;

const viteBin = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js');
const preview = spawn(process.execPath, [viteBin, 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
  cwd: root,
  stdio: ['ignore', 'pipe', 'pipe'],
  env: process.env,
});

try {
  await waitFor(() => probe(base), 20_000);
  // Prefer bundled Chromium (CI). Fall back to system Chrome when the browser
  // download is unavailable (corporate TLS / offline local runs).
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
  } catch {
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
  }
  try {
    const page = await browser.newPage();
    const consoleErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(String(err)));

    await page.goto(base, { waitUntil: 'networkidle', timeout: 20_000 });
    await page.waitForSelector('form.login-card input[type="email"]', { timeout: 15_000 });
    await page.waitForSelector('form.login-card input[type="password"]', { timeout: 5_000 });
    const brand = await page.locator('.brand').first().textContent();
    if (!brand || !brand.includes('Heardloop')) {
      throw new Error(`expected Heardloop brand, got ${JSON.stringify(brand)}`);
    }
    // Ignore failed /api calls (no backend in this smoke); React version mismatches and
    // render crashes show up as pageerror / console error.
    const actionable = consoleErrors.filter(
      (line) => !/Failed to load resource|net::ERR_|404 \(Not Found\)|401 \(Unauthorized\)/i.test(line),
    );
    if (actionable.length) {
      throw new Error(`console errors:\n${actionable.join('\n')}`);
    }
    console.log('frontend smoke ok');
  } finally {
    await browser.close();
  }
} finally {
  preview.kill('SIGTERM');
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const p = typeof addr === 'object' && addr ? addr.port : 0;
      server.close((err) => (err ? reject(err) : resolve(p)));
    });
    server.on('error', reject);
  });
}

function probe(url) {
  return fetch(url)
    .then((r) => r.ok)
    .catch(() => false);
}

async function waitFor(fn, ms) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`timeout waiting for ${base}`);
}
