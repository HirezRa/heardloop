import { useCallback, useEffect, useState } from 'react';
import {
  getNotificationPrefs,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  setNotificationPrefs,
  type AppNotification,
  type Me,
} from './api';
import { t } from './i18n';
import { formatDateTime } from './dates';
import { publishErrorReasonLabel } from './publishErrors';

function describe(n: AppNotification): string {
  const d = n.data as Record<string, string | null | undefined>;
  const who = d.actor_name ?? '';
  switch (n.kind) {
    case 'post.submitted':
      return t.notifications.kinds.submitted(who);
    case 'post.approved':
      return t.notifications.kinds.approved(who);
    case 'post.rejected':
      return t.notifications.kinds.rejected(who, d.comment ?? '');
    case 'post.published':
      return t.notifications.kinds.published;
    case 'post.failed':
      return t.notifications.kinds.failed(publishErrorReasonLabel(d.error ?? ''));
    case 'account.expiring':
      return t.notifications.kinds.expiring(d.account_name ?? '', (d.expires_at ?? '').slice(0, 10));
    case 'account.reconnect_needed':
      return t.notifications.kinds.reconnectNeeded(publishErrorReasonLabel(d.reason ?? ''));
    case 'digest.weekly':
      return t.notifications.kinds.digest(Number(d.published_last_week ?? 0), Number(d.scheduled_this_week ?? 0), Number(d.unmeasured ?? 0));
    case 'backup.stale':
      return d.reason === 'missing'
        ? t.notifications.kinds.backupMissing
        : t.notifications.kinds.backupStale(Math.round(Number(d.age_hours ?? 0)));
    case 'backup.failed':
      return t.notifications.kinds.backupFailed(d.error ?? '');
    case 'app.update_available':
      return t.notifications.kinds.updateAvailable(d.latest ?? '');
    case 'security.password_changed':
      return t.notifications.kinds.securityPasswordChanged;
    case 'security.mfa_enabled':
      return t.notifications.kinds.securityMfaEnabled;
    case 'security.mfa_disabled':
      return t.notifications.kinds.securityMfaDisabled;
    case 'security.recovery_codes_regenerated':
      return t.notifications.kinds.securityCodesRegenerated;
    case 'security.sessions_revoked':
      return t.notifications.kinds.securitySessionsRevoked(Number(d.count ?? 1));
    default:
      return n.kind;
  }
}

export default function Notifications({
  me,
  onOpenPost,
  onOpenSection,
  onUnreadChange,
}: {
  me: Me;
  onOpenPost: (postId: string) => void;
  onOpenSection: (section: 'accounts' | 'reports' | 'settings') => void;
  onUnreadChange: (n: number) => void;
}) {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [prefs, setPrefs] = useState<{ email_notifications: boolean; email_available: boolean } | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    listNotifications()
      .then((page) => {
        setItems(page.items);
        onUnreadChange(page.unread);
      })
      .catch(() => setError(t.errors.generic));
  }, [onUnreadChange]);

  useEffect(() => {
    load();
    getNotificationPrefs().then(setPrefs).catch(() => setPrefs(null));
  }, [load]);

  const fmt = { format: (d: Date) => formatDateTime(d) };

  async function open(n: AppNotification) {
    if (!n.read_at) {
      await markNotificationRead(n.id).catch(() => undefined);
      load();
    }
    if (n.kind === 'account.expiring' || n.kind === 'account.reconnect_needed') onOpenSection('accounts');
    else if (n.kind === 'digest.weekly') onOpenSection('reports');
    else if (n.kind === 'backup.stale' || n.kind === 'backup.failed') {
      window.history.replaceState(null, '', '?section=settings&tab=workspace');
      onOpenSection('settings');
    } else if (n.kind.startsWith('security.')) {
      window.history.replaceState(null, '', '?section=settings&tab=security');
      onOpenSection('settings');
    } else if (n.kind === 'app.update_available') {
      window.history.replaceState(null, '', '?section=settings&tab=about');
      onOpenSection('settings');
    } else if (n.post_id) onOpenPost(n.post_id);
  }

  async function toggleEmail(value: boolean) {
    try {
      setPrefs(await setNotificationPrefs(value));
    } catch {
      setError(t.errors.generic);
    }
  }

  const unread = items.filter((n) => !n.read_at).length;

  return (
    <section className="card">
      <div className="row-between">
        <h2>{t.notifications.title}</h2>
        {unread > 0 && (
          <button
            type="button"
            className="link"
            onClick={() => markAllNotificationsRead().then(load).catch(() => setError(t.errors.generic))}
          >
            {t.notifications.markAll}
          </button>
        )}
      </div>
      {prefs && (
        <label className="check small">
          <input
            type="checkbox"
            checked={prefs.email_notifications}
            disabled={!prefs.email_available}
            onChange={(e) => void toggleEmail(e.target.checked)}
          />
          {t.notifications.emailMe(me.user.email)}
          {!prefs.email_available && <span className="muted"> · {t.notifications.emailOff}</span>}
        </label>
      )}
      {items.length === 0 && <p className="muted">{t.notifications.none}</p>}
      <ul className="queue notif-list">
        {items.map((n) => (
          <li key={n.id}>
            <button type="button" className={n.read_at ? 'notif' : 'notif unread'} onClick={() => void open(n)}>
              <span className="notif-dot" aria-hidden="true" />
              <span className="notif-text" dir="auto">
                {describe(n)}
              </span>
              <span className="muted small">{fmt.format(new Date(n.created_at))}</span>
            </button>
          </li>
        ))}
      </ul>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
