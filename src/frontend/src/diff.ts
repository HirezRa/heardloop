/** Word-level LCS diff for version history. No external dependency. */

export type DiffPart = { type: 'equal' | 'add' | 'del'; text: string };

/** Split on whitespace while keeping the separators attached to the preceding word. */
export function words(text: string): string[] {
  if (!text) return [];
  return text.match(/\S+\s*/g) ?? [text];
}

/**
 * Longest common subsequence on words, then emit equal / add / del parts.
 * Suitable for short post bodies (thousands of characters, hundreds of words).
 */
export function diffWords(before: string, after: string): DiffPart[] {
  const a = words(before);
  const b = words(after);
  const n = a.length;
  const m = b.length;
  // dp[i][j] = LCS length of a[i..] and b[j..]
  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const parts: DiffPart[] = [];
  let i = 0;
  let j = 0;
  const push = (type: DiffPart['type'], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push('equal', a[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push('del', a[i]);
      i++;
    } else {
      push('add', b[j]);
      j++;
    }
  }
  while (i < n) {
    push('del', a[i]);
    i++;
  }
  while (j < m) {
    push('add', b[j]);
    j++;
  }
  return parts;
}
