"use client";

// The downtime reconciliation list (doc 01 §5 pt 5): everything that came in off
// the online path today — offline-kiosk syncs and paper entries — so the
// coordinator can confirm on recovery that nothing was lost and no token
// collided. Read-only; the tokens are already on paper in patients' hands.

import { useEffect, useState } from "react";
import { AuthError, fetchReconciliation, ReconEntry } from "@/app/_lib/queue";

export function ReconciliationTab({
  token,
  onSignOut,
}: {
  token: string;
  onSignOut: () => void;
}) {
  const [entries, setEntries] = useState<ReconEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchReconciliation(token)
      .then((r) => alive && setEntries(r.entries))
      .catch((e) => {
        if (e instanceof AuthError) onSignOut();
        else if (alive) setError("Could not load the reconciliation list.");
      });
    return () => {
      alive = false;
    };
  }, [token, onSignOut]);

  if (error) return <div className="empty-state">{error}</div>;
  if (!entries) return <p className="loading">Loading…</p>;
  if (entries.length === 0) {
    return (
      <div className="empty-state">
        Nothing to reconcile — no offline or paper intakes today.
      </div>
    );
  }

  return (
    <div className="recon">
      <p className="recon-lead">
        {entries.length} intake{entries.length > 1 ? "s" : ""} arrived off the online path today.
        Tokens shown are the ones patients are already holding.
      </p>
      <table className="recon-table">
        <thead>
          <tr>
            <th>Token</th>
            <th>Department</th>
            <th>Source</th>
            <th>Chief complaint</th>
            <th>Red flags</th>
            <th>Completed</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.intake_id} className={e.red_flag_count > 0 ? "has-flag" : ""}>
              <td className="tok">{e.token_no ?? "—"}</td>
              <td>{e.department_key}</td>
              <td>
                <span className={`src src-${e.channel}`}>
                  {e.client_id ? "Offline kiosk" : e.channel === "paper" ? "Paper" : e.channel}
                </span>
              </td>
              <td className="chief">{e.chief_complaint || "—"}</td>
              <td>{e.red_flag_count > 0 ? `⚠ ${e.red_flag_count}` : "—"}</td>
              <td className="when">{formatWhen(e.completed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true });
}
