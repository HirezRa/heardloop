import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  deleteWebhook,
  getWebhook,
  listWebhookDeliveries,
  rotateWebhookSecret,
  saveWebhook,
  testWebhook,
  type WebhookConfig,
  type WebhookDelivery,
} from './api';
import { t } from './i18n';
import { formatDateTime } from './dates';

/**
 * Plain http:// to a public address sends the signed payload (and, when turned on, post text and names)
 * unencrypted across the internet. Private addresses and single-label or .local/.lan/.internal names
 * (n8n next to Heardloop) are fine; the server still applies its own address rules.
 */
export function plainHttpToPublicHost(raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return false;
  }
  if (u.protocol !== 'http:') return false;
  const host = u.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (host.includes(':')) return !/^(fc|fd|fe80|::1$)/.test(host); // IPv6 unique-local / link-local / loopback
  if (!host.includes('.') || /\.(local|lan|internal|home\.arpa)$/.test(host)) return false;
  const v4 = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    const priv = a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a === 127;
    return !priv;
  }
  return true;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.webhook.invalidUrl;
  }
  return t.errors.generic;
}

/** Owner-only: outgoing webhook (e.g. n8n) with an HMAC signing secret shown once. */
export default function WebhookSettings({ workspaceId }: { workspaceId: string }) {
  const [config, setConfig] = useState<WebhookConfig | null>(null);
  const [url, setUrl] = useState('');
  const [includeText, setIncludeText] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getWebhook(workspaceId)
      .then((c) => {
        setConfig(c);
        setUrl(c.url ?? '');
        setIncludeText(c.include_text);
      })
      .catch((e) => setMsg({ error: errorText(e) }));
    listWebhookDeliveries(workspaceId).then(setDeliveries).catch(() => setDeliveries([]));
  }, [workspaceId]);

  useEffect(load, [load]);

  async function run(action: () => Promise<WebhookConfig | unknown>, ok: string) {
    setBusy(true);
    setMsg({});
    try {
      const res = await action();
      if (res && typeof res === 'object' && 'secret' in res) setSecret((res as WebhookConfig).secret ?? null);
      setMsg({ ok });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const fmt = { format: (d: Date) => formatDateTime(d) };

  return (
    <section className="card">
      <h2>{t.webhook.title}</h2>
      <p className="muted small">{t.webhook.hint}</p>
      <div className="form wide">
        <label>
          {t.webhook.url}
          <input dir="ltr" value={url} placeholder="https://" maxLength={2048} onChange={(e) => setUrl(e.target.value)} />
        </label>
        {plainHttpToPublicHost(url) && (
          <p className="warn-text small" role="status">
            {t.webhook.plainHttpWarning}
          </p>
        )}
        <label className="check">
          <input type="checkbox" checked={includeText} onChange={(e) => setIncludeText(e.target.checked)} />
          {t.webhook.includeText}
        </label>
      </div>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !url.trim()}
          onClick={() => run(() => saveWebhook(workspaceId, url.trim(), includeText), t.settings.saved)}
        >
          {t.settings.save}
        </button>
        {config?.configured && (
          <>
            <button type="button" disabled={busy} onClick={() => run(() => testWebhook(workspaceId), t.webhook.testQueued)}>
              {t.webhook.test}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm(t.webhook.confirmRotate)) void run(() => rotateWebhookSecret(workspaceId), t.webhook.rotated);
              }}
            >
              {t.webhook.rotate}
            </button>
            <button
              type="button"
              className="link danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm(t.webhook.confirmRemove)) {
                  void run(async () => {
                    await deleteWebhook(workspaceId);
                    setSecret(null);
                  }, t.webhook.removed);
                }
              }}
            >
              {t.webhook.remove}
            </button>
          </>
        )}
      </div>
      {secret && (
        <div className="recovery">
          <strong>{t.webhook.secretOnce}</strong>
          <pre dir="ltr" className="secret">{secret}</pre>
          <p className="muted small">{t.webhook.verifyHint}</p>
        </div>
      )}
      {deliveries.length > 0 && (
        <>
          <h3>{t.webhook.recent}</h3>
          <table className="table small">
            <tbody>
              {deliveries.map((d) => (
                <tr key={d.id}>
                  <td>{fmt.format(new Date(d.created_at))}</td>
                  <td dir="ltr">{d.event}</td>
                  <td className={d.status === 'failed' ? 'danger' : d.status === 'sent' ? 'ok-text' : ''}>
                    {t.webhook.statuses[d.status]}
                  </td>
                  <td dir="ltr">{d.last_status_code ?? d.last_error ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
    </section>
  );
}
