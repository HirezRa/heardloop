import { useEffect, useState } from 'react';
import { getBestTimes, type BestTimes as BestTimesData, type BestTimeSlot } from './api';
import { LOCALE, t } from './i18n';

/** Today's date and weekday (0 = Monday) and the current hour, as the workspace time zone sees them. */
function nowIn(timeZone: string): { y: number; m: number; d: number; weekday: number; hour: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
      weekday: 'short',
    })
      .formatToParts(new Date())
      .map((p) => [p.type, p.value]),
  );
  const weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(parts.weekday);
  return { y: Number(parts.year), m: Number(parts.month), d: Number(parts.day), weekday, hour: Number(parts.hour) };
}

/** Next occurrence of the slot as YYYY-MM-DDTHH:00 in the workspace time zone (at least one hour ahead). */
export function nextSlotValue(slot: Pick<BestTimeSlot, 'weekday' | 'hour'>, timeZone: string): string {
  const now = nowIn(timeZone);
  let days = (slot.weekday - now.weekday + 7) % 7;
  if (days === 0 && slot.hour <= now.hour) days = 7;
  // Calendar arithmetic on the date only (UTC avoids DST surprises; the server applies the zone).
  const date = new Date(Date.UTC(now.y, now.m - 1, now.d + days));
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(slot.hour)}:00`;
}

function weekdayName(weekday: number): string {
  // 2024-01-01 was a Monday.
  return new Intl.DateTimeFormat(LOCALE, { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(Date.UTC(2024, 0, 1 + weekday)),
  );
}

/** Shifts a wall-clock value from one zone to another, keeping the YYYY-MM-DDTHH:mm shape of the picker. */
export function shiftZone(localValue: string, fromZone: string, toZone: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(localValue);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  // Interpret the wall clock in `fromZone` by correcting a UTC guess with the zone's offset at that moment.
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const offsetAt = (ms: number, zone: string) => {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
        .formatToParts(new Date(ms))
        .map((p) => [p.type, p.value]),
    );
    return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute)) - ms;
  };
  const instant = guess - offsetAt(guess, fromZone);
  const target = instant + offsetAt(instant, toZone);
  const date = new Date(target);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

/**
 * Best weekday + hour slots from the workspace's own metrics for one audience (posts without an audience when
 * none is given), in that audience's time zone; a click puts the next such time in the picker, converted to
 * the workspace zone the picker uses.
 */
export default function BestTimes({
  workspaceId,
  timeZone,
  audienceId,
  audienceCode,
  onPick,
  disabled,
}: {
  workspaceId: string;
  timeZone: string;
  audienceId?: string | null;
  audienceCode?: string | null;
  onPick: (localValue: string) => void;
  disabled?: boolean;
}) {
  const [data, setData] = useState<BestTimesData | null>(null);

  useEffect(() => {
    getBestTimes(workspaceId, audienceId)
      .then(setData)
      .catch(() => setData(null));
  }, [workspaceId, audienceId]);

  if (!data) return null;
  if (data.slots.length === 0) {
    return <span className="muted small">{t.bestTimes.notEnough}</span>;
  }
  const scope = audienceId ? t.bestTimes.forAudience(audienceCode ?? '') : t.bestTimes.withoutAudience;
  return (
    <span className="best-times">
      <span className="muted small" title={`${t.bestTimes.hint(data.days)} ${scope} (${data.time_zone}).`}>
        {t.bestTimes.title}
      </span>
      {data.slots.map((s) => (
        <span key={`${s.weekday}-${s.hour}`} className="chip">
          <button
            type="button"
            disabled={disabled}
            title={t.bestTimes.slotHint(s.posts, Math.round(s.avg_engagement_rate * 1000) / 10)}
            onClick={() => {
              // The slot is in the audience's zone; the picker works in the workspace zone.
              const inAudienceZone = nextSlotValue(s, data.time_zone);
              onPick(shiftZone(inAudienceZone, data.time_zone, timeZone) ?? inAudienceZone);
            }}
          >
            <bdi>
              {weekdayName(s.weekday)} {String(s.hour).padStart(2, '0')}:00
            </bdi>
          </button>
        </span>
      ))}
    </span>
  );
}
