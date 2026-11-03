/** Stable GitHub doc anchors shared by About, banners and e-mail copy. */

export const REPO = 'https://github.com/HirezRa/heardloop';

/** Language-independent INSTALL anchor for update steps (EN + HE). */
export const UPDATE_DOC_HASH = '#update';

export function installDocUrl(pathEn: string, pathHe: string | undefined, locale: string): string {
  const path = locale === 'he' && pathHe ? pathHe : pathEn;
  return `${REPO}/blob/main/${path}`;
}

export function installUpdateDocUrl(locale: string): string {
  return installDocUrl('docs/INSTALL.md', 'docs/he/INSTALL.md', locale) + UPDATE_DOC_HASH;
}

/** The operator's own pages (PRIVACY_URL / ACCESSIBILITY_URL from /api/v1/meta), read once at start-up. */
let operatorPages: { privacy: string | null; accessibility: string | null } = { privacy: null, accessibility: null };

export function setOperatorPages(privacy: string | null | undefined, accessibility: string | null | undefined): void {
  const safe = (u: string | null | undefined) => (u && /^https?:\/\//i.test(u) ? u : null);
  operatorPages = { privacy: safe(privacy), accessibility: safe(accessibility) };
}

/** Privacy notice: the operator's page, or the template in the repository. */
export function privacyUrl(locale: string): string {
  return operatorPages.privacy ?? installDocUrl('docs/PRIVACY.md', 'docs/he/PRIVACY.md', locale);
}

/** Accessibility statement: the operator's page, or the template in the repository. */
export function accessibilityUrl(locale: string): string {
  return operatorPages.accessibility ?? installDocUrl('docs/ACCESSIBILITY.md', 'docs/he/ACCESSIBILITY.md', locale);
}
