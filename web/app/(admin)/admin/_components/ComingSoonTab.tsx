"use client";

// Protocol templates (doc 03 §9/§10) — read-only, and the one deferred panel
// still standing (slot templates, doc 03 §10).
//
// S17 turned protocol templates from a placeholder into a real bank, so this tab
// now shows it: which regimen family a signed note lands on, which days after
// treatment it asks something, and how many grading rules can escalate what she
// answers. Like the message-template registry next door it is read-only — the
// bank is a validated seed file the backend loads at boot, and an editor that
// could move a check-in day would be an editor that changes clinical policy
// without the validator that catches an unreachable question set or a rule that
// can never fire. That editor is S18-late and wants the bank in a table first.

import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function ComingSoonTab({ token, onError }: TabProps) {
  const bank = useLoad(() => api.fetchProtocolTemplates(token), onError);
  const slots = useLoad(() => api.fetchSlotTemplates(token), onError);

  return (
    <>
      <section>
        <h2>Protocol templates</h2>
        <p className="muted">
          Read-only — {bank.data?.source ?? "seeds/protocols.json"}, version{" "}
          {bank.data?.version ?? "…"}. A signed dictation picks the family with the highest
          precedence it matches; the days and question sets below are copied into the plan
          unchanged, and the LLM personalisation may only rewrite the covering message.
        </p>
        <table>
          <thead>
            <tr>
              <th>Family</th>
              <th>Matches</th>
              <th>Cycle</th>
              <th>Asks</th>
            </tr>
          </thead>
          <tbody>
            {(bank.data?.protocols ?? []).map((p) => (
              <tr key={p.key}>
                <td>
                  <b>{p.label}</b>
                  <div className="muted">
                    {p.key} · precedence {p.precedence}
                  </div>
                </td>
                <td className="muted">
                  {[...p.matches.drug_classes, ...p.matches.keywords].slice(0, 4).join(", ")}
                </td>
                <td>{p.cycle_days > 0 ? `${p.cycle_days} days` : "—"}</td>
                <td>
                  {p.checkins.map((c) => (
                    <div key={c.day_offset}>
                      <b>D+{c.day_offset}</b> {c.asks_about}{" "}
                      <span className="muted">
                        ({c.questions} questions, {c.grading_rules} rules)
                      </span>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Grading rules</h2>
        <p className="muted">
          What escalates. Deterministic and authored — no model decides a check-in grade, for
          the same reason none decides a red flag. Green is the absence of a fired rule.
        </p>
        {(bank.data?.question_sets ?? []).map((set) => (
          <div key={set.key} className="notice">
            <b>{set.title}</b> <span className="muted">({set.key})</span>
            <ul>
              {set.grading.map((rule) => (
                <li key={rule.id}>
                  <span className={`pill ${rule.grade === "red" ? "bad" : "approaching"}`}>
                    {rule.grade}
                  </span>{" "}
                  {rule.reason}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section>
        <h2>Slot templates</h2>
        <div className="notice">
          <b>Arrives with {slots.data?.arrives_in ?? "S15"}.</b>{" "}
          {slots.data?.reason ??
            "Slot templates need the appointment slot inventory (telephony part 2)."}
        </div>
      </section>

      <section>
        <h2>Downtime drill</h2>
        <div className="notice">
          Run the downtime drill from the <b>Coordinator</b> console — it owns the queue’s
          downtime state (one switch, one audit trail). Admin does not keep a second copy.
        </div>
      </section>
    </>
  );
}
