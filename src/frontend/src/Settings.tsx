import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  addMember,
  anonymiseMember,
  ApiError,
  changePassword,
  fetchMe,
  getBackupStatus,
  getSettings,
  mfaDisable,
  mfaEnable,
  mfaRegenerateCodes,
  mfaSetup,
  listAudit,
  purgeAudit,
  listMembers,
  listSessions,
  removeMember,
  revokeOtherSessions,
  revokeSession,
  updateMember,
  updateSettings,
  type AuditEntry,
  type AuditFilter,
  type BackupStatus,
  type Me,
  type Member,
  type Role,
  type SessionInfo,
  type AppVersion,
  type WorkspaceSettings,
} from './api';
import QRCode from 'qrcode';
import { LOCALE, t } from './i18n';
import { publishErrorReasonLabel } from './publishErrors';
import { formatDateTime } from './dates';
import { DateInput } from './DateInputs';
import WebhookSettings from './WebhookSettings';
import AppearanceSettings from './AppearanceSettings';
import AboutSettings from './AboutSettings';

const ROLES: Role[] = ['owner', 'editor', 'approver', 'viewer'];

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

type Tab = 'appearance' | 'workspace' | 'members' | 'security' | 'webhook' | 'audit' | 'about';

export default function Settings({
  me,
  onMeChange,
  appVersion,
  onAppVersion,
}: {
  me: Me;
  onMeChange: (me: Me) => void;
  appVersion: AppVersion | null;
  onAppVersion: (v: AppVersion | null) => void;
}) {
  const membership = me.memberships[0];
  const isOwner = membership?.role === 'owner';
  const tabs: Tab[] = isOwner
    ? ['appearance', 'workspace', 'members', 'security', 'webhook', 'audit', 'about']
    : ['appearance', 'workspace', 'members', 'security', 'about'];
  const [tab, setTab] = useState<Tab>(() => {
    const wanted = new URLSearchParams(window.location.search).get('tab') as Tab | null;
    return wanted && tabs.includes(wanted) ? wanted : 'appearance';
  });
  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;
  const wsId = membership.workspace_id;
  return (
    <div className="settings">
      <nav className="subtabs" role="tablist" aria-label={t.settings.tabsLabel}>
        {tabs.map((id) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            <span className="tab-title">{t.settings.tabs[id]}</span>
            <span className="tab-desc">{t.settings.tabDescriptions[id]}</span>
          </button>
        ))}
      </nav>
      <div className="stack" role="tabpanel">
        {tab === 'appearance' && <AppearanceSettings me={me} onMeChange={onMeChange} />}
        {tab === 'workspace' && (
          <>
            <WorkspaceSection workspaceId={wsId} />
            {isOwner && <BackupCard workspaceId={wsId} />}
          </>
        )}
        {tab === 'members' && <MembersSection workspaceId={wsId} isOwner={isOwner} myId={me.user.id} />}
        {tab === 'security' && (
          <>
            <MfaSection enabled={me.mfa_enabled} onChanged={() => fetchMe().then((m) => m && onMeChange(m))} />
            <PasswordSection />
            <SessionsSection />
          </>
        )}
        {tab === 'webhook' && isOwner && <WebhookSettings workspaceId={wsId} />}
        {tab === 'audit' && isOwner && <AuditSection workspaceId={wsId} />}
        {tab === 'about' && <AboutSettings me={me} version={appVersion} onVersion={onAppVersion} />}
      </div>
    </div>
  );
}

function Status({ ok, error }: { ok?: string; error?: string }) {
  if (error) return <p className="error" role="alert">{error}</p>;
  if (ok) return <p className="ok" role="status">{ok}</p>;
  return null;
}

const GUIDE_BACKUPS_URL = 'https://github.com/HirezRa/heardloop/blob/main/docs/GUIDE.md#10-backups-and-restore';

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}

