import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  createCampaign,
  deleteCampaign,
  listAudiences,
  listCampaigns,
  listPosts,
  updateCampaign,
  type Audience,
  type Campaign,
  type CampaignInput,
  type CampaignStatus,
  type Me,
  type Post,
  type PostStatus,
} from './api';
import { t } from './i18n';
import { DateInput } from './DateInputs';
import { formatDay } from './dates';
import { truncateCodePoints } from './textCut';

const STATUSES: CampaignStatus[] = ['planned', 'active', 'completed', 'archived'];
const COLORS = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2', '#ca8a04', '#64748b'];

const empty = (): CampaignInput => ({
  name: '',
  goal: null,
  audience: null,
  audience_id: null,
  start_date: null,
  end_date: null,
  tags: [],
  color: COLORS[0],
  status: 'planned',
});

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

function fromCampaign(c: Campaign): CampaignInput {
  const { name, goal, audience, audience_id, start_date, end_date, tags, color, status } = c;
  return { name, goal, audience, audience_id, start_date, end_date, tags, color, status };
}

export default function Campaigns({
  me,
  onOpenPost,
  onOpenAudiences,
}: {
  me: Me;
  onOpenPost: (postId: string) => void;
  onOpenAudiences?: () => void;
}) {
  const membership = me.memberships[0];
  const wsId = membership?.workspace_id ?? '';
  const canWrite = membership?.role === 'owner' || membership?.role === 'editor' || membership?.role === 'approver';

  const [items, setItems] = useState<Campaign[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [form, setForm] = useState<CampaignInput>(empty);
  const [tagsText, setTagsText] = useState('');
  const [posts, setPosts] = useState<Post[]>([]);
  const [audiences, setAudiences] = useState<Audience[]>([]);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!wsId) return;
    listCampaigns(wsId, showArchived)
      .then((list) => {
        setItems(list);
        setSelected((cur) => (cur ? (list.find((c) => c.id === cur.id) ?? null) : cur));
      })
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [wsId, showArchived]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!wsId) return;
    listAudiences(wsId).then(setAudiences).catch(() => setAudiences([]));
  }, [wsId]);

  useEffect(() => {
    if (!selected) {
      setPosts([]);
      return;
    }
    setForm(fromCampaign(selected));
    setTagsText(selected.tags.join(', '));
    listPosts(wsId, { campaign_id: selected.id }).then(setPosts).catch(() => setPosts([]));
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;

  const fmtDate = (value: string | null) => (value ? formatDay(value) : '');

  function startNew() {
    setSelected(null);
    setForm(empty());
    setTagsText('');
    setMsg({});
  }

  async function run(action: () => Promise<Campaign | void>, ok: string) {
    setBusy(true);
    setMsg({});
    try {
      const result = await action();
      setSelected(result ?? null);
      setMsg({ ok });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  function save() {
    const body: CampaignInput = {
      ...form,
      name: form.name.trim(),
      goal: form.goal?.trim() || null,
      audience: form.audience?.trim() || null,
      start_date: form.start_date || null,
      end_date: form.end_date || null,
      tags: tagsText
        .split(/[,\s]+/)
        .map((s) => s.replace(/^#/, '').trim())
        .filter(Boolean),
    };
    if (!body.name) return setMsg({ error: t.campaigns.nameRequired });
    void run(
      () => (selected ? updateCampaign(wsId, selected.id, body) : createCampaign(wsId, body)),
      t.campaigns.saved,
    );
  }

  const total = (c: Campaign) => Object.values(c.post_counts).reduce((a, b) => a + (b ?? 0), 0);
  const editable = canWrite;

  return (
    <div className="posts-layout">
      <section className="card posts-list">
        <div className="row-between">
          <h2>{t.campaigns.list}</h2>
          {canWrite && (
            <button type="button" className="primary" onClick={startNew}>
              {t.campaigns.new}
            </button>
          )}
        </div>
        <label className="check small">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          {t.campaigns.showArchived}
        </label>
        {items.length === 0 && <p className="muted">{t.campaigns.none}</p>}
        <ul className="post-items">
          {items.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={selected?.id === c.id ? 'post-item active' : 'post-item'}
                onClick={() => {
                  setSelected(c);
                  setMsg({});
                }}
              >
                <span className="camp-title">
                  <span className="dot-color" style={{ background: c.color }} aria-hidden="true" />
                  <span dir="auto">{c.name}</span>
                </span>
                <span className="muted small">
                  {t.campaigns.statuses[c.status]}
                  {c.start_date ? ` · ${fmtDate(c.start_date)}` : ''}
                  {c.end_date ? ` - ${fmtDate(c.end_date)}` : ''} · {t.campaigns.postCount(total(c))}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card posts-editor">
        <h2>{selected ? t.campaigns.edit : t.campaigns.new}</h2>
        <div className="form wide">
          <label>
            {t.campaigns.name}
            <input
              dir="auto"
              maxLength={200}
              value={form.name}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            {t.campaigns.goal}
            <textarea
              dir="auto"
              rows={2}
              maxLength={1000}
              value={form.goal ?? ''}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, goal: e.target.value })}
            />
          </label>
          <label>
            {t.campaigns.audience}
            <input
              dir="auto"
              maxLength={1000}
              value={form.audience ?? ''}
              disabled={!editable}
              onChange={(e) => setForm({ ...form, audience: e.target.value })}
            />
          </label>
          <label>
            {t.campaigns.audienceSelect}
            <select
              value={form.audience_id ?? ''}
              disabled={!editable || audiences.length === 0}
              onChange={(e) => setForm({ ...form, audience_id: e.target.value || null })}
            >
              <option value="">{audiences.length === 0 ? t.posts.noAudiencesOption : t.posts.noAudience}</option>
              {audiences.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.code} · {a.name} ({a.time_zone})
                </option>
              ))}
            </select>
            {audiences.length === 0 ? (
              editable && (
                <span className="muted small">
                  {t.posts.noAudiencesYet}{' '}
                  {onOpenAudiences && (
                    <button type="button" className="link" onClick={onOpenAudiences}>
                      {t.posts.setUpAudiences}
                    </button>
                  )}
                </span>
              )
            ) : (
              <span className="muted small">{t.campaigns.audienceHint}</span>
            )}
          </label>
          <div className="row-fields">
            <label>
              {t.campaigns.start}
              <DateInput
                label={t.campaigns.start}
                value={form.start_date}
                disabled={!editable}
                onChange={(v) => setForm({ ...form, start_date: v })}
              />
            </label>
            <label>
              {t.campaigns.end}
              <DateInput
                label={t.campaigns.end}
                value={form.end_date}
                min={form.start_date ?? undefined}
                disabled={!editable}
                onChange={(v) => setForm({ ...form, end_date: v })}
              />
            </label>
            <label>
              {t.campaigns.status}
              <select
                value={form.status}
                disabled={!editable}
                onChange={(e) => setForm({ ...form, status: e.target.value as CampaignStatus })}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t.campaigns.statuses[s]}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            {t.campaigns.tags}
            <input
              dir="auto"
              value={tagsText}
              placeholder={t.campaigns.tagsHint}
              disabled={!editable}
              onChange={(e) => setTagsText(e.target.value)}
            />
          </label>
          <div className="color-row" role="radiogroup" aria-label={t.campaigns.color}>
            <span className="small">{t.campaigns.color}</span>
            {COLORS.map((c) => (
              <button
                key={c}
                type="button"
                role="radio"
                aria-checked={form.color === c}
                aria-label={c}
                className={form.color === c ? 'swatch active' : 'swatch'}
                style={{ background: c }}
                disabled={!editable}
                onClick={() => setForm({ ...form, color: c })}
              />
            ))}
          </div>
        </div>

        {canWrite && (
          <div className="actions">
            <button type="button" className="primary" disabled={busy} onClick={save}>
              {t.campaigns.save}
            </button>
            {selected && total(selected) === 0 && (
              <button
                type="button"
                className="link danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(t.campaigns.confirmDelete)) {
                    void run(async () => {
                      await deleteCampaign(wsId, selected.id);
                      startNew();
                    }, t.campaigns.deleted);
                  }
                }}
              >
                {t.campaigns.delete}
              </button>
            )}
          </div>
        )}

        {selected && (
          <>
            <h3>{t.campaigns.summary}</h3>
            <div className="summary-chips">
              {(Object.entries(selected.post_counts) as [PostStatus, number][]).map(([st, n]) => (
                <span key={st} className={`status status-${st}`}>
                  {t.posts.statuses[st]}: {n}
                </span>
              ))}
              {total(selected) === 0 && <span className="muted small">{t.campaigns.noPosts}</span>}
            </div>
            <ul className="post-items">
              {posts.map((p) => (
                <li key={p.id}>
                  <button type="button" className="post-item" onClick={() => onOpenPost(p.id)}>
                    <span className={`status status-${p.status}`}>{t.posts.statuses[p.status]}</span>
                    <span className="post-snippet" dir="auto">
                      {truncateCodePoints(p.body, 90)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="muted small">{t.campaigns.assignHint}</p>
          </>
        )}
        {msg.error && <p className="error" role="alert">{msg.error}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </section>
    </div>
  );
}
