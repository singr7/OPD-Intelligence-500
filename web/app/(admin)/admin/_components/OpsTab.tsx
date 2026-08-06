"use client";

// Tab 2 — Intake & operations metrics (doc 03 §11), from the domain tables. The
// funnel (started → completed → confirmed) per channel with median duration, the
// tier-downgrade count, and intake volume by language. Node-level abandonment
// ("where in the tree people quit") arrives with the tree-improvement report
// deferred alongside the tree editor — noted, not faked.

import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function OpsTab({ token, onError }: TabProps) {
  const ops = useLoad(() => api.fetchOps(token), onError);
  // M4: what the ambient notes of the last week were about. Here rather than on
  // the cost tab because it is an operations question — symptom burden and
  // follow-up debt across a clinic — not a rupee one.
  const tags = useLoad(() => api.fetchNoteTags(token), onError);

  return (
    <>
      <section className="cards">
        <div className="card">
          <div className="label">Tier downgrades</div>
          <div className="value">{ops.data?.tier_downgrades ?? "—"}</div>
          <div className="sub">intakes below V1 (proxy; real events S8)</div>
        </div>
        {ops.data &&
          Object.entries(ops.data.intakes_by_lang).map(([lang, n]) => (
            <div className="card" key={lang}>
              <div className="label">Intakes · {lang}</div>
              <div className="value">{n}</div>
            </div>
          ))}
      </section>

      <section>
        <h2>Intake funnel</h2>
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th className="num">Started</th>
              <th className="num">Completed</th>
              <th className="num">Confirmed</th>
              <th className="num">Completion</th>
              <th className="num">Median duration</th>
            </tr>
          </thead>
          <tbody>
            {ops.data?.funnel.map((f) => (
              <tr key={f.channel}>
                <td>{f.channel}</td>
                <td className="num">{f.started}</td>
                <td className="num">{f.completed}</td>
                <td className="num">{f.confirmed}</td>
                <td className="num">
                  {f.started ? `${Math.round((f.completed / f.started) * 100)}%` : "—"}
                </td>
                <td className="num">
                  {f.median_duration_s != null ? `${Math.round(f.median_duration_s)}s` : "—"}
                </td>
              </tr>
            ))}
            {ops.data?.funnel.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No intakes in the last 7 days.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {/* -- What the consult notes were about (M4) ------------------------
          The proof that mapping a spoken observation into a small shape buys
          something a transcript does not: three counts a clinic can act on,
          out of notes doctors confirmed.

          Two things this panel must keep saying. The first is its basis — the
          tags are model-suggested and doctor-accepted, and the sentence comes
          from the server so it cannot drift from the query. The second is the
          denominator: `drafts_excluded` is shown, not hidden, because a week
          where most notes were never confirmed is exactly when these numbers
          stop representing the clinic. */}
      <section>
        <h2>What consult notes were about</h2>
        <p className="muted" data-testid="note-tags-basis">
          {tags.data?.basis ?? "Loading…"}
        </p>

        <section className="cards">
          <div className="card">
            <div className="label">Notes counted</div>
            <div className="value" data-testid="note-tags-counted">
              {tags.data?.notes_counted ?? "—"}
            </div>
            <div className="sub">confirmed in the last 7 days</div>
          </div>
          <div className="card">
            <div className="label">Drafts excluded</div>
            <div className="value">{tags.data?.drafts_excluded ?? "—"}</div>
            <div className="sub">recorded but never reviewed</div>
          </div>
        </section>

        <table>
          <thead>
            <tr>
              <th>Symptom</th>
              <th className="num">Notes</th>
              <th className="num">Grade said</th>
            </tr>
          </thead>
          <tbody>
            {tags.data?.symptoms.map((s) => (
              <tr key={s.label}>
                <td>{s.label}</td>
                <td className="num">{s.notes}</td>
                {/* "Grade said", never "graded". The column counts how often a
                    doctor was specific, and the difference matters: this system
                    does not grade a symptom and must not look as though it has. */}
                <td className="num">{s.with_grade}</td>
              </tr>
            ))}
            {tags.data?.symptoms.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  No symptoms tagged on confirmed notes in the last 7 days.
                </td>
              </tr>
            )}
          </tbody>
        </table>

        <div className="split-tables">
          <table>
            <thead>
              <tr>
                <th>Problem</th>
                <th className="num">Notes</th>
              </tr>
            </thead>
            <tbody>
              {tags.data?.problems.map((p) => (
                <tr key={p.label}>
                  <td>{p.label}</td>
                  <td className="num">{p.notes}</td>
                </tr>
              ))}
              {tags.data?.problems.length === 0 && (
                <tr>
                  <td colSpan={2} className="muted">
                    Nothing tagged.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <table>
            <thead>
              <tr>
                <th>Owed before the next visit</th>
                <th className="num">Notes</th>
              </tr>
            </thead>
            <tbody>
              {tags.data?.followups.map((fu) => (
                <tr key={fu.label}>
                  <td>{fu.label}</td>
                  <td className="num">{fu.notes}</td>
                </tr>
              ))}
              {tags.data?.followups.length === 0 && (
                <tr>
                  <td colSpan={2} className="muted">
                    Nothing tagged.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
