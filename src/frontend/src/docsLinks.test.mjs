/**
 * "How to update" must use the language-independent #update anchor.
 * Run: node --experimental-strip-types src/frontend/src/docsLinks.test.mjs
 */
import assert from 'node:assert/strict';
import { installUpdateDocUrl, UPDATE_DOC_HASH } from './docsLinks.ts';

assert.equal(UPDATE_DOC_HASH, '#update');
assert.equal(
  installUpdateDocUrl('en'),
  'https://github.com/HirezRa/heardloop/blob/main/docs/INSTALL.md#update',
);
assert.equal(
  installUpdateDocUrl('he'),
  'https://github.com/HirezRa/heardloop/blob/main/docs/he/INSTALL.md#update',
);
assert.ok(!installUpdateDocUrl('he').includes('#11-'));
assert.ok(!installUpdateDocUrl('en').includes('#11-'));

console.log('ok docsLinks update anchors');
