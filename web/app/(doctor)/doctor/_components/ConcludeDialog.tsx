"use client";

// Ending the consult, on the record (plan §5.3b).
//
// Single job: make a doctor who is about to end a visit with nothing digital in
// it understand exactly what will not exist afterwards — and then let them do
// it, because a paper script is a legitimate outcome and blocking it would only
// teach people to abandon the visit instead.
//
// The three most important elements, in order:
//   1. which of the three endings this is;
//   2. what is lost by the two that lose something;
//   3. the button that does it.
//
// The prototype's version of this warning said the system "won't capture this
// visit findings". Vague warnings get clicked through. This one names the four
// concrete consequences — the patient's app, the pharmacy, the follow-up
// reminders, the record itself — because the point of the dialog is to make a
// doctor pause, not to have been technically shown.
//
// Colour discipline (plan §6): the destructive-feeling choice is *not* painted
// marigold to make it eye-catching, which is what the prototype did to the one
// path that produces no record. Marigold appears once, on the consequence
// panel, meaning "attention" — and the confirm button stays the ordinary green
// of a safe expected progression, because concluding a consult you have
// finished is exactly that.

import { AlertTriangle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { RxMode } from "../_lib/doctor";

type Props = {
  patientName: string;
  /** Whether a signed note exists. Without one, `system` is not on offer at all
   *  — the backend refuses it, and an option that always errors is a trap. */
  noteSigned: boolean;
  busy: boolean;
  error: string | null;
  onConfirm: (mode: RxMode, note: string) => void;
  onCancel: () => void;
};

const LOSS = [
  "The patient's app and records will not show today's medicines.",
  "The pharmacy will have no digital copy.",
  "Follow-up reminders cannot be generated from this visit.",
];

export function ConcludeDialog({
  patientName,
  noteSigned,
  busy,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const [mode, setMode] = useState<RxMode>(noteSigned ? "system" : "external_manual");
  const [note, setNote] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  const lossy = mode !== "system";

  return (
    <div className="cdlg-scrim" role="presentation">
      <div
        className="cdlg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cdlg-title"
        tabIndex={-1}
        ref={ref}
        data-testid="conclude-dialog"
      >
        <h2 id="cdlg-title">End the consult for {patientName}</h2>
        <p className="cdlg-lead">How did this consultation end?</p>

        <div className="cdlg-choices" role="radiogroup" aria-label="How the consult ended">
          {noteSigned && (
            <Choice
              id="system"
              checked={mode === "system"}
              onPick={setMode}
              title="Prescription issued here"
              line="The signed note and its prescription are the record of this visit."
            />
          )}
          <Choice
            id="external_manual"
            checked={mode === "external_manual"}
            onPick={setMode}
            title="Written on paper, or in another system"
            line="A script exists, but this system has no copy of it."
          />
          <Choice
            id="none"
            checked={mode === "none"}
            onPick={setMode}
            title="No prescription given"
            line="Advice, reassurance or a follow-up date only."
          />
        </div>

        {/* Fixed slot rather than an appearing block: the confirm button must not
            move under the doctor's cursor when they change their mind. */}
        <div className="cdlg-conseq" data-lossy={lossy}>
          {lossy ? (
            <>
              <p className="cdlg-conseq-h">
                <AlertTriangle aria-hidden="true" />
                No consult note and no digital prescription will be recorded for this visit.
              </p>
              <ul>
                {LOSS.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="cdlg-conseq-h is-ok">
              The signed note and prescription stay on the record and reach the patient as usual.
            </p>
          )}
        </div>

        <label className="cdlg-note">
          <span>Anything to add to the record? (optional)</span>
          <textarea
            value={note}
            rows={2}
            maxLength={2000}
            onChange={(e) => setNote(e.target.value)}
            placeholder={
              lossy ? "e.g. written on the OPD pad, patient taking it to the pharmacy" : ""
            }
          />
        </label>

        {error && (
          <p className="cdlg-err" role="alert">
            {error}
          </p>
        )}

        <div className="cdlg-actions">
          <button className="cdlg-cancel" onClick={onCancel} disabled={busy}>
            Go back
          </button>
          <button
            className="cdlg-go"
            onClick={() => onConfirm(mode, note)}
            disabled={busy}
            data-testid="conclude-confirm"
          >
            {busy ? "Ending…" : "End the consult"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Choice({
  id,
  checked,
  onPick,
  title,
  line,
}: {
  id: RxMode;
  checked: boolean;
  onPick: (mode: RxMode) => void;
  title: string;
  line: string;
}) {
  return (
    <label className={`cdlg-choice${checked ? " is-on" : ""}`} data-testid={`rx-mode-${id}`}>
      <input
        type="radio"
        name="rx_mode"
        value={id}
        checked={checked}
        onChange={() => onPick(id)}
      />
      <span>
        <strong>{title}</strong>
        <small>{line}</small>
      </span>
    </label>
  );
}
