import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  createPost,
  deletePost,
  getPost,
  getSettings,
  listAccounts,
  listCampaigns,
  listPosts,
  listPostVersions,
  listTemplates,
  postAction,
  rejectPost,
  restorePostVersion,
  savePostAsTemplate,
  schedulePost,
  updatePost,
  uploadMedia,
  useTemplate,
  MAX_IMAGE_BYTES,
  MAX_VIDEO_BYTES,
  MIN_VIDEO_BYTES,
  type Campaign,
  type LinkCard,
  type LinkedInAccount,
  type Me,
  type Post,
  type PostTemplate,
  type PostVersion,
} from './api';
import { t } from './i18n';
import { publishErrorReasonLabel, publishErrorText } from './publishErrors';
import { DateTimeInput } from './DateInputs';
import { diffWords } from './diff';
import EditorToolbar from './EditorToolbar';
import LinkCardEditor from './LinkCardEditor';
import HashtagPicker from './HashtagPicker';
import AiAssistant from './AiAssistant';
import BestTimes from './BestTimes';
import MetricsPanel from './MetricsPanel';
import { countCharacters, styledRatio, utf16Length } from './unicodeFormat';
import { formatDateTime } from './dates';
import { displaySnippet, isImported } from './postText';

const MAX_CHARS = 3000;
const SEE_MORE_AT = 210; // LinkedIn cuts the feed preview at roughly this many characters
const PLACEHOLDER_RE = /\{\{([A-Za-z0-9_\u058f-\u05ff]{1,32})\}\}/g;

function leftoverPlaceholders(body: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of body.matchAll(PLACEHOLDER_RE)) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.detail.startsWith('invalid_transition')) return t.posts.invalidTransition;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

