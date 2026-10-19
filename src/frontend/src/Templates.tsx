import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  archiveTemplate,
  createTemplate,
  deleteTemplate,
  listCampaigns,
  listTemplates,
  unarchiveTemplate,
  updateTemplate,
  type Campaign,
  type LinkCard,
  type Me,
  type PostTemplate,
  type TemplateInput,
} from './api';
import EditorToolbar from './EditorToolbar';
import HashtagPicker from './HashtagPicker';
import LinkCardEditor from './LinkCardEditor';
import { formatDateTime } from './dates';
import { t } from './i18n';
import { countCharacters } from './unicodeFormat';

const MAX_CHARS = 3000;
const PLACEHOLDER_RE = /\{\{([A-Za-z0-9_\u058f-\u05ff]{1,32})\}\}/g;

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

function empty(): TemplateInput {
  return { name: '', body: '', link: null, campaign_id: null, tags: [] };
}

/** Live preview: placeholders render as chips. */
function TemplatePreview({ body, link }: { body: string; link: LinkCard | null }) {
  const parts: { kind: 'text' | 'chip'; value: string }[] = [];
  let last = 0;
  for (const m of body.matchAll(PLACEHOLDER_RE)) {
    const start = m.index ?? 0;
    if (start > last) parts.push({ kind: 'text', value: body.slice(last, start) });
    parts.push({ kind: 'chip', value: m[1] });
    last = start + m[0].length;
  }
  if (last < body.length) parts.push({ kind: 'text', value: body.slice(last) });
  return (
    <div className="template-preview" aria-label={t.templates.preview}>
      <div className="template-preview-body" dir="auto">
        {parts.length === 0 ? (
          <span className="muted">{t.templates.previewEmpty}</span>
        ) : (
          parts.map((p, i) =>
            p.kind === 'chip' ? (
              <span key={i} className="ph-chip">
                {p.value}
              </span>
            ) : (
              <span key={i}>{p.value}</span>
            ),
          )
        )}
      </div>
      {link && (
        <div className="li-link-card">
          <div className="li-link-title">{link.title || link.url}</div>
          {link.description && <div className="muted small">{link.description}</div>}
          <div className="muted small">{link.url}</div>
        </div>
      )}
    </div>
  );
}

