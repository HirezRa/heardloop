/**
 * String-level guard: About error copy must never say the install is "the latest version".
 * Run: node src/frontend/src/updateStatus.honesty.test.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '../../..');
const i18n = readFileSync(join(root, 'src/frontend/src/i18n.ts'), 'utf8');
const helper = readFileSync(join(root, 'src/frontend/src/updateStatus.ts'), 'utf8');

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// upToDate may say "latest version"; checkError must not claim the running build is latest.
const checkErrorBlock = i18n.match(/checkError:[\s\S]*?errorReasons:/);
assert(checkErrorBlock, 'checkError missing');
assert(
  !/you are on the latest version/i.test(checkErrorBlock[0]),
  'checkError must not say "you are on the latest version"',
);

assert(helper.includes('errorCopyLooksHonest'), 'honesty helper missing');
assert(helper.includes("includes('latest version')"), 'honesty helper incomplete');

console.log('ok update-status honesty strings');
