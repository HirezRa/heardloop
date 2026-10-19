import { useCallback, useEffect, useRef, useState } from 'react';
import {
  addMetrics,
  ApiError,
  deleteMetrics,
  importMetricsExport,
  listMetrics,
  type Demographic,
  type MetricsEntry,
  type MetricsInput,
} from './api';
import { DateInput } from './DateInputs';
import { formatDateTime } from './dates';
import { t } from './i18n';
import { capturedAtOf, CreateFromExport, importErrorText } from './ImportExport';

const FIELDS = ['impressions', 'reactions', 'comments', 'reposts', 'clicks', 'saves', 'followers'] as const;
type Field = (typeof FIELDS)[number];
/** Numbers that only LinkedIn exports carry. */
const EXTRA = ['members_reached', 'profile_viewers', 'followers_gained', 'sends'] as const;

const errorText = importErrorText;

/** Top 3 values per category as small bars, plus the "top" fields of an export. */
function Audience({ entry }: { entry: MetricsEntry }) {
  const groups = new Map<string, Demographic[]>();
  for (const d of entry.demographics ?? []) groups.set(d.category, [...(groups.get(d.category) ?? []), d]);
  const top = entry.top;
  return (
    <div className="audience">
      {top && (top.job_title || top.location || top.industry) && (
        <ul className="muted small audience-top">
          {(['job_title', 'location', 'industry'] as const).map(
            (k) =>
              top[k] && (
                <li key={k}>
                  {t.metrics.topFields[k]}: <span dir="auto">{top[k]}</span>
                </li>
              ),
          )}
        </ul>
      )}
      {[...groups.entries()].map(([category, rows]) => (
        <div key={category}>
          <strong className="small" dir="auto">{category}</strong>
          <div className="bars">
            {[...rows]
              .sort((a, b) => b.share - a.share)
              .slice(0, 3)
              .map((d) => (
                <div key={d.value} className="bar-row" title={`${d.value}: ${(d.share * 100).toFixed(1)}%`}>
                  <span className="bar-label" dir="auto">{d.value}</span>
                  <span className="bar-track">
                    <span className="bar-fill" style={{ width: `${Math.min(100, d.share * 100)}%` }} />
                  </span>
                  <span className="bar-value" dir="ltr">{(d.share * 100).toFixed(0)}%</span>
                </div>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const hasAudience = (e: MetricsEntry) => Boolean(e.demographics?.length || e.top);

/**
 * Metrics of a published post, read by hand from LinkedIn and entered here as dated measurements.
 * The newest measurement feeds the reports; older ones show how the post grew.
 */
export default function MetricsPanel({ workspaceId, postId, canWrite }: { workspaceId: string; postId: string; canWrite: boolean }) {
  const [entries, setEntries] = useState<MetricsEntry[]>([]);
  const [form, setForm] = useState<Record<Field, string>>({ impressions: '', reactions: '', comments: '', reposts: '', clicks: '', saves: '', followers: '' });
  const [note, setNote] = useState('');
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [capturedOn, setCapturedOn] = useState<string | null>(null);
  // An export whose post is not in Heardloop: kept until the user decides whether to create it.
  const [pending, setPending] = useState<{ file: File; shareId: string } | null>(null);
  const [chosenFile, setChosenFile] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    listMetrics(workspaceId, postId).then(setEntries).catch(() => setEntries([]));
  }, [workspaceId, postId]);
  useEffect(load, [load]);

  const latest = entries[0];

  async function save() {
    const body: MetricsInput = {};
    for (const f of FIELDS) {
      const raw = form[f].replace(/[,\s]/g, '');
      if (!raw) continue;
      if (!/^\d+$/.test(raw)) return setMsg({ error: t.metrics.numbersOnly });
      body[f] = Number(raw);
    }
    if (Object.keys(body).length === 0) return setMsg({ error: t.metrics.nothingToSave });
    if (note.trim()) body.note = note.trim();
    setBusy(true);
    setMsg({});
    try {
      await addMetrics(workspaceId, postId, body);
      setForm({ impressions: '', reactions: '', comments: '', reposts: '', clicks: '', saves: '', followers: '' });
      setNote('');
      setOpen(false);
      setMsg({ ok: t.metrics.saved });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(entry: MetricsEntry) {
    if (!window.confirm(t.metrics.confirmDelete)) return;
    try {
      await deleteMetrics(workspaceId, postId, entry.id);
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    }
  }

  async function runImport(file: File, create?: { accountId: string; text: string }) {
    setBusy(true);
    setMsg({});
    try {
      const res = await importMetricsExport(workspaceId, file, {
        capturedAt: capturedAtOf(capturedOn),
        createMissing: Boolean(create),
        accountId: create?.accountId,
        text: create?.text,
      });
      setPending(null);
      setImportOpen(false);
      setCapturedOn(null);
      setMsg({ ok: res.created ? t.metrics.importedNew : res.post_id === postId ? t.metrics.imported : t.metrics.importedOther });
      load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && err.detail === 'post_not_found' && !create) {
        setPending({ file, shareId: String(err.info?.share_id ?? '') });
      } else {
        setPending(null);
        setMsg({ error: errorText(err) });
      }
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  const engagement = latest ? (latest.reactions ?? 0) + (latest.comments ?? 0) + (latest.reposts ?? 0) + (latest.saves ?? 0) : null;
  const rate = latest?.impressions && engagement !== null ? ((engagement / latest.impressions) * 100).toFixed(1) : null;

  return (
    <div className="metrics">
      <div className="row-between">
        <h3>{t.metrics.title}</h3>
        {canWrite && (
          <span className="actions">
            <button type="button" className="link" onClick={() => setOpen((o) => !o)}>
              {open ? t.posts.close : t.metrics.add}
            </button>
            <button type="button" className="link" onClick={() => setImportOpen((o) => !o)}>
              {t.metrics.importButton}
            </button>
          </span>
        )}
      </div>
      {importOpen && canWrite && (
        <div className="metric-form">
          <p className="muted small">{t.metrics.importHelp}</p>
          <div className="actions">
            <label className="check">
              {t.metrics.capturedOn} <DateInput value={capturedOn} onChange={setCapturedOn} label={t.metrics.capturedOn} />
            </label>
            <label className="file-btn">
              {t.metrics.chooseFile}
              <input
                ref={fileInput}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                disabled={busy || pending !== null}
                aria-label={t.metrics.chooseFile}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  setChosenFile(file?.name ?? '');
                  if (file) void runImport(file);
                }}
              />
            </label>
            {chosenFile && <span className="file-name">{chosenFile}</span>}
          </div>
          {pending && (
            <>
              <p className="banner">{t.metrics.notFound}</p>
              <CreateFromExport
                workspaceId={workspaceId}
                shareId={pending.shareId}
                busy={busy}
                onConfirm={(accountId, text) => void runImport(pending.file, { accountId, text })}
                onCancel={() => {
                  setPending(null);
                  if (fileInput.current) fileInput.current.value = '';
                }}
              />
            </>
          )}
        </div>
      )}
      {latest ? (
        <div className="metric-cards">
          {FIELDS.filter((f) => latest[f] !== null).map((f) => (
            <div key={f} className="metric-card">
              <span className="metric-value" dir="ltr">{latest[f]?.toLocaleString()}</span>
              <span className="muted small">{t.metrics.fields[f]}</span>
            </div>
          ))}
          {EXTRA.filter((f) => latest[f] !== null).map((f) => (
            <div key={f} className="metric-card">
              <span className="metric-value" dir="ltr">{latest[f]?.toLocaleString()}</span>
              <span className="muted small">{t.metrics.extra[f]}</span>
            </div>
          ))}
          {rate && (
            <div className="metric-card">
              <span className="metric-value" dir="ltr">{rate}%</span>
              <span className="muted small">{t.metrics.engagementRate}</span>
            </div>
          )}
        </div>
      ) : (
        <p className="muted small">{t.metrics.none}</p>
      )}
      {latest && <p className="muted small">{t.metrics.measuredAt(formatDateTime(latest.captured_at), latest.recorded_by_name ?? '')}</p>}

      {open && (
        <div className="metric-form">
          <p className="muted small">{t.metrics.hint}</p>
          <div className="metric-inputs">
            {FIELDS.map((f) => (
              <label key={f}>
                {t.metrics.fields[f]}
                <input
                  dir="ltr"
                  inputMode="numeric"
                  placeholder={latest?.[f] !== null && latest?.[f] !== undefined ? String(latest[f]) : '-'}
                  value={form[f]}
                  onChange={(e) => setForm({ ...form, [f]: e.target.value })}
                />
              </label>
            ))}
            <label className="wide">
              {t.metrics.note}
              <input dir="auto" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t.metrics.notePlaceholder} />
            </label>
          </div>
          <div className="actions">
            <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
              {t.metrics.save}
            </button>
          </div>
        </div>
      )}

      {latest && hasAudience(latest) && (
        <details className="metric-audience">
          <summary className="small">{t.metrics.audience}</summary>
          <Audience entry={latest} />
        </details>
      )}

      {(entries.length > 1 || entries.some((e) => e.source !== 'manual')) && (
        <details className="metric-history">
          <summary className="small">{t.metrics.history(entries.length)}</summary>
          <table className="table small">
            <thead>
              <tr>
                <th>{t.metrics.when}</th>
                <th>{t.metrics.source}</th>
                {FIELDS.map((f) => (
                  <th key={f}>{t.metrics.short[f]}</th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {entries.flatMap((e) => [
                <tr key={e.id}>
                  <td dir="ltr">{formatDateTime(e.captured_at)}</td>
                  <td>{t.metrics.sources[e.source as keyof typeof t.metrics.sources] ?? e.source}</td>
                  {FIELDS.map((f) => (
                    <td key={f} dir="ltr">{e[f] ?? ''}</td>
                  ))}
                  <td>
                    {canWrite && (
                      <button type="button" className="link danger" onClick={() => void remove(e)}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>,
                hasAudience(e) && (
                  <tr key={`${e.id}-audience`}>
                    <td colSpan={FIELDS.length + 3}>
                      <details>
                        <summary>{t.metrics.audience}</summary>
                        <Audience entry={e} />
                      </details>
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        </details>
      )}
      {msg.error && <p className="error small" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok small" role="status">{msg.ok}</p>}
    </div>
  );
}
