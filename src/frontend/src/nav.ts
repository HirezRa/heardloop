// In-app navigation helpers: the address bar mirrors the screen (?section=, ?tab=, ?post=) so Back/Forward,
// reload and shared links work, plus a guard for unsaved edits shared by every way of leaving a screen.
import { t } from './i18n';

export type NavParams = { tab?: string; post?: string };

/** Fired after the app changes the URL itself (pushState fires no popstate); screens with sub-state listen. */
export const NAV_EVENT = 'heardloop:navigate';

/** The URL of a screen. The calendar is the home screen and lives at "/". */
export function sectionUrl(section: string, params: NavParams = {}): string {
  const q = new URLSearchParams();
  if (section !== 'calendar') q.set('section', section);
  if (params.tab) q.set('tab', params.tab);
  if (params.post) q.set('post', params.post);
  const s = q.toString();
  return s ? `/?${s}` : '/';
}

function here(): string {
  return window.location.pathname + window.location.search;
}

// The last URL the app itself showed: where to return when the user cancels a Back with unsaved edits.
let lastUrl: string | null = null;

export function lastAppUrl(): string {
  return lastUrl ?? here();
}

/** New history entry (screen change). Same URL again replaces instead of stacking duplicates. */
export function pushUrl(url: string): void {
  if (url === here()) window.history.replaceState(window.history.state, '', url);
  else window.history.pushState(null, '', url);
  lastUrl = url;
  window.dispatchEvent(new Event(NAV_EVENT));
}

/** Same history entry, new address (e.g. which post is open). */
export function replaceUrl(url: string): void {
  if (url !== here()) window.history.replaceState(window.history.state, '', url);
  lastUrl = url;
}

/** Called after a popstate the app accepted. */
export function acceptCurrentUrl(): void {
  lastUrl = here();
}

// ---- Unsaved edits ----
let unsaved = false;

/** The post editor reports whether it holds changes that are not saved yet. */
export function setUnsaved(value: boolean): void {
  unsaved = value;
}

export function hasUnsaved(): boolean {
  return unsaved;
}

/**
 * True when it is fine to leave: nothing unsaved, or the user agreed to drop the changes. The flag itself is
 * owned by the editor (it clears when the draft is replaced or the editor unmounts).
 */
export function confirmDiscard(): boolean {
  return !unsaved || window.confirm(t.ux.unsavedLeave);
}
