/**
 * WCAG contrast of theme colours (1.4.3): every accent choice and every status colour must reach 4.5:1 as
 * text on the light and the dark backgrounds.
 * Run from src/frontend: node --experimental-strip-types ./src/contrast.test.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DARK_BACKGROUNDS, LIGHT_BACKGROUNDS, TEXT_RATIO, contrastRatio, readableOn } from './contrast.ts';

const here = new URL('.', import.meta.url);
const prefs = readFileSync(new URL('prefs.ts', here), 'utf8');
const accents = [...prefs.match(/ACCENTS = \[([^\]]+)\]/)[1].matchAll(/'(#[0-9a-f]{6})'/gi)].map((m) => m[1]);
assert.ok(accents.length >= 7, 'accent list found');

for (const accent of accents) {
  const light = readableOn(accent, LIGHT_BACKGROUNDS);
  const dark = readableOn(accent, DARK_BACKGROUNDS);
  assert.equal(light, accent.toLowerCase(), `${accent} is used unchanged in light mode`);
  for (const bg of LIGHT_BACKGROUNDS) assert.ok(contrastRatio(light, bg) >= TEXT_RATIO, `${accent} on ${bg}`);
  for (const bg of DARK_BACKGROUNDS) assert.ok(contrastRatio(dark, bg) >= TEXT_RATIO, `${accent} -> ${dark} on ${bg}`);
  // White text on the filled accent (buttons, active tab) stays readable.
  assert.ok(contrastRatio('#ffffff', accent) >= TEXT_RATIO, `white on ${accent}`);
}

// Status colours as defined in styles.css, light (:root) and dark (data-theme="dark").
const css = readFileSync(new URL('styles.css', here), 'utf8');
const block = (selector) => {
  const start = css.indexOf(selector);
  assert.ok(start >= 0, `${selector} in styles.css`);
  return css.slice(start, css.indexOf('}', start));
};
const vars = (text) => Object.fromEntries([...text.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2]]));
const light = vars(block(':root {'));
const dark = { ...light, ...vars(block(':root[data-theme="dark"] {')) };
for (const name of ['text', 'muted', 'ok', 'warn', 'bad']) {
  for (const bg of ['bg', 'panel']) {
    assert.ok(contrastRatio(light[name], light[bg]) >= TEXT_RATIO, `light --${name} on --${bg}`);
    assert.ok(contrastRatio(dark[name], dark[bg]) >= TEXT_RATIO, `dark --${name} on --${bg}`);
  }
}
assert.ok(contrastRatio(dark['on-bad'], dark.bad) >= TEXT_RATIO, 'dark count badge text');
assert.ok(contrastRatio(light['on-bad'], light.bad) >= TEXT_RATIO, 'light count badge text');

console.log('ok contrast');
