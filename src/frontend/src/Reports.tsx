import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ApiError,
  getPost,
  getReport,
  importMetricsExport,
  listAudiences,
  listCampaigns,
  reportCsvUrl,
  type Audience,
  type Campaign,
  type Me,
  type Report,
  type ReportBucket,
  type ReportFilter,
} from './api';
import { weekendLabel } from './AudienceSettings';
import { DateInput } from './DateInputs';
import { formatDate, formatDay } from './dates';
import { DIR, LOCALE, t } from './i18n';
import { displaySnippet } from './postText';
import { CreateFromExport, importErrorText } from './ImportExport';

/** One file of a bulk import. `needs_post`: the post is not in Heardloop; waiting for the user's decision. */
interface FileResult {
  key: number;
  name: string;
  state: 'working' | 'matched' | 'created' | 'skipped' | 'needs_post' | 'failed';
  file: File;
  postId?: string;
  snippet?: string;
  shareId?: string;
  error?: string;
}

const snippetOf = (body: string) => {
  const line = body.split(/\s+/).join(' ').trim();
  return line.length > 80 ? `${line.slice(0, 79)}…` : line;
};

/** A report post's text; the server sends "" for an imported post without text, so show the placeholder. */
function SnippetText({ snippet }: { snippet: string }) {
  const shown = displaySnippet(snippet, true);
  return (
    <span dir="auto" className={shown.placeholder ? 'muted' : undefined}>
      {shown.text}
    </span>
  );
}

function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const PRESETS: { id: string; days: number }[] = [
  { id: '30', days: 30 },
  { id: '90', days: 90 },
  { id: '180', days: 180 },
  { id: '365', days: 365 },
];

/** Measurement age the posts are compared at; 0 = each post's latest measurement. */
const WINDOWS = [7, 14, 30, 0];

