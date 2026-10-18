// Maps publish error codes (from the worker) to UI copy in i18n.ts.
// This file stays English-only; Hebrew strings live in i18n.ts.
import { t } from './i18n';

/** Every code in the backend KNOWN_CODES set must appear as a key here (asserted by a Python test). */
export const PUBLISH_ERROR_CODES: Record<string, true> = {
  no_content: true,
  account_not_connected: true,
  token_expired: true,
  token_unreadable: true,
  media_missing: true,
  video_with_link: true,
  unknown_outcome: true,
  timeout_unknown_outcome: true,
  network_error: true,
  network: true,
  token_invalid: true,
  not_permitted: true,
  api_version_retired: true,
  no_post_id: true,
  linkedin_not_configured: true,
  linkedin_4xx: true,
  linkedin_5xx: true,
  video_upload_timeout: true,
  image_upload_timeout: true,
  video_processing: true,
  video_processing_failed: true,
  rate_limited: true,
  abandoned_by_user: true,
};

type Entry = { message: string; whatToDo?: string };

function entryFor(code: string): Entry | undefined {
  const map = t.publishErrors as Record<string, Entry>;
  if (map[code]) return map[code];
  if (/^http_4\d\d/.test(code)) return map.linkedin_4xx;
  if (/^http_5\d\d/.test(code)) return map.linkedin_5xx;
  return undefined;
}

/** Maps a raw last_error or code to a short sentence (+ optional what-to-do). */
export function publishErrorText(raw: string | null | undefined): { message: string; whatToDo?: string } {
  const code = (raw || 'unknown').split(':', 1)[0].trim();
  const info = entryFor(code);
  if (!info) return { message: code || 'unknown' };
  return { message: info.message, whatToDo: info.whatToDo };
}

export function publishErrorReasonLabel(code: string | null | undefined): string {
  return publishErrorText(code).message;
}
