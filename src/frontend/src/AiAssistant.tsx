import { useState, type RefObject } from 'react';
import { ApiError, aiAssist, type AssistAction, type AssistTone } from './api';
import { appendTag } from './HashtagPicker';
import { t } from './i18n';

const ACTIONS: AssistAction[] = ['improve', 'shorten', 'hook', 'fix', 'hashtags', 'translate_en', 'translate_he'];
const TONES: AssistTone[] = ['professional', 'friendly', 'bold'];

interface Suggestion {
  action: AssistAction;
  /** What was sent: the selection, or the whole post when nothing was selected. */
  start: number;
  end: number;
  original: string;
  text: string | null;
  hashtags: string[];
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return t.ai.tooMany;
    if (err.detail === 'ai_not_configured') return t.ai.notConfigured;
    if (err.detail === 'ai_failed:timeout') return t.ai.timeout;
    if (err.detail.startsWith('ai_failed:')) return t.ai.failed;
  }
  return t.errors.generic;
}

/**
 * Optional AI help for the post text. Works on the selection, or on the whole post when nothing is selected.
 * The answer is only a suggestion: nothing changes until "Replace" or "Insert below" is clicked.
 */
export default function AiAssistant({
  workspaceId,
  textareaRef,
  value,
  onChange,
  disabled,
}: {
  workspaceId: string;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const [tone, setTone] = useState<AssistTone | ''>('');
  const [busy, setBusy] = useState<AssistAction | null>(null);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Suggestion | null>(null);

  function target(): { start: number; end: number; text: string } {
    const el = textareaRef.current;
    if (el && el.selectionStart !== el.selectionEnd) {
      return { start: el.selectionStart, end: el.selectionEnd, text: value.slice(el.selectionStart, el.selectionEnd) };
    }
    return { start: 0, end: value.length, text: value };
  }

  async function ask(action: AssistAction) {
    const sel = target();
    if (!sel.text.trim()) return setError(t.ai.nothingToWorkOn);
    setError('');
    setResult(null);
    setBusy(action);
    try {
      const res = await aiAssist(workspaceId, action, sel.text, tone || undefined);
      setResult({ action, start: sel.start, end: sel.end, original: sel.text, text: res.text, hashtags: res.hashtags });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  }

  /** The text may have been edited while waiting: only apply when the original part is still there. */
  function stillThere(s: Suggestion): boolean {
    return value.slice(s.start, s.end) === s.original;
  }

  function replace() {
    if (!result?.text) return;
    if (!stillThere(result)) return setError(t.ai.textChanged);
    onChange(value.slice(0, result.start) + result.text + value.slice(result.end));
    setResult(null);
  }

  function insertBelow() {
    if (!result?.text) return;
    if (!stillThere(result)) return setError(t.ai.textChanged);
    onChange(`${value.slice(0, result.end)}\n\n${result.text}${value.slice(result.end)}`);
    setResult(null);
  }

  function addTag(tag: string) {
    onChange(appendTag(value, tag));
    setResult((r) => (r ? { ...r, hashtags: r.hashtags.filter((x) => x !== tag) } : r));
  }

  return (
    <details className="ai-assistant">
      <summary>{t.ai.title}</summary>
      <p className="muted small">{t.ai.hint}</p>
      <div className="ai-actions">
        {ACTIONS.map((a) => (
          <button key={a} type="button" disabled={disabled || busy !== null} onClick={() => void ask(a)}>
            {busy === a ? t.ai.working : t.ai.actions[a]}
          </button>
        ))}
        <label className="small">
          {t.ai.tone}{' '}
          <select value={tone} disabled={disabled || busy !== null} onChange={(e) => setTone(e.target.value as AssistTone | '')}>
            <option value="">{t.ai.toneAny}</option>
            {TONES.map((x) => (
              <option key={x} value={x}>
                {t.ai.tones[x]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
      {result?.text && (
        <div className="ai-result" role="status">
          <div className="ai-text" dir="auto">
            {result.text}
          </div>
          <div className="actions">
            <button type="button" className="primary" disabled={disabled} onClick={replace}>
              {result.start === 0 && result.end === value.length ? t.ai.replaceAll : t.ai.replaceSelection}
            </button>
            <button type="button" disabled={disabled} onClick={insertBelow}>
              {t.ai.insertBelow}
            </button>
            <button type="button" className="link" onClick={() => setResult(null)}>
              {t.ai.discard}
            </button>
          </div>
        </div>
      )}
      {result && result.action === 'hashtags' && (
        <div className="tag-row" role="status">
          <span className="muted small">{t.ai.suggestedTags}</span>
          {result.hashtags.map((tag) => (
            <span key={tag} className="chip">
              <button type="button" disabled={disabled} onClick={() => addTag(tag)} title={t.hashtags.add}>
                #{tag}
              </button>
            </span>
          ))}
          {result.hashtags.length === 0 && <span className="muted small">{t.ai.allTagsAdded}</span>}
        </div>
      )}
    </details>
  );
}
