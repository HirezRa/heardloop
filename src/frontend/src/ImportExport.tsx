import { useEffect, useState } from 'react';
import { ApiError, listAccounts, type LinkedInAccount } from './api';
import { t } from './i18n';

/** Server error code -> text; unknown codes fall back to the generic message. */
export function importErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, string>)[err.detail];
    if (known) return known;
    if (err.status === 422) return t.errors.invalid;
  }
  return t.errors.generic;
}

/** "Captured on" day (yyyy-mm-dd) -> ISO instant at local noon, never later than now. Undefined = let the server decide. */
export function capturedAtOf(day: string | null): string | undefined {
  if (!day) return undefined;
  const noon = new Date(`${day}T12:00:00`);
  if (Number.isNaN(noon.getTime())) return undefined;
  return (noon > new Date() ? new Date() : noon).toISOString();
}

/** Account selector plus optional pasted text, for an export whose post is not in Heardloop yet. */
export function CreateFromExport({
  workspaceId,
  shareId,
  busy,
  onConfirm,
  onCancel,
}: {
  workspaceId: string;
  shareId?: string;
  busy: boolean;
  onConfirm: (accountId: string, text: string) => void;
  onCancel: () => void;
}) {
  const [accounts, setAccounts] = useState<LinkedInAccount[]>([]);
  const [accountId, setAccountId] = useState('');
  const [text, setText] = useState('');

  useEffect(() => {
    listAccounts(workspaceId)
      .then((all) => {
        const active = all.filter((a) => a.status === 'active');
        setAccounts(active);
        setAccountId((current) => current || active[0]?.id || '');
      })
      .catch(() => setAccounts([]));
  }, [workspaceId]);

  return (
    <div className="metric-form" role="group" aria-label={t.exportImport.createTitle}>
      <p>{t.exportImport.createPrompt}</p>
      {shareId && (
        <p className="muted small">
          {t.exportImport.shareId} <code dir="ltr">{shareId}</code>
        </p>
      )}
      <label className="stack small">
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
      <label className="stack small">
        {t.exportImport.text}
        <textarea dir="auto" rows={4} maxLength={3000} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <p className="muted small">{t.exportImport.textHint}</p>
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !accountId} onClick={() => onConfirm(accountId, text)}>
          {t.exportImport.create}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          {t.exportImport.cancel}
        </button>
      </div>
    </div>
  );
}
