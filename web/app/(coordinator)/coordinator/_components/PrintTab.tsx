"use client";

// Downtime sheets to print and laminate before an outage (doc 01 §5 pt 3). The
// endpoints need the staff bearer token, so a plain <a href> won't do — we fetch
// the HTML with auth, then open it as a blob in a new tab where the browser's
// print dialog turns it into a PDF (the same browser-print stance as the kiosk
// ESC/POS bridge fallback).

import { useState } from "react";
import { API_BASE } from "@/app/_lib/queue";

export function PrintTab({ token }: { token: string }) {
  const [kioskId, setKioskId] = useState("KIOSK-1");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function open(path: string, label: string) {
    setBusy(label);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const html = await res.text();
      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      window.open(url, "_blank", "noopener");
      // Revoke after the new tab has had time to load.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      setError("Could not generate the sheet. Check you're still signed in.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="print-tab">
      <div className="print-card">
        <h2>Intake sheets</h2>
        <p>
          One fillable form per department, in Hindi + English, rendered from the live
          question trees. Print and laminate for a total blackout.
        </p>
        <button
          onClick={() => open("/queue/print/intake-sheets", "intake")}
          disabled={busy === "intake"}
        >
          {busy === "intake" ? "Generating…" : "Open intake sheets"}
        </button>
      </div>

      <div className="print-card">
        <h2>Token block sheet</h2>
        <p>
          Tear-off token numerals from a kiosk&apos;s pre-allocated offline block. Staff hand
          these out in order during a blackout.
        </p>
        <label className="kiosk-id">
          Kiosk
          <input value={kioskId} onChange={(e) => setKioskId(e.target.value)} />
        </label>
        <button
          onClick={() =>
            open(`/queue/print/token-block?kiosk_id=${encodeURIComponent(kioskId)}`, "tokens")
          }
          disabled={busy === "tokens" || !kioskId}
        >
          {busy === "tokens" ? "Generating…" : "Open token block"}
        </button>
      </div>

      {error && <p className="print-err">{error}</p>}
    </div>
  );
}
