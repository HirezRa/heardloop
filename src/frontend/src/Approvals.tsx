import { useCallback, useEffect, useState } from 'react';
import { ApiError, listPosts, postAction, rejectPost, type Me, type Post } from './api';
import { t } from './i18n';
import { formatDateTime } from './dates';
import { truncateCodePoints } from './textCut';

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.detail.startsWith('invalid_transition')) return t.posts.invalidTransition;
  }
  return t.errors.generic;
}

/** Review queue: every post waiting for approval, oldest first. */
export default function Approvals({ me, onOpenPost }: { me: Me; onOpenPost: (postId: string) => void }) {
  const membership = me.memberships[0];
  const wsId = membership?.workspace_id ?? '';
  const canApprove = membership?.role === 'owner' || membership?.role === 'approver';
  const [items, setItems] = useState<Post[]>([]);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!wsId) return;
    listPosts(wsId, { status: 'pending_approval', order: 'oldest' })
      .then(setItems)
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [wsId]);

  useEffect(load, [load]);

  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;

  const fmt = { format: (d: Date) => formatDateTime(d) };

  async function act(p: Post, action: () => Promise<Post>, ok: string) {
    setBusy(p.id);
    setMsg({});
    try {
      await action();
      setMsg({ ok });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card">
      {!canApprove && <p className="muted small">{t.approvals.readOnly}</p>}
      {items.length === 0 && <p className="muted">{t.approvals.none}</p>}
      <ul className="queue">
        {items.map((p) => (
          <li key={p.id} className="queue-item">
            <div className="queue-main">
              <div className="muted small">
                {p.account_name} · {t.approvals.waitingSince(fmt.format(new Date(p.updated_at)))}
              </div>
              <div className="post-snippet" dir="auto">
                {truncateCodePoints(p.body, 280)}
              </div>
            </div>
            <div className="actions">
              <button type="button" onClick={() => onOpenPost(p.id)}>
                {t.approvals.open}
              </button>
              {canApprove && (
                <>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy === p.id}
                    onClick={() => act(p, () => postAction(wsId, p.id, 'approve'), t.posts.approved)}
                  >
                    {t.posts.approve}
                  </button>
                  <button
                    type="button"
                    disabled={busy === p.id}
                    onClick={() => {
                      const comment = window.prompt(t.posts.rejectPrompt)?.trim();
                      if (comment) void act(p, () => rejectPost(wsId, p.id, comment), t.posts.rejected);
                    }}
                  >
                    {t.posts.reject}
                  </button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
    </section>
  );
}