/** Owner only: the newest backup as the backup service recorded it. Nothing to configure here. */
function BackupCard({ workspaceId }: { workspaceId: string }) {
  const [data, setData] = useState<BackupStatus | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    getBackupStatus(workspaceId)
      .then(setData)
      .catch(() => setError(t.backup.loadFailed));
  }, [workspaceId]);

  let level: 'ok' | 'warn' | 'bad' | null = null;
  let label = '';
  if (data?.configured) {
    if (data.status !== 'ok') {
      level = 'bad';
      label = data.status === 'failed' ? t.backup.failed : t.backup.unreadable;
    } else if (data.stale) {
      level = 'warn';
      label = t.backup.stale;
    } else {
      level = 'ok';
      label = t.backup.ok;
    }
  }

  return (
    <section className="card backup-card">
      <div className="row-between">
        <h2>{t.backup.title}</h2>
        {level && <span className={`badge backup-${level}`}>{label}</span>}
      </div>
      {data && !data.configured && <p className="muted">{t.backup.none}</p>}
      {data?.configured && data.status === 'unreadable' && <p className="error" role="alert">{t.backup.unreadable}</p>}
      {data?.configured && data.status !== 'unreadable' && data.finished_at && (
        <>
          <p>{t.backup.lastBackup(formatDateTime(new Date(data.finished_at)))}</p>
          {data.status === 'ok' && (
            <p>
              {t.backup.size(formatBytes(data.db_bytes + data.media_bytes))}
              <span className={data.encrypted ? 'badge backup-ok' : 'badge'}>
                {data.encrypted ? t.backup.encrypted : t.backup.notEncrypted}
              </span>
            </p>
          )}
          {data.status === 'failed' && data.error && <p className="error">{t.backup.reason(data.error)}</p>}
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <p className="muted small">{t.backup.runNow}</p>
      <p className="small">
        <a href={GUIDE_BACKUPS_URL} target="_blank" rel="noreferrer noopener">{t.backup.guide}</a>
      </p>
    </section>
  );
}

function WorkspaceSection({ workspaceId }: { workspaceId: string }) {
  const [data, setData] = useState<WorkspaceSettings | null>(null);
  const [form, setForm] = useState({ name: '', time_zone: '', require_approval: true, weekly_digest: true });
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];

  useEffect(() => {
    getSettings(workspaceId)
      .then((s) => {
        setData(s);
        setForm({ name: s.name, time_zone: s.time_zone, require_approval: s.require_approval, weekly_digest: s.weekly_digest });
      })
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [workspaceId]);

  async function save(e: FormEvent) {
    e.preventDefault();
    setMsg({});
    try {
      const s = await updateSettings(workspaceId, form);
      setData(s);
      setMsg({ ok: t.settings.saved });
    } catch (err) {
      setMsg({ error: errorText(err) });
    }
  }

  if (!data) return <section className="card"><Status {...msg} /></section>;
  const disabled = !data.can_edit;
  return (
    <section className="card">
      <h2>{t.settings.workspace}</h2>
      <form className="form" onSubmit={save}>
        <label>
          {t.settings.name}
          <input value={form.name} maxLength={200} disabled={disabled} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>
          {t.settings.timeZone}
          {zones.length ? (
            <select dir="ltr" value={form.time_zone} disabled={disabled} onChange={(e) => setForm({ ...form, time_zone: e.target.value })}>
              {!zones.includes(form.time_zone) && <option value={form.time_zone}>{form.time_zone}</option>}
              {zones.map((z) => (
                <option key={z} value={z}>{z}</option>
              ))}
            </select>
          ) : (
            <input dir="ltr" value={form.time_zone} disabled={disabled} onChange={(e) => setForm({ ...form, time_zone: e.target.value })} />
          )}
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.require_approval}
            disabled={disabled}
            onChange={(e) => setForm({ ...form, require_approval: e.target.checked })}
          />
          {t.settings.requireApproval}
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={form.weekly_digest}
            disabled={disabled}
            onChange={(e) => setForm({ ...form, weekly_digest: e.target.checked })}
          />
          {t.settings.weeklyDigest}
        </label>
        {!disabled && <button type="submit" className="primary">{t.settings.save}</button>}
        <Status {...msg} />
      </form>
    </section>
  );
}

