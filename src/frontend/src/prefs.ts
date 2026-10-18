// Per-user interface preferences: applied to the document (theme, font size, accent, direction) and read by
// dates.ts for formats. Stored on the server (Settings -> Appearance); the .env UI_LANGUAGE is only the
// default for people who did not pick a language.
import { setLocale, type Locale } from './i18n';

export type DateFormat = 'dmy' | 'mdy' | 'ymd';
export type TimeFormat = '24h' | '12h';
export type FontSize = 's' | 'm' | 'l' | 'xl';
export type Theme = 'system' | 'light' | 'dark';

export interface UiPrefs {
  locale: Locale | null;
  date_format: DateFormat;
  time_format: TimeFormat;
  font_size: FontSize;
  theme: Theme;
  accent: string;
  /** Latest update version the owner dismissed on the banner (e.g. "0.9.1"). */
  dismissed_update: string | null;
}

export const DEFAULT_PREFS: UiPrefs = {
  locale: null,
  date_format: 'dmy',
  time_format: '24h',
  font_size: 'm',
  theme: 'system',
  accent: '#0a66c2',
  dismissed_update: null,
};

export const ACCENTS = ['#0a66c2', '#1a7f37', '#9333ea', '#c2410c', '#be123c', '#0f766e', '#4b5563'] as const;
const FONT_PX: Record<FontSize, number> = { s: 14, m: 16, l: 18, xl: 20 };

/** The preferences in effect (module state, like LOCALE in i18n.ts). */
export let PREFS: UiPrefs = DEFAULT_PREFS;
let serverLocale: Locale = 'en';

export function setServerLocale(locale: Locale): void {
  serverLocale = locale;
}

/** Applies everything that does not need React: language/direction, theme, font size, accent colour. */
export function applyPrefs(prefs: UiPrefs): void {
  PREFS = prefs;
  setLocale(prefs.locale ?? serverLocale);
  const root = document.documentElement;
  if (prefs.theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', prefs.theme);
  root.style.fontSize = `${FONT_PX[prefs.font_size]}px`;
  root.style.setProperty('--accent', prefs.accent);
  root.style.setProperty('--accent-rgb', hexToRgb(prefs.accent));
}

function hexToRgb(hex: string): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}
