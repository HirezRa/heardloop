import { useEffect, useId, useRef, type ReactNode } from 'react';
import { t } from './i18n';

/**
 * Release notes in a modal dialog. showModal() moves focus inside, keeps it there and closes on Escape
 * (WCAG 2.1.2, 2.4.3); focus goes back to the control that opened it.
 */
export default function NotesDialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const el = ref.current;
    if (el && !el.open) el.showModal();
    return () => {
      if (el?.open) el.close();
      opener?.focus();
    };
  }, []);
  return (
    <dialog ref={ref} className="notes-dialog" aria-labelledby={headingId} onClose={onClose}>
      <form method="dialog">
        <h3 id={headingId}>{title}</h3>
        <div className="notes-body">{children}</div>
        <button type="submit">{t.about.close}</button>
      </form>
    </dialog>
  );
}