/** Confirmation + two-step code for "Deactivate and anonymise" (native dialog: focus trap and Escape for free). */
function AnonymiseDialog({
  member,
  onCancel,
  onConfirm,
}: {
  member: Member;
  onCancel: () => void;
  onConfirm: (code: string) => Promise<string | null>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.showModal();
    return () => opener?.focus?.();
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    const failure = await onConfirm(code.trim());
    setBusy(false);
    if (failure) setError(failure);
  }

  return (
    <dialog ref={ref} className="modal" aria-label={t.settings.anonymiseTitle(member.display_name)} onClose={onCancel}>
      <form className="form" onSubmit={submit}>
        <h3>{t.settings.anonymiseTitle(member.display_name)}</h3>
        <p>{t.settings.anonymiseWarning}</p>
        <label>
          {t.settings.anonymiseCode}
          <input
            dir="ltr"
            inputMode="text"
            autoComplete="one-time-code"
            required
            minLength={6}
            maxLength={32}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="row-between">
          <button type="button" className="link" onClick={onCancel}>{t.settings.cancel}</button>
          <button type="submit" className="primary danger" disabled={busy || code.trim().length < 6}>
            {t.settings.anonymiseConfirm}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function MembersSection({ workspaceId, isOwner, myId }: { workspaceId: string; isOwner: boolean; myId: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [anonymising, setAnonymising] = useState<Member | null>(null);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [form, setForm] = useState({ email: '', display_name: '', role: 'editor' as Role, initial_password: '' });

  const load = useCallback(() => {
    listMembers(workspaceId).then(setMembers).catch((e) => setMsg({ error: errorText(e) }));
  }, [workspaceId]);
  useEffect(load, [load]);

  async function run(action: () => Promise<unknown>, ok: string) {
    setMsg({});
    try {
      await action();
      setMsg({ ok });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(
      () => addMember(workspaceId, { ...form, initial_password: form.initial_password || undefined }),
      t.settings.memberAdded,
    );
    setForm({ email: '', display_name: '', role: 'editor', initial_password: '' });
  }

  return (
    <section className="card">
      <h2>{t.settings.members}</h2>
      <table className="table">
        <thead>
          <tr>
            <th>{t.settings.name}</th>
            <th>{t.login.email}</th>
            <th>{t.settings.role}</th>
            {isOwner && <th aria-label={t.settings.actions} />}
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name}</td>
              <td dir="ltr">{m.email}</td>
              <td>
                {isOwner ? (
                  <select
                    value={m.role}
                    aria-label={t.settings.role}
                    onChange={(e) => run(() => updateMember(workspaceId, m.user_id, e.target.value as Role), t.settings.saved)}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{t.roles[r]}</option>
                    ))}
                  </select>
                ) : (
                  t.roles[m.role]
                )}
              </td>
              {isOwner && (
                <td className="member-actions">
                  {m.user_id !== myId && (
                    <>
                      <button
                        type="button"
                        className="link"
                        aria-label={t.settings.removeLabel(m.display_name)}
                        onClick={() => {
                          if (window.confirm(t.settings.confirmRemove(m.display_name))) {
                            void run(() => removeMember(workspaceId, m.user_id), t.settings.memberRemoved);
                          }
                        }}
                      >
                        {t.settings.remove}
                      </button>
                      <span className="muted" aria-hidden="true">
                        {' · '}
                      </span>
                      <button
                        type="button"
                        className="link danger"
                        aria-label={t.settings.anonymiseLabel(m.display_name)}
                        onClick={() => setAnonymising(m)}
                      >
                        {t.settings.anonymise}
                      </button>
                    </>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {isOwner && (
        <form className="form inline" onSubmit={add}>
          <h3>{t.settings.addMember}</h3>
          <input placeholder={t.login.email} dir="ltr" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input placeholder={t.settings.name} required maxLength={200} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <select value={form.role} aria-label={t.settings.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>{t.roles[r]}</option>
            ))}
          </select>
          <input
            placeholder={t.settings.initialPassword}
            type="password"
            autoComplete="new-password"
            dir="ltr"
            value={form.initial_password}
            onChange={(e) => setForm({ ...form, initial_password: e.target.value })}
          />
          <button type="submit" className="primary">{t.settings.add}</button>
          <p className="muted small">{t.settings.initialPasswordHint}</p>
        </form>
      )}
      {anonymising && (
        <AnonymiseDialog
          member={anonymising}
          onCancel={() => setAnonymising(null)}
          onConfirm={async (code) => {
            try {
              await anonymiseMember(workspaceId, anonymising.user_id, code);
            } catch (err) {
              return errorText(err);
            }
            setAnonymising(null);
            setMsg({ ok: t.settings.memberAnonymised });
            load();
            return null;
          }}
        />
      )}
      <Status {...msg} />
    </section>
  );
}

/** "Firefox on Windows" from a user agent; good enough to recognise your own devices. */
function deviceLabel(ua: string | null): string {
  if (!ua) return t.settings.sessionUnknownDevice;
  const browser =
    /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Android/.test(ua)
      ? 'Android'
      : /iPhone|iPad/.test(ua)
        ? 'iOS'
        : /Mac OS X/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : '';
  if (!browser && !os) return ua.slice(0, 60);
  return [browser, os].filter(Boolean).join(' · ');
}

function SessionsSection() {
  const [items, setItems] = useState<SessionInfo[] | null>(null);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listSessions()
      .then(setItems)
      .catch((e) => setMsg({ error: errorText(e) }));
  }, []);
  useEffect(load, [load]);

  async function signOut(id: string) {
    setBusy(true);
    try {
      await revokeSession(id);
      setMsg({ ok: t.settings.sessionsSignedOut(1) });
    } catch (e) {
      setMsg({ error: errorText(e) });
    } finally {
      setBusy(false);
      load();
    }
  }

  async function signOutOthers() {
    setBusy(true);
    try {
      const { revoked } = await revokeOtherSessions();
      setMsg({ ok: revoked ? t.settings.sessionsSignedOut(revoked) : t.settings.sessionsNone });
    } catch (e) {
      setMsg({ error: errorText(e) });
    } finally {
      setBusy(false);
      load();
    }
  }

  const others = (items ?? []).filter((s) => !s.current).length;
  return (
    <section className="card">
      <h2>{t.settings.sessionsTitle}</h2>
      <p className="muted small">{t.settings.sessionsHint}</p>
      {items && (
        <ul className="sessions">
          {items.map((s) => (
            <li key={s.id}>
              <div>
                <strong>{deviceLabel(s.user_agent)}</strong>
                {s.current && <span className="badge">{t.settings.sessionThis}</span>}
                <div className="muted small">
                  {t.settings.sessionLastSeen}: {formatDateTime(new Date(s.last_seen_at))} · {t.settings.sessionStarted}:{' '}
                  {formatDateTime(new Date(s.created_at))}
                  {s.ip_address && (
                    <>
                      {' · '}
                      <bdi dir="ltr">{s.ip_address}</bdi>
                    </>
                  )}
                </div>
              </div>
              {!s.current && (
                <button type="button" disabled={busy} onClick={() => void signOut(s.id)}>
                  {t.settings.sessionSignOut}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {items && others === 0 && <p className="muted small">{t.settings.sessionsNone}</p>}
      {others > 0 && (
        <button type="button" className="danger-btn" disabled={busy} onClick={() => void signOutOthers()}>
          {t.settings.sessionsSignOutOthers}
        </button>
      )}
      <Status {...msg} />
    </section>
  );
}

function PasswordSection() {
  const [form, setForm] = useState({ current: '', next: '', repeat: '' });
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (form.next !== form.repeat) {
      setMsg({ error: t.settings.passwordMismatch });
      return;
    }
    setMsg({});
    try {
      await changePassword(form.current, form.next);
      setMsg({ ok: t.settings.passwordChanged });
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setForm({ current: '', next: '', repeat: '' });
    }
  }

  return (
    <section className="card">
      <h2>{t.settings.myPassword}</h2>
      <form className="form" onSubmit={submit}>
        <input type="password" dir="ltr" autoComplete="current-password" required placeholder={t.settings.currentPassword} value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} />
        <input type="password" dir="ltr" autoComplete="new-password" required minLength={12} placeholder={t.settings.newPassword} value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} />
        <input type="password" dir="ltr" autoComplete="new-password" required placeholder={t.settings.repeatPassword} value={form.repeat} onChange={(e) => setForm({ ...form, repeat: e.target.value })} />
        <button type="submit" className="primary">{t.settings.changePassword}</button>
        <Status {...msg} />
      </form>
    </section>
  );
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/** Human-readable audit details: "field: from → to", dates in local time, no raw JSON. */
function formatChanges(changes: Record<string, unknown> | null, fmt: { format: (d: Date) => string }): string {
  if (!changes) return '';
  const show = (key: string, v: unknown): string => {
    if (key === 'reason' && typeof v === 'string') return publishErrorReasonLabel(v);
    if (typeof v === 'string' && ISO_DATE.test(v)) {
      const d = new Date(v);
      return Number.isNaN(d.getTime()) ? v : fmt.format(d);
    }
    if (typeof v === 'boolean') return v ? '✓' : '✗';
    if (v === null || v === undefined) return '-';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };
  return Object.entries(changes)
    .map(([key, value]) => {
      const label = (t.auditFields as Record<string, string>)[key] ?? key.replace(/_/g, ' ');
      if (value && typeof value === 'object' && 'from' in value && 'to' in value) {
        const ft = value as { from: unknown; to: unknown };
        return `${label}: ${show(key, ft.from)} ${LOCALE === 'he' ? '←' : '→'} ${show(key, ft.to)}`;
      }
      return `${label}: ${show(key, value)}`;
    })
    .join(' · ');
}

const AUDIT_CATEGORIES = ['post', 'campaign', 'auth', 'member', 'workspace', 'linkedin', 'media', 'audit'] as const;

/** Local "yyyy-mm-dd" day -> ISO instant at the start (or end) of that day in the browser's zone. */
function dayBound(day: string | null, end: boolean): string | undefined {
  if (!day) return undefined;
  const [y, m, d] = day.split('-').map(Number);
  const date = end ? new Date(y, m - 1, d, 23, 59, 59, 999) : new Date(y, m - 1, d, 0, 0, 0, 0);
  return date.toISOString();
}

function AuditSection({ workspaceId }: { workspaceId: string }) {
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [members, setMembers] = useState<Member[]>([]);
  const [category, setCategory] = useState('');
  const [actor, setActor] = useState('');
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [q]);

  const filter = useMemo<AuditFilter>(
    () => ({ category, actor_id: actor, since: dayBound(from, false), until: dayBound(to, true), q: debouncedQ }),
    [category, actor, from, to, debouncedQ],
  );
  const hasFilter = !!(category || actor || from || to || debouncedQ);

  const load = useCallback(
    (before?: number) => {
      listAudit(workspaceId, before, filter)
        .then((page) => {
          setItems((prev) => (before ? [...prev, ...page.items] : page.items));
          setNext(page.next_before_id);
        })
        .catch((e) => setError(errorText(e)));
    },
    [workspaceId, filter],
  );
  useEffect(() => load(), [load]);
  useEffect(() => {
    listMembers(workspaceId).then(setMembers).catch(() => setMembers([]));
  }, [workspaceId]);

  function clearFilters() {
    setCategory('');
    setActor('');
    setFrom(null);
    setTo(null);
    setQ('');
    setDebouncedQ('');
  }

  const [purging, setPurging] = useState(false);
  const [purgeMode, setPurgeMode] = useState<'all' | 'before'>('before');
  const [purgeBefore, setPurgeBefore] = useState<string | null>(null);
  const [purgePassword, setPurgePassword] = useState('');
  const [purgeMsg, setPurgeMsg] = useState<{ ok?: string; error?: string }>({});

  async function purge() {
    if (purgeMode === 'before' && !purgeBefore) return setPurgeMsg({ error: t.settings.purgePickDate });
    if (!window.confirm(purgeMode === 'all' ? t.settings.purgeConfirmAll : t.settings.purgeConfirmBefore)) return;
    try {
      const res = await purgeAudit(workspaceId, purgePassword, purgeMode === 'before' ? dayBound(purgeBefore, false) : undefined);
      setPurgeMsg({ ok: t.settings.purged(res.deleted) });
      setPurgePassword('');
      setPurging(false);
      load();
    } catch (e) {
      setPurgeMsg({ error: errorText(e) });
    }
  }

  const fmt = { format: (d: Date) => formatDateTime(d) };
  return (
    <section className="card audit-log">
      <div className="audit-heading">
        <h2>{t.settings.audit}</h2>
        <button type="button" className="link danger" onClick={() => setPurging((p) => !p)}>
          {t.settings.purgeOpen}
        </button>
      </div>
      {purging && (
        <div className="purge-box" role="region" aria-label={t.settings.purgeOpen}>
          <p className="small">{t.settings.purgeHint}</p>
          <div className="choices" role="radiogroup">
            <button type="button" role="radio" aria-checked={purgeMode === 'before'} className={purgeMode === 'before' ? 'choice active' : 'choice'} onClick={() => setPurgeMode('before')}>
              {t.settings.purgeBefore}
            </button>
            <button type="button" role="radio" aria-checked={purgeMode === 'all'} className={purgeMode === 'all' ? 'choice active' : 'choice'} onClick={() => setPurgeMode('all')}>
              {t.settings.purgeAll}
            </button>
          </div>
          <div className="filters">
            {purgeMode === 'before' && <DateInput value={purgeBefore} onChange={setPurgeBefore} label={t.settings.purgeBefore} />}
            <input
              type="password"
              autoComplete="current-password"
              placeholder={t.settings.currentPassword}
              value={purgePassword}
              onChange={(e) => setPurgePassword(e.target.value)}
              aria-label={t.settings.currentPassword}
            />
            <button type="button" className="danger-btn" disabled={!purgePassword} onClick={() => void purge()}>
              {t.settings.purgeDo}
            </button>
            <button type="button" className="link" onClick={() => setPurging(false)}>
              {t.posts.close}
            </button>
          </div>
          <Status {...purgeMsg} />
        </div>
      )}
      {!purging && purgeMsg.ok && <Status ok={purgeMsg.ok} />}
      <div className="audit-filters" role="search">
        <input
          type="search"
          dir="auto"
          placeholder={t.settings.filterText}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t.settings.filterText}
        />
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label={t.settings.filterCategory}>
          <option value="">{t.settings.allCategories}</option>
          {AUDIT_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {t.settings.categories[c]}
            </option>
          ))}
        </select>
        <select value={actor} onChange={(e) => setActor(e.target.value)} aria-label={t.settings.filterPerson}>
          <option value="">{t.settings.everyone}</option>
          {members.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.display_name}
            </option>
          ))}
        </select>
        <DateInput value={from} onChange={setFrom} label={t.settings.filterFrom} />
        <DateInput value={to} onChange={setTo} min={from ?? undefined} label={t.settings.filterTo} />
        <button type="button" className="link" disabled={!hasFilter} onClick={clearFilters}>
          {t.settings.clearFilters}
        </button>
      </div>
      {items.length === 0 && <p className="muted">{hasFilter ? t.settings.noMatches : t.settings.noEvents}</p>}
      <table className="table">
        <thead>
          <tr>
            <th>{t.settings.when}</th>
            <th>{t.settings.who}</th>
            <th>{t.settings.what}</th>
            <th>{t.settings.details}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id}>
              <td dir="ltr">{fmt.format(new Date(e.at))}</td>
              <td>{e.actor_name ?? '-'}</td>
              <td>{(t.auditActions as Record<string, string>)[e.action] ?? e.action}</td>
              <td className="small">{formatChanges(e.changes, fmt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {next && (
        <button type="button" className="link" onClick={() => load(next)}>
          {t.settings.loadMore}
        </button>
      )}
      <Status error={error} />
    </section>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="recovery">
      <p><strong>{t.mfa.codesTitle}</strong> {t.mfa.codesHint}</p>
      <ul dir="ltr" className="mono">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <button type="button" className="link" onClick={() => void navigator.clipboard?.writeText(codes.join('\n'))}>
        {t.mfa.copy}
      </button>{' '}
      <button type="button" className="primary" onClick={onDone}>
        {t.mfa.saved}
      </button>
    </div>
  );
}

