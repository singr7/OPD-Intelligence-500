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
    </>
  );
}
