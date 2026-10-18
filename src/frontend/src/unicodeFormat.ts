// "Formatting" for LinkedIn posts. LinkedIn has no rich text, so the only way to show bold or italic is to
// swap each Latin letter / digit for its look-alike from the Unicode "Mathematical Alphanumeric Symbols"
// block. That block has no Hebrew (or any other script), so Hebrew letters pass through unchanged.
// Underline and strikethrough use combining marks and work on any script, but render unevenly on mobile.
//
// Trade-offs the editor must show the user: screen readers read these characters letter by letter or skip
// them, search does not match them, and every styled letter counts as two UTF-16 units.

export type Style = 'bold' | 'italic' | 'boldItalic' | 'script' | 'boldScript' | 'mono';
export type Decoration = 'underline' | 'strike';

const UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const LOWER = 'abcdefghijklmnopqrstuvwxyz';
const DIGITS = '0123456789';

// Code point of "A", "a" and "0" for each style. Sans-serif variants look closest to LinkedIn's own font.
// null = this style has no digits in Unicode.
const RANGES: Record<Style, { upper: number; lower: number; digits: number | null }> = {
  bold: { upper: 0x1d5d4, lower: 0x1d5ee, digits: 0x1d7ec }, // sans-serif bold
  italic: { upper: 0x1d608, lower: 0x1d622, digits: null }, // sans-serif italic
  boldItalic: { upper: 0x1d63c, lower: 0x1d656, digits: 0x1d7ec },
  script: { upper: 0x1d49c, lower: 0x1d4b6, digits: null },
  boldScript: { upper: 0x1d4d0, lower: 0x1d4ea, digits: null },
  mono: { upper: 0x1d670, lower: 0x1d68a, digits: 0x1d7f6 },
};

// Unicode left holes in the script range where older symbols already existed.
const SCRIPT_EXCEPTIONS: Record<string, string> = {
  B: 'ℬ', E: 'ℰ', F: 'ℱ', H: 'ℋ', I: 'ℐ', L: 'ℒ', M: 'ℳ', R: 'ℛ',
  e: 'ℯ', g: 'ℊ', o: 'ℴ',
};

const COMBINING: Record<Decoration, string> = { underline: '̲', strike: '̶' };

function styledChar(ch: string, style: Style): string {
  const r = RANGES[style];
  let i = UPPER.indexOf(ch);
  if (i >= 0) return style === 'script' && SCRIPT_EXCEPTIONS[ch] ? SCRIPT_EXCEPTIONS[ch] : String.fromCodePoint(r.upper + i);
  i = LOWER.indexOf(ch);
  if (i >= 0) return style === 'script' && SCRIPT_EXCEPTIONS[ch] ? SCRIPT_EXCEPTIONS[ch] : String.fromCodePoint(r.lower + i);
  i = DIGITS.indexOf(ch);
  if (i >= 0 && r.digits !== null) return String.fromCodePoint(r.digits + i);
  return ch; // Hebrew, punctuation, emoji, spaces: unchanged
}

/** Applies a style to plain text. Already-styled text is normalised first so styles do not stack. */
export function applyStyle(text: string, style: Style): string {
  return Array.from(stripFormatting(text), (ch) => styledChar(ch, style)).join('');
}

/** User-perceived characters: keeps emoji sequences, flags and letters with their marks together. */
function graphemes(text: string): string[] {
  if (typeof Intl !== 'undefined' && 'Segmenter' in Intl) {
    const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    return Array.from(seg.segment(text), (s) => s.segment);
  }
  return Array.from(text);
}

/** Adds a combining underline / strikethrough after every visible character (any script). */
export function applyDecoration(text: string, decoration: Decoration): string {
  const mark = COMBINING[decoration];
  return graphemes(stripDecorations(text))
    .map((g) => (/^\s+$/.test(g) ? g : g + mark))
    .join('');
}