/** Horizontal bars, pure SVG/CSS; `value` picks the measure. */
function Bars({
  buckets,
  value,
  label,
  format = (v) => v.toLocaleString(),
}: {
  buckets: ReportBucket[];
  value: (b: ReportBucket) => number;
  label: (b: ReportBucket) => string;
  format?: (v: number) => string;
}) {
  const max = Math.max(1e-9, ...buckets.map(value));
  return (
    <div className="bars">
      {buckets.map((b) => {
        const v = value(b);
        return (
          <div
            key={b.key}
            className={b.weekend ? 'bar-row weekend' : 'bar-row'}
            title={`${label(b)}: ${format(v)} · ${t.reports.postsCount(b.posts)}${b.weekend ? ` · ${t.reports.weekend}` : ''}`}
          >
            <span className="bar-label">{label(b)}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(v / max) * 100}%` }} />
            </span>
            <span className="bar-value" dir="ltr">
              {format(v)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const TREND_HEIGHT = 190;
const TREND_PAD = { top: 18, bottom: 28, side: 46 };

/** A round upper bound for an axis (1, 2, 5 x 10^n; at least 2) so the gridline values read cleanly. */
function niceCeil(v: number): number {
  if (v <= 2) return 2;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

/**
 * Week-by-week impressions and engagement, inline SVG drawn at its real pixel size so the 0.75rem labels stay
 * 0.75rem. Three gridlines (0, half, top); each series has its own scale: impressions on the right in
 * right-to-left layouts and on the left otherwise, engagement on the other side. One week is shown as a
 * centred pair of dots with their values; two or more weeks are joined by lines.
 */
function Trend({ buckets }: { buckets: ReportBucket[] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const empty = buckets.length === 0;
  const [labelPx, setLabelPx] = useState(13);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const update = () => {
      setWidth(Math.max(280, Math.round(el.clientWidth)));
      const rootFs = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      setLabelPx(rootFs * 0.8125);
      el.style.setProperty('--chart-fs', `${rootFs * 0.8125}px`);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [empty]);
  if (empty) return <p className="muted small">{t.reports.noData}</p>;

  const h = TREND_HEIGHT;
  const left = TREND_PAD.side;
  const right = width - TREND_PAD.side;
  const base = h - TREND_PAD.bottom;
  const top = TREND_PAD.top;
  const compact = new Intl.NumberFormat(LOCALE === 'he' ? 'he-IL' : 'en-GB', { notation: 'compact', maximumFractionDigits: 1 });
  const maxImp = niceCeil(Math.max(...buckets.map((b) => b.impressions)));
  const maxEng = niceCeil(Math.max(...buckets.map((b) => b.engagement)));
  const single = buckets.length === 1;
  const x = (i: number) => (single ? (left + right) / 2 : left + (i * (right - left)) / (buckets.length - 1));
  const y = (v: number, max: number) => base - (v / max) * (base - top);
  const path = (pick: (b: ReportBucket) => number, max: number) =>
    buckets.map((b, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(pick(b), max).toFixed(1)}`).join(' ');
  const impOnRight = DIR === 'rtl';
  const steps = [0, 0.5, 1];
  const scale = (max: number, onRight: boolean) =>
    steps.map((s) => (
      <text
        key={s}
        x={onRight ? right + 6 : left - 6}
        y={y(s * max, max)}
        dy="0.32em"
        textAnchor={onRight ? 'start' : 'end'}
        className="scale"
        fontSize={labelPx}
      >
        {compact.format(s * max)}
      </text>
    ));
  // With a single week the two dots would sit on top of each other (each series is scaled to its own top).
  const dotX = (i: number, offset: number) => x(i) + (single ? offset : 0);
  const every = Math.ceil(buckets.length / 12);
  return (
    <div className="trend" ref={wrap}>
      <svg width={width} height={h} viewBox={`0 0 ${width} ${h}`} role="img" aria-label={t.reports.trend} className="trend-svg">
        {steps.map((s) => (
          <line key={s} x1={left} y1={y(s * maxImp, maxImp)} x2={right} y2={y(s * maxImp, maxImp)} className={s === 0 ? 'axis' : 'grid'} />
        ))}
        {scale(maxImp, impOnRight)}
        {scale(maxEng, !impOnRight)}
        {!single && <path d={path((b) => b.impressions, maxImp)} className="line-imp" />}
        {!single && <path d={path((b) => b.engagement, maxEng)} className="line-eng" />}
        {buckets.map((b, i) => (
          <g key={b.key}>
            <circle cx={dotX(i, -9)} cy={y(b.impressions, maxImp)} r={3.5} className="dot-imp">
              <title>{`${b.key}: ${b.impressions.toLocaleString()} ${t.reports.impressions}`}</title>
            </circle>
            <circle cx={dotX(i, 9)} cy={y(b.engagement, maxEng)} r={3.5} className="dot-eng">
              <title>{`${b.key}: ${b.engagement.toLocaleString()} ${t.reports.engagement}`}</title>
            </circle>
            {single && (
              <>
                <text x={dotX(i, -9) - 8} y={y(b.impressions, maxImp)} dy="0.32em" textAnchor="end" className="value" fontSize={labelPx}>
                  {b.impressions.toLocaleString()}
                </text>
                <text x={dotX(i, 9) + 8} y={y(b.engagement, maxEng)} dy="0.32em" textAnchor="start" className="value" fontSize={labelPx}>
                  {b.engagement.toLocaleString()}
                </text>
              </>
            )}
            {(buckets.length <= 12 || i % every === 0) && (
              <text x={x(i)} y={h - 8} textAnchor="middle" className="tick" fontSize={labelPx}>
                {b.key.slice(5)}
              </text>
            )}
          </g>
        ))}
      </svg>
      <div className="legend small">
        <span className="swatch-line imp" /> {t.reports.impressions}
        <span className="swatch-line eng" /> {t.reports.engagement}
      </div>
    </div>
  );
}

export default function Reports({ me, onOpenPost }: { me: Me; onOpenPost: (postId: string) => void }) {
  const wsId = me.memberships[0]?.workspace_id ?? '';
  const [preset, setPreset] = useState('90');
  const [start, setStart] = useState<string | null>(null);
  const [end, setEnd] = useState<string | null>(null);
  const [campaignId, setCampaignId] = useState('');
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [audienceId, setAudienceId] = useState('');
  const [audiences, setAudiences] = useState<Audience[]>([]);
  const [windowDays, setWindowDays] = useState(7);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const role = me.memberships[0]?.role;
  const canWrite = role === 'owner' || role === 'editor' || role === 'approver';
  const [importOpen, setImportOpen] = useState(false);
  const [results, setResults] = useState<FileResult[]>([]);
  const [importing, setImporting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const nextKey = useRef(1);

  const patch = (key: number, change: Partial<FileResult>) =>
    setResults((all) => all.map((r) => (r.key === key ? { ...r, ...change } : r)));

  /** Sends one file at a time (the server matches and stores each export on its own). */
  async function importOne(item: FileResult, create?: { accountId: string; text: string }) {
    patch(item.key, { state: 'working', error: undefined });
    try {
      const res = await importMetricsExport(wsId, item.file, {
        createMissing: Boolean(create),
        accountId: create?.accountId,
        text: create?.text,
      });
      patch(item.key, { state: res.created ? 'created' : 'matched', postId: res.post_id });
      getPost(wsId, res.post_id)
        .then((p) => patch(item.key, { snippet: snippetOf(p.body) }))
        .catch(() => undefined);
    } catch (err) {
      if (err instanceof ApiError && err.detail === 'already_imported') patch(item.key, { state: 'skipped' });
      else if (err instanceof ApiError && err.detail === 'post_not_found' && !create)
        patch(item.key, { state: 'needs_post', shareId: String(err.info?.share_id ?? '') });
      else patch(item.key, { state: 'failed', error: importErrorText(err) });
    }
  }

  async function importFiles(files: File[]) {
    if (files.length === 0) return;
    const items: FileResult[] = files.map((file) => ({ key: nextKey.current++, name: file.name, state: 'working', file }));
    setResults((all) => [...items, ...all]);
    setImporting(true);
    for (const item of items) await importOne(item);
    setImporting(false);
    setReloadKey((k) => k + 1);
  }

  async function createMissing(accountId: string, text: string) {
    setImporting(true);
    for (const item of results.filter((r) => r.state === 'needs_post')) await importOne(item, { accountId, text });
    setImporting(false);
    setReloadKey((k) => k + 1);
  }

  const pickFiles = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => f.name.toLowerCase().endsWith('.xlsx'));
    void importFiles(files);
    if (picker.current) picker.current.value = '';
  };

  const range = useMemo(() => {
    if (preset === 'custom') return { start, end };
    const days = PRESETS.find((p) => p.id === preset)?.days ?? 90;
    const to = new Date();
    const from = new Date(to.getFullYear(), to.getMonth(), to.getDate() - (days - 1));
    return { start: isoDay(from), end: isoDay(to) };
  }, [preset, start, end]);

  useEffect(() => {
    if (!wsId) return;
    listCampaigns(wsId, true).then(setCampaigns).catch(() => setCampaigns([]));
    listAudiences(wsId).then(setAudiences).catch(() => setAudiences([]));
  }, [wsId]);

  const filter: ReportFilter = useMemo(
    () => ({ start: range.start, end: range.end, campaignId: campaignId || undefined, audienceId: audienceId || undefined, windowDays }),
    [range.start, range.end, campaignId, audienceId, windowDays],
  );

  useEffect(() => {
    if (!wsId || !range.start || !range.end) return;
    let cancelled = false;
    getReport(wsId, filter)
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setError('');
        }
      })
      .catch(() => !cancelled && setError(t.errors.generic));
    return () => {
      cancelled = true;
    };
  }, [wsId, filter, range.start, range.end, reloadKey]);

  if (!wsId) return <p className="muted">{t.settings.noWorkspace}</p>;

  const weekday = new Intl.DateTimeFormat(LOCALE === 'he' ? 'he-IL' : 'en-GB', { weekday: 'short' });
  // Buckets are keyed 0 = Monday; build a date on a known Monday for the label.
  const weekdayLabel = (key: string) => weekday.format(new Date(2024, 0, 1 + Number(key)));
  const pct = (v: number | null) => (v === null ? '-' : `${(v * 100).toFixed(1)}%`);
  const audienceLabel = (b: ReportBucket) => (b.key === 'none' ? t.reports.noAudience : (b.label ?? b.key));

  return (
    <div className="reports">
      <div className="filters">
        <div className="segmented" role="group" aria-label={t.reports.range}>
          {PRESETS.map((p) => (
            <button key={p.id} type="button" aria-pressed={preset === p.id} onClick={() => setPreset(p.id)}>
              {t.reports.lastDays(p.days)}
            </button>
          ))}
          <button type="button" aria-pressed={preset === 'custom'} onClick={() => setPreset('custom')}>
            {t.reports.custom}
          </button>
        </div>
        {preset === 'custom' && (
          <>
            <DateInput value={start} onChange={setStart} label={t.settings.filterFrom} />
            <DateInput value={end} onChange={setEnd} min={start ?? undefined} label={t.settings.filterTo} />
          </>
        )}
        <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)} aria-label={t.posts.campaign}>
          <option value="">{t.reports.allCampaigns}</option>
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        {audiences.length > 0 && (
          <select value={audienceId} onChange={(e) => setAudienceId(e.target.value)} aria-label={t.posts.audience}>
            <option value="">{t.reports.allAudiences}</option>
            {audiences.map((a) => (
              <option key={a.id} value={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        )}
        <label className="window">
          {t.reports.window}
          <select value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
            {WINDOWS.map((w) => (
              <option key={w} value={w}>
                {w === 0 ? t.reports.windowLatest : t.reports.windowDays(w)}
              </option>
            ))}
          </select>
        </label>
        {range.start && range.end && (
          <a className="link" href={reportCsvUrl(wsId, filter)} download>
            {t.reports.exportCsv}
          </a>
        )}
        {canWrite && (
          <button type="button" className="link" aria-expanded={importOpen} onClick={() => setImportOpen((o) => !o)}>
            {t.reports.importButton}
          </button>
        )}
      </div>
      {importOpen && canWrite && (
        <section className="card import-panel">
          <h2>{t.reports.importTitle}</h2>
          <p className="muted small">{t.metrics.importHelp}</p>
          <div
            className={dragging ? 'dropzone dragging' : 'dropzone'}
            role="button"
            tabIndex={0}
            onClick={() => picker.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && picker.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (!importing) pickFiles(e.dataTransfer.files);
            }}
          >
            {t.reports.dropHint}
            <input
              ref={picker}
              type="file"
              multiple
              hidden
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              disabled={importing}
              onChange={(e) => pickFiles(e.target.files)}
            />
          </div>
          {results.some((r) => r.state === 'needs_post') && (
            <CreateFromExport
              workspaceId={wsId}
              busy={importing}
              onConfirm={(accountId, text) => void createMissing(accountId, text)}
              onCancel={() => setResults((all) => all.filter((r) => r.state !== 'needs_post'))}
            />
          )}
          {results.length > 0 && (
            <ul className="import-results" aria-live="polite">
              {results.map((r) => (
                <li key={r.key}>
                  <span className="small" dir="ltr">{r.name}</span>{' '}
                  <span className={r.state === 'failed' ? 'error small' : r.state === 'matched' || r.state === 'created' ? 'ok small' : 'muted small'}>
                    {t.reports.importState[r.state]}
                    {r.error ? `: ${r.error}` : ''}
                  </span>{' '}
                  {r.postId && (
                    <button type="button" className="link small" onClick={() => onOpenPost(r.postId as string)}>
                      <span dir="auto">{r.snippet || t.reports.openPost}</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {error && <p className="error">{error}</p>}
      {report && (
        <>
          <p className="muted small">
            {formatDay(report.start)} - {formatDay(report.end)} ·{' '}
            {report.time_zones.length === 1 ? t.reports.zoneOf(report.time_zones[0]) : report.time_zones.length === 0 ? t.reports.timeZone(report.time_zone) : t.reports.mixedZones}
            {report.weekend_days.length > 0 && ` · ${t.reports.weekendHint(weekendLabel(report.weekend_days))}`}
          </p>
          <p className="notice">
            {report.window_days === 0 ? t.reports.windowLatestHint : t.reports.windowHint(report.window_days)}
            {report.off_window > 0 && ` ${t.reports.offWindow(report.off_window, report.window_days, report.window_tolerance_days)}`}
          </p>
          <div className="metric-cards wide">
            <div className="metric-card">
              <span className="metric-value">{report.totals.posts}</span>
              <span className="muted small">{t.reports.published}</span>
            </div>
            <div className="metric-card">
              <span className="metric-value">{report.totals.measured}</span>
              <span className="muted small">{t.reports.measured}</span>
            </div>
            <div className="metric-card">
              <span className="metric-value" dir="ltr">{report.totals.impressions.toLocaleString()}</span>
              <span className="muted small">{t.reports.impressions}</span>
            </div>
            <div className="metric-card">
              <span className="metric-value" dir="ltr">{report.totals.engagement.toLocaleString()}</span>
              <span className="muted small">{t.reports.engagement}</span>
            </div>
            <div className="metric-card">
              <span className="metric-value" dir="ltr">{pct(report.totals.avg_engagement_rate)}</span>
              <span className="muted small">{t.reports.avgRate}</span>
            </div>
            {report.totals.members_reached > 0 && (
              <div className="metric-card">
                <span className="metric-value" dir="ltr">{report.totals.members_reached.toLocaleString()}</span>
                <span className="muted small">{t.reports.membersReached}</span>
              </div>
            )}
            {report.totals.followers_gained > 0 && (
              <div className="metric-card">
                <span className="metric-value" dir="ltr">{report.totals.followers_gained.toLocaleString()}</span>
                <span className="muted small">{t.reports.followersGained}</span>
              </div>
            )}
          </div>
          {report.totals.posts > 0 && report.totals.measured === 0 && <p className="banner">{t.reports.noMetricsYet}</p>}

          <section className="card">
            <h2>{t.reports.trend}</h2>
            <Trend buckets={report.trend} />
          </section>

          <div className="report-grid">
            <section className="card">
              <h2>{t.reports.topPosts}</h2>
              {report.top_posts.length === 0 && <p className="muted small">{t.reports.noData}</p>}
              <ol className="top-list">
                {report.top_posts.map((p) => (
                  <li key={p.post_id}>
                    <button type="button" className="link" onClick={() => onOpenPost(p.post_id)}>
                      <SnippetText snippet={p.snippet} />
                    </button>
                    <span className="muted small" dir="ltr">
                      {formatDate(p.published_at)}
                      {p.audience_code ? ` · ${p.audience_code}` : ''} · {p.impressions?.toLocaleString() ?? '-'} / {p.engagement} · {pct(p.engagement_rate)}
                      {p.measured_age_days !== null && ` · ${t.reports.measuredAfter(Math.round(p.measured_age_days))}`}
                      {p.off_window ? ' ⚠' : ''}
                    </span>
                  </li>
                ))}
              </ol>
            </section>
            <section className="card">
              <h2>{t.reports.byCampaign}</h2>
              <Bars buckets={report.by_campaign} value={(b) => b.engagement} label={(b) => b.label ?? t.reports.noCampaign} />
            </section>
            {(audiences.length > 0 || report.by_audience.length > 1) && (
              <section className="card">
                <h2>{t.reports.byAudience}</h2>
                <Bars buckets={report.by_audience} value={(b) => b.avg_engagement_rate ?? 0} label={audienceLabel} format={(v) => `${(v * 100).toFixed(1)}%`} />
                <p className="muted small">{t.reports.audienceHint}</p>
              </section>
            )}
            <section className="card">
              <h2>{t.reports.byWeekday}</h2>
              <Bars buckets={report.by_weekday} value={(b) => b.avg_engagement_rate ?? 0} label={(b) => weekdayLabel(b.key)} format={(v) => `${(v * 100).toFixed(1)}%`} />
              <p className="muted small">{t.reports.rateHint}</p>
            </section>
            <section className="card">
              <h2>{t.reports.byHour}</h2>
              <Bars buckets={report.by_hour.filter((b) => b.posts > 0)} value={(b) => b.avg_impressions ?? 0} label={(b) => `${b.key.padStart(2, '0')}:00`} />
              <p className="muted small">{t.reports.impressionsHint}</p>
            </section>
            <section className="card">
              <h2>{t.reports.byMedia}</h2>
              <Bars buckets={report.by_media} value={(b) => b.avg_engagement_rate ?? 0} label={(b) => t.reports.media[b.key as 'none' | 'image' | 'video' | 'link']} format={(v) => `${(v * 100).toFixed(1)}%`} />
              <p className="muted small">{t.reports.rateHint}</p>
            </section>
            <section className="card">
              <h2>{t.reports.unmeasured}</h2>
              {report.unmeasured.length === 0 ? (
                <p className="muted small">{t.reports.allMeasured}</p>
              ) : (
                <ul className="top-list">
                  {report.unmeasured.map((p) => (
                    <li key={p.post_id}>
                      <button type="button" className="link" onClick={() => onOpenPost(p.post_id)}>
                        <SnippetText snippet={p.snippet} />
                      </button>
                      <span className="muted small" dir="ltr">{formatDate(p.published_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
