"use client";

// Recording, confirming and withdrawing an allergy (SESSION-ALLERGY).
//
// Opened from the spine's third slot, over the console rather than inside a tab:
// this takes ten seconds and must not cost the doctor the tab they were reading.
// The same reason the note dock floats.
//
// ## What the panel is arranged around
//
// The commonest act here is not typing — it is **confirming what the patient
// already said**. She named penicillin at a tablet at 9am; the doctor asks about
// it in the room at 11 and now the record should show that a clinician has heard
// it. So `Confirm` is a single tap on the row that is already there, it never
// asks the doctor to re-type the substance, and the statement stays hers.
//
// The second commonest is **recording that there are none** — which is why that
// is a button and not a checkbox on the form. A doctor who asked and was told
// "nothing" currently has nowhere to put it, and the spine goes on saying nobody
// ever asked, on every visit, forever.
//
// ## Withdrawing is not deleting, and the panel says so
//
// A struck-out statement stays on the list, struck through, with who withdrew it
// and why. That is the point: a record that silently loses a retracted penicillin
// allergy cannot answer what it told the doctor who prescribed last month. The
// reason box is asked for and never required — a doctor who has spotted a wrong
// allergy mid-consult must be able to strike it in one tap, and a mandatory
// justification field is how a safety control becomes a thing people work around.
//
// ## No drug matching, here or anywhere
//
// There is no autocomplete against the formulary and no interaction check. A
// match on free text a patient typed at a kiosk would be a safety feature made
// of guesses, and the failure mode of a *missed* match is a doctor who trusted a
// green tick. The panel puts the words in front of them; they decide.

import { useEffect, useRef, useState } from "react";
import type { AllergyEntry, AllergyView } from "../_lib/doctor";
import { shortDate, sourceLabel, substanceText } from "../_lib/allergies";

type Props = {
  patientName: string;
  view: AllergyView;
  busy: boolean;
  error: string | null;
  onRecord: (input: {
    substance?: string | null;
    reaction?: string | null;
    severity?: "unknown" | "mild" | "severe";
    none_known?: boolean;
  }) => void;
  onConfirm: (allergyId: string) => void;
  onRetract: (allergyId: string, reason: string) => void;
  onClose: () => void;
};

