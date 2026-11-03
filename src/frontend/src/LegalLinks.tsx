import { accessibilityUrl, privacyUrl } from './docsLinks';
import { LOCALE, t } from './i18n';

/** Privacy notice and accessibility statement: on the sign-in page and at the bottom of the menu. */
export default function LegalLinks() {
  return (
    <nav className="legal-links small" aria-label={t.legal.label}>
      <a href={privacyUrl(LOCALE)} target="_blank" rel="noopener noreferrer">
        {t.legal.privacy}
      </a>
      <a href={accessibilityUrl(LOCALE)} target="_blank" rel="noopener noreferrer">
        {t.legal.accessibility}
      </a>
    </nav>
  );
}
