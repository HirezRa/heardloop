/**
 * WCAG 2.1 contrast helpers. The accent colour is chosen per user and is dark enough for light mode, but as
 * text on the dark theme's panels it falls to about 3:1. `readableOn` lightens it (mixes it with white)
 * until it reaches the target ratio on every given background.
 */

/** Dark theme backgrounds that text sits on (keep in sync with --bg and --panel in styles.css). */
export const DARK_BACKGROUNDS = ['#0d1117', '#161b22'] as const;
/** Light theme backgrounds (--bg and --panel). */
export const LIGHT_BACKGROUNDS = ['#f7f7f8', '#ffffff'] as const;
/** WCAG 1.4.3: normal-size text. */
export const TEXT_RATIO = 4.5;

function channels(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex([r, g, b]: [number, number, number]): string {
  return '#' + [r, g, b].map((c) => Math.round(c).toString(16).padStart(2, '0')).join('');
}

export function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The colour itself when it already passes, otherwise the least-lightened tint that passes on every background. */
export function readableOn(hex: string, backgrounds: readonly string[], target = TEXT_RATIO): string {
  const base = channels(hex);
  for (let step = 0; step <= 20; step++) {
    const mix = step / 20;
    const candidate = toHex(base.map((c) => c + (255 - c) * mix) as [number, number, number]);
    if (backgrounds.every((bg) => contrastRatio(candidate, bg) >= target)) return candidate;
  }
  return '#ffffff';
}