function MfaSection({ enabled, onChanged }: { enabled: boolean; onChanged: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; qr: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});

  async function start(e: FormEvent) {
    e.preventDefault();
    setMsg({});
    setCodes(null);
    try {
      const s = await mfaSetup(password);
      const qr = await QRCode.toDataURL(s.otpauth_uri, { margin: 1, width: 200 });
      setSetup({ secret: s.secret, qr });
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setPassword('');
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    setMsg({});
    try {
      const res = await mfaEnable(code);
      setCodes(res.recovery_codes);
      setSetup(null);
      setMsg({ ok: t.mfa.enabledOk });
      onChanged();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setCode('');
    }
  }

  async function regenerate(e: FormEvent) {
    e.preventDefault();
    setMsg({});
    try {
      setCodes((await mfaRegenerateCodes(password, code)).recovery_codes);
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setCode('');
      setPassword('');
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    if (!window.confirm(t.mfa.confirmDisable)) return;
    setMsg({});
    try {
      await mfaDisable(password, code);
      setCodes(null);
      setMsg({ ok: t.mfa.disabledOk });
      onChanged();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setCode('');
      setPassword('');
    }
  }

  return (
    <section className="card">
      <h2>{t.mfa.title}</h2>
      <p className="muted small">{enabled ? t.mfa.statusOn : t.mfa.statusOff}</p>
      {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}

      {!enabled && !setup && (
        <form className="form" onSubmit={start}>
          <input type="password" dir="ltr" autoComplete="current-password" required placeholder={t.settings.currentPassword} value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="submit" className="primary">{t.mfa.enable}</button>
        </form>
      )}

      {!enabled && setup && (
        <form className="form" onSubmit={confirm}>
          <p className="small">{t.mfa.scan}</p>
          <img src={setup.qr} alt={t.mfa.qrAlt} width={200} height={200} className="qr" />
          <p className="small">
            {t.mfa.manual} <code dir="ltr" className="mono">{setup.secret}</code>
          </p>
          <input dir="ltr" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required placeholder={t.mfa.codePlaceholder} value={code} onChange={(e) => setCode(e.target.value)} />
          <button type="submit" className="primary">{t.mfa.confirm}</button>
        </form>
      )}

      {enabled && (
        <div className="stack tight">
          <form className="form" onSubmit={regenerate}>
            <h3>{t.mfa.newCodes}</h3>
            <input type="password" dir="ltr" autoComplete="current-password" required placeholder={t.settings.currentPassword} value={password} onChange={(e) => setPassword(e.target.value)} />
            <input dir="ltr" inputMode="numeric" maxLength={6} required placeholder={t.mfa.codePlaceholder} value={code} onChange={(e) => setCode(e.target.value)} />
            <button type="submit" className="primary">{t.mfa.newCodesButton}</button>
          </form>
          <form className="form" onSubmit={disable}>
            <h3>{t.mfa.disable}</h3>
            <input type="password" dir="ltr" autoComplete="current-password" required placeholder={t.settings.currentPassword} value={password} onChange={(e) => setPassword(e.target.value)} />
            <input dir="ltr" maxLength={32} required placeholder={t.mfa.codeOrRecovery} value={code} onChange={(e) => setCode(e.target.value)} />
            <button type="submit" className="link danger">{t.mfa.disable}</button>
          </form>
        </div>
      )}
      <Status {...msg} />
    </section>
  );
}
