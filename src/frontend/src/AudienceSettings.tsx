import { useEffect, useState, type FormEvent } from 'react';
import {
  ApiError,
  createAudience,
  deleteAudience,
  listAudiencePresets,
  listAudiences,
  updateAudience,
  type Audience,
  type AudienceInput,
  type AudiencePreset,
} from './api';
import { LOCALE, t } from './i18n';

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]; // 0 = Monday, as the server counts

function weekdayName(weekday: number, style: 'short' | 'long' = 'short'): string {
  // 2024-01-01 was a Monday.
  return new Intl.DateTimeFormat(LOCALE, { weekday: style, timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1 + weekday)));
}

export function weekendLabel(days: number[]): string {
  return days.map((d) => weekdayName(d)).join(', ');
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const known = (t.errors as Record<string, unknown>)[err.detail];
    if (typeof known === 'string') return known;
  }
  return t.errors.generic;
}

const EMPTY: AudienceInput = { code: '', name: '', time_zone: 'UTC', weekend_days: [5, 6], is_default: false };

/** Code, name, time zone and weekend; used both for a new audience and for editing one in place. */
function AudienceForm({
  initial,
  zones,
  onSubmit,
  onCancel,
  busy,
}: {
  initial: AudienceInput;
  zones: string[];
  onSubmit: (body: AudienceInput) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [form, setForm] = useState<AudienceInput>(initial);
  const [error, setError] = useState('');
  const toggleDay = (d: number) =>
    setForm((f) => ({
      ...f,
      weekend_days: f.weekend_days.includes(d) ? f.weekend_days.filter((x) => x !== d) : [...f.weekend_days, d].sort(),
    }));
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.code.trim() || !form.name.trim()) return setError(t.audiences.codeRequired);
    setError('');
    onSubmit({ ...form, code: form.code.trim().toUpperCase(), name: form.name.trim() });
  }
  return (
    <form className="form audience-form" onSubmit={submit}>
      <div className="row gap wrap">
        <label>
          {t.audiences.code}
          <input dir="ltr" value={form.code} maxLength={16} placeholder="IL" onChange={(e) => setForm({ ...form, code: e.target.value })} />
        </label>
        <label>
          {t.audiences.name}
          <input value={form.name} maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label>
          {t.audiences.timeZone}
          {zones.length ? (
            <select dir="ltr" value={form.time_zone} onChange={(e) => setForm({ ...form, time_zone: e.target.value })}>
              {!zones.includes(form.time_zone) && <option value={form.time_zone}>{form.time_zone}</option>}
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          ) : (
            <input dir="ltr" value={form.time_zone} onChange={(e) => setForm({ ...form, time_zone: e.target.value })} />
          )}
        </label>
      </div>
      <fieldset className="weekend-days">
        <legend>{t.audiences.weekend}</legend>
        {WEEKDAYS.map((d) => (
          <label key={d} className="check inline">
            <input type="checkbox" checked={form.weekend_days.includes(d)} disabled={!form.weekend_days.includes(d) && form.weekend_days.length >= 3} onChange={() => toggleDay(d)} />
            {weekdayName(d)}
          </label>
        ))}
      </fieldset>
      <p className="muted small">{t.audiences.codeHint}</p>
      {error && <p className="error">{error}</p>}
      <div className="row gap">
        <button type="submit" className="primary" disabled={busy}>
          {t.audiences.save}
        </button>
        <button type="button" className="link" onClick={onCancel} disabled={busy}>
          {t.audiences.cancel}
        </button>
      </div>
    </form>
  );
}

