import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { setLocale, type Locale } from './i18n';
import { setServerLocale } from './prefs';
import './styles.css';

/** UI language comes from the server (.env UI_LANGUAGE); English if the server cannot be reached. */
async function loadLocale(): Promise<Locale> {
  try {
    const res = await fetch('/api/v1/meta', { headers: { Accept: 'application/json' } });
    if (!res.ok) return 'en';
    const meta = (await res.json()) as { ui_language?: string };
    return meta.ui_language === 'he' ? 'he' : 'en';
  } catch {
    return 'en';
  }
}

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

loadLocale().then((locale) => {
  setServerLocale(locale);
  setLocale(locale);
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
