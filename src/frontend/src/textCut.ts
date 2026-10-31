/**
 * Truncate by Unicode code point so a cut never lands inside a surrogate pair
 * (e.g. bold Mathematical letters are two UTF-16 units each).
 * When `ellipsis` is true (default), the last kept point is replaced by "…".
 */
export function truncateCodePoints(text: string, max: number, ellipsis = true): string {
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  if (max <= 0) return '';
  if (!ellipsis) return chars.slice(0, max).join('');
  return `${chars.slice(0, max - 1).join('')}…`;
}
