import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  importMetricsExport,
  listAccounts,
  listAudiences,
  listCampaigns,
  updatePost,
  type Audience,
  type Campaign,
  type LinkedInAccount,
} from './api';
import { t } from './i18n';
import { importErrorText } from './ImportExport';

type LinkKind = 'activity' | 'share' | 'ugcPost';

/** Mirrors heardloop/linkedin/post_link.py for instant feedback; the server checks again. Nothing is fetched. */
export function readPostLink(value: string): { kind: LinkKind; id: string } | { error: string } | null {
  const text = value.trim();
  if (!text) return null;
  let url: URL;
  try {
    url = new URL(text.includes('://') ? text : `https://${text}`);
  } catch {
    return { error: 'invalid_post_link' };
  }
  const host = url.hostname.toLowerCase();
  if (host === 'lnkd.in' || host === 'www.lnkd.in') return { error: 'short_link' };
  if (!['linkedin.com', 'www.linkedin.com', 'm.linkedin.com'].includes(host) || !/^https?:$/.test(url.protocol)) {
    return { error: 'not_linkedin' };
  }
  let path = url.pathname;
  try {
    path = decodeURIComponent(path);
  } catch {
    /* keep the raw path */
  }
  const m = /urn:li:(activity|share|ugcPost):(\d{10,25})(?!\d)/.exec(path) ?? /[-_](activity|share|ugcPost)-(\d{10,25})(?=-|\/|$)/.exec(path);
  return m ? { kind: m[1] as LinkKind, id: m[2] } : { error: 'invalid_post_link' };
}

/** LinkedIn names the export `SinglePostAnalytics_<name>_<activity id>.xlsx`. */
export function activityIdOfFile(name: string): string | null {
  const m = /(?:^|_)(\d{15,25})\.xlsx$/i.exec(name.trim());
  return m ? m[1] : null;
}

/**
 * Bring in a post that was published directly on LinkedIn: its address plus its analytics export. The export
 * is the proof that the post is yours (LinkedIn offers it only to the author); the link and the file must point
 * at the same post. The text cannot be read from LinkedIn, so it is pasted (optional).
 */
export default function LinkImport({
  workspaceId,
  onImported,
  onCancel,
}: {
  workspaceId: string;
  onImported: (postId: string, created: boolean) => void;
  onCancel: () => void;
}) {
  const [link, setLink] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [accounts, setAccounts] = useState<LinkedInAccount[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [audiences, setAudiences] = useState<Audience[]>([]);
  const [accountId, setAccountId] = useState('');
  const [campaignId, setCampaignId] = useState('');
  const [audienceId, setAudienceId] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;
    el.showModal();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    listAccounts(workspaceId)
      .then((all) => {
        const active = all.filter((a) => a.status === 'active');
        setAccounts(active);
        setAccountId((cur) => cur || active[0]?.id || '');
      })
      .catch(() => setAccounts([]));
    listCampaigns(workspaceId).then(setCampaigns).catch(() => setCampaigns([]));
    listAudiences(workspaceId)
      .then((list) => {
        setAudiences(list);
        setAudienceId((cur) => cur || (list.find((a) => a.is_default)?.id ?? ''));
      })
      .catch(() => setAudiences([]));
  }, [workspaceId]);

  const parsed = useMemo(() => readPostLink(link), [link]);
  const fileId = file ? activityIdOfFile(file.name) : null;
  const linkError = parsed && 'error' in parsed ? parsed.error : null;
  const linkOk = parsed && !('error' in parsed) ? parsed : null;
  // Only an activity link is compared with the file name; share/ugcPost links are checked against the file's content.
  const fileWarning =
    file && linkOk?.kind === 'activity'
      ? fileId === null
        ? t.linkImport.fileRenamed
        : fileId !== linkOk.id
          ? t.errors.export_not_for_link
          : null
      : null;
  const canSubmit = !!linkOk && !!file && !!accountId && !busy && !fileWarning;

  async function submit() {
    if (!linkOk || !file) return;
    setBusy(true);
    setError('');
    try {
      const res = await importMetricsExport(workspaceId, file, {
        createMissing: true,
        accountId,
        text,
        postLink: link,
        fileActivityId: fileId ?? undefined,
      });
      if (res.created && (campaignId || audienceId)) {
        await updatePost(workspaceId, res.post_id, {
          ...(campaignId ? { campaign_id: campaignId } : {}),
          ...(audienceId ? { audience_id: audienceId } : {}),
        }).catch(() => undefined); // the post exists; planning fields can be set in the editor
      }
      onImported(res.post_id, res.created);
    } catch (err) {
      setError(err instanceof ApiError && err.detail === 'already_imported' ? t.linkImport.alreadyToday : importErrorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className="modal link-import" aria-label={t.linkImport.title} onClose={onCancel}>
      <h2>{t.linkImport.title}</h2>
      <ol className="small muted link-import-steps">
        <li>{t.linkImport.step1}</li>
        <li>{t.linkImport.step2}</li>
        <li>{t.linkImport.step3}</li>
      </ol>
      <label>
        {t.linkImport.link}
        <input
          dir="ltr"
          type="url"
          value={link}
          placeholder="https://www.linkedin.com/feed/update/urn:li:activity:…"
          maxLength={2048}
          onChange={(e) => setLink(e.target.value)}
        />
        {linkError && <span className="error small">{(t.errors as Record<string, string>)[linkError] ?? t.errors.invalid}</span>}
        {linkOk && (
          <span className="ok small" dir="auto">
            {t.linkImport.linkRead(linkOk.kind, linkOk.id)}
          </span>
        )}
      </label>
      <label>
        {t.linkImport.file}
        <input
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {fileWarning && <span className="error small">{fileWarning}</span>}
      </label>
      <label>
        {t.exportImport.account}
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          {accounts.length === 0 && <option value="">{t.exportImport.noAccounts}</option>}
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name}
            </option>
          ))}
        </select>
      </label>
      <div className="row-fields">
        <label>
          {t.posts.campaign}
          <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            <option value="">{t.posts.noCampaign}</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.posts.audience}
          <select value={audienceId} disabled={audiences.length === 0} onChange={(e) => setAudienceId(e.target.value)}>
            <option value="">{audiences.length === 0 ? t.posts.noAudiencesOption : t.posts.noAudience}</option>
            {audiences.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        {t.exportImport.text}
        <textarea dir="auto" rows={5} maxLength={3000} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <p className="muted small">{t.linkImport.textHint}</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="row gap">
        <button type="button" className="primary" disabled={!canSubmit} onClick={() => void submit()}>
          {busy ? t.linkImport.importing : t.linkImport.import}
        </button>
        <button type="button" className="link" disabled={busy} onClick={onCancel}>
          {t.exportImport.cancel}
        </button>
      </div>
      <p className="muted small">{t.linkImport.privacy}</p>
    </dialog>
  );
}
