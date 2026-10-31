import type { ReportBucket } from './api';

/** Bars with fewer posts than this are marked "too few posts to compare". */
export const REPORT_COMPARE_MIN_POSTS = 3;

export type ReportBarMetric = 'engagement_rate' | 'impressions';

export function bucketMetric(b: ReportBucket, metric: ReportBarMetric): number | null {
  if (b.posts === 0) return null;
  if (metric === 'impressions') return b.avg_impressions;
  return b.avg_engagement_rate;
}

export function barTooFew(b: ReportBucket): boolean {
  return b.posts > 0 && b.posts < REPORT_COMPARE_MIN_POSTS;
}