// Reverse map: every styled code point we (or other tools) may produce -> the plain character.
const REVERSE = new Map<number, string>();
function addRange(start: number, alphabet: string) {
  for (let i = 0; i < alphabet.length; i++) REVERSE.set(start + i, alphabet[i]);
}
for (const r of Object.values(RANGES)) {
  addRange(r.upper, UPPER);
  addRange(r.lower, LOWER);
  if (r.digits !== null) addRange(r.digits, DIGITS);
}
// Other common families from formatting tools: serif bold/italic/bold-italic, fraktur, double-struck, sans.
for (const [u, l] of [
  [0x1d400, 0x1d41a], [0x1d434, 0x1d44e], [0x1d468, 0x1d482], [0x1d504, 0x1d51e], [0x1d538, 0x1d552],
  [0x1d56c, 0x1d586], [0x1d5a0, 0x1d5ba],
]) {
  addRange(u, UPPER);
  addRange(l, LOWER);
}
for (const d of [0x1d7ce, 0x1d7d8, 0x1d7e2]) addRange(d, DIGITS);
for (const [plain, styled] of Object.entries(SCRIPT_EXCEPTIONS)) REVERSE.set(styled.codePointAt(0)!, plain);
REVERSE.set(0x210e, 'h'); // serif italic h (Planck constant)

export function stripDecorations(text: string): string {
  return text.replace(/[̶̲̳]/g, '');
}

/** Back to plain characters: removes letter styles and underline / strikethrough marks. */
export function stripFormatting(text: string): string {
  return Array.from(stripDecorations(text), (ch) => REVERSE.get(ch.codePointAt(0)!) ?? ch).join('');
}

/**
 * Code points, the same unit the server enforces (3000). Underline/strikethrough marks count too.
 * LinkedIn's own unit is not documented; `utf16Length` is the conservative figure shown next to it.
 */
export function countCharacters(text: string): number {
  return Array.from(text).length;
}

export function utf16Length(text: string): number {
  return text.length;
}

/** Share of letters that are styled (0..1); drives the accessibility warning. */
export function styledRatio(text: string): number {
  let letters = 0;
  let styled = 0;
  for (const ch of stripDecorations(text)) {
    const cp = ch.codePointAt(0)!;
    if (REVERSE.has(cp)) {
      styled++;
      letters++;
    } else if (/\p{L}|\p{N}/u.test(ch)) {
      letters++;
    }
  }
  return letters === 0 ? 0 : styled / letters;
}

/** True when the selection has letters a style cannot change (e.g. Hebrew). Marks and controls are ignored. */
export function hasUnstylable(text: string): boolean {
  return /[^\sA-Za-z0-9\p{P}\p{S}\p{M}\p{Cf}]/u.test(stripFormatting(text));
}

// A line may start with a direction mark (our RLM button) before a bullet or number.
const LINE_PREFIX = /^([\s\u200e\u200f]*)(?:\d+[.)]|[•◆▪→✅✔⭐👉-])\s+/u;

export const BULLETS = ['•', '◆', '▪', '→', '✅', '✔', '⭐', '👉'] as const;
export const SEPARATOR = '―――――――――――';
export const RLM = '‏'; // right-to-left mark: fixes a line that starts with a number or Latin word

/** Numbers each non-empty line: "1. …", restarting at 1. Existing "1. " / "• " prefixes are replaced. */
export function numberLines(text: string): string {
  let n = 0;
  return text
    .split('\n')
    .map((line) => {
      if (!line.trim()) return line;
      n++;
      const m = LINE_PREFIX.exec(line);
      const lead = m ? m[1] : '';
      return `${lead}${n}. ${m ? line.slice(m[0].length) : line.slice(lead.length)}`;
    })
    .join('\n');
}

/** Puts a bullet in front of each non-empty line, replacing an existing bullet or number. */
export function bulletLines(text: string, bullet: string): string {
  return text
    .split('\n')
    .map((line) => {
      if (!line.trim()) return line;
      const m = LINE_PREFIX.exec(line);
      const lead = m ? m[1] : '';
      return `${lead}${bullet} ${m ? line.slice(m[0].length) : line.slice(lead.length)}`;
    })
    .join('\n');
}

export function quote(text: string): string {
  return `❝ ${text.trim()} ❞`;
}

/** Hashtags in the text, without '#'. De-duplicated case-insensitively, first spelling wins. */
export function hashtags(text: string): string[] {
  const found = new Map<string, string>();
  // Same rule as the server: '#' not glued to a word or another '#', tag starts with a letter or digit,
  // may contain marks (Hebrew points) and underscores.
  for (const m of text.matchAll(/(?<![\p{L}\p{N}_#])#([\p{L}\p{N}][\p{L}\p{N}\p{M}_]*)/gu)) {
    const key = m[1].toLowerCase();
    if (!found.has(key)) found.set(key, m[1]);
  }
  return [...found.values()];
}
