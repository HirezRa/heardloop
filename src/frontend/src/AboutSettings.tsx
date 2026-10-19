import { useEffect, useState } from 'react';
import { checkAppVersion, getAppVersion, type AppVersion, type Me } from './api';
import { LOCALE, t } from './i18n';
import { notesAsNodes } from './notesText';

const REPO = 'https://github.com/HirezRa/heardloop';
const BUNDLE = __HEARDLOOP_BUNDLE_VERSION__;
const HEX_SHA = /^[0-9a-f]{7,40}$/i;

function docUrl(pathEn: string, pathHe?: string): string {
  const path = LOCALE === 'he' && pathHe ? pathHe : pathEn;
  return `${REPO}/blob/main/${path}`;
}

function showBuild(sha: string | undefined): boolean {
  const v = (sha || '').trim();
  return !!v && v !== 'unknown' && HEX_SHA.test(v);
}

export default function AboutSettings({
  me,
  version,
  onVersion,
}: {
  me: Me;
  version: AppVersion | null;
  onVersion: (v: AppVersion | null) => void;
}) {
  const isOwner = me.memberships[0]?.role === 'owner';
  const [info, setInfo] = useState<AppVersion | null>(version);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showNotes, setShowNotes] = useState(false);

  useEffect(() => {
    setInfo(version);
  }, [version]);

  useEffect(() => {
    if (version) return;
    getAppVersion()
      .then((v) => {
        setInfo(v);
        onVersion(v);
      })
      .catch(() => setError(t.about.loadFailed));
  }, [version, onVersion]);

  async function checkNow() {
    setBusy(true);
    setError('');
    try {
      const v = await checkAppVersion();
      setInfo(v);
      onVersion(v);
    } catch {
      setError(t.about.checkFailed);
    } finally {
      setBusy(false);
    }
  }

  const releaseUrl = info ? `${REPO}/releases/tag/v${info.version}` : `${REPO}/releases`;
  const stale = !!info && info.version !== BUNDLE;

  return (
    <section className="card-free">
      <h2>{t.about.title}</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {info && (
        <>
          <p>
            {t.about.versionLabel}: <strong>v{info.version}</strong>
          </p>
          {stale && (
            <p className="banner version-stale" role="status">
              {t.about.staleBundle}{' '}
              <button type="button" onClick={() => window.location.reload()}>
                {t.about.reload}
              </button>
            </p>
          )}
          {showBuild(info.git_sha) && (
            <p className="muted small">
              {t.about.gitSha}: <code>{info.git_sha}</code>
            </p>
          )}
          <p className="muted small">{t.about.license}</p>
          <p>
            <a href={releaseUrl} target="_blank" rel="noopener noreferrer">
              {t.about.releasePage}
            </a>
          </p>
          <ul className="plain-list">
            <li>
              <a href={docUrl('docs/INSTALL.md', 'docs/he/INSTALL.md')} target="_blank" rel="noopener noreferrer">
                {t.about.installGuide}
              </a>
            </li>
            <li>
              <a href={docUrl('docs/GUIDE.md')} target="_blank" rel="noopener noreferrer">
                {t.about.userGuide}
              </a>
            </li>
            <li>
              <a href={docUrl('docs/WEBHOOKS.md')} target="_blank" rel="noopener noreferrer">
                {t.about.webhooks}
              </a>
            </li>
            <li>
              <a
                href={docUrl('examples/n8n/README.md', 'docs/he/n8n.md')}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t.about.n8nExample}
              </a>
            </li>
          </ul>
          {isOwner && (
            <div className="stack" style={{ marginTop: 16 }}>
              <h3>{t.about.updates}</h3>
              {info.update?.available ? (
                <p>
                  {t.about.available(info.update.latest)}{' '}
                  <button type="button" className="link" onClick={() => setShowNotes(true)}>
                    {t.about.whatsNew}
                  </button>
                  {' · '}
                  <a
                    href={docUrl('docs/INSTALL.md', 'docs/he/INSTALL.md') + '#11-backups-and-updates'}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {t.about.howToUpdate}
                  </a>
                </p>
              ) : (
                <p className="muted">{t.about.upToDate(info.version)}</p>
              )}
              <button type="button" onClick={checkNow} disabled={busy}>
                {busy ? t.about.checking : t.about.checkNow}
              </button>
            </div>
          )}
        </>
      )}
      {showNotes && info?.update && (
        <dialog open className="notes-dialog" onClose={() => setShowNotes(false)}>
          <form method="dialog">
            <h3>{t.about.whatsNewTitle(info.update.latest)}</h3>
            <div className="notes-body">{notesAsNodes(info.update.notes || t.about.noNotes)}</div>
            <button type="submit">{t.about.close}</button>
          </form>
        </dialog>
      )}
    </section>
  );
}
