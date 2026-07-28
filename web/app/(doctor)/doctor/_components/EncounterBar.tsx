"use client";

// The encounter bar (S-UX.6) — one strip that answers "where am I with this
// patient, and what do I press next".
//
// The previous console spread that answer across three places: a "Call next
// patient" button in the app bar, a `state` chip on the card, and a row of
// buttons below the symptoms table. A doctor glancing up mid-consult could not
// tell from any one of them whether the encounter was still open, and the two
// verbs that move the queue sat at opposite ends of the screen. So they are one
// object now, directly above the card, and the rule is:
//
//   * the strip states the encounter in words ("In consultation · token 12"),
//   * there is exactly one filled button, and it is the thing to do next,
//   * everything else on the strip is quiet, and destructive verbs are quietest.
//
// The verbs themselves are unchanged — they are the S8 queue transitions, with
// the same audit trail the board and the coordinator see.

import type { PatientCard as Card, Day } from "../_lib/doctor";

export type Action = "in_consult" | "done" | "no_show" | "lab_requeue";

/** What the strip says, per queue state. Written as the doctor would say it out
 *  loud, not as the state machine spells it: `in_consult` is "in consultation". */
const HEADLINE: Record<string, string> = {
  waiting: "Waiting to be called",
  called: "Called — not started yet",
  in_consult: "In consultation",
  lab_requeue: "Back from the lab",
  done: "Consult completed",
  no_show: "Marked no-show",
};

export function EncounterBar({
  card,
  day,
  busy,
  onAction,
  onCallNext,
  onDictate,
  noteSigned,
}: {
  card: Card | null;
  day: Day | null;
  busy: boolean;
  onAction: (action: Action) => void;
  onCallNext: () => void;
  onDictate: () => void;
  /** True once this visit has a signed consult note — the strip then says so
   *  rather than inviting the doctor to write a second one. */
  noteSigned: boolean;
}) {
  const waiting = day?.rows.filter((r) => r.state === "waiting").length ?? 0;
  const state = card?.entry_state ?? null;
  const finished = state === "done" || state === "no_show";
  const roomIsEmpty = !card || finished;

  return (
    <section className="encounter" data-state={state ?? "none"} data-testid="encounter-bar">
      <div className="enc-who">
        <span className={`enc-dot ${roomIsEmpty ? "idle" : "live"}`} aria-hidden="true" />
        <div className="enc-text">
          <strong>{card ? HEADLINE[state ?? "waiting"] ?? "On the list" : "Room is free"}</strong>
          <span>
            {card ? (
              <>
                Token {card.token_no ?? "—"} · {card.name}
                {card.age != null && ` · ${card.age}y`}
              </>
            ) : (
              <>
                {waiting} {waiting === 1 ? "patient" : "patients"} waiting in{" "}
                {day?.department_name ?? "this department"}
              </>
            )}
          </span>
        </div>
      </div>

      <div className="enc-actions">
        {/* The one filled button is always the next step, and never two at once. */}
        {state === "called" && (
          <button
            className="act primary"
            disabled={busy}
            onClick={() => onAction("in_consult")}
            data-testid="start-consult"
          >
            Start consult
          </button>
        )}

        {(state === "in_consult" || state === "lab_requeue") && (
          <>
            <button
              className={`act ${noteSigned ? "" : "note-action"}`}
              onClick={onDictate}
              data-testid="open-note"
            >
              {noteSigned ? "View consult note" : "Write consult note"}
              <kbd className="hint">D</kbd>
            </button>
            <button
              className="act primary"
              disabled={busy}
              onClick={() => onAction("done")}
              data-testid="complete-consult"
            >
              Complete consult
            </button>
          </>
        )}

        {state === "in_consult" && (
          <button className="act quiet" disabled={busy} onClick={() => onAction("lab_requeue")}>
            Send to lab
          </button>
        )}

        {(state === "called" || state === "waiting") && (
          <button
            className="act quiet danger-quiet"
            disabled={busy}
            onClick={() => onAction("no_show")}
          >
            No-show
          </button>
        )}

        {/* Calling the next patient is the primary act only when the room is
            actually free. Mid-consult it stays reachable but quiet, because a
            doctor who taps it by reflex has just skipped the person in front of
            them without meaning to. */}
        <button
          className={`act ${roomIsEmpty ? "primary" : "quiet"}`}
          onClick={onCallNext}
          disabled={busy || !day}
          data-testid="call-next"
          title={
            roomIsEmpty
              ? "Call the next patient (N)"
              : "Calls the next patient without completing this one"
          }
        >
          Call next patient
          <kbd className="hint">N</kbd>
        </button>
      </div>
    </section>
  );
}
