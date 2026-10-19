/** Copy for Settings → About update states. Never claims "latest" after a failed check. */

export type UpdateStatus =
  | { status: 'disabled' }
  | { status: 'never' }
  | { status: 'current'; checked_at?: string }
  | {
      status: 'newer';
      latest: string;
      url?: string;
      notes?: string;
      published_at?: string | null;
      checked_at?: string;
    }
  | {
      status: 'error';
      error: string;
      checked_at?: string;
      last_ok_at?: string | null;
      last_ok_latest?: string | null;
      update?: { available: boolean; latest: string; url: string; notes?: string };
    };

export type AboutUpdateCopy = {
  upToDate: (version: string) => string;
  available: (latest: string) => string;
  neverChecked: string;
  updatesDisabled: string;
  checkError: (reason: string, lastOk: string | null) => string;
  errorReasons: Record<string, string>;
};

function formatWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function errorReasonText(code: string, reasons: Record<string, string>): string {
  return reasons[code] || reasons.network || code;
}

/** Plain-language About line for the current update_status (no "latest" on error). */
export function aboutUpdateLines(
  status: UpdateStatus | null | undefined,
  runningVersion: string,
  copy: AboutUpdateCopy,
): { kind: 'ok' | 'newer' | 'error' | 'muted'; text: string } {
  if (!status || status.status === 'never') {
    return { kind: 'muted', text: copy.neverChecked };
  }
  if (status.status === 'disabled') {
    return { kind: 'muted', text: copy.updatesDisabled };
  }
  if (status.status === 'newer') {
    return { kind: 'newer', text: copy.available(status.latest) };
  }
  if (status.status === 'current') {
    return { kind: 'ok', text: copy.upToDate(runningVersion) };
  }
  // error
  const reason = errorReasonText(status.error, copy.errorReasons);
  const when = formatWhen(status.last_ok_at);
  const lastOk =
    status.last_ok_latest && when
      ? `v${status.last_ok_latest} (${when})`
      : status.last_ok_latest
        ? `v${status.last_ok_latest}`
        : null;
  return { kind: 'error', text: copy.checkError(reason, lastOk) };
}

/** Guard: error copy must never claim the running build is the newest. */
export function errorCopyLooksHonest(text: string): boolean {
  return !text.toLowerCase().includes('latest version');
}
