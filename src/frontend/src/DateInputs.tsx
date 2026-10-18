import { useEffect, useRef, useState } from 'react';
import { datePlaceholder, dayToText, parseDay, parseTime, timePlaceholder, timeToText } from './dates';
import { t } from './i18n';

// Native date inputs show the browser's format, not the user's preference, so we show a text field in the
// chosen format (Settings -> Appearance) and keep the native picker behind a button.

type PickerInput = HTMLInputElement & { showPicker?: () => void };

function openPicker(el: PickerInput | null) {
  if (!el) return;
  try {
    el.showPicker?.();
  } catch {
    el.focus();
  }
}

/** value / onChange: "yyyy-mm-dd" or null. */
export function DateInput({
  value,
  onChange,
  disabled,
  min,
  label,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  disabled?: boolean;
  min?: string;
  label?: string;
}) {
  const native = useRef<PickerInput>(null);
  const [text, setText] = useState(dayToText(value));
  const [bad, setBad] = useState(false);
  useEffect(() => {
    setText(dayToText(value));
    setBad(false);
  }, [value]);


  function commit(raw: string) {
    if (!raw.trim()) {
      setBad(false);
      return onChange(null);
    }
    const parsed = parseDay(raw);
    setBad(!parsed);
    if (parsed) onChange(parsed);
  }

  return (
    <span className="date-field">
      <input
        type="text"
        dir="ltr"
        inputMode="numeric"
        placeholder={datePlaceholder()}
        aria-label={label}
        aria-invalid={bad || undefined}
        title={bad ? t.dates.invalidDate : undefined}
        className={bad ? 'invalid' : undefined}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
      />
      <button type="button" className="pick" disabled={disabled} aria-label={t.dates.pick} onClick={() => openPicker(native.current)}>
        📅
      </button>
      <input
        ref={native}
        type="date"
        tabIndex={-1}
        aria-hidden="true"
        className="native-hidden"
        value={value ?? ''}
        min={min}
        onChange={(e) => onChange(e.target.value || null)}
      />
    </span>
  );
}

/** value / onChange: "yyyy-mm-ddTHH:mm" (wall-clock time, interpreted by the server in the workspace zone). */
export function DateTimeInput({
  value,
  onChange,
  disabled,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  label?: string;
}) {
  const [timeText, setTimeText] = useState(timeToText(value.slice(11, 16)));
  const [badTime, setBadTime] = useState(false);
  useEffect(() => {
    setTimeText(timeToText(value.slice(11, 16)));
    setBadTime(false);
  }, [value]);


  const day = value.slice(0, 10) || null;
  return (
    <span className="datetime-field">
      <DateInput
        value={day}
        label={label}
        disabled={disabled}
        onChange={(d) => d && onChange(`${d}T${value.slice(11, 16) || '09:00'}`)}
      />
      <input
        type="text"
        dir="ltr"
        inputMode="numeric"
        placeholder={timePlaceholder()}
        className={badTime ? 'time invalid' : 'time'}
        aria-label={t.dates.time}
        aria-invalid={badTime || undefined}
        value={timeText}
        disabled={disabled}
        onChange={(e) => setTimeText(e.target.value)}
        onBlur={(e) => {
          const parsed = parseTime(e.target.value);
          setBadTime(!parsed);
          if (parsed && day) onChange(`${day}T${parsed}`);
        }}
      />
    </span>
  );
}
