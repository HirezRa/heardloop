import { lazy, Suspense, useEffect, useRef, useState, type MouseEvent } from 'react';
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
import Icon from './Icon';
import { acceptCurrentUrl, confirmDiscard, hasUnsaved, lastAppUrl, pushUrl, sectionUrl, type NavParams } from './nav';

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

/** The screen the address bar points at. Links from e-mails and webhooks: /?section=posts&post=<id> */
function readLocation(): { active: SectionId; notFound: boolean; post: string | null } {
  const params = new URLSearchParams(window.location.search);
  const wanted = params.get('section');
  const known = SECTIONS.some((s) => s.id === wanted);
  return {
    active: known ? (wanted as SectionId) : 'calendar',
    // An unknown path or ?section= value (old or mistyped link) gets a "not found" screen, not a silent redirect.
    notFound: window.location.pathname !== '/' || (wanted !== null && !known),
    post: params.get('post'),
  };
}

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
  const [active, setActive] = useState<SectionId>(() => readLocation().active);
  const [notFound, setNotFound] = useState(() => readLocation().notFound);
  const [health, setHealth] = useState<HealthState>('checking');
  const [focusPostId, setFocusPostId] = useState<string | null>(() => readLocation().post);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const shownScreen = useRef<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false); // narrow screens: the folded menu
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
    go('posts', { post: postId });
  }

  /** Every screen change goes through here: unsaved-edit guard, address bar, then state. */
  function go(id: SectionId, params: NavParams = {}) {
    const sameScreen = id === active && !notFound;
    // The menu entry of the screen already shown: nothing to do (and nothing to lose).
    if (sameScreen && !params.tab && !params.post) {
      setMenuOpen(false);
      return;
    }
    if (!sameScreen && !confirmDiscard()) return;
    pushUrl(sectionUrl(id, params));
    setMenuOpen(false);
    setFocusPostId(params.post ?? null);
    setNotFound(false);
    setActive(id);
  }

  /** Settings -> Workspace, where audiences are managed (the editor and the campaign form link here). */
  function openAudiences() {
    go('settings', { tab: 'workspace' });
  }

  // Back / Forward: show the screen the address bar now points at.
  useEffect(() => {
    function onPopState() {
      if (hasUnsaved() && !confirmDiscard()) {
        // Stay: put the address of the screen that is still shown back on top.
        window.history.pushState(null, '', lastAppUrl());
        return;
      }
      acceptCurrentUrl();
      setMenuOpen(false);
      const loc = readLocation();
      setFocusPostId(loc.post);
      setNotFound(loc.notFound);
      setActive(loc.active);
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // A new screen: move keyboard and screen-reader focus to its heading and name the browser tab after it.
  // The first render is skipped (focus stays where the browser puts it on load); StrictMode re-runs are no-ops.
  const screenKey = notFound ? 'not-found' : active;
  useEffect(() => {
    const [name] = notFound ? [t.notFound.title] : t.sections[active];
    document.title = `${name} · Heardloop`;
    if (shownScreen.current === null) {
      shownScreen.current = screenKey;
      return;
    }
    if (shownScreen.current === screenKey) return;
    shownScreen.current = screenKey;
    headingRef.current?.focus();
  }, [screenKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // A reload or closing the tab with unsaved edits asks the browser's own "leave site?" question.
  useEffect(() => {
    function onBeforeUnload(e: BeforeUnloadEvent) {
      if (!hasUnsaved()) return;
      e.preventDefault();
      e.returnValue = '';
    }
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);

  function skipToContent(e: MouseEvent<HTMLAnchorElement>) {
    e.preventDefault(); // no #hash in the address bar (it would add a history entry)
    mainRef.current?.focus();
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
    if (!confirmDiscard()) return;
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
      <a className="skip-link" href="#main" onClick={skipToContent}>
        {t.ux.skipToContent}
      </a>
      <aside className="sidebar">
        <div className="sidebar-top">
          <div>
            <div className="brand">Heardloop</div>
            {/* The workspace name only adds information when it is not the product name again. */}
            {workspace && workspace.workspace_name.trim().toLowerCase() !== 'heardloop' && (
              <div className="muted small">{workspace.workspace_name}</div>
            )}
          </div>
          {/* Narrow screens only (CSS): the menu folds away so the screen's content comes first. */}
          <button
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="app-menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Icon name={menuOpen ? 'close' : 'menu'} />
            {menuOpen ? t.ux.menuClose : t.ux.menuOpen}
            {!menuOpen && unread > 0 && (
              <span className="count" aria-hidden="true">
                {unread > 99 ? '99+' : unread}
              </span>
            )}
          </button>
        </div>
        <div id="app-menu" className={menuOpen ? 'sidebar-panel open' : 'sidebar-panel'}>
        <nav aria-label={t.nav}>
          {SECTIONS.filter((s) => !HIDDEN_IN_MENU.includes(s.id)).map((s) => (
            <button
              key={s.id}
              type="button"
              className={s.id === active && !notFound ? 'nav-item active' : 'nav-item'}
              aria-current={s.id === active && !notFound ? 'page' : undefined}
              onClick={() => go(s.id)}
            >
              <Icon name={s.id === 'notifications' ? 'bell' : s.id} />
              <span>{t.sections[s.id][0]}</span>
            </button>
          ))}
        </nav>
        <button
          type="button"
          className={active === 'notifications' ? 'nav-item bell active' : 'nav-item bell'}
          aria-current={active === 'notifications' ? 'page' : undefined}
          onClick={() => go('notifications')}
        >
          <Icon name="bell" />
          <span>{t.notifications.title}</span>
          {unread > 0 && (
            <>
              <span className="count" aria-hidden="true">
                {unread > 99 ? '99+' : unread}
              </span>
              <span className="visually-hidden">{t.notifications.unread(unread)}</span>
            </>
          )}
        </button>
        <div className="sidebar-footer">
          <div className="user-row">
            <span className="avatar" aria-hidden="true">
              {me.user.display_name.trim().charAt(0).toUpperCase() || '?'}
            </span>
            <span className="user-name" title={me.user.display_name}>
              {me.user.display_name}
            </span>
          </div>
          <button type="button" className="link signout" onClick={signOut}>
            <Icon name="logout" size={16} />
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
        </div>
      </aside>
      <main className="content" id="main" ref={mainRef} tabIndex={-1}>
        {workspace?.role === 'owner' && !me.mfa_enabled && (
          <div className="banner" role="alert">
            {t.mfa.ownerBanner}{' '}
            <button
              type="button"
              className="link"
              onClick={() => go('settings', { tab: 'security' })}
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
        <h1 ref={headingRef} tabIndex={-1} className="screen-title">
          {notFound ? t.notFound.title : title}
        </h1>
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
