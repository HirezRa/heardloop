// One place for date display and date input formats, driven by the user's preferences (Settings -> Appearance).
// Dates are always numeric (dd/mm/yyyy, mm/dd/yyyy or yyyy-mm-dd) and times 24h or 12h, in every language.
import { PREFS } from './prefs';

const pad = (n: number) => String(n).padStart(2, '0');

function parts(value: Date | string, timeZone?: string): Record<string, string> {
  const d = typeof value === 'string' ? new Date(value) : value;
  return Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(d)
      .map((p) => [p.type, p.value]),
  );
}

function joinDay(y: string, m: string, d: string): string {
  switch (PREFS.date_format) {
    case 'mdy':
      return `${m}/${d}/${y}`;
    case 'ymd':
      return `${y}-${m}-${d}`;
    default:
      return `${d}/${m}/${y}`;
  }
}

function joinTime(h: string, mi: string): string {
  if (PREFS.time_format === '12h') {
    const hour = Number(h);
    const suffix = hour < 12 ? 'AM' : 'PM';
    return `${hour % 12 || 12}:${mi} ${suffix}`;
  }
  return `${h}:${mi}`;
}

/** What the user types in a date field, e.g. "dd/mm/yyyy". */
export function datePlaceholder(): string {
  return PREFS.date_format === 'mdy' ? 'mm/dd/yyyy' : PREFS.date_format === 'ymd' ? 'yyyy-mm-dd' : 'dd/mm/yyyy';
}

export function timePlaceholder(): string {
  return PREFS.time_format === '12h' ? 'hh:mm AM' : 'HH:mm';
}

/** A moment in time as a date, e.g. 16/10/2026. */
export function formatDate(value: Date | string, timeZone?: string): string {
  const p = parts(value, timeZone);
  return joinDay(p.year, p.month, p.day);
}

/** A moment in time with hours and minutes, e.g. 16/10/2026 22:30. */
export function formatDateTime(value: Date | string, timeZone?: string): string {
  const p = parts(value, timeZone);
  return `${joinDay(p.year, p.month, p.day)} ${joinTime(p.hour, p.minute)}`;
}

/** A calendar day without a time zone ("2026-10-16") for display. */
export function formatDay(isoDay: string): string {
  const [y, m, d] = isoDay.split('-');
  return joinDay(y, m, d);
}

/** Typed day in the user's format -> "yyyy-mm-dd", or null if it is not a real date. */
export function parseDay(text: string): string | null {
  const m = /^\s*(\d{1,4})[/.-](\d{1,2})[/.-](\d{1,4})\s*$/.exec(text);
  if (!m) return null;
  let d: number;
  let mo: number;
  let y: number;
  if (PREFS.date_format === 'ymd' || m[1].length === 4) [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  else if (PREFS.date_format === 'mdy') [mo, d, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  else [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (y < 1000) return null;
  const date = new Date(y, mo - 1, d);
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return `${y}-${pad(mo)}-${pad(d)}`;
}

/** "HH:mm", "h:mm am/pm" -> normalized 24h "HH:mm", or null. */
export function parseTime(text: string): string | null {
  const m = /^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$/.exec(text);
  if (!m) return null;
  let h = Number(m[1]);
  const mi = Number(m[2]);
  if (m[3]) {
    if (h < 1 || h > 12) return null;
    h = (h % 12) + (m[3].toLowerCase() === 'pm' ? 12 : 0);
  }
  return h < 24 && mi < 60 ? `${pad(h)}:${pad(mi)}` : null;
}

/** "yyyy-mm-dd" -> the user's format for text inputs. */
export function dayToText(isoDay: string | null): string {
  if (!isoDay) return '';
  const [y, m, d] = isoDay.split('-');
  return joinDay(y, m, d);
}

/** "HH:mm" (24h) -> the user's time format for text inputs. */
export function timeToText(hhmm: string): string {
  const [h, mi] = hhmm.split(':');
  return h && mi ? joinTime(h, mi) : hhmm;
}
