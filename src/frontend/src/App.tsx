import { lazy, Suspense, useEffect, useState } from 'react';
import {
  fetchHealth,
  fetchMe,
  getAppVersion,
  logout,
  saveUiPrefs,
  unreadNotifications,
  type AppUpdateInfo,
  type AppVersion,
  type HealthState,
  type Me,
} from './api';
import { installUpdateDocUrl } from './docsLinks';
import { applyPrefs } from './prefs';
import { LOCALE, t } from './i18n';
import { notesAsNodes } from './notesText';
import Calendar from './Calendar';
import Login from './Login';
import LegalLinks from './LegalLinks';
import NotesDialog from './NotesDialog';

// Screens other than the calendar (the default) load on first use: the first download stays small.
const Accounts = lazy(() => import('./Accounts'));
const Approvals = lazy(() => import('./Approvals'));
const Campaigns = lazy(() => import('./Campaigns'));
const Notifications = lazy(() => import('./Notifications'));
const Posts = lazy(() => import('./Posts'));
const Reports = lazy(() => import('./Reports'));
const Settings = lazy(() => import('./Settings'));
const Templates = lazy(() => import('./Templates'));

type SectionId = keyof typeof t.sections;

const SECTIONS: { id: SectionId; phase: number }[] = [
  { id: 'calendar', phase: 3 },
  { id: 'posts', phase: 2 },
  { id: 'approvals', phase: 3 },
  { id: 'campaigns', phase: 3 },
  { id: 'templates', phase: 5 },
  { id: 'reports', phase: 4 },
  { id: 'accounts', phase: 2 },
  { id: 'settings', phase: 1 },
  { id: 'notifications', phase: 3 },
];

