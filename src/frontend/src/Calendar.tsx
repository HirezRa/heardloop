import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, getCalendar, schedulePost, type CalendarItem, type Me } from './api';
import { LOCALE, t } from './i18n';
import { formatDay } from './dates';
import { displaySnippet } from './postText';

type View = 'month' | 'week';

// Plain calendar dates (no time zone): all math is done on Y-M-D, the server applies the workspace zone.
function iso(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function parse(value: string): Date {
  const [y, m, d] = value.split('-').map(Number);
  return new Date(y, m - 1, d);
}
function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}
function todayIn(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(
      new Date(),
    );
  } catch {
    return iso(new Date());
  }
}

// Read LOCALE when called, not at import: the language is set after the modules load.
const weekStart = () => (LOCALE === 'he' ? 0 : 1); // Sunday in Israel, Monday elsewhere

function startOfWeek(d: Date): Date {
  return addDays(d, -((d.getDay() - weekStart() + 7) % 7));
}

function visibleRange(anchor: Date, view: View): Date[] {
  if (view === 'week') {
    const s = startOfWeek(anchor);
    return Array.from({ length: 7 }, (_, i) => addDays(s, i));
  }
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const s = startOfWeek(first);
  const days: Date[] = [];
  for (let d = s; d <= last || days.length % 7 !== 0; d = addDays(d, 1)) days.push(d);
  return days;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

export default function Calendar({ me, onOpenPost }: { me: Me; onOpenPost: (postId: string) => void }) {
  const wsId = me.memberships[0]?.workspace_id ?? '';
  const [view, setView] = useState<View>('month');
  const [timeZone, setTimeZone] = useState('UTC');
  const [anchor, setAnchor] = useState(() => new Date());
  const [items, setItems] = useState<CalendarItem[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [tzReady, setTzReady] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [overDay, setOverDay] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const days = useMemo(() => visibleRange(anchor, view), [anchor, view]);
  const start = iso(days[0]);
  const end = iso(days[days.length - 1]);
  const today = todayIn(timeZone);

  // `cancelled` drops a slow answer for a range the user already navigated away from.
  const fetchRange = useCallback(
    (isCancelled: () => boolean = () => false) => {
      if (!wsId) return;
      getCalendar(wsId, start, end)
        .then((res) => {
          if (isCancelled()) return;
          setItems(res.items);
          setTruncated(res.truncated);
          setTimeZone(res.time_zone);
        })
        .catch((e) => {
          if (!isCancelled()) setMsg({ error: errorText(e) });
        });
    },
    [wsId, start, end],
  );

  useEffect(() => {
    let cancelled = false;
    fetchRange(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [fetchRange]);

  // Start on "today" in the workspace time zone (it can differ from the browser's date).
  useEffect(() => {
    if (tzReady || timeZone === 'UTC') return;
    setTzReady(true);
    setAnchor(parse(todayIn(timeZone)));
  }, [timeZone, tzReady]);

  const load = () => fetchRange();

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>();
    for (const item of items) {
      const key = item.local_time.slice(0, 10);
      map.set(key, [...(map.get(key) ?? []), item]);
    }
    return map;
  }, [items]);

  if (!wsId) return <p className="muted">{t.settings.noWorkspace}</p>;

  const locale = LOCALE === 'he' ? 'he-IL' : 'en-GB';
  const title =
    view === 'month'
      ? new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(anchor)
      : `${formatDay(iso(days[0]))} - ${formatDay(iso(days[6]))}`;
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short' });

  function move(step: number) {
    setAnchor((a) =>
      view === 'month' ? new Date(a.getFullYear(), a.getMonth() + step, 1) : addDays(a, 7 * step),
    );
  }

  async function drop(day: string) {
    setOverDay(null);
    const item = items.find((i) => i.post_id === dragging);
    setDragging(null);
    if (!item || !item.can_reschedule || item.local_time.startsWith(day) || day < today) return;
    const target = `${day}T${item.local_time.slice(11, 16)}`;
    setBusy(true);
    setMsg({});
    try {
      await schedulePost(wsId, item.post_id, target);
      setMsg({ ok: t.calendar.moved });
      load();
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="calendar">
      <div className="cal-toolbar">
        <div className="cal-nav">
          <button type="button" onClick={() => move(-1)} aria-label={t.calendar.previous}>
            {LOCALE === 'he' ? '›' : '‹'}
          </button>
          <button type="button" onClick={() => setAnchor(parse(today))}>
            {t.calendar.today}
          </button>
          <button type="button" onClick={() => move(1)} aria-label={t.calendar.next}>
            {LOCALE === 'he' ? '‹' : '›'}
          </button>
          <h2>{title}</h2>
        </div>
        <div className="segmented" role="group" aria-label={t.calendar.view}>
          {(['month', 'week'] as View[]).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>
              {t.calendar.views[v]}
            </button>
          ))}
        </div>
      </div>
      <p className="muted small">{t.calendar.hint(timeZone)}</p>

      <div className={`cal-grid cal-${view}`} aria-busy={busy}>
        {days.slice(0, 7).map((d) => (
          <div key={`h${d.getDay()}`} className="cal-head">
            {weekday.format(d)}
          </div>
        ))}
        {days.map((d) => {
          const key = iso(d);
          const outside = view === 'month' && d.getMonth() !== anchor.getMonth();
          const classes = ['cal-day', outside && 'outside', key === today && 'today', overDay === key && 'drop']
            .filter(Boolean)
            .join(' ');
          return (
            <div
              key={key}
              className={classes}
              onDragOver={(e) => {
                if (!dragging || key < today) return; // past days do not accept drops
                e.preventDefault();
                setOverDay(key);
              }}
              onDragLeave={() => setOverDay((cur) => (cur === key ? null : cur))}
              onDrop={(e) => {
                e.preventDefault();
                void drop(key);
              }}
            >
              <div className="cal-date">{d.getDate()}</div>
              {(byDay.get(key) ?? []).map((item) => (
                <button
                  key={item.post_id}
                  type="button"
                  className={`cal-item status-${item.status}`}
                  style={item.color ? { borderInlineStartColor: item.color } : undefined}
                  draggable={item.can_reschedule}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('text/plain', item.post_id);
                    e.dataTransfer.effectAllowed = 'move';
                    setDragging(item.post_id);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOverDay(null);
                  }}
                  onClick={() => onOpenPost(item.post_id)}
                  title={`${t.posts.statuses[item.status]} · ${item.account_name}${item.campaign_name ? ` · ${item.campaign_name}` : ''}`}
                >
                  <span className="cal-time" dir="ltr">
                    {item.local_time.slice(11, 16)}
                  </span>{' '}
                  {/* Only imported posts can have no text, and the server sends "" for them. */}
                  <span dir="auto" className={item.snippet ? undefined : 'muted'}>
                    {displaySnippet(item.snippet, true, t.calendar.imported).text}
                  </span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
      {items.length === 0 && <p className="muted">{t.calendar.empty}</p>}
      {truncated && <p className="muted small">{t.calendar.truncated}</p>}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
    </div>
  );
}
