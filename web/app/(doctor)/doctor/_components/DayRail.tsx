"use client";

// The day list, as a vertical clinical spine, now with three scopes on top.
//
// The deliberate aesthetic risk for this surface (doc 04 §5). A doctor's list is
// not a table of rows, it is a line they are moving down — so the tokens are
// stations on a rail: the patient in the room is a filled marigold node, those
// still waiting are hollow nodes below it, and an urgent token wears a danger
// ring rather than being re-sorted by colour alone (the queue already put it at
// the top, by construction — the ring says *why*, it does not do the sorting).
//
// It echoes the kiosk/board train metaphor without reusing the board's giant
// numerals, which belong to a surface read at 8 metres, not at arm's length.
//
// Session B added the scope tabs, and the one rule that governs them: **the
// `Unassigned` count is visible whether or not its tab is open.** It is the
// compensating control for every kiosk `Skip` and every offline arrival, so when
// there are unassigned patients still *waiting* the tab renders as an attention
// state — marigold, with the number spelled out underneath — rather than as a
// quiet grey badge that a busy doctor's eye slides straight past.

import type { Day, DayCounts, DayRow, DayScope } from "../_lib/doctor";
import { DAY_SCOPES } from "../_lib/doctor";

const STATE_LABEL: Record<DayRow["state"], string> = {
  waiting: "waiting",
  called: "called",
  in_consult: "in the room",
  done: "done",
  no_show: "no-show",
  lab_requeue: "at the lab",
};

const SCOPE_LABEL: Record<DayScope, string> = {
  mine: "Mine",
  unassigned: "Unassigned",
  department: "Department",
};

/** What the rail says when a scope is empty. Each one is different on purpose:
 *  an empty `Mine` and an empty `Unassigned` are opposite pieces of news, and a
 *  single "nothing here" would make the good one look like the bad one. */
const EMPTY_COPY: Record<DayScope, string> = {
  mine: "Nobody is assigned to you yet. Check Unassigned — a skipped kiosk arrival or an offline one waits there with nobody's name on it.",
  unassigned: "Everyone waiting has a doctor's name on them.",
  department: "Nobody in the queue yet. Tokens appear here the moment the kiosk issues them.",
};

export function DayRail({
  day,
  selectedVisitId,
  onSelect,
  onScope,
  onTake,
  busy,
}: {
  day: Day;
  selectedVisitId: string | null;
  onSelect: (row: DayRow) => void;
  onScope: (scope: DayScope) => void;
  onTake: (row: DayRow) => void;
  busy: boolean;
}) {
  const counts: DayCounts = day.counts;
  const attention = counts.unassigned_waiting > 0;

  return (
    <nav className="rail" aria-label="Today's patients">
      <div className="scopes" role="tablist" aria-label="Worklist">
        {DAY_SCOPES.map((scope) => {
          const isOpen = day.scope === scope;
          const flagged = scope === "unassigned" && attention;
          return (
            <button
              key={scope}
              role="tab"
              aria-selected={isOpen}
              className={["scope", isOpen ? "is-open" : "", flagged ? "is-attention" : ""]
                .filter(Boolean)
                .join(" ")}
              onClick={() => onScope(scope)}
              data-testid={`scope-${scope}`}
            >
              <span className="scope-name">{SCOPE_LABEL[scope]}</span>
              <span className="scope-n" data-testid={`count-${scope}`}>
                {counts[scope]}
              </span>
            </button>
          );
        })}
      </div>

      {/* The attention state is stated in words as well as in colour — doc 04 §4
          forbids colour-only meaning, and this is the one number on the console
          that must not be missable. It stays put when the tab is closed. */}
      {attention && (
        <p className="unassigned-alert" data-testid="unassigned-alert">
          {counts.unassigned_waiting} waiting with no doctor
        </p>
      )}

      <div className="rail-h">
        <span className="rail-count">{day.rows.length}</span>
        <span className="rail-label">
          {day.rows.length === 1 ? "patient on this list" : "patients on this list"}
        </span>
      </div>

      {day.rows.length === 0 ? (
        <p className="rail-empty">{EMPTY_COPY[day.scope]}</p>
      ) : (
        <ol className="spine">
          {day.rows.map((row) => {
            const active = row.state === "in_consult" || row.state === "called";
            return (
              <li
                key={row.entry_id}
                className={[
                  "station",
                  row.priority,
                  row.state,
                  active ? "is-active" : "",
                  row.visit_id === selectedVisitId ? "is-selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <button
                  className="srow"
                  onClick={() => onSelect(row)}
                  data-testid={`station-${row.token_no}`}
                >
                  <span className="node" aria-hidden="true" />
                  <span className="stok">{row.token_no}</span>
                  <span className="sbody">
                    <span className="sname">
                      {row.patient_name}
                      {row.patient_age != null && <em> · {row.patient_age}y</em>}
                    </span>
                    {/* The diagnosis is the field that must survive (plan §4.5):
                        the rail sheds chips before it truncates this. */}
                    <span className="scc">{row.chief_complaint ?? "—"}</span>
                    <span className="sfoot">
                      <span className="sstate">{STATE_LABEL[row.state]}</span>
                      {row.red_flag_count > 0 && (
                        <span className="sflag">
                          {row.red_flag_count} red flag{row.red_flag_count > 1 ? "s" : ""}
                        </span>
                      )}
                    </span>
                    {/* Whose patient this is, stated only when it is not the
                        reading doctor's. Repeating "you" on every row of `Mine`
                        would be noise; leaving a colleague's row unlabelled
                        would make it look unassigned. */}
                    {!row.is_mine && (
                      <span className={`swho ${row.assigned_doctor_id ? "" : "pool"}`}>
                        {row.assigned_doctor_name ?? "No doctor assigned"}
                      </span>
                    )}
                    {row.priority !== "routine" && row.priority_reason && (
                      <span className="sreason">{row.priority_reason}</span>
                    )}
                  </span>
                </button>

                {/* Cover, in one tap, from the row itself. Offered on anyone who
                    is not already this doctor's — the pool and a colleague's
                    alike, because an absent colleague's line stalls the same way
                    an unassigned one does. */}
                {!row.is_mine && (
                  <button
                    className="take"
                    disabled={busy}
                    onClick={() => onTake(row)}
                    data-testid={`take-${row.token_no}`}
                  >
                    Take this patient
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </nav>
  );
}