// Shown through the bell, not as a menu entry.
const HIDDEN_IN_MENU: SectionId[] = ['notifications'];

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  // Strings and direction live in module state (i18n.ts); remount the shell when preferences change
  // so every screen picks up the new language, formats and sizes at once.
  const [prefsKey, setPrefsKey] = useState('');

  function acceptMe(next: Me | null) {
    if (next) {
      applyPrefs(next.ui_prefs);
      setPrefsKey(JSON.stringify(next.ui_prefs));
    }
    setMe(next);
  }

  useEffect(() => {
    fetchMe()
      .then(acceptMe)
      .catch(() => setMe(null));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (me === undefined) return <div className="loading">{t.loading}</div>;
  if (me === null) return <Login onLoggedIn={acceptMe} />;
  return <Shell key={prefsKey} me={me} onMeChange={acceptMe} onLoggedOut={() => setMe(null)} />;
}

function Shell({ me, onMeChange, onLoggedOut }: { me: Me; onMeChange: (me: Me) => void; onLoggedOut: () => void }) {
  const [active, setActive] = useState<SectionId>(() => {
    const wanted = new URLSearchParams(window.location.search).get('section');
    return SECTIONS.some((s) => s.id === wanted) ? (wanted as SectionId) : 'calendar';
  });
  // An unknown path or ?section= value (old or mistyped link) gets a "not found" screen, not a silent redirect.
  const [notFound, setNotFound] = useState(() => {
    const wanted = new URLSearchParams(window.location.search).get('section');
    return window.location.pathname !== '/' || (wanted !== null && !SECTIONS.some((s) => s.id === wanted));
  });
  const [health, setHealth] = useState<HealthState>('checking');
  // Links from e-mails and webhooks: /?section=posts&post=<id>
  const [focusPostId, setFocusPostId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get('post'),
  );
  const [unread, setUnread] = useState(0);
  const [appVersion, setAppVersion] = useState<AppVersion | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);

  useEffect(() => {
    const check = () =>
      unreadNotifications()
        .then((r) => setUnread(r.unread))
        .catch(() => undefined);
    check();
    const timer = window.setInterval(check, 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    getAppVersion()
      .then(setAppVersion)
      .catch(() => setAppVersion(null));
  }, []);

  function openPost(postId: string) {
    setFocusPostId(postId);
    setActive('posts');
  }

  function go(id: SectionId) {
    setFocusPostId(null);
    if (notFound) {
      setNotFound(false);
      window.history.replaceState(null, '', '/');
    }
    setActive(id);
  }

  /** Settings -> Workspace, where audiences are managed (the editor and the campaign form link here). */
  function openAudiences() {
    window.history.replaceState(null, '', '?section=settings&tab=workspace');
    go('settings');
  }

  useEffect(() => {
    const controller = new AbortController();
    const check = () => fetchHealth(controller.signal).then(setHealth);
    check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  async function signOut() {
    try {
      await logout();
    } finally {
      onLoggedOut();
    }
  }

  async function dismissUpdate(update: AppUpdateInfo) {
    const next = { ...me.ui_prefs, dismissed_update: update.latest };
    try {
      const saved = await saveUiPrefs(next);
      applyPrefs(saved);
      onMeChange({ ...me, ui_prefs: saved });
    } catch {
      /* keep banner if save fails */
    }
  }

  const section = SECTIONS.find((s) => s.id === active) ?? SECTIONS[0];
  const [title, description] = t.sections[section.id];
  const workspace = me.memberships[0];
  const update = workspace?.role === 'owner' ? appVersion?.update : null;
  const showUpdateBanner =
    !!update?.available && update.latest !== me.ui_prefs.dismissed_update;
  const installDoc = installUpdateDocUrl(LOCALE);
  const versionHref = appVersion
    ? `https://github.com/HirezRa/heardloop/releases/tag/v${appVersion.version}`
    : 'https://github.com/HirezRa/heardloop/releases';

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Heardloop</div>
        {workspace && <div className="muted small">{workspace.workspace_name}</div>}
        <nav aria-label={t.nav}>
          {SECTIONS.filter((s) => !HIDDEN_IN_MENU.includes(s.id)).map((s) => (
            <button
              key={s.id}
              type="button"
              className={s.id === active && !notFound ? 'nav-item active' : 'nav-item'}
              aria-current={s.id === active && !notFound ? 'page' : undefined}
              onClick={() => go(s.id)}
            >
              {t.sections[s.id][0]}
            </button>
          ))}
        </nav>
        <button
          type="button"
          className={active === 'notifications' ? 'nav-item bell active' : 'nav-item bell'}
          aria-current={active === 'notifications' ? 'page' : undefined}
          onClick={() => go('notifications')}
        >
          <span aria-hidden="true">🔔</span> {t.notifications.title}
          {unread > 0 && (
            <span className="count" aria-label={t.notifications.unread(unread)}>
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </button>
        <div className="sidebar-footer">
          <div className="muted small">{me.user.display_name}</div>
          <button type="button" className="link" onClick={signOut}>
            {t.logout}
          </button>
          {appVersion && (
            <a className="muted small version-link" href={versionHref} target="_blank" rel="noopener noreferrer">
              Heardloop v{appVersion.version}
            </a>
          )}
          <LegalLinks />
          <div className={`health health-${health}`} role="status">
            <span className="dot" aria-hidden="true" />
            {t.health[health]}
          </div>
        </div>
      </aside>
      <main className="content">
        {workspace?.role === 'owner' && !me.mfa_enabled && (
          <div className="banner" role="alert">
            {t.mfa.ownerBanner}{' '}
            <button
              type="button"
              className="link"
              onClick={() => {
                window.history.replaceState(null, '', '?section=settings&tab=security');
                go('settings');
              }}
            >
              {t.mfa.goToSettings}
            </button>
          </div>
        )}
        {showUpdateBanner && update && (
          <div className="banner" role="status">
            {t.updateBanner.message(update.latest)}
            {' — '}
            <button type="button" className="link" onClick={() => setNotesOpen(true)}>
              {t.updateBanner.whatsNew}
            </button>
            {' · '}
            <a href={installDoc} target="_blank" rel="noopener noreferrer">
              {t.updateBanner.howToUpdate}
            </a>
            {' · '}
            <button type="button" className="link" onClick={() => dismissUpdate(update)}>
              {t.updateBanner.dismiss}
            </button>
          </div>
        )}
        {notesOpen && update && (
          <NotesDialog title={t.about.whatsNewTitle(update.latest)} onClose={() => setNotesOpen(false)}>
            {notesAsNodes(update.notes || t.about.noNotes)}
          </NotesDialog>
        )}
        <h1>{notFound ? t.notFound.title : title}</h1>
        {!notFound && <p className="muted">{description}</p>}
        <Suspense fallback={<div className="loading">{t.loading}</div>}>
        {notFound ? (
          <section className="card">
            <p>{t.notFound.text}</p>
            <button type="button" className="primary" onClick={() => go('calendar')}>
              {t.notFound.toCalendar}
            </button>
          </section>
        ) : section.id === 'settings' ? (
          <Settings me={me} onMeChange={onMeChange} appVersion={appVersion} onAppVersion={setAppVersion} />
        ) : section.id === 'accounts' ? (
          <Accounts me={me} />
        ) : section.id === 'posts' ? (
          <Posts me={me} focusPostId={focusPostId} onOpenAudiences={openAudiences} />
        ) : section.id === 'calendar' ? (
          <Calendar me={me} onOpenPost={openPost} />
        ) : section.id === 'approvals' ? (
          <Approvals me={me} onOpenPost={openPost} />
        ) : section.id === 'notifications' ? (
          <Notifications me={me} onOpenPost={openPost} onOpenSection={go} onUnreadChange={setUnread} />
        ) : section.id === 'reports' ? (
          <Reports me={me} onOpenPost={openPost} />
        ) : section.id === 'campaigns' ? (
          <Campaigns me={me} onOpenPost={openPost} onOpenAudiences={openAudiences} />
        ) : section.id === 'templates' ? (
          <Templates me={me} />
        ) : (
          <div className="placeholder">{t.comingInPhase(section.phase)}</div>
        )}
        </Suspense>
      </main>
    </div>
  );
}
