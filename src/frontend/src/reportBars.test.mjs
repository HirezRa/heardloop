/**
 * Empty buckets and small samples for report bars.
 * Run from src/frontend: node --experimental-strip-types ./src/reportBars.test.mjs
 */
import assert from 'node:assert/strict';
import { REPORT_COMPARE_MIN_POSTS, barTooFew, bucketMetric } from './reportBars.ts';

const empty = {
  key: '0',
  label: null,
  posts: 0,
  measured: 0,
  impressions: 0,
  engagement: 0,
  members_reached: 0,
  followers_gained: 0,
  avg_impressions: null,
  avg_engagement_rate: null,
  weekend: false,
};

const one = { ...empty, posts: 1, measured: 1, avg_engagement_rate: 0.05, avg_impressions: 100 };
const three = { ...empty, posts: 3, measured: 3, avg_engagement_rate: 0.1, avg_impressions: 200 };

assert.equal(REPORT_COMPARE_MIN_POSTS, 3);
assert.equal(bucketMetric(empty, 'engagement_rate'), null);
assert.equal(bucketMetric(empty, 'impressions'), null);
assert.equal(bucketMetric(one, 'engagement_rate'), 0.05);
assert.equal(bucketMetric(one, 'impressions'), 100);
assert.equal(barTooFew(empty), false);
assert.equal(barTooFew(one), true);
assert.equal(barTooFew(three), false);

console.log('ok reportBars');
