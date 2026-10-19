import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  disconnectAccount,
  listAccounts,
  retryFailedAccountPosts,
  startLinkedInConnect,
  type LinkedInAccount,
  type Me,
} from './api';
import { t } from './i18n';
import { formatDate } from './dates';
import { publishErrorReasonLabel } from './publishErrors';

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
  }
  return t.errors.generic;
}

/** Result of the OAuth round trip, passed back by the server as ?linkedin=...&reason=... */
function readReturnMessage(): { ok?: string; error?: string; offerRetry?: boolean } {
  const params = new URLSearchParams(window.location.search);
  const result = params.get('linkedin');
  if (!result) return {};
  window.history.replaceState(null, '', window.location.pathname); // do not keep it in the URL
  if (result === 'connected') return { ok: t.accounts.connected, offerRetry: true };
  if (result === 'cancelled') return { error: t.accounts.cancelled };
  const reason = params.get('reason') ?? '';
  return { error: (t.accounts.reasons as Record<string, string>)[reason] ?? t.accounts.failed };
}

export default function Accounts({ me }: { me: Me }) {
  const membership = me.memberships[0];
  const [accounts, setAccounts] = useState<LinkedInAccount[]>([]);
  const [msg, setMsg] = useState<{ ok?: string; error?: string; offerRetry?: boolean }>(() => readReturnMessage());
  const [busy, setBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState<string | null>(null);
  const isOwner = membership?.role === 'owner';
  const canRetry = membership?.role === 'owner' || membership?.role === 'approver';

  const load = useCallback(() => {
    if (!membership) return;
    listAccounts(membership.workspace_id).then(setAccounts).catch((e) => setMsg({ error: errorText(e) }));
  }, [membership]);
  useEffect(load, [load]);

  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;

  async function connect() {
    setBusy(true);
    setMsg({});
    try {
      const { authorization_url } = await startLinkedInConnect(membership.workspace_id);
      if (!authorization_url.startsWith('https://www.linkedin.com/')) throw new Error('unexpected redirect');
      window.location.assign(authorization_url);
    } catch (err) {
      setMsg({ error: errorText(err) });
      setBusy(false);
    }
  }

  async function disconnect(acc: LinkedInAccount) {
    if (!window.confirm(t.accounts.confirmDisconnect(acc.display_name))) return;
    setMsg({});
    try {
      await disconnectAccount(membership.workspace_id, acc.id);
      setMsg({ ok: t.accounts.disconnected });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    }
  }

  async function retryFailed(acc: LinkedInAccount) {
    setRetryBusy(acc.id);
    setMsg({});
    try {
      const res = await retryFailedAccountPosts(membership.workspace_id, acc.id);
      setMsg({
        ok: t.accounts.retried(res.count, res.skipped_unapproved),
        offerRetry: false,
      });
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setRetryBusy(null);
    }
  }

  const fmt = { format: (d: Date) => formatDate(d) };
  const needsReconnect = accounts.filter((a) => a.needs_reconnect_reason);
  return (
    <div className="stack">
      <section className="card">
        <h2>{t.accounts.title}</h2>
        {needsReconnect.map((a) => (
          <p key={`reconnect-${a.id}`} className="banner" role="status">
            {t.accounts.needsReconnect(a.display_name, publishErrorReasonLabel(a.needs_reconnect_reason))}
            {isOwner && (
              <>
                {' '}
                <button type="button" className="link" onClick={connect} disabled={busy}>
                  {t.accounts.connect}
                </button>
              </>
            )}
          </p>
        ))}
        {msg.offerRetry && canRetry && accounts.length > 0 && (
          <p className="banner" role="status">
            {t.accounts.retryFailedOffer}
            {accounts.map((a) => (
              <button
                key={`retry-${a.id}`}
                type="button"
                className="link"
                disabled={retryBusy === a.id}
                onClick={() => void retryFailed(a)}
              >
                {t.accounts.retryFailed(a.display_name)}
              </button>
            ))}
          </p>
        )}
        {accounts.length === 0 ? (
          <p className="muted">{t.accounts.none}</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t.settings.name}</th>
                <th>{t.accounts.status}</th>
                <th>{t.accounts.expires}</th>
                {isOwner && <th aria-label={t.settings.actions} />}
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td>{a.display_name}</td>
                  <td>
                    {t.accounts.statuses[a.status]}
                    {a.expiring_soon && <span className="badge warn">{t.accounts.expiringSoon}</span>}
                    {a.needs_reconnect_reason && (
                      <span className="badge warn">{t.accounts.needsReconnectBadge}</span>
                    )}
                  </td>
                  <td dir="ltr">{a.status === 'revoked' ? '-' : fmt.format(new Date(a.expires_at))}</td>
                  {isOwner && (
                    <td>
                      {a.status !== 'active' || a.expiring_soon || a.needs_reconnect_reason ? (
                        <button type="button" className="link" onClick={connect} disabled={busy}>
                          {t.accounts.reconnect}
                        </button>
                      ) : null}{' '}
                      {a.status !== 'revoked' && (
                        <button type="button" className="link danger" onClick={() => disconnect(a)}>
                          {t.accounts.disconnect}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {isOwner ? (
          <p>
            <button type="button" className="primary" onClick={connect} disabled={busy}>
              {busy ? t.accounts.redirecting : t.accounts.connect}
            </button>
          </p>
        ) : (
          <p className="muted small">{t.accounts.ownerOnly}</p>
        )}
        <p className="muted small">
          {t.accounts.termsNotice}{' '}
          <a
            href="https://github.com/HirezRa/heardloop#linkedins-terms"
            target="_blank"
            rel="noopener noreferrer"
          >
            {t.accounts.termsMore}
          </a>
        </p>
        <p className="muted small">{t.accounts.hint}</p>
        {msg.error && <p className="error" role="alert">{msg.error}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </section>
    </div>
  );
}