export function AllergyPanel({
  patientName,
  view,
  busy,
  error,
  onRecord,
  onConfirm,
  onRetract,
  onClose,
}: Props) {
  const [substance, setSubstance] = useState("");
  const [reaction, setReaction] = useState("");
  const [severity, setSeverity] = useState<"unknown" | "mild" | "severe">("unknown");
  const [retracting, setRetracting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const first = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    first.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const submit = () => {
    if (!substance.trim()) return;
    onRecord({ substance, reaction, severity });
    setSubstance("");
    setReaction("");
    setSeverity("unknown");
  };

  return (
    <div className="alg-scrim" role="dialog" aria-modal="true" aria-label="Allergies">
      <div className="alg-panel" data-testid="allergy-panel">
        <header className="alg-head">
          <h2>Allergies</h2>
          <p>{patientName}</p>
          <button className="alg-x" onClick={onClose} disabled={busy} aria-label="Close">
            ×
          </button>
        </header>

        {error && (
          <p className="alg-err" data-testid="allergy-error">
            {error}
          </p>
        )}

        <div className="alg-body">
          {/* What is on file. `never_asked` says so in the same words the spine
              does, rather than showing an empty list — an empty list reads as
              "we checked and there are none", which is the claim this whole
              module exists to refuse. */}
          {view.state === "never_asked" && view.retracted.length === 0 ? (
            <p className="alg-empty" data-testid="allergy-empty">
              Nobody has asked this patient yet. This is not the same as no known
              allergies, and the record will not say that it is.
            </p>
          ) : null}

          {view.state === "none_stated" && view.none_statement ? (
            <p className="alg-none" data-testid="allergy-none">
              <strong>None stated.</strong> {sourceLabel(view.none_statement)} ·{" "}
              {shortDate(view.none_statement.stated_at)}
            </p>
          ) : null}

          {view.entries.length > 0 && (
            <ul className="alg-list" data-testid="allergy-list">
              {view.entries.map((entry) => (
                <li
                  key={entry.id}
                  className={entry.severity === "severe" ? "alg-item severe" : "alg-item"}
                  data-testid="allergy-item"
                >
                  <div className="alg-item-main">
                    <span className="alg-sub">{substanceText(entry)}</span>
                    {entry.severity !== "unknown" && (
                      <span className={`alg-sev sev-${entry.severity}`}>{entry.severity}</span>
                    )}
                    {entry.reaction && <span className="alg-rxn">{entry.reaction}</span>}
                  </div>
                  <p className="alg-prov">
                    {sourceLabel(entry)} · {shortDate(entry.stated_at)}
                    {entry.confirmed_at ? (
                      <span className="alg-ok">
                        {" "}
                        · confirmed
                        {entry.confirmed_by_name ? ` by ${entry.confirmed_by_name}` : ""}
                      </span>
                    ) : (
                      <span className="alg-unconf"> · not yet confirmed by a doctor</span>
                    )}
                  </p>

                  {retracting === entry.id ? (
                    <div className="alg-retract">
                      <input
                        className="alg-input"
                        value={reason}
                        disabled={busy}
                        maxLength={500}
                        placeholder="Why is this being withdrawn? (optional)"
                        onChange={(e) => setReason(e.target.value)}
                        data-testid="allergy-reason"
                      />
                      <button
                        className="alg-btn danger"
                        disabled={busy}
                        onClick={() => {
                          onRetract(entry.id, reason);
                          setRetracting(null);
                          setReason("");
                        }}
                        data-testid="allergy-retract-confirm"
                      >
                        Withdraw
                      </button>
                      <button
                        className="alg-btn ghost"
                        disabled={busy}
                        onClick={() => {
                          setRetracting(null);
                          setReason("");
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="alg-acts">
                      {!entry.confirmed_at && (
                        <button
                          className="alg-btn"
                          disabled={busy}
                          onClick={() => onConfirm(entry.id)}
                          data-testid="allergy-confirm"
                        >
                          Confirm
                        </button>
                      )}
                      <button
                        className="alg-btn ghost"
                        disabled={busy}
                        onClick={() => setRetracting(entry.id)}
                        data-testid="allergy-retract"
                      >
                        Withdraw
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {/* Struck out, and still here. Shown last and quietly: it is history,
              not a current fact — but a later reader needs to know the record
              once said this, and who took it back. */}
          {view.retracted.length > 0 && (
            <div className="alg-gone" data-testid="allergy-retracted">
              <h3>Withdrawn</h3>
              <ul>
                {view.retracted.map((entry: AllergyEntry) => (
                  <li key={entry.id}>
                    <s>{entry.kind === "none_known" ? "none stated" : substanceText(entry)}</s>{" "}
                    <span className="alg-prov">
                      withdrawn{entry.retracted_by_name ? ` by ${entry.retracted_by_name}` : ""}
                      {entry.retracted_at ? ` · ${shortDate(entry.retracted_at)}` : ""}
                      {entry.retracted_reason ? ` — ${entry.retracted_reason}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="alg-add">
          <h3>Record an allergy</h3>
          <div className="alg-row">
            <input
              ref={first}
              className="alg-input"
              value={substance}
              disabled={busy}
              maxLength={200}
              placeholder="Substance — e.g. penicillin"
              onChange={(e) => setSubstance(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              data-testid="allergy-substance"
            />
            <input
              className="alg-input"
              value={reaction}
              disabled={busy}
              maxLength={500}
              placeholder="What happened — e.g. throat closed"
              onChange={(e) => setReaction(e.target.value)}
              data-testid="allergy-reaction"
            />
          </div>
          <div className="alg-row">
            {/* Severity is the doctor's to set and nobody else's. It stays
                `unknown` on everything the kiosk writes, because the patient
                named a substance and was never asked what it did to her. */}
            <div className="alg-sevpick" role="group" aria-label="Severity">
              {(["unknown", "mild", "severe"] as const).map((level) => (
                <button
                  key={level}
                  className={severity === level ? "alg-chip on" : "alg-chip"}
                  disabled={busy}
                  onClick={() => setSeverity(level)}
                  data-testid={`allergy-sev-${level}`}
                >
                  {level === "unknown" ? "not known" : level}
                </button>
              ))}
            </div>
            <button
              className="alg-btn primary"
              disabled={busy || !substance.trim()}
              onClick={submit}
              data-testid="allergy-add"
            >
              Add
            </button>
          </div>

          {/* The other half of the same act, and given its own button because a
              doctor who asked and heard "nothing" has learned something worth
              recording — and today has nowhere to put it. */}
          <button
            className="alg-btn wide"
            disabled={busy}
            onClick={() => onRecord({ none_known: true })}
            data-testid="allergy-none-known"
          >
            I asked — the patient reports none
          </button>
        </div>
      </div>
    </div>
  );
}
