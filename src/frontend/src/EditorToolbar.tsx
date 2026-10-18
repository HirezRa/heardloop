import { useState, type RefObject } from 'react';
import { LOCALE, t } from './i18n';
import {
  BULLETS,
  RLM,
  SEPARATOR,
  applyDecoration,
  applyStyle,
  bulletLines,
  hasUnstylable,
  numberLines,
  quote,
  stripFormatting,
  type Decoration,
  type Style,
} from './unicodeFormat';

const EMOJI = ['🚀', '✅', '💡', '🎯', '🔥', '👇', '📌', '💬', '🙌', '⚡', '📈', '🧠', '❤️', '👉', '🎉', '⭐'];

const STYLES: { id: Style; label: string; sample: string }[] = [
  { id: 'bold', label: 'B', sample: applyStyle('Bold', 'bold') },
  { id: 'italic', label: 'I', sample: applyStyle('Italic', 'italic') },
  { id: 'boldItalic', label: 'BI', sample: applyStyle('Both', 'boldItalic') },
  { id: 'script', label: '𝓢', sample: applyStyle('Script', 'script') },
  { id: 'boldScript', label: '𝓑𝓢', sample: applyStyle('Script', 'boldScript') },
  { id: 'mono', label: 'M', sample: applyStyle('Mono', 'mono') },
];

/**
 * Formatting toolbar for a plain <textarea>. Every action rewrites the selected text in place (the post
 * stays plain Unicode, exactly what LinkedIn receives) and restores the selection.
 */
export default function EditorToolbar({
  textareaRef,
  value,
  onChange,
  disabled,
}: {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const [hint, setHint] = useState('');
  const [emojiOpen, setEmojiOpen] = useState(false);

  function selection(): { start: number; end: number; text: string } {
    const el = textareaRef.current;
    if (!el) return { start: value.length, end: value.length, text: '' };
    return { start: el.selectionStart, end: el.selectionEnd, text: value.slice(el.selectionStart, el.selectionEnd) };
  }

  /** Replaces [start, end) with `next` and selects the new text. */
  function replace(start: number, end: number, next: string) {
    onChange(value.slice(0, start) + next + value.slice(end));
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(start, start + next.length);
    });
  }

  /** Current selection, or the whole line when nothing is selected (so bullets work on the caret line). */
  function selectionOrLine(): { start: number; end: number; text: string } {
    const s = selection();
    if (s.start !== s.end) return s;
    const start = value.lastIndexOf('\n', s.start - 1) + 1;
    const endIdx = value.indexOf('\n', s.start);
    const end = endIdx === -1 ? value.length : endIdx;
    return { start, end, text: value.slice(start, end) };
  }

  function style(id: Style) {
    const s = selection();
    if (!s.text) return setHint(t.editor.selectFirst);
    setHint(hasUnstylable(s.text) ? t.editor.hebrewUnchanged : '');
    replace(s.start, s.end, applyStyle(s.text, id));
  }

  function decorate(d: Decoration) {
    const s = selection();
    if (!s.text) return setHint(t.editor.selectFirst);
    setHint(t.editor.decorationHint);
    replace(s.start, s.end, applyDecoration(s.text, d));
  }

  function clear() {
    const s = selection();
    if (!s.text) {
      onChange(stripFormatting(value));
      setHint(t.editor.clearedAll);
      return;
    }
    setHint('');
    replace(s.start, s.end, stripFormatting(s.text));
  }

  function insert(text: string, keepSelectionOnInserted = true) {
    const s = selection();
    onChange(value.slice(0, s.start) + text + value.slice(s.end));
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      const pos = s.start + text.length;
      el.setSelectionRange(keepSelectionOnInserted ? pos : s.start, pos);
    });
  }

  const btn = (label: string, title: string, onClick: () => void, extra?: string) => (
    <button type="button" className={`tb${extra ? ` ${extra}` : ''}`} title={title} aria-label={title} disabled={disabled} onClick={onClick}>
      {label}
    </button>
  );

  return (
    <div className="toolbar" role="toolbar" aria-label={t.editor.toolbar}>
      <div className="tb-group">
        {STYLES.map((s) => btn(s.label, `${t.editor.styles[s.id]} - ${s.sample}`, () => style(s.id), s.id))}
        {btn('U̲', t.editor.underline, () => decorate('underline'))}
        {btn('S̶', t.editor.strike, () => decorate('strike'))}
        {btn('Tx', t.editor.clear, clear)}
      </div>
      <div className="tb-group">
        {BULLETS.slice(0, 4).map((b) =>
          btn(b, t.editor.bullet, () => {
            const s = selectionOrLine();
            replace(s.start, s.end, bulletLines(s.text, b));
          }),
        )}
        {btn('1.', t.editor.numbered, () => {
          const s = selectionOrLine();
          replace(s.start, s.end, numberLines(s.text));
        })}
        {btn('❝', t.editor.quote, () => {
          const s = selection();
          if (!s.text) return setHint(t.editor.selectFirst);
          replace(s.start, s.end, quote(s.text));
        })}
        {btn('―', t.editor.separator, () => insert(`\n${SEPARATOR}\n`))}
        {btn('↵', t.editor.blankLine, () => insert('\n\n'))}
      </div>
      <div className="tb-group">
        <span className="emoji-wrap">
          {btn('😀', t.editor.emoji, () => setEmojiOpen((o) => !o))}
          {emojiOpen && (
            <div className="emoji-pop" role="menu">
              {EMOJI.map((e) => (
                <button
                  key={e}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    insert(e);
                    setEmojiOpen(false);
                  }}
                >
                  {e}
                </button>
              ))}
            </div>
          )}
        </span>
        {btn('🔗', t.editor.link, () => {
          const s = selection();
          const url = window.prompt(t.editor.linkPrompt)?.trim();
          if (!url) return;
          if (!/^https?:\/\/\S+$/i.test(url)) return setHint(t.editor.linkInvalid);
          // LinkedIn has no anchor-text links: the URL itself must appear and LinkedIn turns it into a link.
          replace(s.start, s.end, s.text ? `${s.text} (${url})` : url);
          setHint(t.editor.linkHint);
        })}
        {btn('#', t.editor.hashtag, () => insert('#', true))}
        {LOCALE === 'he' && btn('‏RLM', t.editor.rlm, () => insert(RLM))}
      </div>
      {hint && (
        <span className="muted small tb-hint" role="status">
          {hint}
        </span>
      )}
    </div>
  );
}
