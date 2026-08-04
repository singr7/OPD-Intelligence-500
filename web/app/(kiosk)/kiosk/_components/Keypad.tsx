"use client";

// A big numeric keypad (AR3, doc 04 law 3 and law 6).
//
// It exists because the two numbers this kiosk asks for — a phone number and a
// coordinator's PIN — are both typed by someone standing up, often with an
// on-screen keyboard covering half the screen and an attendant reaching across.
// A `<input type="tel">` on a kiosk browser is a lottery: some shells raise a
// full QWERTY, some raise nothing at all. Ten fixed keys at 64px+ are the same
// every time, and they are the same ten keys a patient has pressed on every
// phone they have ever owned.
//
// The digits are rendered into a **read-only display**, not an input the shell
// can hijack. `masked` hides them for the PIN — the strip is unlocked in a
// public corridor, and the one thing worse than a PIN pad on a kiosk is a PIN
// pad that prints the PIN in 40px type.

import { KioskLang, tb } from "../_lib/i18n";
import s from "../kiosk.module.css";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

export function Keypad({
  lang,
  value,
  onChange,
  maxLength,
  masked = false,
  compact = false,
  disabled = false,
  label,
  testId = "keypad",
}: {
  lang: KioskLang;
  value: string;
  onChange: (next: string) => void;
  maxLength: number;
  /** PIN entry: show a dot per digit instead of the digit. */
  masked?: boolean;
  /** The staff strip's smaller keys — still ≥56px, but not the full patient size. */
  compact?: boolean;
  disabled?: boolean;
  /** Accessible name for the display, since it is not a labelled input. */
  label: string;
  testId?: string;
}) {
  const press = (key: string) => {
    if (disabled) return;
    if (key === "⌫") {
      onChange(value.slice(0, -1));
      return;
    }
    if (value.length >= maxLength) return;
    onChange(value + key);
  };

  const shown = masked ? "•".repeat(value.length) : value;

  return (
    <div className={`${s.keypad} ${compact ? s.keypadCompact : ""}`} data-testid={testId}>
      <output
        className={s.keypadDisplay}
        aria-label={label}
        aria-live="polite"
        data-testid={`${testId}-display`}
      >
        {shown || <span className={s.keypadEmpty}>{"—"}</span>}
      </output>
      <div className={s.keypadKeys}>
        {KEYS.map((key, i) =>
          key === "" ? (
            <span key={`gap-${i}`} aria-hidden="true" />
          ) : (
            <button
              key={key}
              type="button"
              className={`${s.keypadKey} ${key === "⌫" ? s.keypadBack : ""}`}
              disabled={disabled || (key === "⌫" ? value.length === 0 : value.length >= maxLength)}
              onClick={() => press(key)}
              aria-label={key === "⌫" ? tb("keypadDelete", lang) : key}
              data-testid={key === "⌫" ? `${testId}-back` : `${testId}-${key}`}
            >
              {key}
            </button>
          )
        )}
      </div>
    </div>
  );
}