export default function AudienceSettings({ workspaceId, canEdit }: { workspaceId: string; canEdit: boolean }) {
  const [items, setItems] = useState<Audience[]>([]);
  const [presets, setPresets] = useState<AudiencePreset[]>([]);
  const [adding, setAdding] = useState<AudienceInput | null>(null);
  const [editing, setEditing] = useState<Audience | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok?: string; error?: string }>({});
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];

  const load = () => listAudiences(workspaceId).then(setItems).catch((e) => setMsg({ error: errorText(e) }));

  useEffect(() => {
    void load();
    listAudiencePresets(workspaceId).then(setPresets).catch(() => setPresets([]));
  }, [workspaceId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run(action: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg({});
    try {
      await action();
      await load();
      setMsg({ ok });
      setAdding(null);
      setEditing(null);
    } catch (err) {
      setMsg({ error: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const presetName = (p: AudiencePreset) => t.audiences.presetNames[p.code] ?? p.name;
  const unusedPresets = presets.filter((p) => !items.some((a) => a.code.toUpperCase() === p.code.toUpperCase()));

  return (
    <section className="card audiences">
      <h2>{t.audiences.title}</h2>
      <p className="muted small">{t.audiences.intro}</p>
      {items.length === 0 && <p className="muted">{t.audiences.none}</p>}
      {items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>{t.audiences.code}</th>
              <th>{t.audiences.name}</th>
              <th>{t.audiences.timeZone}</th>
              <th>{t.audiences.weekend}</th>
              <th />
              {canEdit && <th aria-label={t.settings.actions} />}
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td dir="ltr">
                  <strong>{a.code}</strong>
                </td>
                <td>{a.name}</td>
                <td dir="ltr">{a.time_zone}</td>
                <td>{weekendLabel(a.weekend_days)}</td>
                <td className="muted small">
                  {a.is_default && <span className="chip">{t.audiences.default}</span>} {t.audiences.posts(a.post_count)}
                </td>
                {canEdit && (
                  <td className="member-actions">
                    <button type="button" className="link" disabled={busy} onClick={() => setEditing(a)}>
                      {t.audiences.edit}
                    </button>
                    {!a.is_default && (
                      <>
                        <span className="muted" aria-hidden="true">
                          {' · '}
                        </span>
                        <button type="button" className="link" disabled={busy} onClick={() => run(() => updateAudience(workspaceId, a.id, { is_default: true }), t.audiences.saved)}>
                          {t.audiences.makeDefault}
                        </button>
                      </>
                    )}
                    <span className="muted" aria-hidden="true">
                      {' · '}
                    </span>
                    <button
                      type="button"
                      className="link danger"
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(t.audiences.confirmDelete)) void run(() => deleteAudience(workspaceId, a.id), t.audiences.deleted);
                      }}
                    >
                      {t.audiences.delete}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editing && canEdit && (
        <AudienceForm
          key={editing.id}
          initial={{ code: editing.code, name: editing.name, time_zone: editing.time_zone, weekend_days: editing.weekend_days }}
          zones={zones}
          busy={busy}
          onCancel={() => setEditing(null)}
          onSubmit={(body) => run(() => updateAudience(workspaceId, editing.id, body), t.audiences.saved)}
        />
      )}
      {adding && canEdit && (
        <AudienceForm key="new" initial={adding} zones={zones} busy={busy} onCancel={() => setAdding(null)} onSubmit={(body) => run(() => createAudience(workspaceId, body), t.audiences.saved)} />
      )}
      {canEdit && !adding && !editing && (
        <div className="row gap wrap">
          {unusedPresets.length > 0 && (
            <label>
              {t.audiences.addPreset}
              <select
                value=""
                disabled={busy}
                onChange={(e) => {
                  const p = presets.find((x) => x.code === e.target.value);
                  if (p) setAdding({ code: p.code, name: presetName(p), time_zone: p.time_zone, weekend_days: p.weekend_days });
                }}
              >
                <option value="">{t.audiences.addPreset}</option>
                {unusedPresets.map((p) => (
                  <option key={p.code} value={p.code}>
                    {presetName(p)} ({p.code}, {p.time_zone})
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="button" disabled={busy} onClick={() => setAdding(EMPTY)}>
            {t.audiences.add}
          </button>
        </div>
      )}
      <p className="muted small">{t.audiences.defaultHint}</p>
      {msg.error && <p className="error">{msg.error}</p>}
      {msg.ok && <p className="ok">{msg.ok}</p>}
    </section>
  );
}
