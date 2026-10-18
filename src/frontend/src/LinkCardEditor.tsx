import { useState } from 'react';
import { ApiError, fetchLinkPreview, type LinkCard } from './api';
import { t } from './i18n';

/** URL + title + description for a LinkedIn link card. "Fetch" pre-fills from the page's Open Graph tags. */
export default function LinkCardEditor({
  workspaceId,
  value,
  onChange,
  disabled,
  hasImage,
}: {
  workspaceId: string;
  value: LinkCard | null;
  onChange: (next: LinkCard | null) => void;
  disabled?: boolean;
  hasImage: boolean;
}) {
  const [url, setUrl] = useState(value?.url ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  async function fetchDetails() {
    const target = url.trim();
    if (!target) return;
    setBusy(true);
    setError('');
    try {
      const p = await fetchLinkPreview(workspaceId, target);
      setImageUrl(p.image_url);
      onChange({ url: p.url, title: (p.title ?? value?.title ?? '').slice(0, 200), description: p.description });
      setUrl(p.url);
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : '';
      setError(code.includes('blocked') ? t.linkCard.blocked : code.includes('not_html') ? t.linkCard.notPage : t.linkCard.fetchFailed);
      // Still let the user fill the card by hand.
      if (!value) onChange({ url: target, title: '', description: null });
    } finally {
      setBusy(false);
    }
  }

  if (!value) {
    return (
      <div className="link-add">
        <input
          dir="ltr"
          type="url"
          placeholder="https://"
          value={url}
          disabled={disabled || busy}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void fetchDetails();
            }
          }}
          aria-label={t.linkCard.url}
        />
        <button type="button" disabled={disabled || busy || !url.trim()} onClick={() => void fetchDetails()}>
          {busy ? t.linkCard.fetching : t.linkCard.add}
        </button>
        {error && <span className="error small">{error}</span>}
      </div>
    );
  }

  return (
    <div className="link-card-edit">
      <div className="row-between">
        <strong>{t.linkCard.title}</strong>
        <span className="actions">
          <button type="button" className="link" disabled={disabled || busy} onClick={() => void fetchDetails()}>
            {busy ? t.linkCard.fetching : t.linkCard.refetch}
          </button>
          <button type="button" className="link danger" disabled={disabled} onClick={() => onChange(null)}>
            {t.linkCard.remove}
          </button>
        </span>
      </div>
      <label>
        {t.linkCard.url}
        <input
          dir="ltr"
          type="url"
          value={url}
          disabled={disabled}
          onChange={(e) => setUrl(e.target.value)}
          onBlur={() => {
            const next = url.trim();
            if (!next) return setUrl(value.url); // empty is not a valid card URL: keep the saved one
            if (next !== value.url) onChange({ ...value, url: next });
          }}
        />
      </label>
      <label>
        {t.linkCard.cardTitle}
        <input dir="auto" maxLength={200} value={value.title} disabled={disabled} onChange={(e) => onChange({ ...value, title: e.target.value })} />
      </label>
      <label>
        {t.linkCard.description}
        <textarea
          dir="auto"
          rows={2}
          maxLength={300}
          value={value.description ?? ''}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, description: e.target.value || null })}
        />
      </label>
      <p className="muted small">
        {hasImage ? t.linkCard.thumbnailFromImage : imageUrl ? t.linkCard.thumbnailHint : t.linkCard.noThumbnail}
      </p>
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
