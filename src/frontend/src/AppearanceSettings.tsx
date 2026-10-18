import { useState } from 'react';
import { saveUiPrefs, type Me } from './api';
import { t } from './i18n';
import { ACCENTS, applyPrefs, PREFS, type DateFormat, type FontSize, type Theme, type TimeFormat, type UiPrefs } from './prefs';

/**
 * Per-user interface preferences. Theme, font size and colour preview live while choosing; everything is
 * applied for real (and the app re-rendered) when saved. Nothing here touches .env or the server config.
 */
export default function AppearanceSettings({ me, onMeChange }: { me: Me; onMeChange: (me: Me) => void }) {
  const [draft, setDraft] = useState<UiPrefs>(me.ui_prefs);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(me.ui_prefs);

  /** Cosmetic changes show at once; the saved preferences come back if the user leaves without saving. */
  function preview(next: UiPrefs) {
    setDraft(next);
    applyPrefs({ ...PREFS, theme: next.theme, font_size: next.font_size, accent: next.accent });
  }

  async function save() {
    setBusy(true);
    setMsg({});
    try {
      const saved = await saveUiPrefs(draft);
      onMeChange({ ...me, ui_prefs: saved }); // App applies and remounts with the new language/formats
      setMsg({ ok: t.settings.saved });
    } catch {
      setMsg({ error: t.errors.generic });
    } finally {
      setBusy(false);
    }
  }

  function revert() {
    setDraft(me.ui_prefs);
    applyPrefs(me.ui_prefs);
    setMsg({});
  }

  const sample = new Date(2026, 9, 16, 14, 5);
  const radio = <K extends keyof UiPrefs>(key: K, value: UiPrefs[K], label: string) => (
    <button
      key={String(value)}
      type="button"
      role="radio"
      aria-checked={draft[key] === value}
      className={draft[key] === value ? 'choice active' : 'choice'}
      disabled={busy}
      onClick={() => preview({ ...draft, [key]: value })}
    >
      {label}
    </button>
  );

  return (
    <section className="card">
      <h2>{t.appearance.title}</h2>
      <p className="muted small">{t.appearance.hint}</p>
      <div className="prefs">
        <div className="pref">
          <span className="pref-label">{t.appearance.language}</span>
          <div className="choices" role="radiogroup">
            {radio('locale', null, t.appearance.serverDefault)}
            {radio('locale', 'en', t.appearance.locales.en)}
            {radio('locale', 'he', t.appearance.locales.he)}
          </div>
        </div>
        <div className="pref">
          <span className="pref-label">{t.appearance.theme}</span>
          <div className="choices" role="radiogroup">
            {(['system', 'light', 'dark'] as Theme[]).map((v) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={draft.theme === v}
                className={draft.theme === v ? 'theme-tile active' : 'theme-tile'}
                disabled={busy}
                onClick={() => preview({ ...draft, theme: v })}
              >
                <span className={`tile tile-${v}`} aria-hidden="true">
                  <span className="tile-bar" />
                  <span className="tile-line" />
                  <span className="tile-line short" />
                </span>
                {t.appearance.themes[v]}
              </button>
            ))}
          </div>
        </div>
        <div className="pref">
          <span className="pref-label">{t.appearance.fontSize}</span>
          <div className="choices" role="radiogroup">
            {(['s', 'm', 'l', 'xl'] as FontSize[]).map((v) => radio('font_size', v, t.appearance.fontSizes[v]))}
          </div>
        </div>
        <div className="pref">
          <span className="pref-label">{t.appearance.accent}</span>
          <div className="choices color-row" role="radiogroup">
            {ACCENTS.map((c) => (
              <button
                key={c}
                type="button"
                role="radio"
                aria-checked={draft.accent === c}
                aria-label={c}
                className={draft.accent === c ? 'swatch active' : 'swatch'}
                style={{ background: c }}
                disabled={busy}
                onClick={() => preview({ ...draft, accent: c })}
              />
            ))}
          </div>
        </div>
        <div className="pref">
          <span className="pref-label">{t.appearance.dateFormat}</span>
          <div className="choices" role="radiogroup">
            {(['dmy', 'mdy', 'ymd'] as DateFormat[]).map((v) =>
              radio('date_format', v, v === 'dmy' ? '16/10/2026' : v === 'mdy' ? '10/16/2026' : '2026-10-16'),
            )}
          </div>
        </div>
        <div className="pref">
          <span className="pref-label">{t.appearance.timeFormat}</span>
          <div className="choices" role="radiogroup">
            {(['24h', '12h'] as TimeFormat[]).map((v) => radio('time_format', v, v === '24h' ? '14:05' : '2:05 PM'))}
          </div>
        </div>
        <p className="muted small">
          {t.appearance.sample}: <span dir="ltr">{sampleText(draft, sample)}</span>
        </p>
      </div>
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !dirty} onClick={() => void save()}>
          {t.settings.save}
        </button>
        {dirty && (
          <button type="button" className="link" disabled={busy} onClick={revert}>
            {t.appearance.revert}
          </button>
        )}
      </div>
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
    </section>
  );
}

function sampleText(p: UiPrefs, d: Date): string {
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  const day = p.date_format === 'mdy' ? `${mm}/${dd}/${yyyy}` : p.date_format === 'ymd' ? `${yyyy}-${mm}-${dd}` : `${dd}/${mm}/${yyyy}`;
  const h = d.getHours();
  const mi = String(d.getMinutes()).padStart(2, '0');
  const time = p.time_format === '12h' ? `${h % 12 || 12}:${mi} ${h < 12 ? 'AM' : 'PM'}` : `${String(h).padStart(2, '0')}:${mi}`;
  return `${day} ${time}`;
}
