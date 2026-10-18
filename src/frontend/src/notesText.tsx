/** Render release notes as plain text: keep line breaks; turn bare https:// URLs into safe links. */
import type { ReactNode } from 'react';

const URL_RE = /(https:\/\/[^\s<>"']+)/g;

export function notesAsNodes(notes: string): ReactNode[] {
  const lines = notes.replace(/\r\n/g, '\n').split('\n');
  const out: ReactNode[] = [];
  lines.forEach((line, i) => {
    if (i > 0) out.push(<br key={`br-${i}`} />);
    out.push(...linkifyLine(line, i));
  });
  return out;
}

function linkifyLine(line: string, lineKey: number): ReactNode[] {
  const parts: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(URL_RE.source, 'g');
  while ((m = re.exec(line)) !== null) {
    if (m.index > last) parts.push(line.slice(last, m.index));
    const href = m[1];
    parts.push(
      <a key={`u-${lineKey}-${m.index}`} href={href} target="_blank" rel="noopener noreferrer">
        {href}
      </a>,
    );
    last = m.index + href.length;
  }
  if (last < line.length) parts.push(line.slice(last));
  if (parts.length === 0) parts.push(line);
  return parts;
}
