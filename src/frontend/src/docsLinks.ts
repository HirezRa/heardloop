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