/** "Next full hour" as YYYY-MM-DDTHH:mm in the workspace time zone (the server interprets it there). */
function defaultScheduleValue(timeZone: string): string {
  const next = new Date(Date.now() + 60 * 60 * 1000);
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(next)
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:00`;
}

/** An absolute time as YYYY-MM-DDTHH:mm in the workspace time zone (for datetime-local inputs). */
function toLocalInput(value: string, timeZone: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(new Date(value))
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

const URL_RE = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;

/** Plain text with URLs shown as links, the way LinkedIn renders them (text itself is never HTML). */
function Linkified({ text }: { text: string }) {
  const parts = text.split(URL_RE);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <a key={i} href={part} target="_blank" rel="noopener noreferrer nofollow" dir="ltr">
            {part}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

type PreviewMedia = { url: string; mime_type?: string } | undefined;

function Preview({
  name,
  body,
  media,
  link,
  expanded,
  imported,
}: {
  name: string;
  body: string;
  media?: PreviewMedia;
  link?: LinkCard | null;
  expanded?: boolean;
  imported?: boolean;
}) {
  const [open, setOpen] = useState(!!expanded);
  const chars = Array.from(body);
  const long = chars.length > SEE_MORE_AT;
  const shown = open || !long ? body : chars.slice(0, SEE_MORE_AT).join('').trimEnd();
  const isVideo = !!media?.mime_type?.startsWith('video/');
  const imageUrl = media && !isVideo ? media.url : undefined;
  return (
    <div className="li-preview" aria-label={t.posts.preview}>
      <div className="li-head">
        <div className="li-avatar" aria-hidden="true">
          {(name || '?').slice(0, 1).toUpperCase()}
        </div>
        <div>
          <div className="li-name">{name || t.posts.noAccount}</div>
          <div className="muted small">{t.posts.previewMeta}</div>
        </div>
      </div>
      <div className="li-body" dir="auto">
        {shown ? (
          <Linkified text={shown} />
        ) : (
          <span className="muted">{imported ? t.posts.importedPlaceholder : t.posts.emptyPreview}</span>
        )}
        {long && !open && (
          <>
            {'… '}
            <button type="button" className="link" onClick={() => setOpen(true)}>
              {t.posts.seeMore}
            </button>
          </>
        )}
      </div>
      {link ? (
        <div className="li-card">
          {imageUrl && <img className="li-card-img" src={imageUrl} alt="" />}
          <div className="li-card-text">
            <div className="li-card-title" dir="auto">
              {link.title || t.linkCard.untitled}
            </div>
            {link.description && (
              <div className="muted small li-card-desc" dir="auto">
                {link.description}
              </div>
            )}
            <div className="muted small" dir="ltr">
              {hostOf(link.url)}
            </div>
          </div>
        </div>
      ) : isVideo && media ? (
        <video className="li-video" src={media.url} controls preload="metadata" />
      ) : (
        imageUrl && <img className="li-image" src={imageUrl} alt="" />
      )}
    </div>
  );
}

/** The post as it will look in the feed, in a native dialog (focus trap and Escape come for free). */
function FullPreview({
  onClose,
  ...props
}: {
  onClose: () => void;
  name: string;
  body: string;
  media?: PreviewMedia;
  link?: LinkCard | null;
  imported?: boolean;
}) {
  const [width, setWidth] = useState<'desktop' | 'mobile'>('desktop');
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
      opener?.focus?.();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      aria-label={t.posts.fullPreview}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // backdrop click
      }}
    >
      <div className="row-between">
        <div className="segmented" role="group" aria-label={t.posts.previewWidth}>
          {(['desktop', 'mobile'] as const).map((w) => (
            <button key={w} type="button" aria-pressed={width === w} onClick={() => setWidth(w)}>
              {t.posts.widths[w]}
            </button>
          ))}
        </div>
        <button type="button" className="link" onClick={onClose}>
          {t.posts.close}
        </button>
      </div>
      <div className={`feed-frame ${width}`}>
        <Preview {...props} expanded />
      </div>
    </dialog>
  );
}

function HistoryPanel({
  workspaceId,
  post,
  timeZone,
  canRestore,
  restoreHint,
  onRestored,
  onClose,
}: {
  workspaceId: string;
  post: Post;
  timeZone: string;
  canRestore: boolean;
  restoreHint: string;
  onRestored: (p: Post) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [versions, setVersions] = useState<PostVersion[]>([]);
  const [picked, setPicked] = useState<PostVersion | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const current = versions.find((v) => v.is_current) ?? versions[0] ?? null;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.showModal();
    document.body.style.overflow = 'hidden';
    listPostVersions(workspaceId, post.id)
      .then((list) => {
        setVersions(list);
        setPicked(list.find((v) => !v.is_current) ?? list[0] ?? null);
      })
      .catch((e) => setError(errorText(e)));
    return () => {
      document.body.style.overflow = '';
      opener?.focus?.();
    };
  }, [workspaceId, post.id]);

  const parts = picked && current ? diffWords(current.body, picked.body) : [];
  const mediaId = picked?.media_ids[0];
  const mediaSrc = mediaId ? `/api/v1/workspaces/${workspaceId}/media/${mediaId}` : null;

  async function restore() {
    if (!picked || !canRestore) return;
    setBusy(true);
    setError('');
    try {
      const result = await restorePostVersion(workspaceId, post.id, picked.id);
      onRestored(result);
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="modal history-modal"
      aria-label={t.posts.history}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="row-between">
        <h2>{t.posts.history}</h2>
        <button type="button" className="link" onClick={onClose}>
          {t.posts.close}
        </button>
      </div>
      <p className="muted small">{t.posts.historyCap}</p>
      <div className="history-layout">
        <ul className="history-list">
          {versions.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                className={picked?.id === v.id ? 'history-item active' : 'history-item'}
                onClick={() => setPicked(v)}
              >
                <span>{formatDateTime(v.created_at, timeZone)}</span>
                <span className="muted small">{v.author_name}</span>
                <span className="post-snippet">{v.body.slice(0, 80) || '—'}</span>
                <span className="badges">
                  {v.is_current && <span className="badge">{t.posts.currentVersion}</span>}
                  {v.approved && <span className="badge ok-badge">{t.posts.approvedVersion}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="history-detail">
          {picked ? (
            <>
              <div className="diff-body" dir="auto">
                {parts.map((p, i) =>
                  p.type === 'equal' ? (
                    <span key={i}>{p.text}</span>
                  ) : p.type === 'add' ? (
                    <ins key={i}>{p.text}</ins>
                  ) : (
                    <del key={i}>{p.text}</del>
                  ),
                )}
              </div>
              {mediaSrc && <img className="history-thumb" src={mediaSrc} alt="" />}
              {picked.link && (
                <div className="li-link-card">
                  <div className="li-link-title">{picked.link.title}</div>
                  {picked.link.description && <div className="muted small">{picked.link.description}</div>}
                  <div className="muted small">{picked.link.url}</div>
                </div>
              )}
              <button
                type="button"
                className="primary"
                disabled={busy || !canRestore || picked.is_current}
                title={!canRestore ? restoreHint : picked.is_current ? t.posts.alreadyCurrent : undefined}
                onClick={() => void restore()}
              >
                {t.posts.restoreVersion}
              </button>
            </>
          ) : (
            <p className="muted">{t.posts.historyEmpty}</p>
          )}
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </dialog>
  );
}

function TemplatePicker({
  workspaceId,
  onPick,
  onClose,
}: {
  workspaceId: string;
  onPick: (filled: { body: string; link: LinkCard | null; campaign_id: string | null; leftovers: string[] }) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<PostTemplate[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.showModal();
    document.body.style.overflow = 'hidden';
    listTemplates(workspaceId)
      .then(setItems)
      .catch((e) => setError(errorText(e)));
    return () => {
      document.body.style.overflow = '';
      opener?.focus?.();
    };
  }, [workspaceId]);

  const filtered = items.filter((x) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      x.name.toLowerCase().includes(q) ||
      x.tags.some((tag) => tag.toLowerCase().includes(q)) ||
      (x.campaign_name ?? '').toLowerCase().includes(q)
    );
  });

  async function choose(id: string) {
    setBusy(true);
    setError('');
    try {
      const used = await useTemplate(workspaceId, id);
      onPick({
        body: used.body,
        link: used.link,
        campaign_id: used.campaign_id,
        leftovers: used.placeholders_left,
      });
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-label={t.posts.fromTemplate}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="row-between">
        <h2>{t.posts.fromTemplate}</h2>
        <button type="button" className="link" onClick={onClose}>
          {t.posts.close}
        </button>
      </div>
      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t.templates.search}
        aria-label={t.templates.search}
      />
      <ul className="post-items">
        {filtered.map((item) => (
          <li key={item.id}>
            <button type="button" className="post-item" disabled={busy} onClick={() => void choose(item.id)}>
              <span className="post-snippet">{item.name}</span>
              <span className="muted small">
                {item.campaign_name ? `${item.campaign_name} · ` : ''}
                {t.templates.used(item.use_count)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {filtered.length === 0 && <p className="muted">{t.templates.none}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </dialog>
  );
}

export default function Posts({ me, focusPostId }: { me: Me; focusPostId?: string | null }) {
  const membership = me.memberships[0];
  const role = membership?.role;
  const canWrite = role === 'owner' || role === 'editor' || role === 'approver';
  const canApprove = role === 'owner' || role === 'approver';

  const [items, setItems] = useState<Post[]>([]);
  const [accounts, setAccounts] = useState<LinkedInAccount[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [newCampaignId, setNewCampaignId] = useState('');
  const [requireApproval, setRequireApproval] = useState(true);
  const [timeZone, setTimeZone] = useState('UTC');
  const [selected, setSelected] = useState<Post | null>(null);
  const [draft, setDraft] = useState<{
    account_id: string;
    body: string;
    media: { id: string; url: string; mime_type?: string }[];
    link: LinkCard | null;
  }>({ account_id: '', body: '', media: [], link: null });
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [fullPreview, setFullPreview] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [placeholdersLeft, setPlaceholdersLeft] = useState<string[]>([]);
  const [when, setWhen] = useState(() => defaultScheduleValue('UTC'));
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const wsId = membership?.workspace_id ?? '';
  const active = useMemo(() => accounts.filter((a) => a.status === 'active'), [accounts]);

  const load = useCallback(() => {
    if (!wsId) return;
    listPosts(wsId)
      .then((list) => {
        setItems(list);
        setSelected((cur) => (cur ? (list.find((p) => p.id === cur.id) ?? null) : cur));
      })
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [wsId]);

  useEffect(() => {
    if (!wsId) return;
    load();
    listAccounts(wsId).then(setAccounts).catch(() => setAccounts([]));
    listCampaigns(wsId).then(setCampaigns).catch(() => setCampaigns([]));
    getSettings(wsId)
      .then((s) => {
        setRequireApproval(s.require_approval);
        setTimeZone(s.time_zone);
        setWhen(defaultScheduleValue(s.time_zone));
      })
      .catch(() => setRequireApproval(true));
  }, [wsId, load]);

  // Refresh while something is on its way to LinkedIn.
  useEffect(() => {
    if (!items.some((p) => p.status === 'scheduled' || p.status === 'publishing')) return;
    const timer = window.setInterval(load, 10_000);
    return () => window.clearInterval(timer);
  }, [items, load]);

  useEffect(() => {
    if (!selected && !draft.account_id && active[0]) setDraft((d) => ({ ...d, account_id: active[0].id }));
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Opened from the calendar, a campaign or the approval queue.
  useEffect(() => {
    if (!wsId || !focusPostId) return;
    getPost(wsId, focusPostId)
      .then((p) => {
        setSelected(p);
        setMsg({});
      })
      .catch((e) => setMsg({ error: errorText(e) }));
  }, [wsId, focusPostId]);

  useEffect(() => {
    if (selected?.status === 'scheduled' && selected.scheduled_at) setWhen(toLocalInput(selected.scheduled_at, timeZone));
  }, [selected?.id, selected?.scheduled_at, timeZone]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selected) {
      setDraft({ account_id: selected.account_id, body: selected.body, media: selected.media, link: selected.link });
      setPlaceholdersLeft([]);
    }
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!membership) return <p className="muted">{t.settings.noWorkspace}</p>;

  function startNew() {
    setSelected(null);
    setNewCampaignId('');
    setDraft({ account_id: active[0]?.id ?? '', body: '', media: [], link: null });
    setPlaceholdersLeft([]);
    setMsg({});
  }

  async function pickMedia(file: File | undefined) {
    if (!file) return;
    const isVideo = file.type === 'video/mp4' || /\.mp4$/i.test(file.name);
    if (isVideo) {
      if (file.size > MAX_VIDEO_BYTES) return setMsg({ error: t.posts.videoTooLarge });
      if (file.size < MIN_VIDEO_BYTES) return setMsg({ error: t.posts.videoTooSmall });
      if (draft.link) return setMsg({ error: t.errors.video_with_link });
    } else {
      if (file.size > MAX_IMAGE_BYTES) return setMsg({ error: t.posts.imageTooLarge });
      if (!['image/png', 'image/jpeg', 'image/gif'].includes(file.type)) return setMsg({ error: t.posts.imageType });
    }
    setBusy(true);
    setMsg({});
    setUploadPct(0);
    try {
      const uploaded = await uploadMedia(wsId, file, setUploadPct);
      setDraft((d) => ({ ...d, media: [{ id: uploaded.id, url: uploaded.url, mime_type: uploaded.mime_type }] }));
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }

  async function run(action: () => Promise<Post | void>, ok: string) {
    setBusy(true);
    setMsg({});
    try {
      const result = await action();
      if (result) setSelected(result);
      setMsg({ ok });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const mediaKey = (m: { id: string }[]) => m.map((x) => x.id).join(',');
  const linkKey = (l: LinkCard | null) => (l ? `${l.url}|${l.title}|${l.description ?? ''}` : '');
  const dirty = selected
    ? draft.body !== selected.body ||
      draft.account_id !== selected.account_id ||
      mediaKey(draft.media) !== mediaKey(selected.media) ||
      linkKey(draft.link) !== linkKey(selected.link)
    : !!draft.body;
  const status = selected?.status;
  const editable = !status || ['draft', 'pending_approval', 'approved', 'scheduled', 'failed'].includes(status);
  const imported = !!selected && isImported(selected);
  // An imported post is already live; only its text (and campaign) can still be filled in.
  const importedLive = imported && status === 'published';
  const textEditable = editable || importedLive;
  const accountName = accounts.find((a) => a.id === draft.account_id)?.display_name ?? '';
  const count = countCharacters(draft.body);
  const units = utf16Length(draft.body);
  const styled = styledRatio(draft.body);
  const linkIncomplete = !!draft.link && !draft.link.title.trim();

  async function save() {
    const left = leftoverPlaceholders(draft.body);
    if (left.length) {
      setPlaceholdersLeft(left);
      return setMsg({ error: t.posts.placeholdersRemain(left) });
    }
    setPlaceholdersLeft([]);
    if (selected && importedLive) {
      await run(() => updatePost(wsId, selected.id, { body: draft.body }), t.posts.saved);
      return;
    }
    if (!draft.account_id) return setMsg({ error: t.posts.chooseAccount });
    if (linkIncomplete) return setMsg({ error: t.linkCard.titleRequired });
    if (selected && status !== 'draft' && !window.confirm(t.posts.confirmEditResets)) return;
    await run(
      () =>
        selected
          ? updatePost(wsId, selected.id, {
              body: draft.body,
              account_id: draft.account_id,
              media_ids: draft.media.map((m) => m.id),
              link: draft.link,
            })
          : createPost(wsId, {
              account_id: draft.account_id,
              body: draft.body,
              media_ids: draft.media.map((m) => m.id),
              campaign_id: newCampaignId || null,
              link: draft.link,
            }),
      t.posts.saved,
    );
  }

  const canRestoreVersion =
    !!selected &&
    (['draft', 'pending_approval', 'approved', 'failed'].includes(selected.status) ||
      (isImported(selected) && selected.status === 'published'));
  const restoreHint = selected?.status === 'scheduled' ? t.posts.restoreScheduledHint : t.posts.restoreNotEditable;

  const fmt = { format: (d: Date) => formatDateTime(d, timeZone) };
  const canSchedule = status === 'approved' || status === 'failed' || (status === 'draft' && !requireApproval);
  const unknownOutcome = !!selected?.last_error && /unknown_outcome|timeout/.test(selected.last_error);

  return (
    <div className="posts-layout">
      <section className="card posts-list">
        <div className="row-between">
          <h2>{t.posts.list}</h2>
          {canWrite && (
            <div className="row gap">
              <button type="button" className="primary" onClick={startNew}>
                {t.posts.new}
              </button>
            </div>
          )}
        </div>
        {items.length === 0 && <p className="muted">{t.posts.none}</p>}
        <ul className="post-items">
          {items.map((p) => {
            const shown = displaySnippet(p.body, isImported(p));
            return (
              <li key={p.id}>
                <button
                  type="button"
                  className={selected?.id === p.id ? 'post-item active' : 'post-item'}
                  onClick={() => {
                    setSelected(p);
                    setMsg({});
                  }}
                >
                  <span
                    className={`status status-${p.status}`}
                    title={
                      p.status === 'failed' && p.last_error
                        ? publishErrorText(p.last_error).message
                        : undefined
                    }
                  >
                    {t.posts.statuses[p.status]}
                  </span>
                  {p.external_source === 'linkedin_export' && (
                    <span className="badge imported" title={t.posts.importedTitle}>
                      {t.posts.imported}
                    </span>
                  )}
                  {(p.status === 'scheduled' || p.status === 'failed') && p.account_needs_reconnect_reason && (
                    <span
                      className="badge warn"
                      title={t.posts.accountNeedsReconnect(
                        publishErrorReasonLabel(p.account_needs_reconnect_reason),
                      )}
                    >
                      {t.posts.needsReconnectShort}
                    </span>
                  )}
                  <span className={shown.placeholder ? 'post-snippet muted' : 'post-snippet'} dir="auto">
                    {shown.text.slice(0, 90)}
                    {shown.text.length > 90 ? '…' : ''}
                  </span>
                  <span className="muted small">
                    {p.published_at
                      ? fmt.format(new Date(p.published_at))
                      : p.scheduled_at
                        ? fmt.format(new Date(p.scheduled_at))
                        : fmt.format(new Date(p.updated_at))}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="card posts-editor">
        <h2>{selected ? t.posts.edit : t.posts.new}</h2>
        {active.length === 0 && <p className="error">{t.posts.noActiveAccount}</p>}
        {selected?.last_review_comment && (
          <p className="banner">
            {t.posts.reviewComment} <span dir="auto">{selected.last_review_comment}</span>
          </p>
        )}
        {selected?.status === 'failed' && selected.last_error && (() => {
          const pe = publishErrorText(selected.last_error);
          return (
            <p className="error">
              {t.posts.failedWith} {pe.message}
              {pe.whatToDo && <><br />{pe.whatToDo}</>}
              {unknownOutcome && !pe.whatToDo && <><br />{t.posts.checkLinkedInFirst}</>}
            </p>
          );
        })()}
        {selected &&
          (selected.status === 'scheduled' || selected.status === 'failed') &&
          selected.account_needs_reconnect_reason && (
            <p className="banner" role="status">
              {t.posts.accountNeedsReconnect(publishErrorReasonLabel(selected.account_needs_reconnect_reason))}{' '}
              <a href="/?section=accounts">{t.posts.goToAccounts}</a>
            </p>
          )}
        {selected?.status === 'published' && selected.linkedin_url && (
          <p className="ok">
            {t.posts.publishedAt} {selected.published_at && fmt.format(new Date(selected.published_at))} ·{' '}
            <a href={selected.linkedin_url} target="_blank" rel="noopener noreferrer">
              {t.posts.viewOnLinkedIn}
            </a>
          </p>
        )}
        {selected?.status === 'scheduled' && selected.scheduled_at && (
          <p className="ok">
            {t.posts.scheduledFor} {fmt.format(new Date(selected.scheduled_at))}
          </p>
        )}
        {selected?.status === 'publishing' && <p className="muted">{t.posts.publishing}</p>}

        {canWrite && !selected && (
          <div className="actions" style={{ marginBottom: 8 }}>
            <button type="button" disabled={busy} onClick={() => setShowTemplates(true)}>
              {t.posts.fromTemplate}
            </button>
          </div>
        )}
        <div className="form wide">
          <label>
            {t.posts.account}
            <select
              value={draft.account_id}
              disabled={!canWrite || !editable}
              onChange={(e) => setDraft({ ...draft, account_id: e.target.value })}
            >
              <option value="">{t.posts.chooseAccount}</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id} disabled={a.status !== 'active'}>
                  {a.display_name}
                  {a.status !== 'active' ? ` (${t.accounts.statuses[a.status]})` : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t.posts.campaign}
            <select
              value={selected ? (selected.campaign_id ?? '') : newCampaignId}
              disabled={!canWrite || busy || status === 'publishing'}
              onChange={(e) => {
                const value = e.target.value;
                if (!selected) return setNewCampaignId(value);
                void run(() => updatePost(wsId, selected.id, { campaign_id: value || null }), t.posts.campaignSaved);
              }}
            >
              <option value="">{t.posts.noCampaign}</option>
              {campaigns
                .filter((c) => c.status !== 'archived' || c.id === selected?.campaign_id)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
          </label>
          {importedLive && canWrite && <p className="muted small">{t.posts.importedHint}</p>}
          <label>
            {t.posts.text}
            {canWrite && textEditable && (
              <EditorToolbar
                textareaRef={textareaRef}
                value={draft.body}
                onChange={(body) => setDraft({ ...draft, body })}
                disabled={busy}
              />
            )}
            <textarea
              ref={textareaRef}
              dir="auto"
              rows={10}
              value={draft.body}
              disabled={!canWrite || !textEditable}
              placeholder={imported ? t.posts.importedPlaceholder : undefined}
              className={placeholdersLeft.length ? 'has-placeholders' : undefined}
              onChange={(e) => {
                const body = e.target.value;
                setDraft({ ...draft, body });
                setPlaceholdersLeft(leftoverPlaceholders(body));
              }}
            />
          </label>
          <div className="counter-row">
            <span
              className={count > MAX_CHARS ? 'counter over' : count > MAX_CHARS - 200 ? 'counter warn' : 'counter'}
              dir="ltr"
              title={units !== count ? t.editor.unitsHint(units) : undefined}
            >
              {count} / {MAX_CHARS}
              {units !== count && <span className="muted"> ({units})</span>}
            </span>
            {styled > 0.2 && <span className="muted small warn-text">{t.editor.accessibilityWarning}</span>}
          </div>
          {placeholdersLeft.length > 0 && (
            <p className="muted small warn-text" role="status">
              {t.posts.placeholdersRemain(placeholdersLeft)}
            </p>
          )}
          {canWrite && textEditable && (
            <HashtagPicker workspaceId={wsId} body={draft.body} onChange={(body) => setDraft({ ...draft, body })} disabled={busy} />
          )}
          {canWrite && textEditable && me.ai_assist && (
            <AiAssistant
              workspaceId={wsId}
              textareaRef={textareaRef}
              value={draft.body}
              onChange={(body) => {
                setDraft({ ...draft, body });
                setPlaceholdersLeft(leftoverPlaceholders(body));
              }}
              disabled={busy}
            />
          )}
          {canWrite && editable && (
            <div className="image-row">
              {draft.media.length === 0 ? (
                <label className="file-btn">
                  {t.posts.addMedia}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/gif,video/mp4"
                    disabled={busy}
                    onChange={(e) => {
                      void pickMedia(e.target.files?.[0]);
                      e.target.value = '';
                    }}
                  />
                </label>
              ) : (
                <button type="button" className="link danger" onClick={() => setDraft({ ...draft, media: [] })}>
                  {draft.media[0]?.mime_type?.startsWith('video/') ? t.posts.removeVideo : t.posts.removeImage}
                </button>
              )}
              {uploadPct !== null ? (
                <progress className="upload" value={uploadPct} max={1} aria-label={t.posts.uploading} />
              ) : (
                <span className="muted small">{t.posts.mediaHint}</span>
              )}
            </div>
          )}
          {draft.media[0]?.mime_type?.startsWith('video/') ? (
            <p className="muted small">{t.linkCard.notWithVideo}</p>
          ) : (canWrite && editable) || draft.link ? (
            <LinkCardEditor
              key={selected?.id ?? 'new'}
              workspaceId={wsId}
              value={draft.link}
              onChange={(link) => setDraft({ ...draft, link })}
              disabled={!canWrite || !editable || busy}
              hasImage={draft.media.length > 0}
            />
          ) : null}
        </div>

        <div className="preview-head">
          <span className="muted small">{t.posts.preview}</span>
          <button type="button" className="link" onClick={() => setFullPreview(true)}>
            {t.posts.fullPreview}
          </button>
        </div>
        <Preview name={accountName} body={draft.body} media={draft.media[0]} link={draft.link} imported={imported} />
        {fullPreview && (
          <FullPreview
            name={accountName}
            body={draft.body}
            media={draft.media[0]}
            link={draft.link}
            imported={imported}
            onClose={() => setFullPreview(false)}
          />
        )}

        {canWrite && (
          <div className="actions">
            {textEditable && (
              <button
                type="button"
                className="primary"
                disabled={
                  busy ||
                  !dirty ||
                  (!importedLive && !draft.body.trim()) ||
                  count > MAX_CHARS ||
                  placeholdersLeft.length > 0
                }
                onClick={save}
              >
                {t.posts.save}
              </button>
            )}
            {selected && canWrite && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const name = window.prompt(t.posts.saveAsTemplatePrompt)?.trim();
                  if (!name) return;
                  setBusy(true);
                  setMsg({});
                  savePostAsTemplate(wsId, selected.id, { name })
                    .then(() => setMsg({ ok: t.posts.savedAsTemplate }))
                    .catch((err) => setMsg({ error: errorText(err) }))
                    .finally(() => setBusy(false));
                }}
              >
                {t.posts.saveAsTemplate}
              </button>
            )}
            {selected && !dirty && status === 'draft' && requireApproval && (
              <button type="button" disabled={busy} onClick={() => run(() => postAction(wsId, selected.id, 'submit'), t.posts.submitted)}>
                {t.posts.submit}
              </button>
            )}
            {selected && !dirty && status === 'pending_approval' && canApprove && (
              <>
                <button type="button" disabled={busy} onClick={() => run(() => postAction(wsId, selected.id, 'approve'), t.posts.approved)}>
                  {t.posts.approve}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    const comment = window.prompt(t.posts.rejectPrompt)?.trim();
                    if (comment) void run(() => rejectPost(wsId, selected.id, comment), t.posts.rejected);
                  }}
                >
                  {t.posts.reject}
                </button>
              </>
            )}
            {selected && !dirty && status === 'pending_approval' && !canApprove && (
              <span className="muted small">{t.posts.waitingApproval}</span>
            )}
            {selected && !dirty && canSchedule && (
              <>
                <DateTimeInput value={when} onChange={setWhen} label={t.posts.scheduleAt} />
                <BestTimes workspaceId={wsId} timeZone={timeZone} onPick={setWhen} disabled={busy} />
                <button
                  type="button"
                  disabled={busy || !when}
                  onClick={() => {
                    if (unknownOutcome && !window.confirm(t.posts.confirmRetry)) return;
                    void run(() => schedulePost(wsId, selected.id, when), t.posts.scheduled);
                  }}
                >
                  {t.posts.schedule}
                </button>
                <button
                  type="button"
                  className="danger-btn"
                  disabled={busy}
                  onClick={() => {
                    if (unknownOutcome && !window.confirm(t.posts.confirmRetry)) return;
                    if (window.confirm(t.posts.confirmPublishNow(selected.account_name))) {
                      void run(() => postAction(wsId, selected.id, 'publish-now'), t.posts.queued);
                    }
                  }}
                >
                  {t.posts.publishNow}
                </button>
              </>
            )}
            {selected && !dirty && status === 'scheduled' && (
              <>
                <DateTimeInput value={when} onChange={setWhen} label={t.posts.scheduleAt} />
                <BestTimes workspaceId={wsId} timeZone={timeZone} onPick={setWhen} disabled={busy} />
                <button
                  type="button"
                  disabled={busy || !when}
                  onClick={() => run(() => schedulePost(wsId, selected.id, when), t.posts.rescheduled)}
                >
                  {t.posts.reschedule}
                </button>
              </>
            )}
            {selected && status === 'scheduled' && (
              <button type="button" disabled={busy} onClick={() => run(() => postAction(wsId, selected.id, 'unschedule'), t.posts.unscheduled)}>
                {t.posts.unschedule}
              </button>
            )}
            {selected && status && ['draft', 'pending_approval', 'approved', 'failed'].includes(status) && (
              <button
                type="button"
                className="link danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(t.posts.confirmDelete)) {
                    void run(async () => {
                      await deletePost(wsId, selected.id);
                      setSelected(null);
                      setDraft({ account_id: active[0]?.id ?? '', body: '', media: [], link: null });
                    }, t.posts.deleted);
                  }
                }}
              >
                {t.posts.delete}
              </button>
            )}
          </div>
        )}
        {selected?.status === 'published' && <MetricsPanel workspaceId={wsId} postId={selected.id} canWrite={canWrite} />}
        {selected && (
          <p className="muted small">
            {t.posts.versions(selected.version_count)}{' '}
            <button type="button" className="link" onClick={() => setShowHistory(true)}>
              {t.posts.history}
            </button>{' '}
            · {t.posts.timeZoneNote(timeZone)}
          </p>
        )}
        {msg.error && <p className="error" role="alert">{msg.error}</p>}
        {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
      </section>
      {showHistory && selected && (
        <HistoryPanel
          workspaceId={wsId}
          post={selected}
          timeZone={timeZone}
          canRestore={canRestoreVersion && canWrite}
          restoreHint={restoreHint}
          onRestored={(p) => {
            setSelected(p);
            setDraft({ account_id: p.account_id, body: p.body, media: p.media, link: p.link });
            setMsg({ ok: t.posts.restored });
            load();
          }}
          onClose={() => setShowHistory(false)}
        />
      )}
      {showTemplates && (
        <TemplatePicker
          workspaceId={wsId}
          onPick={({ body, link, campaign_id, leftovers }) => {
            setDraft((d) => ({ ...d, body, link, media: [] }));
            setNewCampaignId(campaign_id ?? '');
            setPlaceholdersLeft(leftovers);
            setMsg(leftovers.length ? { error: t.posts.placeholdersRemain(leftovers) } : { ok: t.posts.fromTemplateOk });
          }}
          onClose={() => setShowTemplates(false)}
        />
      )}
    </div>
  );
}
