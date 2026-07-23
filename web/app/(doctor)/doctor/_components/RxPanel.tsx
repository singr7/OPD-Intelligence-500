"use client";

// What signing produced (doc 03 §8). It appears *after* the note locks, in the
// same column, because it is the consequence of the signature and not a second
// task the doctor has to go and find.
//
// The design job here is narrow and mostly about restraint. The prescription is
// already decided — nothing on this panel changes a dose. So it shows three
// things and no more: what is on the paper, which lines the pharmacist must
// question, and how the patient gets a copy. The pictograms are rendered exactly
// as the printed sheet renders them, from the same `slots_known` flag, so what
// the doctor sees here is what the patient is handed. A preview that quietly
// prettified an unclear schedule would be worse than no preview.

import { useCallback, useEffect, useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import type { Prescription, RxMed } from "../_lib/prescription";
import { deliverPrescription, openPrintCopy, readPrescription } from "../_lib/prescription";

type Props = {
  token: string;
  visitId: string;
  /** Bumped by the parent when the note is signed, so this refetches. */
  signedAt?: string | null;
  onAuthError: () => void;
};

export function RxPanel({ token, visitId, signedAt, onAuthError }: Props) {
  const [rx, setRx] = useState<Prescription | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      setRx(await readPrescription(token, visitId));
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
    } finally {
      setLoaded(true);
    }
  }, [token, visitId, onAuthError]);

  useEffect(() => {
    void load();
  }, [load, signedAt]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      if (e instanceof AuthError) {
        onAuthError();
        return;
      }
      setError("That did not go through. The paper copy still works.");
    } finally {
      setBusy(null);
    }
  }

  if (!loaded || !rx) return null;

  const flagged = rx.meds.filter((m) => m.flagged);

  return (
    <section className="rx" aria-label="Prescription">
      <header className="rx-head">
        <h3>Prescription</h3>
        <span className="rx-count">
          {rx.meds.length} {rx.meds.length === 1 ? "medicine" : "medicines"}
        </span>
      </header>

      <ol className="rx-list">
        {rx.meds.map((med, i) => (
          <RxRow key={`${med.name}-${i}`} med={med} />
        ))}
      </ol>

      {flagged.length > 0 && (
        <p className="rx-flagnote">
          {flagged.length === 1 ? "One line prints" : `${flagged.length} lines print`} with a
          confirm-with-the-doctor mark. You acknowledged {flagged.length === 1 ? "it" : "them"} to
          sign; the pharmacist has not.
        </p>
      )}

      <div className="rx-actions">
        <button
          className="rx-print"
          onClick={() => run("clinical", () => openPrintCopy(token, rx.id, "clinical"))}
          disabled={!!busy}
        >
          {busy === "clinical" ? "Opening…" : "Print clinical copy"}
        </button>
        <button
          className="rx-print is-patient"
          onClick={() => run("patient", () => openPrintCopy(token, rx.id, "patient"))}
          disabled={!!busy}
        >
          {busy === "patient" ? "Opening…" : "Print patient copy"}
        </button>
      </div>

      <div className="rx-send">
        <button
          onClick={() =>
            run("whatsapp", async () => setRx(await deliverPrescription(token, rx.id, "whatsapp")))
          }
          disabled={!!busy}
        >
          {busy === "whatsapp" ? "Sending…" : "WhatsApp"}
        </button>
        <button
          onClick={() => run("sms", async () => setRx(await deliverPrescription(token, rx.id, "sms")))}
          disabled={!!busy}
        >
          {busy === "sms" ? "Sending…" : "SMS"}
        </button>
        {Object.entries(rx.delivered_via)
          .filter(([channel]) => channel !== "print")
          .map(([channel, d]) => (
            <span key={channel} className={`rx-deliv is-${d.status}`}>
              {channel} {d.status}
            </span>
          ))}
      </div>

      {error && <p className="rx-err">{error}</p>}
    </section>
  );
}

/**
 * One printed line, previewed.
 *
 * The schedule has three renderings and they are the three states the backend
 * can report — see `parse_schedule`. Only the first names a time of day.
 */
function RxRow({ med }: { med: RxMed }) {
  const s = med.schedule;
  return (
    <li className={`rx-row${med.flagged ? " is-flagged" : ""}`}>
      <div className="rx-name">
        {med.name}
        {med.dose && <span className="rx-dose"> {med.dose}</span>}
      </div>
      <div className="rx-when">
        {s && s.slots_known ? (
          <span className="rx-slots" aria-label={slotsLabel(s.morning, s.afternoon, s.night)}>
            <i className={s.morning ? "on" : ""}>☀</i>
            <i className={s.afternoon ? "on" : ""}>☼</i>
            <i className={`night${s.night ? " on" : ""}`}>☾</i>
          </span>
        ) : s ? (
          // A count with no time of day: say how many, refuse to say when.
          <span className="rx-count-only">{s.per_day}× a day</span>
        ) : (
          // Unreadable as a schedule — the doctor's own words go on the sheet.
          <span className="rx-words">{med.freq ?? "—"}</span>
        )}
        {med.duration && <span className="rx-dur">{med.duration}</span>}
      </div>
      {med.flagged && <div className="rx-why">{med.flag_reason}</div>}
    </li>
  );
}

function slotsLabel(morning: boolean, afternoon: boolean, night: boolean): string {
  const parts = [morning && "morning", afternoon && "afternoon", night && "night"].filter(Boolean);
  return parts.length ? `Taken in the ${parts.join(", ")}` : "No time of day given";
}
