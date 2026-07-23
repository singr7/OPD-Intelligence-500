"use client";

// The day list, as a vertical clinical spine.
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

import type { Day, DayRow } from "../_lib/doctor";

const STATE_LABEL: Record<DayRow["state"], string> = {
  waiting: "waiting",
  called: "called",
  in_consult: "in the room",
  done: "done",
  no_show: "no-show",
  lab_requeue: "at the lab",
};

export function DayRail({
  day,
  selectedVisitId,
  onSelect,
}: {
  day: Day;
  selectedVisitId: string | null;
  onSelect: (row: DayRow) => void;
}) {
  return (
    <nav className="rail" aria-label="Today's patients">
      <div className="rail-h">
        <span className="rail-count">{day.rows.length}</span>
        <span className="rail-label">
          {day.rows.length === 1 ? "patient waiting" : "patients on the list"}
        </span>
      </div>

      {day.rows.length === 0 ? (
        <p className="rail-empty">
          Nobody in the queue yet. Tokens appear here the moment the kiosk issues them.
        </p>
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
                <button onClick={() => onSelect(row)} data-testid={`station-${row.token_no}`}>
                  <span className="node" aria-hidden="true" />
                  <span className="stok">{row.token_no}</span>
                  <span className="sbody">
                    <span className="sname">
                      {row.patient_name}
                      {row.patient_age != null && <em> · {row.patient_age}y</em>}
                    </span>
                    <span className="scc">{row.chief_complaint ?? "—"}</span>
                    <span className="sfoot">
                      <span className="sstate">{STATE_LABEL[row.state]}</span>
                      {row.red_flag_count > 0 && (
                        <span className="sflag">
                          {row.red_flag_count} red flag{row.red_flag_count > 1 ? "s" : ""}
                        </span>
                      )}
                    </span>
                    {row.priority !== "routine" && row.priority_reason && (
                      <span className="sreason">{row.priority_reason}</span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </nav>
  );
}