export default function Templates({ me }: { me: Me }) {
  const membership = me.memberships[0];
  const wsId = membership?.workspace_id ?? '';
  const canWrite = membership?.role === 'owner' || membership?.role === 'editor' || membership?.role === 'approver';
  const isOwner = membership?.role === 'owner';

  const [items, setItems] = useState<PostTemplate[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<PostTemplate | null>(null);
  const [form, setForm] = useState<TemplateInput>(empty());
  const [tagsText, setTagsText] = useState('');
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(() => {
    if (!wsId) return;
    listTemplates(wsId, showArchived)
      .then((list) => {
        setItems(list);
        setSelected((cur) => (cur ? (list.find((x) => x.id === cur.id) ?? null) : cur));
      })
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [wsId, showArchived]);

  useEffect(load, [load]);
  useEffect(() => {
    if (!wsId) return;
    listCampaigns(wsId).then(setCampaigns).catch(() => setCampaigns([]));
  }, [wsId]);

  useEffect(() => {
    if (selected) {
      setForm({
        name: selected.name,
        body: selected.body,
        link: selected.link,
        campaign_id: selected.campaign_id,
        tags: selected.tags,
      });
      setTagsText(selected.tags.join(', '));
    }
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (x) =>
        x.name.toLowerCase().includes(q) ||
        x.tags.some((tag) => tag.toLowerCase().includes(q)) ||
        (x.campaign_name ?? '').toLowerCase().includes(q),
    );
  }, [items, search]);

  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;

  function startNew() {
    setSelected(null);
    setForm(empty());
    setTagsText('');
    setMsg({});
  }

  async function run(action: () => Promise<PostTemplate | void>, ok: string) {
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
    const tags = tagsText
      .split(/[,\s]+/)
      .map((s) => s.replace(/^#/, '').trim())
      .filter(Boolean)
      .slice(0, 10);
    const body: TemplateInput = {
      name: form.name.trim(),
      body: form.body,
      link: form.link,
      campaign_id: form.campaign_id || null,
      tags,
    };
    if (!body.name) return setMsg({ error: t.templates.nameRequired });
    void run(
      () => (selected ? updateTemplate(wsId, selected.id, body) : createTemplate(wsId, body)),
      t.templates.saved,
    );
  }

  const count = countCharacters(form.body);
  const archived = !!selected?.archived_at;

  return (
    <div className="posts-layout">
      <section className="card posts-list">
        <div className="row-between">
          <h2>{t.templates.list}</h2>
          {canWrite && (
            <button type="button" className="primary" onClick={startNew}>
              {t.templates.new}
            </button>
          )}
        </div>
        <input
          className="templates-search"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t.templates.search}
          aria-label={t.templates.search}
        />
        <label className="check">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          {t.templates.showArchived}
        </label>
        {filtered.length === 0 && <p className="muted">{t.templates.none}</p>}
        <ul className="post-items">
          {filtered.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={selected?.id === item.id ? 'post-item active' : 'post-item'}
                onClick={() => setSelected(item)}
              >
                <span className="post-snippet">{item.name}</span>
                <span className="muted small">
                  {item.campaign_name ? `${item.campaign_name} · ` : ''}
                  {t.templates.used(item.use_count)}
                  {item.archived_at ? ` · ${t.templates.archived}` : ''}
                </span>
                {item.tags.length > 0 && (
                  <span className="muted small">{item.tags.map((tag) => `#${tag}`).join(' ')}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card post-editor">
        <h2>{selected ? t.templates.edit : t.templates.new}</h2>
        <div className="form wide">
          <label>
            {t.templates.name}
            <input
              value={form.name}
              disabled={!canWrite || busy || archived}
              maxLength={80}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            {t.posts.campaign}
            <select
              value={form.campaign_id ?? ''}
              disabled={!canWrite || busy || archived}
              onChange={(e) => setForm({ ...form, campaign_id: e.target.value || null })}
            >
              <option value="">{t.posts.noCampaign}</option>
              {campaigns
                .filter((c) => c.status !== 'archived' || c.id === form.campaign_id)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            {t.templates.tags}
            <input
              value={tagsText}
              disabled={!canWrite || busy || archived}
              placeholder={t.templates.tagsHint}
              onChange={(e) => setTagsText(e.target.value)}
            />
          </label>
          <label>
            {t.posts.text}
            {canWrite && !archived && (
              <EditorToolbar
                textareaRef={textareaRef}
                value={form.body}
                onChange={(body) => setForm({ ...form, body })}
                disabled={busy}
              />
            )}
            <textarea
              ref={textareaRef}
              dir="auto"
              rows={10}
              value={form.body}
              disabled={!canWrite || busy || archived}
              onChange={(e) => setForm({ ...form, body: e.target.value })}
            />
          </label>
          <div className="counter-row">
            <span className={count > MAX_CHARS ? 'counter over' : 'counter'} dir="ltr">
              {count} / {MAX_CHARS}
            </span>
          </div>
          {canWrite && !archived && (
            <HashtagPicker
              workspaceId={wsId}
              body={form.body}
              onChange={(body) => setForm({ ...form, body })}
              disabled={busy}
            />
          )}
          {(canWrite && !archived) || form.link ? (
            <LinkCardEditor
              key={selected?.id ?? 'new'}
              workspaceId={wsId}
              value={form.link ?? null}
              onChange={(link) => setForm({ ...form, link })}
              disabled={!canWrite || busy || archived}
              hasImage={false}
            />
          ) : null}
        </div>

        <p className="muted small">{t.templates.placeholdersHint}</p>
        <TemplatePreview body={form.body} link={form.link ?? null} />

        {canWrite && (
          <div className="actions">
            {!archived && (
              <button type="button" className="primary" disabled={busy || !form.name.trim()} onClick={save}>
                {t.templates.save}
              </button>
            )}
            {selected && !archived && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => archiveTemplate(wsId, selected.id), t.templates.archivedOk)}
              >
                {t.templates.archive}
              </button>
            )}
            {selected && archived && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => unarchiveTemplate(wsId, selected.id), t.templates.unarchivedOk)}
              >
                {t.templates.unarchive}
              </button>
            )}
            {selected && isOwner && (
              <button
                type="button"
                className="link danger"
                disabled={busy}
                title={selected.use_count > 0 ? t.templates.deleteUsedHint : undefined}
                onClick={() => {
                  if (selected.use_count > 0) return setMsg({ error: t.errors.template_in_use });
                  if (window.confirm(t.templates.confirmDelete)) {
                    void run(async () => {
                      await deleteTemplate(wsId, selected.id);
                      setSelected(null);
                      setForm(empty());
                    }, t.templates.deleted);
                  }
                }}
              >
                {t.templates.delete}
              </button>
            )}
          </div>
        )}
        {selected && (
          <p className="muted small">
            {t.templates.used(selected.use_count)} · {formatDateTime(selected.updated_at)}
          </p>
        )}
        {msg.error && <p className="error" role="alert">{msg.error}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </section>
    </div>
  );
}
