/**
 * Safe text cut: bold Unicode letters are two UTF-16 units each; slice by code unit can split one.
 * Run from src/frontend: node --experimental-strip-types ./src/textCut.test.mjs
 */
import assert from 'node:assert/strict';
import { applyStyle } from './unicodeFormat.ts';
import { truncateCodePoints } from './textCut.ts';

const bold = applyStyle('ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ', 'bold');
assert.equal(Array.from(bold).length, 78);
assert.equal(bold.length, 156, 'each bold letter is a surrogate pair');

const naive = bold.slice(0, 79);
assert.equal(naive.length, 79);
const last = naive.charCodeAt(naive.length - 1);
assert.ok(last >= 0xd800 && last <= 0xdbff, 'precondition: naive UTF-16 slice ends inside a pair');

const cut = truncateCodePoints(bold, 40);
assert.ok(cut.endsWith('…'));
const body = cut.slice(0, -1);
assert.equal(Array.from(body).length, 39);
assert.equal(body.length, 78, '39 complete bold letters = 78 UTF-16 units');
assert.equal(body, bold.slice(0, 78));

assert.equal(truncateCodePoints(bold, 100), bold);

console.log('ok truncateCodePoints');
