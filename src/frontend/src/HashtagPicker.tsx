import { useEffect, useState } from 'react';
import { ApiError, deleteHashtag, getHashtags, saveHashtag, type Hashtags } from './api';
import { t } from './i18n';
import { hashtags as tagsIn } from './unicodeFormat';

const TAG_RE = /^[\p{L}\p{N}][\p{L}\p{N}\p{M}_]{0,63}$/u;

/** Removes "#tag" (any case) from the text; only the lines it touched are tidied. */
function removeTag(body: string, tag: string): string {
  const escaped = tag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(^|\\s)#${escaped}(?![\\p{L}\\p{N}\\p{M}_])`, 'giu');
  const lines = body.split('\n').map((line) => {
    if (!re.test(line)) return line;
    re.lastIndex = 0;
    return line.replace(re, '$1').replace(/[ \t]{2,}/g, ' ').trim();
  });
  // Drop a hashtag line that became empty, and any blank lines it leaves at the end.
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd();
}

/** Appends "#tag" on the post's last line (a hashtag line), creating it when needed. */
function appendTag(body: string, tag: string): string {
  const trimmed = body.trimEnd();
  if (!trimmed) return `#${tag}`;
  const lastLine = trimmed.slice(trimmed.lastIndexOf('\n') + 1);
  const lastIsTags = /^(\s*#[\p{L}\p{N}][\p{L}\p{N}\p{M}_]*)+\s*$/u.test(lastLine);
  return lastIsTags ? `${trimmed} #${tag}` : `${trimmed}\n\n#${tag}`;
}

/**
 * Hashtags for a post: the ones already in the text (with a counter against LinkedIn's 3-5 guidance),
 * the workspace's saved library and recently used tags. Click = add or remove in the post text.
 */
export default function HashtagPicker({
  workspaceId,
  body,
  onChange,
  disabled,
}: {
  workspaceId: string;
  body: string;
  onChange: (body: string) => void;
  disabled?: boolean;
}) {
  const [lib, setLib] = useState<Hashtags>({ saved: [], recent: [], recommended_max: 5 });
  const [newTag, setNewTag] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    getHashtags(workspaceId).then(setLib).catch(() => undefined);
  }, [workspaceId]);

  const inPost = tagsIn(body);
  const inPostKeys = new Set(inPost.map((x) => x.toLowerCase()));
  const max = lib.recommended_max;
  const over = inPost.length > max;

  function toggle(tag: string) {
    if (inPostKeys.has(tag.toLowerCase())) onChange(removeTag(body, tag));
    else onChange(appendTag(body, tag));
  }

  async function save() {
    const tag = newTag.trim().replace(/^#/, '');
    if (!tag) return;
    if (!TAG_RE.test(tag)) return setError(t.hashtags.invalid);
    setError('');
    try {
      setLib(await saveHashtag(workspaceId, tag));
      setNewTag('');
      if (!inPostKeys.has(tag.toLowerCase())) onChange(appendTag(body, tag));
    } catch (err) {
      setError(err instanceof ApiError && err.detail === 'too_many_hashtags' ? t.hashtags.libraryFull : t.errors.generic);
    }
  }

  async function forget(tag: string) {
    try {
      await deleteHashtag(workspaceId, tag);
      setLib((l) => ({ ...l, saved: l.saved.filter((x) => x.toLowerCase() !== tag.toLowerCase()) }));
    } catch {
      setError(t.errors.generic);
    }
  }

  const chip = (tag: string, saved: boolean) => {
    const active = inPostKeys.has(tag.toLowerCase());
    return (
      <span key={`${saved ? 's' : 'r'}:${tag}`} className={active ? 'chip active' : 'chip'}>
        <button type="button" disabled={disabled} onClick={() => toggle(tag)} title={active ? t.hashtags.remove : t.hashtags.add}>
          #{tag}
        </button>
        {saved && !disabled && (
          <button type="button" className="chip-x" aria-label={t.hashtags.forget(tag)} onClick={() => void forget(tag)}>
            ×
          </button>
        )}
      </span>
    );
  };

  const unsavedInPost = inPost.filter((x) => !lib.saved.some((s) => s.toLowerCase() === x.toLowerCase()));

  return (
    <div className="hashtags">
      <div className="row-between">
        <span className="small">
          {t.hashtags.title}{' '}
          <span className={over ? 'warn-text' : 'muted'}>
            {inPost.length} / {max}
          </span>
        </span>
        {over && <span className="warn-text small">{t.hashtags.tooMany(max)}</span>}
      </div>
      {(lib.saved.length > 0 || unsavedInPost.length > 0) && (
        <div className="tag-row">
          <span className="muted small">{t.hashtags.saved}</span>
          {lib.saved.map((x) => chip(x, true))}
          {unsavedInPost.map((x) => (
            <span key={`p:${x}`} className="chip active">
              <button type="button" disabled={disabled} onClick={() => toggle(x)} title={t.hashtags.remove}>
                #{x}
              </button>
              {!disabled && (
                <button type="button" className="chip-x" title={t.hashtags.saveThis} aria-label={t.hashtags.saveThis} onClick={() => void saveHashtag(workspaceId, x).then(setLib).catch(() => setError(t.errors.generic))}>
                  ☆
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {lib.recent.length > 0 && (
        <div className="tag-row">
          <span className="muted small">{t.hashtags.recent}</span>
          {lib.recent.slice(0, 15).map((x) => chip(x, false))}
        </div>
      )}
      {!disabled && (
        <div className="tag-add">
          <input
            dir="auto"
            placeholder={t.hashtags.newPlaceholder}
            value={newTag}
            maxLength={65}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                void save();
              }
            }}
          />
          <button type="button" disabled={!newTag.trim()} onClick={() => void save()}>
            {t.hashtags.saveAndAdd}
          </button>
          {error && <span className="error small">{error}</span>}
        </div>
      )}
    </div>
  );
}
